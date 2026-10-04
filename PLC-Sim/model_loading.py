"""从显式 Python 入口加载设备包能力，不搜索历史目录或回退内置模型。"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from unilabos_sim_contracts import SourceLock, VerifiedSource, verify_source


class ModelLoadError(ImportError):
    """选定设备包能力无法加载。"""


@dataclass(frozen=True)
class ModelSource:
    """调用方冻结的锁及独立来源采集结果；不自行猜测 Git 或 wheel 身份。"""

    lock: SourceLock
    root: Path
    actual_commit: str
    actual_dirty_digest: str | None

    def verify(self, imported_file: str) -> VerifiedSource:
        return verify_source(self.lock, root=self.root, imported_file=Path(imported_file),
                             actual_commit=self.actual_commit, actual_dirty_digest=self.actual_dirty_digest)


def load_module(name: str, *, source: ModelSource | None = None) -> ModuleType:
    """仅加载调用方选择的模块；不会实例化设备或启动仿真。"""
    if not isinstance(name, str) or not name or any(not part.isidentifier() for part in name.split(".")):
        raise ModelLoadError(f"设备模型模块名无效: {name!r}")
    try:
        if source is not None:
            # 先校验将被导入的文件，再校验实际模块，拒绝旧 editable 及缓存串源。
            spec = importlib.util.find_spec(name)
            if spec is None or not spec.origin:
                raise ValueError("选定模型没有可核验的来源文件")
            source.verify(spec.origin)
        module = importlib.import_module(name)
        if source is not None:
            location = getattr(module, "__file__", None)
            if not location:
                raise ValueError("实际模型模块没有来源文件")
            source.verify(location)
        return module
    except (ImportError, ValueError, OSError) as exc:
        if source is None and not isinstance(exc, ImportError):
            raise
        raise ModelLoadError(
            f"无法加载设备包模块 {name}；请安装所选版本的驱动包及其依赖。原始错误: {exc}"
        ) from exc


def load_symbol(reference: str, *, source: ModelSource | None = None) -> Any:
    """解析 module:attribute 入口，保留原始类型身份。"""
    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ModelLoadError(f"设备模型入口须为 module:attribute: {reference!r}")
    module, attribute = reference.split(":")
    if not attribute or any(not part.isidentifier() for part in attribute.split(".")):
        raise ModelLoadError(f"设备模型属性名无效: {reference!r}")
    value: Any = load_module(module, source=source)
    try:
        for part in attribute.split("."):
            value = getattr(value, part)
    except AttributeError as exc:
        raise ModelLoadError(f"设备包没有提供入口: {reference}") from exc
    return value
