"""操作门控的派发、去重和记录故障边界；仅使用临时数据库。"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import sqlite3
import threading
import time

import pytest

from reference_operation_gate import ContractError, ReferenceOperationGate

TOKEN = "test-operation-token-at-least-16"


class Counter:
    def act(self, value=1):
        return value


@pytest.fixture
def rig(tmp_path):
    db = sqlite3.connect(tmp_path / "operations.sqlite3", check_same_thread=False)
    facility = SimpleNamespace(world=SimpleNamespace(lock=threading.RLock()),
                               devices={"one": Counter(), "two": Counter()}, clock_error=None)
    effects, checkpoints = [], []

    def dispatch(command):
        effects.append(command)
        return {"ok": True, "result": command["args"].get("value", 1)}

    facility.dispatch = dispatch
    gate = ReferenceOperationGate(facility, db, TOKEN, "world-test",
                                  {"one": ("act",), "two": ("act",)},
                                  lambda operation, phase: checkpoints.append((operation, phase)))
    yield gate, facility, db, effects, checkpoints
    gate.close()
    db.close()


def request(gate, *, device="one", operation="op-one", executor="edge-one"):
    claim = gate.claim({"dispatcher_token": TOKEN, "device_id": device, "executor_id": executor})
    return {"dispatcher_token": TOKEN, "device_id": device, "executor_id": executor,
            "epoch": claim["epoch"], "world_id": "world-test", "operation_id": operation,
            "action": "act", "args": {"value": 2}, "expires_at": time.time() + 60,
            "execution_identity": {"origin_instance_id": "origin", "task_uuid": "task-" + operation,
                                   "job_uuid": "job-" + operation, "command_uuid": "cmd-" + operation,
                                   "payload_sha256": "a" * 64}}


def test_concurrent_duplicates_share_one_effect_and_two_checkpoints(rig):
    gate, _, db, effects, checkpoints = rig
    body = request(gate)
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(gate.execute, [body] * 16))
    assert all(reply == replies[0] for reply in replies)
    assert len(effects) == 1
    assert checkpoints == [("op-one", "before"), ("op-one", "after")]
    assert db.execute("SELECT status FROM operations").fetchall() == [("completed",)]


def test_operation_id_is_shared_across_devices(rig):
    gate, _, _, effects, _ = rig
    gate.execute(request(gate))
    with pytest.raises(ContractError, match="operation_payload_conflict"):
        gate.execute(request(gate, device="two"))
    assert len(effects) == 1


@pytest.mark.parametrize("invalid", ["expired", "revoked"])
def test_prior_result_does_not_bypass_current_authority(rig, invalid):
    gate, _, _, effects, _ = rig
    body = request(gate)
    gate.execute(body)
    if invalid == "expired":
        body["expires_at"] = 0
        code = "expired_request"
    else:
        request(gate, executor="edge-two")
        code = "stale_executor"
    with pytest.raises(ContractError, match=code):
        gate.execute(body)
    assert len(effects) == 1


def test_exception_after_effect_marks_unknown_and_prevents_redispatch(rig):
    gate, facility, db, effects, _ = rig
    def broken(command):
        effects.append(command)
        raise RuntimeError("effect-already-happened")
    facility.dispatch = broken
    body = request(gate)
    reply = gate.execute(body)
    assert reply["status"] == "unknown"
    assert facility.clock_error == "execution_result_unknown"
    assert db.execute("SELECT status FROM operations").fetchone() == ("unknown",)
    with pytest.raises(ContractError, match="facility_unavailable"):
        gate.execute(body)
    assert len(effects) == 1


def test_after_checkpoint_failure_preserves_in_progress_without_redispatch(rig):
    gate, _, db, effects, _ = rig
    def broken_checkpoint(operation, phase):
        if phase == "after":
            raise OSError("disk-full")
    gate._checkpoint = broken_checkpoint
    body = request(gate)
    with pytest.raises(OSError, match="disk-full"):
        gate.execute(body)
    assert db.execute("SELECT status,response_json FROM operations").fetchone() == ("in_progress", None)
    assert gate.execute(body)["status"] == "unknown"
    assert len(effects) == 1


def test_closed_gate_rejects_without_accessing_closed_database(rig):
    gate, _, db, effects, _ = rig
    body = request(gate)
    gate.close()
    db.close()
    with pytest.raises(ContractError, match="facility_unavailable"):
        gate.execute(body)
    with pytest.raises(ContractError, match="facility_unavailable"):
        gate.claim({"dispatcher_token": TOKEN, "device_id": "one", "executor_id": "edge-one"})
    assert effects == []


def test_existing_operation_tables_are_never_reinitialized(rig):
    gate, facility, db, _, _ = rig
    gate.execute(request(gate))
    before = db.execute("SELECT * FROM operations").fetchall()
    with pytest.raises(sqlite3.OperationalError):
        ReferenceOperationGate(facility, db, TOKEN, "other-world", {"one": ("act",)}, lambda *_: None)
    assert db.execute("SELECT * FROM operations").fetchall() == before
