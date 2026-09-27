"""有限状态编码跨JSON保留类型；不授予设备世界恢复资格。"""
import json
import math
import random
import pytest
from durable_state_codec import encode, decode


def test_json_roundtrip_keeps_integer_keys_layers_collections_and_rng():
    rng = random.Random(728)
    state = {1: {'lower': {'volume': 10.5}, 'upper': {'volume': 3.0}},
             'controller': ('paused', [False, None, '样品']), 'set': {1, 4, 8}, 'rng': rng.getstate()}
    restored = decode(json.loads(json.dumps(encode(state), allow_nan=False)))
    assert restored == state and 1 in restored and '1' not in restored
    second = random.Random()
    second.setstate(restored['rng'])
    assert [rng.random() for _ in range(10)] == [second.random() for _ in range(10)]
    restored[1]['lower']['volume'] = 0
    assert state[1]['lower']['volume'] == 10.5


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_values_rejected_on_both_sides(value):
    for operation in [encode, decode]:
        with pytest.raises(ValueError, match='nonfinite_durable_state'):
            operation([value])


@pytest.mark.parametrize('value', [b'bytes', object(), frozenset({1})])
def test_unsupported_objects_are_not_serialized(value):
    with pytest.raises(ValueError, match='unsupported_durable_state_type'):
        encode(value)


@pytest.mark.parametrize('value', [
    {'type': 'object', 'items': []}, {'type': 'tuple', 'items': 'bad'},
    {'type': 'tuple', 'items': [], 'extra': True}, {'plain': 'dict'},
    {'type': 'dict', 'items': [[1, 'a'], [True, 'b']]},
])
def test_invalid_tags_fields_and_equal_keys_rejected(value):
    with pytest.raises(ValueError):
        decode(value)
