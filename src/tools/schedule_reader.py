"""
tools/schedule_reader.py  —  Phase 7

Reads maintenance windows and production schedules from data/operations_context.json
to determine when maintenance actions can be executed.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Optional


def _parse_utc(ts: str) -> Optional[datetime]:
    """Parse an ISO-8601 UTC timestamp string; return None on failure."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def is_window_long_enough(window: dict, required_hours: float) -> bool:
    """Return True if the window's duration meets the required hours."""
    start = _parse_utc(window.get("start_utc", ""))
    end   = _parse_utc(window.get("end_utc", ""))
    if start is None or end is None:
        return False
    return (end - start).total_seconds() / 3600 >= required_hours


def is_bottleneck_now(asset_id: str) -> bool:
    """Return True if the asset is currently flagged as a production bottleneck."""
    from src.tools.data_loader import load_assets
    for asset in load_assets():
        if asset.get("asset_id") == asset_id:
            return bool(asset.get("is_bottleneck", False))
    return False


def next_planned_shutdown(asset_id: str) -> Optional[dict]:
    """Return the soonest upcoming planned_maintenance window for the asset's production line."""
    from src.tools.data_loader import load_operations_context, load_assets
    now = datetime.now(tz=timezone.utc)

    line_id = None
    for asset in load_assets():
        if asset.get("asset_id") == asset_id:
            line_id = asset.get("production_line_id")
            break

    windows = load_operations_context()
    candidates = []
    for w in windows:
        if w.get("window_type") != "planned_maintenance":
            continue
        if w.get("asset_id") != asset_id:
            continue
        start = _parse_utc(w.get("start_utc", ""))
        if start and start > now:
            candidates.append(w)

    candidates.sort(key=lambda w: w.get("start_utc", ""))
    return candidates[0] if candidates else None


def find_windows(
    asset_id: str,
    required_hours: float,
    within_days: int = 30,
) -> List[dict]:
    """
    Find upcoming planned_maintenance windows for asset_id that:
      - are of type planned_maintenance (not production_blackout)
      - start within within_days from now
      - have duration >= required_hours

    Returns windows sorted chronologically (soonest first).

    Series-isolation note: when a series asset goes down it stops the whole
    line, so any series window on that line also covers this asset. Parallel
    assets have independent windows, so only windows explicitly for this
    asset_id are returned.
    """
    from src.tools.data_loader import load_operations_context, load_assets

    now      = datetime.now(tz=timezone.utc)
    deadline = now + timedelta(days=max(within_days, 1))

    asset_line = None
    asset_isolation = "parallel"
    for asset in load_assets():
        if asset.get("asset_id") == asset_id:
            asset_line       = asset.get("production_line_id")
            asset_isolation  = asset.get("maintenance_isolation", "parallel")
            break

    windows = load_operations_context()
    results = []

    for w in windows:
        if w.get("window_type") != "planned_maintenance":
            continue

        w_asset_id = w.get("asset_id")

        if w_asset_id == asset_id:
            pass  # direct match — always included
        elif asset_isolation == "series" and asset_line:
            # series asset: a window on any same-line series asset covers this one
            for asset in load_assets():
                if (asset.get("asset_id") == w_asset_id
                        and asset.get("production_line_id") == asset_line
                        and asset.get("maintenance_isolation", "parallel") == "series"):
                    break
            else:
                continue
        else:
            continue  # parallel asset — only direct-match windows count

        start = _parse_utc(w.get("start_utc", ""))
        if start is None or start <= now or start > deadline:
            continue

        if not is_window_long_enough(w, required_hours):
            continue

        results.append(w)

    results.sort(key=lambda w: w.get("start_utc", ""))
    return results
