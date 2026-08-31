"""Durable development repository for detailed Agent 2 monitoring decisions."""

# ************** Added by Prateek Mittal on 17th July 2026 ******************
# Persists healthy, suppressed, insufficient, ineligible, and anomalous
# MonitoringResult objects without coupling Agent 2 to a production database.

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Optional

from src.tools.postgres_runtime import ensure_runtime_tables, use_postgres_tables


class SQLiteMonitoringDecisionRepository:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS monitoring_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telemetry_id TEXT NOT NULL,
                    bearing_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_monitoring_telemetry "
                "ON monitoring_decisions (telemetry_id)"
            )
            connection.commit()

    def save(self, result) -> int:
        payload = json.dumps(result.to_dict(), sort_keys=True)
        with closing(sqlite3.connect(self.path)) as connection:
            cursor = connection.execute(
                "INSERT INTO monitoring_decisions "
                "(telemetry_id, bearing_id, status, processed_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (result.telemetry_id, result.bearing_id, result.status,
                 result.processed_at, payload),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def latest(self, telemetry_id: str) -> Optional[dict]:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT payload_json FROM monitoring_decisions "
                "WHERE telemetry_id = ? ORDER BY decision_id DESC LIMIT 1",
                (telemetry_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def count(self) -> int:
        with closing(sqlite3.connect(self.path)) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM monitoring_decisions").fetchone()[0])


class PostgreSQLMonitoringDecisionRepository:
    """PostgreSQL repository for Agent 2 monitoring decisions."""

    def __init__(self):
        ensure_runtime_tables(("monitoring_decisions",))

    def save(self, result) -> int:
        payload = json.dumps(result.to_dict(), sort_keys=True)
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO monitoring_decisions "
                    "(telemetry_id,bearing_id,status,processed_at,payload_json) "
                    "VALUES (%s,%s,%s,%s,%s::jsonb) RETURNING decision_id",
                    (
                        result.telemetry_id,
                        result.bearing_id,
                        result.status,
                        result.processed_at or None,
                        payload,
                    ),
                )
                decision_id = int(cursor.fetchone()[0])
            db.commit()
        return decision_id

    def latest(self, telemetry_id: str) -> Optional[dict]:
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT payload_json::text FROM monitoring_decisions "
                    "WHERE telemetry_id=%s ORDER BY decision_id DESC LIMIT 1",
                    (telemetry_id,),
                )
                row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def count(self) -> int:
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM monitoring_decisions")
                return int(cursor.fetchone()[0])


def build_monitoring_decision_repository(sqlite_path: str):
    if use_postgres_tables():
        return PostgreSQLMonitoringDecisionRepository()
    return SQLiteMonitoringDecisionRepository(sqlite_path)

# ***********************
