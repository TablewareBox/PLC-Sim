"""执行真实代理控制流；领域模型、时钟及 OPC 适配器全部为显式替身。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType

import pytest


@pytest.fixture
def agent(tmp_path, monkeypatch):
    seen={"adapter":0,"projector":0,"writes":{},"config":{"mode":"package","initial_values":{"ready":True}}}
    def project(graph, roles):
        seen["projector"] += 1
        return SimpleNamespace(values={"present":graph["occupied"]}, sources={"present":"warehouse:A"},
            occupancy={"warehouse:A":"sample" if graph["occupied"] else None}, unmapped_sites=())
    class Model:
        def __init__(self, adapter, **kwargs):
            seen["model_options"]=kwargs;self.values=kwargs["initial_values"];self.completed_actions=0
        def initialize(self):seen["writes"].update(self.values)
        def initialization_values(self):return self.values
        def check_supported_prerequisites(self):return [(k,True,v,v) for k,v in self.values.items()]
        def step(self, now):self.completed_actions=1;return []
        def all_cycles_idle(self):return True
        def protocol_snapshot(self):return {"test_double":True}
        def cleanup(self):seen["cleanup"]=True
    class Runtime:
        def __init__(self, **kwargs):self.runtime=SimpleNamespace(clock=SimpleNamespace(now=lambda:1))
        def initialize_protocol(self, values):seen["runtime_values"]=dict(values)
        def snapshot(self, value):return value
        def stop(self):seen["stopped"]=True
    domain=SimpleNamespace(HandshakeEvent=object,SUPPORTED_ACTIONS=(),VariableAdapter=object,
        WORKFLOW_ALIASES={},WORKFLOW_IDS=("fixture_only",),WorkflowHandshakeSimulator=Model,
        WorkflowSpec=object,build_workflow_specs=lambda **kwargs:())
    def load(name):
        if name.endswith("graph_inputs"):return SimpleNamespace(project_initial_inputs=project)
        assert name.endswith("plc_handshake");return domain
    dependencies={"common":{"load_yaml":lambda _:deepcopy(seen["config"])},
        "package_simulation":{"write_snapshot_atomic":lambda path,value:Path(path).write_text(json.dumps(value))},
        "szlab_package_runtime":{"SzlabPackageRuntime":Runtime,"default_package_config_path":lambda:"unused"},
        "szlab_s1_sim":{"S1SimulationServer":object},"model_loading":{"load_module":load}}
    for name, attrs in dependencies.items():
        module=ModuleType(name);module.__dict__.update(attrs);monkeypatch.setitem(sys.modules,name,module)
    source=Path(__file__).resolve().parents[1]/"szlab_handshake_agent.py"
    spec=importlib.util.spec_from_file_location("plc_graph_agent_fixture",source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Adapter:
        def __init__(self,*args,**kwargs):seen["adapter"]+=1
        def connect(self):seen["connected"]=True
        def disconnect(self):seen["disconnected"]=True
    monkeypatch.setattr(module,"OpcUaVariableAdapter",Adapter)
    graph,roles,state=tmp_path/"graph.json",tmp_path/"roles.json",tmp_path/"state.json"
    graph.write_text('{"occupied":true}');roles.write_text('{"R":"warehouse"}')
    return module,seen,graph,roles,state


def test_graph_projection_reaches_existing_initializer_and_snapshot(agent):
    module,seen,graph,roles,state=agent
    assert module.main(["serve","--graph",str(graph),"--warehouse-roles",str(roles),
        "--state-file",str(state),"--max-actions","1","--no-s1-http"])==0
    assert seen["writes"]=={"ready":True,"present":True}
    assert seen["runtime_values"]==seen["writes"]
    assert seen["projector"]==1 and seen["adapter"]==1 and seen["disconnected"]
    assert json.loads(state.read_text())["graph_initialization"]["graph"]==str(graph)


@pytest.mark.parametrize("kind", ["missing_graph","missing_roles","no_initialize","list","check","conflict","bad_json"])
def test_invalid_graph_startup_never_constructs_adapter(agent,kind):
    module,seen,graph,roles,state=agent
    args=["serve","--graph",str(graph),"--warehouse-roles",str(roles),"--no-s1-http"]
    if kind=="missing_graph":args=["serve","--warehouse-roles",str(roles)]
    if kind=="missing_roles":args=["serve","--graph",str(graph)]
    if kind=="no_initialize":args.append("--no-initialize")
    if kind in {"list","check"}:args[0]=kind
    if kind=="conflict":seen["config"]["initial_values"]["present"]=False
    if kind=="bad_json":graph.write_text("{")
    assert module.main(args)==2
    assert seen["adapter"]==0 and not seen["writes"]


def test_legacy_path_does_not_load_graph_projector(agent):
    module,seen,*_=agent
    assert module.main(["serve","--max-actions","1","--no-s1-http"])==0
    assert seen["projector"]==0 and seen["writes"]=={"ready":True}
