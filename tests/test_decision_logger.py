"""
Test for agents/decision_logger.py — NOT deleted after run (per spec).
Leaves data/decision_log.json populated for the app.
"""
import os
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from scenarios import SCENARIO_MAP
from agents.prescriptive_optimization_agent import recommend_action
from agents.decision_logger import log_recommendation, get_history

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f"\n       {detail}" if detail else ""))


# --- Run B4 and B2 through the agent ---
b4 = SCENARIO_MAP["B4 - Lubrication issue, pump stage_2"] \
     if "B4 - Lubrication issue, pump stage_2" in SCENARIO_MAP \
     else next(v for k, v in SCENARIO_MAP.items() if k.startswith("B4"))

b2 = next(v for k, v in SCENARIO_MAP.items() if k.startswith("B2"))

print("\nRunning recommend_action for B4...")
rec_b4 = recommend_action(b4["diagnosis"], b4["risk"], b4["guidance"])
print(f"  B4 status={rec_b4.recommendation_status}, action={rec_b4.recommended_action.name}")

print("Running recommend_action for B2...")
rec_b2 = recommend_action(b2["diagnosis"], b2["risk"], b2["guidance"])
print(f"  B2 status={rec_b2.recommendation_status}, action={rec_b2.recommended_action.name}")

# --- Log both ---
print()
log_id_b4 = log_recommendation(rec_b4, b4["diagnosis"], b4["risk"])
log_id_b2 = log_recommendation(rec_b2, b2["diagnosis"], b2["risk"])
print(f"Logged B4 -> {log_id_b4}")
print(f"Logged B2 -> {log_id_b2}")

check("B4 log_id generated (LOG- prefix)", log_id_b4.startswith("LOG-"), log_id_b4)
check("B2 log_id generated (LOG- prefix)", log_id_b2.startswith("LOG-"), log_id_b2)
check("B4 and B2 have distinct log_ids", log_id_b4 != log_id_b2)

# --- Filter: asset_id=AST_MTR_001 should return B2 ---
print()
hist_mtr = get_history(asset_id="AST_MTR_001")
print(f"get_history(asset_id='AST_MTR_001') -> {len(hist_mtr)} entry/entries")
for e in hist_mtr:
    print(f"  {e['log_id']} | {e['asset_id']} | {e['fault_mode']} | {e['recommended_action']}")
check(
    "get_history(asset_id='AST_MTR_001') returns at least 1 entry",
    len(hist_mtr) >= 1,
)
check(
    "get_history(asset_id='AST_MTR_001') entry is for outer_race_fault (B2)",
    any(e["fault_mode"] == "outer_race_fault" for e in hist_mtr),
)

# --- Filter: fault_mode=lubrication_issue should return B4 ---
print()
hist_lub = get_history(fault_mode="lubrication_issue")
print(f"get_history(fault_mode='lubrication_issue') -> {len(hist_lub)} entry/entries")
for e in hist_lub:
    print(f"  {e['log_id']} | {e['asset_id']} | {e['fault_mode']} | {e['recommended_action']}")
check(
    "get_history(fault_mode='lubrication_issue') returns at least 1 entry",
    len(hist_lub) >= 1,
)
check(
    "get_history(fault_mode='lubrication_issue') entry is for AST_PMP_001 (B4)",
    any(e["asset_id"] == "AST_PMP_001" for e in hist_lub),
)

# --- No filters: should return both, most recent first ---
print()
hist_all = get_history()
print(f"get_history() -> {len(hist_all)} total entries")
for e in hist_all:
    print(f"  {e['log_id']} | {e['logged_at_utc'][:19]} | {e['asset_id']} | {e['fault_mode']}")
check(
    "get_history() returns at least 2 entries",
    len(hist_all) >= 2,
)
check(
    "get_history() is sorted most-recent first",
    hist_all[0]["logged_at_utc"] >= hist_all[-1]["logged_at_utc"],
)

# --- Summary ---
print()
passed = sum(1 for s, _, _ in results if s == PASS)
total  = len(results)
print(f"{'ALL TESTS PASSED' if passed == total else 'SOME TESTS FAILED'} ({passed}/{total})")
print(f"data/decision_log.json left in place for the app.")
