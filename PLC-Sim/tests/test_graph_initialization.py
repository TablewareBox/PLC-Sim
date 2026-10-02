"""只用人工图与投影替身检查基础设施，不导入领域包或连接端点。"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from graph_initialization import prepare_graph_initialization


@pytest.fixture
def inputs(tmp_path):
    graph, roles = tmp_path / "graph.json", tmp_path / "roles.json"
    graph.write_text('{"nodes": [], "fixture": "synthetic"}')
    roles.write_text('{"R": "warehouse"}')
    projection = SimpleNamespace(values={"present": True, "empty": False},
        sources={"present": "warehouse:A", "empty": "warehouse:B"},
        occupancy={"warehouse:A": "sample", "warehouse:B": None, "warehouse:C": None},
        unmapped_sites=("warehouse:C",))
    return graph, roles, projection


def test_merge_and_provenance_keep_graph_authority(inputs):
    graph, roles, projection = inputs
    before = graph.read_bytes(), roles.read_bytes()
    result = prepare_graph_initialization(graph, roles, projector=lambda *_: projection,
                                           configured_values={"ready": True, "present": True})
    assert result.initial_values == {"ready": True, "present": True, "empty": False}
    assert result.provenance["unmapped_sites"] == ["warehouse:C"]
    assert result.provenance["runtime_qualified"] is False
    assert result.provenance["occupancy"] == projection.occupancy
    projection.values["present"] = False
    assert result.initial_values["present"] is True
    assert before == (graph.read_bytes(), roles.read_bytes())


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_conflicting_config_never_silently_overrides_graph(inputs, value):
    graph, roles, projection = inputs
    with pytest.raises(ValueError, match="冲突"):
        prepare_graph_initialization(graph, roles, projector=lambda *_: projection, configured_values={"present": value})


@pytest.mark.parametrize("kind", ["integer", "missing_source", "unknown_site", "duplicate_site", "unreported_site", "wrong_occupancy", "overlap", "empty"])
def test_inconsistent_projector_result_rejected(inputs, kind):
    graph, roles, projection = inputs
    if kind == "integer":projection.values["present"] = 1
    if kind == "missing_source":del projection.sources["present"]
    if kind == "unknown_site":projection.sources["present"] = "missing"
    if kind == "duplicate_site":projection.sources["empty"] = "warehouse:A"
    if kind == "unreported_site":projection.unmapped_sites = ()
    if kind == "wrong_occupancy":projection.values["present"] = False
    if kind == "overlap":projection.unmapped_sites = ("warehouse:A", "warehouse:C")
    if kind == "empty":projection.values = {};projection.sources = {}
    with pytest.raises(ValueError):
        prepare_graph_initialization(graph, roles, projector=lambda *_: projection, configured_values={})


@pytest.mark.parametrize("kind", ["duplicate_json", "nonfinite", "duplicate_role", "relative", "symlink", "mutated_graph", "changed_file"])
def test_bad_or_changed_inputs_rejected_before_use(inputs, kind):
    graph, roles, projection = inputs
    if kind == "duplicate_json":graph.write_text('{"nodes":[],"nodes":[]}')
    if kind == "nonfinite":graph.write_text('{"value":NaN}')
    if kind == "duplicate_role":roles.write_text('{"A":"warehouse","B":"warehouse"}')
    if kind == "relative":graph = type(graph)("relative.json")
    if kind == "symlink":
        link=graph.with_name("link.json");link.symlink_to(graph);graph=link
    def project(document, bindings):
        if kind == "mutated_graph":document["new"] = True
        if kind == "changed_file":graph.write_text("{}")
        return projection
    with pytest.raises(ValueError):
        prepare_graph_initialization(graph, roles, projector=project, configured_values={})
