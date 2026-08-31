"""Verify LangSmith tracing configuration and ingestion for DRO.

This script prints only non-secret configuration status and recent run names.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langsmith import Client, traceable

from src.tools.environment import load_project_environment
from src.tools.observability import flush_observability


@traceable(name="DRO LangSmith Smoke", run_type="chain", tags=["dro", "smoke"])
def _smoke_trace() -> dict:
    return {"ok": True, "component": "langsmith"}


def main() -> int:
    load_project_environment()
    project = os.getenv("LANGSMITH_PROJECT", "")
    print(f"LANGSMITH_TRACING={os.getenv('LANGSMITH_TRACING', '')}")
    print(f"LANGSMITH_ENDPOINT_SET={bool(os.getenv('LANGSMITH_ENDPOINT'))}")
    print(f"LANGSMITH_PROJECT={project}")
    print(f"LANGSMITH_API_KEY_SET={bool(os.getenv('LANGSMITH_API_KEY'))}")
    _smoke_trace()
    flush_observability()
    time.sleep(2)
    runs = list(Client().list_runs(project_name=project or None, limit=5))
    print(f"recent_run_count={len(runs)}")
    print("recent_run_names=" + ", ".join(getattr(run, "name", "") for run in runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
