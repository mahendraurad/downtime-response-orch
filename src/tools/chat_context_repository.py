"""Durable conversation context for chat follow-ups.

The browser may cache messages for presentation, but backend context is stored
in PostgreSQL when the durable runtime profile is enabled. A process-local
dictionary remains the explicit local fallback.
"""
from __future__ import annotations

import json
import os
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from src.tools.postgres_runtime import connect, ensure_runtime_tables, use_postgres_tables


def _sqlite_path() -> str:
    """Use the runtime SQLite database for demo history persistence.

    Work orders and chat history therefore survive an API restart in the local
    profile.  ``CHAT_HISTORY_SQLITE_PATH`` remains an escape hatch for tests or
    deployments that want a separate file.
    """
    return (os.getenv("CHAT_HISTORY_SQLITE_PATH")
            or os.getenv("WORK_ORDER_SQLITE_PATH")
            or "data/work_orders.db")


def _ensure_sqlite_history() -> sqlite3.Connection:
    path = _sqlite_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE IF NOT EXISTS chat_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, "
        "owner_id TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_history_conversation "
        "ON chat_history(conversation_id, id)"
    )
    db.commit()
    return db


def load_context(conversation_id: str, memory: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not use_postgres_tables():
        return deepcopy(memory.get(conversation_id, {}))
    ensure_runtime_tables(("chat_contexts",))
    with connect() as db:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT payload_json FROM chat_contexts WHERE conversation_id=%s",
                (conversation_id,),
            )
            row = cursor.fetchone()
    return deepcopy(json.loads(row[0]) if row else {})


def save_context(conversation_id: str, context: dict[str, Any], memory: dict[str, dict[str, Any]]) -> None:
    if not use_postgres_tables():
        memory[conversation_id] = deepcopy(context)
        return
    ensure_runtime_tables(("chat_contexts",))
    now = datetime.now(timezone.utc)
    with connect() as db:
        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_contexts(conversation_id,payload_json,updated_at) "
                "VALUES (%s,%s::jsonb,%s) ON CONFLICT(conversation_id) DO UPDATE SET "
                "payload_json=EXCLUDED.payload_json, updated_at=EXCLUDED.updated_at",
                (conversation_id, json.dumps(context, sort_keys=True), now),
            )
        db.commit()


def clear_context(conversation_id: str, memory: dict[str, dict[str, Any]]) -> None:
    memory.pop(conversation_id, None)
    if not use_postgres_tables():
        return
    ensure_runtime_tables(("chat_contexts",))
    with connect() as db:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM chat_contexts WHERE conversation_id=%s", (conversation_id,))
        db.commit()


def append_history(conversation_id: str, owner_id: str, record: dict[str, Any],
                   memory: dict[str, dict[str, Any]], max_turns: int = 500) -> None:
    """Persist one user/assistant turn without making it pipeline context."""
    row = deepcopy(record)
    row["conversation_id"] = conversation_id
    row["owner_id"] = owner_id
    if use_postgres_tables():
        context = load_context(conversation_id, memory)
        history = context.get("_history", [])
        if not isinstance(history, list):
            history = []
        context["_history"] = (history + [row])[-max_turns:]
        save_context(conversation_id, context, memory)
        return
    db = _ensure_sqlite_history()
    try:
        db.execute(
            "INSERT INTO chat_history(conversation_id,owner_id,created_at,payload_json) "
            "VALUES (?,?,?,?)",
            (conversation_id, owner_id, str(row.get("created_at", "")),
             json.dumps(row, sort_keys=True, default=str)),
        )
        db.execute(
            "DELETE FROM chat_history WHERE conversation_id=? AND owner_id=? "
            "AND id NOT IN (SELECT id FROM chat_history WHERE conversation_id=? "
            "AND owner_id=? ORDER BY id DESC LIMIT ?)",
            (conversation_id, owner_id, conversation_id, owner_id, max_turns),
        )
        db.commit()
    finally:
        db.close()


def load_history(conversation_id: str, owner_id: str,
                 memory: dict[str, dict[str, Any]], limit: int = 120) -> list[dict[str, Any]]:
    """Return persisted turns in chronological order for the authenticated owner."""
    if use_postgres_tables():
        context = load_context(conversation_id, memory)
        history = context.get("_history", [])
        if not isinstance(history, list):
            return []
        return [deepcopy(row) for row in history
                if isinstance(row, dict) and row.get("owner_id") == owner_id][-limit:]
    db = _ensure_sqlite_history()
    try:
        rows = db.execute(
            "SELECT payload_json FROM chat_history WHERE conversation_id=? "
            "AND owner_id=? ORDER BY id DESC LIMIT ?",
            (conversation_id, owner_id, limit),
        ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]
    finally:
        db.close()


def clear_history(conversation_id: str, owner_id: str,
                  memory: dict[str, dict[str, Any]]) -> None:
    """Delete the transcript when the user explicitly resets a conversation."""
    if use_postgres_tables():
        context = load_context(conversation_id, memory)
        history = context.get("_history", [])
        if isinstance(history, list):
            context["_history"] = [row for row in history
                                    if not isinstance(row, dict)
                                    or row.get("owner_id") != owner_id]
            save_context(conversation_id, context, memory)
        return
    db = _ensure_sqlite_history()
    try:
        db.execute("DELETE FROM chat_history WHERE conversation_id=? AND owner_id=?",
                   (conversation_id, owner_id))
        db.commit()
    finally:
        db.close()
