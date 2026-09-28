"""真实SQLite配合显式控制器回调；物理停止由组合回归另行验证。"""
import copy
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import reference_recovery_coordinator as module
from reference_operation_gate import ContractError, canonical
from reference_recovery_coordinator import ReferenceRecoveryCoordinator


@pytest.fixture
def rig(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.executescript("""
        CREATE TABLE leases(device_id TEXT PRIMARY KEY,executor_id TEXT,epoch INTEGER);
        CREATE TABLE retired_executors(device_id TEXT,executor_id TEXT,PRIMARY KEY(device_id,executor_id));
        CREATE TABLE operations(operation_id TEXT PRIMARY KEY,fingerprint TEXT,identity_json TEXT,device_id TEXT,status TEXT);
        CREATE TABLE operation_fences(operation_id TEXT PRIMARY KEY,device_id TEXT,fingerprint TEXT,identity_json TEXT,proof_json TEXT);
        CREATE TABLE revoked_origins(origin TEXT PRIMARY KEY);
        CREATE TABLE controller_processes(device_id TEXT PRIMARY KEY,generation INTEGER,operation_id TEXT,identity_json TEXT,recovery_json TEXT);
        CREATE TABLE commits(sequence INTEGER PRIMARY KEY AUTOINCREMENT);
        CREATE TABLE reconciliation_checks(request_json TEXT,proof_json TEXT);
    """)
    for device in ('pump', 'reader'):
        db.execute('INSERT INTO leases VALUES (?,NULL,0)', (device,))
        db.execute('INSERT INTO controller_processes VALUES (?,1,NULL,NULL,NULL)', (device,))
    db.execute('INSERT INTO commits DEFAULT VALUES'); db.commit()
    state = SimpleNamespace(active_token='valid', now=0, moving={'pump':False,'reader':False}, stops=[], auth=[], fail_save=False)
    monkeypatch.setattr(module.time, 'monotonic', lambda: state.now)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: setattr(state, 'now', state.now + seconds))
    def authenticate(request):
        state.auth.append(copy.deepcopy(request))
        if request.get('dispatcher_token') != state.active_token:
            raise ContractError('unauthorized', 403)
    def interrupt(device, reason):
        state.stops.append((device,reason)); state.moving[device]=False
        db.execute('UPDATE controller_processes SET recovery_json=? WHERE device_id=?',
                   (canonical({'reason':reason}),device))
    def save():
        if state.fail_save: raise RuntimeError('checkpoint_failed')
        db.execute('INSERT INTO commits DEFAULT VALUES')
    controllers=SimpleNamespace(busy=lambda d:state.moving[d],state=lambda d:'running' if state.moving[d] else 'idle',interrupt=interrupt)
    gate=ReferenceRecoveryCoordinator(db,world_id='world',environment_instance_id='instance',device_ids=('pump','reader'),
        lock=threading.RLock(),world_lock=threading.RLock(),controllers=controllers,authenticate=authenticate,
        save_durable=save,authority_current=lambda:{'grant_id':'grant','generation':1})
    yield SimpleNamespace(db=db,state=state,gate=gate)
    db.close()


def claim(rig, executor='one', device='pump'):
    return rig.gate.claim(dict(dispatcher_token='valid',executor_id=executor,device_id=device))


def request(rig, mode='inspect'):
    lease=claim(rig)
    return dict(dispatcher_token='valid',executor_id='one',epoch=lease['epoch'],world_id='world',device_id='pump',
                operation_id='operation',fingerprint='a'*64,mode=mode,expires_at=module.time.time()+60,
                execution_identity=dict(origin_instance_id='origin',task_uuid='task',job_uuid='job',command_uuid='command',payload_sha256='b'*64))


def prior(rig, body, owner=True):
    rig.db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
        (body['operation_id'],body['fingerprint'],canonical(body['execution_identity']),body['device_id'],'completed'))
    if owner:
        rig.db.execute('UPDATE controller_processes SET operation_id=?,identity_json=? WHERE device_id=?',
            (body['operation_id'],canonical(body['execution_identity']),body['device_id']))
    rig.db.commit()


def test_claim_reuses_epoch_then_permanently_retires_previous_executor(rig):
    a=claim(rig); assert a['epoch']==1 and a['authority']['generation']==1
    count=rig.db.execute('SELECT count(*) FROM commits').fetchone()[0]
    assert claim(rig)==a and rig.db.execute('SELECT count(*) FROM commits').fetchone()[0]==count
    assert claim(rig,'two')['epoch']==2
    with pytest.raises(ContractError,match='retired_executor'):claim(rig)
    assert rig.db.execute("SELECT executor_id,epoch FROM leases WHERE device_id='reader'").fetchone()==(None,0)


def test_claim_calls_real_stop_port_and_does_not_equate_lease_with_no_recovery(rig):
    claim(rig); rig.state.moving['pump']=True
    result=claim(rig,'two')
    assert rig.state.stops==[('pump','executor_replaced')]
    assert result['recovery']['stopped'] is True
    assert result['recovery']['reason']=='executor_replaced'


def test_unknown_inspect_cannot_invent_terminal_proof(rig):
    body=request(rig); proof=rig.gate.reconcile(body)
    assert proof['terminal'] is proof['stopped'] is False and proof['operation_state']=='unknown'
    assert rig.db.execute('SELECT count(*) FROM operation_fences').fetchone()[0]==0
    recorded=json.loads(rig.db.execute('SELECT request_json FROM reconciliation_checks').fetchone()[0])
    assert 'dispatcher_token' not in recorded and not rig.state.stops


def test_fence_unknown_operation_revokes_origin_and_cached_proof_is_generation_bound(rig):
    body=request(rig,'ensure_stopped'); proof=rig.gate.reconcile(body)
    assert proof['terminal'] and proof['stopped'] and not rig.state.stops
    assert rig.db.execute('SELECT origin FROM revoked_origins').fetchall()==[('origin',)]
    assert rig.gate.reconcile(body)==proof
    with rig.db:rig.db.execute("UPDATE controller_processes SET generation=2 WHERE device_id='pump'")
    changed=rig.gate.reconcile(body)
    assert changed['evidence_id']!=proof['evidence_id'] and changed['controller_generation']==2


def test_stop_only_interrupts_matching_process_owner(rig):
    body=request(rig,'ensure_stopped');prior(rig,body);rig.state.moving['pump']=True
    proof=rig.gate.reconcile(body)
    assert rig.state.stops==[('pump','reconciliation_stop')] and proof['stopped']
    assert proof['process_owner_operation_id']==body['operation_id']


def test_busy_different_owner_is_not_stopped_or_reported_terminal(rig):
    body=request(rig,'ensure_stopped');prior(rig,body,owner=False);rig.state.moving['pump']=True
    with rig.db:rig.db.execute("UPDATE controller_processes SET operation_id='different' WHERE device_id='pump'")
    proof=rig.gate.reconcile(body)
    assert not rig.state.stops and proof['stopped'] is proof['terminal'] is False
    assert proof['operation_state']=='running'
    assert rig.db.execute('SELECT proof_json FROM operation_fences').fetchone()[0] is None


@pytest.mark.parametrize('field,value,error', [
    ('world_id','other','world_mismatch'), ('epoch',99,'stale_executor'),
    ('epoch',True,'stale_executor'), ('device_id','unknown','unsupported_device'),
    ('expires_at',0,'expired_request'), ('expires_at',float('nan'),'expired_request'),
    ('expires_at',True,'expired_request'), ('mode','restart','invalid_reconciliation'),
    ('fingerprint','broken','invalid_reconciliation'), ('execution_identity',{},'invalid_execution_identity'),
    ('dispatcher_token','old','unauthorized'),
])
def test_invalid_reconciliation_has_no_fence_or_stop(rig,field,value,error):
    body=request(rig,'ensure_stopped');body[field]=value
    with pytest.raises(ContractError,match=error):rig.gate.reconcile(body)
    assert not rig.state.stops
    assert rig.db.execute('SELECT count(*) FROM operation_fences').fetchone()[0]==0


@pytest.mark.parametrize('field,error', [('fingerprint','operation_payload_conflict'),('execution_identity','execution_identity_conflict')])
def test_existing_operation_cannot_be_rebound(rig,field,error):
    body=request(rig,'ensure_stopped');prior(rig,body)
    if field=='fingerprint':body[field]='c'*64
    else:body[field]['origin_instance_id']='different'
    with pytest.raises(ContractError,match=error):rig.gate.reconcile(body)
    assert rig.db.execute('SELECT count(*) FROM operation_fences').fetchone()[0]==0


def test_authority_rechecked_after_wait_before_proof_publication(rig,monkeypatch):
    body=request(rig,'ensure_stopped');rig.state.moving['pump']=True
    def tick(seconds):
        rig.state.now+=seconds;rig.state.active_token='replacement'
    monkeypatch.setattr(module.time,'sleep',tick)
    with pytest.raises(ContractError,match='unauthorized'):rig.gate.reconcile(body)
    assert rig.db.execute('SELECT count(*) FROM reconciliation_checks').fetchone()[0]==0


def test_checkpoint_error_propagates_and_does_not_grant_new_epoch(rig):
    claim(rig);rig.state.moving['pump']=True;rig.state.fail_save=True
    with pytest.raises(RuntimeError,match='checkpoint_failed'):claim(rig,'two')
    assert rig.db.execute("SELECT executor_id,epoch FROM leases WHERE device_id='pump'").fetchone()==('one',1)
    assert rig.state.stops==[('pump','executor_replaced')]
    # 实际停止已发生；调用方仍负责失败后的内存/持久状态隔离。
    assert rig.state.moving['pump'] is False
