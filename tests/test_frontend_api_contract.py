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
