"""
tools/lma_case_store.py  —  Phase 10

File-based JSON case store for the Learning & Memory Agent.
Provides TF-IDF cosine similarity search over learned case content,
using the same numpy-only approach as src/tools/retriever.py (no FAISS).

Layout:
  data/learned_cases/<CASE_ID>.json
    { case_id, fault_mode, asset_type, bearing_type, iso_stage,
      outcome, content, tags, created_at }

Thread-safety: in-memory registry is rebuilt on first access and after
each save_case() call.  Suitable for single-process dev servers.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_CASES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "learned_cases"
)

# ── In-memory registry ────────────────────────────────────────────────────────

_REGISTRY: List[Dict] = []
_REGISTRY_LOADED: bool = False

# TF-IDF index (rebuilt whenever registry changes)
_vocab: List[str] = []
_idf: np.ndarray = np.array([])
_doc_vecs: np.ndarray = np.array([])
_index_built: bool = False


def _load_registry() -> None:
    global _REGISTRY, _REGISTRY_LOADED
    if _REGISTRY_LOADED:
        return
    _REGISTRY = []
    if not os.path.isdir(_CASES_DIR):
        os.makedirs(_CASES_DIR, exist_ok=True)
        _REGISTRY_LOADED = True
        return
    for fname in sorted(os.listdir(_CASES_DIR)):
        if not fname.lower().endswith(".json"):
            continue
        path = os.path.join(_CASES_DIR, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            _REGISTRY.append(doc)
        except Exception as exc:
            logger.warning("Failed to load learned case '%s': %s", fname, exc)
    logger.info("[lma_store] Loaded %d learned cases from %s", len(_REGISTRY), _CASES_DIR)
    _REGISTRY_LOADED = True


def _invalidate_index() -> None:
    global _index_built
    _index_built = False


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_tfidf_index() -> None:
    global _vocab, _idf, _doc_vecs, _index_built
    if not _REGISTRY:
        _index_built = True
        return

    all_tokens: set = set()
    for doc in _REGISTRY:
        all_tokens.update(_tokenize(doc.get("content", "")))
    _vocab = sorted(all_tokens)
    v2i = {t: i for i, t in enumerate(_vocab)}
    n_docs = len(_REGISTRY)
    V = len(_vocab)

    tf = np.zeros((n_docs, V), dtype=np.float32)
    for d, doc in enumerate(_REGISTRY):
        tokens = _tokenize(doc.get("content", ""))
        for t in tokens:
            if t in v2i:
                tf[d, v2i[t]] += 1.0
        if tokens:
            tf[d] /= len(tokens)

    df = (tf > 0).sum(axis=0).astype(np.float32)
    _idf = np.log((n_docs + 1) / (df + 1)) + 1.0

    _doc_vecs = tf * _idf
    norms = np.linalg.norm(_doc_vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    _doc_vecs = _doc_vecs / norms
    _index_built = True


def _ensure_ready() -> None:
    _load_registry()
    if not _index_built:
        _build_tfidf_index()


# ── Public API ────────────────────────────────────────────────────────────────

def search(
    content_query: str,
    fault_mode: str = "",
    asset_type: str = "",
    top_k: int = 3,
) -> List[Dict]:
    """
    Search learned cases by TF-IDF cosine similarity with metadata boosting.

    Returns a list of dicts sorted by descending score:
      {"case_id": str, "score": float, "doc": dict}
    """
    _ensure_ready()
    if not _REGISTRY or not _vocab:
        return []

    v2i = {t: i for i, t in enumerate(_vocab)}
    q_tokens = _tokenize(content_query)
    q_vec = np.zeros(len(_vocab), dtype=np.float32)
    for t in q_tokens:
        if t in v2i:
            q_vec[v2i[t]] += 1.0
    if q_tokens:
        q_vec /= len(q_tokens)
    q_vec = q_vec * _idf
    norm = np.linalg.norm(q_vec)
    if norm > 0:
        q_vec /= norm

    scores = _doc_vecs @ q_vec

    for i, doc in enumerate(_REGISTRY):
        if fault_mode and doc.get("fault_mode") == fault_mode:
            scores[i] += 0.25
        if asset_type and doc.get("asset_type") == asset_type:
            scores[i] += 0.15

    top_idx = np.argsort(scores)[::-1][:top_k]
    results = []
    for idx in top_idx:
        sc = float(scores[idx])
        if sc < 0.05:
            break
        results.append({
            "case_id": _REGISTRY[int(idx)].get("case_id", ""),
            "score":   round(sc, 4),
            "doc":     _REGISTRY[int(idx)],
        })
    return results


def save_case(doc: Dict) -> str:
    """
    Persist a new learned case to disk and add it to the in-memory registry.
    Mints a new CASE_NNN id if doc["case_id"] is empty or already exists.
    Returns the final case_id.
    """
    _load_registry()
    existing_ids = {d.get("case_id", "") for d in _REGISTRY}

    cid = doc.get("case_id", "")
    if not cid or cid in existing_ids:
        n = len(_REGISTRY) + 1
        cid = f"CASE_{n:03d}"
        while cid in existing_ids:
            n += 1
            cid = f"CASE_{n:03d}"
        doc["case_id"] = cid

    if not doc.get("created_at"):
        doc["created_at"] = datetime.now(timezone.utc).isoformat()

    os.makedirs(_CASES_DIR, exist_ok=True)
    path = os.path.join(_CASES_DIR, f"{cid}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)

    _REGISTRY.append(doc)
    _invalidate_index()
    logger.info("[lma_store] Saved new case %s → %s", cid, path)
    return cid


def get_all_cases() -> List[Dict]:
    """Return all learned cases (shallow copy)."""
    _load_registry()
    return list(_REGISTRY)


def get_case(case_id: str) -> Optional[Dict]:
    """Return a single case by ID, or None if not found."""
    _load_registry()
    for doc in _REGISTRY:
        if doc.get("case_id") == case_id:
            return doc
    return None


def reload() -> None:
    """Force-reload from disk (useful in tests or after external writes)."""
    global _REGISTRY_LOADED
    _REGISTRY_LOADED = False
    _invalidate_index()
    _load_registry()
