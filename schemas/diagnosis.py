"""
FaultDiagnosis schema.
Input that Agent 6.6 receives FROM Agent 6.3 (Failure Intelligence Agent).
Describes WHAT KIND of fault was found (e.g. outer_race_fault, lubrication_issue).
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FaultDiagnosis(BaseModel):
    """
    The structured fault diagnosis emitted by Agent 6.3 (Failure Intelligence Agent).
    This is one of the three inputs Agent 6.6 consumes to produce a maintenance recommendation.
    """

    # --- Identification: which case, which asset, which bearing ---
    case_id: str
    asset_id: str
    bearing_id: str

    # --- The diagnosis itself ---
    fault_code: str
    fault_mode: Literal[
        "outer_race_fault",
        "inner_race_fault",
        "lubrication_issue",
        "imbalance",
        "misalignment",
        "cage_fault",
        "unknown",
    ]
    affected_component: str
    severity: Literal["monitor", "stage_1", "stage_2", "stage_3"]
    confidence: float = Field(ge=0.0, le=1.0)

    # --- Supporting context (lists may be empty if nothing to report) ---
    evidence: list[str] = Field(default_factory=list)
    likely_causes: list[str] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)

    # --- When the diagnosis was made --- 
    diagnosed_at_utc: datetime