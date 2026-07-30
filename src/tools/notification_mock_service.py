"""In-memory persona notification adapter used by the development frontend.

The narrow function interface keeps delivery replaceable by Teams, email, or
another production notifier without coupling the API or Executor Agent to it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


PERSONAS = (
    "supervisor", "manager", "executive", "engineer", "maintenance", "ot", "safety"
)

_INBOX: dict[str, list[dict]] = {persona: [] for persona in PERSONAS}

_URGENCY_ROUTING = {
    "immediate": ("supervisor", "engineer", "maintenance", "safety"),
    "urgent": ("supervisor", "engineer", "maintenance"),
    "planned": ("engineer", "maintenance"),
    "monitor": ("engineer", "ot"),
}


def _value(source, name: str, default=None):
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def send_notifications(recommendation, work_order_id: str) -> list[str]:
    """Store one unread notification for each persona selected by urgency."""
    urgency = _value(recommendation, "urgency", "planned") or "planned"
    asset_id = _value(recommendation, "asset_id", "") or ""
    action = _value(recommendation, "recommended_action", {})
    action_name = _value(action, "name", "maintenance action")
    recipients = _URGENCY_ROUTING.get(urgency, ("supervisor", "engineer"))

    for persona in recipients:
        _INBOX[persona].append({
            "id": f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
            "persona_id": persona,
            "title": f"Maintenance update - {asset_id}",
            "message": (
                f"{action_name.replace('_', ' ')} for {asset_id}; "
                f"urgency {urgency}; work order {work_order_id or 'not required'}."
            ),
            "asset_id": asset_id,
            "work_order_id": work_order_id,
            "urgency": urgency,
            "sent_at": datetime.now(tz=timezone.utc).isoformat(),
            "read": False,
        })
    return list(recipients)


def get_notifications(persona_id: str) -> list[dict]:
    """Return a copy, newest first, so callers cannot mutate the inbox."""
    return [dict(item) for item in reversed(_INBOX.get(persona_id, []))]


def get_unread_counts() -> dict[str, int]:
    return {
        persona: sum(not item["read"] for item in notifications)
        for persona, notifications in _INBOX.items()
    }


def mark_all_read(persona_id: str) -> None:
    for item in _INBOX.get(persona_id, []):
        item["read"] = True


def clear_inbox(persona_id: str | None = None) -> None:
    """Reset one or every inbox; intended for isolated tests and demos."""
    targets = (persona_id,) if persona_id is not None else PERSONAS
    for target in targets:
        if target in _INBOX:
            _INBOX[target].clear()
