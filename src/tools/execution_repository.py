"""SQLite idempotency and audit boundary for Agent 7."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

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
# ***********************
