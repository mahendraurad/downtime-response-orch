"""
MaintenanceRecommendation schema.
The OUTPUT that Agent 6.6 produces and sends to Agent 6.7 (Executor Agent).
Describes the recommended action, ranked alternatives, parts needed, and approval status.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Action(BaseModel):
    """
    A single maintenance action that could be taken (e.g. replace a bearing, lubricate).
    Used both for the chosen action and for the ranked alternatives.
    """

    name: str
    description: str
    estimated_duration_hours: float = Field(ge=0)


class RequiredPart(BaseModel):
    """
    A single part needed to carry out a maintenance action.
    lead_time_days is 0 if the part is in stock, or a positive number if it must be ordered.
    """

    part_number: str
    quantity: int = Field(ge=1)
    lead_time_days: int = Field(ge=0)


class MaintenanceRecommendation(BaseModel):
    """
    The final output of Agent 6.6, sent to Agent 6.7 (Executor Agent).
    Describes the chosen maintenance action, the alternatives considered,
    the parts needed, a plain-language rationale, and whether human approval is required.
    """

    # --- Identification: ties the recommendation back to the case ---
    case_id: str
    asset_id: str
    bearing_id: str

    # --- The decision (note the nested Action bundle) ---
    recommended_action: Action
    urgency: Literal["monitor", "planned", "urgent", "emergency"]

    # --- Supporting detail (lists of nested bundles) ---
    ranked_alternatives: list[Action] = Field(default_factory=list)
    required_parts: list[RequiredPart] = Field(default_factory=list)

    # --- Context for the executor ---
    window_chosen: str | None = None
    rationale: str
    evidence: dict[str, str] = Field(default_factory=dict)

    # --- Safety gate, status, and timestamp ---
    recommendation_status: Literal[
        "ok",
        "blocked_no_part",
        "blocked_unknown_asset",
        "unreliable_diagnosis",
        "catalog_miss",
        "novel_llm_suggestion",   # novel fault, no SOP — LLM proposed a tentative action
        "blocked_invalid_input",  # upstream data failed pre-processing validation
    ] = "ok"

    # True when the recommended action was PROPOSED BY THE LLM for a novel fault with no
    # approved procedure. Lets the UI/executor distinguish a tentative AI suggestion from
    # a deterministic, rule-backed recommendation. Always False for normal/rule-backed cases.
    is_llm_suggested: bool = False
    approval_status: Literal[
        "pending",    # valid recommendation awaiting routine human approval
        "escalated",  # something is wrong; a human must actively investigate/intervene
        "approved",   # set later by the UI / approver
        "rejected",   # set later by the UI / approver
    ] = "pending"

    # --- Responsible person (role who must review/approve before execution) ---
    # Kept for backward compatibility with the existing UI; assembly now sets it
    # to the same value as responsible_approver.
    responsible_person:    str       = "Maintenance Supervisor"
    responsible_person_id: str | None = None

    # --- Approver (the single person who signs off, from the severity hierarchy) ---
    responsible_approver:    str = "Plant Supervisor — James Kowalski"
    responsible_approver_id: str = "PERSONA_SUP"

    # --- Contributors (specialists who must weigh in; empty for clean routine cases) ---
    # Each item is a small dict: {"role": ..., "name": ..., "concern": ...}
    contributors: list[dict] = Field(default_factory=list)

    generated_at_utc: datetime
