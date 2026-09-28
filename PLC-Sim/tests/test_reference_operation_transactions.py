"""真实 SQLite 两连接验证提交边界；模型回调是明确的内存计数替身。"""
import copy
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

import pytest

from reference_operation_gate import ContractError
from reference_operation_transactions import ReferenceOperationTransactions


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / 'operations.sqlite3'
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE operations(operation_id TEXT PRIMARY KEY,fingerprint TEXT,request_json TEXT,
            identity_json TEXT,device_id TEXT,accepted_epoch INTEGER,status TEXT,response_json TEXT,
            accepted_at REAL,settled_at REAL);
        CREATE TABLE snapshots(sequence INTEGER PRIMARY KEY,phase TEXT,value INTEGER);
        CREATE TABLE domain_records(value TEXT);
        INSERT INTO snapshots(phase,value) VALUES ('initial',0);
    """)
    state = {'value': 0, 'restores': 0, 'effects': 0}
    def checkpoint(operation, phase):
        db.execute('INSERT INTO snapshots(phase,value) VALUES (?,?)', (phase, state['value']))
    def restore():
        assert not db.in_transaction
        state['value'] = db.execute('SELECT value FROM snapshots ORDER BY sequence DESC LIMIT 1').fetchone()[0]
        state['restores'] += 1
    ledger = ReferenceOperationTransactions(db, world_id='world', checkpoint=checkpoint, restore_committed=restore)
    payload = dict(world_id='world', operation_id='op', device_id='pump', action='dispense', args={'volume': 7})
    identity = dict(origin_instance_id='origin', job_uuid='job', task_uuid='task', command_uuid='command', payload_sha256='a'*64)
    def before():
        db.execute("INSERT INTO domain_records VALUES ('accepted')")
    def effect(response):
        state['effects'] += 1
        state['value'] += 7
        db.execute("INSERT INTO domain_records VALUES ('effect')")
        response.update(status='completed', result={'value': state['value']})
    def run(**kwargs):
        return ledger.execute_new(payload, execution_identity=identity, epoch=4,
                                  before=kwargs.get('before', before), perform=kwargs.get('perform', effect))
    yield SimpleNamespace(db=db,path=path,state=state,ledger=ledger,payload=payload,identity=identity,
                          before=before,effect=effect,run=run)
    db.close()


def test_acceptance_is_committed_before_effect_and_settlement_is_atomic(rig):
    observations = []
    def external():
        with closing(sqlite3.connect(rig.path)) as observer:
            return (observer.execute('SELECT status FROM operations').fetchall(),
                    observer.execute('SELECT phase,value FROM snapshots ORDER BY sequence').fetchall())
    def before():
        rig.before()
        observations.append(external())
    def effect(response):
        observations.append(external())
        rig.effect(response)
        observations.append(external())
    response = rig.run(before=before,perform=effect)
    assert observations[0] == ([], [('initial',0)])
    assert observations[1] == observations[2] == ([('in_progress',)], [('initial',0),('before',0)])
    assert external() == ([('completed',)], [('initial',0),('before',0),('after',7)])
    assert rig.ledger.lookup(rig.payload,rig.identity) == response
    assert rig.state == {'value':7,'restores':0,'effects':1}


@pytest.mark.parametrize('status',['completed','failed','unknown'])
def test_terminal_receipt_replays_without_reapplying_effect(rig,status):
    def effect(response):
        rig.effect(response)
        response['status'] = status
    response = rig.run(perform=effect)
    assert rig.ledger.lookup(rig.payload,rig.identity) == response
    loaded = rig.ledger.lookup(rig.payload,rig.identity)
    loaded['result']['value'] = 99
    assert rig.ledger.lookup(rig.payload,rig.identity)['result']['value'] == 7
    with pytest.raises(sqlite3.IntegrityError):
        rig.run()
    assert rig.state['effects'] == 1 and rig.state['value'] == 7


@pytest.mark.parametrize('change',['args','device','action','world','identity'])
def test_payload_or_identity_conflict_is_not_a_cache_hit(rig,change):
    rig.run()
    payload,identity = copy.deepcopy(rig.payload),copy.deepcopy(rig.identity)
    if change == 'args':payload['args']['volume'] = 9
    elif change == 'device':payload['device_id'] = 'balance'
    elif change == 'action':payload['action'] = 'tare'
    elif change == 'world':payload['world_id'] = 'other'
    else:identity['job_uuid'] = 'other'
    with pytest.raises(ContractError,match='execution_identity_conflict' if change=='identity' else 'operation_payload_conflict'):
        rig.ledger.lookup(payload,identity)
    assert rig.state['effects'] == 1


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize('failure',[RuntimeError,Interrupted])
def test_interrupted_effect_rolls_back_then_restores_and_remains_unknown(rig,failure):
    def effect(response):
        rig.effect(response)
        raise failure('interrupted')
    with pytest.raises(failure):rig.run(perform=effect)
    assert rig.state == {'value':0,'restores':1,'effects':1}
    assert rig.db.execute('SELECT value FROM domain_records').fetchall() == [('accepted',)]
    response = rig.ledger.lookup(rig.payload,rig.identity)
    assert response['status'] == 'unknown' and 'result' not in response
    with closing(sqlite3.connect(rig.path)) as reopened:
        ledger = ReferenceOperationTransactions(reopened,world_id='world',checkpoint=lambda *a:None,restore_committed=lambda:None)
        assert ledger.lookup(rig.payload,rig.identity) == response


def test_settlement_sql_failure_does_not_commit_after_snapshot(rig):
    rig.db.execute("CREATE TRIGGER fail_settle BEFORE UPDATE ON operations BEGIN SELECT RAISE(ABORT,'injected_settle'); END")
    rig.db.commit()
    with pytest.raises(sqlite3.IntegrityError,match='injected_settle'):rig.run()
    assert rig.state['value'] == 0 and rig.state['restores'] == 1
    assert rig.db.execute('SELECT phase FROM snapshots ORDER BY sequence').fetchall() == [('initial',),('before',)]
    assert rig.ledger.lookup(rig.payload,rig.identity)['status'] == 'unknown'


def test_unserializable_receipt_cannot_leave_model_effect_committed(rig):
    def effect(response):
        rig.effect(response)
        response['result'] = object()
    with pytest.raises(TypeError):rig.run(perform=effect)
    assert rig.state['value'] == 0 and rig.state['restores'] == 1
    assert rig.ledger.lookup(rig.payload,rig.identity)['status'] == 'unknown'


@pytest.mark.parametrize('point',['before','checkpoint'])
def test_acceptance_failure_never_reaches_effect(rig,point):
    def failure(*args):
        rig.before()
        raise RuntimeError('acceptance_failed')
    kwargs = {'before':failure} if point=='before' else {}
    if point=='checkpoint':rig.ledger.checkpoint = failure
    with pytest.raises(RuntimeError,match='acceptance_failed'):rig.run(**kwargs)
    assert rig.db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
    assert rig.db.execute('SELECT count(*) FROM domain_records').fetchone()[0] == 0
    assert rig.state == {'value':0,'restores':0,'effects':0}


def test_missing_operation_is_distinct_from_unknown(rig):
    assert rig.ledger.lookup(rig.payload,rig.identity) is None


def test_authority_response_is_observed_after_acceptance(rig):
    def current():
        with closing(sqlite3.connect(rig.path)) as observer:
            assert observer.execute('SELECT status FROM operations').fetchall() == [('in_progress',)]
        return dict(grant_id='grant',generation=2)
    rig.ledger.authority_current = current
    response = rig.run()
    assert response['authority'] == dict(grant_id='grant',generation=2)
    assert rig.ledger.lookup(rig.payload,rig.identity) == response


def test_restore_failure_propagates_and_does_not_reapply_effect(rig):
    def bad_restore():raise RuntimeError('restore_failed_isolate_facility')
    def effect(response):
        rig.effect(response)
        raise ValueError('effect_failed')
    rig.ledger.restore_committed = bad_restore
    with pytest.raises(RuntimeError,match='restore_failed_isolate_facility'):rig.run(perform=effect)
    assert rig.ledger.lookup(rig.payload,rig.identity)['status'] == 'unknown'
    assert rig.state['effects'] == 1


@pytest.mark.parametrize('part',['payload','identity'])
def test_first_request_is_serialized_even_without_prior_record(rig,part):
    if part == 'payload':rig.payload['args']['invalid'] = object()
    else:rig.identity['invalid'] = object()
    with pytest.raises(TypeError):rig.ledger.lookup(rig.payload,rig.identity)
    assert rig.state['effects'] == 0
    assert rig.db.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
