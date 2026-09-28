"""恢复接管先核对世界，再修改租约或停止控制器。"""
import pytest

from reference_operation_gate import ContractError
from test_reference_recovery_coordinator import rig,claim


@pytest.mark.parametrize('world',[None,False,'another-world'])
def test_wrong_world_claim_preserves_active_lease_and_motion(rig,world):
    original=claim(rig);rig.state.moving['pump']=True
    before='\n'.join(rig.db.iterdump())
    with pytest.raises(ContractError,match='world_mismatch'):
        rig.gate.claim(dict(dispatcher_token='valid',executor_id='new',device_id='pump',world_id=world))
    assert '\n'.join(rig.db.iterdump())==before
    assert rig.state.moving['pump'] and not rig.state.stops
    assert claim(rig)==original


def test_matching_world_claim_uses_original_takeover_contract(rig):
    claim(rig);rig.state.moving['pump']=True
    reply=rig.gate.claim(dict(dispatcher_token='valid',executor_id='new',device_id='pump',world_id='world'))
    assert reply['epoch']==2 and reply['recovery']['stopped']
    assert rig.state.stops==[('pump','executor_replaced')]
