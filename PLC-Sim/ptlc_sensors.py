"""PTLC传感器模型兼容入口；本设备输入定义由PTLC包维护。"""
from __future__ import annotations
from typing import Any

try:
    from .model_loading import load_module
except ImportError:  # 兼容源码目录命令行。
    from model_loading import load_module

_model = load_module("eit_ptlc.simulation.plc_sensors")
PTLC_SENSOR_KEYS = _model.PTLC_SENSOR_KEYS
PTLC_SENSOR_SITES = _model.PTLC_SENSOR_SITES
PTLC_TRANSFER_SITES = _model.PTLC_TRANSFER_SITES
PTLC_EVENT_KINDS = _model.PTLC_EVENT_KINDS
SensorTransition = _model.SensorTransition
PtlcSensorEngine = _model.PtlcSensorEngine


def __getattr__(name: str) -> Any:
    return getattr(_model, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_model)))
