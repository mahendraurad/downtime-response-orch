"""
tools/config_loader.py

Loads dfa_config.json and exposes all values as a typed DFAConfig object.
Every hardcoded constant that was previously scattered across validators.py
and data_foundation_agent.py now lives in config/dfa_config.json and is
accessed through this module.

Usage:
    from src.tools.config_loader import load_config

    cfg = load_config()
    cfg.min_signal_quality_score   # 0.50
    cfg.weights["asset_mapping"]   # 0.25
    cfg.improvement_hints["completeness"]   # "Ensure historian publishes..."

The config is loaded once per process. Call load_config() at agent
construction time and pass the result in — do not call it inside a
per-record hot path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List


_HERE    = os.path.dirname(os.path.abspath(__file__))
_CONFIG  = os.path.join(_HERE, "..", "..", "config", "dfa_config.json")
_MON_CONFIG = os.path.join(_HERE, "..", "..", "config", "monitoring_config.json")
_FI_CONFIG  = os.path.join(_HERE, "..", "..", "config", "fi_config.json")
_RISK_CONFIG = os.path.join(_HERE, "..", "..", "config", "risk_config.json")


# ---------------------------------------------------------------------------
# Typed config dataclass
# ---------------------------------------------------------------------------

@dataclass
class DFAConfig:
    # ── Validation thresholds ────────────────────────────────────────
    min_signal_quality_score:           float
    max_rpm:                            float
    max_load_pct:                       float
    min_kurtosis:                       float
    critical_signal_fields:             List[str]
    advisory_reason_keywords:           List[str]

    # ── Cross-field consistency rules ────────────────────────────────
    running_states:         List[str]
    stopped_states:         List[str]
    running_min_rpm:        float
    stopped_rpm_threshold:  float
    stopped_max_vib_mm_s:   float

    # ── Quality score ────────────────────────────────────────────────
    flagged_threshold:      float
    weights:                Dict[str, float]
    dimension_labels:       Dict[str, str]
    dimension_descriptions: Dict[str, str]
    improvement_hints:      Dict[str, str]

    # ── Remediation policy ───────────────────────────────────────────
    remediation_default_action: str
    auto_impute_method:         str
    imputable_fields:           Dict[str, str]   # signal field -> baseline attr
    never_impute_fields:        List[str]

    # ── Fallback sensor bounds ───────────────────────────────────────
    fallback_vib_min:   float
    fallback_vib_max:   float
    fallback_temp_min:  float
    fallback_temp_max:  float

    # ── Logging ──────────────────────────────────────────────────────
    log_rejected: bool
    log_flagged:  bool
    log_valid:    bool

    def validate(self) -> None:
        """Sanity-check the config on load so bad edits are caught early."""
        weight_sum = round(sum(self.weights.values()), 6)
        if abs(weight_sum - 1.0) > 0.001:
            raise ValueError(
                f"quality_score.weights must sum to 1.0, got {weight_sum}. "
                f"Edit config/dfa_config.json to fix."
            )
        if not (0.0 < self.flagged_threshold <= 1.0):
            raise ValueError(
                f"flagged_threshold must be between 0 and 1, got {self.flagged_threshold}"
            )
        if not self.critical_signal_fields:
            raise ValueError("critical_signal_fields must not be empty")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(path: str = _CONFIG) -> DFAConfig:
    """
    Load and return a DFAConfig from the JSON file.
    Raises FileNotFoundError if config is missing.
    Raises ValueError if weights don't sum to 1.0.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Config file not found: {os.path.abspath(path)}\n"
            f"Expected at: config/dfa_config.json"
        )

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    v   = raw["validation"]
    c   = raw["consistency"]
    qs  = raw["quality_score"]
    rem = raw["remediation"]
    fb  = raw["fallback_sensor_bounds"]
    log = raw["logging"]

    cfg = DFAConfig(
        # validation
        min_signal_quality_score           = v["min_signal_quality_score"],
        max_rpm                            = v["max_rpm"],
        max_load_pct                       = v["max_load_pct"],
        min_kurtosis                       = v["min_kurtosis"],
        critical_signal_fields             = v["critical_signal_fields"],
        advisory_reason_keywords           = v["advisory_reason_keywords"],
        # consistency
        running_states        = c["running_states"],
        stopped_states        = c["stopped_states"],
        running_min_rpm       = c["running_min_rpm"],
        stopped_rpm_threshold = c["stopped_rpm_threshold"],
        stopped_max_vib_mm_s  = c["stopped_max_vib_mm_s"],
        # quality score
        flagged_threshold      = qs["flagged_threshold"],
        weights                = qs["weights"],
        dimension_labels       = qs["dimension_labels"],
        dimension_descriptions = qs["dimension_descriptions"],
        improvement_hints      = qs["improvement_hints"],
        # remediation
        remediation_default_action = rem["default_action"],
        auto_impute_method         = rem["auto_impute_method"],
        imputable_fields           = rem["imputable_fields"],
        never_impute_fields        = rem["never_impute_fields"],
        # fallback bounds
        fallback_vib_min  = fb["vib_min_mm_s"],
        fallback_vib_max  = fb["vib_max_mm_s"],
        fallback_temp_min = fb["temp_min_c"],
        fallback_temp_max = fb["temp_max_c"],
        # logging
        log_rejected = log["log_rejected"],
        log_flagged  = log["log_flagged"],
        log_valid    = log["log_valid"],
    )
    cfg.validate()
    return cfg


# ===========================================================================
# Monitoring Agent config (Phase 3)
# ===========================================================================

@dataclass
class MonitoringConfig:
    # ── Anomaly scoring (Hotelling T²) ───────────────────────────────
    anomaly_threshold:      float
    min_signals_for_t2:     int
    signal_order:           List[str]
    signal_baselines:       Dict[str, Dict[str, str]]  # field -> {mean: attr, std: attr}
    trigger_z:              float
    exclude_imputed_fields: bool

    # ── Regime classifier ────────────────────────────────────────────
    running_states:          List[str]
    running_min_rpm:         float
    rated_rpm_tolerance_pct: float
    low_load_max_pct:        float
    high_load_min_pct:       float

    # ── EWMA control chart (parallel to T²) ──────────────────────────
    ewma_enabled:          bool
    ewma_alpha:            float
    ewma_control_limit:    float
    ewma_signals:          List[str]
    ewma_state_file:       str
    ewma_exclude_imputed:  bool

    # ── Confidence ───────────────────────────────────────────────────
    confidence_weights: Dict[str, float]

    # ── Logging ──────────────────────────────────────────────────────
    log_anomalies: bool
    log_healthy:   bool

    def validate(self) -> None:
        if not (0.0 < self.anomaly_threshold <= 1.0):
            raise ValueError(
                f"anomaly_threshold must be between 0 and 1, "
                f"got {self.anomaly_threshold}"
            )
        if self.min_signals_for_t2 < 1:
            raise ValueError("min_signals_for_t2 must be >= 1")
        if self.ewma_enabled:
            if not (0.0 < self.ewma_alpha <= 1.0):
                raise ValueError(
                    f"ewma.alpha must be in (0, 1], got {self.ewma_alpha}"
                )
            if self.ewma_control_limit <= 0:
                raise ValueError(
                    f"ewma.control_limit_L must be positive, "
                    f"got {self.ewma_control_limit}"
                )


def load_monitoring_config(path: str = _MON_CONFIG) -> MonitoringConfig:
    """Load and return a MonitoringConfig from the JSON file."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Monitoring config not found: {os.path.abspath(path)}\n"
            f"Expected at: config/monitoring_config.json"
        )

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    a    = raw["anomaly"]
    reg  = raw["regime"]
    ewma = raw.get("ewma", {"enabled": False})
    conf = raw["confidence"]
    log  = raw["logging"]

    cfg = MonitoringConfig(
        # anomaly
        anomaly_threshold      = a["anomaly_threshold"],
        min_signals_for_t2     = a["min_signals_for_t2"],
        signal_order           = a["signal_order"],
        signal_baselines       = a["signal_baselines"],
        trigger_z              = a["trigger_z"],
        exclude_imputed_fields = a["exclude_imputed_fields"],
        # regime
        running_states          = reg["running_states"],
        running_min_rpm         = reg["running_min_rpm"],
        rated_rpm_tolerance_pct = reg["rated_rpm_tolerance_pct"],
        low_load_max_pct        = reg["low_load_max_pct"],
        high_load_min_pct       = reg["high_load_min_pct"],
        # ewma
        ewma_enabled         = ewma.get("enabled", False),
        ewma_alpha           = ewma.get("alpha", 0.2),
        ewma_control_limit   = ewma.get("control_limit_L", 3.0),
        ewma_signals         = ewma.get("signals", []),
        ewma_state_file      = ewma.get("state_file", "data/ewma_state.json"),
        ewma_exclude_imputed = ewma.get("exclude_imputed_fields", True),
        # confidence
        confidence_weights = conf["weights"],
        # logging
        log_anomalies = log["log_anomalies"],
        log_healthy   = log["log_healthy"],
    )
    cfg.validate()
    return cfg


# ===========================================================================
# Failure Intelligence Agent config (Phase 4)
# ===========================================================================

@dataclass
class FailureIntelligenceConfig:
    # ── Severity policy ──────────────────────────────────────────────
    severity_stage_base:        Dict[str, str]   # "1"/"2"/"3" -> base label
    severity_escalation_order:  List[str]
    escalate_if_high_criticality: bool
    escalate_if_bottleneck:     bool
    high_criticality_label:     str
    undetermined_severity:      str

    # ── Broadband (lubrication) detection ────────────────────────────
    broadband_vib_elevation_ratio: float

    # ── Recommended checks ───────────────────────────────────────────
    fault_checks:               Dict[str, List[str]]  # fault_code -> checklist

    # ── Confidence blend ─────────────────────────────────────────────
    confidence_weights:         Dict[str, float]      # anomaly_confidence / match_strength

    # ── Logging ──────────────────────────────────────────────────────
    log_diagnoses:              bool

    def validate(self) -> None:
        for stage in ("1", "2", "3"):
            if stage not in self.severity_stage_base:
                raise ValueError(
                    f"severity.stage_base must define stage '{stage}'. "
                    f"Edit config/fi_config.json to fix."
                )
        if not self.severity_escalation_order:
            raise ValueError("severity.escalation_order must not be empty")
        if not (0.0 < self.broadband_vib_elevation_ratio):
            raise ValueError(
                f"broadband.vib_elevation_ratio must be positive, "
                f"got {self.broadband_vib_elevation_ratio}"
            )
        weight_sum = round(sum(self.confidence_weights.values()), 6)
        if abs(weight_sum - 1.0) > 0.001:
            raise ValueError(
                f"confidence.weights must sum to 1.0, got {weight_sum}. "
                f"Edit config/fi_config.json to fix."
            )


def load_fi_config(path: str = _FI_CONFIG) -> FailureIntelligenceConfig:
    """Load and return a FailureIntelligenceConfig from the JSON file."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Failure Intelligence config not found: {os.path.abspath(path)}\n"
            f"Expected at: config/fi_config.json"
        )

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    sev  = raw["severity"]
    bb   = raw["broadband"]
    conf = raw["confidence"]
    log  = raw["logging"]

    cfg = FailureIntelligenceConfig(
        # severity
        severity_stage_base          = sev["stage_base"],
        severity_escalation_order    = sev["escalation_order"],
        escalate_if_high_criticality = sev["escalate_if_high_criticality"],
        escalate_if_bottleneck       = sev["escalate_if_bottleneck"],
        high_criticality_label       = sev["high_criticality_label"],
        undetermined_severity        = sev["undetermined_severity"],
        # broadband
        broadband_vib_elevation_ratio = bb["vib_elevation_ratio"],
        # checks
        fault_checks                 = raw["fault_checks"],
        # confidence
        confidence_weights           = conf["weights"],
        # logging
        log_diagnoses                = log["log_diagnoses"],
    )
    cfg.validate()
    return cfg


# ===========================================================================
# Predictive Risk Agent config (Phase 5)
# ===========================================================================

@dataclass
class RiskConfig:
    # ── Failure probability blend ────────────────────────────────────
    fp_stage_base:  Dict[str, float]   # "0"/"1"/"2"/"3" -> base probability
    fp_weights:     Dict[str, float]   # stage_base / anomaly_score (sum to 1.0)

    # ── Risk level ───────────────────────────────────────────────────
    risk_level_source:   str           # "diagnosis_severity"
    risk_level_fallback: str

    # ── Business impact flagging ─────────────────────────────────────
    flag_if_bottleneck:       bool
    flag_if_high_criticality: bool
    high_criticality_label:   str

    # ── Monitor band (iso_stage == 0 / undetermined) ─────────────────
    monitor_rul_min: int
    monitor_rul_max: int
    monitor_label:   str

    # ── LLM fallback (optional advisory layer) ───────────────────────
    llm_enabled:                 bool
    llm_trigger_on_undetermined: bool
    llm_low_confidence_below:    float
    llm_temperature:             float
    llm_max_tokens:              int
    llm_timeout_seconds:         float
    llm_hitl_enabled:            bool   # interactive runners should inject HITL handler

    # ── Logging ──────────────────────────────────────────────────────
    log_assessments: bool

    def validate(self) -> None:
        for stage in ("1", "2", "3"):
            if stage not in self.fp_stage_base:
                raise ValueError(
                    f"failure_probability.stage_base must define stage '{stage}'. "
                    f"Edit config/risk_config.json to fix."
                )
        for stage, prob in self.fp_stage_base.items():
            if not (0.0 <= prob <= 1.0):
                raise ValueError(
                    f"failure_probability.stage_base['{stage}'] must be in [0, 1], "
                    f"got {prob}"
                )
        weight_sum = round(sum(self.fp_weights.values()), 6)
        if abs(weight_sum - 1.0) > 0.001:
            raise ValueError(
                f"failure_probability.weights must sum to 1.0, got {weight_sum}. "
                f"Edit config/risk_config.json to fix."
            )


def load_risk_config(path: str = _RISK_CONFIG) -> RiskConfig:
    """Load and return a RiskConfig from the JSON file."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Predictive Risk config not found: {os.path.abspath(path)}\n"
            f"Expected at: config/risk_config.json"
        )

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    fp  = raw["failure_probability"]
    rl  = raw["risk_level"]
    bi  = raw["business_impact"]
    mb  = raw["monitor_band"]
    llm = raw.get("llm_fallback", {})
    log = raw["logging"]

    cfg = RiskConfig(
        # failure probability
        fp_stage_base = fp["stage_base"],
        fp_weights    = fp["weights"],
        # risk level
        risk_level_source   = rl["source"],
        risk_level_fallback = rl["fallback"],
        # business impact
        flag_if_bottleneck       = bi["flag_if_bottleneck"],
        flag_if_high_criticality = bi["flag_if_high_criticality"],
        high_criticality_label   = bi["high_criticality_label"],
        # monitor band
        monitor_rul_min = mb["rul_min_days"],
        monitor_rul_max = mb["rul_max_days"],
        monitor_label   = mb["label"],
        # llm fallback (optional block — safe defaults keep it disabled)
        llm_enabled                 = llm.get("enabled", False),
        llm_trigger_on_undetermined = llm.get("trigger_on_undetermined", True),
        llm_low_confidence_below    = llm.get("trigger_low_confidence_below", 0.0),
        llm_temperature             = llm.get("temperature", 0.2),
        llm_max_tokens              = llm.get("max_tokens", 500),
        llm_timeout_seconds         = llm.get("timeout_seconds", 20),
        llm_hitl_enabled            = llm.get("hitl_enabled", False),
        # logging
        log_assessments = log["log_assessments"],
    )
    cfg.validate()
    return cfg