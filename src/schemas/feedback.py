"""
schemas/feedback.py  —  Phase 10 (Learning & Memory Agent output)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from typing import Literal


class FeedbackEvent(BaseModel):
    """Closed-loop learning input."""
    case_id:                    str            = ""
    asset_id:                   str            = ""
    bearing_id:                 str            = ""
    confirmed_fault_mode:       str            = ""
    root_cause:                 str            = ""
    action_taken:               str            = ""
    recommendation_followed:    bool           = False
    technician_notes:           str            = ""
    post_repair_vib_mm_s:       float          = 0.0
    post_repair_temp_c:         float          = 0.0
    days_to_failure_actual:     Optional[int]  = None
    closed_at:                  str            = ""


class LearnedCaseDocument(BaseModel):
    """Written to vector store by Learning Agent."""
    case_id:         str       = ""
    content:         str       = ""   # human-readable narrative for RAG retrieval
    fault_mode:      str       = ""
    asset_type:      str       = ""
    bearing_type:    str       = ""
    iso_stage:       int       = 0
    outcome:         str       = ""
    tags:            List[str] = Field(default_factory=list)
    created_at:      str       = ""
    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    learning_status: str       = "learned"  # learned | duplicate | invalid_input | persistence_failed
    learning_eligible: bool    = True
    status_reason:   str       = ""
    recommendation_followed: bool = False
    post_repair_vib_mm_s: float = 0.0
    post_repair_temp_c: float = 0.0
    days_to_failure_actual: Optional[int] = None
    narrative_source: str = "template"
    schema_version: str = "1.1"
    learning_config_version: str = ""
    source_execution_schema_version: str = ""
    source_executor_config_version: str = ""
    linked_execution_case_id: str = ""
    persistence_status: str = "not_requested"
    index_status: str = "not_requested"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
    # ***********************


class RejectionFeedback(BaseModel):
    """Structured operator rejection routed to Agent 8 memory."""
    case_id: str
    asset_id: str
    fault_mode: str
    reason_code: Literal[
        "diagnosis_wrong", "parts_concern", "second_opinion",
        "wrong_window", "other",
    ]
    free_text: str = ""
    persona_id: str
    rejected_at: str


class RejectionLearningResult(BaseModel):
    case_id: str
    status: str = "recorded"
    reliability_review_required: bool = False
    consecutive_rejections: int = 1
    review_reason: str = ""
    persistence_status: str = "stored"
