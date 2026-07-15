"""
tests/test_rag_chat.py
End-to-end test: RAG tool wired into the chat brain (general mode, rec=None).

Confirms:
  - search_documents_tool fires for SOP/procedure/case questions
  - check_inventory_tool still routes for live-data questions
  - out-of-scope questions are still declined
  - answers cite the correct source document

Run from project root (venv active):
    python tests/test_rag_chat.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rag.config import INDEX_PATH, CHUNKS_PATH
from rag.document_store import build_index
from recommendation_chat import answer_about_recommendation

SEP  = "=" * 72
HSEP = "-" * 72

# Ensure index exists
if not Path(INDEX_PATH).exists() or not Path(CHUNKS_PATH).exists():
    print("Index not found — building now…")
    build_index()
    print()

QUESTIONS = [
    (
        "Q1",
        "How do I replace an outer race bearing on a gearbox?",
        "search_documents_tool → answer should cite SOP_005",
    ),
    (
        "Q2",
        "Have we had a gearbox bearing part shortage before? What did we do?",
        "search_documents_tool → answer should reference CASE_004",
    ),
    (
        "Q3",
        "What torque spec is used for the motor bearing end cover bolts?",
        "search_documents_tool → answer should cite SOP_001 with a number",
    ),
    (
        "Q4",
        "Is the part for BRG_005 in stock?",
        "check_inventory_tool (NOT document search)",
    ),
    (
        "Q5",
        "What's the weather today?",
        "out-of-scope decline — no tools",
    ),
]

history: list = []

print(SEP)
print("RAG CHAT TEST — general mode (no recommendation loaded)")
print(SEP)

for label, question, hint in QUESTIONS:
    print(f"\n{SEP}")
    print(f"{label}: {question}")
    print(f"EXPECT: {hint}")
    print(SEP)

    result = answer_about_recommendation(
        question=question,
        rec=None,
        history=history,
    )

    answer = result["answer"]
    trace  = result["trace"]

    print(f"\nANSWER:\n{answer}")

    print(f"\n{HSEP}")
    if trace:
        print("TOOLS CALLED:")
        for step in trace:
            snippet = str(step["result"])[:300]
            ellipsis = "…" if len(str(step["result"])) > 300 else ""
            print(f"  tool : {step['tool']}")
            print(f"  args : {step['args']}")
            print(f"  got  : {snippet}{ellipsis}")
    else:
        print("TOOLS CALLED: none")

    history.append({"role": "user",      "content": question})
    history.append({"role": "assistant", "content": answer})

print(f"\n{SEP}")
print(f"ALL QUESTIONS COMPLETE. History length: {len(history)} entries.")
print(SEP)
