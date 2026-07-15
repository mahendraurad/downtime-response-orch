"""
LearnedCase schema for the Learning and Memory Agent.

This schema validates learned cases read from data/learned_cases/ JSON files.
These cases are manually seeded for Phase 1. In future phases they will
arrive from the Executor Agent after work order closure.

valid_until comes directly from the input record.
It is NOT calculated from bearing life.
Future Streamlit users will select this date manually.
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


class LearnedCase(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "case_id": "CASE_001",
                "asset_type": "motor",
                "bearing_type": "SKF6310",
                "fault_mode": "outer_race_fault",
                "root_cause": "contamination ingress",
                "action_taken": "bearing replacement",
                "result": "successful repair",
                "lessons_learned": "inspect seals during replacement",
                "valid_until": "2028-12-31",
                "created_by": "seed_data"
            }
        }
    }
    
    case_id: str
    asset_type: str
    bearing_type: str
    fault_mode: str
    root_cause: str
    action_taken: str
    result: str
    lessons_learned: str
    
    # valid_until must come from the input record directly.
    # Do NOT calculate this from bearing life or machine schedule.
    # Validate it is a valid date string in YYYY-MM-DD format.
    valid_until: str
    
    created_by: str = "seed_data"
    
    @field_validator("valid_until")
    @classmethod
    def validate_valid_until_format(cls, v):
        """
        Validates that valid_until is a real date in YYYY-MM-DD format.
        Raises ValueError if the format is wrong or date is invalid.
        Example valid: "2028-12-31"
        Example invalid: "31-12-2028" or "2028/12/31"
        """
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(
                f"valid_until must be in YYYY-MM-DD format, got: {v}"
            )
        return v
    
    @field_validator("fault_mode")
    @classmethod
    def validate_fault_mode(cls, v):
        """Validates fault_mode is a known fault type."""
        allowed = [
            "outer_race_fault", "inner_race_fault", "lubrication_issue",
            "imbalance", "misalignment", "cage_fault", "sensor_fault", "healthy"
        ]
        if v not in allowed:
            raise ValueError(f"fault_mode must be one of {allowed}, got: {v}")
        return v


class CaseMetadata(BaseModel):
    """
    Metadata stored alongside every learned case.
    Stored in data/faiss_index/metadata.json
    Used for audit, lifecycle management, and future retrieval by Knowledge Agent.
    """
    case_id: str
    created_at: str    # ISO timestamp when stored
    updated_at: str    # ISO timestamp of last update
    valid_until: str   # From the input record directly
    created_by: str    # Who or what created this record
    source: str        # Where it came from: "seed_data", "work_order", "manual"
    embedding_dim: int # Dimension of the stored vector (384 for MiniLM)
    fault_mode: str
    asset_type: str
    bearing_type: str
