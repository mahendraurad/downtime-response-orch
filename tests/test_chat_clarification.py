"""Structured clarification and bounded multi-turn Chat API behavior."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
import src.api.main as api
from src.tools.data_loader import load_telemetry_rows


@pytest.fixture
def client():
    api._CHAT_CONTEXT_STORE.clear()
    return TestClient(api.app,raise_server_exceptions=False)


@pytest.mark.parametrize("question",[
    "What is RUL?", "What does RUL mean?", "What does remaining useful life mean?"
])
def test_conceptual_rul_needs_no_asset_or_agents(client,question):
    data=client.post("/api/chat",json={"message":question}).json()
    assert data["intent"]=="concept" and data["clarification_required"] is False
    assert data["needs_context"] is False
    assert data["pipeline_log"][0]["node"]=="knowledge_rag"
    assert "Remaining Useful Life" in data["response"] and data["source_type"]=="approved_rag"
    assert data["citations"]


def test_conceptual_signal_question_needs_no_asset(client):
    data=client.post("/api/chat",json={"message":"Explain why vibration and temperature matter for bearing health."}).json()
    assert data["intent"]=="concept" and "asset-specific baseline" in data["response"]


def test_bpfi_bpfo_comparison_explains_both_without_asset(client):
    data=client.post("/api/chat",json={"message":"How is BPFI different from BPFO?"}).json()
    assert data["intent"]=="concept" and not data["clarification_required"]
    assert "inner race" in data["response"] and "outer race" in data["response"]


def test_asset_specific_rul_requests_asset_and_telemetry(client):
    data=client.post("/api/chat",json={"message":"What is the RUL for this bearing?"}).json()
    assert data["intent"]=="risk" and data["clarification_required"]
    assert data["clarification"]["missing_fields"]==["asset_id","telemetry_or_scenario"]
    assert data["pipeline_log"]==[] and data["conversation_id"].startswith("CONV-")


def test_follow_up_registered_demo_asset_completes_pending_rul_question(client):
    first=client.post("/api/chat",json={"message":"What is the RUL for this bearing?"}).json()
    second=client.post("/api/chat",json={"message":"The asset is AST_MTR_001","conversation_id":first["conversation_id"]}).json()
    assert second["intent"]=="risk" and not second["clarification_required"]
    assert [row["node"] for row in second["pipeline_log"]]==[
        "data_foundation","monitoring","failure_intelligence","predictive_risk",
    ]


def test_follow_up_scenario_completes_pending_rul_pipeline(client):
    first=client.post("/api/chat",json={"message":"What is the RUL for this bearing?"}).json()
    second=client.post("/api/chat",json={"message":"Use this validated scenario","conversation_id":first["conversation_id"],"context":{"scenario":"outer_race_fault"}}).json()
    assert second["intent"]=="risk" and second["clarification_required"] is False
    assert [row["node"] for row in second["pipeline_log"]]==["data_foundation","monitoring","failure_intelligence","predictive_risk"]


def test_unknown_asset_requests_registration_without_agents(client):
    data=client.post("/api/chat",json={"message":"What is the RUL for AST_NOT_REGISTERED?"}).json()
    assert data["intent"]=="risk" and data["pipeline_log"]==[]
    assert data["clarification"]["missing_fields"]==["asset_registration"]
    assert "not registered" in data["response"]


def test_incomplete_signal_requests_exact_identity_fields(client):
    signal={"asset_id":"AST_MTR_001","vib_rms_mm_s":4.2}
    data=client.post("/api/chat",json={"message":"Diagnose this reading","context":{"signal":signal}}).json()
    assert data["pipeline_log"]==[] and data["clarification_required"]
    assert data["clarification"]["missing_fields"]==["bearing_id","channel_id","telemetry_id","timestamp_utc"]


def test_fleet_question_requests_fleet_scope_not_asset(client):
    data=client.post("/api/chat",json={"message":"Which bearing has the highest risk?"}).json()
    assert data["intent"]=="fleet" and data["pipeline_log"]==[]
    assert data["clarification"]["missing_fields"]==["timeframe","fleet_snapshot"]
    assert "asset_id" not in data["clarification"]["missing_fields"]


def test_general_question_requests_objective_and_timeframe(client):
    data=client.post("/api/chat",json={"message":"How can we improve reliability across our operations?"}).json()
    assert not data["clarification_required"]
    assert data["source_type"]=="approved_rag" and data["citations"]
    assert data["pipeline_log"][0]["node"]=="knowledge_rag"


def test_complete_unseen_signal_still_runs_requested_depth(client):
    signal=deepcopy(load_telemetry_rows("outer_race_fault")[-1]); signal["telemetry_id"]="CLARIFY-COMPLETE-1"
    data=client.post("/api/chat",json={"message":"What is the RUL for this bearing?","context":{"signal":signal}}).json()
    assert data["clarification_required"] is False
    assert data["pipeline_log"][-1]["node"]=="predictive_risk"


def test_context_store_is_bounded(client):
    original=api._MAX_CHAT_CONTEXTS; api._MAX_CHAT_CONTEXTS=3
    try:
        ids=[client.post("/api/chat",json={"message":"What is the RUL for this bearing?"}).json()["conversation_id"] for _ in range(5)]
        assert len(api._CHAT_CONTEXT_STORE)==3 and ids[0] not in api._CHAT_CONTEXT_STORE
    finally:
        api._MAX_CHAT_CONTEXTS=original; api._CHAT_CONTEXT_STORE.clear()
