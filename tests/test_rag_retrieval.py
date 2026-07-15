"""
tests/test_rag_retrieval.py
Eyeball test for RAG retrieval quality.

Runs 5 targeted queries and prints top-4 results per query so we can verify
the right document is retrieved.  Builds the index first if missing.

Run from project root (venv active):
    python tests/test_rag_retrieval.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.config import INDEX_PATH, CHUNKS_PATH
from rag.document_store import build_index, search_documents

SEP  = "=" * 72
HSEP = "-" * 72

# Build index if not present
if not Path(INDEX_PATH).exists() or not Path(CHUNKS_PATH).exists():
    print("Index not found — building now…")
    build_index()
    print()

QUERIES = [
    (
        "How do I replace an outer race bearing on a gearbox?",
        "SOP_005",
    ),
    (
        "What lubrication service is needed for a pump bearing?",
        "SOP_003",
    ),
    (
        "Have we had a gearbox bearing part shortage before and what was done?",
        "CASE_004",
    ),
    (
        "Post-repair QA results for a motor bearing replacement?",
        "CASE_001 / SOP_006",
    ),
    (
        "What torque spec for the motor bearing end cover bolts?",
        "SOP_001",
    ),
]

print(SEP)
print("RAG RETRIEVAL TEST — top-4 results per query")
print(SEP)

for query, expected in QUERIES:
    print(f"\nQUERY : {query}")
    print(f"EXPECT: {expected}")
    print(HSEP)

    results = search_documents(query, k=4)
    for rank, r in enumerate(results, start=1):
        snippet = r["text"][:200].replace("\n", " ")
        print(
            f"  #{rank}  {r['source_file']}  |  page {r['page_number']}  "
            f"|  score {r['score']:.4f}"
        )
        print(f"       {snippet}…")
    print()

print(SEP)
print("Test complete.")
print(SEP)
