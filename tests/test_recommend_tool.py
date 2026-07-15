"""
tests/test_recommend_tool.py
Direct-call smoke test for recommend_action_tool.
No LLM routing — calls tool.invoke() directly, same as the agent would receive.

Run from the project root (venv active):
    python tests/test_recommend_tool.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from dotenv import load_dotenv
load_dotenv()

from tools.langgraph_tools import (
    recommend_action_tool,
    check_inventory_tool,
    find_windows_tool,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

SEP = "=" * 70

def run(label: str, tool, args: dict) -> str:
    result = tool.invoke(args)
    print(f"\n{SEP}")
    print(f"CASE: {label}")
    print(SEP)
    print(result)
    return result

# ---------------------------------------------------------------------------
# recommend_action_tool — scenario coverage
# ---------------------------------------------------------------------------

# B4: normal path — lubrication issue, pump, part in stock
run("B4 (prefix match, normal path)",
    recommend_action_tool, {"scenario_label": "B4"})

# B2: normal path — outer race fault, motor, critical
run("B2 (full label match)",
    recommend_action_tool,
    {"scenario_label": "B2 — Outer race fault, motor stage_3"})

# B1: low-risk, preventive service
run("B1 (healthy motor, routine)",
    recommend_action_tool, {"scenario_label": "b1"})  # lowercase

# A1: guard 1 — unknown asset
run("A1 (guard: blocked_unknown_asset)",
    recommend_action_tool, {"scenario_label": "A1"})

# A2: guard 2 — unreliable diagnosis
run("A2 (guard: unreliable_diagnosis)",
    recommend_action_tool, {"scenario_label": "A2"})

# A4: guard 4 — blocked on part
run("A4 (guard: blocked_no_part)",
    recommend_action_tool, {"scenario_label": "A4"})

# ZZ: invalid label
run("ZZ (invalid label — should list available prefixes)",
    recommend_action_tool, {"scenario_label": "ZZ"})

# ---------------------------------------------------------------------------
# check_inventory_tool — validation
# ---------------------------------------------------------------------------

# BRG_999 is not in any compatible_bearing_ids list
run("check_inventory: unknown bearing BRG_999",
    check_inventory_tool,
    {"bearing_id": "BRG_999", "action_type": "replace_bearing"})

# ---------------------------------------------------------------------------
# find_windows_tool — validation
# ---------------------------------------------------------------------------

# AST_FAKE_001 is not in asset master data
run("find_windows: unknown asset AST_FAKE_001",
    find_windows_tool,
    {"asset_id": "AST_FAKE_001", "required_hours": 2.0})

# ---------------------------------------------------------------------------
print(f"\n{SEP}")
print("All cases complete.")
print(SEP)
