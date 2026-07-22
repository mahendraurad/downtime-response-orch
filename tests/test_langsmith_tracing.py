"""LangSmith instrumentation contract; these tests never send network traces."""
from pathlib import Path

from fastapi.testclient import TestClient

from src.agents.reflexion_agent import ReflexionAgent
from src.api.main import app, chat
from src.orchestrator import graph


def _traceable(function, expected_tag):
    assert function.__langsmith_traceable__
    assert hasattr(function, "__wrapped__")
    assert "dro" in function.__traceable_config__["tags"]
    assert expected_tag in function.__traceable_config__["tags"]


def test_chat_orchestrator_agents_and_reflexion_are_traceable(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    _traceable(chat, "chat-api")
    _traceable(graph.run_pipeline, "orchestrator")
    for number, function in enumerate((
        graph.node_data_foundation,
        graph.node_monitoring,
        graph.node_failure_intelligence,
        graph.node_predictive_risk,
        graph.node_knowledge,
        graph.node_prescriptive,
        graph.node_executor,
        graph.node_learning,
    ), start=1):
        _traceable(function, f"agent-{number}")
    _traceable(ReflexionAgent.process, "reflexion")


def test_tracing_disabled_does_not_change_chat_contract(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    response = TestClient(app).post("/api/chat", json={"message": "What is RUL?"})
    assert response.status_code == 200
    assert response.json()["intent"] == "concept"


def test_example_configuration_contains_no_langsmith_secret():
    example = Path(".env.example").read_text(encoding="utf-8")
    assert "LANGSMITH_TRACING=false" in example
    assert "LANGSMITH_PROJECT=DRO" in example
    assert "LANGSMITH_API_KEY=\n" in example.replace("\r\n", "\n")
