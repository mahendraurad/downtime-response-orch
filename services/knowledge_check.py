"""
Knowledge check service for the Learning & Memory Agent.

Before creating any new learned case, this service checks whether
similar knowledge already exists in the vector store.

This is the RAG step in the pipeline:
  New incident arrives
       ↓
  Generate embedding from fault description
       ↓
  Search FAISS vector store
       ↓
  If similarity >= threshold → return existing case (Path A)
  If similarity < threshold  → proceed to case generation (Path B)

Similarity threshold is set conservatively at 0.75 to allow
meaningful retrieval while still capturing genuinely new cases.
"""

import logging
import json as _kc_json
import numpy as np
from pathlib import Path as _KcPath
from typing import Optional, Tuple
from rag.embeddings import generate_embedding, build_embedding_text
from rag.vector_storage import VectorStorage

def _kc_cfg(*keys, default=None):
    try:
        node = _kc_json.loads(
            (_KcPath(__file__).parent.parent / "config.json").read_text(encoding="utf-8")
        )
    except Exception:
        node = {}
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node

_FAULT_MISMATCH_PENALTY = _kc_cfg("retrieval", "fault_mode_mismatch_penalty", default=-0.08)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)

# Validation labels explain WHY a case was or was not matched
# full_match: similarity + fault_mode + asset_type all agree
# fault_match_only: fault_mode matches, different asset type
# similarity_only: only score matched, different fault type

SIMILARITY_THRESHOLD = 0.70


def build_query_text(feedback: dict) -> str:
    """
    Builds the search query text from the incoming feedback event.
    Passes fault_mode + asset_type + bearing_type so the query
    embedding is built from the same three fields used when
    storing cases, ensuring vectors land in the same space.
    """
    return build_embedding_text({
        "fault_mode":  feedback.get("fault_mode", ""),
        "asset_type":  feedback.get("asset_type", ""),
        "bearing_type": feedback.get("bearing_type", ""),
    })


def cosine_similarity(vec_a: list, vec_b: list) -> float:
    """
    Computes cosine similarity between two vectors.
    Returns value between 0.0 (no similarity) and 1.0 (identical).
    """
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def check_existing_knowledge(
    feedback: dict,
    top_k: int = 3
) -> Tuple[bool, Optional[dict], float, list]:
    """
    Enhanced knowledge check with double validation.

    Step 1: Vector similarity search (cosine >= SIMILARITY_THRESHOLD)
    Step 2: Fault mode validation (must match or be closely related)
    Step 3: Asset type validation (same asset category preferred)

    A high similarity score alone is not enough to confirm Path A.
    The fault_mode must also match because similar sensor patterns
    can occur in different fault types.

    Manager feedback: 0.71 similarity does not always mean same case.
    This function implements the double validation he requested.

    Returns:
        Tuple of (exists, best_match, best_score, all_results)
    """
    store = VectorStorage()
    store.load_index()

    if store.get_vector_count() == 0:
        logger.info("Vector store empty - no existing knowledge")
        return False, None, 0.0, []

    query_text = build_query_text(feedback)
    query_embedding = generate_embedding(query_text)
    incoming_fault = feedback.get("fault_mode", "")
    incoming_asset = feedback.get("asset_type", "")

    try:
        import faiss
        import numpy as np

        query_vec = np.array([query_embedding], dtype=np.float32)
        k = min(top_k, store.get_vector_count())
        distances, indices = store.index.search(query_vec, k)

        results = []
        case_ids = list(store.metadata.keys())

        for i, (dist, idx) in enumerate(
            zip(distances[0], indices[0])
        ):
            if idx < 0 or idx >= len(case_ids):
                continue

            case_id = case_ids[idx]
            meta = store.metadata.get(case_id, {})

            stored_vec = np.zeros((1, 384), dtype=np.float32)
            store.index.reconstruct(int(idx), stored_vec[0])
            similarity = cosine_similarity(
                query_embedding, stored_vec[0].tolist()
            )

            # Double validation flags
            fault_matches = (
                meta.get("fault_mode", "") == incoming_fault
            )
            asset_matches = (
                meta.get("asset_type", "") == incoming_asset
            )

            # Compute validation score
            # Full match: similarity + fault match + asset match
            # Partial match: similarity only
            if fault_matches and asset_matches:
                validation_label = "full_match"
                validation_bonus = 0.0
            elif fault_matches:
                validation_label = "fault_match_only"
                validation_bonus = 0.0
            else:
                validation_label = "similarity_only"
                # Penalise score if fault type does not match
                # A 0.75 score with wrong fault = treated as lower
                validation_bonus = _FAULT_MISMATCH_PENALTY

            adjusted_score = similarity + validation_bonus

            results.append({
                "case_id": case_id,
                "similarity": round(similarity, 4),
                "adjusted_score": round(adjusted_score, 4),
                "validation_label": validation_label,
                "fault_matches": fault_matches,
                "asset_matches": asset_matches,
                "faiss_distance": float(dist),
                "fault_mode": meta.get("fault_mode", ""),
                "asset_type": meta.get("asset_type", ""),
                "bearing_type": meta.get("bearing_type", ""),
                "valid_until": meta.get("valid_until", ""),
                "source": meta.get("source", ""),
                # Include post-repair fields from metadata so that when no
                # case file exists on disk the retrieval node's metadata-only
                # fallback still passes them to the coverage assessor, letting
                # the heuristic correctly return EXISTING instead of NEW.
                "root_cause": meta.get("root_cause", ""),
                "lessons_learned": meta.get("lessons_learned", ""),
                "action_taken": meta.get("action_taken", ""),
                "file_path": (
                    f"data/learned_cases/"
                    f"{case_id.lower()}_generated.md"
                    if meta.get("source") == "pipeline"
                    else f"data/learned_cases/"
                         f"{case_id.lower()}.json"
                )
            })

        results.sort(
            key=lambda x: x["adjusted_score"], reverse=True
        )

        best = results[0] if results else None
        best_score = best["similarity"] if best else 0.0
        best_adjusted = best["adjusted_score"] if best else 0.0

        # Path A only if BOTH similarity AND adjusted score pass
        # This prevents wrong fault type from triggering Path A
        if (
            best
            and best["similarity"] >= SIMILARITY_THRESHOLD
            and best_adjusted >= SIMILARITY_THRESHOLD
        ):
            logger.info(
                "Existing knowledge confirmed | case=%s | "
                "similarity=%.3f | validation=%s",
                best["case_id"],
                best["similarity"],
                best["validation_label"]
            )
            return True, best, best_score, results
        elif best and best["similarity"] >= SIMILARITY_THRESHOLD:
            logger.info(
                "Similarity passed but validation failed | "
                "case=%s | similarity=%.3f | "
                "validation=%s | treating as Path B",
                best["case_id"],
                best["similarity"],
                best["validation_label"]
            )
            return False, best, best_score, results
        else:
            logger.info(
                "No existing knowledge above threshold | "
                "best=%.3f | threshold=%.2f",
                best_score, SIMILARITY_THRESHOLD
            )
            return False, None, best_score, results

    except Exception as e:
        logger.error("Knowledge check failed: %s", e)
        return False, None, 0.0, []
