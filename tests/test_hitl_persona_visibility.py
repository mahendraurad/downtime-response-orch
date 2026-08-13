"""HITL cards expose backend-configured action rights without hiding evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.main as api


ROOT = Path(__file__).resolve().parents[1]
PERSONAS = (
    "supervisor", "engineer", "maintenance", "manager",
    "executive", "ot", "safety",
)
GATES = (
    "remediation", "monitoring", "diagnosis", "advisory",
    "knowledge", "execution",
)


@pytest.fixture
def client(tmp_path):
    from src.tools.hitl_repository import SQLiteHITLRepository

    previous = api._HITL_REPOSITORY
    api._HITL_REPOSITORY = SQLiteHITLRepository(tmp_path / "visibility.db")
    api._HITL_STORE.clear()
    try:
        yield TestClient(api.app, raise_server_exceptions=False)
    finally:
        api._HITL_STORE.clear()
        api._HITL_REPOSITORY = previous


def _configured_permissions() -> dict:
    payload = json.loads(
        (ROOT / "config" / "orchestrator_config.json").read_text(
            encoding="utf-8"
        )
    )
    return payload["hitl"]["permissions"]


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("persona", PERSONAS)
def test_access_contract_exactly_matches_backend_configuration(gate, persona):
    configured = _configured_permissions()[gate]
    access = api._hitl_access(gate, persona)
    assert access["gate"] == gate
    assert access["selected_persona"] == persona
    assert access["allowed_personas"] == configured
    assert len(access["allowed_persona_names"]) == len(configured)
    assert access["can_resolve"] is (persona in configured)
    assert access["mode"] == (
        "actionable" if persona in configured else "read_only"
    )


@pytest.mark.parametrize("persona", PERSONAS)
def test_pipeline_gate_keeps_evidence_visible_but_marks_actionability(
    client, persona,
):
    response = client.post("/api/pipeline/run", json={
        "signal": {},
        "scenario": "signal_dropout",
        "query": "Analyse the asset completely",
        "persona": persona,
    })
    assert response.status_code == 200
    gate = response.json()["hitl_required"]
    assert gate["type"] == "remediation"
    assert "missing_fields" in gate
    assert "quality_score" in gate
    assert gate["access"]["can_resolve"] is (
        persona in _configured_permissions()["remediation"]
    )


@pytest.mark.parametrize("persona", PERSONAS)
def test_executor_recommendation_carries_configured_action_rights(client, persona):
    response = client.post("/api/pipeline/run", json={
        "signal": {},
        "scenario": "outer_race_fault",
        "query": "Recommend the complete maintenance action",
        "persona": persona,
    })
    assert response.status_code == 200
    recommendation = response.json()["recommendation"]
    assert recommendation["recommendation_status"] == "ok"
    access = recommendation["hitl_access"]
    assert access["gate"] == "execution"
    assert access["can_resolve"] is (
        persona in _configured_permissions()["execution"]
    )


def test_frontend_all_hitl_cards_have_config_driven_read_only_rendering():
    chat = (
        ROOT / "frontend" / "react-app" / "src" / "components"
        / "ChatView" / "ChatView.jsx"
    ).read_text(encoding="utf-8")
    assert "function canResolveHITL(access, persona)" in chat
    assert "function HITLReadOnlyNotice({ access })" in chat
    assert "Read-only for this persona." in chat
    assert chat.count("<HITLReadOnlyNotice access=") == 6
    assert chat.count("!resolved && canResolve") == 5
    assert "!decision && canResolve" in chat
    assert "rec.hitl_access" in chat
