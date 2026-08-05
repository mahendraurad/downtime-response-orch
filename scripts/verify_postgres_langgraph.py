"""Live, non-destructive verification for DRO PostgreSQL and LangGraph state.

The command loads ``.env``/``.env.local``, creates uniquely named verification
records, validates durable HITL and checkpoint resume semantics, and removes
the verification records before exit. It never prints the connection string.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools.environment import load_project_environment


def _require_postgres_configuration() -> None:
    missing = [
        name for name in ("POSTGRES_URL",)
        if not os.getenv(name, "").strip()
    ]
    if missing:
        raise RuntimeError(f"Missing required setting(s): {', '.join(missing)}")
    if os.getenv("CHECKPOINT_BACKEND", "none").lower() != "postgres":
        raise RuntimeError("CHECKPOINT_BACKEND must be postgres")
    if os.getenv("HITL_REPOSITORY", "auto").lower() not in {"auto", "postgres"}:
        raise RuntimeError("HITL_REPOSITORY must be auto or postgres")


def main() -> int:
    load_project_environment()
    _require_postgres_configuration()

    # This command validates state persistence, not external retrieval or LLMs.
    os.environ["RAG_BACKEND"] = "local"

    from src.orchestrator import graph
    from src.tools.data_loader import load_telemetry_rows
    from src.tools.hitl_repository import build_hitl_repository

    suffix = uuid.uuid4().hex[:12]
    run_id = f"VERIFY-{suffix}"
    thread_id = f"VERIFY-CONV-{suffix}"
    hitl_run_id = f"VERIFY-HITL-{suffix}"
    physical_thread_id = f"{thread_id}:{run_id}"
    repository = None

    checks: dict[str, bool] = {}
    try:
        checks["checkpoint_connection"] = (
            graph.graph_checkpoint_health().get("status") == "ok"
        )
        if not checks["checkpoint_connection"]:
            raise RuntimeError("LangGraph PostgreSQL checkpoint probe failed")

        signal = deepcopy(load_telemetry_rows("outer_race_fault")[-1])
        pending = graph.run_pipeline(
            signal,
            run_id=run_id,
            thread_id=thread_id,
            intent="full",
        )
        pending_nodes = [entry["node"] for entry in pending["pipeline_log"]]
        checks["pending_before_executor"] = (
            pending.get("recommendation") is not None
            and pending.get("execution_result") is None
            and pending_nodes[-1:] == ["prescriptive"]
        )

        # Recreate the pool, saver, and compiled graph to simulate API restart.
        graph.close_graph_resources()
        from src.tools.checkpointing import build_checkpoint_resources
        graph._checkpoint_resources = build_checkpoint_resources()
        graph._graph = graph._build_graph(
            checkpointer=graph._checkpoint_resources.saver
        )
        checks["checkpoint_survives_restart"] = (
            graph.graph_checkpoint_health().get("status") == "ok"
        )

        snapshot = graph.get_pipeline_checkpoint(thread_id, run_id)
        checks["checkpoint_readback"] = (
            snapshot.values.get("run_id") == run_id
            and snapshot.values.get("approval_status") == "pending"
        )

        resumed = graph.resume_pipeline(thread_id, run_id, "approved")
        resumed_nodes = [entry["node"] for entry in resumed["pipeline_log"]]
        checks["resume_without_replay"] = (
            resumed.get("execution_result") is not None
            and resumed_nodes == pending_nodes + ["executor"]
        )
        replay = graph.resume_pipeline(thread_id, run_id, "approved")
        checks["resume_idempotent"] = (
            [entry["node"] for entry in replay["pipeline_log"]] == resumed_nodes
        )

        repository = build_hitl_repository({
            "hitl": {
                "repository_backend": "postgres",
                "sqlite_path": "data/hitl_sessions.db",
            }
        })
        checks["hitl_connection"] = repository.probe().get("status") == "ok"
        now = time.time()
        repository.save_session(
            hitl_run_id,
            "prescriptive",
            "supervisor",
            {"verification": True},
            now,
            now + 300,
        )
        repository.close()
        repository = build_hitl_repository({
            "hitl": {
                "repository_backend": "postgres",
                "sqlite_path": "data/hitl_sessions.db",
            }
        })
        checks["hitl_survives_restart"] = (
            repository.get_session(hitl_run_id).get("status") == "pending"
        )
        claim = repository.claim(
            hitl_run_id, "prescriptive", "supervisor", "APPROVE"
        )
        checks["hitl_atomic_claim"] = claim.get("outcome") == "claimed"
        checks["hitl_decision_persisted"] = repository.complete(
            hitl_run_id,
            claim.get("claim_token", ""),
            "prescriptive",
            "APPROVE",
            "supervisor",
            "automated verification",
            {"verified": True},
        )
        checks["hitl_decision_readback"] = (
            len(repository.decisions(hitl_run_id)) == 1
        )

        for name, passed in checks.items():
            print(f"{name}: {'PASS' if passed else 'FAIL'}")
        if not all(checks.values()):
            raise RuntimeError("One or more live verification checks failed")
        print("Live PostgreSQL/LangGraph verification: PASS")
        return 0
    finally:
        # Remove verification-only state so repeated runs remain clean.
        try:
            import psycopg
            with psycopg.connect(os.environ["POSTGRES_URL"]) as db:
                with db.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM hitl_decisions WHERE run_id=%s", (hitl_run_id,)
                    )
                    cursor.execute(
                        "DELETE FROM hitl_sessions WHERE run_id=%s", (hitl_run_id,)
                    )
                db.commit()
        except Exception:
            pass
        try:
            if graph._checkpoint_resources.saver is not None:
                graph._checkpoint_resources.saver.delete_thread(physical_thread_id)
        finally:
            if repository is not None:
                repository.close()
            graph.close_graph_resources()


if __name__ == "__main__":
    raise SystemExit(main())
