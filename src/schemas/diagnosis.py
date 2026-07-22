"""
schemas/diagnosis.py  —  Phase 4 (Failure Intelligence Agent output)

FaultDiagnosis is emitted when the Failure Intelligence Agent classifies an
AnomalyEvent against the fault taxonomy.  It is the contract between the
Failure Intelligence Agent and the Predictive Risk Agent (Phase 5), which
turns the named fault + ISO stage into a remaining-useful-life estimate.
"""
from __future__ import annotations

from typing import Any, Dict, List
from pydantic import BaseModel, Field


class FaultDiagnosis(BaseModel):
    """
    Failure Intelligence Agent output.

    Required contract:
      case_id               : stable id, carried from the AnomalyEvent
      asset_id / bearing_id : identity, carried from the signal
      fault_mode            : outer_race_fault | inner_race_fault |
                              rolling_element_fault | lubrication_issue |
                              cage_fault | undetermined
      fault_code            : FT_001 | FT_002 | FT_003 | FT_006 | ""
      iso_stage             : 1 | 2 | 3 (0 when undetermined)
      severity              : low | medium | high | critical
      confidence            : 0.0–1.0 trust in the classification
      bpfo_multiple         : bpfo_energy value at detection
      bpfi_multiple         : bpfi_energy value at detection
      kurtosis_at_detection : kurtosis value at detection
      evidence              : which thresholds were crossed + differentials
      recommended_checks    : concrete inspection actions
      narrative             : human-readable explanation
      processed_at          : UTC ISO-8601 timestamp from the agent

    Additive fields (optional, consumed by the Predictive Risk Agent):
      is_bottleneck         : asset sits on the production critical path
      typical_causes        : taxonomy-listed root causes for this fault mode
      rul_days_estimate     : taxonomy RUL (days) for the assigned ISO stage
    """
    # ── Required contract ────────────────────────────────────────────
    case_id:                str              = ""
    asset_id:               str              = ""
    bearing_id:             str              = ""
    fault_mode:             str              = ""   # outer_race_fault | inner_race_fault | ...
    fault_code:             str              = ""   # FT_001 | FT_002 | ...
    iso_stage:              int              = 0    # 1 | 2 | 3
    severity:               str              = ""   # low | medium | high | critical
    confidence:             float            = 0.0
    bpfo_multiple:          float            = 0.0
    bpfi_multiple:          float            = 0.0
    kurtosis_at_detection:  float            = 0.0
    evidence:               Dict[str, Any]   = Field(default_factory=dict)
    recommended_checks:     List[str]        = Field(default_factory=list)
    narrative:              str              = ""
    processed_at:           str              = ""

    # ── Additive (Phase 5 hand-off) ──────────────────────────────────
    is_bottleneck:          bool             = False
    typical_causes:         List[str]        = Field(default_factory=list)
    rul_days_estimate:      int              = 0

    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    # Additive status and provenance fields distinguish a successful diagnosis,
    # a valid-but-unmatched anomaly, and a rejected upstream handoff. They also
    # identify the exact Agent 3 policy and taxonomy used for audit/replay.
    diagnosis_status:       str              = "diagnosed"
    diagnostic_eligible:    bool             = True
    status_reason:          str              = ""
    schema_version:         str              = "1.1"
    fi_config_version:      str              = ""
    taxonomy_version:       str              = ""
    source_schema_version:  str              = ""
    source_config_version:  str              = ""
    source_master_data_version: str          = ""
    source_monitoring_config_version: str    = ""
    source_detector_version: str             = ""
    # ***********************

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
