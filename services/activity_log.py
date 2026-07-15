"""Activity log reader — merges agent_decisions.log, memory.log, fault_tracker.json.

Each entry is normalised to:
    {timestamp: datetime, source: str, event: str,
     case_id: str|None, fault_mode: str, detail: str}

Usage:
    from services.activity_log import load_activity_log
    entries = load_activity_log(limit=20)
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _al_cfg(*keys, default=None):
    try:
        node = json.loads(
            (Path(__file__).parent.parent / "config.json").read_text(encoding="utf-8")
        )
    except Exception:
        node = {}
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node

_AGENT_LOG = Path("logs/agent_decisions.log")
_MEMORY_LOG = Path("logs/memory.log")
_TRACKER = Path(_al_cfg("paths", "fault_tracker", default="logs/fault_tracker.json"))
_CASES_DIR = Path("data/learned_cases")

# When False, memory.log lifecycle events (MEMORY_CREATED, VECTOR_STORED,
# METADATA_STORED, VALID_UNTIL_SET) are suppressed from the Activity Log
# panel. Four such events fire per PATH_B run — including them creates
# demo noise (5 rows for one pipeline decision instead of 1). The
# memory.log file itself is untouched; audit trail is preserved on disk.
# Flip to True post-PPO for full lifecycle audit view.
_INCLUDE_MEMORY_LIFECYCLE = False


def _parse_ts(s: str) -> Optional[datetime]:
    """Parse any log timestamp into a naive local-time datetime.

    Timezone-aware ISO strings (e.g. +00:00 / Z from fault_tracker) are
    converted to the system's local wall-clock time before the tzinfo is
    stripped so that IST users see IST times, not UTC.
    Naive strings (agent_decisions.log, memory.log) are already local and
    returned as-is.
    """
    if not s:
        return None
    s_clean = str(s).strip()
    if s_clean.endswith("Z"):
        s_clean = s_clean[:-1] + "+00:00"
    # fromisoformat handles both naive and tz-aware ISO strings in Python 3.7+
    try:
        dt = datetime.fromisoformat(s_clean)
        if dt.tzinfo is not None:
            # UTC (or other tz) → convert to local wall-clock time, strip tzinfo
            return dt.astimezone().replace(tzinfo=None)
        return dt
    except (ValueError, OverflowError):
        pass
    # Fallback for truncated / non-standard formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s_clean[:26], fmt)
        except ValueError:
            continue
    return None


def _build_fault_cache() -> dict:
    """Return {case_id: fault_mode} by scanning data/learned_cases/*.json once."""
    cache: dict = {}
    if not _CASES_DIR.exists():
        return cache
    for p in _CASES_DIR.glob("*.json"):
        if p.name.endswith(".bak"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            cid = d.get("case_id")
            if cid:
                cache[cid] = d.get("fault_mode", "")
        except Exception:
            continue
    return cache


def _load_agent_decisions(fault_cache: dict) -> list:
    entries = []
    if not _AGENT_LOG.exists():
        return entries
    try:
        for line in _AGENT_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 5:
                continue
            ts = _parse_ts(parts[0])
            if ts is None:
                continue
            case_id = parts[1] if len(parts) > 1 else None
            raw_fault = parts[2] if len(parts) > 2 else ""
            if not raw_fault and case_id:
                raw_fault = fault_cache.get(case_id, "")
            sim = parts[4] if len(parts) > 4 else ""
            detail_suffix = parts[5] if len(parts) > 5 else ""
            entries.append({
                "timestamp": ts,
                "source": "agent_decisions",
                "event": parts[3].replace("PATH_", "") if len(parts) > 3 else "?",
                "case_id": case_id,
                "fault_mode": raw_fault.replace("_", " "),
                "detail": f"{raw_fault} · sim={sim} · {detail_suffix}".rstrip(" ·"),
            })
    except Exception as e:
        logger.warning("[activity_log] agent_decisions.log parse error: %s", e)
    return entries


def _load_memory(fault_cache: dict) -> list:
    if not _INCLUDE_MEMORY_LIFECYCLE:
        return []
    entries = []
    if not _MEMORY_LOG.exists():
        return entries
    try:
        for line in _MEMORY_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue
            ts = _parse_ts(parts[0])
            if ts is None:
                continue
            case_id = parts[2] if len(parts) > 2 else None
            detail = parts[3] if len(parts) > 3 else ""
            # Extract fault=X from detail segment (e.g. "fault=outer_race_fault asset=motor")
            raw_fault = ""
            for seg in detail.split():
                if seg.startswith("fault="):
                    raw_fault = seg[6:]
                    break
            if not raw_fault and case_id:
                raw_fault = fault_cache.get(case_id, "")
            entries.append({
                "timestamp": ts,
                "source": "memory",
                "event": parts[1] if len(parts) > 1 else "EVENT",
                "case_id": case_id or None,
                "fault_mode": raw_fault.replace("_", " "),
                "detail": detail,
            })
    except Exception as e:
        logger.warning("[activity_log] memory.log parse error: %s", e)
    return entries


def _load_tracker() -> list:
    entries = []
    if not _TRACKER.exists():
        return entries
    try:
        raw = _TRACKER.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("records", [])
        if not isinstance(data, list):
            return entries
        for r in data:
            ts = _parse_ts(str(r.get("timestamp", "")))
            if ts is None:
                continue
            raw_fault = r.get("fault_mode", "")
            asset = r.get("asset_type", "—")
            asset_id = r.get("asset_id", "")
            entries.append({
                "timestamp": ts,
                "source": "fault_tracker",
                "event": (r.get("path_taken") or "EVENT").upper(),
                "case_id": r.get("case_id"),
                "display_case_id": (
                    r.get("source_case_id")
                    if r.get("path_taken") in ("A", "C") and r.get("source_case_id")
                    else r.get("case_id")
                ),
                "fault_mode": raw_fault.replace("_", " "),
                "detail": f"{raw_fault} on {asset}" + (f" · {asset_id}" if asset_id else ""),
            })
    except Exception as e:
        logger.warning("[activity_log] fault_tracker.json parse error: %s", e)
    return entries


def load_activity_log(limit: int = 20) -> list:
    """Merge all log sources, dedup, sort descending by timestamp, return top `limit`."""
    logger.info("[activity_log] loading, limit=%d", limit)
    fault_cache = _build_fault_cache()
    all_entries = (
        _load_agent_decisions(fault_cache)
        + _load_memory(fault_cache)
        + _load_tracker()
    )
    all_entries.sort(key=lambda e: e["timestamp"], reverse=True)

    # Deduplicate: one row per (case_id, event) across all log sources.
    # Timestamp is intentionally excluded from the key for two reasons:
    #   1. DISC_xxx case_ids are unique per pipeline run, so (case_id, event)
    #      already identifies a single logical occurrence without a timestamp.
    #   2. agent_decisions.log writes UTC naive timestamps while fault_tracker.json
    #      writes UTC+offset which _parse_ts converts to local time — the
    #      resulting timezone mismatch made timestamp-based dedup unreliable
    #      (entries for the same event could differ by hours on IST machines).
    seen: set = set()
    deduped = []
    for e in all_entries:
        key = (e.get("case_id") or "", e.get("event", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    dropped = len(all_entries) - len(deduped)
    if dropped:
        logger.debug("[activity_log] deduped %d duplicate rows", dropped)

    return deduped[:limit]
