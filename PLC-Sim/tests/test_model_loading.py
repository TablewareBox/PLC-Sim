from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from model_loading import ModelLoadError, load_module, load_symbol


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


def test_legacy_imports_use_exact_package_types():
    import szlab_handshake_agent
    import szlab_s1_sim
    from szlab_package_runtime import SzlabPackageRuntime
    from szlab_poly_studio.simulation.plc_handshake import WorkflowHandshakeSimulator
    from szlab_poly_studio.simulation.protocol_observer import SzlabProtocolObserver
    from szlab_poly_studio.simulation.s1 import S1SimulationServer

    assert szlab_handshake_agent.WorkflowHandshakeSimulator is WorkflowHandshakeSimulator
    assert szlab_s1_sim.S1SimulationServer is S1SimulationServer
    namespace = {}
    exec("from szlab_handshake_agent import *", namespace)
    assert namespace["S06_PROCESS"] == szlab_handshake_agent.S06_PROCESS
    assert isinstance(SzlabPackageRuntime(), SzlabProtocolObserver)


@pytest.mark.parametrize("command,expected", [("server", 0), ("ptlc-handshake", 0), ("szlab-handshake", 1)])
def test_missing_szlab_package_only_blocks_selected_szlab_entry(command, expected, tmp_path):
    root = str(Path(__file__).parents[1])
    script = """
import sys
import importlib.abc
class MissingSzlab(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'szlab_poly_studio' or fullname.startswith('szlab_poly_studio.'):
            raise ModuleNotFoundError('isolated missing package', name=fullname)
sys.meta_path.insert(0, MissingSzlab())
sys.path.insert(0, sys.argv[1])
import cli
raise SystemExit(cli.main([sys.argv[2], '--help']))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, root, command],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if expected:
        assert "无法加载设备包模块" in result.stderr
        assert "szlab_poly_studio" in result.stderr
    else:
        assert "usage:" in result.stdout
