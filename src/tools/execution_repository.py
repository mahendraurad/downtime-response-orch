"""SQLite idempotency and audit boundary for Agent 7."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.tools.postgres_runtime import ensure_runtime_tables, use_postgres_tables

# ************** Added by Prateek Mittal on 20th July 2026 ******************
class SQLiteExecutionRepository:
    def __init__(self, path):
        self.path = str(path); Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS executions (case_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
            db.commit()
    def exists(self, case_id):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT 1 FROM executions WHERE case_id=?", (case_id,)).fetchone() is not None
    def save(self, result):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO executions VALUES (?,?)", (result.case_id, json.dumps(result.to_dict(), sort_keys=True)))
            db.commit()
    def get(self, case_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload_json FROM executions WHERE case_id=?", (case_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def count(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT COUNT(*) FROM executions").fetchone()[0]


class PostgreSQLExecutionRepository:
    """PostgreSQL idempotency and audit boundary for Agent 7."""
    def __init__(self):
        ensure_runtime_tables(("executions",))
    def exists(self, case_id):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT 1 FROM executions WHERE case_id=%s", (case_id,))
                return cursor.fetchone() is not None
    def save(self, result):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO executions(case_id,payload_json) VALUES (%s,%s::jsonb)",
                    (result.case_id, json.dumps(result.to_dict(), sort_keys=True)),
                )
            db.commit()
    def get(self, case_id):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT payload_json::text FROM executions WHERE case_id=%s", (case_id,))
                row = cursor.fetchone()
        return json.loads(row[0]) if row else None
    def count(self):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM executions")
                return int(cursor.fetchone()[0])


def build_execution_repository(sqlite_path):
    if use_postgres_tables():
        return PostgreSQLExecutionRepository()
    return SQLiteExecutionRepository(sqlite_path)
# ***********************
