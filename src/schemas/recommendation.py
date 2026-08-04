"""
schemas/recommendation.py  —  Phase 7 (Prescriptive Optimization Agent output)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.schemas.persona import PersonaContext


class Verdict(BaseModel):
    headline: str = ""
    reasoning: str = ""


class ConditionSummary(BaseModel):
    fault_type: str = ""
    fault_stage: int = 0
    rul_min_days: int = 0
    rul_max_days: int = 0
    failure_probability: float = 0.0
    vibration_mms: Optional[float] = None
    vibration_threshold: Optional[float] = None
    temperature_c: Optional[float] = None
    temperature_threshold: Optional[float] = None


class Consequence(BaseModel):
    type: str
    description: str
    value: Optional[str] = None
    evidence_status: str = "available"


class PrescriptiveAction(BaseModel):
    rank: int
    action: str
    rationale: str
    urgency: str
    prescriptive_score: float


class ConfidenceSummary(BaseModel):
    fault_identification: float = 0.0
    rul_prediction: float = 0.0
    recommendation: float = 0.0
    data_completeness: float = 0.0
    training_sample_size: int = 0
    warnings: List[str] = Field(default_factory=list)


class HistoricalCaseCitation(BaseModel):
    case_id: str
    source: str
    fault_mode: str = ""
    action_taken: str = ""
    outcome: str = ""
    recorded_at: str = ""
    relevance_score: float = 0.0


class PartsRULComparison(BaseModel):
    part_number: str = ""
    eta_days: Optional[int] = None
    rul_min_days: int = 0
    rul_max_days: int = 0
    status: str = "not_required"
    explanation: str = ""


class DecisionSupport(BaseModel):
    cost_if_approved: Optional[float] = None
    cost_if_deferred: Optional[float] = None
    deferred_cost_per_hour: Optional[float] = None
    cost_breakdown: str = ""
    cost_basis: str = ""
    currency: str = "USD"
    cost_data_status: str = "unavailable"
    parts_vs_rul: PartsRULComparison = Field(default_factory=PartsRULComparison)
    historical_cases: List[HistoricalCaseCitation] = Field(default_factory=list)
    authority_check: str = "not_evaluated"
    authority_reason: str = "persona authority and approved cost inputs are pending"
    authority_limit: Optional[float] = None
    decision_support_config_version: str = ""


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
    recommended_action:     RecommendedAction    = Field(default_factory=RecommendedAction)
    urgency:                str                  = ""   # planned | urgent | immediate | monitor
    ranked_alternatives:    List[Dict[str, Any]] = Field(default_factory=list)
    required_parts:         List[RequiredPart]   = Field(default_factory=list)
    window_chosen:          Optional[str]        = None
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
    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    approval_required:      bool                 = False
    recommendation_eligible: bool                = True
    status_reason:          str                  = ""
    procurement_required:   bool                 = False
    schema_version:         str                  = "1.1"
    prescriptive_config_version: str             = ""
    source_risk_config_version: str              = ""
    source_knowledge_config_version: str         = ""
    source_knowledge_index_version: str          = ""
    linked_risk_case_id:    str                  = ""
    rationale_source:       str                  = "rules"
    # Group A response-quality contract.
    verdict: Verdict = Field(default_factory=Verdict)
    condition: ConditionSummary = Field(default_factory=ConditionSummary)
    consequences: List[Consequence] = Field(default_factory=list)
    prescriptive_actions: List[PrescriptiveAction] = Field(default_factory=list)
    confidence: ConfidenceSummary = Field(default_factory=ConfidenceSummary)
    persona_context: Optional[PersonaContext] = None
    historical_cases: List[HistoricalCaseCitation] = Field(default_factory=list)
    sop_citations: List[str] = Field(default_factory=list)
    cost_data_status: str = "unavailable"
    authority_check: str = "not_evaluated"
    decision_support: DecisionSupport = Field(default_factory=DecisionSupport)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
    # ***********************
