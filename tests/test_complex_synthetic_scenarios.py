"""Certification matrix for configured, deliberately difficult fleet cases."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.main as api
import src.orchestrator.graph as graph
from src.agents.data_foundation_agent import DataFoundationAgent
from src.api.persona_formatter import format_for_persona
from src.tools.data_loader import (
    list_telemetry_scenarios,
    load_bearing_master,
    load_fleet_scenario_map,
    load_telemetry_rows,
)


ROOT = Path(__file__).resolve().parents[1]
PERSONAS = (
    "supervisor", "engineer", "maintenance", "manager",
    "executive", "ot", "safety",
)


def _payload() -> dict:
    return json.loads(
        (ROOT / "data" / "fleet_scenario_map.json").read_text(encoding="utf-8")
    )


def _spec(name: str) -> dict:
    return next(row for row in _payload()["complex_scenarios"] if row["name"] == name)


def _status(result) -> str:
    value = result.validation_status
    return value.value if hasattr(value, "value") else str(value).split(".")[-1]


@pytest.fixture(scope="module", autouse=True)
def isolated_pipeline(tmp_path_factory):
    """Prevent EWMA history and configured LLM calls from affecting certification."""
    previous = graph._agents
    previous_action_agents = graph._action_agents
    agents = graph._build_agents()
    agents[1]._cfg.ewma_state_file = str(
        tmp_path_factory.mktemp("complex-fleet") / "ewma.json"
    )
    agents[3]._cfg.llm_fallback_enabled = False
    graph._agents = agents
    graph._action_agents = None
    action_agents = graph._get_action_agents()
    action_agents[0]._llm = None
    action_agents[2]._llm = None
    api._CHAT_CONTEXT_STORE.clear()
    try:
        yield
    finally:
        graph._agents = previous
        graph._action_agents = previous_action_agents
        api._CHAT_CONTEXT_STORE.clear()


def test_complex_scenario_config_and_generated_metadata_are_aligned():
    config = json.loads(
        (ROOT / "config" / "synthetic_fleet_config.json").read_text(encoding="utf-8")
    )
    generated = _payload()["complex_scenarios"]
    assert len(config["complex_scenarios"]) == len(generated) == 15
    assert {row["name"] for row in config["complex_scenarios"]} == {
        row["name"] for row in generated
    }
    assert all(row["description"] and row["expected"] for row in generated)


def test_complex_scenarios_do_not_replace_dashboard_current_state_mapping():
    mapping = load_fleet_scenario_map()
    assert len(mapping) == 30
    assert all(not scenario.startswith("complex_") for _, scenario in mapping.values())
    assert {row["display_asset_id"] for row in _payload()["complex_scenarios"]} <= set(mapping)


def test_complex_histories_are_ordered_finite_and_globally_identity_unique():
    telemetry_ids = []
    for spec in _payload()["complex_scenarios"]:
        rows = load_telemetry_rows(spec["name"])
        assert len(rows) == 12
        assert [row["timestamp_utc"] for row in rows] == sorted(
            row["timestamp_utc"] for row in rows
        )
        telemetry_ids.extend(row["telemetry_id"] for row in rows)
        for row in rows:
            for value in row.values():
                if isinstance(value, float):
                    assert math.isfinite(value)
    assert len(telemetry_ids) == len(set(telemetry_ids))


@pytest.mark.parametrize("name", [
    "complex_dual_race_signature",
    "complex_cage_rolling_tie",
    "complex_outer_stage3_boundary",
    "complex_outer_kurtosis_boundary",
    "complex_nde_rolling_fault",
    "complex_running_low_speed_conflict",
    "complex_non_running_fault_signature",
    "complex_complete_low_quality",
    "complex_low_criticality_outer_stage2",
    "complex_high_load_outer_fault",
    "complex_low_load_outer_fault",
    "complex_asset_channel_mismatch",
    "complex_temperature_at_limit",
    "complex_temperature_over_limit",
    "complex_partial_critical_dropout",
])
def test_every_declared_foundation_outcome_matches_agent_1(name):
    expected = _spec(name)["expected"]
    result = DataFoundationAgent.from_data_files().process(
        deepcopy(load_telemetry_rows(name)[-1])
    )
    assert _status(result) == expected["foundation_status"]
    if "next_route" in expected:
        assert result.next_route == expected["next_route"]
    if "ranges_valid" in expected:
        assert result.validation.ranges_valid is expected["ranges_valid"]


@pytest.mark.parametrize(("name", "reason"), [
    ("complex_running_low_speed_conflict", "state and speed disagree"),
    ("complex_complete_low_quality", "signal_quality_score"),
    ("complex_asset_channel_mismatch", "channel_id"),
    ("complex_temperature_over_limit", "temp_c"),
    ("complex_partial_critical_dropout", "bpfo_energy"),
])
def test_quality_and_identity_failures_are_explainable(name, reason):
    result = DataFoundationAgent.from_data_files().process(
        deepcopy(load_telemetry_rows(name)[-1])
    )
    assert reason.lower() in " ".join(result.validation.reasons).lower()
    assert not result.downstream_eligible


@pytest.mark.parametrize(("name", "fault", "stage"), [
    ("complex_dual_race_signature", "outer_race_fault", 2),
    ("complex_cage_rolling_tie", "cage_fault", 3),
    ("complex_outer_stage3_boundary", "outer_race_fault", 3),
    ("complex_outer_kurtosis_boundary", "undetermined", 0),
    ("complex_nde_rolling_fault", "rolling_element_fault", 2),
    ("complex_low_criticality_outer_stage2", "outer_race_fault", 2),
    ("complex_high_load_outer_fault", "outer_race_fault", 2),
    ("complex_low_load_outer_fault", "outer_race_fault", 2),
])
def test_complex_diagnostic_outcomes_are_deterministic(name, fault, stage):
    state = graph.run_pipeline(
        deepcopy(load_telemetry_rows(name)[-1]),
        intent="risk", persona="engineer", run_id=f"COMPLEX-{name}",
    )
    diagnosis = state["fault_diagnosis"]
    assert diagnosis.fault_mode == fault
    assert diagnosis.iso_stage == stage
    assert [row["node"] for row in state["pipeline_log"]] == [
        "data_foundation", "monitoring", "failure_intelligence",
        "predictive_risk",
    ]


@pytest.mark.parametrize(("name", "primary", "differential"), [
    ("complex_dual_race_signature", "outer_race_fault", "inner_race_fault"),
    ("complex_cage_rolling_tie", "cage_fault", "rolling_element_fault"),
])
def test_competing_signatures_keep_primary_and_differential_evidence(
    name, primary, differential,
):
    state = graph.run_pipeline(
        deepcopy(load_telemetry_rows(name)[-1]), intent="diagnosis",
        persona="engineer", run_id=f"DIFF-{name}",
    )
    diagnosis = state["fault_diagnosis"]
    assert diagnosis.fault_mode == primary
    assert differential in {
        row["fault_mode"]
        for row in diagnosis.evidence["differential_diagnoses"]
    }


def test_exact_stage_threshold_is_inclusive_but_kurtosis_gate_is_strict():
    stage = graph.run_pipeline(
        deepcopy(load_telemetry_rows("complex_outer_stage3_boundary")[-1]),
        intent="diagnosis", persona="engineer", run_id="BOUNDARY-STAGE",
    )["fault_diagnosis"]
    kurtosis = graph.run_pipeline(
        deepcopy(load_telemetry_rows("complex_outer_kurtosis_boundary")[-1]),
        intent="diagnosis", persona="engineer", run_id="BOUNDARY-KURTOSIS",
    )["fault_diagnosis"]
    assert stage.fault_mode == "outer_race_fault" and stage.iso_stage == 3
    assert kurtosis.fault_mode == "undetermined" and kurtosis.iso_stage == 0


def test_non_drive_end_case_uses_the_correct_bearing_and_channel_context():
    row = load_telemetry_rows("complex_nde_rolling_fault")[-1]
    bearings, channels = load_bearing_master()
    trusted = DataFoundationAgent.from_data_files().process(deepcopy(row))
    assert trusted.bearing_ctx.bearing_position == "non_drive_end"
    assert row["bearing_id"].endswith("_NDE")
    assert bearings[row["bearing_id"]].channel_id == row["channel_id"]
    assert channels[row["channel_id"]].bearing_id == row["bearing_id"]


@pytest.mark.parametrize(("name", "nodes"), [
    ("complex_running_low_speed_conflict", ["data_foundation"]),
    ("complex_complete_low_quality", ["data_foundation"]),
    ("complex_asset_channel_mismatch", ["data_foundation"]),
    ("complex_temperature_over_limit", ["data_foundation"]),
    ("complex_partial_critical_dropout", ["data_foundation"]),
    ("complex_non_running_fault_signature", ["data_foundation", "monitoring"]),
])
def test_fail_closed_and_suppression_cases_stop_at_the_declared_agent(name, nodes):
    state = graph.run_pipeline(
        deepcopy(load_telemetry_rows(name)[-1]),
        intent="risk", persona="engineer", run_id=f"STOP-{name}",
    )
    assert [row["node"] for row in state["pipeline_log"]] == nodes
    assert state.get("fault_diagnosis") is None
    assert state.get("risk_assessment") is None


@pytest.mark.parametrize(("name", "regime"), [
    ("complex_high_load_outer_fault", "high_load@rated"),
    ("complex_low_load_outer_fault", "low_load@rated"),
])
def test_load_regime_is_preserved_in_anomaly_evidence(name, regime):
    state = graph.run_pipeline(
        deepcopy(load_telemetry_rows(name)[-1]),
        intent="anomaly", persona="engineer", run_id=f"REGIME-{name}",
    )
    assert state["anomaly_event"].regime == regime


def test_identical_stage_fault_is_contextualized_by_asset_criticality():
    high = graph.run_pipeline(
        deepcopy(load_telemetry_rows("complex_high_load_outer_fault")[-1]),
        intent="risk", persona="engineer", run_id="CONTEXT-HIGH",
    )
    low = graph.run_pipeline(
        deepcopy(load_telemetry_rows("complex_low_criticality_outer_stage2")[-1]),
        intent="risk", persona="engineer", run_id="CONTEXT-LOW",
    )
    assert high["fault_diagnosis"].iso_stage == low["fault_diagnosis"].iso_stage == 2
    assert high["fault_diagnosis"].fault_mode == low["fault_diagnosis"].fault_mode
    assert high["fault_diagnosis"].severity == "high"
    assert low["fault_diagnosis"].severity == "medium"
    assert high["risk_assessment"].business_impact_flag is True
    assert low["risk_assessment"].business_impact_flag is False


@pytest.mark.parametrize("persona", PERSONAS)
def test_every_persona_can_format_a_complex_multi_signature_result(persona):
    state = graph.run_pipeline(
        deepcopy(load_telemetry_rows("complex_dual_race_signature")[-1]),
        intent="full", persona=persona, run_id=f"PERSONA-COMPLEX-{persona}",
    )
    response = format_for_persona(state, persona)
    assert response["persona"] == persona
    assert response["headline"]
    assert response["details"]
    assert response["actions"]


def test_scenario_api_exposes_complex_cases_with_useful_metadata():
    response = TestClient(api.app).get("/api/pipeline/scenarios")
    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert len(scenarios) == 55
    complex_rows = [row for row in scenarios if row["name"].startswith("complex_")]
    assert len(complex_rows) == 15
    assert all(row["display_asset_id"] and row["asset_id"] for row in complex_rows)
    assert all("deterministic fleet history" not in row["description"] for row in complex_rows)


@pytest.mark.parametrize(("name", "hitl", "last_node"), [
    ("complex_complete_low_quality", "hitl_required", "data_foundation"),
    ("complex_temperature_over_limit", "hitl_required", "data_foundation"),
    ("complex_partial_critical_dropout", "hitl_required", "data_foundation"),
    ("complex_dual_race_signature", None, "failure_intelligence"),
])
def test_pipeline_api_handles_complex_scenarios_without_fabricating_downstream_results(
    name, hitl, last_node,
):
    response = TestClient(api.app).post("/api/pipeline/run", json={
        "signal": {}, "scenario": name, "query": "diagnose the fault",
        "persona": "engineer",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["pipeline_log"][-1]["node"] == last_node
    if hitl:
        assert data[hitl] is not None
        assert data["fault_diagnosis"] is None
        assert data["risk_assessment"] is None
    else:
        assert data["fault_diagnosis"]["fault_mode"] == "outer_race_fault"


def test_mixed_multi_asset_chat_keeps_complex_results_isolated():
    response = TestClient(api.app).post("/api/chat", json={
        "message": "Plan M-121, F-411, M-126 and P-207 for this week",
        "persona": "supervisor",
        "context": {"fleet_snapshot": {
            "M-121": {"scenario": "complex_dual_race_signature"},
            "F-411": {"scenario": "complex_complete_low_quality"},
            "M-126": {"scenario": "complex_asset_channel_mismatch"},
        }},
    })
    assert response.status_code == 200
    rows = {
        row["display_asset_id"]: row
        for row in response.json()["multi_asset_results"]
    }
    assert rows["M-121"]["status"] == "action_required"
    assert rows["F-411"]["status"] == "data_review"
    assert rows["M-126"]["status"] == "data_review"
    assert rows["P-207"]["status"] == "unavailable"
    assert rows["P-207"]["pipeline_log"] == []
    assert rows["P-207"]["recommendation"] is None


def test_complex_pipeline_does_not_mutate_source_rows():
    row = load_telemetry_rows("complex_dual_race_signature")[-1]
    original = deepcopy(row)
    graph.run_pipeline(row, intent="risk", persona="engineer", run_id="IMMUTABLE-COMPLEX")
    assert row == original
