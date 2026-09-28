"""倍率必须在任何连接/初始化之前校验，拒绝时不污染已有时钟。"""
from __future__ import annotations

import importlib

import pytest

from package_simulation import SimulationClock


@pytest.mark.parametrize("rate", [0.0, -1.0, float("nan"), float("inf"), -float("inf")])
def test_invalid_constructor_rate_does_not_sample_source(rate):
    def source():
        pytest.fail("非法倍率不应读取时间源")

    with pytest.raises(ValueError):
        SimulationClock(rate, source=source)


@pytest.mark.parametrize("rate", [0.0, -1.0, float("nan"), float("inf"), -float("inf")])
def test_invalid_rate_change_preserves_existing_clock(rate):
    source = [10.0]
    clock = SimulationClock(2.0, source=lambda: source[0])
    source[0] = 12.0
    with pytest.raises(ValueError):
        clock.rate = rate
    assert clock.rate == 2.0
    assert clock.now() == 4.0
    source[0] = 13.0
    assert clock.now() == 6.0
    clock.rate = 3.0
    source[0] = 14.0
    assert clock.now() == 9.0


@pytest.fixture(params=["ptlc_agent_cli", "szlab_handshake_agent"])
def entry(request, monkeypatch):
    module = importlib.import_module(request.param)
    config = {"gvl_path": ["GVL"], "time_scale": 1.0}
    monkeypatch.setattr(module, "load_yaml", lambda _: config)

    def forbidden_adapter(*args, **kwargs):
        pytest.fail("非法倍率不能构造适配器、连接或初始化协议变量")

    monkeypatch.setattr(module, "OpcUaVariableAdapter", forbidden_adapter)
    return module, config


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf"), -float("inf"), None, "invalid"])
def test_invalid_config_is_rejected_before_adapter(entry, rate, capsys):
    module, config = entry
    config["time_scale"] = rate
    assert module.main(["serve"]) == 2
    assert capsys.readouterr().err


@pytest.mark.parametrize("rate", ["nan", "inf", "-inf"])
def test_invalid_cli_override_is_rejected_before_adapter(entry, rate):
    module, _ = entry
    assert module.main(["serve", "--time-scale=" + rate]) == 2


def test_ptlc_upper_limit_is_rejected_before_adapter(monkeypatch):
    import ptlc_agent_cli as module
    monkeypatch.setattr(module, "load_yaml", lambda _: {"gvl_path": ["GVL"], "time_scale": 1000.001})
    monkeypatch.setattr(module, "OpcUaVariableAdapter", lambda *a, **k: pytest.fail("超过上限不能连接"))
    assert module.main(["serve"]) == 2


class Admitted(Exception):
    """在任何真实连接之前截断有效配置的后续路径。"""


@pytest.mark.parametrize("rate", [0.25, 1.0, 1000.0])
def test_existing_valid_rate_range_still_reaches_adapter(entry, rate, monkeypatch):
    module, config = entry
    config["time_scale"] = rate

    def admitted(*args, **kwargs):
        raise Admitted

    monkeypatch.setattr(module, "OpcUaVariableAdapter", admitted)
    with pytest.raises(Admitted):
        module.main(["serve"])


def test_valid_cli_rate_overrides_invalid_config(entry, monkeypatch):
    module, config = entry
    config["time_scale"] = None

    def admitted(*args, **kwargs):
        raise Admitted

    monkeypatch.setattr(module, "OpcUaVariableAdapter", admitted)
    with pytest.raises(Admitted):
        module.main(["serve", "--time-scale=2"])


@pytest.mark.parametrize("command", ["list", "check"])
def test_ptlc_non_simulating_commands_keep_ignoring_unused_rate(command, monkeypatch):
    import ptlc_agent_cli as module
    monkeypatch.setattr(module, "load_yaml", lambda _: {"gvl_path": ["GVL"], "time_scale": None})

    def admitted(*args, **kwargs):
        raise Admitted

    monkeypatch.setattr(module, "OpcUaVariableAdapter", admitted)
    with pytest.raises(Admitted):
        module.main([command])
