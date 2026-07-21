"""Persistence boundary for Agent 1 trusted-signal and ingestion decisions."""

# ************** Added by Prateek Mittal on 16th July 2026 ******************
# Durable Agent 1 development repository. This isolates persistence,
# idempotency, and event-order lookups from the agent's validation logic.

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional


class SQLiteTrustedSignalRepository:
    """Small durable development repository; replace behind this interface for PostgreSQL."""

    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS trusted_signals (
                    telemetry_id TEXT PRIMARY KEY,
                    bearing_id TEXT NOT NULL,
                    event_epoch REAL,
                    validation_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_trusted_bearing_time "
                "ON trusted_signals (bearing_id, event_epoch)"
            )
            connection.commit()

    def exists(self, telemetry_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM trusted_signals WHERE telemetry_id = ?", (telemetry_id,)
            ).fetchone()
        return row is not None

    def latest_event_epoch(self, bearing_id: str) -> Optional[float]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT MAX(event_epoch) FROM trusted_signals WHERE bearing_id = ?",
                (bearing_id,),
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    def save(self, trusted_signal, event_epoch: Optional[float]) -> None:
        payload = json.dumps(trusted_signal.to_dict(), sort_keys=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO trusted_signals "
                "(telemetry_id, bearing_id, event_epoch, validation_status, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    trusted_signal.raw.telemetry_id,
                    trusted_signal.raw.bearing_id,
                    event_epoch,
                    trusted_signal.validation_status.value,
                    payload,
                ),
            )
            connection.commit()

    def get(self, telemetry_id: str) -> Optional[dict]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM trusted_signals WHERE telemetry_id = ?",
                (telemetry_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def count(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM trusted_signals").fetchone()[0])

# ***********************
