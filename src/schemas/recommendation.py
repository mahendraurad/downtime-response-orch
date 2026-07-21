"""
schemas/recommendation.py  —  Phase 7 (Prescriptive Optimization Agent output)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RecommendedAction(BaseModel):
    name:                     str   = ""   # lubrication_service | bearing_replacement | inspection | continue_monitoring
    description:              str   = ""
    estimated_duration_hours: float = 0.0


class RequiredPart(BaseModel):
    part_number:    str = ""
    quantity:       int = 1
    lead_time_days: int = 0


class Contributor(BaseModel):
    role:    str = ""
    name:    str = ""
    concern: str = ""


class MaintenanceRecommendation(BaseModel):
    """Prescriptive Optimization Agent output."""
    case_id:                str                  = ""
    asset_id:               str                  = ""
    bearing_id:             str                  = ""
    fault_mode:             str                  = ""   # propagated from FaultDiagnosis
    asset_type:             str                  = ""   # propagated from asset master
    iso_stage:              int                  = 0    # propagated from FaultDiagnosis
    recommended_action:     RecommendedAction    = Field(default_factory=RecommendedAction)
    urgency:                str                  = ""   # planned | urgent | immediate | monitor
    ranked_alternatives:    List[RecommendedAction] = Field(default_factory=list)
    required_parts:         List[RequiredPart]      = Field(default_factory=list)
    window_chosen:          Optional[str]           = None
    rationale:              str                  = ""
    evidence:               Dict[str, Any]       = Field(default_factory=dict)
    recommendation_status:  str                  = ""   # ok | no_action | fallback
    is_llm_suggested:       bool                 = False
    approval_status:        str                  = "pending"  # pending | approved | rejected
    responsible_person:     str                  = ""
    responsible_person_id:  str                  = ""
    responsible_approver:   str                  = ""
    responsible_approver_id: str                 = ""
    contributors:           List[Contributor]    = Field(default_factory=list)
    generated_at_utc:       str                  = ""
