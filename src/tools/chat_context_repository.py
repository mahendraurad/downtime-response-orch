"""Durable conversation context for chat follow-ups.

The browser may cache messages for presentation, but backend context is stored
in PostgreSQL when the durable runtime profile is enabled. A process-local
dictionary remains the explicit local fallback.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from src.tools.postgres_runtime import connect, ensure_runtime_tables, use_postgres_tables


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
