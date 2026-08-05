"""Compatibility contract for the React client on feature/8agents_frontend."""
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.tools.notification_mock_service import PERSONAS, clear_inbox, send_notifications


@pytest.fixture
def client():
    clear_inbox()
    return TestClient(app, raise_server_exceptions=False)


def test_swagger_and_openapi_surfaces_are_available(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/chat" in paths
    assert "/api/pipeline/run" in paths
    assert "/api/recommendations/reject" in paths
    assert "/api/dashboard/assets" in paths


def test_pipeline_response_has_every_field_read_by_chat_view(client):
    response = client.post("/api/pipeline/run", json={
        "signal": {}, "scenario": "outer_race_fault", "persona": "supervisor"
    })
    assert response.status_code == 200
    assert {
        "run_id", "headline", "details", "actions", "tags", "pipeline_log",
        "recommendation", "hitl_required", "hitl_advisory", "hitl_monitoring",
        "hitl_diagnosis", "hitl_knowledge",
    } <= response.json().keys()


def test_notification_counts_cover_all_frontend_personas(client):
    data = client.get("/api/notifications/counts").json()
    assert set(data) == set(PERSONAS)
    assert all(count == 0 for count in data.values())


def test_notification_list_and_mark_read_contract(client):
    send_notifications({
        "urgency": "planned", "asset_id": "M-104",
        "recommended_action": {"name": "inspect_bearing"},
    }, "WO-100")
    listing = client.get("/api/notifications/supervisor").json()
    assert listing["persona_id"] == "supervisor"
    assert listing["count"] == 1
    assert listing["notifications"][0]["read"] is False

    marked = client.post("/api/notifications/supervisor/read")
    assert marked.status_code == 200
    assert client.get("/api/notifications/counts").json()["supervisor"] == 0


def test_unknown_persona_is_graceful(client):
    assert client.get("/api/notifications/not-a-persona").status_code == 404
    assert client.post("/api/notifications/not-a-persona/read").status_code == 404


def test_dashboard_asset_contract_is_backed_by_agent_pipeline(client):
    response = TestClient(app).get("/api/dashboard/assets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "backend_agent_pipeline"
    assert payload["fleet_total"] == len(payload["assets"])
    assert payload["fleet_total"] >= 3
    required = {
        "id", "asset_id", "name", "status", "rul", "vibration",
        "temperature", "validation_status", "pipeline_log", "source",
    }
    assert all(required <= row.keys() for row in payload["assets"])
    assert all(row["pipeline_log"] for row in payload["assets"])


def test_frontend_uses_backend_contracts_for_group_a():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "frontend" / "react-app" / "src"
    app_context = (root / "context" / "AppContext.jsx").read_text(encoding="utf-8")
    chat = (root / "components" / "ChatView" / "ChatView.jsx").read_text(encoding="utf-8")
    assert "fetchDashboardAssets" in app_context
    assert "fetchWorkOrders" in app_context
    assert "activeEscalationStep" in app_context
    assert "Object.entries(previous)" in app_context
    assert "escalationTriggered" in app_context
    assert "data.execution_steps" in chat
    assert "rejectRecommendation" in chat
    assert "type=\"radio\"" in chat
    assert "decision_support" in chat
    assert "approval_escalation" in chat
    assert "approval escalation countdown" in chat
    assert "escalationStageIndex" in chat
    assert "target: step.to_persona_id" in app_context
    assert "transferId" in app_context
    assert "FOR: Plant Supervisor" not in chat
    assert "buildExecutionSteps" not in chat


def test_pipeline_approval_contract_includes_timed_persona_escalation(client):
    response = client.post("/api/pipeline/run", json={
        "signal": {}, "scenario": "outer_race_fault", "persona": "supervisor"
    })
    assert response.status_code == 200
    recommendation = response.json()["recommendation"]
    escalation = recommendation["decision_support"]["approval_escalation"]
    assert escalation["status"] == "active"
    assert escalation["current_persona_id"] == "supervisor"
    assert [step["to_persona_id"] for step in escalation["steps"]] == [
        "manager", "executive",
    ]
    assert all(step["escalates_at_utc"] for step in escalation["steps"])
    assert all(step["timeout_seconds"] > 0 for step in escalation["steps"])
