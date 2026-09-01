"""Optional FAISS-backed local SOP retrieval.

FAISS indexes the same deterministic TF-IDF vectors used by the local
retriever. This gives the project a fast local vector index without changing
the Knowledge Agent's metadata boosts or citation output contract.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List

from src.tools.retriever import index_snapshot, knowledge_index_version, query_vector


class FaissRetriever:
    """In-process FAISS index built from the effective SOP corpus."""

    def __init__(self) -> None:
        try:
            import faiss  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "RAG_BACKEND=faiss requires faiss-cpu; install the pinned "
                "FAISS dependency from requirements.txt"
            ) from exc
        self._faiss = faiss
        self._chunks, vectors, _vocab, _idf, _copy = index_snapshot()
        self._index = faiss.IndexFlatIP(vectors.shape[1])
        self._index.add(vectors)

    def version(self) -> str:
        return knowledge_index_version()

    def retrieve(self, query: str, top_k: int = 3, fault_mode: str = "",
                 asset_type: str = "", iso_stage: int = 0,
                 minimum_score: float = 0.45, fault_mode_boost: float = 0.25,
                 asset_type_boost: float = 0.15, iso_stage_boost: float = 0.10,
                 **_: object) -> List[Dict]:
        vector = query_vector(query).reshape(1, -1)
        # Retrieve a wider candidate set before metadata boosts; otherwise a
        # relevant fault/stage match could be omitted before its boost applies.
        candidate_k = min(len(self._chunks), max(top_k * 4, top_k))
        scores, indices = self._index.search(vector, candidate_k)
        ranked = []
        for raw_score, index in zip(scores[0], indices[0]):
            if index < 0:
                continue
            chunk = self._chunks[int(index)]
            score = float(raw_score)
            if fault_mode and chunk.get("fault_mode") == fault_mode:
                score += fault_mode_boost
            if asset_type and chunk.get("asset_type") == asset_type:
                score += asset_type_boost
            if iso_stage and chunk.get("iso_stage") == iso_stage:
                score += iso_stage_boost
            if score < minimum_score:
                continue
            chunk_id = str(chunk.get("chunk_id", "") or "") or hashlib.sha256(
                f"{chunk.get('source', '')}:{chunk.get('text', '')}".encode()
            ).hexdigest()[:16]
            ranked.append({
                "source": chunk.get("source", ""),
                "chunk_id": chunk_id,
                "source_uri": str(chunk.get("source_uri", "") or ""),
                "text": chunk.get("text", ""),
                "score": round(score, 4),
                "fault_mode": chunk.get("fault_mode", ""),
                "asset_type": chunk.get("asset_type", ""),
                "iso_stage": chunk.get("iso_stage", 0),
            })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:top_k]
