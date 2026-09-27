#!/usr/bin/env python3
"""SZLab OPC UA 工作流握手代理。

用途：

1. ``list``：列出仓库中全部工作流的 PLC/配置先决条件，不连接服务器。
2. ``check``：只读检查远端 OPC UA 中可自动判定的先决条件。
3. ``serve``：写入测试先决条件，并监听 PC→PLC 信号，模拟 PLC 握手。

协议目录与 Uni-Lab-SZLab 当前工作流源码对齐，覆盖 ``workflows`` 目录中全部
19 个 Python 工作流和 2 个 PLC-SIM 双 TASK 扩展场景，共 37 个唯一动作调用。
状态机只依赖 :class:`VariableAdapter`
这一处 interface；OPC UA、内存测试替身等实现都作为 adapter 接入。
握手场景名称使用工作流源码中的真实函数名；旧版 S07/S09 场景名仍作为兼容别名：

- ``s07_material_dosing`` → ``s07_粉桶与烧杯搬运后固体称量``
- ``szlab_s09_pipetting_workflow`` → ``s09_移液调试``

主要动作包括：

- ``szlab_mixer_robot.submit_place_to_s04``（机器人任务号 7）
- ``szlab_mixer_stirrer.run_stirring``
- ``szlab_mixer_robot.submit_pick_from_s04``（机器人任务号 8）
- ``szlab_mixer_photoshotting.take_photo``（当前为只读完成信号）
- ``szlab_mixer_pump.run_solvent_addition``
- ``szlab_mixer_robot.submit_place_to_s06``（机器人任务号 11）
- ``szlab_mixer_robot.submit_pick_from_s06``（机器人任务号 12）
- ``szlab_mixer_robot.submit_place_to_s071``（机器人任务号 13）
- ``szlab_mixer_robot.submit_place_to_s072``（机器人任务号 15）
- ``szlab_mixer_robot.submit_pick_from_s072``（机器人任务号 16）
- ``szlab_s07_solid_addition.scan_powder_cartridges``（S07 工艺 1）
- ``szlab_s07_solid_addition.rotate_powder_cartridge_to_feed``（S07 工艺 2）
- ``szlab_s07_solid_addition.dose_powder``（S07 工艺 3）
- ``szlab_s08_cap_station.process_cap_with_sample_parts``（S08 工艺 1-6）
- ``szlab_mixer_pipetting_station.prepare_liquid_station``
- ``szlab_mixer_pipetting_station.bind_sample_to_station``
- ``szlab_mixer_pipetting_station.add_liquid``（内部工艺 5→7→8→6；完成只认 ``S09工艺完成``）
- ``szlab_mixer_pipetting_station.release_station``
- S09 工艺 9 测密度：按 ``S09测密度次数`` 写入抽/放液天平数组前 N 项；不再使用 ``S09天平读数稳定``
- ``szlab_poly_plc.get_stack_status``（只读，无动态握手）
- ``szlab_mixer_pump.add_solvent_to_beaker``
- ``szlab_mixer_robot.pick``（标准 Site 动作，S071/S03）
- ``szlab_s07_solid_addition.prepare_powder_cartridge_site``（S07 工艺 2）
- ``szlab_mixer_robot.place``（标准 Site 动作，S072）
- ``host_node.transfer_resource``（物理动作成功后的物料系统记账）
- ``szlab_s07_solid_addition.dose_powder_with_materials``（S07 工艺 3）
- ``szlab_mixer_robot.transfer_material_atomic``（一个动作内完成取料、放料和记账）
- ``szlab_mixer_robot.pick_pour_place_atomic``（一个动作内完成取料、倒液、放料和记账）

建议用 ``--workflow WORKFLOW_ID`` 定向运行单个工作流；选择
``s06_robot_workflow`` 时会让 S06
烧杯传感器从 False 开始，并由任务 11/12 的握手周期切换；选择
``s09_移液调试``（或兼容别名 ``szlab_s09_pipetting_workflow``）时会初始化
S09 工位和液体余量，并响应全部内部工艺。原有
``--s06-robot-workflow``、``--s09-pipetting-workflow`` 参数仍作为兼容别名保留。

本文件不依赖 Uni-Lab-OS 进程，也不创建 OPC UA 节点；它只连接由 CSV
创建好的节点。请使用包含 ``python-opcua`` 的 unilab Python 环境运行。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from .common import load_yaml
    from .package_simulation import write_snapshot_atomic
    from .szlab_package_runtime import SzlabPackageRuntime, default_package_config_path
    from .szlab_s1_sim import S1SimulationServer
except ImportError:  # Direct ``python szlab_handshake_agent.py`` compatibility.
    from common import load_yaml
    from package_simulation import write_snapshot_atomic
    from szlab_package_runtime import SzlabPackageRuntime, default_package_config_path
    from szlab_s1_sim import S1SimulationServer

DEFAULT_URL = "opc.tcp://opcua.ideawit.com:4855/xuse_sim"
DEFAULT_NODE_PREFIX = "ns=4;s=上位机通讯|"

try:
    from .model_loading import load_module
except ImportError:  # 源码命令行兼容。
    from model_loading import load_module

# 仅 SZLab 专用兼容入口选择此包；通用加载器不含设备分支。
_device_model = load_module("szlab_poly_studio.simulation.plc_handshake")
HandshakeEvent = _device_model.HandshakeEvent
SUPPORTED_ACTIONS = _device_model.SUPPORTED_ACTIONS
VariableAdapter = _device_model.VariableAdapter
WORKFLOW_ALIASES = _device_model.WORKFLOW_ALIASES
WORKFLOW_IDS = _device_model.WORKFLOW_IDS
WorkflowHandshakeSimulator = _device_model.WorkflowHandshakeSimulator
WorkflowSpec = _device_model.WorkflowSpec
build_workflow_specs = _device_model.build_workflow_specs


def __getattr__(name: str) -> Any:
    """保留旧模块变量/工具的导入身份，不复制设备定义。"""
    return getattr(_device_model, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_device_model)))



class OpcUaVariableAdapter:
    """使用直接 NodeId 访问 CSV 已创建变量的生产 adapter。"""

    def __init__(
        self, url: str, node_prefix: str, username: str = "", password: str = ""
    ) -> None:
        self.url = url
        self.node_prefix = node_prefix
        self.username = username
        self.password = password
        self._client = self._new_client()
        self._nodes: dict[str, Any] = {}
        self._browse_index: dict[str, Any] | None = None

    def _new_client(self) -> Any:
        from opcua import Client

        client = Client(self.url, timeout=10)
        if self.username:
            client.set_user(self.username)
            client.set_password(self.password)
        return client

    def connect(self) -> None:
        self._client.connect()

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        except Exception as exc:
            print(
                f"OPC UA 断开连接时忽略临时错误: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    def _reconnect(self) -> None:
        try:
            self._client.disconnect()
        except Exception:
            pass
        self._client = self._new_client()
        self._client.connect()
        self._nodes.clear()
        self._browse_index = None

    def _run_io(self, name: str, operation: Any) -> Any:
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                return operation()
            except (TimeoutError, ConnectionError, OSError) as exc:
                if attempt >= attempts:
                    raise RuntimeError(
                        f"{name}: OPC UA 通信失败（已重试 {attempts} 次）"
                    ) from exc
                print(
                    f"{name}: OPC UA {type(exc).__name__}，"
                    f"正在重连并重试 ({attempt}/{attempts})",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(1.0)
                self._reconnect()
        raise AssertionError("unreachable")

    def _node(self, name: str) -> Any:
        node = self._nodes.get(name)
        if node is None:
            node = self._client.get_node(f"{self.node_prefix}{name}")
            try:
                node.get_data_type_as_variant_type()
            except Exception as direct_error:  # noqa: BLE001 - 兼容非标准 NodeId 树
                node = self._browse_name_index().get(name)
                if node is None:
                    raise KeyError(
                        f"OPC UA 节点不存在: {name} ({self.node_prefix}{name})"
                    ) from direct_error
            self._nodes[name] = node
        return node

    def _browse_name_index(
        self,
        *,
        max_depth: int = 12,
        max_nodes: int = 20_000,
    ) -> dict[str, Any]:
        """扫描一次 BrowseName，兼容 Uni-Lab 测试服务器创建的嵌套节点。"""

        cached = getattr(self, "_browse_index", None)
        if cached is not None:
            return cached
        index: dict[str, Any] = {}
        stack: list[tuple[Any, int]] = [(self._client.get_objects_node(), 0)]
        visited = 0
        while stack and visited < max_nodes:
            node, depth = stack.pop()
            visited += 1
            try:
                index.setdefault(node.get_browse_name().Name, node)
                if depth < max_depth:
                    stack.extend((child, depth + 1) for child in node.get_children())
            except Exception:
                continue
        self._browse_index = index
        return index

    def read(self, name: str) -> Any:
        return self._run_io(name, lambda: self._node(name).get_value())

    def write(self, name: str, value: Any) -> None:
        """按远端变量真实 VariantType 写 Value，不改时间戳或状态码。"""

        self._run_io(name, lambda: self._write_once(name, value))

    def _write_once(self, name: str, value: Any) -> None:
        from opcua import ua

        node = self._node(name)
        variant_type = node.get_data_type_as_variant_type()
        data_value = ua.DataValue()
        data_value.Value = ua.Variant(value, variant_type)
        data_value.StatusCode = None
        data_value.SourceTimestamp = None
        data_value.ServerTimestamp = None
        data_value.SourcePicoseconds = None
        data_value.ServerPicoseconds = None

        write_value = ua.WriteValue()
        write_value.NodeId = node.nodeid
        write_value.AttributeId = ua.AttributeIds.Value
        write_value.Value = data_value

        params = ua.WriteParameters()
        params.NodesToWrite = [write_value]
        results = self._client.uaclient.write(params)
        if results and not results[0].is_good():
            raise RuntimeError(f"{name}: {results[0]}")



def _print_catalog(specs: tuple[WorkflowSpec, ...]) -> None:
    print(f"当前工作流数量: {len(specs)}")
    print(f"已支持动作数量: {len(SUPPORTED_ACTIONS)}")
    print()
    for spec in specs:
        print(f"[{spec.workflow_id}]")
        print("  动作:")
        for action in spec.actions:
            supported = (
                " [已支持握手]" if action.split("(")[0] in SUPPORTED_ACTIONS else ""
            )
            print(f"    - {action}{supported}")
        print("  先决条件:")
        for requirement in spec.requirements:
            note = f"；{requirement.note}" if requirement.note else ""
            print(
                f"    - ({requirement.kind}/{requirement.phase}) "
                f"{requirement.subject} {requirement.expectation}{note}"
            )
        print()


def _print_check(specs: tuple[WorkflowSpec, ...], adapter: VariableAdapter) -> bool:
    all_passed = True
    for spec in specs:
        print(f"[{spec.workflow_id}]")
        for requirement in spec.requirements:
            passed, actual = requirement.evaluate(adapter)
            if passed is None:
                marker = "MANUAL"
            elif passed:
                marker = "PASS"
            else:
                marker = "FAIL"
                all_passed = False
            actual_text = "" if passed is None else f"，实际={actual!r}"
            print(
                f"  {marker:6} {requirement.subject} "
                f"{requirement.expectation}{actual_text}"
            )
    return all_passed


def _event_line(event: HandshakeEvent) -> str:
    return json.dumps(
        {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": event.action,
            "phase": event.phase,
            "detail": event.detail,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _config_path() -> str:
    return str(Path(__file__).with_name("config") / "szlab_handshake.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("list", "check", "serve"),
        default="serve",
    )
    parser.add_argument("--config", default=_config_path(), help="YAML 配置文件")
    parser.add_argument(
        "--package-config",
        default=None,
        help="设备包级世界状态与覆盖配置；默认使用内置 config/szlab_package.yaml",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="原子写入设备包运行状态 JSON，供 GUI/诊断读取",
    )
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4855/xuse_sim/")
    parser.add_argument("--node-prefix", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--s1-host", default=None, help="S1 HTTP stand-in 监听地址")
    parser.add_argument("--s1-port", type=int, default=None, help="S1 HTTP stand-in 监听端口")
    parser.add_argument(
        "--no-s1-http",
        action="store_true",
        help="不启动设备包自带的 S1 HTTP stand-in",
    )
    parser.add_argument("--position", type=int, default=None, help="S04 位置，1-6")
    parser.add_argument("--pump", type=int, default=None, choices=(1, 2, 3))
    parser.add_argument("--poll-interval", type=float, default=None)
    parser.add_argument("--poll-ms", type=int, default=None, help="轮询间隔（毫秒）")
    parser.add_argument(
        "--process-delay",
        type=float,
        default=None,
        help="无设备时长参数动作的统一延时（秒）",
    )
    parser.add_argument(
        "--delay-ms", type=int, default=None, help="统一动作延时（毫秒）"
    )
    parser.add_argument(
        "--time-scale",
        type=float,
        default=None,
        help="仿真时间倍率，必须大于 0；动作参数时长和配置延时统一生效",
    )
    parser.add_argument(
        "--workflow",
        choices=("all", *WORKFLOW_IDS, *WORKFLOW_ALIASES),
        default=None,
        help="list/check 的工作流过滤器；serve 设备包模式中仅作为兼容初始场景",
    )
    parser.add_argument(
        "--legacy-workflow-mode",
        action="store_true",
        help="恢复旧版只启用所选工作流协议的行为；默认一次启动全部设备协议",
    )
    parser.add_argument(
        "--s06-robot-workflow",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="完整模拟 S06 机器人工作流：初始烧杯传感器为 False，并响应机器人任务号 11/12",
    )
    parser.add_argument(
        "--s09-pipetting-workflow",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="完整模拟 S09 移液工作流：初始化工位/液量，并响应工艺 5、7、8、6",
    )
    parser.add_argument(
        "--s09-remaining-volume-ml",
        type=float,
        default=None,
        help="S09 1-5 号液体瓶的初始余量（mL，默认 100）",
    )
    parser.add_argument(
        "--s07-balance-reading",
        type=float,
        default=None,
        help="S07 注粉完成时写入的模拟天平读数（默认 1.0）",
    )
    parser.add_argument(
        "--s09-balance-reading",
        type=float,
        default=None,
        help="S09 工艺 8 遥测/工艺 9 密度数组写入的模拟天平值（默认 1.0）",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=0,
        help="完成指定数量的交互动作后退出；0 表示持续运行",
    )
    parser.add_argument(
        "--no-initialize",
        action="store_true",
        help="不写入 PLC→PC 仿真初值",
    )
    parser.add_argument(
        "--keep-state-on-exit",
        action="store_true",
        help="退出时不把仿真器负责的 PLC→PC 信号恢复为安全初始值",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_yaml(args.config)
    position = (
        args.position if args.position is not None else int(config.get("position", 1))
    )
    pump = args.pump if args.pump is not None else int(config.get("pump", 1))
    requested_workflow = args.workflow or str(config.get("workflow", "all"))
    selected_workflow = WORKFLOW_ALIASES.get(requested_workflow, requested_workflow)
    if selected_workflow not in ("all", *WORKFLOW_IDS):
        print(f"不支持的握手工作流: {requested_workflow}", file=sys.stderr)
        return 2
    package_mode = (
        not args.legacy_workflow_mode
        and str(config.get("mode", "package")).strip().lower() == "package"
    )
    time_scale = (
        float(args.time_scale)
        if args.time_scale is not None
        else float(config.get("time_scale", 1.0))
    )
    if time_scale <= 0:
        print("仿真时间倍率必须大于 0", file=sys.stderr)
        return 2

    if args.delay_ms is not None:
        process_delay = max(args.delay_ms, 0) / 1000.0
        delays: dict[str, float] = {}
    elif args.process_delay is not None:
        process_delay = max(args.process_delay, 0.0)
        delays = {}
    else:
        process_delay = max(float(config.get("delay_ms", 500)), 0.0) / 1000.0
        delay_aliases = {"s04": "stirrer", "s06": "pump"}
        delays = {
            delay_aliases.get(str(key), str(key)): max(float(value), 0.0) / 1000.0
            for key, value in dict(config.get("delays", {})).items()
        }

    if args.poll_ms is not None:
        poll_interval = max(args.poll_ms, 5) / 1000.0
    elif args.poll_interval is not None:
        poll_interval = max(args.poll_interval, 0.005)
    else:
        poll_interval = max(float(config.get("poll_ms", 20)), 5.0) / 1000.0

    def config_bool(cli_value: bool | None, key: str, default: bool) -> bool:
        return bool(config.get(key, default)) if cli_value is None else bool(cli_value)

    s06_robot_workflow = config_bool(
        args.s06_robot_workflow,
        "s06_robot_workflow",
        False,
    )
    s09_pipetting_workflow = config_bool(
        args.s09_pipetting_workflow,
        "s09_pipetting_workflow",
        False,
    )
    s09_remaining_volume_ml = (
        args.s09_remaining_volume_ml
        if args.s09_remaining_volume_ml is not None
        else float(config.get("s09_remaining_volume_ml", 100.0))
    )
    s07_balance_reading = (
        args.s07_balance_reading
        if args.s07_balance_reading is not None
        else float(config.get("s07_balance_reading", 1.0))
    )
    s09_balance_reading = (
        args.s09_balance_reading
        if args.s09_balance_reading is not None
        else float(config.get("s09_balance_reading", 1.0))
    )

    specs = build_workflow_specs(position=position, pump=pump)
    if selected_workflow != "all" and args.command in {"list", "check"}:
        specs = tuple(spec for spec in specs if spec.workflow_id == selected_workflow)
    if args.command == "list":
        _print_catalog(specs)
        return 0

    adapter = OpcUaVariableAdapter(
        args.url,
        args.node_prefix or str(config.get("node_prefix", DEFAULT_NODE_PREFIX)),
        username=args.username or str(config.get("username", "")),
        password=args.password or str(config.get("password", "")),
    )
    print(f"连接 OPC UA: {args.url}")
    adapter.connect()
    print("OPC UA 已连接")
    try:
        if args.command == "check":
            return 0 if _print_check(specs, adapter) else 2

        simulator = WorkflowHandshakeSimulator(
            adapter,
            position=position,
            pump=pump,
            process_delay=process_delay,
            delays=delays,
            initial_values=dict(config.get("initial_values", {})),
            s06_robot_workflow=s06_robot_workflow,
            s09_pipetting_workflow=s09_pipetting_workflow,
            s09_remaining_volume_ml=s09_remaining_volume_ml,
            s07_balance_reading=s07_balance_reading,
            s09_balance_reading=s09_balance_reading,
            workflow=requested_workflow,
            package_mode=package_mode,
            time_scale=time_scale,
        )
        package_runtime = SzlabPackageRuntime(
            config_path=args.package_config or default_package_config_path(),
            scenario=selected_workflow,
            time_scale=time_scale,
        )
        s1_config = dict(config.get("s1_http", {}))
        s1_enabled = (
            package_mode
            and not args.no_s1_http
            and bool(s1_config.get("enabled", True))
        )
        s1_server: S1SimulationServer | None = None
        snapshot_lock = threading.RLock()

        def publish_state() -> None:
            if args.state_file:
                with snapshot_lock:
                    protocol_snapshot = simulator.protocol_snapshot()
                    if s1_server is not None:
                        protocol_snapshot["s1_http"] = s1_server.snapshot()
                    write_snapshot_atomic(
                        args.state_file,
                        package_runtime.snapshot(protocol_snapshot),
                    )

        def observe_s1(
            action: str, phase: str, detail: dict[str, Any]
        ) -> None:
            package_runtime.observe_external(action, phase, detail)
            publish_state()

        if s1_enabled:
            s1_server = S1SimulationServer(
                host=args.s1_host or str(s1_config.get("host", "127.0.0.1")),
                port=(
                    int(args.s1_port)
                    if args.s1_port is not None
                    else int(s1_config.get("port", 8055))
                ),
                event_sink=observe_s1,
            )
            try:
                s1_server.start()
            except OSError as exc:
                print(f"S1 HTTP stand-in 启动失败: {exc}", file=sys.stderr)
                package_runtime.runtime.world.update_device(
                    "s1_workstation",
                    state="failed",
                    error=str(exc),
                )
                package_runtime.stop()
                publish_state()
                return 4
            package_runtime.runtime.world.update_device(
                "s1_workstation",
                state="ready",
                endpoint=s1_server.endpoint,
            )
            print(f"S1 HTTP stand-in 已启动: {s1_server.endpoint}")

        # 即使显式禁止 OPC UA 初始化，GUI 也应立即看到会话身份与覆盖报告。
        publish_state()

        if not args.no_initialize:
            mode_label = "设备包" if package_mode else f"握手场景 {selected_workflow!r}"
            print(f"写入 {mode_label} 的仿真先决条件...")
            simulator.initialize()
            package_runtime.initialize_protocol(simulator.initialization_values())
            publish_state()
            checks = simulator.check_supported_prerequisites()
            failed = [item for item in checks if not item[1]]
            for name, passed, expected, actual in checks:
                print(
                    f"  {'PASS' if passed else 'FAIL'} {name}: "
                    f"expected={expected!r}, actual={actual!r}"
                )
            if failed:
                print("先决条件写入后校验失败，拒绝进入握手循环", file=sys.stderr)
                if s1_server is not None:
                    s1_server.stop()
                package_runtime.stop()
                publish_state()
                return 3

        if package_mode:
            print("SZLab 设备包仿真器已启动；Robot 与 S04-S09 全部常驻，按 Ctrl+C 停止。")
        else:
            print("兼容工作流握手仿真器已启动；按 Ctrl+C 停止。")
        print("S05 为只读完成信号，已保持 S05加工完成=True、S05拍照结果=1。")
        stop_requested = False

        def _request_stop(_signum: int, _frame: Any) -> None:
            nonlocal stop_requested
            stop_requested = True

        previous_sigint = signal.signal(signal.SIGINT, _request_stop)
        previous_sigterm: Any = None
        try:
            previous_sigterm = signal.signal(signal.SIGTERM, _request_stop)
        except (AttributeError, ValueError):
            pass
        try:
            while not stop_requested:
                events = simulator.step()
                for event in events:
                    package_runtime.observe(event)
                    print(_event_line(event), flush=True)
                if events:
                    publish_state()
                if (
                    args.max_actions > 0
                    and simulator.completed_actions >= args.max_actions
                    and simulator.all_cycles_idle()
                ):
                    print(
                        f"已完成 {simulator.completed_actions} 个动作，退出握手循环。"
                    )
                    break
                time.sleep(poll_interval)
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
            cleanup_on_exit = bool(config.get("cleanup_on_exit", True))
            if (
                not args.keep_state_on_exit
                and cleanup_on_exit
                and not args.no_initialize
            ):
                print("恢复仿真器负责的 PLC→PC 信号...")
                simulator.cleanup()
            if s1_server is not None:
                s1_server.stop()
            package_runtime.stop()
            publish_state()
        return 0
    finally:
        adapter.disconnect()
        print("OPC UA 已断开")


__all__ = [name for name in __dir__() if not name.startswith("_")]


if __name__ == "__main__":
    raise SystemExit(main())
