"""可选托管检查的实际执行顺序及失败边界；不加载设备包或启动世界时钟。"""
from concurrent.futures import ThreadPoolExecutor
import copy

import pytest

from material_world import World, ModelError


def world():
    value = World()
    value.add_plate('plate', rows=1, columns=1)
    return value


def test_default_world_retains_plain_custody_events():
    value = world()
    assert value._operation_geometry_guard is None
    value.claim_plate('plate', 'reader')
    value.release_plate('plate', 'reader')
    assert value.plate_locations['plate'] == 'deck'
    assert [e['kind'] for e in value.events[-2:]] == ['plate.claimed', 'plate.released']
    assert all('model_event_sequence' not in e for e in value.events)


@pytest.mark.parametrize('operation', ['claim', 'release'])
def test_rejection_before_handoff_preserves_owner_material_and_events(operation):
    value = world()
    if operation == 'release':
        value.claim_plate('plate', 'reader')
    before, events = value.snapshot(), copy.deepcopy(value.events)
    calls = []
    def reject(boundary, details):
        calls.append((boundary, details))
        raise ModelError('rejected-by-device-package')
    value._operation_geometry_guard = reject
    with pytest.raises(ModelError, match='rejected-by-device-package'):
        (value.claim_plate if operation == 'claim' else value.release_plate)('plate', 'reader')
    assert value.snapshot() == before and value.events == events
    assert len(calls) == 1 and calls[0][0] == 'handoff'


def test_notifications_observe_actual_owner_and_exact_model_event_sequence():
    value, observed = world(), []
    def inspect(boundary, details):
        observed.append((boundary, details, value.plate_locations['plate'], len(value.events)))
    value._operation_geometry_guard = inspect
    first = len(value.events)
    value.claim_plate('plate', 'reader')
    value.release_plate('plate', 'reader')
    assert [(b, owner) for b, _, owner, _ in observed] == [
        ('handoff', 'deck'), ('handoff_committed', 'reader'),
        ('handoff', 'reader'), ('handoff_committed', 'deck')]
    for index, source, target in [(1, 'deck', 'reader'), (3, 'reader', 'deck')]:
        _, details, _, count = observed[index]
        assert details == dict(plate_id='plate', source=source, target=target,
                               device_id='reader', model_event_sequence=count)
        assert value.events[count-1]['sequence'] == count
    assert len(value.events) == first + 2


@pytest.mark.parametrize('operation', ['claim', 'release'])
def test_post_handoff_failure_retains_actual_effect_for_host_recovery(operation):
    value = world()
    if operation == 'release':
        value.claim_plate('plate', 'reader')
    count = len(value.events)
    def fail_after(boundary, details):
        if boundary == 'handoff_committed':
            raise OSError('audit-store-failed')
    value._operation_geometry_guard = fail_after
    with pytest.raises(OSError, match='audit-store-failed'):
        (value.claim_plate if operation == 'claim' else value.release_plate)('plate', 'reader')
    # 后置回调不承诺回滚实际世界，宿主不能把异常误报为零效果。
    assert value.plate_locations['plate'] == ('reader' if operation == 'claim' else 'deck')
    assert len(value.events) == count + 1


def test_existing_custody_conflict_is_rejected_before_new_guard():
    value = world()
    value.claim_plate('plate', 'reader')
    observed = []
    value._operation_geometry_guard = lambda *args: observed.append(args)
    with pytest.raises(ModelError, match='plate_in_other_device'):
        value.claim_plate('plate', 'shaker')
    with pytest.raises(ModelError, match='plate_location_mismatch'):
        value.release_plate('plate', 'shaker')
    assert observed == [] and value.plate_locations['plate'] == 'reader'


def test_concurrent_claims_have_one_actual_owner_and_one_notification_pair():
    value, observed = world(), []
    value._operation_geometry_guard = lambda boundary, details: observed.append((boundary, details))
    def claim(device):
        try:
            value.claim_plate('plate', device)
            return device
        except ModelError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(claim, ['device-'+str(i) for i in range(16)]))
    winners = [x for x in result if x is not None]
    assert winners == [value.plate_locations['plate']]
    assert [x[0] for x in observed] == ['handoff', 'handoff_committed']
    assert len([e for e in value.events if e['kind'] == 'plate.claimed']) == 1
