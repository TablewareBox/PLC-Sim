"""PTLC行为契约的兼容导入；实际定义与规格由设备包维护。"""
from __future__ import annotations

from typing import Any

try:
    from .model_loading import load_module
except ImportError:  # 兼容源码目录命令行。
    from model_loading import load_module

_model = load_module("eit_ptlc.simulation.plc_behavior")
STATIONS = _model.STATIONS
ActionContract = _model.ActionContract
StationContract = _model.StationContract
default_behavior_dir = _model.default_behavior_dir
load_station_contract = _model.load_station_contract
load_behavior_contracts = _model.load_behavior_contracts


def __getattr__(name: str) -> Any:
    return getattr(_model, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_model)))
