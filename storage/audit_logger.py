"""
Audit logger for the Learning and Memory Agent.

Writes timestamped audit events to logs/memory.log.

Manager requirement: every step must produce a log entry with timestamp
so the system is auditable and debuggable.

Log events defined in this agent:
  MEMORY_CREATED   - a new learned case record was validated and accepted
  VECTOR_STORED    - embedding was added to FAISS index
  METADATA_STORED  - metadata was written to metadata.json
  VALID_UNTIL_SET  - valid_until date was recorded for a case

Log format:
  YYYY-MM-DD HH:MM:SS | EVENT_TYPE | case_id | details

Example:
  2026-06-01 14:23:11 | MEMORY_CREATED | CASE_001 | fault=outer_race_fault asset=motor
  2026-06-01 14:23:11 | VECTOR_STORED  | CASE_001 | dim=384 index_total=1
  2026-06-01 14:23:11 | METADATA_STORED| CASE_001 | valid_until=2028-01-10
  2026-06-01 14:23:11 | VALID_UNTIL_SET| CASE_001 | valid_until=2028-01-10 source=seed_data
"""

import logging
import os
from datetime import datetime, timezone

os.makedirs("logs", exist_ok=True)

# Separate file handler for audit log
# This is distinct from the main application logger
# so audit events are never mixed with debug output
audit_handler = logging.FileHandler("logs/memory.log")
audit_handler.setLevel(logging.INFO)
audit_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)

audit_logger = logging.getLogger("audit")
audit_logger.setLevel(logging.INFO)
audit_logger.addHandler(audit_handler)
# Also print to console so developer can see events during dev
audit_logger.addHandler(logging.StreamHandler())


def log_memory_created(case_id: str, fault_mode: str, asset_type: str):
    """
    Logs that a new learned case was validated and accepted into memory.
    Called after schema validation passes.
    """
    audit_logger.info(
        "MEMORY_CREATED | %s | fault=%s asset=%s",
        case_id, fault_mode, asset_type
    )


def log_vector_stored(case_id: str, dim: int, total_vectors: int):
    """
    Logs that a vector was successfully added to the FAISS index.
    Called after VectorStorage.add() succeeds.
    """
    audit_logger.info(
        "VECTOR_STORED | %s | dim=%d index_total=%d",
        case_id, dim, total_vectors
    )


def log_metadata_stored(case_id: str, valid_until: str, source: str):
    """
    Logs that metadata was written to metadata.json.
    Called after VectorStorage.save_index() completes.
    """
    audit_logger.info(
        "METADATA_STORED | %s | valid_until=%s source=%s",
        case_id, valid_until, source
    )


def log_valid_until_set(case_id: str, valid_until: str, source: str):
    """
    Logs that valid_until was recorded for this case.
    This is separate from METADATA_STORED to make TTL events
    easy to grep for in the log file.
    Called whenever valid_until is stored or updated.
    """
    audit_logger.info(
        "VALID_UNTIL_SET | %s | valid_until=%s source=%s",
        case_id, valid_until, source
    )
