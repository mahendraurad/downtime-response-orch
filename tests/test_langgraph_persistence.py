"""LangGraph thread identity, checkpoint resume, pooling, and lifecycle tests."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

import src.orchestrator.graph as graph
from src.tools.checkpointing import (
    CheckpointResources, build_checkpoint_resources, build_checkpoint_serializer,
)
from src.tools.data_loader import load_telemetry_rows


def _row(name="outer_race_fault"):
    return deepcopy(load_telemetry_rows(name)[-1])


@pytest.fixture()
def checkpointed_graph(monkeypatch):
    saver = InMemorySaver(serde=build_checkpoint_serializer())
    compiled = graph._build_graph(saver)
    monkeypatch.setattr(graph, "_graph", compiled)
    return compiled


def test_pending_recommendation_carries_checkpoint_coordinates(checkpointed_graph):
    state = graph.run_pipeline(
        _row(), run_id="RUN-1", thread_id="CONV-1", intent="full"
    )
    recommendation = state["recommendation"]
    assert recommendation.checkpoint_thread_id == "CONV-1"
    assert recommendation.checkpoint_namespace == "RUN-1"
    snapshot = graph.get_pipeline_checkpoint("CONV-1", "RUN-1")
    assert snapshot.values["run_id"] == "RUN-1"
    assert snapshot.values["approval_status"] == "pending"


def test_approval_resumes_at_executor_without_replaying_agents(checkpointed_graph):
    pending = graph.run_pipeline(
        _row(), run_id="RUN-2", thread_id="CONV-2", intent="full"
    )
    pending_nodes = [row["node"] for row in pending["pipeline_log"]]
    resumed = graph.resume_pipeline("CONV-2", "RUN-2", "approved")
    resumed_nodes = [row["node"] for row in resumed["pipeline_log"]]
    assert pending_nodes == [
        "data_foundation", "monitoring", "failure_intelligence",
        "predictive_risk", "knowledge", "prescriptive",
    ]
    assert resumed_nodes == pending_nodes + ["executor"]
    assert resumed["execution_result"].status in {"success", "partial"}

    replay = graph.resume_pipeline("CONV-2", "RUN-2", "approved")
    assert [row["node"] for row in replay["pipeline_log"]] == resumed_nodes


def test_rejection_does_not_execute_side_effect_node(checkpointed_graph):
    graph.run_pipeline(
        _row(), run_id="RUN-3", thread_id="CONV-3", intent="full"
    )
    rejected = graph.resume_pipeline("CONV-3", "RUN-3", "rejected")
    assert rejected["approval_status"] == "rejected"
    assert rejected.get("execution_result") is None
    assert "executor" not in [row["node"] for row in rejected["pipeline_log"]]


def test_namespaces_isolate_runs_under_one_logical_conversation(checkpointed_graph):
    first = graph.run_pipeline(
        _row(), run_id="RUN-A", thread_id="CONV-SHARED", intent="status"
    )
    second = graph.run_pipeline(
        _row(), run_id="RUN-B", thread_id="CONV-SHARED", intent="risk"
    )
    assert first["run_id"] == "RUN-A"
    assert second["run_id"] == "RUN-B"
    assert graph.get_pipeline_checkpoint(
        "CONV-SHARED", "RUN-A"
    ).values["intent"] == "status"
    assert graph.get_pipeline_checkpoint(
        "CONV-SHARED", "RUN-B"
    ).values["intent"] == "risk"


@pytest.mark.parametrize("thread,namespace", [
    ("bad/thread", "RUN-1"),
    ("CONV-1", "bad namespace"),
    ("", "RUN-1"),
])
def test_checkpoint_identifiers_are_validated(thread, namespace):
    with pytest.raises(ValueError, match="checkpoint"):
        graph.get_pipeline_checkpoint(thread, namespace)


def test_postgres_pool_is_bounded_and_setup_is_explicit(monkeypatch):
    import langgraph.checkpoint.postgres
    import psycopg_pool

    events = []

    class FakePool:
        def __init__(self, **kwargs):
            events.append(("pool", kwargs))
        def wait(self):
            events.append(("wait", None))
        def close(self):
            events.append(("close", None))

    class FakeSaver:
        def __init__(self, connection, serde=None):
            self.connection = connection
            assert serde is not None
        def setup(self):
            events.append(("setup", None))

    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example.invalid/dro")
    monkeypatch.setenv("POSTGRES_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("POSTGRES_POOL_MAX_SIZE", "7")
    monkeypatch.setenv("POSTGRES_CHECKPOINT_SETUP", "true")
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakePool)
    monkeypatch.setattr(langgraph.checkpoint.postgres, "PostgresSaver", FakeSaver)

    resources = build_checkpoint_resources()
    pool_args = events[0][1]
    assert pool_args["min_size"] == 2
    assert pool_args["max_size"] == 7
    assert ("wait", None) in events
    assert ("setup", None) in events
    resources.close()
    assert events[-1] == ("close", None)


def test_invalid_pool_bounds_fail_before_connect(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example.invalid/dro")
    monkeypatch.setenv("POSTGRES_POOL_MIN_SIZE", "5")
    monkeypatch.setenv("POSTGRES_POOL_MAX_SIZE", "2")
    with pytest.raises(RuntimeError, match="MAX_SIZE"):
        build_checkpoint_resources()


def test_disabled_checkpoint_probe_is_sanitized():
    assert CheckpointResources().probe() == {
        "status": "not_configured", "backend": "none",
    }


def test_sqlite_hitl_probe_reports_backend(tmp_path):
    from src.tools.hitl_repository import SQLiteHITLRepository

    repository = SQLiteHITLRepository(tmp_path / "hitl.db")
    assert repository.probe() == {"status": "ok", "backend": "sqlite"}


def test_environment_local_overrides_base_but_not_process(tmp_path, monkeypatch):
    import src.tools.environment as environment

    (tmp_path / ".env").write_text(
        "DRO_LAYERED_VALUE=base\nDRO_PROCESS_VALUE=base\n", encoding="utf-8"
    )
    (tmp_path / ".env.local").write_text(
        "DRO_LAYERED_VALUE=local\nDRO_PROCESS_VALUE=local\n", encoding="utf-8"
    )
    monkeypatch.setattr(environment, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("DRO_LAYERED_VALUE", raising=False)
    monkeypatch.setenv("DRO_PROCESS_VALUE", "deployment")
    environment.load_project_environment()
    assert environment.os.environ["DRO_LAYERED_VALUE"] == "local"
    assert environment.os.environ["DRO_PROCESS_VALUE"] == "deployment"


def test_api_rejects_invalid_caller_thread_id():
    from src.api.main import app

    response = TestClient(app).post("/api/pipeline/run", json={
        "signal": {}, "scenario": "outer_race_fault",
        "thread_id": "invalid thread/id",
    })
    assert response.status_code == 422


def test_application_lifespan_closes_external_resources(monkeypatch):
    import src.api.main as api

    closed = []

    class FakeHITL:
        def close(self):
            closed.append("hitl")

    monkeypatch.setattr(api, "_HITL_REPOSITORY", FakeHITL())
    monkeypatch.setattr(api, "close_graph_resources", lambda: closed.append("graph"))
    monkeypatch.setattr(api, "flush_observability", lambda: closed.append("tracing"))

    async def exercise():
        async with api._application_lifespan(api.app):
            assert closed == []

    asyncio.run(exercise())
    assert closed == ["hitl", "graph", "tracing"]


def test_health_exposes_sanitized_state_store_status(monkeypatch):
    import src.api.main as api

    class HealthyHITL:
        def probe(self):
            return {"status": "ok", "backend": "postgres"}

    monkeypatch.setattr(api, "_HITL_REPOSITORY", HealthyHITL())
    monkeypatch.setattr(
        api, "graph_checkpoint_health",
        lambda: {"status": "ok", "backend": "postgres"},
    )
    result = api.health_check()
    assert not hasattr(result, "status_code")
    assert result["checks"]["hitl_store"] == {
        "status": "ok", "backend": "postgres",
    }
    assert result["checks"]["checkpoints"] == {
        "status": "ok", "backend": "postgres",
    }
