"""
schemas/knowledge.py  —  Phase 6 (Knowledge Agent output)
"""
from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field


class KnowledgeGuidance(BaseModel):
    """Knowledge Agent output."""
    case_id:            str       = ""
    fault_mode:         str       = ""
    asset_type:         str       = ""
    bearing_type:       str       = ""
    source_documents:   List[str] = Field(default_factory=list)   # SOP file names
    relevant_sections:  List[str] = Field(default_factory=list)   # chunked text passages
    inspection_steps:   List[str] = Field(default_factory=list)
    safety_notes:       List[str] = Field(default_factory=list)
    loto_reference:     str       = ""
    processed_at:       str       = ""
