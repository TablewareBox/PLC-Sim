"""生产派发声明的完整性与操作绑定；不以字段校验授予执行权。"""
import copy
import hashlib
import json

import pytest

from reference_dispatch_binding import bind_dispatch, validate_dispatch_binding, DispatchBindingError


def inputs():
    context = {'schema': 'unilab.device-execution-identity/v1', 'kind': 'edge_claim',
               'job_uuid': 'job', 'task_uuid': 'task', 'command_uuid': 'command', 'node_uuid': 'node',
               'local_device_id': 'device', 'action_name': 'execute', 'execution_source': 'workflow',
               'claim_uuid': 'claim', 'attempt': 1, 'fences': [['material', 7]]}
    operation = {'world_id': 'world', 'operation_id': 'op', 'device_id': 'device', 'action': 'run', 'args': {}}
    return context, operation


def test_binding_detaches_metadata_and_fingerprints_exact_driver_parameters():
    context, operation = inputs()
    first = bind_dispatch(context, parameters={'command': '{ "x": 1 }'}, operation=operation)
    second = bind_dispatch(context, parameters={'command': '{"x":1}'}, operation=operation)
    context['fences'][0][1] = 99
    assert first['dispatch']['fences'] == [['material', 7]]
    assert first['payload_sha256'] != second['payload_sha256']
    assert first['operation_sha256'] == second['operation_sha256']
    expected = json.dumps({'action_name': 'execute', 'param': {'command': '{ "x": 1 }'}},
                          sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    assert first['payload_sha256'] == hashlib.sha256(expected.encode()).hexdigest()
    assert set(first) == {'schema', 'dispatch', 'payload_sha256', 'operation_sha256'}
    validate_dispatch_binding(first, operation=operation)


@pytest.mark.parametrize('change', [
    {'kind': 'unknown'}, {'command_uuid': None}, {'attempt': True}, {'attempt': 0},
    {'claim_uuid': ''}, {'fences': [['material', True]]}, {'fences': [['material', 1], ['material', 2]]},
    {'origin_instance_id': 'invented'}, {'schema': 'other'}, {'node_uuid': False},
])
def test_incomplete_or_ambiguous_source_identity_is_rejected(change):
    context, operation = inputs()
    context.update(change)
    with pytest.raises(DispatchBindingError):
        bind_dispatch(context, parameters={}, operation=operation)


def test_local_identity_does_not_invent_claim_or_process_generation():
    context, operation = inputs()
    context.update(kind='job', claim_uuid=None, attempt=None, fences=[])
    value = bind_dispatch(context, parameters={}, operation=operation)
    assert value['dispatch']['claim_uuid'] is None
    assert 'origin_instance_id' not in value['dispatch']
    context['fences'] = [['device', 1]]
    with pytest.raises(DispatchBindingError):
        bind_dispatch(context, parameters={}, operation=operation)


@pytest.mark.parametrize('change', [{'device_id': 'other'}, {'args': {'speed': 5}}, {'world_id': 'other'}])
def test_transport_cannot_change_bound_operation(change):
    context, operation = inputs()
    value = bind_dispatch(context, parameters={}, operation=operation)
    with pytest.raises(DispatchBindingError):
        validate_dispatch_binding(value, operation={**operation, **change})


@pytest.mark.parametrize('part', ['payload_sha256', 'operation_sha256', 'schema'])
def test_malformed_wire_identity_is_rejected(part):
    context, operation = inputs()
    value = bind_dispatch(context, parameters={}, operation=operation)
    value[part] = 'invalid'
    with pytest.raises(DispatchBindingError):
        validate_dispatch_binding(value)


def test_distinct_runtime_and_facility_targets_use_version_two():
    context, operation = inputs()
    context['local_device_id'] = 'graph-node'
    target = {'local_device_id': 'graph-node', 'device_id': operation['device_id']}
    with pytest.raises(DispatchBindingError, match='dispatch_device_mismatch'):
        bind_dispatch(context, parameters={}, operation=operation)
    value = bind_dispatch(context, parameters={}, operation=operation, target=target)
    assert value['schema'] == 'labos.production-dispatch-binding/v2'
    assert value['dispatch']['local_device_id'] == 'graph-node'
    assert value['target'] == target
    target['device_id'] = 'mutated'
    validate_dispatch_binding(value, operation=operation)
    assert value['target']['device_id'] == 'device'


@pytest.mark.parametrize('target', [{}, {'local_device_id': 'device'},
    {'local_device_id': 'other', 'device_id': 'device'},
    {'local_device_id': 'device', 'device_id': ''},
    {'local_device_id': 'device', 'device_id': 'other'},
    {'local_device_id': 'device', 'device_id': 'device', 'extra': True}, []])
def test_version_two_rejects_incomplete_or_mismatched_target(target):
    context, operation = inputs()
    with pytest.raises(DispatchBindingError):
        bind_dispatch(context, parameters={}, operation=operation, target=target)


@pytest.mark.parametrize('change', ['remove_target', 'downgrade', 'unknown_schema',
                                    'malformed_schema', 'context', 'target', 'operation'])
def test_wire_target_substitution_is_rejected(change):
    context, operation = inputs()
    value = bind_dispatch(context, parameters={}, operation=operation,
                          target={'local_device_id': 'device', 'device_id': 'device'})
    if change == 'remove_target':
        value.pop('target')
    elif change == 'downgrade':
        value['schema'] = 'labos.production-dispatch-binding/v1'
    elif change == 'unknown_schema':
        value['schema'] = 'labos.production-dispatch-binding/v3'
    elif change == 'malformed_schema':
        value['schema'] = []
    elif change == 'context':
        value['dispatch']['local_device_id'] = 'other'
    elif change == 'target':
        value['target']['device_id'] = 'other'
    else:
        operation['device_id'] = 'other'
    with pytest.raises(DispatchBindingError):
        validate_dispatch_binding(value, operation=operation)
