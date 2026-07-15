"""
Schedule reader.
Reads maintenance windows and production schedule to find when an action can run.
"""
from datetime import datetime, timedelta, timezone

from tools.data_loader import load_assets, load_operations_context


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_utc(iso_string: str) -> datetime | None:
    """
    Convert an ISO timestamp string to a timezone-aware datetime object.
    Strips a trailing 'Z' (which means UTC) before parsing, then attaches UTC.
    Returns None if the string is empty or cannot be parsed.
    """
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string.rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _build_asset_line_map() -> dict:
    """
    Return a dict mapping every asset_id to its production_line_id.
    Used to find which line a window belongs to (windows store asset_id, not line_id).
    """
    assets = load_assets()
    return {
        a.get("asset_id"): a.get("production_line_id")
        for a in assets
        if a.get("asset_id") and a.get("production_line_id")
    }


def _build_asset_isolation_map() -> dict:
    """
    Return a dict mapping every asset_id to its maintenance_isolation value
    ("series" or "parallel"). If the field is missing, defaults to "parallel"
    (the safe choice — don't assume a window applies unless we're sure).
    """
    assets = load_assets()
    return {
        a.get("asset_id"): a.get("maintenance_isolation", "parallel")
        for a in assets
        if a.get("asset_id")
    }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def is_window_long_enough(window: dict, required_hours: float) -> bool:
    """
    Return True if the window's duration (end - start) is >= required_hours.
    Returns False if either timestamp is missing or unparseable.
    """
    start = _parse_utc(window.get("start_utc", ""))
    end   = _parse_utc(window.get("end_utc", ""))
    if start is None or end is None:
        return False
    duration_hours = (end - start).total_seconds() / 3600
    return duration_hours >= required_hours


def find_windows(
    asset_id: str,
    required_hours: float,
    within_days: int,
    now: datetime | None = None,
) -> list[dict]:
    """
    Find all planned_maintenance windows on the asset's production line that:
      1. belong to the same production line as asset_id
      2. start between now and now + within_days days
      3. are long enough: duration >= required_hours

    Returns a list of matching window dicts sorted soonest-first.
    An empty list is a valid result — it means no window fits, not an error.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    horizon = now + timedelta(days=within_days)

    # Map asset_id -> production_line_id and asset_id -> maintenance_isolation
    asset_line_map      = _build_asset_line_map()
    asset_isolation_map = _build_asset_isolation_map()

    # Look up which line the requested asset belongs to
    target_line = asset_line_map.get(asset_id)
    if target_line is None:
        return []  # unknown asset_id or asset has no production_line_id

    all_windows = load_operations_context()
    matching = []

    for window in all_windows:

        # Filter 1 — only usable maintenance slots, not blackouts
        if window.get("window_type") != "planned_maintenance":
            continue

        # Filter 2 — window must belong to this asset OR to a series asset on the same line.
        #
        # Rule (a): the window is the requested asset's own scheduled slot.
        # Rule (b): the window belongs to a DIFFERENT asset that is:
        #           - on the same production line, AND
        #           - maintenance_isolation == "series"
        #           When a series asset goes down it stops the whole line,
        #           so the requested asset is down anyway and can be worked on.
        #           A "parallel" asset's window is NOT counted — taking it offline
        #           does not affect the rest of the line.
        #           Missing isolation defaults to "parallel" (safe/strict default).
        window_asset_id   = window.get("asset_id")
        is_own_window     = (window_asset_id == asset_id)
        window_line       = asset_line_map.get(window_asset_id)
        window_isolation  = asset_isolation_map.get(window_asset_id, "parallel")
        is_series_on_same_line = (window_line == target_line and window_isolation == "series")

        if not (is_own_window or is_series_on_same_line):
            continue

        # Parse the start time; skip if missing or bad
        start = _parse_utc(window.get("start_utc", ""))
        if start is None:
            continue

        # Filter 3 — horizon: window must start in the future and within the limit
        if not (now <= start <= horizon):
            continue

        # Filter 4 — duration: window must be long enough for the job
        if not is_window_long_enough(window, required_hours):
            continue

        matching.append(window)

    matching.sort(key=lambda w: _parse_utc(w.get("start_utc", "")))
    return matching


def is_bottleneck_now(asset_id: str) -> bool:
    """
    Return True if the asset is flagged as a production bottleneck.
    Returns False if the asset is not found or the flag is absent.
    """
    assets = load_assets()
    for asset in assets:
        if asset.get("asset_id") == asset_id:
            return bool(asset.get("is_bottleneck", False))
    return False


def next_planned_shutdown(line_id: str) -> dict | None:
    """
    Return the soonest upcoming planned_maintenance window for the given production line.
    Returns None if no such window exists.

    Note: the operations_context data uses window_type 'planned_maintenance', not
    'planned_shutdown'. This function finds the earliest planned_maintenance window
    on the requested line that starts in the future.
    """
    now = datetime.now(tz=timezone.utc)
    asset_line_map = _build_asset_line_map()
    all_windows = load_operations_context()

    candidates = []
    for window in all_windows:
        if window.get("window_type") != "planned_maintenance":
            continue
        if asset_line_map.get(window.get("asset_id")) != line_id:
            continue
        start = _parse_utc(window.get("start_utc", ""))
        if start is None or start < now:
            continue
        candidates.append(window)

    if not candidates:
        return None

    candidates.sort(key=lambda w: _parse_utc(w.get("start_utc", "")))
    return candidates[0]
