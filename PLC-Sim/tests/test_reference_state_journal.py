"""真实SQLite事务与损坏记录检查；不构造设备或世界。"""
import hashlib
import json
import sqlite3

import pytest

from reference_state_journal import ReferenceStateJournal
from reference_operation_gate import canonical


@pytest.fixture
def db():
    connection = sqlite3.connect(':memory:')
    yield connection
    connection.close()


@pytest.fixture
def journal(db):
    return ReferenceStateJournal(db, create=True)


def event(sequence):
    return {'sequence': sequence, 'time_s': sequence / 10, 'kind': 'observed'}


def request(sequence):
    return {'sequence': sequence, 'request': {'operation': str(sequence)}, 'response': {'ok': True}}


def save(journal, *, state=None, events=None, requests=None, checkpoints=None):
    return journal.save({'model': [1, 2]} if state is None else state, time_s=2.5,
                        events=[event(1)] if events is None else events,
                        requests=[request(1)] if requests is None else requests, checkpoints=checkpoints)


def test_exact_legacy_format_and_independent_readback(db, journal):
    checkpoints = {'authority': {'generation': 1}, 'sample': {'reservations': []}}
    with db:
        assert save(journal, checkpoints=checkpoints) == (1, 1)
    meta = dict(db.execute('SELECT key,value FROM meta'))
    assert meta['durable_state'] == canonical({'model': [1, 2]})
    assert json.loads(meta['authority_checkpoint']) == checkpoints['authority']
    assert json.loads(meta['sample_checkpoint_sha256']) == hashlib.sha256(canonical(checkpoints['sample']).encode()).hexdigest()
    assert db.execute('SELECT time_s,state_sha256,event_count,request_count FROM commits').fetchone() == (
        2.5, hashlib.sha256(meta['durable_state'].encode()).hexdigest(), 1, 1)
    loaded = journal.load(checkpoints=checkpoints)
    assert loaded.time_s == 2.5 and loaded.events == [event(1)] and loaded.requests == [request(1)]
    loaded.state['model'].append(99); loaded.events.clear()
    assert journal.load().state == {'model': [1, 2]} and journal.load().events == [event(1)]


def test_append_and_reopen_reuses_database_inventory(db, journal):
    with db:
        save(journal)
    reopened = ReferenceStateJournal(db)
    with db:
        assert save(reopened, state={'model': 3}, events=[event(1), event(2)]) == (2, 1)
    assert db.execute('SELECT count(*) FROM commits').fetchone()[0] == 2
    assert reopened.load().events == [event(1), event(2)]
    assert reopened.load().state == {'model': 3}


def test_save_does_not_commit_other_callers_work(tmp_path):
    path = tmp_path / 'state.db'
    writer, reader = sqlite3.connect(path), sqlite3.connect(path)
    try:
        journal = ReferenceStateJournal(writer, create=True)
        writer.execute('CREATE TABLE other (value TEXT)'); writer.commit()
        writer.execute("INSERT INTO other VALUES ('uncommitted')")
        save(journal)
        assert writer.in_transaction
        assert reader.execute('SELECT count(*) FROM commits').fetchone()[0] == 0
        assert reader.execute('SELECT count(*) FROM other').fetchone()[0] == 0
        writer.rollback()
        assert writer.execute('SELECT count(*) FROM commits').fetchone()[0] == 0
        assert writer.execute('SELECT count(*) FROM other').fetchone()[0] == 0
    finally:
        reader.close(); writer.close()


def test_failed_transaction_can_retry_without_stale_append_counters(db, journal):
    with db:
        save(journal)
    with pytest.raises(RuntimeError, match='later_failure'):
        with db:
            save(journal, events=[event(1), event(2)], state={'model': 'uncommitted'})
            raise RuntimeError('later_failure')
    assert journal.load().state == {'model': [1, 2]}
    assert journal.load().events == [event(1)]
    with db:
        save(journal, events=[event(1), event(2)], state={'model': 'retry'})
    assert journal.load().state == {'model': 'retry'} and len(journal.load().events) == 2


@pytest.mark.parametrize('name', ['authority', 'sample'])
@pytest.mark.parametrize('damage', ['actual', 'recorded', 'digest', 'missing'])
def test_checkpoint_mismatch_rejected_before_returning_state(db, journal, name, damage):
    checks = {name: {'generation': 1}}
    with db:
        save(journal, checkpoints=checks)
        if damage == 'actual': checks[name] = {'generation': 2}
        elif damage == 'recorded': db.execute('UPDATE meta SET value=? WHERE key=?', ('{}', name + '_checkpoint'))
        elif damage == 'digest': db.execute('UPDATE meta SET value=? WHERE key=?', ('"wrong"', name + '_checkpoint_sha256'))
        else: db.execute('DELETE FROM meta WHERE key=?', (name + '_checkpoint',))
    with pytest.raises(RuntimeError, match=name + '_checkpoint_mismatch'):
        journal.load(checkpoints=checks)


@pytest.mark.parametrize('damage', ['state', 'state_missing', 'commit_missing', 'event_missing', 'request_missing', 'count'])
def test_incomplete_or_corrupt_record_cannot_supply_model_state(db, journal, damage):
    with db:
        save(journal)
        if damage == 'state': db.execute("UPDATE meta SET value='{}' WHERE key='durable_state'")
        elif damage == 'state_missing': db.execute("DELETE FROM meta WHERE key='durable_state'")
        elif damage == 'commit_missing': db.execute('DELETE FROM commits')
        elif damage == 'event_missing': db.execute('DELETE FROM events')
        elif damage == 'request_missing': db.execute('DELETE FROM requests')
        else: db.execute('UPDATE commits SET event_count=3')
    with pytest.raises(RuntimeError, match='durable_(state_digest|event_inventory)_mismatch'):
        journal.load()


def test_failed_insert_rolls_back_whole_host_transaction(db, journal):
    with db:
        save(journal)
    with pytest.raises(sqlite3.IntegrityError):
        with db:
            save(journal, events=[event(1), event(2)], requests=[request(1), request(1)])
    assert len(journal.load().events) == 1
    assert db.execute('SELECT count(*) FROM commits').fetchone()[0] == 1


@pytest.mark.parametrize('events,requests', [([], [request(1)]), ([event(1)], [])])
def test_truncated_caller_inventory_rejected(db, journal, events, requests):
    with db: save(journal)
    with pytest.raises(RuntimeError, match='durable_event_inventory_mismatch'):
        with db: save(journal, events=events, requests=requests)
    assert len(journal.load().events) == len(journal.load().requests) == 1


def test_readonly_database_load_has_no_writes(tmp_path):
    path = tmp_path / 'state.db'
    db = sqlite3.connect(path)
    with db: save(ReferenceStateJournal(db, create=True))
    db.close(); before = path.read_bytes()
    reader = sqlite3.connect('file:' + str(path) + '?mode=ro&immutable=1', uri=True)
    try:
        journal = ReferenceStateJournal(reader)
        assert journal.load().state == {'model': [1, 2]}
        assert not reader.in_transaction
    finally: reader.close()
    assert path.read_bytes() == before
