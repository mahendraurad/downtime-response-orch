"""Work-order rejection closes the loop with Agent 8 memory."""
import pytest
from fastapi.testclient import TestClient

import src.api.main as api
from src.api.auth import get_current_user
from src.schemas.feedback import RejectionLearningResult
from src.tools.work_order_repository import InMemoryWorkOrderRepository


class SpyLearningAgent:
    def __init__(self):
        self.feedback = []

    def record_rejection(self, feedback):
        self.feedback.append(feedback)
        return RejectionLearningResult(
            case_id=feedback.case_id,
            status="recorded",
            valid_closure=True,
            closure_status="rejected",
            learning_recorded=True,
        )


@pytest.fixture
def work_order_context(monkeypatch):
    repository = InMemoryWorkOrderRepository()
    learner = SpyLearningAgent()
    monkeypatch.setattr(api, "_WORKORDER_REPOSITORY", repository)
    monkeypatch.setattr(api, "_learning_agent", lambda: learner)
    api.app.dependency_overrides[get_current_user] = lambda: {
        "sub": "pytest-work-order",
        "role": "admin",
        "display_name": "Work Order Test",
        "allowed_personas": ["supervisor", "manager", "executive", "maintenance"],
    }
    yield repository, learner
    api.app.dependency_overrides.pop(get_current_user, None)


def test_work_order_rejection_records_agent8_learning(work_order_context):
    repository, learner = work_order_context
    repository.upsert({
        "id": "WO-AGENT8-1",
        "asset_id": "AST_MTR_001",
        "asset_display": "M-104",
        "case_id": "CASE-M104-1",
        "fault_mode": "outer_race_fault",
        "status": "Pending",
    })

    response = TestClient(api.app).patch("/api/workorders/WO-AGENT8-1", json={
        "status": "Rejected",
        "updated_by": "Plant Supervisor",
        "persona": "supervisor",
        "reason_code": "second_opinion",
        "free_text": "Reliability Engineer review required before stopping the line.",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "Rejected"
    assert payload["rejection_reason_code"] == "second_opinion"
    assert payload["agent8_learning"]["learning_recorded"] is True
    assert payload["agent8_learning"]["valid_closure"] is True
    assert len(learner.feedback) == 1
    feedback = learner.feedback[0]
    assert feedback.case_id == "CASE-M104-1"
    assert feedback.asset_id == "AST_MTR_001"
    assert feedback.fault_mode == "outer_race_fault"
    assert feedback.persona_id == "supervisor"


def test_repeated_work_order_rejection_is_idempotent(work_order_context):
    repository, learner = work_order_context
    repository.upsert({
        "id": "WO-AGENT8-2", "asset_id": "AST_MTR_001",
        "case_id": "CASE-M104-2", "fault_mode": "outer_race_fault",
        "status": "Pending",
    })
    client = TestClient(api.app)
    body = {
        "status": "Rejected", "updated_by": "Plant Supervisor",
        "persona": "supervisor", "reason_code": "wrong_window",
    }
    assert client.patch("/api/workorders/WO-AGENT8-2", json=body).status_code == 200
    assert client.patch("/api/workorders/WO-AGENT8-2", json=body).status_code == 200
    assert len(learner.feedback) == 1
    assert repository.get("WO-AGENT8-2")["status"] == "Rejected"


def test_legacy_work_order_without_case_metadata_still_records_audit(work_order_context):
    repository, learner = work_order_context
    repository.upsert({
        "id": "WO-LEGACY-1", "asset_id": "AST_MTR_002", "status": "Pending",
    })
    response = TestClient(api.app).patch("/api/workorders/WO-LEGACY-1", json={
        "status": "Rejected", "persona": "manager",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent8_learning"]["learning_recorded"] is True
    assert learner.feedback[0].case_id == "WO-LEGACY-1"
    assert learner.feedback[0].fault_mode == "work_order_action"
    assert payload["rejection_reason"]


def test_rejection_learning_failure_does_not_lose_work_order_decision(work_order_context, monkeypatch):
    repository, _ = work_order_context
    repository.upsert({
        "id": "WO-AGENT8-3", "asset_id": "AST_MTR_003", "status": "Pending",
    })

    class BrokenAgent:
        def record_rejection(self, feedback):
            raise RuntimeError("memory store unavailable")

    monkeypatch.setattr(api, "_learning_agent", lambda: BrokenAgent())
    response = TestClient(api.app).patch("/api/workorders/WO-AGENT8-3", json={
        "status": "Rejected", "persona": "supervisor",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "Rejected"
    assert payload["agent8_learning"]["persistence_status"] == "failed"
    assert repository.get("WO-AGENT8-3")["status"] == "Rejected"
