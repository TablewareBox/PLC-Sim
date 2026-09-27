"""SZLab 设备包协议事件到通用仿真运行时的 Adapter。"""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

try:
    from .model_loading import load_module, load_symbol
    from .common import load_yaml
    from .package_simulation import (
        BehaviorCoverage,
        PackageSimulationRuntime,
        SimulationClock,
        WorldState,
    )
except ImportError:  # Direct source-checkout imports.
    from model_loading import load_module, load_symbol
    from common import load_yaml
    from package_simulation import (
        BehaviorCoverage,
        PackageSimulationRuntime,
        SimulationClock,
        WorldState,
    )

_definition_package = load_module("szlab_poly_studio.simulation")
_observer_class = load_symbol("szlab_poly_studio.simulation.protocol_observer:SzlabProtocolObserver")
_legacy_behavior = json.loads(files(_definition_package).joinpath("contracts/legacy_behavior.json").read_text())
PACKAGE_ID = _legacy_behavior["package_id"]
EXPECTED_REAL_DEVICE_COUNT = _legacy_behavior["catalog_summary"]["real_devices"]
EXPECTED_REAL_ACTION_COUNT = _legacy_behavior["catalog_summary"]["real_actions"]
EXPECTED_WORKFLOW_COUNT = _legacy_behavior["catalog_summary"]["workflows"]


def default_package_config_path() -> Path:
    """返回随 PLC-SIM 发布的 SZLab 设备包会话配置。"""

    return Path(__file__).resolve().with_name("config") / "szlab_package.yaml"


def default_behavior_path() -> Traversable:
    """返回设备包提供的历史兼容动作分类资源。"""

    return files(_definition_package).joinpath("contracts/legacy_behavior.json")


def load_szlab_coverage(path: str | Path | None = None) -> BehaviorCoverage:
    """加载并校验真实设备动作覆盖快照。"""

    payload = load_yaml(str(path)) if path is not None else json.loads(default_behavior_path().read_text())
    if payload.get("schema") != "unilab.szlab_behavior/v1":
        raise ValueError("SZLab 行为覆盖 schema 不受支持")
    if payload.get("package_id") != PACKAGE_ID:
        raise ValueError("SZLab 行为覆盖 package_id 不匹配")

    summary = dict(payload.get("catalog_summary", {}))
    expected = {
        "real_devices": EXPECTED_REAL_DEVICE_COUNT,
        "real_actions": EXPECTED_REAL_ACTION_COUNT,
        "workflows": EXPECTED_WORKFLOW_COUNT,
    }
    if summary != expected:
        raise ValueError(f"SZLab Catalog 摘要漂移: expected={expected!r}, actual={summary!r}")

    coverage = BehaviorCoverage()
    seen: set[str] = set()
    devices = dict(payload.get("devices", {}))
    if len(devices) != EXPECTED_REAL_DEVICE_COUNT:
        raise ValueError(f"SZLab 行为覆盖设备数应为 {EXPECTED_REAL_DEVICE_COUNT}")
    for device_id, groups in devices.items():
        for status, actions in dict(groups or {}).items():
            if status not in BehaviorCoverage.VALID:
                raise ValueError(f"{device_id} 使用未知覆盖状态: {status}")
            for action in actions or ():
                fq_action = f"{device_id}.{action}"
                if fq_action in seen:
                    raise ValueError(f"SZLab 动作覆盖重复: {fq_action}")
                seen.add(fq_action)
                coverage.register(fq_action, status)
    if len(seen) != EXPECTED_REAL_ACTION_COUNT:
        raise ValueError(
            f"SZLab 行为覆盖动作数应为 {EXPECTED_REAL_ACTION_COUNT}，实际 {len(seen)}"
        )
    return coverage


class SzlabPackageRuntime(_observer_class):
    """一次启动常驻全部 SZLab 协议的设备包会话状态面。"""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        behavior_path: str | Path | None = None,
        scenario: str | None = None,
        time_scale: float | None = None,
    ) -> None:
        config = load_yaml(str(config_path or default_package_config_path()))
        if config.get("schema") != "unilab.package_simulation/v1":
            raise ValueError("SZLab 设备包会话配置 schema 不受支持")
        if config.get("package_id") != PACKAGE_ID:
            raise ValueError("SZLab 设备包会话 package_id 不匹配")
        world_config = dict(config.get("world", {}))
        world = WorldState(
            sites=dict(world_config.get("sites", {})),
            quantities=dict(world_config.get("quantities", {})),
            devices=dict(world_config.get("devices", {})),
            flags=dict(world_config.get("flags", {})),
        )
        resolved_scenario = str(scenario or config.get("scenario", "ready"))
        world.set_flag("scenario", resolved_scenario)
        world.set_flag("witness_policy", str(config.get("witness_policy", "permissive")))
        self.runtime = PackageSimulationRuntime(
            PACKAGE_ID,
            clock=SimulationClock(
                float(time_scale if time_scale is not None else config.get("time_scale", 1.0))
            ),
            world=world,
            coverage=load_szlab_coverage(behavior_path),
            history_limit=int(config.get("history_limit", 500)),
        )
