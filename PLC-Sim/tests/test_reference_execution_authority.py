"""参考授权账本的凭证、代次和调用方事务边界；仅使用临时SQLite。"""
import json
import sqlite3

import pytest

from reference_execution_authority import Authority, AuthorityError

ADMIN = 'test-admin-credential-01234567890123456789'
TOKEN_A = 'test-edge-a-credential-01234567890123456789'
TOKEN_B = 'test-edge-b-credential-01234567890123456789'


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'authority.sqlite3'
    db = sqlite3.connect(path)
    with db:
        authority = Authority(db, ADMIN, create=True)
    yield authority, db, path
    db.close()


def request(**changes):
    return dict(admin_token=ADMIN, world_id='world-one', action='activate',
                transition_id='activate-a', expected_generation=0,
                grant_id='edge-a', grant_token=TOKEN_A) | changes


def transition(authority, body):
    # 宿主在自己的锁和事务内顺序验证、应用并记录回执。
    safe, prior = authority.validate_transition(body, 'world-one')
    if prior is not None:
        return prior
    previous, current = authority.apply(safe)
    response = {'previous': previous, 'authority': current}
    authority.record_transition(safe, response)
    return response


@pytest.mark.parametrize('token', [None, 1, ADMIN, TOKEN_A, 'unknown'])
def test_no_executor_id_or_admin_credential_fallback(store, token):
    authority, _, _ = store
    with pytest.raises(AuthorityError, match='unauthorized'):
        authority.authenticate(token)


def test_activation_replay_and_stale_generation_are_distinct(store):
    authority, db, _ = store
    with db:
        first = transition(authority, request())
        assert transition(authority, request()) == first
        with pytest.raises(AuthorityError, match='authority_transition_conflict'):
            transition(authority, request(grant_token=TOKEN_B))
        with pytest.raises(AuthorityError, match='authority_generation_conflict'):
            transition(authority, request(transition_id='activate-b', grant_id='edge-b', grant_token=TOKEN_B))
    assert authority.authenticate(TOKEN_A) == {'grant_id': 'edge-a', 'generation': 1}
    assert db.execute('SELECT count(*) FROM authority_transitions').fetchone()[0] == 1


@pytest.mark.parametrize('changes,code', [
    ({'admin_token': TOKEN_A}, 'authority_admin_unauthorized'),
    ({'world_id': 'world-two'}, 'world_mismatch'),
    ({'extra': True}, 'invalid_authority_request'),
    ({'expected_generation': True}, 'invalid_authority_transition'),
    ({'expected_generation': -1}, 'invalid_authority_transition'),
    ({'transition_id': '../escape'}, 'invalid_authority_transition'),
    ({'grant_token': ADMIN}, 'invalid_authority_grant'),
    ({'grant_token': 'short'}, 'invalid_authority_grant'),
])
def test_rejected_transition_does_not_change_inventory(store, changes, code):
    authority, _, _ = store
    before = authority.checkpoint()
    with pytest.raises(AuthorityError, match=code):
        authority.validate_transition(request(**changes), 'world-one')
    assert authority.checkpoint() == before


def test_revoke_cannot_be_undone_by_replaying_old_activation(store):
    authority, db, _ = store
    with db:
        original = transition(authority, request())
        transition(authority, {'admin_token': ADMIN, 'world_id': 'world-one', 'action': 'revoke',
                              'transition_id': 'revoke-a', 'expected_generation': 1})
        assert transition(authority, request()) == original
    assert authority.current() == {'grant_id': None, 'generation': 2}
    with pytest.raises(AuthorityError, match='authority_revoked'):
        authority.authenticate(TOKEN_A)


@pytest.mark.parametrize('changes', [{'grant_id': 'edge-a', 'grant_token': TOKEN_B},
                                    {'grant_id': 'edge-b', 'grant_token': TOKEN_A}])
def test_grant_identity_and_credential_cannot_be_reused(store, changes):
    authority, db, _ = store
    with db:
        transition(authority, request())
        with pytest.raises(AuthorityError, match='authority_grant_already_used'):
            authority.validate_transition(request(transition_id='reuse', expected_generation=1, **changes), 'world-one')


def test_installation_does_not_commit_callers_existing_transaction(tmp_path):
    with sqlite3.connect(tmp_path/'install.sqlite3') as db:
        db.execute('CREATE TABLE caller(value TEXT)')
        db.execute("INSERT INTO caller VALUES ('pending')")
        Authority(db, ADMIN, create=True)
        db.rollback()
        assert db.execute('SELECT * FROM caller').fetchall() == []
        assert db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'authority_%'").fetchall() == []
    db.close()


def test_uncommitted_transition_is_invisible_and_rollback_restores_generation(store):
    authority, db, path = store
    before = authority.checkpoint()
    db.execute('BEGIN')
    transition(authority, request())
    observer = sqlite3.connect(path)
    try:
        assert Authority(observer, ADMIN).current() == {'grant_id': None, 'generation': 0}
    finally:
        observer.close()
    db.rollback()
    assert authority.checkpoint() == before
    with pytest.raises(AuthorityError, match='unauthorized'):
        authority.authenticate(TOKEN_A)


def test_failed_receipt_insert_rolls_back_grant_and_revocation(store):
    authority, db, _ = store
    with db:
        transition(authority, request())
        db.execute("CREATE TRIGGER fail_transition BEFORE INSERT ON authority_transitions BEGIN SELECT RAISE(ABORT, 'receipt-failed'); END")
    before = authority.checkpoint()
    with pytest.raises(sqlite3.IntegrityError, match='receipt-failed'):
        with db:
            transition(authority, request(transition_id='activate-b', expected_generation=1,
                                          grant_id='edge-b', grant_token=TOKEN_B))
    assert authority.checkpoint() == before
    assert authority.authenticate(TOKEN_A)['generation'] == 1
    with pytest.raises(AuthorityError, match='unauthorized'):
        authority.authenticate(TOKEN_B)


def test_reopen_preserves_generation_binding_and_excludes_raw_credentials(store):
    authority, db, path = store
    with db:
        transition(authority, request())
        authority.record_operation('op-one', 'executor-one', 4, {'origin_instance_id': 'origin-one'})
        transition(authority, request(transition_id='activate-b', expected_generation=1,
                                      grant_id='edge-b', grant_token=TOKEN_B))
    before = authority.checkpoint()
    observer = sqlite3.connect(path)
    try:
        restored = Authority(observer, ADMIN)
        assert restored.checkpoint() == before
        assert restored.authenticate(TOKEN_B)['generation'] == 2
        with pytest.raises(AuthorityError, match='authority_revoked'):
            restored.authenticate(TOKEN_A)
        assert observer.execute('SELECT * FROM operation_authority').fetchone() == (
            'op-one', 'edge-a', 1, 'executor-one', 4, 'origin-one')
        evidence = '\n'.join(observer.iterdump()) + json.dumps(before)
        assert all(token not in evidence for token in (ADMIN, TOKEN_A, TOKEN_B))
        with pytest.raises(RuntimeError, match='incompatible_existing_authority'):
            Authority(observer, TOKEN_A)
    finally:
        observer.close()


def test_checkpoint_changes_when_resource_evidence_changes(store):
    authority, db, _ = store
    before = authority.checkpoint()
    db.execute("INSERT INTO operation_kinds VALUES ('op-one','reservation')")
    after = authority.checkpoint()
    assert before['resource_tables_sha256']['operation_kinds'] != after['resource_tables_sha256']['operation_kinds']
    db.rollback()
    assert authority.checkpoint() == before
