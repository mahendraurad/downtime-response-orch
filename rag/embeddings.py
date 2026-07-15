"""
Embeddings generator for the Learning and Memory Agent.

Converts learned case text into vector embeddings for storage in FAISS.
Dev: uses sentence-transformers all-MiniLM-L6-v2 (free, runs locally, no API key)
Prod: swap generate_embedding() body to call Azure OpenAI text-embedding-ada-002

The text that gets embedded is built from the case fields that matter most
for future semantic search by the Knowledge Agent:
  fault_mode + bearing_type + root_cause + lessons_learned

This combination gives the best retrieval precision because it captures
both what went wrong and what was learned from it.
"""

import logging
import numpy as np
from typing import List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)

# Load sentence-transformers model once at module level
# all-MiniLM-L6-v2 produces 384-dimensional embeddings
# Fast enough for dev, good enough for semantic search on short case texts
try:
    from sentence_transformers import SentenceTransformer
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    logger.info("Embedding model loaded: all-MiniLM-L6-v2")
except ImportError:
    _model = None
    logger.warning(
        "sentence-transformers not installed. "
        "Run: pip install sentence-transformers"
    )


def build_embedding_text(case: dict) -> str:
    """
    Build embedding text for FAISS storage and query.
    Must be identical structure for both so vectors land
    in the same space.

    Uses only fault_mode + asset_type + bearing_type.
    Normalized: fault and asset lowercased with underscores
    replaced by spaces; bearing uppercased.
    """
    fault = str(case.get("fault_mode", case.get("fault", ""))).strip().lower()
    fault = fault.replace("_", " ")

    asset = str(case.get("asset_type", "")).strip().lower()

    bearing = str(case.get("bearing_type", case.get("bearing", ""))).strip().upper()

    parts = [p for p in (fault, asset, bearing) if p]
    text = " ".join(parts)
    logger.debug("Embedding text built: %s", text[:100])
    return text


def generate_embedding(text: str) -> List[float]:
    """
    Generates a 384-dimensional vector embedding for the given text.
    
    Uses sentence-transformers all-MiniLM-L6-v2 locally in dev.
    Returns a Python list of floats for FAISS storage.
    
    If the model failed to load (import error), returns a zero vector
    as a fallback so the rest of the pipeline still runs.
    """
    if _model is None:
        logger.error(
            "Embedding model not loaded. Returning zero vector as fallback."
        )
        return [0.0] * 384
    
    embedding = _model.encode(text, convert_to_numpy=True)
    logger.debug(
        "Embedding generated | dim=%d | text_length=%d chars",
        len(embedding), len(text)
    )
    return embedding.tolist()


def batch_embed(texts: List[str]) -> List[List[float]]:
    """
    Embeds a list of texts in one batch call.
    More efficient than calling generate_embedding() in a loop.
    Used by load_data.py when loading all 6 seed cases at once.
    """
    if _model is None:
        logger.error("Embedding model not loaded. Returning zero vectors.")
        return [[0.0] * 384 for _ in texts]
    
    embeddings = _model.encode(texts, convert_to_numpy=True)
    logger.info("Batch embedded %d texts", len(texts))
    return embeddings.tolist()
