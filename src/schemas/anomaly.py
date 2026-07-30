"""
schemas/anomaly.py  —  Phase 3 (Monitoring Agent output)

AnomalyEvent is emitted when the Monitoring Agent detects that a
TrustedBearingSignal deviates significantly from its per-bearing baseline.
It is the contract between the Monitoring Agent and the Failure Intelligence
Agent (Phase 4), which classifies the fault from this evidence.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AnomalyEvent(BaseModel):
    """
    Monitoring Agent output.

    Fields:
      case_id            : stable id for this anomaly (asset/bearing/timestamp)
      asset_id/bearing_id/channel_id : identity, carried from the signal
      timestamp_utc      : reading timestamp
      anomaly_score      : 0.0–1.0 composite deviation severity
      confidence_score   : 0.0–1.0 trust in the verdict (sensor + coverage + DQ)
      z_score            : largest |z| across scored signals (primary deviation)
      triggered_features : signals whose |z| crossed the trigger threshold
      reason             : plain-language, business-readable explanation of why
                           this reading was flagged (which signals deviated, by
                           how much, and which detector fired). Deterministic —
                           built from the same evidence, no LLM.
      regime             : operating-regime label at the time of the reading
      evidence           : per-signal z/severity, regime, excluded fields, etc.
      baseline_ref       : bearing_id whose baseline was used
      processed_at       : UTC ISO-8601 timestamp from the Monitoring Agent
    """
    case_id:            str              = ""
    asset_id:           str              = ""
    bearing_id:         str              = ""
    channel_id:         str              = ""
    timestamp_utc:      str              = ""
    anomaly_score:      float            = 0.0   # 0.0–1.0
    confidence_score:   float            = 0.0
    z_score:            float            = 0.0   # primary signal z-score
    triggered_features: List[str]        = Field(default_factory=list)
    reason:             str              = ""    # business-readable "why it was flagged"
    regime:             str              = ""
    evidence:           Dict[str, Any]   = Field(default_factory=dict)
    baseline_ref:       str              = ""    # bearing_id used for baseline lookup
    processed_at:       str              = ""
    # ************** Added by Prateek Mittal on 17th July 2026 ******************
    # Provenance fields make the detector decision reproducible and traceable
    # back to Agent 1 configuration/master data and Agent 2 configuration.
    schema_version:             str = "2.0"
    monitoring_config_version: str = ""
    detector_version:           str = "hotelling_t2+ewma-v1"
    source_schema_version:      str = ""
    source_config_version:      str = ""
    source_master_data_version: str = ""
    # ***********************

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


# ************** Added by Prateek Mittal on 17th July 2026 ******************
# Detailed Agent 2 decision contract. Unlike the compatibility `process()` API,
# this distinguishes healthy, suppressed, ineligible, insufficient-data, and
# anomalous outcomes and can be persisted for monitoring audit.
class MonitoringResult(BaseModel):
    telemetry_id:              str = ""
    asset_id:                  str = ""
    bearing_id:                str = ""
    timestamp_utc:             str = ""
    status:                    str = "not_assessed"
    assessed:                  bool = False
    suppression_reason:       str = ""
    anomaly_event:            Optional[AnomalyEvent] = None
    regime:                   str = ""
    usable_signals:           List[str] = Field(default_factory=list)
    excluded_signals:         List[str] = Field(default_factory=list)
    processed_at:             str = ""
    schema_version:           str = "1.0"
    monitoring_config_version: str = ""
    source_schema_version:    str = ""
    source_config_version:    str = ""
    source_master_data_version: str = ""
    state_recovered:          bool = False
    persistence_status:       str = "not_requested"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
# ***********************
