"""Domain constants for the DRO Learning & Memory Agent.

Active consumers:
  - RUL_BY_STAGE: services/learning_service.py, streamlit_app_v3.py

Reference data (no current consumers):
  - POST_REPAIR_QA_THRESHOLDS, POST_REPAIR_BASELINES, RELUBE_INTERVALS_DAYS
  - MEMORY_TTL_SHORT, MAX_SESSION_LENGTH, RELEVANCE_DECAY_MONTHS,
    ARCHIVE_AFTER_MONTHS, DEDUP_SIMILARITY_THRESHOLD,
    MIN_VALID_QUALITY_SCORE
  - SUPPORTED_FAULT_MODES, SUPPORTED_ASSET_TYPES, SUPPORTED_BEARING_TYPES

These are kept for downstream agents (Monitoring, Predictive Risk)
that may consume them. Removing them requires confirming with the
integration team that no other DRO agent expects them.
"""
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(module)s | %(message)s'
)
logger = logging.getLogger(__name__)

# Memory management
MEMORY_TTL_SHORT = 3600  # short_term memory expires after 1 hour (seconds)
MAX_SESSION_LENGTH = 20  # summarizer triggers when session exceeds this many messages

# Deduplication thresholds (3-gate dedup system)
DEDUP_SIMILARITY_THRESHOLD = 0.92  # Gate 2: cosine similarity above this = duplicate

# Data lifecycle
RELEVANCE_DECAY_MONTHS = 12  # relevance_score starts decaying after this many months
ARCHIVE_AFTER_MONTHS = 24  # cases inactive this long get archived (not deleted)
MIN_VALID_QUALITY_SCORE = 0.50  # signals below this are rejected by Data Foundation

# Fault modes (from fault_taxonomy.json + sensor and healthy cases)
SUPPORTED_FAULT_MODES = [
    "outer_race_fault", "inner_race_fault", "lubrication_issue",
    "imbalance", "misalignment", "cage_fault", "sensor_fault", "healthy"
]

# Asset types (from asset_master.json)
SUPPORTED_ASSET_TYPES = ["motor", "pump", "conveyor", "gearbox"]

# Bearing types (from bearing_master.json)
SUPPORTED_BEARING_TYPES = ["SKF6310", "SKF6208", "SKF22212", "SKF22318"]

# Post-repair QA thresholds by asset (from SOP_006)
# Used by Monitoring Agent to validate repair success
POST_REPAIR_QA_THRESHOLDS = {
    "AST_MTR_001": {"vib_rms": 2.5, "temp_c": 60.0, "kurtosis": 2.8, "bpfo_ratio": 1.2},
    "AST_MTR_002": {"vib_rms": 2.2, "temp_c": 55.0, "kurtosis": 2.5, "bpfo_ratio": 1.2},
    "AST_PMP_001": {"vib_rms": 3.5, "temp_c": 65.0, "kurtosis": 3.5, "bpfo_ratio": 1.2},
    "AST_PMP_002": {"vib_rms": 3.0, "temp_c": 60.0, "kurtosis": 3.0, "bpfo_ratio": 1.2},
    "AST_CON_001": {"vib_rms": 2.8, "temp_c": 58.0, "kurtosis": 2.8, "bpfo_ratio": 1.2},
    "AST_GBX_001": {"vib_rms": 3.5, "temp_c": 65.0, "kurtosis": 2.8, "bpfo_ratio": 1.2},
}

# Confirmed post-repair baselines from closed cases (from CASE_001 through CASE_006)
# Used as expected healthy state reference after repair
POST_REPAIR_BASELINES = {
    "BRG_001": {"vib_rms": 1.8, "temp_c": 55.0, "case_ref": "CASE_001"},
    "BRG_005": {"vib_rms": 2.2, "temp_c": 60.0, "case_ref": "CASE_002"},
    "BRG_009": {"vib_rms": 2.3, "temp_c": 57.0, "case_ref": "CASE_003"},
    "BRG_003": {"vib_rms": 1.7, "temp_c": 51.0, "case_ref": "CASE_005"},
    "BRG_007": {"vib_rms": 2.0, "temp_c": 57.0, "case_ref": "CASE_006"},
}

# Re-lubrication intervals (corrected from CASE_002 root cause finding)
# CASE_002 confirmed that 90-day system default was WRONG for pumps
RELUBE_INTERVALS_DAYS = {
    "SKF6310": 60,
    "SKF6208": 60,
    "SKF22212": 75,
    "SKF22318": 60,
}

# RUL estimates by stage from fault_taxonomy.json
# sensor_fault and healthy have no bearing degradation path (None = not applicable)
# lubrication_failure mirrors lubrication_issue values
RUL_BY_STAGE = {
    "outer_race_fault":    {1: 90,   2: 30,   3: 7},
    "inner_race_fault":    {1: 80,   2: 25,   3: 5},
    "lubrication_issue":   {1: 60,   2: 20,   3: 5},
    "lubrication_failure": {1: 60,   2: 20,   3: 5},
    "cage_fault":          {1: 75,   2: 20,   3: 4},
    "imbalance":           {1: 120,  2: 60,   3: 15},
    "misalignment":        {1: 100,  2: 40,   3: 10},
    "sensor_fault":        {1: None, 2: None, 3: None},
    "healthy":             {1: None, 2: None, 3: None},
}

# SOP mapping: which SOP applies to which fault mode and asset type
SOP_ROUTING = {
    ("outer_race_fault", "motor"): "SOP_001",
    ("outer_race_fault", "gearbox"): "SOP_005",
    ("lubrication_issue", "pump"): "SOP_003",
    ("sensor_fault", "conveyor"): "SOP_004",
    ("healthy", "motor"): "SOP_002",
    ("healthy", "pump"): "SOP_003",
}
