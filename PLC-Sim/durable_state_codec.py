"""可信模型状态的有限JSON编码；不保存文件、不恢复对象或创建世界。"""
import math


def encode(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('nonfinite_durable_state')
        return value
    if type(value) is list:
        return [encode(item) for item in value]
    if type(value) in (dict, tuple, set):
        if type(value) is dict:
            return {'type': 'dict', 'items': [[encode(k), encode(v)] for k, v in value.items()]}
        return {'type': type(value).__name__, 'items': [encode(v) for v in (sorted(value) if type(value) is set else value)]}
    raise ValueError('unsupported_durable_state_type')


def decode(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('nonfinite_durable_state')
        return value
    if type(value) is list:
        return [decode(item) for item in value]
    if type(value) is not dict or set(value) != {'type', 'items'} or type(value['items']) is not list:
        raise ValueError('invalid_durable_state_encoding')
    items = value['items']
    if value['type'] == 'tuple':
        return tuple(decode(v) for v in items)
    if value['type'] == 'set':
        return set(decode(v) for v in items)
    if value['type'] == 'dict':
        result = {decode(k): decode(v) for k, v in items}
        if len(result) != len(items):
            raise ValueError('duplicate_durable_state_key')
        return result
    raise ValueError('invalid_durable_state_encoding')
