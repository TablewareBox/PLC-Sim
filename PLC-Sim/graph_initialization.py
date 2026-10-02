"""将设备包的既有图投影接到协议初始化；不解释设备语义或建立第二份场景。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


def _read(path: Path) -> tuple[bytes, dict]:
    path = Path(path)
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError("图与角色绑定必须为规范绝对普通文件")
    raw = path.read_bytes()
    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("JSON 存在重复键")
            value[key] = item
        return value
    def invalid_number(value):
        raise ValueError("JSON 不允许非有限数")
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)
    if not isinstance(value, dict):
        raise ValueError("图与角色绑定必须是 JSON 对象")
    return raw, value


@dataclass(frozen=True)
class GraphInitialization:
    """一次启动的投影记录；不是库存运行时或传感器采样器。"""
    initial_values: dict[str, Any]
    provenance: dict[str, Any]


def prepare_graph_initialization(graph: Path, roles: Path, *, projector: Callable,
                                 configured_values: dict[str, Any]) -> GraphInitialization:
    graph_raw, document = _read(graph)
    roles_raw, bindings = _read(roles)
    if (not bindings or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                            for k, v in bindings.items()) or len(set(bindings.values())) != len(bindings)):
        raise ValueError("角色绑定必须明确且实例不能重复")
    if not isinstance(configured_values, dict) or any(not isinstance(k, str) or not k for k in configured_values):
        raise ValueError("既有协议初始化值格式无效")
    original_document, original_bindings = deepcopy(document), deepcopy(bindings)
    result = projector(document, bindings)
    if document != original_document or bindings != original_bindings:
        raise ValueError("投影器不得改写启动图或角色绑定")
    values, sources = result.values, result.sources
    occupancy, unmapped = result.occupancy, result.unmapped_sites
    if (not isinstance(values, dict) or not values or not isinstance(sources, dict)
            or set(values) != set(sources) or any(not isinstance(k, str) or not k or type(v) is not bool for k,v in values.items())
            or not isinstance(occupancy, dict) or any(not isinstance(k,str) or not k or (v is not None and (not isinstance(v,str) or not v)) for k,v in occupancy.items())
            or not isinstance(unmapped, (tuple, list))
            or any(not isinstance(site, str) or site not in occupancy for site in unmapped)
            or len(set(unmapped)) != len(unmapped)
            or any(not isinstance(site, str) or site not in occupancy for site in sources.values())
            or len(set(sources.values())) != len(sources)
            or set(sources.values()) & set(unmapped)
            or set(sources.values()) | set(unmapped) != set(occupancy)
            or any(values[name] != (occupancy[site] is not None) for name,site in sources.items())):
        raise ValueError("设备投影的值、占用与来源不一致")
    conflicts = sorted(name for name in values if name in configured_values and (
        type(configured_values[name]) is not bool or configured_values[name] != values[name]))
    if conflicts:
        raise ValueError("既有 initial_values 与启动图冲突：" + ", ".join(conflicts))
    if Path(graph).read_bytes() != graph_raw or Path(roles).read_bytes() != roles_raw:
        raise ValueError("准备期间启动输入变化")
    return GraphInitialization(
        {**deepcopy(configured_values), **deepcopy(values)},
        {"schema": "plc.graph-initial-inputs/v1", "graph": str(graph),
         "graph_sha256": hashlib.sha256(graph_raw).hexdigest(), "roles": str(roles),
         "roles_sha256": hashlib.sha256(roles_raw).hexdigest(), "warehouse_roles": bindings,
         "values": deepcopy(values), "sources": deepcopy(sources), "occupancy": deepcopy(occupancy),
         "unmapped_sites": list(unmapped), "scope": "只覆盖已映射的初始在位输入，不证明物料数量或动态反馈",
         "runtime_qualified": False})
