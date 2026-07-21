"""
schemas/feedback.py  —  Phase 10 (Learning & Memory Agent output)
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


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
