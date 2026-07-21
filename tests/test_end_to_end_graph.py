"""Real LangGraph wiring and conditional-agent invocation tests."""
from copy import deepcopy
import pytest
from src.orchestrator.graph import run_pipeline
from src.tools.data_loader import load_telemetry_rows

# ************** Added by Prateek Mittal on 20th July 2026 ******************
def _row(name): return deepcopy(load_telemetry_rows(name)[-1])
def _nodes(state): return [x["node"] for x in state["pipeline_log"]]

@pytest.mark.parametrize(("intent","expected"),[("status",["data_foundation"]),("anomaly",["data_foundation","monitoring"]),("diagnosis",["data_foundation","monitoring","failure_intelligence"]),("risk",["data_foundation","monitoring","failure_intelligence","predictive_risk"])])
def test_intent_calls_only_required_agents(intent,expected): assert _nodes(run_pipeline(_row("outer_race_fault"),intent=intent))==expected

def test_full_fault_calls_agents_1_to_6_and_pauses_for_approval():
    state=run_pipeline(_row("outer_race_fault"),intent="full")
    assert _nodes(state)==["data_foundation","monitoring","failure_intelligence","predictive_risk","knowledge","prescriptive"]
    assert state["recommendation"].approval_status=="pending" and "execution_result" not in state

def test_approved_fault_calls_executor_not_learning_without_feedback():
    state=run_pipeline(_row("outer_race_fault"),approval_status="approved")
    assert _nodes(state)[-1]=="executor" and "learning" not in _nodes(state)

@pytest.mark.parametrize("scenario",["healthy","startup_filter"])
def test_non_anomaly_stops_after_monitoring(scenario): assert _nodes(run_pipeline(_row(scenario)))==["data_foundation","monitoring"]
@pytest.mark.parametrize("scenario",["signal_dropout","unknown_asset"])
def test_bad_data_stops_after_foundation(scenario): assert _nodes(run_pipeline(_row(scenario)))==["data_foundation"]
def test_every_log_entry_has_status_and_latency(): assert all({"node","status","latency_ms"}<=set(x) for x in run_pipeline(_row("outer_race_fault"))["pipeline_log"])

def test_agent_failure_is_captured_in_state(monkeypatch):
    import src.orchestrator.graph as graph
    agents=list(graph._get_agents())
    class Broken:
        def process(self,*_): raise RuntimeError("internal secret detail")
    agents[2]=Broken(); monkeypatch.setattr(graph,"_agents",tuple(agents))
    state=graph.run_pipeline(_row("outer_race_fault"),intent="diagnosis")
    assert "error" in state and state["pipeline_log"][-1]["status"]=="error"
# ***********************
