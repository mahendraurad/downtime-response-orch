"""
Fault occurrence tracker for the Learning & Memory Agent.

Records every case processed by the agent with fault type,
timestamp, asset, path taken, and similarity score.

This data drives the recurring fault analytics panel in the
Streamlit dashboard and feeds the Knowledge Agent with
frequency information.

Stored in: logs/fault_tracker.json
"""

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path as _FtPath
from typing import List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)

def _ft_cfg(*keys, default=None):
    try:
        node = json.loads(
            (_FtPath(__file__).parent.parent / "config.json").read_text(encoding="utf-8")
        )
    except Exception:
        node = {}
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node

TRACKER_PATH = _ft_cfg("paths", "fault_tracker", default="logs/fault_tracker.json")


def record_fault_event(
    case_id: str,
    fault_mode: str,
    asset_id: str,
    asset_type: str,
    bearing_type: str,
    path_taken: str,
    similarity_score: float,
    source_case_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    source: str = "unknown",
) -> bool:
    """
    Records one fault event to the tracker file.
    Called by memory_storage_node after every workflow run
    regardless of Path A or Path B.

    Args:
        case_id: incoming case identifier
        fault_mode: confirmed fault type
        asset_id: which asset
        asset_type: motor, pump, conveyor, gearbox
        bearing_type: SKF6310 etc.
        path_taken: A or B
        similarity_score: cosine similarity from knowledge check
        source_case_id: which existing case was matched (Path A only)
        source: provenance of this event (discovery_demo, chatbot, manual, pipeline)

    Returns:
        True if recorded successfully
    """
    os.makedirs("logs", exist_ok=True)

    # Load existing records
    records = []
    if os.path.exists(TRACKER_PATH):
        try:
            with open(TRACKER_PATH, "r") as f:
                records = json.load(f)
        except Exception:
            records = []

    # Append new record
    records.append({
        "case_id": case_id,
        "fault_mode": fault_mode,
        "asset_id": asset_id,
        "asset_type": asset_type,
        "bearing_type": bearing_type,
        "path_taken": path_taken,
        "similarity_score": round(similarity_score, 4),
        "source_case_id": source_case_id,
        "duration_ms": duration_ms,
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        with open(TRACKER_PATH, "w") as f:
            json.dump(records, f, indent=2)
        logger.info(
            "Fault event recorded | case=%s | fault=%s | path=%s",
            case_id, fault_mode, path_taken
        )
        return True
    except Exception as e:
        logger.error("Failed to record fault event: %s", e)
        return False


def load_all_records() -> List[dict]:
    """Loads all recorded fault events from tracker file."""
    if not os.path.exists(TRACKER_PATH):
        return []
    try:
        with open(TRACKER_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def get_recurring_faults(top_n: int = 5) -> List[dict]:
    """
    Returns the top N most frequently occurring fault modes.

    Returns list of:
    {fault_mode, count, assets_affected, last_seen}
    """
    records = load_all_records()
    if not records:
        return []

    from collections import Counter
    fault_counts = Counter(r["fault_mode"] for r in records)

    result = []
    for fault_mode, count in fault_counts.most_common(top_n):
        matching = [r for r in records if r["fault_mode"] == fault_mode]
        assets = list(set(r["asset_id"] for r in matching))
        last_seen = max(r["timestamp"] for r in matching)[:10]
        result.append({
            "fault_mode": fault_mode,
            "count": count,
            "assets_affected": assets,
            "last_seen": last_seen
        })
    return result


def get_monthly_trend() -> List[dict]:
    """
    Returns fault counts grouped by month.
    Used for the monthly trend chart in the analytics panel.
    """
    records = load_all_records()
    if not records:
        return []

    from collections import defaultdict
    monthly = defaultdict(int)
    for r in records:
        month = r["timestamp"][:7]
        monthly[month] += 1

    return [
        {"month": m, "count": c}
        for m, c in sorted(monthly.items())
    ]


def get_path_split() -> dict:
    """Return counts of Path A (Existing), Path B (New), Path C (Partial), plus total.

    Reads logs/fault_tracker.json and counts every record by its path_taken
    field. Records with unknown or missing path_taken are counted under 'other'.
    """
    records = load_all_records()
    counts = {"path_a": 0, "path_b": 0, "path_c": 0, "other": 0}
    for r in records:
        p = (r.get("path_taken") or "").upper()
        if p == "A":
            counts["path_a"] += 1
        elif p == "B":
            counts["path_b"] += 1
        elif p == "C":
            counts["path_c"] += 1
        else:
            counts["other"] += 1
    counts["total"] = len(records)
    return counts


def get_root_cause_frequency() -> List[dict]:
    """Returns most common fault modes as a simple frequency list."""
    records = load_all_records()
    if not records:
        return []
    from collections import Counter
    counts = Counter(
        r.get("fault_mode", "unknown") for r in records
    )
    return [
        {"fault_mode": fm, "count": c}
        for fm, c in counts.most_common(10)
    ]
