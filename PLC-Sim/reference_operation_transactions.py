"""CPU 参考操作的去重和两阶段持久提交；不创建连接、世界或设备。"""
from __future__ import annotations

from collections.abc import Callable
import json
import sqlite3
import time

try:
    from .reference_operation_gate import ContractError, canonical, fingerprint
except ImportError:
    from reference_operation_gate import ContractError, canonical, fingerprint


class ReferenceOperationTransactions:
    """调用方先验证请求，并持有覆盖查重、准入和执行的同一设施/世界锁。

    before 只登记授权/资源账本；perform 负责模型效果与领域回执。
    checkpoint 在同一事务保存模型状态，restore_committed 还原已提交状态。
    此处保留既有提交点，不能嵌入无关的未提交数据库事务。
    """
    def __init__(self, db: sqlite3.Connection, *, world_id: str,
                 checkpoint: Callable[[str, str], None],
                 restore_committed: Callable[[], None],
                 authority_current: Callable[[], dict] | None = None):
        self.db = db
        self.world_id = world_id
        self.checkpoint = checkpoint
        self.restore_committed = restore_committed
        self.authority_current = authority_current

    def lookup(self, payload: dict, execution_identity: dict) -> dict | None:
        """匹配已验证请求；受理未结算返回 unknown，不能作为新操作重发。"""
        operation_id = payload['operation_id']
        digest = fingerprint(payload)
        identity = canonical(execution_identity)
        prior = self.db.execute('SELECT fingerprint,identity_json,response_json FROM operations WHERE operation_id=?',
                                (operation_id,)).fetchone()
        if not prior:
            return None
        if prior[0] != digest:
            raise ContractError('operation_payload_conflict', 409)
        if prior[1] != identity:
            raise ContractError('execution_identity_conflict', 409)
        return json.loads(prior[2]) if prior[2] else {
            'status': 'unknown', 'world_id': self.world_id,
            'operation_id': operation_id, 'source': 'simulated',
            'execution_identity': execution_identity}

    def execute_new(self, payload: dict, *, execution_identity: dict, epoch: int,
                    before: Callable[[], None], perform: Callable[[dict], None]) -> dict:
        """执行已完成准入的新操作；重复 ID 在效果之前由唯一键拒绝。

        受理阶段失败由调用方恢复准入前的模型状态；效果阶段失败在这里先回滚
        SQL 再还原模型。若还原失败，调用方必须隔离该设施，不能继续派发。
        """
        operation_id = payload['operation_id']
        with self.db:
            self.db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                (operation_id, fingerprint(payload), canonical(payload), canonical(execution_identity),
                 payload['device_id'], epoch, 'in_progress', None, time.time()))
            before()
            self.checkpoint(operation_id, 'before')
        response = {'operation_id': operation_id, 'world_id': self.world_id, 'source': 'simulated',
                    'execution_identity': execution_identity}
        if self.authority_current:
            response['authority'] = self.authority_current()
        try:
            with self.db:
                perform(response)
                self.checkpoint(operation_id, 'after')
                self.db.execute('UPDATE operations SET status=?,response_json=?,settled_at=? WHERE operation_id=?',
                                (response['status'], canonical(response), time.time(), operation_id))
        except BaseException:
            self.db.rollback()
            self.restore_committed()
            raise
        return response
