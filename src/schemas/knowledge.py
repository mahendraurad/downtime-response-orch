"""
schemas/knowledge.py  —  Phase 6 (Knowledge Agent output)
"""
from __future__ import annotations

from typing import Any, Dict, List
from pydantic import BaseModel, Field


# ************** Added by Prateek Mittal on 20th July 2026 ******************
class GroundedGuidanceItem(BaseModel):
    text: str
    source_document: str
    item_type: str  # inspection_step | safety_note
    retrieval_score: float = 0.0


class SourceDocument(BaseModel):
    title: str
    retrieval_score: float = 0.0
    fault_mode: str = ""
    asset_type: str = ""
    iso_stage: int = 0
# ***********************


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

    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    guidance_status:    str       = "grounded"  # grounded | no_guidance | retrieval_failed | invalid_input
    guidance_eligible:  bool      = True
    status_reason:      str       = ""
    retrieval_query:    str       = ""
    retrieval_hit_count: int      = 0
    grounded_items:     List[GroundedGuidanceItem] = Field(default_factory=list)
    source_details:     List[SourceDocument] = Field(default_factory=list)
    schema_version:     str       = "1.1"
    knowledge_config_version: str = ""
    knowledge_index_version: str = ""
    source_diagnosis_schema_version: str = ""
    source_fi_config_version: str = ""
    source_taxonomy_version: str = ""
    source_risk_schema_version: str = ""
    source_risk_config_version: str = ""
    source_monitoring_config_version: str = ""
    source_data_config_version: str = ""
    source_master_data_version: str = ""
    linked_risk_case_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
    # ***********************