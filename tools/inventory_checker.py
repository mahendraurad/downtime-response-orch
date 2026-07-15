"""
Inventory checker.
Looks up whether the required parts are in stock, reserved, or need procurement.
"""
from tools.data_loader import load_inventory

# Maps the action_type strings the agent uses to the part_type stored in inventory.json.
# Any action_type not listed here is treated as unrecognised.
ACTION_TO_PART_TYPE = {
    "replace":             "bearing",
    "replace_bearing":     "bearing",
    "lubricate":           "lubricant",
    "lubrication_service": "lubricant",
    "bearing":             "bearing",     # direct part_type passthrough
    "lubricant":           "lubricant",   # direct part_type passthrough
}


def check_part_for_action(bearing_id: str, action_type: str) -> dict:
    """
    Given a bearing and the action being planned, find the matching inventory part
    and return its availability. Returns a status dict — never crashes.

    Return dict keys:
      status             - 'in_stock', 'out_of_stock', 'no_matching_part', or 'unknown_action_type'
      in_stock           - bool: True if quantity_available > 0
      quantity_available - units free to use right now
      lead_time_days     - 0 if in stock, part's lead_time if out of stock, None if no match
      part_id            - matched part's ID, or None
      part_model         - matched part's model string, or None
      bearing_id         - echoed back from input
      part_type          - the part_type we searched for, or None if action unrecognised
    """
    # --- Step 1: map action_type to the part_type we need ---
    needed_part_type = ACTION_TO_PART_TYPE.get(action_type)

    if needed_part_type is None:
        return {
            "status": "unknown_action_type",
            "in_stock": False,
            "quantity_available": 0,
            "lead_time_days": None,
            "part_id": None,
            "part_model": None,
            "bearing_id": bearing_id,
            "part_type": None,
        }

    # --- Step 2: search inventory for a part matching BOTH conditions ---
    all_parts = load_inventory()

    matched_part = None
    for part in all_parts:
        part_type_matches    = part.get("part_type") == needed_part_type
        compatible_ids       = part.get("compatible_bearing_ids", [])
        bearing_is_compatible = bearing_id in compatible_ids

        if part_type_matches and bearing_is_compatible:
            matched_part = part
            break

    # --- Step 3: no part covers this bearing + part_type combination ---
    if matched_part is None:
        return {
            "status": "no_matching_part",
            "in_stock": False,
            "quantity_available": 0,
            "lead_time_days": None,
            "part_id": None,
            "part_model": None,
            "bearing_id": bearing_id,
            "part_type": needed_part_type,
        }

    # --- Step 4: compute available quantity ---
    if "qty_available" in matched_part:
        quantity_available = matched_part["qty_available"]
    else:
        qty_on_hand  = matched_part.get("qty_on_hand", 0)
        qty_reserved = matched_part.get("qty_reserved", 0)
        quantity_available = qty_on_hand - qty_reserved

    in_stock = quantity_available > 0

    # --- Step 5: build and return the status dict ---
    return {
        "status": "in_stock" if in_stock else "out_of_stock",
        "in_stock": in_stock,
        "quantity_available": quantity_available,
        "lead_time_days": 0 if in_stock else matched_part.get("lead_time_days"),
        "part_id": matched_part.get("part_id"),
        "part_model": matched_part.get("part_model"),
        "bearing_id": bearing_id,
        "part_type": needed_part_type,
    }
