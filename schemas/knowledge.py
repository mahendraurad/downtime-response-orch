"""
KnowledgeGuidance schema.
Input that Agent 6.6 receives FROM Agent 6.5 (Knowledge Agent).
Contains relevant SOP steps, safety notes, and similar past cases.
"""
from pydantic import BaseModel, Field


class KnowledgeGuidance(BaseModel):
    """
    The structured knowledge guidance emitted by Agent 6.5 (Knowledge Agent).
    This is one of the three inputs Agent 6.6 consumes to produce a maintenance recommendation.
    """

    # --- Identification: which case this guidance applies to ---
    case_id: str

    # --- Source material (lists may be empty if nothing was retrieved) ---
    source_documents: list[str] = Field(default_factory=list)
    relevant_sections: list[str] = Field(default_factory=list)

    # --- Actionable guidance extracted from those sources ---
    inspection_steps: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
