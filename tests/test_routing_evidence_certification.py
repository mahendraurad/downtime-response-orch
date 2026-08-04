"""Certification of minimum-agent routing and evidence-gated chat results."""
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

import src.api.main as api
from src.orchestrator.graph import (
    _sources_executor, _sources_monitoring, _sources_prescriptive,
)
from src.tools.data_loader import load_telemetry_rows


@pytest.fixture
def client():
    api._CHAT_CONTEXT_STORE.clear()
    return TestClient(api.app,raise_server_exceptions=False)


@pytest.mark.parametrize(("message","expected"),[
    ("Show vibration status",["data_foundation"]),
    ("Is this reading anomalous?",["data_foundation","monitoring"]),
    ("Diagnose the fault",["data_foundation","monitoring","failure_intelligence"]),
    ("What is the RUL risk?",[
        "data_foundation","monitoring","failure_intelligence","predictive_risk"]),
    ("Show the applicable SOP",[
        "data_foundation","monitoring","failure_intelligence","predictive_risk","knowledge"]),
    ("Recommend the maintenance action",[
        "data_foundation","monitoring","failure_intelligence","predictive_risk",
        "knowledge","prescriptive"]),
])
def test_chat_calls_exact_minimum_agents_for_evidenced_question(client,message,expected):
    data=client.post("/api/chat",json={
        "message":message,
        "context":{"scenario":"outer_race_fault"},
    }).json()
    assert [row["node"] for row in data["pipeline_log"]]==expected
    assert data["sources"]==expected


@pytest.mark.parametrize(("message","last_agent"),[
    ("Show vibration status for M-104","data_foundation"),
    ("Is M-104 anomalous?","monitoring"),
    ("Diagnose the fault on M-104","failure_intelligence"),
    ("What is the RUL risk for M-104?","predictive_risk"),
    ("Show the SOP for M-104","knowledge"),
    ("Recommend maintenance for M-104","prescriptive"),
    ("Analyse the asset M-104 completely","prescriptive"),
    ("Draft an operations risk briefing: M-104 situation, production exposure, "
     "recommended action and decision needed from leadership","prescriptive"),
])
def test_registered_demo_asset_uses_mapped_evidence(client,message,last_agent):
    data=client.post("/api/chat",json={"message":message}).json()
    assert data["clarification_required"] is False
    assert data["pipeline_log"]
    assert data["pipeline_log"][-1]["node"]==last_agent
    assert data["sources"]


@pytest.mark.parametrize("classification",["conversational","pipeline"])
def test_llm_cannot_replace_registered_asset_pipeline(client,monkeypatch,classification):
    class TemptingLLM:
        @staticmethod
        def is_configured(): return True
        @staticmethod
        def complete_json(**kwargs):
            if "intent_type" in kwargs.get("system_prompt",""):
                return {"intent_type":classification}
            return {"answer":"M-104 has 3 days RUL.","requires_telemetry":False}
    monkeypatch.setattr(api,"_CHAT_LLM",TemptingLLM())
    data=client.post("/api/chat",json={
        "message":"What is the RUL risk for M-104?",
        "conversation_history":[{"role":"user","content":"We discussed M-104."}],
    }).json()
    assert not data["clarification_required"]
    assert [row["node"] for row in data["pipeline_log"]]==[
        "data_foundation","monitoring","failure_intelligence","predictive_risk",
    ]


def test_fleet_ranking_is_not_fabricated_even_when_unaggregated_snapshot_is_named(client):
    data=client.post("/api/chat",json={
        "message":"Which bearing has the highest risk?",
        "context":{
            "timeframe":"this week",
            "fleet_snapshot":{"M-104":{"scenario":"outer_race_fault"}},
        },
    }).json()
    assert data["intent"]=="fleet" and data["clarification_required"]
    assert data["pipeline_log"]==[] and data["sources"]==[]
    assert data["clarification"]["missing_fields"]==["fleet_aggregation_service"]
    assert "No ranking has been fabricated" in data["response"]
    assert "multi_asset_results" not in data


def test_partial_multi_asset_evidence_does_not_infer_missing_asset_results(client):
    data=client.post("/api/chat",json={
        "message":"Plan M-104 and P-207 this week",
        "context":{"fleet_snapshot":{"M-104":{"scenario":"outer_race_fault"}}},
    }).json()
    rows=data["multi_asset_results"]
    assert rows[0]["status"]=="action_required" and rows[0]["pipeline_log"]
    assert rows[1]["status"]=="unavailable" and rows[1]["pipeline_log"]==[]
    assert rows[1]["recommendation"] is None
    assert rows[1]["reason"]=="no fleet evidence was supplied for this asset"
    assert all(log["asset"]=="M-104" for log in data["pipeline_log"])


def test_mismatched_asset_evidence_is_rejected_at_foundation(client):
    wrong_signal=dict(load_telemetry_rows("lubrication_issue")[-1])
    data=client.post("/api/chat",json={
        "message":"Plan M-104 and P-207 this week",
        "context":{"fleet_snapshot":{
            "M-104":{"signal":wrong_signal},
            "P-207":{"scenario":"lubrication_issue"},
        }},
    }).json()
    m104=data["multi_asset_results"][0]
    assert m104["status"]=="data_review" and m104["recommendation"] is None
    assert m104["pipeline_log"]==[]
    assert "identity does not match" in m104["reason"]


def test_concept_definition_uses_cited_non_asset_evidence_only(client):
    data=client.post("/api/chat",json={"message":"What is RUL?"}).json()
    assert data["intent"]=="concept" and data["source_type"]=="approved_rag"
    assert data["pipeline_log"][0]["node"]=="knowledge_rag"
    assert data["sources"]==["knowledge_rag"] and data["citations"]


def test_monitoring_source_uses_canonical_motor_current_field():
    trusted = SimpleNamespace(raw=SimpleNamespace(
        vib_rms_mm_s=None, temp_c=None, motor_current_a=12.4,
    ))
    assert _sources_monitoring(trusted) == "current"
    trusted.raw.motor_current_a = None
    assert "current" not in _sources_monitoring(trusted)


def test_prescriptive_sources_report_only_supplied_evidence():
    unavailable = SimpleNamespace(
        decision_support=SimpleNamespace(cost_data_status="unavailable")
    )
    assert _sources_prescriptive({}, unavailable) == "configured prescriptive rules"
    configured = SimpleNamespace(
        decision_support=SimpleNamespace(cost_data_status="configured_demo")
    )
    sources = _sources_prescriptive({
        "inventory_lookup": {"SKF6310": {"qty_on_hand": 1}},
        "context_lookup": {"planned_stop_windows": [{"window_id": "W1"}]},
    }, configured)
    assert sources == (
        "configured demo cost model · supplied inventory lookup · "
        "supplied maintenance windows"
    )


def test_executor_sources_distinguish_guard_from_mock_side_effects():
    guarded = SimpleNamespace(
        work_order_id="", parts_status=[], notification_status="skipped"
    )
    assert _sources_executor(guarded) == "execution guard only"
    executed = SimpleNamespace(
        work_order_id="WO-1", parts_status=[{"status": "reserved"}],
        notification_status="sent",
    )
    assert _sources_executor(executed) == (
        "mock CMMS · mock parts inventory · mock notification service"
    )
