"""CPU参考设施的执行权与操作去重；不创建世界、时钟、数据库连接或网络。"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import math
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

if __package__:
    from .reference_dispatch_binding import SCHEMA as DISPATCH_SCHEMA, DispatchBindingError, validate_dispatch_binding
else:
    from reference_dispatch_binding import SCHEMA as DISPATCH_SCHEMA, DispatchBindingError, validate_dispatch_binding

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and all(c.isalnum() or c in '-_.:' for c in value)

class ContractError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


class ReferenceOperationGate:
    """同一设施内串行化claim/execute；不提供设备复位或世界崩溃恢复。

    facility提供world.lock、devices、dispatch及clock_error；调用方负责其生命周期。
    db由调用方新建并保持打开，checkpoint在同一db事务和world锁内写入证据。
    所有设施关闭操作须持有gate.lock，先关闭gate，再停止模型和关闭db。
    """
    REQUEST_FIELDS = {'dispatcher_token', 'executor_id', 'epoch', 'world_id', 'operation_id',
                      'device_id', 'action', 'args', 'expires_at', 'execution_identity'}

    def __init__(self, facility: Any, db: sqlite3.Connection, dispatcher_token: str,
                 world_id: str, device_actions: Mapping[str, Sequence[str]],
                 checkpoint: Callable[[str, str], None]):
        if not isinstance(dispatcher_token, str) or len(dispatcher_token) < 16:
            raise ValueError('dispatcher_token_too_short')
        self.facility, self.db, self.world_id = facility, db, world_id
        self._token = dispatcher_token
        self._actions = {device: tuple(actions) for device, actions in device_actions.items()}
        self._checkpoint = checkpoint
        self.lock = self._lock = threading.RLock()
        self._closed = False
        # 使用新连接中的独立表；存在旧表时拒绝，不重新解释历史操作。
        self.db.executescript("""
            CREATE TABLE leases (device_id TEXT PRIMARY KEY, executor_id TEXT, epoch INTEGER NOT NULL);
            CREATE TABLE operations (
              operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, request_json TEXT NOT NULL,
              identity_json TEXT NOT NULL, device_id TEXT NOT NULL, accepted_epoch INTEGER NOT NULL,
              status TEXT NOT NULL, response_json TEXT, accepted_at REAL NOT NULL, settled_at REAL);
        """)
        with self.db:
            for device in self._actions:
                self.db.execute('INSERT INTO leases VALUES (?,NULL,0)', (device,))

    def close(self) -> None:
        """关闭派发门，连接和设施由调用方关闭；不发送设备停止命令。"""
        with self._lock:
            self._closed = True

    def _auth(self, token):
        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            raise ContractError('unauthorized', 403)

    def claim(self, request):
        with self._lock:
            if self._closed:
                raise ContractError('facility_unavailable', 503)
            if not isinstance(request, dict) or set(request) != {'dispatcher_token', 'device_id', 'executor_id'}:
                raise ContractError('invalid_claim')
            self._auth(request['dispatcher_token'])
            device, executor = request['device_id'], request['executor_id']
            if not isinstance(device, str) or device not in self._actions or not identifier(executor):
                raise ContractError('invalid_device_or_executor')
            row = self.db.execute('SELECT executor_id,epoch FROM leases WHERE device_id=?', (device,)).fetchone()
            epoch = row[1]
            if row[0] != executor:
                epoch += 1
                with self.db:
                    self.db.execute('UPDATE leases SET executor_id=?,epoch=? WHERE device_id=?', (executor, epoch, device))
            return {'world_id': self.world_id, 'device_id': device, 'executor_id': executor,
                    'epoch': epoch, 'source': 'simulated_controller'}

    @staticmethod
    def _identity(value):
        if isinstance(value, dict) and value.get('schema') == DISPATCH_SCHEMA:
            try:
                validate_dispatch_binding(value)
            except DispatchBindingError:
                raise ContractError('invalid_execution_identity') from None
            return
        fields = {'origin_instance_id', 'job_uuid', 'task_uuid', 'command_uuid', 'payload_sha256'}
        if (not isinstance(value, dict) or set(value) != fields
                or any(not identifier(value[k]) for k in fields - {'payload_sha256'})
                or not isinstance(value['payload_sha256'], str) or len(value['payload_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in value['payload_sha256'])):
            raise ContractError('invalid_execution_identity')

    def _validate(self, request):
        if self._closed:
            raise ContractError('facility_unavailable', 503)
        if not isinstance(request, dict) or set(request) != self.REQUEST_FIELDS:
            raise ContractError('invalid_request_fields')
        self._auth(request['dispatcher_token'])
        device = request['device_id']
        if not isinstance(device, str) or device not in self._actions:
            raise ContractError('unsupported_device')
        lease = self.db.execute('SELECT executor_id,epoch FROM leases WHERE device_id=?', (device,)).fetchone()
        if (type(request['epoch']) is not int or request['epoch'] != lease[1]
                or request['executor_id'] != lease[0] or lease[0] is None):
            raise ContractError('stale_executor', 409)
        try:
            valid = (type(request['expires_at']) in (int, float) and math.isfinite(request['expires_at'])
                     and request['expires_at'] > time.time())
        except (ValueError, OverflowError):
            valid = False
        if not valid:
            raise ContractError('expired_request', 409)
        if request['world_id'] != self.world_id:
            raise ContractError('world_mismatch', 409)
        if not identifier(request['operation_id']):
            raise ContractError('invalid_operation_id')
        self._identity(request['execution_identity'])
        action, args = request['action'], request['args']
        if not isinstance(action, str) or action not in self._actions[device] or not isinstance(args, dict):
            raise ContractError('unsupported_action')
        try:
            canonical(args)
            inspect.signature(getattr(self.facility.devices[device], action)).bind(**args)
        except (TypeError, ValueError, OverflowError):
            raise ContractError('invalid_arguments') from None
        if request['execution_identity'].get('schema') == DISPATCH_SCHEMA:
            operation = {key: request[key] for key in ('world_id', 'operation_id', 'device_id', 'action', 'args')}
            try:
                validate_dispatch_binding(request['execution_identity'], operation=operation)
            except DispatchBindingError as error:
                raise ContractError(str(error), 409) from None
        if self._closed or self.facility.clock_error:
            raise ContractError('facility_unavailable', 503)

    def execute(self, request):
        with self._lock, self.facility.world.lock:
            self._validate(request)
            payload = {key: request[key] for key in ('world_id', 'operation_id', 'device_id', 'action', 'args')}
            digest = fingerprint(payload)
            identity = canonical(request['execution_identity'])
            operation_id = request['operation_id']
            prior = self.db.execute('SELECT fingerprint,identity_json,response_json FROM operations WHERE operation_id=?',
                                    (operation_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ContractError('operation_payload_conflict', 409)
                if prior[1] != identity:
                    raise ContractError('execution_identity_conflict', 409)
                return json.loads(prior[2]) if prior[2] else {'status': 'unknown', 'world_id': self.world_id,
                                                            'operation_id': operation_id, 'source': 'simulated',
                                                            'execution_identity': request['execution_identity']}
            now = time.time()
            with self.db:
                self.db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                    (operation_id, digest, canonical(payload), identity, request['device_id'], request['epoch'],
                     'in_progress', None, now))
                self._checkpoint(operation_id, 'before')
            response = {'operation_id': operation_id, 'world_id': self.world_id, 'source': 'simulated',
                        'execution_identity': request['execution_identity']}
            try:
                receipt = self.facility.dispatch({'device': request['device_id'], 'action': request['action'],
                                                 'args': request['args']})
                if receipt['ok']:
                    response.update(status='completed', result=receipt['result'])
                else:
                    response.update(status='failed', error=receipt['error'])
                canonical(response)
            except Exception:
                # A possible model effect is never retried after lost computation.
                response.update(status='unknown')
                self.facility.clock_error = 'execution_result_unknown'
            with self.db:
                self._checkpoint(operation_id, 'after')
                self.db.execute('UPDATE operations SET status=?,response_json=?,settled_at=? WHERE operation_id=?',
                                (response['status'], canonical(response), time.time(), operation_id))
            return response
