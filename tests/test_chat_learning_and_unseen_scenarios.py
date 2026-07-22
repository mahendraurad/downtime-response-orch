"""Chat routing for unseen telemetry and Agent 8 learned-history questions."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
import src.api.main as api
from src.agents.learning_memory_agent import LearningMemoryAgent
from src.schemas.execution import ExecutionResult
from src.schemas.feedback import FeedbackEvent
from src.tools.config_loader import load_learning_config
from src.tools.data_loader import load_telemetry_rows


@pytest.fixture
def client(): return TestClient(api.app, raise_server_exceptions=False)


def _agent(tmp_path):
    cfg=load_learning_config(); cfg.repository_path=str(tmp_path/"cases.json")
    cfg.training_export_path=str(tmp_path/"training.jsonl"); cfg.llm_narrative_enabled=False
    return LearningMemoryAgent(cfg)


def _learn(agent, number, fault="outer_race_fault"):
    case_id=f"CASE-{number}"
    execution=ExecutionResult(case_id=case_id,action_taken="stop_and_replace",
        status="success",audit_reference=f"AUDIT-{number}",execution_eligible=True)
    feedback=FeedbackEvent(case_id=case_id,asset_id="AST_MTR_001",bearing_id="BRG_001",
        confirmed_fault_mode=fault,root_cause=f"confirmed cause {number}",
        action_taken="bearing_replacement",recommendation_followed=True,
        technician_notes=f"lesson {number}",post_repair_vib_mm_s=1.0,
        post_repair_temp_c=40.0,closed_at=f"2026-07-{10+number:02d}T10:00:00Z")
    return agent.process(execution,feedback)


def test_md_history_routes_to_agent8_and_returns_latest_three(client,tmp_path,monkeypatch):
    agent=_agent(tmp_path)
    for number in range(1,5): assert _learn(agent,number).learning_status=="learned"
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    data=client.post("/api/chat",json={"message":"As MD, what were the last 3 failures and what are the learnings from them?","persona":"executive"}).json()
    assert data["intent"]=="learning_history" and data["call_plan"]==["agent_8"]
    assert data["needs_context"] is False
    assert [row["case_id"] for row in data["agent_outputs"]["learned_cases"]]==["CASE-4","CASE-3","CASE-2"]
    assert data["pipeline_log"][0]["node"]=="learning_memory" and data["pipeline_log"][0]["count"]==3
    assert len(data["case_references"])==3


def test_history_with_no_learning_is_honest(client,tmp_path,monkeypatch):
    monkeypatch.setattr(api,"_learning_agent",lambda:_agent(tmp_path))
    data=client.post("/api/chat",json={"message":"What did we learn from recent failures?","persona":"md"}).json()
    assert data["agent_outputs"]["learned_cases"]==[]
    assert "No validated closed failure cases" in data["response"]


def test_history_returns_fewer_than_three_when_only_one_exists(client,tmp_path,monkeypatch):
    agent=_agent(tmp_path); _learn(agent,1); monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    data=client.post("/api/chat",json={"message":"last three failures"}).json()
    assert len(data["agent_outputs"]["learned_cases"])==1 and "last 1 validated" in data["response"]


def test_agent8_learning_is_immediately_queryable(client,tmp_path,monkeypatch):
    agent=_agent(tmp_path); learned=_learn(agent,1,"inner_race_fault")
    assert learned.learning_status=="learned" and learned.persistence_status=="stored"
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    data=client.post("/api/chat",json={"message":"failure history"}).json()
    assert data["agent_outputs"]["learned_cases"][0]["fault_mode"]=="inner_race_fault"
    assert "confirmed cause 1" in data["details"][0]


def _unseen_signal(label):
    signal=deepcopy(load_telemetry_rows("outer_race_fault")[-1]); signal["telemetry_id"]=f"UNSEEN-{label}"
    return signal


def test_unseen_raw_signal_routes_without_named_scenario(client):
    data=client.post("/api/chat",json={"message":"What maintenance action should we take for this new reading?","persona":"engineer","context":{"signal":_unseen_signal('FULL')}}).json()
    assert [item["node"] for item in data["pipeline_log"]]==["data_foundation","monitoring","failure_intelligence","predictive_risk","knowledge","prescriptive"]
    assert data["agent_outputs"]["recommendation"]["recommendation_status"]=="ok"


@pytest.mark.parametrize(("question","last_node"),[("What is the sensor status?","data_foundation"),("Is this reading anomalous?","monitoring"),("Diagnose this new fault","failure_intelligence"),("What is its remaining useful life?","predictive_risk"),("Show the applicable SOP","knowledge"),("What maintenance plan do you recommend?","prescriptive")])
def test_unseen_signal_respects_requested_agent_depth(client,question,last_node):
    data=client.post("/api/chat",json={"message":question,"context":{"signal":_unseen_signal(last_node)}}).json()
    assert data["pipeline_log"][-1]["node"]==last_node


def test_unknown_named_scenario_is_not_fabricated(client):
    response=client.post("/api/chat",json={"message":"Diagnose this","context":{"scenario":"never-seen-scenario"}})
    assert response.status_code==404
