from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Optional


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
