"""
data_loader.py — read-only helpers that load JSON data files from the data/ folder.
Each function opens one file, parses it, and returns plain Python data (dicts/lists).
No decisions are made here; all business logic lives in the tools and agent.
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from streamlit import cache_data as _cache_data
except Exception:
    def _cache_data(*a, **k):
        def _wrap(fn): return fn
        return _wrap if not a else a[0]


# The demo data was authored with 2026-05-20 as the effective "today"
# (the date of the telemetry scenarios). We use this as an anchor so that
# window dates can always be shifted to stay in the near future.
_WINDOW_ANCHOR = datetime(2026, 5, 20, tzinfo=timezone.utc)

# Resolve the absolute path to the data/ folder once, at import time.
# __file__ is this file (tools/data_loader.py).
# .parent      goes up one level → tools/
# .parent.parent goes up another  → project root
# / "data"     appends the data folder name
DATA_DIR = Path(__file__).parent.parent / "data"


@_cache_data(ttl=300)
def load_assets() -> list[dict]:
    """Load all assets from asset_master.json. Returns a list of asset dicts."""
    file_path = DATA_DIR / "asset_master.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return []


@_cache_data(ttl=300)
def load_bearings() -> list[dict]:
    """Load all bearings from bearing_master.json. Returns a list of bearing dicts."""
    file_path = DATA_DIR / "bearing_master.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return []


@_cache_data(ttl=300)
def load_fault_taxonomy() -> list[dict]:
    """Load all fault definitions from fault_taxonomy.json. Returns a list of fault dicts."""
    file_path = DATA_DIR / "fault_taxonomy.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return []


def load_scenario(scenario_name: str) -> list[dict]:
    """
    Load telemetry rows for one named scenario from telemetry_scenarios.json.
    Valid scenario names: healthy, outer_race_fault, inner_race_fault,
    lubrication_issue, signal_dropout, unknown_asset, startup_filter, gearbox_fault.
    Returns a list of telemetry row dicts.
    """
    file_path = DATA_DIR / "telemetry_scenarios.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            all_scenarios = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return []

    if scenario_name not in all_scenarios:
        valid = [k for k in all_scenarios if not k.startswith("_")]
        raise ValueError(
            f"Unknown scenario '{scenario_name}'. Valid options are: {valid}"
        )

    return all_scenarios[scenario_name]


@_cache_data(ttl=300)
def load_inventory() -> list[dict]:
    """Load all spare parts from inventory.json. Returns a list of part dicts."""
    file_path = DATA_DIR / "inventory.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return []


@_cache_data(ttl=300)
def load_operations_context() -> list[dict]:
    """
    Load maintenance windows from operations_context.json.

    All start_utc / end_utc timestamps are shifted forward by however many days
    have elapsed since _WINDOW_ANCHOR (2026-05-20 — the day the demo data was
    authored). This keeps every window in the near future no matter when you run
    the demo, so in_window actions always have a real scheduling target.

    The JSON file itself is never changed; only the in-memory copy is adjusted.
    """
    file_path = DATA_DIR / "operations_context.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            raw_windows = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return []

    # How far ahead do we need to shift?
    # Replace H/M/S with zeros so the offset is in whole days only.
    today  = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    offset = today - _WINDOW_ANCHOR   # e.g. timedelta(days=19) if today is 2026-06-08

    shifted = []
    for window in raw_windows:
        w = dict(window)   # shallow copy — do not mutate the parsed JSON
        for field in ("start_utc", "end_utc"):
            raw = w.get(field)
            if raw:
                # Parse the ISO string (strip trailing Z, attach UTC, shift, reformat)
                dt = datetime.fromisoformat(raw.rstrip("Z")).replace(tzinfo=timezone.utc)
                w[field] = (dt + offset).strftime("%Y-%m-%dT%H:%M:%SZ")
        shifted.append(w)

    return shifted


@_cache_data(ttl=300)
def load_action_catalog() -> list[dict]:
    """Load all SOP-derived action entries from action_catalog.json. Returns a list of catalog dicts."""
    file_path = DATA_DIR / "action_catalog.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return []


@_cache_data(ttl=300)
def load_personas() -> dict:
    """Load the persona/role mapping from personas.json. Returns the full mapping dict."""
    file_path = DATA_DIR / "personas.json"
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[data_loader] WARNING: could not load {file_path}: {e}")
        return {}
