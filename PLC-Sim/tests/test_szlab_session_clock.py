"""通过有限CLI运行核对实际模型、会话时钟与协议事件；不连接任何端点。"""
from __future__ import annotations

import json

import pytest

import szlab_handshake_agent as cli
import szlab_package_runtime as package_runtime
from package_simulation import SimulationClock
from test_szlab_handshake_agent import MemoryAdapter


@pytest.mark.parametrize("rate", [0.25, 1.0, 4.0])
@pytest.mark.parametrize("family", ["stirrer", "pump"])
@pytest.mark.parametrize("legacy", [False, True])
def test_cli_scans_on_session_time_without_scaling_duration_twice(
    rate, family, legacy, monkeypatch, tmp_path
):
    wall = [100.0]
    events = []
    calls = []
    connections = []
    clock = SimulationClock(rate, source=lambda: wall[0])
    monkeypatch.setattr(package_runtime, "SimulationClock", lambda selected_rate: clock)

    class Adapter(MemoryAdapter):
        def __init__(self, *args, **kwargs):
            super().__init__()
            # 整包扫描包含六个搅拌位；PC拥有的请求节点由测试输入提供。
            for position in range(1, 7):
                self.values[cli.s04_process(position)] = 0
                self.values[cli.s04_params_written(position)] = False

        def connect(self):
            connections.append("test-adapter-connect")

        def disconnect(self):
            connections.append("test-adapter-disconnect")

    monkeypatch.setattr(cli, "OpcUaVariableAdapter", Adapter)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)

    def sleep(seconds):
        assert len(calls) < 100, "有限测试禁止进入无限扫描循环"
        wall[0] += seconds

    monkeypatch.setattr(cli.time, "sleep", sleep)
    original_step = cli.WorkflowHandshakeSimulator.step

    def stimulate_and_scan(model, now=None):
        assert now == clock.now(), "扫描应消费会话仿真时间，不能自行读取墙钟"
        assert model.time_scale == 1.0, "倍速不能在时钟和动作延时中重复计算"
        if family == "stirrer":
            process, written, done = cli.s04_process(1), cli.s04_params_written(1), cli.s04_done(1)
        else:
            process, written, done = cli.S06_PROCESS, cli.S06_PARAMS_WRITTEN, cli.S06_DONE
        if not calls:
            model.adapter.write(process, 3 if family == "stirrer" else 1)
            model.adapter.write(written, True)
            if family == "stirrer":
                model.adapter.write(cli.s04_duration(1), 2000)
        elif model.adapter.read(done):
            model.adapter.write(process, 0)
            model.adapter.write(written, False)
        result = original_step(model, now=now)
        calls.append(now)
        events.extend((event.phase, now, wall[0] - 100.0) for event in result)
        return result

    monkeypatch.setattr(cli.WorkflowHandshakeSimulator, "step", stimulate_and_scan)
    output = tmp_path / "state.json"
    workflow = "szlab_magnetic_stirring_workflow" if family == "stirrer" else "szlab_mixer_pump_production"
    argv = ["serve", "--workflow", workflow, "--no-s1-http", "--max-actions=1",
            "--time-scale=" + str(rate), "--process-delay=2", "--poll-interval=0.125",
            "--state-file", str(output)]
    if legacy:
        argv.append("--legacy-workflow-mode")
    assert cli.main(argv) == 0
    assert connections == ["test-adapter-connect", "test-adapter-disconnect"]
    assert [phase for phase, _, _ in events] == ["accepted", "completed", "reset"]
    assert events[0][1:] == (0.0, 0.0)
    assert events[1][1] == 2.0
    assert events[1][2] == 2.0 / rate
    snapshot = json.loads(output.read_text())
    assert snapshot["time_scale"] == rate
    assert snapshot["state"] == "stopped"
    assert snapshot["active_runs"] == []
    assert snapshot["recent_runs"][0]["state"] == "SUCCEEDED"
    assert snapshot["recent_runs"][0]["completed_at"] == 2.0
    assert [event["timestamp"] for event in snapshot["events"]] == [when for _, when, _ in events]
    assert snapshot["protocol"]["time_domain"] == "simulation_seconds"
    assert snapshot["protocol"]["mode"] == ("legacy-workflow" if legacy else "package")
    if family == "stirrer":
        assert snapshot["events"][0]["detail"]["duration_seconds"] == 2.0
