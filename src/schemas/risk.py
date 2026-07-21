"""
schemas/risk.py  —  Phase 5 (Predictive Risk Agent output)
"""
from __future__ import annotations

import json
from typing import Any, Dict
from pydantic import BaseModel


class RiskAssessment(BaseModel):
    """Output contract of the Predictive Risk Agent."""
    case_id:              str   = ""
    asset_id:             str   = ""
    bearing_id:           str   = ""
    failure_probability:  float = 0.0   # 0.0–1.0
    risk_level:           str   = ""    # low | medium | high | critical
    rul_min_days:         int   = 0     # lower bound of RUL window
    rul_max_days:         int   = 0     # upper bound of RUL window
    rul_band_label:       str   = ""    # "0–7 days" | "7–30 days" | "monitor" etc.
    confidence:           float = 0.0
    health_index:         float = 0.0   # 0.0 (failed) – 1.0 (healthy)
    business_impact_flag: bool  = False # True if bottleneck or high criticality
    financial_exposure:   float = 0.0  # rul_max_days × 24 × downtime_cost_per_hour
    assessment_source:    str   = "rules"  # "rules" | "rules+llm_fallback"
    advisory_note:        str   = ""       # LLM advisory when rules were insufficient
    processed_at:         str   = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return all fields as a plain dict (JSON-serialisable)."""
        return self.model_dump()

    def to_json(self, indent: int = 2) -> str:
        """Return the full assessment as a formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def __str__(self) -> str:
        src_tag = " [LLM advisory]" if "llm_fallback" in self.assessment_source else ""
        lines = [
            f"RiskAssessment{src_tag}",
            f"  case_id              : {self.case_id}",
            f"  asset / bearing      : {self.asset_id} / {self.bearing_id}",
            f"  risk_level           : {self.risk_level}",
            f"  rul_band             : {self.rul_band_label}  "
            f"({self.rul_min_days}–{self.rul_max_days} days)",
            f"  failure_probability  : {self.failure_probability}",
            f"  health_index         : {self.health_index}",
            f"  confidence           : {self.confidence}",
            f"  business_impact_flag : {self.business_impact_flag}",
            f"  financial_exposure   : {self.financial_exposure:,.0f}",
            f"  assessment_source    : {self.assessment_source}",
            f"  processed_at         : {self.processed_at}",
        ]
        if self.advisory_note:
            lines.append(f"  advisory_note        : {self.advisory_note}")
        return "\n".join(lines)
