"""
Retriever for the Learning and Memory Agent.

This is the component that the Knowledge Agent (Agent 5) and DRO orchestrator call
when they need to find relevant past cases or SOP sections.

RETRIEVAL COST HIERARCHY (manager requirement - be cost efficient):
1. Short-term memory: FREE (in-memory dict lookup)
2. PostgreSQL exact match: CHEAP (database query)
3. Vector semantic search: MODERATE (embedding generation + index search)
4. LLM synthesis: EXPENSIVE (only used if nothing found above)

Always try cheaper tiers first.

CURRENT USAGE STATUS
--------------------
NOTE FOR HANDOVER READER: The Retriever class defined in this file is
currently NOT used by the main pipeline. The Discovery pipeline
(orchestrator/langgraph_flow.py) calls services/knowledge_check.py::
check_existing_knowledge() directly, which uses rag/vector_storage.py
without going through this Retriever class.

This class exists as a thin abstraction layer intended for future use
if the pipeline ever needs multiple retrieval strategies swapped at
runtime. It is imported by services/memory_service.py — which itself
is archived (see archive/fastapi_removed_*/services/memory_service.py).

If you're wiring new code that needs FAISS retrieval, look at
services/knowledge_check.py::check_existing_knowledge() as your
reference — not this class.
"""
import logging
from typing import List, Optional

from rag.embeddings import generate_embedding
from rag.vector_storage import VectorStorage
from schemas.learned_case import LearnedCaseDocument

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(module)s | %(message)s'
)
logger = logging.getLogger(__name__)


class Retriever:
    def __init__(self) -> None:
        self.vector_storage = VectorStorage()

    async def retrieve(self, query_text: str, asset_type: str, fault_mode: str, top_k: int = 3) -> List[LearnedCaseDocument]:
        """
        Main retrieval method. Builds search query from fault_mode + asset_type + bearing_type.
        Searches vector store and returns top_k most relevant learned cases.
        Used by Knowledge Agent to provide context when similar fault occurs.
        Increments access_count on retrieved documents.
        """
        query_embedding = await generate_embedding(query_text)
        results = await self.vector_storage.search(query_embedding, top_k=top_k)
        learned_cases: List[LearnedCaseDocument] = []
        for result in results:
            metadata = result.get("metadata", {})
            try:
                learned_cases.append(LearnedCaseDocument(**metadata.get("case_record", {})))
            except Exception:
                continue
        logger.info("Retriever returned %d cases for asset_type=%s fault_mode=%s", len(learned_cases), asset_type, fault_mode)
        return learned_cases

    async def check_similarity(self, text1: str, text2: str) -> float:
        """
        Gate 2 of the 3-gate deduplication system.
        Generates embeddings for both texts and computes cosine similarity.
        Returns float 0.0 to 1.0.
        If score >= DEDUP_SIMILARITY_THRESHOLD (0.92): treat as duplicate.
        """
        import numpy as np

        embedding1 = await generate_embedding(text1)
        embedding2 = await generate_embedding(text2)
        v1 = np.array(embedding1, dtype="float32")
        v2 = np.array(embedding2, dtype="float32")
        if np.linalg.norm(v1) == 0 or np.linalg.norm(v2) == 0:
            return 0.0
        score = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
        logger.info("Computed similarity score=%.4f", score)
        return score

    async def retrieve_sop(self, fault_mode: str, asset_type: str) -> List[dict]:
        """
        Retrieves the most relevant SOP sections for a given fault mode and asset type.
        Used by Knowledge Agent to get field guidance alongside past cases.
        Returns list of {sop_id, section_title, content, relevance_score}
        """
        query_text = f"{fault_mode} {asset_type} SOP guidance"
        query_embedding = await generate_embedding(query_text)
        results = await self.vector_storage.search(query_embedding, top_k=3)
        return [
            {
                "sop_id": item["metadata"].get("doc_id"),
                "section_title": item["metadata"].get("section_title", "unknown"),
                "content": item["metadata"].get("source_text", ""),
                "relevance_score": item["similarity_score"],
            }
            for item in results
        ]
