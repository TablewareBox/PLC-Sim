"""PTLC握手兼容入口；设备响应归PTLC包，通信和CLI留在PLC-Sim。"""
from __future__ import annotations
from typing import Any

try:
    from .model_loading import load_module
except ImportError:  # 兼容源码目录命令行。
    from model_loading import load_module

try:
    from .ptlc_runtime import OpcUaVariableAdapter
except ImportError:  # 兼容源码目录命令行。
    from ptlc_runtime import OpcUaVariableAdapter

_model = load_module("eit_ptlc.simulation.plc_handshake")
OUTPUT_DEFAULTS = _model.OUTPUT_DEFAULTS
HandshakeEvent = _model.HandshakeEvent
PtlcHandshakeSimulator = _model.PtlcHandshakeSimulator
RuntimeFaults = _model.RuntimeFaults

__all__ = ["OUTPUT_DEFAULTS", "HandshakeEvent", "OpcUaVariableAdapter",
           "PtlcHandshakeSimulator", "RuntimeFaults", "main"]


def __getattr__(name: str) -> Any:
    return getattr(_model, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_model)))


def main(argv: list[str] | None = None) -> int:
    """兼容旧入口；参数为可选 argv，返回独立 CLI 的退出码。"""

    try:
        from .ptlc_agent_cli import main as cli_main
    except ImportError:
        from ptlc_agent_cli import main as cli_main
    return cli_main(argv)



if __name__ == "__main__":
    raise SystemExit(main())
