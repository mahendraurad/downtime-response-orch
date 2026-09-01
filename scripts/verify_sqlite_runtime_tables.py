"""Create and verify the SQLite development runtime schema."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


TABLES = (
    "trusted_signals", "monitoring_decisions", "executions", "learned_cases",
    "rejection_feedback", "chat_learning_observations", "work_orders",
    "curated_signals",
)


def verify(path: str, setup: bool = True) -> list[str]:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        if setup:
            schema = Path(__file__).resolve().parents[1] / "migrations" / "002_runtime_tables_sqlite.sql"
            db.executescript(schema.read_text(encoding="utf-8"))
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    found = sorted(row[0] for row in rows)
    missing = sorted(set(TABLES) - set(found))
    if missing:
        raise RuntimeError("SQLite runtime schema is missing: " + ", ".join(missing))
    return found


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/dro_runtime.db")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print("SQLite runtime tables:", ", ".join(verify(args.db, not args.check_only)))
