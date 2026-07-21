"""
tools/data_loader.py

Loads the synthetic baseline data from /data/*.json into typed, in-memory
lookup structures.  Every agent that needs reference data calls these
functions at startup — they never read JSON at inference time.

Lookups returned:
  load_asset_master()    -> Dict[asset_id, AssetMaster]
  load_bearing_master()  -> Dict[bearing_id, BearingMaster]
                         + Dict[channel_id, BearingMaster]   (second return value)
  load_fault_taxonomy()  -> List[FaultTaxonomyRule]
  load_telemetry_rows()  -> List[Dict]  (for a named scenario)
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple, Any

# Resolve the data directory relative to this file's location
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_HERE, "..", "..", "data")


def _load_json(filename: str) -> Any:
    path = os.path.join(_DATA_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Data file not found: {path}\n"
            f"Expected data directory: {os.path.abspath(_DATA_DIR)}"
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def load_asset_master():
    """
    Returns dict keyed by asset_id.
    Import AssetMaster here to avoid circular imports at module level.
    """
    from src.schemas.asset import AssetMaster

    rows = _load_json("asset_master.json")
    return {row["asset_id"]: AssetMaster.from_dict(row) for row in rows}


def load_bearing_master() -> Tuple[Dict, Dict]:
    """
    Returns two dicts:
      by_bearing_id  : Dict[bearing_id, BearingMaster]
      by_channel_id  : Dict[channel_id, BearingMaster]
    Both dicts point to the same BearingMaster instances.
    """
    from src.schemas.asset import BearingMaster

    rows = _load_json("bearing_master.json")
    by_bearing: Dict[str, Any] = {}
    by_channel: Dict[str, Any] = {}
    for row in rows:
        bm = BearingMaster.from_dict(row)
        by_bearing[bm.bearing_id] = bm
        by_channel[bm.channel_id] = bm
    return by_bearing, by_channel


def load_fault_taxonomy() -> List[Dict]:
    """
    Returns list of raw dicts from fault_taxonomy.json.
    The Failure Intelligence Agent converts these to FaultTaxonomyRule objects.
    Returning raw dicts here keeps the loader free of agent-layer schemas.
    """
    return _load_json("fault_taxonomy.json")


def load_telemetry_rows(scenario: str) -> List[Dict]:
    """
    Returns raw telemetry dicts for a named scenario.

    Valid scenario names:
      healthy | outer_race_fault | inner_race_fault | lubrication_issue |
      signal_dropout | unknown_asset | startup_filter | gearbox_fault

    Raises KeyError if the scenario name is not found.
    """
    data = _load_json("telemetry_scenarios.json")
    if scenario not in data:
        valid = [k for k in data if not k.startswith("_")]
        raise KeyError(
            f"Unknown scenario '{scenario}'. Valid options: {valid}"
        )
    return data[scenario]


def load_assets() -> List[Dict]:
    """
    Returns a flat list of asset dicts from asset_master.json.
    Used by Phase 7 (POA) tools that need to iterate assets by dict key.
    """
    return _load_json("asset_master.json")


def load_bearings() -> List[Dict]:
    """
    Returns a flat list of bearing dicts from bearing_master.json.
    Each dict includes bearing_id and asset_id for cross-reference.
    Used by Phase 7 input validator.
    """
    return _load_json("bearing_master.json")


def load_action_catalog() -> List[Dict]:
    """Returns the Phase 7 action catalog from data/action_catalog.json."""
    return _load_json("action_catalog.json")


def load_inventory() -> List[Dict]:
    """Returns the spare-parts inventory from data/inventory.json."""
    return _load_json("inventory.json")


def load_operations_context() -> List[Dict]:
    """
    Returns maintenance windows and production schedules from
    data/operations_context.json.

    Timestamps are stored as fixed UTC strings. The schedule_reader
    handles relative-date comparisons against datetime.now().
    """
    return _load_json("operations_context.json")


def load_personas() -> Dict:
    """Returns approver and contributor personas from data/personas.json."""
    return _load_json("personas.json")


def load_all_telemetry_rows() -> List[Dict]:
    """Returns every telemetry row across all scenarios (useful for bulk tests)."""
    data = _load_json("telemetry_scenarios.json")
    all_rows: List[Dict] = []
    for key, rows in data.items():
        if not key.startswith("_"):
            all_rows.extend(rows)
    return all_rows


def load_curated_signals(
    remediation_action: str = None,
    asset_id:           str = None,
) -> List[Dict]:
    """
    Load routable records from the curated signals table.

    This is the official data source for downstream agents (Monitoring,
    Failure Intelligence, etc.) when running in batch / offline mode.
    In a live pipeline the same data flows in-memory; this loader is the
    at-scale / replay / decoupled alternative.

    Optional filters:
      remediation_action  "KEEP" | "IMPUTE"   (omit = all)
      asset_id            filter to one asset  (omit = all)

    Swap the json.load() call for a DB query when you move to Postgres:
        SELECT * FROM curated_signals
        WHERE (:action IS NULL OR remediation_action = :action)
          AND (:asset  IS NULL OR asset_id = :asset)
          AND validation_status != 'REJECTED'

    Raises FileNotFoundError if the curated table has not been written yet
    (run `python run_demo.py --remediate` first, or process a batch).
    """
    path = os.path.normpath(os.path.join(_DATA_DIR, "curated_signals.json"))
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Curated signals table not found: {path}\n"
            f"Run `python run_demo.py --remediate` (or process a batch) to create it."
        )
    with open(path, "r", encoding="utf-8") as fh:
        rows: List[Dict] = json.load(fh)

    if remediation_action:
        rows = [r for r in rows if r.get("remediation_action") == remediation_action]
    if asset_id:
        rows = [r for r in rows if r.get("asset_id") == asset_id]
    return rows
