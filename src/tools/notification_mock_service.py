"""
tools/notification_mock_service.py  —  Phase 9

Mock persona-aware notification routing for the Executor Agent.
Stores notifications in-memory per persona; each urgency level
fans out to the appropriate role set.

In Phase 11, replace send_notifications() with real
email / Teams / PagerDuty adapters per persona.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

# ── In-memory inbox: persona_id -> list[notification dict] ──────────────────
_INBOX: dict[str, list] = {
    "supervisor":  [],
    "manager":     [],
    "executive":   [],
    "engineer":    [],
    "maintenance": [],
    "ot":          [],
    "safety":      [],
}

# ── Routing table: which personas receive a notification by urgency ──────────
_URGENCY_ROUTING: dict[str, list[str]] = {
    "immediate": ["supervisor", "manager", "executive", "engineer", "maintenance", "ot", "safety"],
    "urgent":    ["supervisor", "manager", "engineer", "maintenance", "safety"],
    "planned":   ["supervisor", "engineer", "maintenance"],
    "monitor":   ["engineer", "ot"],
}


def _build_message(persona_id: str, asset_id: str, action_name: str,
                   fault_mode: str, wo_id: str, urgency: str,
                   part_info: str, window: str) -> tuple[str, str]:
    """Return (title, message) tailored to the receiving persona."""
    ad = action_name.replace("_", " ") if action_name else "maintenance action"
    fd = fault_mode.replace("_", " ") if fault_mode else "detected fault"
    wl = f"WO {wo_id}" if wo_id else "work order"
    wn = f" Scheduled window: {window}." if window else ""

    if persona_id == "supervisor":
        title = f"{wl} created — {asset_id}"
        msg   = (
            f"{wl} has been raised for asset {asset_id}. "
            f"Action: {ad}. "
            f"{'Part ' + part_info + ' reserved. ' if part_info else ''}"
            f"Urgency: {urgency}.{wn} "
            f"Crew deployment authorisation required."
        )
    elif persona_id == "manager":
        title = f"Maintenance action authorised — {asset_id}"
        msg   = (
            f"{wl} created for {asset_id} ({fd}). Urgency: {urgency}. "
            f"{'Part secured: ' + part_info + '. ' if part_info else ''}"
            f"Planned window avoids unplanned downtime exposure.{wn}"
        )
    elif persona_id == "executive":
        title = f"High-priority asset event — {asset_id}"
        msg   = (
            f"Asset {asset_id} maintenance action approved and executing. "
            f"Urgency: {urgency}. {wl} created. "
            f"Proactive intervention avoids unplanned downtime exposure."
        )
    elif persona_id == "engineer":
        title = f"Fault-driven {wl} — {asset_id}"
        msg   = (
            f"{wl} created for {asset_id}. Fault: {fd}. Action: {ad}.{wn} "
            f"Post-repair: validate vibration baseline and reset alert thresholds."
        )
    elif persona_id == "maintenance":
        title = f"{wl} — parts & crew required"
        msg   = (
            f"{wl} created for {asset_id}. "
            f"{'Part reserved: ' + part_info + '. ' if part_info else 'No additional parts required. '}"
            f"Urgency: {urgency}.{wn} "
            f"Crew allocation and LOTO preparation required before job start."
        )
    elif persona_id == "safety":
        title = f"Safety brief required — {asset_id}"
        msg   = (
            f"Maintenance action approved for {asset_id}: {ad}. Urgency: {urgency}. "
            f"LOTO procedure and work permit must be validated before crew dispatch. "
            f"Verify PPE requirements and crew qualifications prior to job start."
        )
    elif persona_id == "ot":
        title = f"Asset {asset_id} — post-repair actions required"
        msg   = (
            f"{wl} created for {asset_id} ({ad}). "
            f"Post-repair: reset historian baselines and alert thresholds. "
            f"Validate data pipeline and confirm all sensor channels are nominal."
        )
    else:
        title = f"Notification — {asset_id}"
        msg   = f"{wl} created. Action: {ad}. Urgency: {urgency}."

    return title, msg


def send_notifications(recommendation, work_order_id: str) -> list[str]:
    """
    Route and store persona-specific notifications for an executed recommendation.
    Accepts a MaintenanceRecommendation Pydantic model or equivalent dict.
    Returns the list of persona_ids that were notified.
    """
    g = ((lambda k, d=None: recommendation.get(k, d))
         if isinstance(recommendation, dict)
         else (lambda k, d=None: getattr(recommendation, k, d)))

    urgency  = g("urgency") or "planned"
    asset_id = g("asset_id") or ""
    window   = g("window_chosen") or ""

    evidence   = g("evidence") or {}
    fault_mode = (evidence.get("fault_mode", "") if isinstance(evidence, dict)
                  else getattr(evidence, "fault_mode", "")) or ""

    ra = g("recommended_action")
    if isinstance(ra, dict):
        action_name = ra.get("name", "") or ra.get("action", "")
    elif ra is not None:
        action_name = getattr(ra, "name", "") or getattr(ra, "action", "")
    else:
        action_name = ""

    parts = g("required_parts") or []
    part_info = ""
    if parts:
        p = parts[0]
        if isinstance(p, dict):
            pn = p.get("part_number") or p.get("part_model", "")
            lt = p.get("lead_time_days", 0)
        else:
            pn = getattr(p, "part_number", None) or getattr(p, "part_model", "")
            lt = getattr(p, "lead_time_days", 0)
        if pn:
            part_info = f"{pn} ({'in stock' if lt == 0 else f'lead {lt}d'})"

    target_personas = _URGENCY_ROUTING.get(urgency, ["supervisor", "engineer"])
    notified: list[str] = []

    for persona_id in target_personas:
        title, message = _build_message(
            persona_id=persona_id,
            asset_id=asset_id,
            action_name=action_name,
            fault_mode=fault_mode,
            wo_id=work_order_id,
            urgency=urgency,
            part_info=part_info,
            window=window,
        )
        _INBOX[persona_id].append({
            "id":            f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
            "persona_id":    persona_id,
            "title":         title,
            "message":       message,
            "asset_id":      asset_id,
            "work_order_id": work_order_id,
            "fault_mode":    fault_mode,
            "urgency":       urgency,
            "sent_at":       datetime.now(tz=timezone.utc).isoformat(),
            "read":          False,
        })
        notified.append(persona_id)

    return notified


def get_notifications(persona_id: str) -> list:
    """Return all notifications for a persona, newest first."""
    return list(reversed(_INBOX.get(persona_id, [])))


def get_unread_counts() -> dict[str, int]:
    """Return unread notification count per persona_id."""
    return {
        pid: sum(1 for n in notifs if not n.get("read", False))
        for pid, notifs in _INBOX.items()
    }


def mark_all_read(persona_id: str) -> None:
    """Mark every notification for a persona as read."""
    for notif in _INBOX.get(persona_id, []):
        notif["read"] = True


def clear_inbox(persona_id: str) -> None:
    """Delete all notifications for a persona (testing / reset)."""
    _INBOX[persona_id] = []
