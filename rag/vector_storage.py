"""
Vector storage for the Learning and Memory Agent.

Stores case embeddings in a FAISS index at data/faiss_index/.
Also stores metadata (case_id, valid_until, timestamps, etc.) as JSON
at data/faiss_index/metadata.json.

FAISS index (index.faiss): stores the vectors, indexed by integer position
Metadata (metadata.json): stores CaseMetadata for each vector by case_id

The integer position in FAISS corresponds to the order of insertion.
Metadata maps case_id -> position so we can look up metadata by case_id.

Dev: FAISS local files only
Prod: swap to Azure AI Search by replacing add() and save_index()
"""

import os
import json
import logging
import numpy as np
from typing import List, Dict
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("faiss-cpu not installed. Run: pip install faiss-cpu")

INDEX_PATH = "data/faiss_index/index.faiss"
METADATA_PATH = "data/faiss_index/metadata.json"
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension


class VectorStorage:
    """
    Manages the FAISS vector index and associated metadata.
    
    Usage:
        store = VectorStorage()
        store.load_index()
        store.add(embedding, metadata_dict, "CASE_001")
        store.save_index()
    """
    
    def __init__(self):
        """Initialises empty index and metadata dict."""
        self.index = None
        # metadata dict: {case_id: CaseMetadata dict}
        self.metadata: Dict[str, dict] = {}
        self._ensure_dirs()
    
    def _ensure_dirs(self):
        """Creates data/faiss_index/ directory if it does not exist."""
        os.makedirs("data/faiss_index", exist_ok=True)
    
    def load_index(self):
        """
        Loads the FAISS index from disk.
        If no index exists yet, creates a fresh empty IndexFlatL2.
        Called once at startup before any add() calls.
        """
        if not FAISS_AVAILABLE:
            logger.error("FAISS not available. Cannot load index.")
            return
        
        if os.path.exists(INDEX_PATH):
            self.index = faiss.read_index(INDEX_PATH)
            logger.info(
                "FAISS index loaded from %s | vectors=%d",
                INDEX_PATH, self.index.ntotal
            )
        else:
            # IndexFlatL2 is exact nearest-neighbour search
            # Suitable for dev with small number of cases (< 10,000)
            # For production scale: swap to IndexIVFFlat or IndexHNSW
            self.index = faiss.IndexFlatL2(EMBEDDING_DIM)
            logger.info(
                "No existing index found. Created fresh IndexFlatL2 | dim=%d",
                EMBEDDING_DIM
            )
        
        # Load metadata
        if os.path.exists(METADATA_PATH):
            with open(METADATA_PATH, "r") as f:
                self.metadata = json.load(f)
            logger.info(
                "Metadata loaded from %s | records=%d",
                METADATA_PATH, len(self.metadata)
            )
        else:
            self.metadata = {}
            logger.info("No existing metadata found. Starting fresh.")

        # Sanity check: FAISS vector count must equal metadata count.
        # A mismatch means positional lookups (case_ids[idx]) will map
        # vectors to the wrong cases — run tools/fix_faiss_alignment.py.
        if self.index is not None and self.index.ntotal != len(self.metadata):
            logger.warning(
                "FAISS/metadata mismatch detected! vectors=%d metadata=%d "
                "— run tools/fix_faiss_alignment.py to repair.",
                self.index.ntotal, len(self.metadata)
            )
    
    def add(self, embedding: List[float], metadata: dict, case_id: str) -> bool:
        """
        Adds one vector and its metadata to the index.

        Args:
            embedding: 384-dimensional float list from generate_embedding()
            metadata: CaseMetadata dict (case_id, timestamps, valid_until, etc.)
            case_id: string ID used as key in metadata dict

        Returns:
            True if stored successfully, False if error.

        Note: If case_id already exists in metadata, metadata is updated but
        no new vector is added to FAISS. This keeps FAISS count == metadata count.
        """
        if not FAISS_AVAILABLE or self.index is None:
            logger.error("FAISS index not ready. Cannot add vector.")
            return False

        try:
            # Convert to numpy float32 array (FAISS requirement)
            vector = np.array([embedding], dtype=np.float32)

            already_exists = case_id in self.metadata

            if not already_exists:
                # Only add a new FAISS vector for genuinely new cases.
                # Skipping this for existing case_ids keeps the FAISS
                # positional index aligned with the metadata key list.
                self.index.add(vector)

            # Always update metadata (allows field updates on re-run)
            self.metadata[case_id] = metadata

            if already_exists:
                logger.info(
                    "Metadata updated (vector skipped — case already indexed) | "
                    "case_id=%s | total_vectors=%d",
                    case_id, self.index.ntotal
                )
            else:
                logger.info(
                    "Vector added | case_id=%s | total_vectors=%d",
                    case_id, self.index.ntotal
                )
            return True

        except Exception as e:
            logger.error("Failed to add vector for %s: %s", case_id, e)
            return False

    def rebuild_from_metadata(self) -> bool:
        """
        Rebuilds the FAISS index from scratch using current metadata entries.

        Regenerates each embedding from the stored fault_mode / asset_type /
        bearing_type fields and inserts them in metadata key order, so that
        FAISS position N maps to the Nth metadata key.

        Use this to repair a FAISS/metadata positional mismatch (Cause D).
        Caller must call save_index() afterwards to persist the result.

        Returns True if rebuilt vector count matches metadata count.
        """
        if not FAISS_AVAILABLE:
            logger.error("FAISS not available. Cannot rebuild.")
            return False

        from rag.embeddings import build_embedding_text, generate_embedding

        fresh_index = faiss.IndexFlatL2(EMBEDDING_DIM)

        for case_id, meta in self.metadata.items():
            emb_text = build_embedding_text({
                "fault_mode":  meta.get("fault_mode", ""),
                "asset_type":  meta.get("asset_type", ""),
                "bearing_type": meta.get("bearing_type", ""),
            })
            emb = generate_embedding(emb_text)
            vec = np.array([emb], dtype=np.float32)
            fresh_index.add(vec)
            logger.info("Rebuilt vector | case_id=%s | text=%r", case_id, emb_text)

        self.index = fresh_index
        aligned = fresh_index.ntotal == len(self.metadata)
        logger.info(
            "Index rebuilt from metadata | vectors=%d | metadata=%d | aligned=%s",
            fresh_index.ntotal, len(self.metadata), aligned
        )
        return aligned
    
    def save_index(self):
        """
        Saves FAISS index and metadata to disk.
        Must be called after every add() or changes are lost on restart.
        """
        if not FAISS_AVAILABLE or self.index is None:
            logger.error("FAISS index not ready. Cannot save.")
            return
        
        faiss.write_index(self.index, INDEX_PATH)
        
        with open(METADATA_PATH, "w") as f:
            json.dump(self.metadata, f, indent=2, default=str)
        
        logger.info(
            "Index saved to %s | vectors=%d | metadata records=%d",
            INDEX_PATH, self.index.ntotal, len(self.metadata)
        )
    
    def get_vector_count(self) -> int:
        """Returns total number of vectors currently in the index."""
        if self.index is None:
            return 0
        return self.index.ntotal
    
    def case_exists(self, case_id: str) -> bool:
        """
        Checks if a case_id already exists in metadata.
        Used by load_data.py to skip re-inserting existing cases.
        """
        return case_id in self.metadata
