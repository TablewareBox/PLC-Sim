"""PTLC行为模型兼容入口；具体实现由设备包维护。"""
from __future__ import annotations
from typing import Any

try:
    from .model_loading import load_module
except ImportError:  # 兼容源码目录命令行。
    from model_loading import load_module

_model = load_module("eit_ptlc.simulation.plc_plant")
PlantAction = _model.PlantAction
PtlcPlant = _model.PtlcPlant


def __getattr__(name: str) -> Any:
    return getattr(_model, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_model)))
