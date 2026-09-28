"""参考设施的生产派发身份和操作摘要；不把声明身份当作授权。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = 'labos.production-dispatch-binding/v1'
TARGET_SCHEMA = 'labos.production-dispatch-binding/v2'
BINDING_SCHEMAS = (SCHEMA, TARGET_SCHEMA)
CONTEXT_SCHEMA = 'unilab.device-execution-identity/v1'
CONTEXT_FIELDS = {'schema', 'kind', 'job_uuid', 'task_uuid', 'local_device_id',
                  'action_name', 'execution_source', 'command_uuid', 'node_uuid',
                  'claim_uuid', 'attempt', 'fences'}
OPERATION_FIELDS = {'world_id', 'operation_id', 'device_id', 'action', 'args'}


class DispatchBindingError(ValueError):
    """身份或操作绑定不一致；调用方在模型效果之前拒绝。"""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_dispatch_binding(value: dict, *, operation: dict | None = None) -> None:
    """核对版本、完整身份和操作绑定；不查询OS权威或替代设施租约。"""
    fields = {'schema', 'dispatch', 'payload_sha256', 'operation_sha256'}
    if isinstance(value, dict) and value.get('schema') == TARGET_SCHEMA:
        fields.add('target')
    if (not isinstance(value, dict)
            or set(value) != fields
            or value['schema'] not in BINDING_SCHEMAS
            or not _sha(value['payload_sha256']) or not _sha(value['operation_sha256'])):
        raise DispatchBindingError('invalid_production_dispatch_binding')
    context = value['dispatch']
    if (not isinstance(context, dict) or set(context) != CONTEXT_FIELDS
            or context['schema'] != CONTEXT_SCHEMA
            or any(not _text(context[k]) for k in ('job_uuid', 'task_uuid', 'command_uuid',
                                                   'local_device_id', 'action_name', 'execution_source'))
            or (context['node_uuid'] is not None and not _text(context['node_uuid']))):
        raise DispatchBindingError('invalid_production_dispatch_context')
    if context['kind'] == 'job':
        if context['claim_uuid'] is not None or context['attempt'] is not None or context['fences'] != []:
            raise DispatchBindingError('invalid_local_dispatch_context')
    elif context['kind'] == 'edge_claim':
        if (not _text(context['claim_uuid']) or type(context['attempt']) is not int
                or context['attempt'] < 1 or not isinstance(context['fences'], list)):
            raise DispatchBindingError('invalid_edge_dispatch_context')
        keys = set()
        for pair in context['fences']:
            if (not isinstance(pair, list) or len(pair) != 2 or not _text(pair[0])
                    or type(pair[1]) is not int or pair[1] < 1 or pair[0] in keys):
                raise DispatchBindingError('invalid_edge_dispatch_fences')
            keys.add(pair[0])
    else:
        raise DispatchBindingError('unsupported_dispatch_kind')
    target = value.get('target')
    if value['schema'] == TARGET_SCHEMA:
        if (not isinstance(target, dict) or set(target) != {'local_device_id', 'device_id'}
                or any(not _text(target[key]) for key in target)
                or target['local_device_id'] != context['local_device_id']):
            raise DispatchBindingError('invalid_dispatch_target')
    if operation is not None:
        if not isinstance(operation, dict) or set(operation) != OPERATION_FIELDS:
            raise DispatchBindingError('invalid_bound_operation')
        expected_device = context['local_device_id'] if target is None else target['device_id']
        if expected_device != operation['device_id']:
            raise DispatchBindingError('dispatch_device_mismatch')
        try:
            matches = _digest(operation) == value['operation_sha256']
        except (TypeError, ValueError, OverflowError):
            matches = False
        if not matches:
            raise DispatchBindingError('dispatch_operation_mismatch')


def bind_dispatch(context: dict, *, parameters: dict, operation: dict, target: dict | None = None) -> dict:
    """冻结本次调用的元数据及参数摘要；不保存参数或签发执行权限。"""
    if not isinstance(parameters, dict):
        raise DispatchBindingError('invalid_dispatch_parameters')
    try:
        detached = json.loads(_canonical(context))
        value = {'schema': SCHEMA, 'dispatch': detached,
                 'payload_sha256': _digest({'action_name': detached['action_name'], 'param': parameters}),
                 'operation_sha256': _digest(operation)}
        if target is not None:
            value.update(schema=TARGET_SCHEMA, target=json.loads(_canonical(target)))
        validate_dispatch_binding(value, operation=operation)
        return value
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        if isinstance(error, DispatchBindingError):
            raise
        raise DispatchBindingError('invalid_production_dispatch_binding') from error
