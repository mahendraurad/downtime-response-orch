"""Durable transcript checks separate from short-lived orchestration context."""

import pytest
from fastapi.testclient import TestClient

from src.tools import chat_context_repository as repo


def test_sqlite_chat_history_survives_memory_reset(tmp_path, monkeypatch):
    path = tmp_path / "runtime.db"
    monkeypatch.setenv("CHAT_HISTORY_SQLITE_PATH", str(path))
    monkeypatch.setattr(repo, "use_postgres_tables", lambda: False)
    memory = {}
    repo.append_history("CONV-1", "admin", {
        "run_id": "CHAT-1", "persona": "supervisor",
        "user_message": "status of M-104", "response": {"response": "warning"},
        "created_at": "2026-09-08T10:00:00+00:00",
    }, memory)

    # A new process has a new in-memory context map, but the SQLite transcript
    # remains available to the authenticated owner.
    assert repo.load_history("CONV-1", "admin", {})[0]["run_id"] == "CHAT-1"
    assert repo.load_history("CONV-1", "other-user", {}) == []


def test_chat_history_is_bounded_and_resettable(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_HISTORY_SQLITE_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setattr(repo, "use_postgres_tables", lambda: False)
    memory = {}
    for index in range(4):
        repo.append_history("CONV-2", "admin", {"run_id": str(index)}, memory, max_turns=2)
    rows = repo.load_history("CONV-2", "admin", memory)
    assert [row["run_id"] for row in rows] == ["2", "3"]
    repo.clear_history("CONV-2", "admin", memory)
    assert repo.load_history("CONV-2", "admin", memory) == []


@pytest.fixture
def client():
    from src.api.main import app
    return TestClient(app)


def test_chat_api_persists_and_returns_transcript(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_HISTORY_SQLITE_PATH", str(tmp_path / "runtime.db"))
    response = client.post("/api/chat", json={
        "message": "Thanks", "persona": "supervisor",
        "conversation_id": "CONV-HISTORY-1",
    })
    assert response.status_code == 200
    history = client.get("/api/chat/history/CONV-HISTORY-1")
    assert history.status_code == 200
    row = history.json()["messages"][-1]
    assert row["user_message"] == "Thanks"
    assert row["response"]["response"]
