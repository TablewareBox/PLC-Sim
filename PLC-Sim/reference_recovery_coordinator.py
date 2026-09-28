"""CPU参考执行者接管与恢复确认；调用方提供设备事实、停止及持久化。"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import math
import sqlite3
import time
from typing import Any, Protocol
import uuid

try:
    from .reference_operation_gate import ContractError, canonical, identifier
    from .reference_execution_scope import validate_execution_identity, execution_scope, revoke_execution_scope
except ImportError:
    from reference_operation_gate import ContractError, canonical, identifier
    from reference_execution_scope import validate_execution_identity, execution_scope, revoke_execution_scope


class ControllerRecovery(Protocol):
    """调用方在同一世界中提供读回和实际停止，不伪造停稳。"""
    def busy(self, device: str) -> bool: ...
    def state(self, device: str) -> str: ...
    def interrupt(self, device: str, reason: str) -> Any: ...


class ReferenceRecoveryCoordinator:
    """复用现有租约/操作/恢复表，不创建表、连接、时钟或设备。

    lock与world_lock必须是宿主共同使用的可重入锁。authenticate接收完整
    请求，负责当前凭证及拒绝记录；save_durable在同一数据库事务中保存世界。
    controllers.interrupt负责实际停止、恢复证据及控制器代次，不以停钟替代。
    """
    def __init__(self, db: sqlite3.Connection, *, world_id: str,
                 environment_instance_id: str, device_ids: Sequence[str],
                 lock: Any, world_lock: Any, controllers: ControllerRecovery,
                 authenticate: Callable[[dict], Any], save_durable: Callable[[], None],
                 authority_current: Callable[[], dict] | None = None):
        self.db = db
        self.world_id = world_id
        self.environment_instance_id = environment_instance_id
        self.device_ids = tuple(device_ids)
        self._lock, self.world_lock = lock, world_lock
        self.controllers = controllers
        self.authenticate, self.save_durable = authenticate, save_durable
        self.authority_current = authority_current

    def claim(self, request):
        with self._lock, self.world_lock:
            fields = {'dispatcher_token', 'device_id', 'executor_id'}
            if not isinstance(request, dict) or set(request) not in (fields, fields | {'world_id'}):
                raise ContractError('invalid_claim')
            self.authenticate(request)
            if 'world_id' in request and request['world_id'] != self.world_id:
                raise ContractError('world_mismatch', 409)
            device, executor = request['device_id'], request['executor_id']
            if not isinstance(device, str) or device not in self.device_ids or not identifier(executor):
                raise ContractError('invalid_device_or_executor')
            if self.db.execute('SELECT 1 FROM retired_executors WHERE device_id=? AND executor_id=?', (device, executor)).fetchone():
                raise ContractError('retired_executor', 409)
            old, epoch = self.db.execute('SELECT executor_id,epoch FROM leases WHERE device_id=?', (device,)).fetchone()
            if old != executor:
                with self.db:
                    if old is not None:
                        self.db.execute('INSERT OR IGNORE INTO retired_executors VALUES (?,?)', (device, old))
                    if self.controllers.busy(device):
                        self.controllers.interrupt(device, 'executor_replaced')
                    epoch += 1
                    self.db.execute('UPDATE leases SET executor_id=?,epoch=? WHERE device_id=?', (executor, epoch, device))
                    self.save_durable()
        # 由原有世界时钟推进减速；不在驱动中步进或直接改写停稳转速。
        deadline = time.monotonic() + (2 if old != executor else 0)
        while time.monotonic() < deadline:
            with self._lock, self.world_lock:
                if not self.controllers.busy(device):
                    break
            time.sleep(.01)
        with self._lock, self.world_lock:
            self.authenticate(request)
            self._lease(device, executor, epoch)
            result = {'world_id': self.world_id, 'device_id': device, 'executor_id': executor,
                      'epoch': epoch, 'source': 'simulated_controller'}
            if self.authority_current:
                result['authority'] = self.authority_current()
            recovery = self.db.execute('SELECT recovery_json FROM controller_processes WHERE device_id=?', (device,)).fetchone()[0]
            if recovery:
                result['recovery'] = {**json.loads(recovery), 'controller_state': self.controllers.state(device),
                                      'stopped': not self.controllers.busy(device)}
            return result

    def _lease(self, device, executor, epoch):
        lease = self.db.execute('SELECT executor_id,epoch FROM leases WHERE device_id=?', (device,)).fetchone()
        if type(epoch) is not int or epoch != lease[1] or executor != lease[0] or lease[0] is None:
            raise ContractError('stale_executor', 409)

    def reconcile(self, request):
        fields = {'dispatcher_token', 'executor_id', 'epoch', 'world_id', 'device_id',
                  'operation_id', 'fingerprint', 'execution_identity', 'mode', 'expires_at'}
        with self._lock, self.world_lock:
            if not isinstance(request, dict) or set(request) != fields:
                raise ContractError('invalid_reconciliation_fields')
            self.authenticate(request)
            device = request['device_id']
            if not isinstance(device, str) or device not in self.device_ids:
                raise ContractError('unsupported_device')
            self._lease(device, request['executor_id'], request['epoch'])
            expiry = request['expires_at']
            try:
                valid_expiry = type(expiry) in (int, float) and math.isfinite(expiry) and expiry > time.time()
            except (ValueError, OverflowError):
                valid_expiry = False
            if not valid_expiry:
                raise ContractError('expired_request', 409)
            if request['world_id'] != self.world_id:
                raise ContractError('world_mismatch', 409)
            self._identity(request['execution_identity'])
            op_id, digest = request['operation_id'], request['fingerprint']
            if (not identifier(op_id) or not isinstance(digest, str) or len(digest) != 64
                    or any(c not in '0123456789abcdef' for c in digest)
                    or request['mode'] not in ('inspect', 'ensure_stopped')):
                raise ContractError('invalid_reconciliation')
            try:
                validate_execution_identity(request['execution_identity'], device_id=device, fingerprint=digest)
            except ValueError as error:
                raise ContractError(str(error), 409) from None
            identity = canonical(request['execution_identity'])
            prior = self.db.execute('SELECT fingerprint,identity_json,device_id,status FROM operations WHERE operation_id=?', (op_id,)).fetchone()
            fence = self.db.execute('SELECT fingerprint,identity_json,device_id FROM operation_fences WHERE operation_id=?', (op_id,)).fetchone()
            for row in (prior, fence):
                if row and (row[0] != digest or row[2] != device):
                    raise ContractError('operation_payload_conflict', 409)
                if row and row[1] != identity:
                    raise ContractError('execution_identity_conflict', 409)
            if request['mode'] == 'ensure_stopped':
                with self.db:
                    self.db.execute('INSERT OR IGNORE INTO operation_fences VALUES (?,?,?,?,NULL)', (op_id, device, digest, identity))
                    revoke_execution_scope(self.db, execution_scope(request['execution_identity']))
                    owner = self.db.execute('SELECT operation_id FROM controller_processes WHERE device_id=?', (device,)).fetchone()[0]
                    if self.controllers.busy(device) and owner == op_id:
                        self.controllers.interrupt(device, 'reconciliation_stop')
                    self.save_durable()
        if request['mode'] == 'ensure_stopped':
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with self._lock, self.world_lock:
                    if not self.controllers.busy(device):
                        break
                time.sleep(.01)
        with self._lock, self.world_lock:
            self.authenticate(request)
            self._lease(device, request['executor_id'], request['epoch'])
            cached = self.db.execute('SELECT proof_json FROM operation_fences WHERE operation_id=?', (op_id,)).fetchone()
            if cached and cached[0] and not self.controllers.busy(device):
                saved = json.loads(cached[0])
                current_generation = self.db.execute('SELECT generation FROM controller_processes WHERE device_id=?', (device,)).fetchone()[0]
                if (saved['epoch'] == request['epoch'] and saved['environment_instance_id'] == self.environment_instance_id
                        and saved['controller_generation'] == current_generation):
                    return saved
            owner = self.db.execute('SELECT generation,operation_id,identity_json FROM controller_processes WHERE device_id=?', (device,)).fetchone()
            busy = self.controllers.busy(device)
            terminal = bool(not busy and (prior is not None or request['mode'] == 'ensure_stopped'))
            proof = {'evidence_id': 'termination-' + uuid.uuid4().hex,
                     'environment_instance_id': self.environment_instance_id,
                     'world_id': self.world_id, 'device_id': device, 'operation_id': op_id,
                     'fingerprint': digest, 'execution_identity': request['execution_identity'],
                     'epoch': request['epoch'], 'source': 'simulated_controller', 'observed_at': time.time(),
                     'terminal': terminal, 'stopped': terminal, 'controller_state': self.controllers.state(device),
                     'operation_state': 'stopped' if terminal else 'running' if busy else 'unknown',
                     'controller_generation': owner[0], 'process_owner_operation_id': owner[1],
                     'process_owner_identity': json.loads(owner[2]) if owner[2] else None,
                     'checkpoint_sequence': self.db.execute('SELECT max(sequence) FROM commits').fetchone()[0]}
            if self.authority_current:
                proof['authority'] = self.authority_current()
            with self.db:
                if request['mode'] == 'ensure_stopped' and terminal:
                    self.db.execute('UPDATE operation_fences SET proof_json=? WHERE operation_id=?', (canonical(proof), op_id))
                self.db.execute('INSERT INTO reconciliation_checks(request_json,proof_json) VALUES (?,?)',
                                (canonical({k:v for k,v in request.items() if k != 'dispatcher_token'}), canonical(proof)))
            return proof

    @staticmethod
    def _identity(value):
        try:
            validate_execution_identity(value)
        except ValueError:
            raise ContractError('invalid_execution_identity') from None
