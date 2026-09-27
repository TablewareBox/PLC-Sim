"""从显式 Python 入口加载设备包能力，不搜索历史目录或回退内置模型。"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any


class ModelLoadError(ImportError):
    """选定设备包能力无法加载。"""


def load_module(name: str) -> ModuleType:
    """仅加载调用方选择的模块；不会实例化设备或启动仿真。"""
    if not isinstance(name, str) or not name or any(not part.isidentifier() for part in name.split(".")):
        raise ModelLoadError(f"设备模型模块名无效: {name!r}")
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise ModelLoadError(
            f"无法加载设备包模块 {name}；请安装所选版本的驱动包及其依赖。原始错误: {exc}"
        ) from exc


def load_symbol(reference: str) -> Any:
    """解析 module:attribute 入口，保留原始类型身份。"""
    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ModelLoadError(f"设备模型入口须为 module:attribute: {reference!r}")
    module, attribute = reference.split(":")
    if not attribute or any(not part.isidentifier() for part in attribute.split(".")):
        raise ModelLoadError(f"设备模型属性名无效: {reference!r}")
    value: Any = load_module(module)
    try:
        for part in attribute.split("."):
            value = getattr(value, part)
    except AttributeError as exc:
        raise ModelLoadError(f"设备包没有提供入口: {reference}") from exc
    return value
