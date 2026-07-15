"""
Data loader for the Learning and Memory Agent.

Reads all JSON files from data/learned_cases/, validates each one,
generates embeddings, stores vectors in FAISS, and writes audit logs.

Run this script once after setup to populate the vector index:
  python tools/load_data.py

Run it again at any time to reload — existing cases are skipped
(checked by case_id in metadata) so no duplicates are created.

What it does for each case:
  1. Load JSON file
  2. Validate with LearnedCase Pydantic schema
  3. Build embedding text from fault_mode + bearing_type + root_cause + lessons_learned
  4. Generate 384-dim embedding with sentence-transformers
  5. Add vector to FAISS index
  6. Store CaseMetadata to metadata.json
  7. Write 4 audit log events to logs/memory.log
  8. Save FAISS index to disk

Output:
  Prints a summary table showing which cases were loaded, skipped, or failed.
"""

import json
import os
import sys
import logging
from datetime import datetime, timezone

# Add project root to path so imports work from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.learned_case import LearnedCase, CaseMetadata
from rag.embeddings import generate_embedding, build_embedding_text
from rag.vector_storage import VectorStorage
from storage.audit_logger import (
    log_memory_created,
    log_vector_stored,
    log_metadata_stored,
    log_valid_until_set
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)

CASES_DIR = "data/learned_cases"


def load_all_cases():
    """
    Main function. Loads all JSON files from data/learned_cases/.
    
    Returns a summary dict:
    {
        "loaded": [list of case_ids successfully stored],
        "skipped": [list of case_ids already in index],
        "failed": [list of (filename, error) tuples]
    }
    """
    summary = {"loaded": [], "skipped": [], "failed": []}
    
    # Initialise vector storage and load existing index
    store = VectorStorage()
    store.load_index()
    
    # Find all JSON files in the cases directory
    if not os.path.exists(CASES_DIR):
        logger.error("Cases directory not found: %s", CASES_DIR)
        return summary
    
    json_files = [f for f in os.listdir(CASES_DIR) if f.endswith(".json")]
    
    if not json_files:
        logger.warning("No JSON files found in %s", CASES_DIR)
        return summary
    
    logger.info("Found %d case files in %s", len(json_files), CASES_DIR)
    
    for filename in sorted(json_files):
        filepath = os.path.join(CASES_DIR, filename)
        
        try:
            # Step 1: Load JSON file
            with open(filepath, "r") as f:
                raw_data = json.load(f)
            
            # Step 2: Validate with Pydantic schema
            # This checks valid_until format, fault_mode, required fields
            case = LearnedCase(**raw_data)
            
            # Step 3: Check if already loaded (skip duplicates)
            if store.case_exists(case.case_id):
                logger.info("Skipping %s — already in index", case.case_id)
                summary["skipped"].append(case.case_id)
                continue
            
            # Step 4: Log that memory was created
            log_memory_created(case.case_id, case.fault_mode, case.asset_type)
            
            # Step 5: Build embedding text and generate vector
            embedding_text = build_embedding_text(raw_data)
            embedding = generate_embedding(embedding_text)
            
            # Step 6: Build metadata record
            now = datetime.now(timezone.utc).isoformat()
            metadata = CaseMetadata(
                case_id=case.case_id,
                created_at=now,
                updated_at=now,
                valid_until=case.valid_until,
                created_by=case.created_by,
                source="seed_data",
                embedding_dim=len(embedding),
                fault_mode=case.fault_mode,
                asset_type=case.asset_type,
                bearing_type=case.bearing_type
            ).model_dump()
            
            # Step 7: Add vector to FAISS index
            success = store.add(embedding, metadata, case.case_id)
            
            if success:
                # Step 8: Write audit log events
                log_vector_stored(case.case_id, len(embedding), store.get_vector_count())
                log_metadata_stored(case.case_id, case.valid_until, case.created_by)
                log_valid_until_set(case.case_id, case.valid_until, case.created_by)
                
                summary["loaded"].append(case.case_id)
                logger.info("Successfully loaded: %s", case.case_id)
            else:
                summary["failed"].append((filename, "vector storage failed"))
                
        except Exception as e:
            logger.error("Failed to load %s: %s", filename, str(e))
            summary["failed"].append((filename, str(e)))
    
    # Step 9: Save the FAISS index to disk
    store.save_index()
    
    return summary


def print_summary(summary: dict):
    """Prints a formatted summary table after loading completes."""
    print("\n" + "=" * 55)
    print("DATA LOADING COMPLETE")
    print("=" * 55)
    print(f"  Loaded:  {len(summary['loaded'])} cases")
    for case_id in summary["loaded"]:
        print(f"    + {case_id}")
    
    if summary["skipped"]:
        print(f"  Skipped: {len(summary['skipped'])} cases (already in index)")
        for case_id in summary["skipped"]:
            print(f"    = {case_id}")
    
    if summary["failed"]:
        print(f"  Failed:  {len(summary['failed'])} cases")
        for filename, error in summary["failed"]:
            print(f"    x {filename}: {error}")
    
    print("=" * 55)
    print(f"  FAISS index: data/faiss_index/index.faiss")
    print(f"  Metadata:    data/faiss_index/metadata.json")
    print(f"  Audit log:   logs/memory.log")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    summary = load_all_cases()
    print_summary(summary)
