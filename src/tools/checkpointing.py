"""Optional PostgreSQL LangGraph checkpoint construction."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class CheckpointResources:
    saver: Any = None
    connection: Any = None

    @property
    def enabled(self) -> bool:
        return self.saver is not None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()


def build_checkpoint_resources() -> CheckpointResources:
    backend = os.getenv("CHECKPOINT_BACKEND", "none").strip().lower()
    if backend not in {"none", "postgres"}:
        raise RuntimeError("CHECKPOINT_BACKEND must be none or postgres")
    if backend == "none":
        return CheckpointResources()

    url = os.getenv("POSTGRES_URL", "").strip()
    if not url:
        raise RuntimeError("POSTGRES_URL is required for PostgreSQL checkpoints")
    try:
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "Install langgraph-checkpoint-postgres and psycopg[binary] "
            "for PostgreSQL checkpoints"
        ) from exc

    connection = psycopg.connect(url, autocommit=True, prepare_threshold=0)
    saver = PostgresSaver(connection)
    if os.getenv("POSTGRES_CHECKPOINT_SETUP", "false").lower() in {
        "1", "true", "yes", "on",
    }:
        saver.setup()
    return CheckpointResources(saver=saver, connection=connection)
