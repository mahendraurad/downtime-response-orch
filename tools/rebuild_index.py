"""
Rebuild FAISS Index — Utility Script
=====================================

WHAT THIS FILE DOES
-------------------
Rebuilds the FAISS vector index from scratch by reading every JSON case
document in data/learned_cases/ and generating a fresh embedding for
each one. Writes:
  - data/faiss_index/index.faiss    (binary FAISS index)
  - data/faiss_index/metadata.json  (per-case metadata sidecar)

WHEN TO RUN THIS
----------------
  1. Nuclear reset — after wiping data/faiss_index/ to return to a
     pristine baseline (see README Section 3.5)
  2. After manually adding case JSON files to data/learned_cases/ that
     were not written through the app
  3. When FAISS ntotal is out of sync with metadata length (before
     resorting to this, try tools/fix_faiss_alignment.py)
  4. After changing the embedding model in rag/embeddings.py — the
     old vectors are stale and must be regenerated

WARNING — NOT IDEMPOTENT
------------------------
This script DELETES the existing index files first, then rebuilds
from disk .json files. Any vectors present in FAISS that don't have
a corresponding .json file on disk will be LOST. If you want to
preserve current FAISS state and only add missing entries, use the
alignment tool instead.

WHAT IT USES
------------
Third-party libraries:
  - faiss    — index construction (IndexFlatL2, 384 dimensions)
  - numpy    — vector array manipulation

Local modules:
  - rag.embeddings         — generate_embedding, build_embedding_text
  - rag.vector_storage     — VectorStorage helper class

TECHNIQUES APPLIED
------------------
  - Batch embedding generation (all cases embedded before write)
  - Positional alignment between FAISS vectors and metadata entries

USAGE
-----
    .\\venv\\Scripts\\python.exe tools\\rebuild_index.py

Runs in ~30 seconds on a laptop for 6 seed cases. Scales linearly
with case count.

CALLED BY
---------
Manually via command line only. Not imported by any runtime code.
"""
import sys
import os
import re
import json
import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rag.vector_storage import VectorStorage
from rag.embeddings import generate_embedding, build_embedding_text


def _natural_key(case_id: str) -> str:
    """Pad digit runs so 'CASE_001' < 'CASE_0010' < 'CASE_002'."""
    return re.sub(r"\d+", lambda m: m.group().zfill(8), case_id)


def main():
    # Wipe existing index
    for path in ("data/faiss_index/index.faiss",
                 "data/faiss_index/metadata.json"):
        if os.path.exists(path):
            os.remove(path)
            print(f"Removed: {path}")

    vs = VectorStorage()
    vs.load_index()  # creates fresh IndexFlatL2 since files were deleted

    raw_files = glob.glob("data/learned_cases/*.json")
    if not raw_files:
        print("No JSON files found in data/learned_cases/")
        return

    # Sort by case_id from JSON content using natural (numeric-aware) order
    def _sort_key(fp):
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                cid = json.load(fh).get("case_id", fp)
            return _natural_key(cid)
        except Exception:
            return fp

    case_files = sorted(raw_files, key=_sort_key)

    count = 0
    skipped = 0
    failed = 0

    for fpath in case_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                case = json.load(f)

            case_id = case.get("case_id", "")
            if not case_id:
                print(f"  SKIP (no case_id): {fpath}")
                skipped += 1
                continue

            if "DEMO" in case_id.upper():
                print(f"  SKIP (demo):       {case_id}")
                skipped += 1
                continue

            text = build_embedding_text(case)
            embedding = generate_embedding(text)

            meta = {
                "case_id":         case_id,
                "fault_mode":      case.get("fault_mode", ""),
                "asset_type":      case.get("asset_type", ""),
                "bearing_type":    case.get("bearing_type", ""),
                "root_cause":      case.get("root_cause", "unknown"),
                "lessons_learned": case.get("lessons_learned", ""),
                "action_taken":    case.get("action_taken", ""),
                "result":          case.get("result", ""),
                "valid_until":     case.get("valid_until", ""),
                "created_by":      case.get("created_by", "seed_data"),
                "source":          "seed_data",
            }

            vs.add(embedding, meta, case_id)
            count += 1
            print(f"  Indexed: {case_id:12}  "
                  f"fault={meta['fault_mode']:20}  "
                  f"text={text!r}")

        except Exception as e:
            print(f"  FAILED {fpath}: {e}")
            failed += 1

    vs.save_index()
    total = vs.index.ntotal if vs.index else 0
    print(f"\nDone. {count} indexed, {skipped} skipped, {failed} failed.")
    print(f"FAISS index now contains {total} vectors.")

    # Verification
    import numpy as np
    print("\n--- Verification ---")
    checks = [
        ("outer_race_fault", "motor",   "SKF6310",  "CASE_001"),
        ("lubrication issue","pump",    "SKF6208",  "CASE_002"),
        ("inner_race_fault", "motor",   "SKF6310",  "CASE_003"),
        ("cage fault",       "conveyor","SKF22212", "CASE_004"),
        ("healthy",          "motor",   "SKF6310",  "CASE_005"),
        ("sensor fault",     "conveyor","SKF22212", "CASE_006"),
    ]
    with open("data/faiss_index/metadata.json") as fj:
        meta_dict = json.load(fj)
    meta_list = list(meta_dict.values())

    passed = 0
    for fault, asset, bearing, expected in checks:
        q = {"fault_mode": fault, "asset_type": asset, "bearing_type": bearing}
        q_text = build_embedding_text(q)
        q_emb = np.array([generate_embedding(q_text)], dtype="float32")
        dists, idxs = vs.index.search(q_emb, 1)
        top_idx = int(idxs[0][0])
        top_id = meta_list[top_idx].get("case_id", "?") if top_idx < len(meta_list) else "?"
        status = "OK" if top_id == expected else "FAIL"
        if status == "OK":
            passed += 1
        print(f"  {status}  {expected:12} -> got {top_id}  (dist={dists[0][0]:.4f})  text={q_text!r}")

    print(f"\n{passed}/{len(checks)} verification checks passed.")


if __name__ == "__main__":
    main()
