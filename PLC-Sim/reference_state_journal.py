"""CPU参考状态日志；复用旧表格式，不创建世界、时钟或数据库事务。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any

try:
    from .reference_operation_gate import canonical, fingerprint
except ImportError:
    from reference_operation_gate import canonical, fingerprint


@dataclass(frozen=True)
class CommittedState:
    """已核验的独立解码值；调用方仍须验证模型来源并安全还原。"""
    state: Any
    events: list[dict]
    requests: list[dict]
    time_s: float


class ReferenceStateJournal:
    """调用方持有单写者/世界锁及SQLite事务；本类不commit或rollback。

    save接收设备包编码后的状态、完整事件/请求序列及当前账本摘要。
    load在返回任何模型状态前验证封存摘要和完整数量；不调用设备还原。
    连接、表的生命周期与来源可信性由宿主负责。create只用于空白库。
    """
    def __init__(self, db: sqlite3.Connection, *, create: bool = False):
        self.db = db
        if create:
            for sql in (
                'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
                'CREATE TABLE events (sequence INTEGER PRIMARY KEY, event_json TEXT NOT NULL)',
                'CREATE TABLE requests (sequence INTEGER PRIMARY KEY, request_json TEXT NOT NULL)',
                'CREATE TABLE commits (sequence INTEGER PRIMARY KEY AUTOINCREMENT,time_s REAL,state_sha256 TEXT,event_count INTEGER,request_count INTEGER)',
            ):
                self.db.execute(sql)

    def _meta(self, key: str, value: Any) -> None:
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, canonical(value)))

    def _counts(self) -> tuple[int, int]:
        counts = []
        for table in ('events', 'requests'):
            count, maximum = self.db.execute('SELECT count(*),coalesce(max(sequence),0) FROM ' + table).fetchone()
            if count != maximum:
                raise RuntimeError('durable_event_inventory_mismatch')
            counts.append(count)
        return tuple(counts)

    def save(self, state: Any, *, time_s: float, events: Sequence[dict],
             requests: Sequence[dict], checkpoints: Mapping[str, Any] | None = None) -> tuple[int, int]:
        """追加状态与日志；事务失败后从数据库重新取计数，不保留失效游标。"""
        event_count, request_count = self._counts()
        if len(events) < event_count or len(requests) < request_count:
            raise RuntimeError('durable_event_inventory_mismatch')
        raw = canonical(state)
        for event in events[event_count:]:
            self.db.execute('INSERT INTO events VALUES (?,?)', (event['sequence'], canonical(event)))
        for request in requests[request_count:]:
            self.db.execute('INSERT INTO requests VALUES (?,?)', (request['sequence'], canonical(request)))
        if self._counts() != (len(events), len(requests)):
            raise RuntimeError('durable_event_inventory_mismatch')
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('durable_state', raw))
        for name, value in (checkpoints or {}).items():
            self._meta(name + '_checkpoint', value)
            self._meta(name + '_checkpoint_sha256', fingerprint(value))
        self.db.execute('INSERT INTO commits(time_s,state_sha256,event_count,request_count) VALUES (?,?,?,?)',
                        (time_s, hashlib.sha256(raw.encode()).hexdigest(), len(events), len(requests)))
        return len(events), len(requests)

    def load(self, *, checkpoints: Mapping[str, Any] | None = None) -> CommittedState:
        """仅读取当前连接可见的最后提交；须先回滚失败事务再调用。"""
        row = self.db.execute("SELECT value FROM meta WHERE key='durable_state'").fetchone()
        commit = self.db.execute('SELECT state_sha256,event_count,request_count,time_s FROM commits ORDER BY sequence DESC LIMIT 1').fetchone()
        if row is None or commit is None or hashlib.sha256(row[0].encode()).hexdigest() != commit[0]:
            raise RuntimeError('durable_state_digest_mismatch')
        for name, actual in (checkpoints or {}).items():
            recorded = {key: json.loads(value) for key, value in self.db.execute(
                'SELECT key,value FROM meta WHERE key IN (?,?)', (name + '_checkpoint', name + '_checkpoint_sha256'))}
            if (canonical(recorded.get(name + '_checkpoint')) != canonical(actual)
                    or recorded.get(name + '_checkpoint_sha256') != fingerprint(actual)):
                raise RuntimeError(name + '_checkpoint_mismatch')
        events = [json.loads(r[0]) for r in self.db.execute('SELECT event_json FROM events ORDER BY sequence')]
        requests = [json.loads(r[0]) for r in self.db.execute('SELECT request_json FROM requests ORDER BY sequence')]
        if self._counts() != tuple(commit[1:3]) or (len(events), len(requests)) != tuple(commit[1:3]):
            raise RuntimeError('durable_event_inventory_mismatch')
        return CommittedState(json.loads(row[0]), events, requests, commit[3])
