"""
tools/inventory_mock_service.py  —  Phase 9

Mock inventory reservation — simulates part reservation without a real ERP.
Replace the body of reserve_part() with a real ERP/MES API call in Phase 11.

The catalog is module-level so reservations persist across calls within one
process — this lets multiple parts for the same WO each reduce qty_on_hand.
"""
import uuid
from datetime import datetime, timezone

# In-memory parts catalog: part_number -> qty_on_hand
# Must match part_model values in data/inventory.json.
_CATALOG: dict[str, int] = {
    # Rolling-element bearings (from inventory.json)
    "SKF6310-ZZ":   2,   # PRT_001 — BRG_001-004  (outer/inner race motor fault)
    "SKF6208-2RS":  4,   # PRT_002 — BRG_005-008
    "SKF22212-EK":  1,   # PRT_003 — BRG_009-010
    "SKF22318-E":   0,   # PRT_004 — BRG_011-012 (intentionally OOS for gearbox scenario)
    # Lubricants
    "SHELL-GADUS-S2-V220": 12,  # PRT_005
    "MOBIL-DTE-25":         6,  # PRT_006 — pump lubrication scenario
}


def reserve_part(part_number: str, quantity: int,
                 work_order_id: str) -> dict:
    """
    Simulate part reservation in an ERP/MES.

    Returns a dict with reservation_id, status ('reserved' or 'shortage'),
    and inventory counts.  In Phase 11, replace the body with a real API call.
    """
    qty_available = _CATALOG.get(part_number, 0)

    if qty_available >= quantity:
        _CATALOG[part_number] = qty_available - quantity
        reservation_id = f"RES-{uuid.uuid4().hex[:8].upper()}"
        return {
            "reservation_id":    reservation_id,
            "part_number":       part_number,
            "reserved_qty":      quantity,
            "qty_on_hand_after": _CATALOG[part_number],
            "work_order_id":     work_order_id,
            "status":            "reserved",
            "reserved_at":       datetime.now(tz=timezone.utc).isoformat(),
        }

    # Shortage — return info needed for procurement alert
    return {
        "reservation_id": "",
        "part_number":    part_number,
        "reserved_qty":   0,
        "qty_on_hand":    qty_available,
        "requested_qty":  quantity,
        "work_order_id":  work_order_id,
        "status":         "shortage",
        "reserved_at":    datetime.now(tz=timezone.utc).isoformat(),
    }


def get_qty_on_hand(part_number: str) -> int:
    """Return current on-hand quantity for a part number (0 if unknown)."""
    return _CATALOG.get(part_number, 0)
