"""Chat routing for unseen telemetry and Agent 8 learned-history questions."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
import src.api.main as api
from src.agents.learning_memory_agent import LearningMemoryAgent
from src.schemas.execution import ExecutionResult
from src.schemas.feedback import FeedbackEvent, RejectionFeedback
from src.tools.config_loader import load_learning_config
from src.tools.data_loader import load_telemetry_rows


@pytest.fixture
def client(): return TestClient(api.app, raise_server_exceptions=False)


def _agent(tmp_path):
    cfg=load_learning_config(); cfg.repository_path=str(tmp_path/"cases.json")
    cfg.training_export_path=str(tmp_path/"training.jsonl")
    cfg.rejection_repository_path=str(tmp_path/"rejections.json")
    cfg.chat_observation_repository_path=str(tmp_path/"chat_observations.json")
    cfg.llm_narrative_enabled=False
    return LearningMemoryAgent(cfg)


def test_asset_lifecycle_reads_backend_work_order_state(monkeypatch):
    monkeypatch.setattr(api, "_list_workorders", lambda: [
        {"id": "WO-1", "asset_id": "AST_MTR_001", "status": "Pending",
         "updated_at": "2026-09-04T10:00:00+00:00"},
    ])
    assert api._asset_lifecycle("AST_MTR_001") == {
        "status": "warning", "work_order_id": "WO-1", "work_order_status": "Pending",
    }


def test_asset_lifecycle_marks_closed_work_order_healthy(monkeypatch):
    monkeypatch.setattr(api, "_list_workorders", lambda: [
        {"id": "WO-1", "asset_id": "AST_MTR_001", "status": "Pending",
         "updated_at": "2026-09-04T09:00:00+00:00"},
        {"id": "WO-1", "asset_id": "AST_MTR_001", "status": "Closed",
         "updated_at": "2026-09-04T10:00:00+00:00"},
    ])
    assert api._asset_lifecycle("AST_MTR_001")["status"] == "healthy"


@pytest.mark.parametrize("work_order_status", [
    "Approved", "Open", "Active", "Scheduled", "In Progress", "in_progress",
])
def test_asset_lifecycle_keeps_approved_work_order_warning_after_restart(
        monkeypatch, work_order_status):
    """A persisted active WO must override the raw pre-intervention projection."""
    monkeypatch.setattr(api, "_list_workorders", lambda: [{
        "id": "WO-ACTIVE", "asset_id": "AST_MTR_001", "status": work_order_status,
        "updated_at": "2026-09-08T10:00:00+00:00",
    }])
    lifecycle = api._asset_lifecycle("M-104")
    assert lifecycle["status"] == "warning"
    assert lifecycle["work_order_id"] == "WO-ACTIVE"


def test_asset_lifecycle_preserves_latest_rejection_as_unresolved_risk(monkeypatch):
    """Rejection is a valid Agent 8 closure, not evidence of a repaired asset."""
    class Agent:
        def recent_rejections(self, limit=1, asset_id=""):
            assert asset_id == "M-104"
            return [{
                "case_id": "CASE-REJECTED", "asset_id": "M-104",
                "fault_mode": "outer_race_fault", "recorded_at": "2026-09-08T11:00:00+00:00",
            }]

    monkeypatch.setattr(api, "_list_workorders", lambda: [])
    monkeypatch.setattr(api, "_learning_agent", lambda: Agent())
    lifecycle = api._asset_lifecycle("M-104")
    assert lifecycle["status"] == "critical"
    assert lifecycle["decision"] == "rejected"
    assert lifecycle["work_order_status"] == "Rejected"


def test_chat_status_after_rejection_does_not_claim_asset_healthy(client, monkeypatch):
    """The next status question must use durable decision state, not raw DFA text."""
    monkeypatch.setattr(api, "_asset_lifecycle", lambda asset_id: {
        "status": "critical", "work_order_id": "", "work_order_status": "Rejected",
        "decision": "rejected",
    })
    monkeypatch.setattr(api, "format_for_persona", lambda state, persona: {
        "headline": "M-104 — No fault detected. Asset healthy.",
        "details": [], "actions": [],
    })
    data = client.post("/api/chat", json={
        "message": "What is the current status of M-104?",
        "persona": "supervisor",
    }).json()
    assert "critical" in data["response"].lower()
    assert "rejected" in data["response"].lower()
    assert "healthy" not in data["response"].lower()


def _reject(agent, case_id="REC-1", asset_id="AST_MTR_001",
            fault_mode="outer_race_fault", rejected_at="2026-08-26T10:00:00Z"):
    return agent.record_rejection(RejectionFeedback(
        case_id=case_id, asset_id=asset_id, fault_mode=fault_mode,
        reason_code="diagnosis_wrong", free_text="Inspection evidence disagrees",
        persona_id="supervisor", rejected_at=rejected_at,
    ))


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
    assert "No completed or rejected closure records" in data["response"]
    assert data["learning_observation"]["status"]=="no_new_learning"


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


def test_rejection_is_immediately_queryable_by_asset(client,tmp_path,monkeypatch):
    agent=_agent(tmp_path)
    result=_reject(agent)
    assert result.status=="recorded" and result.learning_recorded
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    monkeypatch.setattr(api,"_get_demo_signal",lambda *_: pytest.fail(
        "learning query must not load a registered scenario"))
    data=client.post("/api/chat",json={
        "message":"What do you learnt about M-104?","persona":"engineer"
    }).json()
    assert data["intent"]=="learning_history"
    assert data["call_plan"]==["agent_8"]
    assert [row["node"] for row in data["pipeline_log"]]==["learning_memory"]
    assert data["pipeline_log"][0]["asset_id"]=="AST_MTR_001"
    assert len(data["agent_outputs"]["rejection_learnings"])==1
    assert "Inspection evidence disagrees" in data["details"][0]
    assert "Learning for" in data["details"][0]
    assert "fresh telemetry" in data["details"][0]
    assert data["agent_outputs"]["rejection_learnings"][0]["valid_closure"] is True
    assert data["agent_outputs"]["rejection_learnings"][0]["closure_status"]=="rejected"


def test_learning_history_headline_contains_actionable_m104_learning(
        client,tmp_path,monkeypatch):
    agent=_agent(tmp_path)
    _reject(agent, "REC-M104-HEADLINE", "AST_MTR_001", "diagnosis_wrong")
    monkeypatch.setattr(api, "_learning_agent", lambda: agent)
    data=client.post("/api/chat", json={
        "message":"What did you learn from M-104?", "persona":"supervisor"
    }).json()
    assert data["intent"] == "learning_history"
    assert "Learning update for M-104" in data["response"]
    assert "fresh telemetry" in data["response"]


def test_asset_learning_query_does_not_leak_other_assets(client,tmp_path,monkeypatch):
    agent=_agent(tmp_path)
    _reject(agent,"REC-M104","AST_MTR_001")
    _reject(agent,"REC-P207","AST_PMP_001","lubrication_issue")
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    data=client.post("/api/chat",json={
        "message":"What have you learned about M-104?","persona":"manager"
    }).json()
    records=data["agent_outputs"]["learning_records"]
    assert [row["case_id"] for row in records]==["REC-M104"]
    assert "REC-P207" not in str(data)


def test_global_learning_query_cannot_be_intercepted_as_conversation(
        client,tmp_path,monkeypatch):
    agent=_agent(tmp_path); _reject(agent)
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    monkeypatch.setattr(api,"_llm_chat_classify",lambda *_:{
        "intent_type":"conversational","confidence":1.0})
    data=client.post("/api/chat",json={
        "message":"What have we learnt from recent failures?",
        "conversation_history":[{"role":"user","content":"hello"}],
    }).json()
    assert data["intent"]=="learning_history"
    assert data["call_plan"]==["agent_8"]
    assert data["agent_outputs"]["rejection_learnings"]


def test_rejection_api_then_asset_chat_query_is_end_to_end_queryable(
        client,tmp_path,monkeypatch):
    agent=_agent(tmp_path)
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    rejected=client.post("/api/recommendations/reject",json={
        "case_id":"REC-API-E2E","asset_id":"M-104",
        "fault_mode":"outer_race_fault","reason_code":"wrong_window",
        "free_text":"Production window is unavailable","persona":"supervisor",
    })
    assert rejected.status_code==200
    assert rejected.json()["learning_recorded"] is True
    data=client.post("/api/chat",json={
        "message":"What did you learn about M-104?","persona":"supervisor"
    }).json()
    row=data["agent_outputs"]["rejection_learnings"][0]
    assert row["case_id"]=="REC-API-E2E"
    assert row["asset_id"]=="AST_MTR_001"
    assert "Production window is unavailable" in data["details"][0]


def test_chat_calls_agent8_observer_even_when_nothing_new_is_learned(
        client,tmp_path,monkeypatch):
    agent=_agent(tmp_path)
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    data=client.post("/api/chat",json={"message":"What is RUL?","persona":"engineer"}).json()
    assert data["learning_observation"]["status"] in {"no_new_learning","learned"}
    assert data["learning_observation"]["learning_type"]=="chat_observation"
    assert data["agent_outputs"]["agent8_observation"]==data["learning_observation"]


def test_unknown_asset_chat_gap_is_learned_and_queryable(
        client,tmp_path,monkeypatch):
    agent=_agent(tmp_path)
    monkeypatch.setattr(api,"_learning_agent",lambda:agent)
    first=client.post("/api/chat",json={
        "message":"Analyse asset X-999 completely","persona":"manager",
    }).json()
    assert first["clarification_required"] is True
    assert first["learning_observation"]["status"]=="learned"
    assert first["learning_observation"]["learning_kind"]=="asset_registration_gap"
    second=client.post("/api/chat",json={
        "message":"What have you learned about X-999?","persona":"manager",
    }).json()
    assert second["intent"]=="learning_history"
    assert second["agent_outputs"]["chat_observations"]
    assert "Unregistered asset blocked analysis" in second["details"][0]


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
