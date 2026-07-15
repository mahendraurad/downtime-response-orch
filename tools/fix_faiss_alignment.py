"""
tools/fix_faiss_alignment.py
One-shot repair for the FAISS/metadata positional mismatch (Cause D).

Rebuilds the FAISS index from scratch by regenerating embeddings for
each entry in metadata.json in key order, so that:
    FAISS position N  →  Nth metadata key

Run from project root:
    python tools/fix_faiss_alignment.py

WHEN TO REACH FOR THIS TOOL
---------------------------
Symptom: The Streamlit app shows an error like "FAISS index has N
vectors but metadata.json has M entries — misalignment detected"
or "Cause D: index/metadata length mismatch".

This happens when:
  - A case was written to disk but the FAISS add step failed
  - metadata.json was manually edited but the index wasn't rebuilt
  - A partial nuclear reset left FAISS in inconsistent state

WHAT THIS TOOL DOES vs REBUILD_INDEX
------------------------------------
  - fix_faiss_alignment (this file) — attempts to reconcile the two
    files by finding the misaligned entries and re-embedding just
    those. Preserves existing vectors.
  - rebuild_index.py — deletes both files and starts fresh from
    disk .json cases. Nuclear option.

Reach for this tool first. If it can't reconcile, use rebuild_index.

USAGE
-----
    .\\venv\\Scripts\\python.exe tools\\fix_faiss_alignment.py

Prints diagnostic report of misaligned entries before making changes.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.vector_storage import VectorStorage


def main():
    sep = "=" * 60
    print(sep)
    print("FAISS Alignment Repair")
    print(sep)

    store = VectorStorage()
    store.load_index()

    before_vectors = store.get_vector_count()
    before_meta = len(store.metadata)
    print(f"\nBEFORE: vectors={before_vectors}  metadata={before_meta}")
    if before_vectors != before_meta:
        print(f"  MISMATCH of {before_vectors - before_meta} orphan vector(s) — will be repaired")
    else:
        print("  No mismatch — index appears healthy (rebuild is safe but not required)")

    print(f"\nMetadata keys ({before_meta}):")
    for i, (k, v) in enumerate(store.metadata.items()):
        print(f"  [{i:2d}] {k:30s}  fault={v.get('fault_mode', '?'):<25}  asset={v.get('asset_type', '?')}")

    print("\nRebuilding FAISS index from metadata...")
    aligned = store.rebuild_from_metadata()

    after_vectors = store.get_vector_count()
    after_meta = len(store.metadata)
    print(f"\nAFTER:  vectors={after_vectors}  metadata={after_meta}  aligned={aligned}")

    if not aligned:
        print("ERROR: rebuild did not produce a fully aligned index. Aborting save.")
        sys.exit(1)

    store.save_index()
    print("\nSaved to disk.")
    print(f"\nDone. {after_vectors} vectors aligned with {after_meta} metadata entries.")
    print(sep)


if __name__ == "__main__":
    main()
