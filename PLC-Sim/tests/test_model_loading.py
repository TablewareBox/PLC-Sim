from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from plc_sim.model_loading import ModelLoadError, load_module, load_symbol


def test_explicit_entry_preserves_type_and_does_not_construct(monkeypatch):
    module = ModuleType("independent_device_model")

    class Model:
        def __init__(self):
            raise AssertionError("加载入口不得实例化设备")

    module.Model = Model
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert load_module(module.__name__) is module
    assert load_symbol(module.__name__ + ":Model") is Model


@pytest.mark.parametrize("reference", ["", "name", "name:", ":Model", "name:bad-name", "a:b:c"])
def test_invalid_entry_is_rejected(reference):
    with pytest.raises(ModelLoadError):
        load_symbol(reference)


def test_missing_module_and_attribute_explain_selected_entry():
    with pytest.raises(ModelLoadError, match="missing_device_model_xyz") as error:
        load_symbol("missing_device_model_xyz:Model")
    assert isinstance(error.value.__cause__, ModuleNotFoundError)
    with pytest.raises(ModelLoadError, match="没有提供入口"):
        load_symbol("json:missing_factory_xyz")


