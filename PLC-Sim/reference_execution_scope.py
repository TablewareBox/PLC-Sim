"""参考设施的版本化执行身份与撤销范围；不授予权限、不停止设备。"""
from __future__ import annotations

import hashlib
import json

if __package__:
    from .reference_dispatch_binding import BINDING_SCHEMAS, validate_dispatch_binding
else:
    from reference_dispatch_binding import BINDING_SCHEMAS, validate_dispatch_binding


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and all(c.isalnum() or c in '-_.:' for c in value)


def is_dispatch_identity(value):
    return isinstance(value, dict) and value.get('schema') in BINDING_SCHEMAS


def validate_execution_identity(value, *, operation=None, device_id=None, fingerprint=None):
    """校验完整声明及其操作摘要；设施凭证/租约仍由宿主核验。"""
    if is_dispatch_identity(value):
        validate_dispatch_binding(value, operation=operation)
        target = value.get('target', {'device_id': value['dispatch']['local_device_id']})
        if device_id is not None and target['device_id'] != device_id:
            raise ValueError('dispatch_device_mismatch')
        if fingerprint is not None and value['operation_sha256'] != fingerprint:
            raise ValueError('dispatch_operation_mismatch')
        return
    fields = {'origin_instance_id', 'job_uuid', 'task_uuid', 'command_uuid', 'payload_sha256'}
    if (not isinstance(value, dict) or set(value) != fields
            or any(not _identifier(value[k]) for k in fields - {'payload_sha256'})
            or not isinstance(value['payload_sha256'], str) or len(value['payload_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in value['payload_sha256'])):
        raise ValueError('invalid_execution_identity')


def execution_scope(identity):
    """旧协议按进程撤销；生产协议按设备节点上的稳定作业撤销全部attempt。"""
    validate_execution_identity(identity)
    if is_dispatch_identity(identity):
        context = identity['dispatch']
        return {'kind': 'production_job', 'local_device_id': context['local_device_id'],
                'job_uuid': context['job_uuid']}
    return {'kind': 'legacy_origin', 'origin_instance_id': identity['origin_instance_id']}


def revoke_execution_scope(db, scope):
    """调用方持有世界锁和事务；不自行提交，也不把job/claim冒充旧进程。"""
    if (isinstance(scope, dict) and set(scope) == {'kind', 'origin_instance_id'}
            and scope['kind'] == 'legacy_origin' and _identifier(scope['origin_instance_id'])):
        db.execute('INSERT OR IGNORE INTO revoked_origins VALUES (?)', (scope['origin_instance_id'],))
    elif (isinstance(scope, dict) and set(scope) == {'kind', 'local_device_id', 'job_uuid'}
            and scope['kind'] == 'production_job'
            and all(isinstance(scope[k], str) and scope[k] for k in ('local_device_id', 'job_uuid'))):
        db.execute('INSERT OR IGNORE INTO revoked_dispatch_jobs VALUES (?,?)',
                   (scope['local_device_id'], scope['job_uuid']))
    else:
        raise ValueError('invalid_execution_scope')


def execution_revoked(db, identity):
    scope = execution_scope(identity)
    if scope['kind'] == 'legacy_origin':
        return bool(db.execute('SELECT 1 FROM revoked_origins WHERE origin=?', (scope['origin_instance_id'],)).fetchone())
    return bool(db.execute('SELECT 1 FROM revoked_dispatch_jobs WHERE local_device_id=? AND job_uuid=?',
                           (scope['local_device_id'], scope['job_uuid'])).fetchone())


def execution_scope_checkpoint(db):
    """撤销记录与模型一起保存/校验；不能重启后丢失作业隔离。"""
    rows = {
        'legacy_origins': db.execute('SELECT origin FROM revoked_origins ORDER BY origin').fetchall(),
        'production_jobs': db.execute('SELECT local_device_id,job_uuid FROM revoked_dispatch_jobs ORDER BY local_device_id,job_uuid').fetchall(),
    }
    return {'schema': 'labos.execution-revocations/v1',
            'counts': {key: len(values) for key, values in rows.items()},
            'sha256': hashlib.sha256(_canonical(rows).encode()).hexdigest()}
