"""撤销范围有类型，且授权库存保留完整身份；仅用临时SQLite。"""
import copy
import json
import sqlite3

import pytest

from reference_dispatch_binding import bind_dispatch
from reference_execution_authority import Authority
from reference_execution_scope import (execution_scope, execution_revoked, revoke_execution_scope,
                                       execution_scope_checkpoint, validate_execution_identity)


def binding(**changes):
    context=dict(schema='unilab.device-execution-identity/v1',kind='edge_claim',job_uuid='job',
        task_uuid='task',local_device_id='os-pump',action_name='execute',execution_source='unit',
        command_uuid='command',node_uuid='node',claim_uuid='claim',attempt=1,fences=[['os-pump',1]])
    context.update(changes)
    operation=dict(world_id='world',operation_id='op',device_id='pump',action='status',args={})
    return bind_dispatch(context,parameters={'command':'frozen'},operation=operation,
                         target={'local_device_id':'os-pump','device_id':'pump'})


def test_job_scope_is_stable_across_attempts_and_keeps_local_device_identity():
    scope=execution_scope(binding())
    assert scope==dict(kind='production_job',local_device_id='os-pump',job_uuid='job')
    assert execution_scope(binding(claim_uuid='claim-two',attempt=2,fences=[['os-pump',2]]))==scope
    assert execution_scope(binding(job_uuid='other-job'))!=scope
    legacy=dict(origin_instance_id='origin',job_uuid='job',task_uuid='task',command_uuid='command',payload_sha256='a'*64)
    assert execution_scope(legacy)==dict(kind='legacy_origin',origin_instance_id='origin')


@pytest.mark.parametrize('change', [
    {'schema':'unknown'}, {'target':{'local_device_id':'other','device_id':'pump'}},
    {'operation_sha256':'bad'}, {'origin_instance_id':'fake'},
])
def test_bad_binding_is_not_silently_interpreted_as_legacy(change):
    with pytest.raises(ValueError):validate_execution_identity({**binding(),**change})


def test_scope_write_is_transactional_and_does_not_revoke_other_jobs():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE revoked_origins(origin TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE revoked_dispatch_jobs(local_device_id TEXT,job_uuid TEXT,PRIMARY KEY(local_device_id,job_uuid))')
        before=execution_scope_checkpoint(db)
        revoke_execution_scope(db,execution_scope(binding()))
        assert execution_revoked(db,binding(attempt=2,claim_uuid='new'))
        assert not execution_revoked(db,binding(job_uuid='other'))
        assert db.execute('SELECT * FROM revoked_origins').fetchall()==[]
        db.rollback()
        assert execution_scope_checkpoint(db)==before


def test_authority_versions_require_explicit_selection_without_in_place_upgrade(tmp_path):
    token='admin-only-credential-012345678901234567890'
    for dispatch in (False,True):
        with sqlite3.connect(tmp_path/str(dispatch)) as db:
            a=Authority(db,token,create=True,dispatch_identities=dispatch)
            before='\n'.join(db.iterdump())
            with pytest.raises(RuntimeError,match='incompatible_existing_authority'):
                Authority(db,token,dispatch_identities=not dispatch)
            assert '\n'.join(db.iterdump())==before
            assert a.checkpoint()['schema']=='labos.plate-authority/v'+('2' if dispatch else '1')


def test_v2_records_frozen_identity_and_scope_without_committing(tmp_path):
    with sqlite3.connect(tmp_path/'authority.sqlite3') as db:
        authority=Authority(db,'admin-only-credential-012345678901234567890',create=True,dispatch_identities=True)
        # 调用方的已认证grant；只测试存储边界，不冒充设施授权流程。
        db.execute("UPDATE authority_state SET generation=1,active_grant_id='grant' WHERE id=1")
        db.commit()
        before=authority.checkpoint()
        identity=binding()
        authority.record_operation('op','executor',2,identity)
        identity['dispatch']['claim_uuid']='mutated-after-record'
        raw=db.execute('SELECT identity_json FROM operation_execution_scopes').fetchone()[0]
        assert json.loads(raw)['dispatch']['claim_uuid']=='claim'
        assert db.execute('SELECT origin_instance_id FROM operation_authority').fetchone()==(None,)
        assert authority.revocation_scopes('grant')==[execution_scope(binding())]
        assert authority.checkpoint()['execution_scopes_sha256']!=before['execution_scopes_sha256']
        db.rollback()
        assert authority.checkpoint()==before
