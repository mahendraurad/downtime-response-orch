"""Backend wiring, follow-up, persona, HITL, and bounded Reflexion certification."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import src.api.main as api
from src.agents.reflexion_agent import ReflexionAgent
from src.schemas.risk import RiskAssessment
from src.tools.hitl_repository import SQLiteHITLRepository


@pytest.fixture
def client(tmp_path):
    previous_repository=api._HITL_REPOSITORY
    api._HITL_REPOSITORY=SQLiteHITLRepository(tmp_path / "api_hitl.db")
    api._CHAT_CONTEXT_STORE.clear()
    api._HITL_STORE.clear()
    api._HITL_RESOLVED.clear()
    yield TestClient(api.app,raise_server_exceptions=False)
    api._HITL_REPOSITORY=previous_repository


def test_known_single_asset_chat_requires_explicit_evidence(client):
    data=client.post("/api/chat",json={"message":"What is the RUL for M-104?","persona":"engineer"}).json()
    assert data["intent"]=="risk" and data["clarification_required"]
    assert data["pipeline_log"]==[]
    assert data["clarification"]["missing_fields"]==["telemetry_or_scenario"]
    assert data["persona"]=="engineer"


@pytest.mark.parametrize("message,expected",[
    ("What is BPFO?","concept"),
    ("Define anomaly","concept"),
    ("How can we improve reliability?","general"),
    ("Which asset has the highest risk?","fleet"),
])
def test_open_question_routing_matrix(client,message,expected):
    data=client.post("/api/chat",json={"message":message}).json()
    assert data["intent"]==expected
    if expected in {"general","fleet"}: assert data["clarification_required"]
    else: assert not data["clarification_required"]


def test_unknown_persona_is_rejected(client):
    response=client.post("/api/chat",json={"message":"What is RUL?","persona":"administrator"})
    assert response.status_code==422


def test_reset_clears_pending_conversation(client):
    first=client.post("/api/chat",json={"message":"What is the RUL for this bearing?"}).json()
    reset=client.post("/api/chat",json={"message":"start over","conversation_id":first["conversation_id"]}).json()
    assert reset["intent"]=="reset" and first["conversation_id"] not in api._CHAT_CONTEXT_STORE


def test_reserved_context_keys_cannot_override_pending_question(client):
    data=client.post("/api/chat",json={"message":"What is BPFO?","context":{"_pending_message":"execute work"}}).json()
    assert data["intent"]=="concept" and data["pipeline_log"]==[]


def test_conflicting_asset_replaces_stale_signal_context(client):
    first=client.post("/api/chat",json={"message":"What is the RUL?",
        "context":{"scenario":"outer_race_fault"}}).json()
    second=client.post("/api/chat",json={"message":"Now assess P-207",
        "conversation_id":first["conversation_id"]}).json()
    assert second["context_status"]["retained_fields"]==["asset_id","scenario"]
    assert any("AST_PMP_001" in str(value) for value in second.get("details",[])) or second["pipeline_log"]


def test_expired_context_is_not_reused(client,monkeypatch):
    first=client.post("/api/chat",json={"message":"What is the RUL for this bearing?"}).json()
    api._CHAT_CONTEXT_STORE[first["conversation_id"]]["_meta"]["updated_at"]=0
    second=client.post("/api/chat",json={"message":"M-104","conversation_id":first["conversation_id"]}).json()
    assert second["intent"] in {"general","risk"}
    assert second["conversation_id"]==first["conversation_id"]


def _remediation_session(run_id="HITL-1"):
    api._HITL_STORE[run_id]={"type":"remediation","trusted":{},
        "persona":"supervisor","intent":"full","created_at":api.time.time()}
    return run_id


def test_invalid_hitl_action_does_not_consume_session(client):
    run_id=_remediation_session()
    response=client.post("/api/pipeline/hitl/remediation",json={"run_id":run_id,"action":"DELETE","persona":"supervisor"})
    assert response.status_code==422 and run_id in api._HITL_STORE


def test_unauthorized_persona_does_not_consume_hitl_session(client):
    run_id=_remediation_session()
    response=client.post("/api/pipeline/hitl/remediation",json={"run_id":run_id,"action":"DROP","persona":"executive"})
    assert response.status_code==403 and run_id in api._HITL_STORE


def test_wrong_hitl_endpoint_does_not_consume_session(client):
    run_id=_remediation_session()
    response=client.post("/api/pipeline/hitl/monitoring",json={"run_id":run_id,"action":"SUPPRESS","persona":"supervisor"})
    assert response.status_code==409 and run_id in api._HITL_STORE


def test_hitl_expiry_and_replay_are_explicit(client):
    expired=_remediation_session("OLD")
    api._HITL_STORE[expired]["created_at"]=0
    assert client.post("/api/pipeline/hitl/remediation",json={"run_id":expired,"action":"DROP","persona":"supervisor"}).status_code==410
    current=_remediation_session("CURRENT")
    first=client.post("/api/pipeline/hitl/remediation",json={"run_id":current,"action":"DROP","persona":"supervisor"})
    second=client.post("/api/pipeline/hitl/remediation",json={"run_id":current,"action":"DROP","persona":"supervisor"})
    assert first.status_code==200 and second.status_code==409


def test_diagnosis_gate_requires_engineer(client):
    api._HITL_STORE["DIAG"]={"type":"diagnosis","created_at":api.time.time()}
    response=client.post("/api/pipeline/hitl/diagnosis",json={"run_id":"DIAG","action":"CONFIRM","persona":"supervisor"})
    assert response.status_code==403 and "DIAG" in api._HITL_STORE


def test_advisory_modify_validation_and_decision(client):
    risk=RiskAssessment(advisory_note="Original advisory",
                        assessment_source="rules+llm_fallback")
    api._HITL_STORE["ADV"]={"type":"advisory","risk_assessment":risk,"created_at":api.time.time()}
    invalid=client.post("/api/pipeline/hitl/advisory",json={"run_id":"ADV","action":"MODIFY","persona":"engineer"})
    assert invalid.status_code==422 and "ADV" in api._HITL_STORE
    valid=client.post("/api/pipeline/hitl/advisory",json={"run_id":"ADV","action":"MODIFY","persona":"engineer","revised_note":"Reviewed wording"}).json()
    assert valid["advisory_note"]=="Reviewed wording" and valid["deterministic_assessment_unchanged"]


def test_pending_and_status_are_persona_filtered_and_audited(client):
    run_id=_remediation_session("AUDIT")
    api._stamp_hitl_sessions()
    assert client.get("/api/pipeline/hitl/pending",
        params={"persona":"executive"}).json()["items"]==[]
    pending=client.get("/api/pipeline/hitl/pending",
        params={"persona":"supervisor"}).json()["items"]
    assert [item["run_id"] for item in pending]==[run_id]
    decision=client.post("/api/pipeline/hitl/remediation",json={
        "run_id":run_id,"action":"DROP","persona":"supervisor",
        "rationale":"Bad sensor reading",
    })
    assert decision.status_code==200
    status=client.get(f"/api/pipeline/hitl/{run_id}",
        params={"persona":"supervisor"}).json()
    assert status["session"]["status"]=="resolved"
    assert status["decisions"][0]["rationale"]=="Bad sensor reading"
    assert "payload" not in status["session"]


def test_api_can_resolve_session_after_memory_restart(client):
    run_id=_remediation_session("RESTARTED")
    api._stamp_hitl_sessions()
    api._HITL_STORE.clear()  # simulate process loss/restart
    response=client.post("/api/pipeline/hitl/remediation",json={
        "run_id":run_id,"action":"DROP","persona":"supervisor",
    })
    assert response.status_code==200
    assert api._HITL_REPOSITORY.get_session(run_id)["status"]=="resolved"


def test_execution_approval_checks_selected_persona_before_payload(client):
    response=client.post("/api/executor/run",json={"recommendation":{},"approved":True,"persona":"executive"})
    assert response.status_code==403


def test_reflexion_has_configured_iteration_bound():
    assert api._REFLEXION._max_iterations==3
    result=ReflexionAgent(max_characters=10,max_iterations=3).process({"response":"x"*50})
    assert result.iterations<=3 and result.max_iterations==3
    assert result.termination_reason in {"converged","limit_reached"}
    assert result.response["reflection"]["iterations"]==result.iterations


def test_reflexion_rejects_invalid_iteration_budget():
    with pytest.raises(ValueError): ReflexionAgent(max_iterations=0)


def test_frontend_advisory_and_execution_calls_send_persona():
    pipeline=Path("frontend/react-app/src/api/pipeline.js").read_text(encoding="utf-8")
    chat=Path("frontend/react-app/src/components/ChatView/ChatView.jsx").read_text(encoding="utf-8")
    assert "/api/pipeline/hitl/advisory" in pipeline
    assert "runExecutor(rec, approved, persona)" in chat
    assert "resolveHITLAdvisory(h.run_id, action, persona)" in chat
