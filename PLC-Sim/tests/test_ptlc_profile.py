from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sys

import pytest

from common import default_ptlc_nodes_path, default_ptlc_config_path, load_ptlc_nodes, load_yaml
from eit_ptlc.simulation import plc_profile
from gui.server_routes import ServerStartReq, _resolve_server_node_paths
from ptlc_agent_cli import build_parser
import server

ROOT = Path(__file__).parents[1]


def test_package_defaults_preserve_all_node_fields_and_config_values():
    assert default_ptlc_nodes_path() == plc_profile.default_nodes_path()
    assert default_ptlc_config_path() == plc_profile.default_config_path()
    assert [asdict(n) for n in load_ptlc_nodes(default_ptlc_nodes_path())] == [
        asdict(n) for n in load_ptlc_nodes(ROOT / "config/ptlc_nodes.yaml")
    ]
    assert load_yaml(str(default_ptlc_config_path())) == load_yaml(str(ROOT / "config/ptlc_handshake.yaml"))
    assert build_parser().parse_args(["list"]).config == str(default_ptlc_config_path())


def test_gui_default_and_explicit_snapshot_are_distinct():
    assert _resolve_server_node_paths(ServerStartReq(profile="ptlc"), "ptlc") == [default_ptlc_nodes_path()]
    snapshot = ROOT / "config/ptlc_nodes.yaml"
    assert _resolve_server_node_paths(ServerStartReq(profile="ptlc", csv=str(snapshot)), "ptlc") == [snapshot]


@pytest.mark.parametrize("explicit", [False, True])
def test_server_resolves_package_default_or_explicit_snapshot_before_start(monkeypatch, explicit):
    """捕获解析输入后中止，测试不得启动网络服务。"""
    paths = []
    class BeforeServerStart(Exception):
        pass
    def capture(path, ns_index):
        paths.append(path)
        raise BeforeServerStart
    monkeypatch.setattr(server, "load_ptlc_nodes", capture)
    argv = ["server", "--profile", "ptlc"]
    snapshot = ROOT / "config/ptlc_nodes.yaml"
    if explicit:
        argv.extend(["--csv", str(snapshot)])
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(BeforeServerStart):
        server.main()
    assert paths == [snapshot if explicit else default_ptlc_nodes_path()]


def test_gui_ptlc_request_passes_default_table_and_namespace_before_start(monkeypatch):
    """验证真实请求解析到线程调用，捕获输入后结束，不创建服务进程。"""
    import asyncio
    from fastapi import HTTPException
    from gui import server_routes
    paths = []
    def capture(path, ns_index):
        paths.append((path, ns_index))
        raise ValueError("已捕获解析输入")
    monkeypatch.setattr(server_routes.STATE, "attached", False)
    monkeypatch.setattr(server_routes.STATE, "server_proc", None)
    monkeypatch.setattr(server_routes, "clear_server_metadata", lambda **kwargs: None)
    monkeypatch.setattr(server_routes, "load_ptlc_nodes", capture)
    with pytest.raises(HTTPException, match="已捕获解析输入"):
        asyncio.run(server_routes.api_server_start(ServerStartReq(profile="ptlc", ns_index=6)))
    assert paths == [(default_ptlc_nodes_path(), 6)]
