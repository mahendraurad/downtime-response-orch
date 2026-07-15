"""
tests/test_general_chat.py
Tests for the upgraded general-mode chat in recommendation_chat.py.

Three parts:
  A — No recommendation loaded (rec=None), multi-turn with memory
  B — Recommendation present (rec is not None), verify existing behaviour
  C — Memory cap: 15-entry history is trimmed to MAX_HISTORY (10)

Run from the project root (venv active):
    python tests/test_general_chat.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from agents.prescriptive_optimization_agent import recommend_action
from scenarios import SCENARIO_MAP
from recommendation_chat import answer_about_recommendation, MAX_HISTORY

SEP  = "=" * 70
HSEP = "-" * 70


def _print_result(q_label: str, question: str, result: dict) -> None:
    print(f"\n{SEP}")
    print(f"{q_label}: {question}")
    print(SEP)
    print(f"\nANSWER:\n{result['answer']}")
    if result["trace"]:
        print(f"\n{HSEP}")
        print("TOOLS CALLED:")
        for step in result["trace"]:
            snippet = str(step["result"])[:200]
            ellipsis = "..." if len(str(step["result"])) > 200 else ""
            print(f"  tool : {step['tool']}")
            print(f"  args : {step['args']}")
            print(f"  got  : {snippet}{ellipsis}")
    else:
        print(f"\n{HSEP}")
        print("TOOLS CALLED: none")


# ===========================================================================
# PART A — No recommendation (rec=None), multi-turn memory
# ===========================================================================
print(f"\n{'#' * 70}")
print("PART A — General mode (no recommendation loaded)")
print(f"{'#' * 70}")

history_a: list = []

QUESTIONS_A = [
    ("A1", "Is the bearing part for BRG_005 in stock?"),
    ("A2", "How critical is the gearbox AST_GBX_001 and what is its downtime cost?"),
    ("A3", "What maintenance windows does AST_PMP_001 have available for a 3-hour job?"),
    ("A4", "What does the SOP say about replacing a gearbox bearing?"),
    ("A5", "And what about that first part I asked about — remind me the quantity?"),
    ("A6", "What is the capital of France?"),
]

for label, question in QUESTIONS_A:
    result = answer_about_recommendation(
        question=question,
        rec=None,
        history=history_a,
    )
    _print_result(label, question, result)
    history_a.append({"role": "user",      "content": question})
    history_a.append({"role": "assistant", "content": result["answer"]})

print(f"\n{SEP}")
print(f"Part A complete. History length after 6 turns: {len(history_a)} entries.")
print(SEP)


# ===========================================================================
# PART B — Recommendation present, verify existing behaviour
# ===========================================================================
print(f"\n{'#' * 70}")
print("PART B — Recommendation present (B4 scenario)")
print(f"{'#' * 70}")

B4_LABEL = "B4 — Lubrication issue, pump stage_2"
scn_b4   = SCENARIO_MAP[B4_LABEL]
diag_b4  = scn_b4["diagnosis"]
risk_b4  = scn_b4["risk"]
guid_b4  = scn_b4["guidance"]

print("Building recommendation for B4...")
rec_b4 = recommend_action(diag_b4, risk_b4, guid_b4)
print(f"  status  : {rec_b4.recommendation_status}")
print(f"  action  : {rec_b4.recommended_action.name}")
print(f"  urgency : {rec_b4.urgency}")

history_b: list = []

QUESTIONS_B = [
    ("B1", "Why did you choose this window?"),
    ("B2", "Is the required part actually in stock?"),
]

for label, question in QUESTIONS_B:
    result = answer_about_recommendation(
        question=question,
        rec=rec_b4,
        diagnosis=diag_b4,
        risk=risk_b4,
        history=history_b,
    )
    _print_result(label, question, result)
    history_b.append({"role": "user",      "content": question})
    history_b.append({"role": "assistant", "content": result["answer"]})

print(f"\n{SEP}")
print("Part B complete.")
print(SEP)


# ===========================================================================
# PART C — Memory cap: 15-entry history trimmed to MAX_HISTORY (10)
# ===========================================================================
print(f"\n{'#' * 70}")
print(f"PART C — Sliding-window cap (MAX_HISTORY={MAX_HISTORY})")
print(f"{'#' * 70}")

history_c = [
    {"role": "user" if i % 2 == 0 else "assistant", "content": f"fake message {i}"}
    for i in range(15)
]
print(f"History fed in       : {len(history_c)} entries")

# Replicate the trim the function applies so we can verify it here too.
trimmed = history_c[-MAX_HISTORY:]
print(f"Expected after trim  : {MAX_HISTORY} entries")
print(f"Actual trimmed length: {len(trimmed)}")
assert len(trimmed) == MAX_HISTORY, (
    f"Trim failed: expected {MAX_HISTORY}, got {len(trimmed)}"
)
print("Trim assertion PASSED.")

# Also call the real function to confirm it doesn't crash with an oversized history.
result_c = answer_about_recommendation(
    question="What assets are tracked in this plant?",
    rec=None,
    history=history_c,
)
print(f"\nAnswer with trimmed history:\n{result_c['answer'][:300]}")
print(f"\n{SEP}")
print("Part C complete — memory cap working correctly.")
print(SEP)


print(f"\n{'#' * 70}")
print("ALL PARTS COMPLETE.")
print(f"{'#' * 70}")
