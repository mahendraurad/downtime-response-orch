"""Shared PostgreSQL runtime-table helpers."""
from __future__ import annotations

import os
from pathlib import Path


def table_backend() -> str:
    backend = os.getenv("DRO_TABLE_BACKEND", "auto").strip().lower()
    if backend not in {"auto", "local", "postgres"}:
        raise RuntimeError("DRO_TABLE_BACKEND must be auto, local, or postgres")
    return backend


def postgres_url() -> str:
    return os.getenv("POSTGRES_URL", "").strip()


def use_postgres_tables() -> bool:
    backend = table_backend()
    url = postgres_url()
    if backend == "postgres" and not url:
        raise RuntimeError("POSTGRES_URL is required when DRO_TABLE_BACKEND=postgres")
    return backend == "postgres" or (backend == "auto" and bool(url))


def setup_enabled() -> bool:
    return os.getenv("POSTGRES_TABLE_SETUP", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def connect():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary] to use PostgreSQL tables") from exc
    timeout = int(os.getenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "10"))
    return psycopg.connect(postgres_url(), connect_timeout=timeout)


def ensure_runtime_tables(required_tables: tuple[str, ...]) -> None:
    if setup_enabled():
        migration = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "002_runtime_tables_postgres.sql"
        )
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(migration.read_text(encoding="utf-8"))
            db.commit()
        return
    with connect() as db:
        with db.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(required_tables))
            cursor.execute(
                f"SELECT relname FROM pg_class WHERE relname IN ({placeholders})",
                required_tables,
            )
            found = {row[0] for row in cursor.fetchall()}
    missing = sorted(set(required_tables) - found)
    if missing:
        raise RuntimeError(
            "PostgreSQL runtime schema is missing "
            f"{', '.join(missing)}; run once with POSTGRES_TABLE_SETUP=true"
        )
