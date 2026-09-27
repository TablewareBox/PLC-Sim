"""旧 S1 导入入口；实现由 SZLab 设备包提供。"""
from __future__ import annotations

try:
    from .model_loading import load_module
except ImportError:  # 源码命令行兼容。
    from model_loading import load_module

_model = load_module("szlab_poly_studio.simulation.s1")
EventSink = _model.EventSink
S1World = _model.S1World
S1SimulationServer = _model.S1SimulationServer

__all__ = ["EventSink", "S1World", "S1SimulationServer"]
