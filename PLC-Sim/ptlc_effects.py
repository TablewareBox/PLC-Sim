"""PTLC设备规则兼容入口，唯一实现由设备包维护。"""
from __future__ import annotations
from typing import Any

try:
    from .model_loading import load_module
except ImportError:  # 兼容源码目录命令行。
    from model_loading import load_module

_model = load_module("eit_ptlc.simulation.plc_effects")
all_effects = _model.all_effects
apply_effect = _model.apply_effect
apply_process_effects = _model.apply_process_effects
effect_names = _model.effect_names
effects_for = _model.effects_for
fault_codes = _model.fault_codes


def __getattr__(name: str) -> Any:
    return getattr(_model, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_model)))
