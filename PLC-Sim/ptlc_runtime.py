"""PTLC状态定义兼容入口及OPC UA通信适配器。"""
from __future__ import annotations
import time
from typing import Any

try:
    from .model_loading import load_module
except ImportError:  # 兼容源码目录命令行。
    from model_loading import load_module

_model = load_module("eit_ptlc.simulation.plc_runtime")
STATIONS = _model.STATIONS
INPUT_FIELDS = _model.INPUT_FIELDS
OUTPUT_DEFAULTS = _model.OUTPUT_DEFAULTS
TERMINAL_STATES = _model.TERMINAL_STATES
MODELED_ACTIONS = _model.MODELED_ACTIONS
INSTANT_ACTIONS = _model.INSTANT_ACTIONS
VariableAdapter = _model.VariableAdapter
HandshakeEvent = _model.HandshakeEvent
MotionSegment = _model.MotionSegment
ActionCycle = _model.ActionCycle
DeployCycle = _model.DeployCycle
RuntimeFaults = _model.RuntimeFaults


class OpcUaVariableAdapter:
    """按 PTLC GVL BrowseName 路径定位变量并保持远端 VariantType 写入。"""

    def __init__(
        self,
        url: str,
        browse_path: tuple[str, ...],
        username: str = "",
        password: str = "",
    ) -> None:
        """创建适配器；参数为端点、GVL 路径和可选凭据，暂不建立连接。"""

        self.url = url
        self.browse_path = browse_path
        self.username = username
        self.password = password
        self._client = self._new_client()
        self._nodes: dict[str, Any] = {}
        self._gvl: Any = None

    def _new_client(self) -> Any:
        """按当前连接参数创建一个尚未连接的 OPC UA 客户端。"""

        from opcua import Client

        client = Client(self.url, timeout=10)
        if self.username:
            client.set_user(self.username)
            client.set_password(self.password)
        return client

    def connect(self) -> None:
        """连接远端 OPC UA 服务；无参数和返回值。"""

        self._client.connect()

    def disconnect(self) -> None:
        """尽力断开远端连接；重复调用安全，无返回值。"""

        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - 三方客户端断开异常不应阻止进程退出。
            return

    def _reconnect(self) -> None:
        """重建连接并清除节点缓存；无参数和返回值。"""

        self.disconnect()
        self._client = self._new_client()
        self._client.connect()
        self._nodes.clear()
        self._gvl = None

    @staticmethod
    def _child(parent: Any, browse_name: str) -> Any:
        """按 BrowseName 查询直接子节点；找不到时抛出 ``KeyError``。"""

        for child in parent.get_children():
            if child.get_browse_name().Name == browse_name:
                return child
        raise KeyError(f"BrowseName 子节点不存在: {browse_name}")

    def _gvl_node(self) -> Any:
        """解析并缓存配置的 GVL 根节点。"""

        if self._gvl is None:
            node = self._client.get_objects_node()
            for part in self.browse_path:
                node = self._child(node, part)
            self._gvl = node
        return self._gvl

    def _node(self, name: str) -> Any:
        """按变量名解析并缓存节点；参数为 BrowseName，返回 OPC UA 节点。"""

        if name not in self._nodes:
            self._nodes[name] = self._child(self._gvl_node(), name)
        return self._nodes[name]

    def _io(self, operation: Any) -> Any:
        """执行可重连 I/O；参数为零参数调用，返回其结果。"""

        for attempt in range(3):
            try:
                return operation()
            except (TimeoutError, ConnectionError, OSError):
                if attempt == 2:
                    raise
                time.sleep(0.5)
                self._reconnect()
        raise AssertionError("unreachable")

    def read(self, name: str) -> Any:
        """读取变量；参数为 BrowseName，返回保留原类型的节点值。"""

        return self._io(lambda: self._node(name).get_value())

    def write(self, name: str, value: Any) -> None:
        """写入变量；参数为 BrowseName 和新值，返回无。"""

        self._io(lambda: self._write_once(name, value))

    def _write_once(self, name: str, value: Any) -> None:
        """使用远端节点的 VariantType 执行一次写入。"""

        from opcua import ua

        node = self._node(name)
        variant_type = node.get_data_type_as_variant_type()
        node.set_value(ua.Variant(value, variant_type))



def __getattr__(name: str) -> Any:
    return getattr(_model, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_model)))
