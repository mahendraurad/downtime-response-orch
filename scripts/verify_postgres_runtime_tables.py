"""Verify DRO mutable runtime tables in PostgreSQL.

Usage:
    python scripts/verify_postgres_runtime_tables.py --setup
    python scripts/verify_postgres_runtime_tables.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tools.environment import load_project_environment
from src.tools.postgres_runtime import connect, ensure_runtime_tables


RUNTIME_TABLES = (
    "trusted_signals",
    "monitoring_decisions",
    "executions",
    "learned_cases",
    "rejection_feedback",
    "chat_learning_observations",
    "work_orders",
    "curated_signals",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--setup", action="store_true",
        help="Create missing runtime tables before verification.",
    )
    args = parser.parse_args()

    load_project_environment()
    if not os.getenv("POSTGRES_URL", "").strip():
        raise RuntimeError("POSTGRES_URL is required")
    os.environ["DRO_TABLE_BACKEND"] = "postgres"
    if args.setup:
        os.environ["POSTGRES_TABLE_SETUP"] = "true"

    ensure_runtime_tables(RUNTIME_TABLES)
    counts: dict[str, int] = {}
    with connect() as db:
        with db.cursor() as cursor:
            for table in RUNTIME_TABLES:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = int(cursor.fetchone()[0])

    print("PostgreSQL runtime tables verified")
    for table in RUNTIME_TABLES:
        print(f"{table}: {counts[table]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
