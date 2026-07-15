"""
Shared helper for minting new CASE_YYYYMMDD_NNN case IDs.

Used by:
  - orchestrator/langgraph_flow.py's memory_storage_node (PATH_B mint)
  - streamlit_app_v3.py's generate_next_case_id (UI-layer callers)

Consolidating here prevents ID-format drift between the pipeline and
the UI. If the case_id format ever changes, edit this file only.
"""
import os
import re
from datetime import datetime

_LEARNED_CASES_DIR = "data/learned_cases"


def mint_new_case_id(directory: str = _LEARNED_CASES_DIR) -> str:
    """
    Return the next available CASE_YYYYMMDD_NNN identifier.

    Scans `directory` for filenames matching CASE_<today>_NNN and
    returns f"CASE_{today}_{max_N + 1:03d}". If no matching files
    exist, returns CASE_<today>_001.

    The scan is case-insensitive on the filename prefix to handle
    both CASE_xxx and case_xxx naming.
    """
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"CASE_{today}_"
    pattern = re.compile(rf"^case_{today}_(\d{{3}})", re.IGNORECASE)

    existing_nums = []
    try:
        for fn in os.listdir(directory):
            stem = os.path.splitext(fn)[0]
            m = pattern.match(stem)
            if m:
                existing_nums.append(int(m.group(1)))
    except FileNotFoundError:
        pass  # directory missing → treat as empty

    next_n = (max(existing_nums) + 1) if existing_nums else 1
    return f"{prefix}{next_n:03d}"
