"""
tests/test_recommendation_chat.py
Multi-turn conversational test for recommendation_chat.py.

Produces a REAL recommendation for scenario B4, then asks 8 sequential
questions.  Each answer is appended to history so the next turn has memory.
No LLM routing for the recommendation itself — recommend_action() is called
directly.  The chat LLM is live (Azure OpenAI).

Run from the project root (venv active):
    python tests/test_recommendation_chat.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from dotenv import load_dotenv
load_dotenv()

from agents.prescriptive_optimization_agent import recommend_action
from scenarios import SCENARIO_MAP
from recommendation_chat import answer_about_recommendation

# ---------------------------------------------------------------------------
# Build the real recommendation for B4
# ---------------------------------------------------------------------------
B4_LABEL = "B4 — Lubrication issue, pump stage_2"
scn      = SCENARIO_MAP[B4_LABEL]
diagnosis = scn["diagnosis"]
risk      = scn["risk"]
guidance  = scn["guidance"]

print("Building recommendation for B4...")
rec = recommend_action(diagnosis, risk, guidance)
print(f"  status  : {rec.recommendation_status}")
print(f"  action  : {rec.recommended_action.name}")
print(f"  urgency : {rec.urgency}")
print()

# ---------------------------------------------------------------------------
# 8-question conversation
# ---------------------------------------------------------------------------
QUESTIONS = [
    "What action are you recommending and why?",
    "Why did you choose that maintenance window?",
    "Why is that person the approver?",
    "What alternatives did you consider?",
    "Is the required part actually in stock right now?",
    "What's the hourly downtime cost of this asset?",
    "What would you do if that part were out of stock?",
    "What's the weather today?",
]

SEP   = "=" * 70
HSEP  = "-" * 70

history: list = []

for i, question in enumerate(QUESTIONS, start=1):
    print(f"\n{SEP}")
    print(f"Q{i}: {question}")
    print(SEP)

    result = answer_about_recommendation(
        question=question,
        rec=rec,
        diagnosis=diagnosis,
        risk=risk,
        history=history,
    )

    answer = result["answer"]
    trace  = result["trace"]

    print(f"\nANSWER:\n{answer}")

    if trace:
        print(f"\n{HSEP}")
        print("TOOLS CALLED:")
        for step in trace:
            print(f"  tool : {step['tool']}")
            print(f"  args : {step['args']}")
            print(f"  got  : {step['result'][:200]}{'...' if len(step['result']) > 200 else ''}")
    else:
        print(f"\n{HSEP}")
        print("TOOLS CALLED: none (answered from recommendation / declined)")

    # Append both sides to history so next turn has memory
    history.append({"role": "user",      "content": question})
    history.append({"role": "assistant", "content": answer})

print(f"\n{SEP}")
print("All 8 questions complete.")
print(SEP)
