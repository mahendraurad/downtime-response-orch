"""
tools/cmms_mock_service.py  —  Phase 9

Mock CMMS connector — simulates work order creation without a real system.
Replace the body of create_work_order() with a SAP PM / Maximo API call in Phase 11.
"""
import uuid
from datetime import datetime, timezone

# Human-readable priority labels fed to downstream CMMS display
_PRIORITY_LABELS = {
    "stop_and_replace":         "P1-Critical",
    "repair_next_planned_stop": "P2-High",
    "inspect_next_shift":       "P3-Medium",
    "derate_and_monitor":       "P3-Medium",
    "continue_monitoring":      "P4-Low",
}


def create_work_order(asset_id: str, fault_mode: str,
                      priority: str, description: str) -> dict:
    """
    Simulate work-order creation in a CMMS.

    Returns a dict with work_order_id, status, and metadata.
    In Phase 11, replace the body with a real CMMS API call.
    """
    wo_id = f"WO-{uuid.uuid4().hex[:8].upper()}"
    return {
        "work_order_id": wo_id,
        "asset_id": asset_id,
        "fault_mode": fault_mode,
        "priority": priority,
        "description": description,
        "status": "open",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def map_action_to_priority(action: str) -> str:
    """Return the CMMS priority label for a recommended action."""
    return _PRIORITY_LABELS.get(action, "P3-Medium")
