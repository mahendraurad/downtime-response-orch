"""
scripts/run_executor_demo.py  —  Phase 9 manual test

Run from the project root:
    python scripts/run_executor_demo.py

Toggle APPROVAL_STATUS and URGENCY below to exercise different paths.
"""
import json
import logging
import os
import sys

# Make the repo root importable when run as `python scripts/run_executor_demo.py`
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

from src.schemas.recommendation import (
    MaintenanceRecommendation, RecommendedAction, RequiredPart, Contributor,
)
from src.agents.executor_agent import ExecutorAgent

# ── Tweak these to test different scenarios ───────────────────────────────────
APPROVAL_STATUS = "approved"   # "pending" → blocked, "approved" → executes
URGENCY         = "planned"    # "immediate" | "urgent" | "planned" | "monitor"
ACTION_NAME     = "lubrication_service"  # any action name
PARTS           = [
    RequiredPart(part_number="MOBIL-DTE-25", quantity=1, lead_time_days=0),
    # RequiredPart(part_number="UNKNOWN_PART", quantity=1),  # uncomment → shortage
]
# ─────────────────────────────────────────────────────────────────────────────

rec = MaintenanceRecommendation(
    case_id="DEMO-004",
    asset_id="AST_PMP_001",
    bearing_id="BRG_005",
    recommended_action=RecommendedAction(
        name=ACTION_NAME,
        description=f"{ACTION_NAME} (in_window) per SOP_003",
        estimated_duration_hours=3.0,
    ),
    urgency=URGENCY,
    required_parts=PARTS,
    window_chosen="WIN_005",
    rationale=(
        "Lubrication Issue detected at severity 'stage_2' on bearing BRG_005 "
        "(asset AST_PMP_001). Remaining useful life estimate: ~14 days."
    ),
    approval_status=APPROVAL_STATUS,
    responsible_person="Plant Supervisor – James Kowalski",
    responsible_person_id="PERSONA_SUP",
    responsible_approver="Plant Supervisor – James Kowalski",
    responsible_approver_id="PERSONA_SUP",
    contributors=[
        Contributor(
            role="Maintenance Planner",
            name="Tom Rodriguez",
            concern="confirms parts and crew readiness",
        )
    ],
)

result = ExecutorAgent().process(rec)

print("\n" + "=" * 60)
print("EXECUTION RESULT")
print("=" * 60)
print(result.model_dump_json(indent=2))
