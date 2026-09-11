"""Backend-owned persona notification store.

SQLite is the default source of truth so forwarded HITL requests remain
available after browser refreshes and App Service restarts. Delivery can later
be replaced by Teams/email without changing the public functions.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

PERSONAS = (
    "supervisor", "manager", "executive", "engineer", "maintenance", "ot", "safety"
)
_INBOX: dict[str, list[dict]] = {persona: [] for persona in PERSONAS}

_URGENCY_ROUTING = {
    "immediate": ("supervisor", "engineer", "maintenance", "safety"),
    "urgent": ("supervisor", "engineer", "maintenance"),
    "planned": ("supervisor", "engineer", "maintenance"),
    "monitor": ("engineer", "ot"),
}


def _db_path() -> str:
    return os.getenv("NOTIFICATION_SQLITE_PATH", "data/notifications.db")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS notifications ("
        "id TEXT PRIMARY KEY, persona_id TEXT NOT NULL, sent_at TEXT NOT NULL, "
        "read INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS ix_notifications_persona "
        "ON notifications(persona_id, sent_at DESC)"
    )
    return db


def _value(source, name: str, default=None):
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _store(item: dict[str, Any]) -> dict[str, Any]:
    with _connect() as db:
        db.execute(
            "INSERT INTO notifications(id, persona_id, sent_at, read, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (item["id"], item["persona_id"], item["sent_at"], 0,
             json.dumps(item, sort_keys=True)),
        )
    _INBOX[item["persona_id"]].append(dict(item))
    return dict(item)


def send_notifications(recommendation, work_order_id: str) -> list[str]:
    """Store one unread notification for each persona selected by urgency."""
    urgency = _value(recommendation, "urgency", "planned") or "planned"
    asset_id = _value(recommendation, "asset_id", "") or ""
    action = _value(recommendation, "recommended_action", {})
    action_name = _value(action, "name", "maintenance action")
    recipients = _URGENCY_ROUTING.get(urgency, ("supervisor", "engineer"))
    for persona in recipients:
        _store({
            "id": f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
            "persona_id": persona, "type": "maintenance",
            "title": f"Maintenance update - {asset_id}",
            "message": (
                f"{str(action_name).replace('_', ' ')} for {asset_id}; "
                f"urgency {urgency}; work order {work_order_id or 'not required'}."
            ),
            "asset_id": asset_id, "work_order_id": work_order_id,
            "urgency": urgency, "run_id": "", "metadata": {},
            "sent_at": datetime.now(tz=timezone.utc).isoformat(), "read": False,
        })
    return list(recipients)


def send_custom_notification(persona_id: str, *, title: str, message: str,
                              notification_type: str = "agent",
                              asset_id: str = "", work_order_id: str = "",
                              run_id: str = "", metadata: dict | None = None) -> dict:
    """Persist a cross-persona notification and return its serialized item."""
    if persona_id not in PERSONAS:
        raise ValueError(f"Unknown persona: {persona_id}")
    item = {
        "id": f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
        "persona_id": persona_id, "type": notification_type,
        "title": str(title or "DRO notification")[:200],
        "message": str(message or "")[:2000],
        "asset_id": str(asset_id or ""), "work_order_id": str(work_order_id or ""),
        "run_id": str(run_id or ""), "metadata": dict(metadata or {}),
        "sent_at": datetime.now(tz=timezone.utc).isoformat(), "read": False,
    }
    return _store(item)


def get_notifications(persona_id: str) -> list[dict]:
    """Return durable notifications newest first."""
    with _connect() as db:
        rows = db.execute(
            "SELECT payload_json, read FROM notifications "
            "WHERE persona_id=? ORDER BY sent_at DESC, id DESC", (persona_id,)
        ).fetchall()
    result = []
    for row in rows:
        item = json.loads(row["payload_json"])
        item["read"] = bool(row["read"])
        result.append(item)
    return result


def get_unread_counts() -> dict[str, int]:
    with _connect() as db:
        rows = db.execute(
            "SELECT persona_id, COUNT(*) AS total FROM notifications "
            "WHERE read=0 GROUP BY persona_id"
        ).fetchall()
    counts = {persona: 0 for persona in PERSONAS}
    counts.update({row["persona_id"]: int(row["total"]) for row in rows})
    return counts


def mark_all_read(persona_id: str) -> None:
    with _connect() as db:
        db.execute("UPDATE notifications SET read=1 WHERE persona_id=?", (persona_id,))
    for item in _INBOX.get(persona_id, []):
        item["read"] = True


def clear_inbox(persona_id: str | None = None) -> None:
    """Reset one or every inbox; intended for isolated tests and demos."""
    with _connect() as db:
        if persona_id is None:
            db.execute("DELETE FROM notifications")
            targets = PERSONAS
        else:
            db.execute("DELETE FROM notifications WHERE persona_id=?", (persona_id,))
            targets = (persona_id,)
    for target in targets:
        if target in _INBOX:
            _INBOX[target].clear()
