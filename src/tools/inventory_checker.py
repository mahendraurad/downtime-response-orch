"""
tools/inventory_checker.py  —  Phase 7

Checks whether spare parts are available for a given bearing + action type.
Never raises — always returns a structured status dict.
"""
from __future__ import annotations

ACTION_TO_PART_TYPE: dict[str, str] = {
    "replace":             "bearing",
    "replace_bearing":     "bearing",
    "lubricate":           "lubricant",
    "lubrication_service": "lubricant",
    # direct part_type passthrough (catalog part_type used as action_type)
    "bearing":             "bearing",
    "lubricant":           "lubricant",
}


def check_part_for_action(bearing_id: str, action_type: str) -> dict:
    """
    Search inventory for a part matching both bearing_id and the action's part type.

    Returns a dict with keys:
      status           - "in_stock" | "out_of_stock" | "no_matching_part" | "unknown_action_type"
      in_stock         - bool
      quantity_available
      lead_time_days   - 0 if in stock, supplier lead time if out of stock, None if no match
      part_model       - matched part model number or None
      part_id          - matched part ID or None
    """
    from src.tools.data_loader import load_inventory

    part_type = ACTION_TO_PART_TYPE.get((action_type or "").lower())
    if part_type is None:
        return {
            "status": "unknown_action_type",
            "in_stock": False,
            "quantity_available": 0,
            "lead_time_days": None,
            "part_model": None,
            "part_id": None,
        }

    inventory = load_inventory()
    matched = None
    for part in inventory:
        if (part.get("part_type") == part_type
                and bearing_id in part.get("compatible_bearing_ids", [])):
            matched = part
            break

    if matched is None:
        return {
            "status": "no_matching_part",
            "in_stock": False,
            "quantity_available": 0,
            "lead_time_days": None,
            "part_model": None,
            "part_id": None,
        }

    qty_available = matched.get("qty_available", 0)
    in_stock      = qty_available > 0
    lead_time     = 0 if in_stock else matched.get("lead_time_days", 0)

    return {
        "status":             "in_stock" if in_stock else "out_of_stock",
        "in_stock":           in_stock,
        "quantity_available": qty_available,
        "lead_time_days":     lead_time,
        "part_model":         matched.get("part_model"),
        "part_id":            matched.get("part_id"),
    }
