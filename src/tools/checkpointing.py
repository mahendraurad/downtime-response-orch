"""Optional PostgreSQL LangGraph checkpoint construction."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


CHECKPOINT_ALLOWED_TYPES = (
    ("src.schemas.persona", "PersonaContext"),
    ("src.schemas.bearing_signal", "ValidationStatus"),
    ("src.schemas.bearing_signal", "MachineState"),
    ("src.schemas.bearing_signal", "BearingSignalFact"),
    ("src.schemas.bearing_signal", "AssetContext"),
    ("src.schemas.bearing_signal", "BearingContext"),
    ("src.schemas.bearing_signal", "ValidationDetail"),
    ("src.schemas.bearing_signal", "QualityDimension"),
    ("src.schemas.bearing_signal", "QualityReport"),
    ("src.schemas.bearing_signal", "TrustedBearingSignal"),
    ("src.schemas.anomaly", "AnomalyEvent"),
    ("src.schemas.anomaly", "MonitoringResult"),
    ("src.schemas.diagnosis", "FaultDiagnosis"),
    ("src.schemas.risk", "RiskAssessment"),
    ("src.schemas.knowledge", "GroundedGuidanceItem"),
    ("src.schemas.knowledge", "SourceDocument"),
    ("src.schemas.knowledge", "KnowledgeGuidance"),
    ("src.schemas.recommendation", "MaintenanceRecommendation"),
    ("src.schemas.execution", "ExecutionStep"),
    ("src.schemas.execution", "ExecutionResult"),
    ("src.schemas.feedback", "FeedbackEvent"),
    ("src.schemas.feedback", "LearnedCaseDocument"),
)


def build_checkpoint_serializer():
    """Allow only the typed DRO contracts persisted in graph state."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(
        allowed_msgpack_modules=CHECKPOINT_ALLOWED_TYPES,
    )


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
            self.connection = None
            self.saver = None

    def probe(self) -> dict[str, str]:
        """Return a non-secret readiness result for the configured saver."""
        if not self.enabled or self.connection is None:
            return {"status": "not_configured", "backend": "none"}
        try:
            with self.connection.connection() as db:
                db.execute("SELECT 1")
            return {"status": "ok", "backend": "postgres"}
        except Exception as exc:
            return {
                "status": "error",
                "backend": "postgres",
                "detail": type(exc).__name__,
            }


def _pool_size(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


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
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "Install langgraph-checkpoint-postgres and psycopg[binary] "
            "for PostgreSQL checkpoints"
        ) from exc

    min_size = _pool_size("POSTGRES_POOL_MIN_SIZE", 1)
    max_size = _pool_size("POSTGRES_POOL_MAX_SIZE", 10)
    if max_size < min_size:
        raise RuntimeError(
            "POSTGRES_POOL_MAX_SIZE must be greater than or equal to "
            "POSTGRES_POOL_MIN_SIZE"
        )
    connection = ConnectionPool(
        conninfo=url,
        min_size=min_size,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=True,
        name="dro-langgraph-checkpoints",
    )
    try:
        connection.wait()
        saver = PostgresSaver(
            connection,
            serde=build_checkpoint_serializer(),
        )
    except Exception:
        connection.close()
        raise
    if os.getenv("POSTGRES_CHECKPOINT_SETUP", "false").lower() in {
        "1", "true", "yes", "on",
    }:
        saver.setup()
    return CheckpointResources(saver=saver, connection=connection)
