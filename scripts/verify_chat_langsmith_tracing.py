"""Verify that /api/chat produces LangSmith traces.

The script uses a local trusted test identity and forces local table backends so
it validates trace wiring without requiring PostgreSQL access.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from src.tools.environment import load_project_environment
    load_project_environment()

    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", "DRO")
    os.environ["DRO_TABLE_BACKEND"] = "local"
    os.environ["HITL_REPOSITORY"] = "sqlite"
    os.environ["CHECKPOINT_BACKEND"] = "none"

    from fastapi.testclient import TestClient
    from langsmith import Client

    import src.api.main as api
    from src.api.auth import get_current_user
    from src.tools.observability import flush_observability

    api.app.dependency_overrides[get_current_user] = lambda: {
        "sub": "trace-smoke",
        "role": "admin",
        "display_name": "Trace Smoke",
        "allowed_personas": [
            "supervisor", "engineer", "maintenance", "manager",
            "executive", "md", "ot", "safety",
        ],
    }
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/chat",
            json={"message": "What is RUL?", "persona": "engineer"},
        )
        print(f"chat_status={response.status_code}")
        print(f"chat_intent={response.json().get('intent')}")
    api.app.dependency_overrides.pop(get_current_user, None)

    flush_observability()
    time.sleep(3)
    project = os.getenv("LANGSMITH_PROJECT", "DRO")
    runs = list(Client().list_runs(project_name=project, limit=10))
    print(f"recent_run_count={len(runs)}")
    print("recent_run_names=" + ", ".join(getattr(run, "name", "") for run in runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
