"""
schemas/bearing_signal.py

Defines the core signal schemas:
  - BearingSignalFact     : raw inbound telemetry record (as it arrives from the historian)
  - TrustedBearingSignal  : Data Foundation Agent output — validated, enriched, quality-scored

These are the contracts between the historian/ingest layer and all downstream agents.
Nothing reaches the Monitoring Agent unless it passes through here first.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ValidationStatus(str, Enum):
    VALID    = "VALID"     # all checks passed, safe for downstream
    FLAGGED  = "FLAGGED"   # passed but with quality concerns (low score)
    REJECTED = "REJECTED"  # hard failure — do not route downstream


class MachineState(str, Enum):
    RUNNING  = "running"
    STARTUP  = "startup"
    SHUTDOWN = "shutdown"
    IDLE     = "idle"
    FAULT    = "fault"


# ---------------------------------------------------------------------------
# BearingSignalFact — raw inbound record
# ---------------------------------------------------------------------------

class BearingSignalFact(BaseModel):
    """
    Represents one raw telemetry record exactly as it arrives.
    All fields are optional except the identity triad (asset_id,
    bearing_id, channel_id) and the timestamp.  The Data Foundation
    Agent decides what to do with missing signal fields.
    """
    # Identity
    telemetry_id:           str
    timestamp_utc:          str                    # ISO-8601 string from historian
    asset_id:               str
    bearing_id:             str
    channel_id:             str

    # Operating condition
    rpm:                    Optional[float] = None
    load_pct:               Optional[float] = None
    machine_state:          Optional[str]   = None
    startup_shutdown_flag:  bool            = False

    # Vibration
    vib_rms_mm_s:           Optional[float] = None
    vib_peak_g:             Optional[float] = None
    kurtosis:               Optional[float] = None

    # Thermal
    temp_c:                 Optional[float] = None

    # Band energies — direct threshold values (same unit as fault_taxonomy multiples)
    bpfo_energy:            Optional[float] = None
    bpfi_energy:            Optional[float] = None
    bsf_energy:             Optional[float] = None
    ftf_energy:             Optional[float] = None
    envelope_peak_hz:       Optional[float] = None

    # Electrical
    motor_current_a:        Optional[float] = None
    voltage_v:              Optional[float] = None
    current_deviation:      Optional[float] = None

    # Data quality
    signal_quality_score:   Optional[float] = None  # 0.0–1.0 from historian
    data_source:            str             = "historian"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BearingSignalFact":
        """Build from a raw dict (e.g. JSON row from telemetry_scenarios.json)."""
        required = {"telemetry_id", "timestamp_utc", "asset_id", "bearing_id", "channel_id"}
        missing = required - d.keys()
        if missing:
            raise ValueError(f"BearingSignalFact missing required fields: {missing}")
        known = set(cls.model_fields.keys())
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# TrustedBearingSignal — Data Foundation Agent output
# ---------------------------------------------------------------------------

class AssetContext(BaseModel):
    """Subset of AssetMaster joined onto the signal."""
    asset_id:               str
    asset_name:             str
    asset_type:             str
    bearing_type:           str
    rated_rpm:              int
    power_kw:               float
    production_line_id:     str
    criticality:            str
    is_bottleneck:          bool
    downtime_cost_per_hour: float
    iso_10816_zone:         str
    status:                 str


class BearingContext(BaseModel):
    """Subset of BearingMaster joined onto the signal."""
    bearing_id:             str
    bearing_position:       str
    bearing_model:          str
    lubrication_type:       str
    last_lubrication_date:  str
    current_health_score:   float
    historian_tag:          str
    vib_min_valid:          float
    vib_max_valid:          float
    temp_min_valid:         float
    temp_max_valid:         float
    # Baseline stats — used by Monitoring Agent
    baseline_vib_rms_mean:  float
    baseline_vib_rms_std:   float
    baseline_kurtosis_mean: float
    baseline_kurtosis_std:  float
    baseline_temp_mean:     float
    baseline_temp_std:      float
    baseline_bpfo_mean:     float
    baseline_bpfo_std:      float
    # Pairwise baseline correlations — assembles the T² covariance matrix
    baseline_corr_vib_kurtosis:  float = 0.0
    baseline_corr_vib_temp:      float = 0.0
    baseline_corr_vib_bpfo:      float = 0.0
    baseline_corr_kurtosis_temp: float = 0.0
    baseline_corr_kurtosis_bpfo: float = 0.0
    baseline_corr_temp_bpfo:     float = 0.0


class ValidationDetail(BaseModel):
    """Per-check result stored in TrustedBearingSignal for audit."""
    asset_mapping:    bool      = False  # asset_id found in master        (Integrity)
    bearing_mapping:  bool      = False  # bearing_id + channel found      (Integrity)
    fields_complete:  bool      = False  # all critical fields present     (Completeness)
    ranges_valid:     bool      = False  # values within sensor bounds     (Validity)
    accuracy_ok:      bool      = False  # signal_quality at/above floor   (Accuracy)
    consistency_valid: bool     = False  # cross-field values agree        (Consistency)
    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Timestamp quality is reported separately from the original six checks.
    freshness_valid:   bool     = False  # timestamp syntax and live age   (Freshness)
    # ***********************
    reasons:          List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# QualityReport — per-dimension breakdown of data_quality_score
# ---------------------------------------------------------------------------

class QualityDimension(BaseModel):
    """
    One scoring axis in the quality report.

    Fields:
      name        : machine key matching config weight key (e.g. "completeness")
      label       : human-readable label from config
      description : what this dimension measures and why it matters (from config)
      weight      : contribution to overall score (0.0–1.0, all weights sum to 1.0)
      score       : this dimension's score (0.0–1.0)
      passed      : True when score == 1.0
      reason      : what went wrong (empty string when passed)
      hint        : concrete fix instruction from config (empty when passed)
    """
    name:        str
    label:       str
    weight:      float
    score:       float
    passed:      bool
    description: str = ""
    reason:      str = ""
    hint:        str = ""

    @property
    def weighted_contribution(self) -> float:
        return round(self.weight * self.score, 4)


class QualityReport(BaseModel):
    """
    Full quality breakdown produced by the Data Foundation Agent.

    Attach this to TrustedBearingSignal so any downstream consumer —
    a dashboard, a data engineer, or a future Learning Agent — can see
    exactly why a record scored what it scored and what to fix.
    """
    overall_score: float
    dimensions:    List[QualityDimension] = Field(default_factory=list)

    @property
    def failed_dimensions(self) -> List[QualityDimension]:
        """Dimensions that did not score 1.0 — the ones worth fixing."""
        return [d for d in self.dimensions if not d.passed]

    @property
    def improvement_actions(self) -> List[tuple]:
        """List of (label, hint) tuples for every failed dimension that has a hint."""
        return [
            (d.label, d.hint)
            for d in self.failed_dimensions
            if d.hint
        ]

    def as_text(self) -> str:
        """Human-readable quality report."""
        lines = [f"Quality Score: {self.overall_score:.0%}"]
        for d in self.dimensions:
            icon   = "✓" if d.passed else "✗"
            status = f"{d.score:.0%} (weight {d.weight:.0%})"
            lines.append(f"  {icon}  {d.label:<50} {status}")
            if d.description:
                lines.append(f"       What   : {d.description}")
            if not d.passed and d.reason:
                lines.append(f"       Reason : {d.reason}")
            if not d.passed and d.hint:
                lines.append(f"       Fix    : {d.hint}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TrustedBearingSignal — Data Foundation Agent output
# ---------------------------------------------------------------------------

class TrustedBearingSignal(BaseModel):
    """
    Data Foundation Agent output.  Every downstream agent reads this type.

    Fields:
      raw                : original BearingSignalFact, unchanged
      asset_ctx          : joined AssetMaster context
      bearing_ctx        : joined BearingMaster context (includes baselines)
      validation         : per-check pass/fail booleans
      data_quality_score : 0.0–1.0 composite score
      validation_status  : VALID | FLAGGED | REJECTED
      quality_report     : per-dimension breakdown with reasons and fix hints
      processed_at       : UTC ISO-8601 timestamp from Data Foundation Agent
      remediation_action : "" | IMPUTE | DROP | KEEP — set by the remediation step
      imputed_fields     : signal fields that were filled in during remediation
      imputation_method  : how they were filled (e.g. "baseline_mean")
    """
    raw:                  BearingSignalFact
    asset_ctx:            Optional[AssetContext]   = None
    bearing_ctx:          Optional[BearingContext] = None
    validation:           ValidationDetail         = Field(default_factory=ValidationDetail)
    data_quality_score:   float                    = 0.0
    validation_status:    ValidationStatus         = ValidationStatus.REJECTED
    quality_report:       Optional[QualityReport]  = None
    processed_at:         str                      = ""
    remediation_action:   str                      = ""
    imputed_fields:       List[str]                = Field(default_factory=list)
    imputation_method:    str                      = ""
    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Agent 1 audit, routing, provenance, and durable-ingestion contract fields.
    normalization_actions: List[str]               = Field(default_factory=list)
    unknown_fields:        List[str]               = Field(default_factory=list)
    record_age_seconds:    Optional[float]          = None
    downstream_eligible:   bool                     = False
    next_route:            str                      = "stop"
    routing_reason:        str                      = ""
    schema_version:        str                      = "1.1"
    config_version:        str                      = ""
    master_data_version:   str                      = ""
    source_profile:        str                      = "default"
    persistence_status:    str                      = "not_requested"
    duplicate_detected:    bool                     = False
    out_of_order:          bool                     = False
    # ***********************

    def to_dict(self) -> Dict[str, Any]:
        report_dict = None
        if self.quality_report:
            report_dict = {
                "overall_score": self.quality_report.overall_score,
                "dimensions": [
                    {
                        "name":                  d.name,
                        "label":                 d.label,
                        "description":           d.description,
                        "weight":                d.weight,
                        "score":                 d.score,
                        "passed":                d.passed,
                        "weighted_contribution": d.weighted_contribution,
                        "reason":                d.reason,
                        "hint":                  d.hint,
                    }
                    for d in self.quality_report.dimensions
                ],
                "improvement_actions": [
                    {"dimension": label, "action": hint}
                    for label, hint in self.quality_report.improvement_actions
                ],
            }
        return {
            "raw":                self.raw.model_dump(),
            "asset_ctx":          self.asset_ctx.model_dump() if self.asset_ctx else None,
            "bearing_ctx":        self.bearing_ctx.model_dump() if self.bearing_ctx else None,
            "validation":         self.validation.model_dump(),
            "data_quality_score": round(self.data_quality_score, 4),
            "validation_status":  self.validation_status.value,
            "quality_report":     report_dict,
            "processed_at":       self.processed_at,
            "remediation_action": self.remediation_action,
            "imputed_fields":     list(self.imputed_fields),
            "imputation_method":  self.imputation_method,
            # ************** Added by Prateek Mittal on 16th July 2026 ******************
            # Serialize every new audit/provenance field for APIs and persistence.
            "normalization_actions": list(self.normalization_actions),
            "unknown_fields":        list(self.unknown_fields),
            "record_age_seconds":    self.record_age_seconds,
            "downstream_eligible":   self.downstream_eligible,
            "next_route":            self.next_route,
            "routing_reason":        self.routing_reason,
            "schema_version":        self.schema_version,
            "config_version":        self.config_version,
            "master_data_version":   self.master_data_version,
            "source_profile":        self.source_profile,
            "persistence_status":    self.persistence_status,
            "duplicate_detected":    self.duplicate_detected,
            "out_of_order":          self.out_of_order,
            # ***********************
        }

    @property
    def is_processable(self) -> bool:
        """True when safe to pass to the Monitoring Agent."""
        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Processability now follows the explicit Agent 1 routing decision.
        return self.downstream_eligible
        # ***********************