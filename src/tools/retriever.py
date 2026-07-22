"""
tools/retriever.py  —  Phase 6

Vector-store interface.  Dev mode: in-memory TF-IDF cosine similarity.

Document sources (merged at startup, in priority order):
  1. Real documents from data/sops/   ← .txt files (always) or .pdf (needs pdfplumber)
  2. Synthetic fallback from sop_catalog.py  ← always included as baseline

Adding real SOPs:
  Option A (simplest, no new packages):
    Save your SOP text as .txt files in data/sops/
    Name the file to hint the content:
      outer_race_motor_stage3_sop.txt
      cage_fault_conveyor_sop.txt
    The loader extracts fault_mode / asset_type / iso_stage from the filename.
    Restart the server — the files are auto-detected at startup.

  Option B (PDF):
    pip install pdfplumber
    Save .pdf files in data/sops/
    Same filename convention applies.
    Restart the server.

The Knowledge Agent never changes regardless of which option is used.
"""
from __future__ import annotations

import logging
import os
import re
from typing import List, Dict

import numpy as np

from src.tools.sop_catalog import SOP_CHUNKS

logger = logging.getLogger(__name__)

# ── Real-document loader ──────────────────────────────────────────────────────

_SOPS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "sops"
)

# Filename keyword → metadata field mapping
_FAULT_KEYWORDS = {
    "outer_race": "outer_race_fault",
    "inner_race": "inner_race_fault",
    "cage":       "cage_fault",
    "lube":       "lubrication_issue",
    "lubrication":"lubrication_issue",
    "gearbox":    "gearbox_fault",
}
_ASSET_KEYWORDS = {
    "motor":    "motor",
    "pump":     "pump",
    "conveyor": "conveyor",
    "gearbox":  "gearbox",
}
_STAGE_RE = re.compile(r"stage(\d)", re.IGNORECASE)


def _metadata_from_filename(fname: str) -> Dict:
    """Infer fault_mode, asset_type, iso_stage from the filename."""
    base = fname.lower().replace("-", "_")
    fault_mode = next((v for k, v in _FAULT_KEYWORDS.items() if k in base), "")
    asset_type = next((v for k, v in _ASSET_KEYWORDS.items() if k in base), "")
    m = _STAGE_RE.search(base)
    iso_stage = int(m.group(1)) if m else 0
    return {"fault_mode": fault_mode, "asset_type": asset_type, "iso_stage": iso_stage}


def _chunk_text(text: str, source: str, chunk_size: int = 600,
                overlap: int = 100, meta: Dict = None) -> List[Dict]:
    """Split text into overlapping chunks of ~chunk_size characters."""
    meta = meta or {}
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        snippet = text[start:end].strip()
        if snippet:
            chunks.append({
                "source":     source,
                "fault_mode": meta.get("fault_mode", ""),
                "asset_type": meta.get("asset_type", ""),
                "iso_stage":  meta.get("iso_stage", 0),
                "text":       snippet,
            })
        start += chunk_size - overlap
    return chunks


def _load_txt(path: str, source: str, meta: Dict) -> List[Dict]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return _chunk_text(fh.read(), source, meta=meta)


def _load_pdf(path: str, source: str, meta: Dict) -> List[Dict]:
    try:
        import pdfplumber  # type: ignore
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return _chunk_text("\n".join(text_parts), source, meta=meta)
    except ImportError:
        logger.warning(
            "pdfplumber not installed — skipping PDF '%s'. "
            "Install with: pip install pdfplumber", source
        )
        return []
    except Exception as exc:
        logger.warning("Failed to parse PDF '%s': %s", source, exc)
        return []


def load_real_documents(sops_dir: str = _SOPS_DIR) -> List[Dict]:
    """
    Scan data/sops/ for .txt and .pdf files and return chunks.
    Returns an empty list (silently) if the directory has no documents.
    """
    if not os.path.isdir(sops_dir):
        return []

    chunks: List[Dict] = []
    for fname in sorted(os.listdir(sops_dir)):
        if fname.startswith("."):
            continue  # skip .gitkeep
        path = os.path.join(sops_dir, fname)
        meta = _metadata_from_filename(fname)
        if fname.lower().endswith(".txt"):
            new = _load_txt(path, fname, meta)
            chunks.extend(new)
            if new:
                logger.info("Loaded %d chunks from %s", len(new), fname)
        elif fname.lower().endswith(".pdf"):
            new = _load_pdf(path, fname, meta)
            chunks.extend(new)
            if new:
                logger.info("Loaded %d chunks from PDF %s", len(new), fname)
    return chunks


# ── Active chunk list (synthetic + real documents merged) ─────────────────────

def _build_chunk_list() -> List[Dict]:
    """Merge real documents (higher priority) with synthetic fallback chunks."""
    real = load_real_documents()
    if real:
        # Keep synthetic chunks only for fault modes not covered by real docs
        real_faults = {c["fault_mode"] for c in real if c["fault_mode"]}
        synthetic = [c for c in SOP_CHUNKS if c["fault_mode"] not in real_faults]
        merged = real + synthetic
        logger.info(
            "Knowledge index: %d real chunks + %d synthetic fallback chunks",
            len(real), len(synthetic),
        )
    else:
        merged = list(SOP_CHUNKS)
        logger.info(
            "Knowledge index: no real documents found in data/sops/ — "
            "using %d synthetic chunks", len(merged)
        )
    return merged


# ── TF-IDF index ──────────────────────────────────────────────────────────────

_chunks: List[Dict] = []
_vocab: List[str] = []
_idf: np.ndarray = np.array([])
_doc_vecs: np.ndarray = np.array([])
_index_built: bool = False


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def build_index(chunks: List[Dict] = None) -> None:
    """Build (or rebuild) the in-memory TF-IDF index."""
    global _chunks, _vocab, _idf, _doc_vecs, _index_built

    _chunks = chunks if chunks is not None else _build_chunk_list()

    all_tokens: set = set()
    for doc in _chunks:
        all_tokens.update(_tokenize(doc["text"]))
    _vocab = sorted(all_tokens)
    v2i = {t: i for i, t in enumerate(_vocab)}
    n_docs = len(_chunks)
    V = len(_vocab)

    tf = np.zeros((n_docs, V), dtype=np.float32)
    for d, doc in enumerate(_chunks):
        tokens = _tokenize(doc["text"])
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


def retrieve(query: str, top_k: int = 3,
             fault_mode: str = "",
             asset_type: str = "",
             iso_stage: int = 0,
             minimum_score: float = 0.45,
             fault_mode_boost: float = 0.25,
             asset_type_boost: float = 0.15,
             iso_stage_boost: float = 0.10) -> List[Dict]:
    """
    Return top_k SOP passages most similar to query.
    Works identically whether index was built from real docs or synthetic fallback.

    Each result: {"source": "...", "text": "...", "score": float}
    """
    if not _index_built:
        build_index()

    v2i = {t: i for i, t in enumerate(_vocab)}
    q_tokens = _tokenize(query)
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

    # Metadata boosting — uses _chunks (works for both real and synthetic docs)
    for i, chunk in enumerate(_chunks):
        if fault_mode and chunk.get("fault_mode") == fault_mode:
            scores[i] += fault_mode_boost
        if asset_type and chunk.get("asset_type") == asset_type:
            scores[i] += asset_type_boost
        if iso_stage and chunk.get("iso_stage") == iso_stage:
            scores[i] += iso_stage_boost

    top_idx = np.argsort(scores)[::-1][:top_k]
    results = []
    for idx in top_idx:
        if float(scores[idx]) < minimum_score:
            break
        chunk = _chunks[int(idx)]
        results.append({
            "source": chunk["source"],
            "text":   chunk["text"],
            "score":  round(float(scores[idx]), 4),
        })

    return results


def knowledge_index_version() -> str:
    """Return a stable identifier for the current in-memory knowledge index."""
    import hashlib
    import json
    if not _index_built:
        build_index()
    sources = sorted({c.get("source", "") for c in _chunks})
    payload = json.dumps({"n_chunks": len(_chunks), "sources": sources},
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# Build index eagerly on import so first call is fast
build_index()
