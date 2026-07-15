"""
RiskAssessment schema.
Input that Agent 6.6 receives FROM Agent 6.4 (Risk Assessment Agent).
Describes HOW LIKELY a failure is, HOW SOON it may occur, and HOW COSTLY it would be.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RiskAssessment(BaseModel):
    """
    The structured risk assessment emitted by Agent 6.4 (Risk Assessment Agent).
    This is one of the three inputs Agent 6.6 consumes to produce a maintenance recommendation.
    """

    # --- Identification: which case, which asset, which bearing ---
    case_id: str
    asset_id: str
    bearing_id: str

    # --- Probability and level of risk ---
    failure_probability: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["low", "medium", "high", "critical"]

    # --- Remaining useful life estimate (optimistic/pessimistic range in days) ---
    rul_min_days: int = Field(ge=0)
    rul_max_days: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)

    # --- Business impact ---
    business_impact_flag: bool
    estimated_downtime_cost_per_hour: float = Field(ge=0.0)

    # --- When the assessment was made ---
    assessed_at_utc: datetime

    # --- Cross-field validation ---
    @model_validator(mode="after")
    def check_rul_range(self) -> "RiskAssessment":
        """rul_max_days must be >= rul_min_days (best-case life >= worst-case life)."""
        if self.rul_max_days < self.rul_min_days:
            raise ValueError(
                f"rul_max_days ({self.rul_max_days}) must be >= "
                f"rul_min_days ({self.rul_min_days})"
            )
        return self
