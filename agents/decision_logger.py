"""decision_logger.py — Append-only audit log for recommendations.

Records each MaintenanceRecommendation (with its diagnosis/risk context) to a
JSON log and returns a log id; also provides filtered history lookups used by
the conversational assistant."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path("data/decision_log.json")


def log_recommendation(rec, diagnosis, risk) -> str:
    """Append one recommendation to the decision log. Returns log_id."""
    if LOG_PATH.exists():
        try:
            entries = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    else:
        entries = []

    is_blocked = rec.recommendation_status not in ("ok", "novel_llm_suggestion")
    entry = {
        "log_id": f"LOG-{uuid.uuid4().hex[:8].upper()}",
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": rec.case_id,
        "asset_id": rec.asset_id,
        "bearing_id": rec.bearing_id,
        "fault_mode": diagnosis.fault_mode,
        "severity": diagnosis.severity,
        "confidence": diagnosis.confidence,
        "recommendation_status": rec.recommendation_status,
        "is_blocked": is_blocked,
        "block_reason": rec.recommendation_status if is_blocked else None,
        "recommended_action": rec.recommended_action.name,
        "urgency": rec.urgency,
        "approval_status": rec.approval_status,
        "responsible_approver": rec.responsible_approver,
        "window_chosen": rec.window_chosen,
        "rul_min_days": risk.rul_min_days,
        "rul_max_days": risk.rul_max_days,
        "risk_level": risk.risk_level,
        "is_llm_suggested": rec.is_llm_suggested,
        "required_parts": [
            p.part_number for p in (rec.required_parts or [])
        ],
        "estimated_downtime_cost_per_hour": (
            risk.estimated_downtime_cost_per_hour),
    }
    entries.append(entry)

    LOG_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    return entry["log_id"]


def get_history(asset_id: str = None, fault_mode: str = None,
                status: str = None, limit: int = 10) -> list[dict]:
    """Filter and return recent log entries. All filters optional."""
    if not LOG_PATH.exists():
        return []
    try:
        entries = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if asset_id:
        entries = [e for e in entries if e.get("asset_id") == asset_id]
    if fault_mode:
        entries = [e for e in entries
                   if e.get("fault_mode") == fault_mode]
    if status:
        entries = [e for e in entries
                   if e.get("recommendation_status") == status]
    entries = sorted(entries,
                     key=lambda e: e.get("logged_at_utc", ""),
                     reverse=True)
    return entries[:limit]
