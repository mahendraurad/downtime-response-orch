"""
tools/decision_logger.py  —  Phase 7

Append-only audit log for MaintenanceRecommendations.
Records each recommendation (with diagnosis/risk context) to data/decision_log.json
and returns a log_id. Also provides filtered history lookups.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

_HERE     = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "data", "decision_log.json"))


def log_recommendation(rec, diagnosis, risk) -> str:
    """Append one recommendation to the decision log. Returns log_id."""
    if os.path.exists(_LOG_PATH):
        try:
            with open(_LOG_PATH, "r", encoding="utf-8") as fh:
                entries = json.load(fh)
        except Exception:
            entries = []
    else:
        entries = []

    is_blocked = rec.recommendation_status not in ("ok", "novel_llm_suggestion")
    entry = {
        "log_id":                    f"LOG-{uuid.uuid4().hex[:8].upper()}",
        "logged_at_utc":             datetime.now(timezone.utc).isoformat(),
        "case_id":                   rec.case_id,
        "asset_id":                  rec.asset_id,
        "bearing_id":                rec.bearing_id,
        "fault_mode":                diagnosis.fault_mode,
        "severity":                  diagnosis.severity,
        "confidence":                diagnosis.confidence,
        "recommendation_status":     rec.recommendation_status,
        "is_blocked":                is_blocked,
        "block_reason":              rec.recommendation_status if is_blocked else None,
        "recommended_action":        rec.recommended_action.name,
        "urgency":                   rec.urgency,
        "approval_status":           rec.approval_status,
        "responsible_approver":      rec.responsible_approver,
        "window_chosen":             rec.window_chosen,
        "rul_min_days":              risk.rul_min_days,
        "rul_max_days":              risk.rul_max_days,
        "risk_level":                risk.risk_level,
        "is_llm_suggested":          rec.is_llm_suggested,
        "required_parts":            [p.part_number for p in (rec.required_parts or [])],
    }
    entries.append(entry)

    with open(_LOG_PATH, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)

    return entry["log_id"]


def get_history(
    asset_id: str = None,
    fault_mode: str = None,
    status: str = None,
    limit: int = 10,
) -> list[dict]:
    """Filter and return recent log entries. All filters optional."""
    if not os.path.exists(_LOG_PATH):
        return []
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except Exception:
        return []

    if asset_id:
        entries = [e for e in entries if e.get("asset_id") == asset_id]
    if fault_mode:
        entries = [e for e in entries if e.get("fault_mode") == fault_mode]
    if status:
        entries = [e for e in entries if e.get("recommendation_status") == status]

    entries = sorted(entries, key=lambda e: e.get("logged_at_utc", ""), reverse=True)
    return entries[:limit]
