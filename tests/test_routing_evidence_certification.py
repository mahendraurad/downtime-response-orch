"""Certification of minimum-agent routing and evidence-gated chat results."""
import pytest
from fastapi.testclient import TestClient

import src.api.main as api
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


@pytest.mark.parametrize("message",[
    "Show vibration status for M-104",
    "Is M-104 anomalous?",
    "Diagnose the fault on M-104",
    "What is the RUL risk for M-104?",
    "Show the SOP for M-104",
    "Recommend maintenance for M-104",
    "Execute maintenance for M-104",
])
def test_named_asset_without_evidence_never_runs_agents_or_returns_result(client,message):
    data=client.post("/api/chat",json={"message":message}).json()
    assert data["clarification_required"] is True
    assert data["pipeline_log"]==[] and data["sources"]==[]
    assert data["agent_outputs"]=={"recommendation":None,"execution_result":None}
    assert data["clarification"]["missing_fields"]==["telemetry_or_scenario"]


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


def test_concept_definition_uses_controlled_non_asset_evidence_only(client):
    data=client.post("/api/chat",json={"message":"What is RUL?"}).json()
    assert data["intent"]=="concept" and data["source_type"]=="controlled_glossary"
    assert data["pipeline_log"]==[] and data["sources"]==[]
