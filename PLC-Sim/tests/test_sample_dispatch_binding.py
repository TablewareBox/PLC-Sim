"""样品绑定是版本化操作身份，不改变v1/v2或凭证授权。"""
import copy
import pytest
from reference_dispatch_binding import bind_dispatch,validate_dispatch_binding,DispatchBindingError,SAMPLE_SCHEMA
from test_reference_dispatch_binding import inputs

def sample_operation():
    context,operation=inputs()
    operation['sample_context']={'sample_id':'sample-a','reservation_id':'reservation-a'}
    return context,operation

@pytest.mark.parametrize('mapped',[False,True])
def test_sample_binding_covers_both_sample_and_reservation(mapped):
    context,operation=sample_operation()
    target=None
    if mapped:
        context['local_device_id']='graph-node'
        target={'local_device_id':'graph-node','device_id':'device'}
    value=bind_dispatch(context,parameters={},operation=operation,target=target)
    assert value['schema']==SAMPLE_SCHEMA
    validate_dispatch_binding(value,operation=operation)
    for key in ('sample_id','reservation_id'):
        changed=copy.deepcopy(operation);changed['sample_context'][key]+='-changed'
        with pytest.raises(DispatchBindingError,match='dispatch_operation_mismatch'):
            validate_dispatch_binding(value,operation=changed)
    operation.pop('sample_context')
    with pytest.raises(DispatchBindingError,match='invalid_bound_operation'):
        validate_dispatch_binding(value,operation=operation)

@pytest.mark.parametrize('sample',[None,[],{}, {'sample_id':'a'}, {'sample_id':'a','reservation_id':''},
    {'sample_id':True,'reservation_id':'r'}, {'sample_id':'a','reservation_id':'r','extra':1}])
def test_invalid_sample_context_has_no_binding(sample):
    context,operation=sample_operation();operation['sample_context']=sample
    with pytest.raises(DispatchBindingError,match='invalid_bound_sample_context'):
        bind_dispatch(context,parameters={},operation=operation)

@pytest.mark.parametrize('mapped',[False,True])
def test_old_binding_cannot_be_extended_with_unsigned_sample(mapped):
    context,operation=inputs()
    target={'local_device_id':'device','device_id':'device'} if mapped else None
    value=bind_dispatch(context,parameters={},operation=operation,target=target)
    assert value['schema']=='labos.production-dispatch-binding/'+('v2' if mapped else 'v1')
    operation['sample_context']={'sample_id':'a','reservation_id':'r'}
    with pytest.raises(DispatchBindingError,match='invalid_bound_operation'):
        validate_dispatch_binding(value,operation=operation)

def test_sample_binding_does_not_invent_different_device_mapping():
    context,operation=sample_operation();context['local_device_id']='another-node'
    with pytest.raises(DispatchBindingError,match='dispatch_device_mismatch'):
        bind_dispatch(context,parameters={},operation=operation)
