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


def test_known_single_asset_chat_loads_registered_demo_evidence(client):
    data=client.post("/api/chat",json={"message":"What is the RUL for M-104?","persona":"engineer"}).json()
    assert data["intent"]=="risk" and not data["clarification_required"]
    assert [row["node"] for row in data["pipeline_log"]]==[
        "data_foundation","monitoring","failure_intelligence","predictive_risk",
    ]
    assert data["persona"]=="engineer"


def test_completed_asset_counterfactual_does_not_repeat_current_risk(client, monkeypatch):
    monkeypatch.setattr(api, "_asset_lifecycle", lambda _asset: {
        "status": "healthy", "work_order_id": "WO-CLOSED", "work_order_status": "Closed",
    })
    data = client.post("/api/chat", json={
        "message": "What would have happened if I had rejected the work order for M-104?",
        "persona": "supervisor", "conversation_id": "COUNTERFACTUAL-CLOSED-1",
    }).json()
    assert data["intent"] == "risk"
    assert "If it had been rejected instead" in data["response"]
    assert "pre-repair risk" in data["response"]
    assert "Current status is healthy" in data["response"]
    assert data["lifecycle"]["status"] == "healthy"
    assert not data.get("hitl_required")


def test_completed_asset_actions_are_not_presented_as_new_approval(client, monkeypatch):
    monkeypatch.setattr(api, "_asset_lifecycle", lambda _asset: {
        "status": "healthy", "work_order_id": "WO-CLOSED", "work_order_status": "Closed",
    })
    data = client.post("/api/chat", json={
        "message": "What is the current status of M-104?",
        "persona": "supervisor", "conversation_id": "HEALTHY-ACTIONS-1",
    }).json()
    assert any("was completed" in action for action in data["actions"])
    assert all("Approve work order" not in action for action in data["actions"])


@pytest.mark.parametrize("message,expected",[
    ("What is BPFO?","concept"),
    ("Define anomaly","concept"),
    ("How can we improve reliability?","general"),
    ("Which asset has the highest risk?","fleet"),
])
def test_open_question_routing_matrix(client,message,expected):
    data=client.post("/api/chat",json={"message":message}).json()
    assert data["intent"]==expected
    if expected=="fleet": assert data["clarification_required"]
    else: assert not data["clarification_required"]
    if expected=="general": assert data["source_type"]=="approved_rag" and data["citations"]


def test_unknown_persona_is_rejected(client):
    response=client.post("/api/chat",json={"message":"What is RUL?","persona":"administrator"})
    assert response.status_code==422


def test_reset_clears_pending_conversation(client):
    first=client.post("/api/chat",json={"message":"What is the RUL for this bearing?"}).json()
    reset=client.post("/api/chat",json={"message":"start over","conversation_id":first["conversation_id"]}).json()
    assert reset["intent"]=="reset" and first["conversation_id"] not in api._CHAT_CONTEXT_STORE


def test_reserved_context_keys_cannot_override_pending_question(client):
    data=client.post("/api/chat",json={"message":"What is BPFO?","context":{"_pending_message":"execute work"}}).json()
    assert data["intent"]=="concept"
    assert data["pipeline_log"][0]["node"]=="knowledge_rag"


def test_conflicting_asset_replaces_stale_signal_context(client):
    first=client.post("/api/chat",json={"message":"What is the RUL?",
        "context":{"scenario":"outer_race_fault"}}).json()
    second=client.post("/api/chat",json={"message":"Now assess P-207",
        "conversation_id":first["conversation_id"]}).json()
    assert second["context_status"]["retained_fields"]==[
        "asset_id","evidence_source","row_index","scenario",
    ]
    assert not second["clarification_required"] and second["pipeline_log"]
    assert api._CHAT_CONTEXT_STORE[first["conversation_id"]]["scenario"]=="lubrication_issue"


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


def test_execution_card_is_durable_and_completed_asset_has_no_new_card(client):
    pending = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-EXEC-1",
    }).json()
    run_id = pending["recommendation"]["hitl_run_id"]
    assert pending["recommendation"]["approval_required"] is True
    rows = client.get("/api/pipeline/hitl/pending", params={
        "persona": "supervisor", "gate": "execution",
    }).json()["items"]
    card = next(row for row in rows if row["run_id"] == run_id)
    assert card["hitl_payload"]["recommendation"]["asset_id"] == "AST_PMP_001"

    healthy = client.post("/api/chat", json={
        "message": "Analyse M-104 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-EXEC-2",
    }).json()
    assert healthy["lifecycle"]["status"] == "healthy"
    assert healthy["recommendation"]["approval_required"] is False
    assert not any(row["run_id"] == healthy["recommendation"]["hitl_run_id"]
                   for row in client.get("/api/pipeline/hitl/pending",
                                         params={"persona": "supervisor", "gate": "execution"}).json()["items"])


def test_forward_moves_durable_card_to_target_persona(client):
    data = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-FWD-1",
    }).json()
    run_id = data["recommendation"]["hitl_run_id"]
    response = client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "supervisor", "to_persona": "manager",
    })
    assert response.status_code == 200
    supervisor = client.get("/api/pipeline/hitl/pending", params={
        "persona": "supervisor", "gate": "execution",
    }).json()["items"]
    manager = client.get("/api/pipeline/hitl/pending", params={
        "persona": "manager", "gate": "execution",
    }).json()["items"]
    assert not any(row["run_id"] == run_id for row in supervisor)
    target = next(row for row in manager if row["run_id"] == run_id)
    assert target["persona"] == "manager"
    assert target["hitl_payload"]["requesting_persona"] == "supervisor"
    assert target["hitl_payload"]["recommendation"]["asset_id"] == "AST_PMP_001"


def test_non_authority_requester_gets_read_only_card_and_forwards_to_owner(client):
    data = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "engineer",
        "conversation_id": "DURABLE-REQUESTER-HANDOFF-1",
    }).json()
    rec = data["recommendation"]
    run_id = rec["hitl_run_id"]
    assert rec["hitl_access"]["can_resolve"] is False
    assert rec["hitl_access"]["owner_persona"] == "supervisor"
    forwarded = client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "engineer", "to_persona": "supervisor",
        "escalation_stage_index": 0,
    })
    assert forwarded.status_code == 200
    owner_cards = client.get("/api/pipeline/hitl/pending", params={
        "persona": "supervisor", "gate": "execution",
    }).json()["items"]
    owner_card = next(item for item in owner_cards if item["run_id"] == run_id)
    assert owner_card["persona"] == "supervisor"
    assert owner_card["hitl_payload"]["requesting_persona"] == "engineer"
    assert owner_card["hitl_payload"]["escalation_stage_index"] == 0


def test_escalation_forward_at_deadline_reopens_next_stage_and_blocks_loop(client):
    data = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-ESCALATION-1",
    }).json()
    run_id = data["recommendation"]["hitl_run_id"]
    first = client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "supervisor", "to_persona": "manager",
        "escalation_stage_index": 1,
    })
    assert first.status_code == 200
    manager = client.get("/api/pipeline/hitl/pending", params={
        "persona": "manager", "gate": "execution",
    }).json()["items"]
    card = next(item for item in manager if item["run_id"] == run_id)
    assert card["hitl_payload"]["escalation_stage_index"] == 1
    assert card["expires_at"] > __import__("time").time()

    second = client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "manager", "to_persona": "executive",
        "escalation_stage_index": 2,
    })
    assert second.status_code == 200
    executive = client.get("/api/pipeline/hitl/pending", params={
        "persona": "executive", "gate": "execution",
    }).json()["items"]
    assert any(item["run_id"] == run_id for item in executive)
    loop = client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "executive", "to_persona": "supervisor",
        "escalation_stage_index": 3,
    })
    assert loop.status_code == 422


def test_stale_forward_from_previous_owner_is_rejected(client):
    data = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-STALE-FWD-1",
    }).json()
    run_id = data["recommendation"]["hitl_run_id"]
    assert client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "supervisor", "to_persona": "manager",
        "escalation_stage_index": 1,
    }).status_code == 200
    stale = client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "supervisor", "to_persona": "manager",
        "escalation_stage_index": 1,
    })
    assert stale.status_code == 409


def test_only_current_hitl_owner_can_resolve_forwarded_card(client):
    data = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-OWNER-1",
    }).json()
    run_id = data["recommendation"]["hitl_run_id"]
    client.post("/api/pipeline/hitl/forward", json={
        "run_id": run_id, "gate": "execution",
        "from_persona": "supervisor", "to_persona": "manager",
    })
    denied = client.post("/api/executor/run", json={
        "recommendation": data["recommendation"], "approved": True,
        "persona": "supervisor",
    })
    assert denied.status_code == 403


def test_existing_pending_card_is_idempotent_for_repeated_analysis(client):
    first = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-IDEMPOTENT-1",
    }).json()
    second = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-IDEMPOTENT-1",
    }).json()
    assert second["recommendation"]["hitl_run_id"] == first["recommendation"]["hitl_run_id"]
    pending = client.get("/api/pipeline/hitl/pending", params={
        "persona": "supervisor", "gate": "execution",
    }).json()["items"]
    assert [item["run_id"] for item in pending].count(first["recommendation"]["hitl_run_id"]) == 1


def test_healthy_lifecycle_cancels_obsolete_approval_card(client, monkeypatch):
    first = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-HEALTHY-SUPERSEDES-1",
    }).json()
    run_id = first["recommendation"]["hitl_run_id"]
    monkeypatch.setattr(api, "_asset_lifecycle", lambda _asset: {
        "status": "healthy", "work_order_id": "WO-COMPLETE",
        "work_order_status": "Closed",
    })
    second = client.post("/api/chat", json={
        "message": "Analyse P-207 completely", "persona": "supervisor",
        "conversation_id": "DURABLE-HEALTHY-SUPERSEDES-1",
    }).json()
    assert second["recommendation"]["approval_required"] is False
    assert not any(item["run_id"] == run_id for item in client.get(
        "/api/pipeline/hitl/pending", params={"persona": "supervisor", "gate": "execution"}
    ).json()["items"])


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
    assert "runExecutor(rec, true, persona)" in chat
    assert "rejectRecommendation(rec, rejectCode, reason || '', persona)" in chat
    assert "resolveHITLAdvisory(h.run_id, action, persona)" in chat


def test_hitl_decision_is_persisted_for_follow_up_chat(client):
    conversation_id = "CONV-HITL-AUDIT"
    trusted = type("Trusted", (), {
        "raw": type("Raw", (), {"asset_id": "AST_MTR_001"})(),
    })()
    # Exercise the same persistence helper used by the HITL claim path without
    # constructing an invalid serialized pipeline payload for the repository.
    api._record_hitl_decision(
        {"trusted": trusted, "conversation_id": conversation_id,
         "owner_id": "pytest-admin"},
        "DECISION-1", "diagnosis", "CONFIRM", "engineer",
        "Reviewed the spectral evidence",
    )
    # The durable decision must be queryable by the chat audit path.
    assert api._CHAT_CONTEXT_STORE[conversation_id]["hitl_decisions"][0]["action"] == "CONFIRM"
    data = client.post("/api/chat", json={
        "message": "What action did the Reliability Engineer take on M-104?",
        "persona": "supervisor", "conversation_id": conversation_id,
    }).json()
    assert data["intent"] == "hitl_decision"
    assert "Reliability Engineer" in data["response"]
    assert "CONFIRM" in data["response"]


def test_anomaly_response_exposes_score_and_trigger(client, monkeypatch):
    from src.schemas.anomaly import AnomalyEvent
    anomaly = AnomalyEvent(
        asset_id="AST_PMP_002", bearing_id="BRG_007", anomaly_score=0.61,
        confidence_score=0.73, triggered_features=["vib_rms_mm_s"],
        reason="vibration exceeded its configured baseline threshold",
    )
    state = {"anomaly_event": anomaly, "trusted_signal": None}
    result = api.format_for_persona(state, "supervisor")
    assert "Anomaly detected" in result["headline"]
    assert any("0.61" in row for row in result["details"])
    assert any("vib_rms_mm_s" in row for row in result["details"])


def test_frontend_has_durable_forwarding_and_notification_hydration():
    from pathlib import Path
    chat = Path("frontend/react-app/src/components/ChatView/ChatView.jsx").read_text(encoding="utf-8")
    context = Path("frontend/react-app/src/context/AppContext.jsx").read_text(encoding="utf-8")
    pipeline = Path("frontend/react-app/src/api/pipeline.js").read_text(encoding="utf-8")
    assert "forwardHITL" in chat
    assert "getNotifs" in context and "postPersonaNotification" in context
    assert "/api/pipeline/hitl/forward" in pipeline
    assert "/api/notifications" in pipeline
    assert "escalationStageIndex + 1" in chat
    assert "setResolved(false)" in chat
    assert "pendingRunIds" in context
