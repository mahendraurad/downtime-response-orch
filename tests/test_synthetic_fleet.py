"""Expanded synthetic fleet integrity, behavior, and API certification."""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.agents.data_foundation_agent import DataFoundationAgent
from src.api.persona_formatter import format_for_persona
from src.orchestrator.graph import run_pipeline
from src.tools.data_loader import (
    list_telemetry_scenarios,
    load_asset_master,
    load_bearing_master,
    load_demo_work_orders,
    load_fleet_scenario_map,
    load_telemetry_rows,
)
from src.tools.persona_formatter import build_persona_context


ROOT = Path(__file__).resolve().parents[1]
PERSONAS = (
    "supervisor", "engineer", "maintenance", "manager",
    "executive", "ot", "safety",
)


@pytest.fixture(scope="module", autouse=True)
def isolated_monitoring_state(tmp_path_factory):
    """Keep fleet certification independent of prior demo/API EWMA history."""
    import src.orchestrator.graph as graph

    previous_agents = graph._agents
    agents = graph._build_agents()
    agents[1]._cfg.ewma_state_file = str(
        tmp_path_factory.mktemp("synthetic-fleet") / "ewma.json"
    )
    graph._agents = agents
    try:
        yield
    finally:
        graph._agents = previous_agents


def _fleet_payload():
    return json.loads(
        (ROOT / "data" / "fleet_scenario_map.json").read_text(encoding="utf-8")
    )


def test_expanded_fleet_has_expected_scale_and_asset_class_mix():
    assets = load_asset_master()
    types = Counter(asset.asset_type for asset in assets.values())
    assert len(assets) == 30
    assert types == {
        "motor": 8, "pump": 8, "conveyor": 5,
        "gearbox": 5, "fan": 3, "compressor": 1,
    }
    assert sum(asset.is_bottleneck for asset in assets.values()) >= 8
    assert {asset.criticality for asset in assets.values()} == {
        "low", "medium", "high",
    }


def test_every_asset_has_two_unique_bearings_and_channels():
    assets = load_asset_master()
    bearings, channels = load_bearing_master()
    assert len(bearings) == 60
    assert len(channels) == 60
    counts = Counter(row.asset_id for row in bearings.values())
    assert set(counts) == set(assets)
    assert set(counts.values()) == {2}
    assert all(row.channel_id in channels for row in bearings.values())


def test_every_dashboard_asset_maps_to_identity_correct_telemetry():
    assets = load_asset_master()
    bearings, channels = load_bearing_master()
    mapping = load_fleet_scenario_map()
    assert len(mapping) == len(assets) == 30
    for display, (asset_id, scenario) in mapping.items():
        assert display
        assert asset_id in assets
        rows = load_telemetry_rows(scenario)
        assert rows
        assert all(row["asset_id"] == asset_id for row in rows)
        assert all(row["bearing_id"] in bearings for row in rows)
        assert all(row["channel_id"] in channels for row in rows)
        assert all(
            bearings[row["bearing_id"]].asset_id == asset_id
            for row in rows
        )


def test_generated_histories_are_deterministic_and_twelve_points_long():
    catalog = list_telemetry_scenarios()
    generated = [row for row in catalog if row["name"].startswith("fleet_")]
    assert len(generated) == 25  # 24 new assets + G-112 post-repair history
    assert all(row["record_count"] == 12 for row in generated)
    telemetry_ids = []
    for row in generated:
        history = load_telemetry_rows(row["name"])
        timestamps = [item["timestamp_utc"] for item in history]
        assert timestamps == sorted(timestamps)
        telemetry_ids.extend(item["telemetry_id"] for item in history)
    assert len(telemetry_ids) == len(set(telemetry_ids))


def test_profile_mix_covers_fault_health_regime_and_data_quality_cases():
    profiles = {row["profile"] for row in _fleet_payload()["assets"]}
    assert {
        "healthy", "outer_stage1", "outer_stage2", "outer_stage3",
        "inner_stage1", "inner_stage2", "lubrication_stage1",
        "lubrication_stage2", "lubrication_stage3", "cage_stage2",
        "cage_stage3", "rolling_stage1", "rolling_stage2",
        "rolling_stage3", "thermal", "startup", "electrical",
        "load_spike", "signal_dropout", "borderline",
    } <= profiles


def test_every_latest_fleet_record_passes_identity_gate_or_intended_review():
    agent = DataFoundationAgent.from_data_files()
    statuses = {}
    for display, (_, scenario) in load_fleet_scenario_map().items():
        result = agent.process(deepcopy(load_telemetry_rows(scenario)[-1]))
        status = str(result.validation_status).split(".")[-1]
        assert status != "REJECTED", (display, result.validation.reasons)
        statuses[display] = status
    assert {display for display, status in statuses.items() if status == "FLAGGED"} == {
        "C-301", "C-317",
    }
    assert Counter(statuses.values()) == {"VALID": 28, "FLAGGED": 2}


@pytest.mark.parametrize(("display", "fault", "stage"), [
    ("M-121", "outer_race_fault", 2),
    ("M-123", "inner_race_fault", 1),
    ("M-124", "lubrication_issue", 2),
    ("P-220", "rolling_element_fault", 2),
    ("P-222", "lubrication_issue", 3),
    ("P-224", "outer_race_fault", 1),
    ("C-315", "cage_fault", 2),
    ("C-318", "rolling_element_fault", 1),
    ("G-120", "outer_race_fault", 3),
    ("G-121", "cage_fault", 3),
    ("G-123", "lubrication_issue", 1),
    ("F-411", "inner_race_fault", 2),
    ("K-501", "rolling_element_fault", 3),
])
def test_fault_profiles_reach_expected_diagnosis_and_stage(display, fault, stage):
    _, scenario = load_fleet_scenario_map()[display]
    state = run_pipeline(
        deepcopy(load_telemetry_rows(scenario)[-1]),
        intent="risk", persona="engineer",
        run_id=f"SYN-{display.replace('-', '')}",
    )
    diagnosis = state["fault_diagnosis"]
    assert diagnosis.fault_mode == fault
    assert diagnosis.iso_stage == stage
    assert state.get("risk_assessment") is not None
    assert [row["node"] for row in state["pipeline_log"]] == [
        "data_foundation", "monitoring", "failure_intelligence",
        "predictive_risk",
    ]


@pytest.mark.parametrize("display", [
    "M-089", "G-112", "M-122", "P-221", "C-316", "G-122", "F-412",
    "M-125", "M-126", "F-410",
])
def test_healthy_and_suppressed_profiles_do_not_fabricate_diagnosis(display):
    _, scenario = load_fleet_scenario_map()[display]
    state = run_pipeline(
        deepcopy(load_telemetry_rows(scenario)[-1]),
        intent="risk", persona="engineer",
        run_id=f"SYN-SAFE-{display.replace('-', '')}",
    )
    assert state.get("fault_diagnosis") is None
    assert state.get("risk_assessment") is None
    assert [row["node"] for row in state["pipeline_log"]] == [
        "data_foundation", "monitoring",
    ]


@pytest.mark.parametrize("persona", PERSONAS)
def test_all_personas_can_address_every_registered_display_asset(persona):
    import src.api.main as api

    context = build_persona_context(persona)
    assert context.id == persona
    for display, (asset_id, scenario) in load_fleet_scenario_map().items():
        matches = api._assets_from_message(f"Assess {display}")
        assert matches == [(display, asset_id, scenario)]


@pytest.fixture(scope="module")
def representative_full_state():
    _, scenario = load_fleet_scenario_map()["M-121"]
    return run_pipeline(
        deepcopy(load_telemetry_rows(scenario)[-1]),
        intent="full", persona="supervisor", run_id="SYN-PERSONA-FORMAT",
    )


@pytest.mark.parametrize("persona", PERSONAS)
def test_all_persona_formatters_accept_expanded_asset_state(
    persona, representative_full_state,
):
    response = format_for_persona(representative_full_state, persona)
    assert response["persona"] == persona
    assert response["headline"]
    assert isinstance(response["details"], list)
    assert isinstance(response["actions"], list)


def test_scenario_api_exposes_full_catalog():
    from src.api.main import app

    response = TestClient(app).get("/api/pipeline/scenarios")
    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert len(scenarios) == 40
    assert len({row["name"] for row in scenarios}) == 40
    assert all({"name", "description", "record_count"} <= row.keys()
               for row in scenarios)


def test_dashboard_api_projects_all_assets_with_varied_statuses():
    from src.api.main import app

    response = TestClient(app).get("/api/dashboard/assets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["fleet_total"] == 30
    assert len(payload["assets"]) == 30
    statuses = Counter(row["status"] for row in payload["assets"])
    assert statuses == {"healthy": 15, "critical": 10, "warning": 5}
    assert all(row["pipeline_log"] for row in payload["assets"])


def test_demo_work_orders_cover_registered_assets_and_multiple_priorities():
    from src.api.main import app

    configured = load_demo_work_orders()
    assets = load_asset_master()
    assert len(configured) == 18
    assert all(row["asset_id"] in assets for row in configured)
    assert {row["priority"] for row in configured} == {
        "CRITICAL", "HIGH", "MEDIUM",
    }
    response = TestClient(app).get("/api/workorders")
    assert response.status_code == 200
    rows = response.json()["workorders"]
    assert {"WO-2024-1847", "WO-DEMO-0207", "WO-DEMO-0301"} <= {
        row["id"] for row in rows
    }


def test_websocket_baselines_cover_the_entire_fleet():
    import src.api.main as api

    assert set(api._SENSOR_BASELINES) == set(load_fleet_scenario_map())
    assert all(
        {"vib", "temp", "bpfo", "rpm"} == set(values)
        for values in api._SENSOR_BASELINES.values()
    )


def test_frontend_fleet_counts_are_resolved_from_backend_assets():
    personas = (ROOT / "frontend" / "react-app" / "src" / "data" / "personas.js").read_text(
        encoding="utf-8"
    )
    chat = (
        ROOT / "frontend" / "react-app" / "src" / "components" / "ChatView" / "ChatView.jsx"
    ).read_text(encoding="utf-8")
    assert "import { ASSETS }" not in personas
    assert "export function resolvePersonaData" in personas
    assert "assets?.length" in chat
    assert "resolvePersonaData(persona, fleetSummary, workOrders)" in chat
    assert "FLEET_TOTAL" not in chat


def test_persona_tasks_are_projected_from_backend_work_orders_without_demo_literals():
    personas = (ROOT / "frontend" / "react-app" / "src" / "data" / "personas.js").read_text(
        encoding="utf-8"
    )
    chat = (
        ROOT / "frontend" / "react-app" / "src" / "components" / "ChatView" / "ChatView.jsx"
    ).read_text(encoding="utf-8")
    assert "workOrders.map(normaliseWorkOrder)" in personas
    assert "TERMINAL_STATUSES" in personas
    assert "workOrderId: workOrder.id" in personas
    assert "current backend record" in personas
    assert "workOrders={workOrders}" in chat
    assert "resolvePersonaData(persona, fleetSummary, workOrders)" in chat
    for literal in (
        "M-104", "P-207", "C-301", "WO-2024-1847", "Wednesday 06:00",
        "SKF 6310-2RS", "$601K",
    ):
        assert literal not in personas


def test_work_order_seed_supports_all_persona_task_evidence_categories():
    rows = load_demo_work_orders()
    active = [
        row for row in rows
        if row["status"].lower() not in {"closed", "completed", "cancelled", "rejected"}
    ]
    assert active
    assert all(
        {"id", "asset_display", "title", "priority", "status", "due"} <= row.keys()
        for row in active
    )
    assert any(row["priority"] in {"CRITICAL", "HIGH"} for row in active)
    assert any("condition" in row["type"].lower() for row in active)
    assert any("instrument" in row["type"].lower() for row in active)
    checklist_text = " ".join(
        f"{item.get('tag', '')} {item.get('text', '')}"
        for row in active for item in row.get("checklist", [])
    ).lower()
    assert "safety" in checklist_text or "loto" in checklist_text
    assert any(token in checklist_text for token in ("ot", "sensor", "signal", "qa", "evidence"))
