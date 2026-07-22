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
_KNOWLEDGE_CONFIG = os.path.join(_HERE, "..", "..", "config", "knowledge_config.json")
_PRESCRIPTIVE_CONFIG = os.path.join(_HERE, "..", "..", "config", "prescriptive_config.json")
_EXECUTOR_CONFIG = os.path.join(_HERE, "..", "..", "config", "executor_config.json")
_LEARNING_CONFIG = os.path.join(_HERE, "..", "..", "config", "learning_config.json")


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

    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Configuration contract for Agent 1 freshness, normalization, routing,
    # source profiles, and durable ingestion behavior.
    require_timestamp_timezone: bool
    max_future_seconds:         float
    max_age_seconds:            float
    live_sources:               List[str]
    field_aliases:              Dict[str, str]
    unit_conversions:           Dict[str, Dict]
    source_profiles:            Dict[str, Dict]
    routes:                     Dict[str, str]
    repository_sqlite_path:     str
    reject_duplicates:          bool
    flag_out_of_order:          bool
    # ***********************

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
        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Fail fast when newly added time/routing policies are invalid.
        if self.max_future_seconds < 0 or self.max_age_seconds <= 0:
            raise ValueError("freshness time limits must be positive")
        if not {"VALID", "FLAGGED", "REJECTED"}.issubset(self.routes):
            raise ValueError("routing must define VALID, FLAGGED, and REJECTED")
        # ***********************


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
    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Load the Agent 1 hardening and persistence configuration blocks.
    fresh = raw.get("freshness", {})
    norm = raw.get("normalization", {})
    routes = raw.get("routing", {})
    repo = raw.get("repository", {})
    # ***********************
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
        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Materialize the new policy blocks into the typed DFA configuration.
        require_timestamp_timezone = fresh.get("require_timezone", True),
        max_future_seconds         = fresh.get("max_future_seconds", 30),
        max_age_seconds            = fresh.get("max_age_seconds", 300),
        live_sources               = fresh.get("live_sources", ["live"]),
        field_aliases              = norm.get("field_aliases", {}),
        unit_conversions           = norm.get("unit_conversions", {}),
        source_profiles            = norm.get("source_profiles", {}),
        routes                     = routes or {"VALID": "monitoring", "FLAGGED": "data_review", "REJECTED": "stop"},
        repository_sqlite_path     = repo.get("sqlite_path", "data/trusted_signals.db"),
        reject_duplicates          = repo.get("reject_duplicates", True),
        flag_out_of_order          = repo.get("flag_out_of_order", True),
        # ***********************
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

    # ************** Added by Prateek Mittal on 17th July 2026 ******************
    # Stateful detector namespace, lightweight alert policy, and decision store.
    state_namespace_version:      str
    min_consecutive_anomalies:    int
    alert_cooldown_seconds:       float
    decision_repository_path:     str
    # ***********************

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
        # ************** Added by Prateek Mittal on 17th July 2026 ******************
        # Validate the complete detector configuration instead of accepting
        # inconsistent confidence/signal/state policies at runtime.
        if abs(sum(self.confidence_weights.values()) - 1.0) > 0.001:
            raise ValueError("monitoring confidence weights must sum to 1.0")
        if self.trigger_z <= 0:
            raise ValueError("trigger_z must be positive")
        missing = [signal for signal in self.signal_order if signal not in self.signal_baselines]
        if missing:
            raise ValueError(f"signal_baselines missing mappings for: {missing}")
        invalid_ewma = [signal for signal in self.ewma_signals if signal not in self.signal_baselines]
        if invalid_ewma:
            raise ValueError(f"ewma signals missing baseline mappings: {invalid_ewma}")
        if self.min_consecutive_anomalies < 1:
            raise ValueError("min_consecutive_anomalies must be >= 1")
        if self.alert_cooldown_seconds < 0:
            raise ValueError("alert cooldown must be >= 0")
        # ***********************


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
    # ************** Added by Prateek Mittal on 17th July 2026 ******************
    state_cfg = raw.get("state", {})
    alert = raw.get("alert_policy", {})
    decision_repo = raw.get("decision_repository", {})
    # ***********************
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
        # ************** Added by Prateek Mittal on 17th July 2026 ******************
        state_namespace_version   = state_cfg.get("namespace_version", "monitoring-state-v2"),
        min_consecutive_anomalies = alert.get("min_consecutive_anomalies", 1),
        alert_cooldown_seconds    = alert.get("cooldown_seconds", 0),
        decision_repository_path  = decision_repo.get("sqlite_path", "data/monitoring_decisions.db"),
        # ***********************
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
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        # Validate the complete Agent 3 policy, not only the sum of confidence
        # weights. This prevents valid-looking JSON from producing undefined
        # severity labels or silently omitting one confidence component.
        for stage in ("1", "2", "3"):
            if stage not in self.severity_stage_base:
                raise ValueError(
                    f"severity.stage_base must define stage '{stage}'. "
                    f"Edit config/fi_config.json to fix."
                )
        if not self.severity_escalation_order:
            raise ValueError("severity.escalation_order must not be empty")
        labels = set(self.severity_escalation_order)
        if any(value not in labels for value in self.severity_stage_base.values()):
            raise ValueError("severity.stage_base contains a label outside escalation_order")
        if self.undetermined_severity not in labels:
            raise ValueError("severity.undetermined_severity must be in escalation_order")
        if not (0.0 < self.broadband_vib_elevation_ratio):
            raise ValueError(
                f"broadband.vib_elevation_ratio must be positive, "
                f"got {self.broadband_vib_elevation_ratio}"
            )
        expected_weights = {"anomaly_confidence", "match_strength"}
        if set(self.confidence_weights) != expected_weights:
            raise ValueError(
                "confidence.weights must define anomaly_confidence and match_strength"
            )
        if any(not isinstance(v, (int, float)) or not 0 <= v <= 1
               for v in self.confidence_weights.values()):
            raise ValueError("confidence.weights values must be between 0 and 1")
        weight_sum = round(sum(self.confidence_weights.values()), 6)
        if abs(weight_sum - 1.0) > 0.001:
            raise ValueError(
                f"confidence.weights must sum to 1.0, got {weight_sum}. "
                f"Edit config/fi_config.json to fix."
            )
        # ***********************


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
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        # Validate every tunable that can change a risk card. Agent 4 must fail
        # at startup rather than emit probabilities/RUL from contradictory policy.
        import math
        for stage in ("0", "1", "2", "3"):
            if stage not in self.fp_stage_base:
                raise ValueError(
                    f"failure_probability.stage_base must define stage '{stage}'. "
                    f"Edit config/risk_config.json to fix."
                )
        for stage, prob in self.fp_stage_base.items():
            if (isinstance(prob, bool) or not isinstance(prob, (int, float))
                    or not math.isfinite(prob) or not (0.0 <= prob <= 1.0)):
                raise ValueError(
                    f"failure_probability.stage_base['{stage}'] must be in [0, 1], "
                    f"got {prob}"
                )
        if set(self.fp_weights) != {"stage_base", "anomaly_score"}:
            raise ValueError("failure_probability.weights must define stage_base and anomaly_score")
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or not 0 <= v <= 1
               for v in self.fp_weights.values()):
            raise ValueError("failure_probability.weights must be finite values in [0, 1]")
        weight_sum = round(sum(self.fp_weights.values()), 6)
        if abs(weight_sum - 1.0) > 0.001:
            raise ValueError(
                f"failure_probability.weights must sum to 1.0, got {weight_sum}. "
                f"Edit config/risk_config.json to fix."
            )
        ordered = [self.fp_stage_base[str(stage)] for stage in range(4)]
        if ordered != sorted(ordered):
            raise ValueError("failure_probability.stage_base must not decrease as stage increases")
        if self.risk_level_source != "diagnosis_severity":
            raise ValueError("risk_level.source must be diagnosis_severity")
        if self.risk_level_fallback not in {"low", "medium", "high", "critical"}:
            raise ValueError("risk_level.fallback is invalid")
        if (isinstance(self.monitor_rul_min, bool) or isinstance(self.monitor_rul_max, bool)
                or self.monitor_rul_min < 0 or self.monitor_rul_max < self.monitor_rul_min):
            raise ValueError("monitor band must have 0 <= minimum <= maximum")
        if not self.monitor_label:
            raise ValueError("monitor band label must not be empty")
        if not 0 <= self.llm_low_confidence_below <= 1:
            raise ValueError("LLM low-confidence threshold must be in [0, 1]")
        if not 0 <= self.llm_temperature <= 2:
            raise ValueError("LLM temperature must be in [0, 2]")
        if self.llm_max_tokens <= 0 or self.llm_timeout_seconds <= 0:
            raise ValueError("LLM max_tokens and timeout_seconds must be positive")
        # ***********************


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


# ************** Added by Prateek Mittal on 20th July 2026 ******************
# Agent 5 retrieval and grounding policy. Kept in the shared loader so the
# agent, tests, and future Azure Search adapter use one validated contract.
@dataclass
class KnowledgeConfig:
    top_k: int
    minimum_score: float
    fault_mode_boost: float
    asset_type_boost: float
    iso_stage_boost: float
    require_source: bool
    allow_generic_documents: bool
    max_relevant_sections: int
    max_inspection_steps: int
    max_safety_notes: int
    section_preview_characters: int
    log_retrievals: bool

    def validate(self) -> None:
        import math
        for name in (
            "top_k", "max_relevant_sections", "max_inspection_steps",
            "max_safety_notes", "section_preview_characters",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"knowledge.{name} must be a positive integer")
        for name in (
            "minimum_score", "fault_mode_boost", "asset_type_boost",
            "iso_stage_boost",
        ):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"knowledge.{name} must be finite and non-negative")


def load_knowledge_config(path: str = _KNOWLEDGE_CONFIG) -> KnowledgeConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Knowledge config not found: {os.path.abspath(path)}\n"
            "Expected at: config/knowledge_config.json"
        )
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    retrieval = raw["retrieval"]
    output = raw["output"]
    cfg = KnowledgeConfig(
        top_k=retrieval["top_k"],
        minimum_score=retrieval["minimum_score"],
        fault_mode_boost=retrieval["fault_mode_boost"],
        asset_type_boost=retrieval["asset_type_boost"],
        iso_stage_boost=retrieval["iso_stage_boost"],
        require_source=retrieval["require_source"],
        allow_generic_documents=retrieval["allow_generic_documents"],
        max_relevant_sections=output["max_relevant_sections"],
        max_inspection_steps=output["max_inspection_steps"],
        max_safety_notes=output["max_safety_notes"],
        section_preview_characters=output["section_preview_characters"],
        log_retrievals=raw["logging"]["log_retrievals"],
    )
    cfg.validate()
    return cfg
# ***********************


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@dataclass
class PrescriptiveConfig:
    risk_actions: Dict[str, List[str]]
    durations_hours: Dict[str, float]
    urgency: Dict[str, str]
    approval_actions: List[str]
    replacement_faults: List[str]
    lubrication_part: str
    personnel: Dict[str, str]
    llm_rationale_enabled: bool
    llm_max_characters: int
    log_recommendations: bool

    def validate(self) -> None:
        allowed = {"continue_monitoring", "inspect_next_shift", "repair_next_planned_stop",
                   "derate_and_monitor", "stop_and_replace", "lubrication_service"}
        if set(self.risk_actions) != {"low", "medium", "high", "critical"}:
            raise ValueError("prescriptive risk_actions must cover low, medium, high, critical")
        if any(not actions or not set(actions) <= allowed for actions in self.risk_actions.values()):
            raise ValueError("prescriptive risk action contains an unsupported or empty ranking")
        if any(a not in self.durations_hours or self.durations_hours[a] < 0 for a in allowed):
            raise ValueError("every prescriptive action requires a non-negative duration")
        if set(self.urgency) != {"low", "medium", "high", "critical"}:
            raise ValueError("prescriptive urgency must cover every risk level")
        if not set(self.approval_actions) <= allowed:
            raise ValueError("prescriptive approval action is unsupported")
        if self.llm_max_characters <= 0:
            raise ValueError("prescriptive LLM rationale limit must be positive")


def load_prescriptive_config(path: str = _PRESCRIPTIVE_CONFIG) -> PrescriptiveConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prescriptive config not found: {os.path.abspath(path)}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    llm = raw.get("llm_rationale", {})
    cfg = PrescriptiveConfig(
        risk_actions=raw["risk_actions"], durations_hours=raw["durations_hours"],
        urgency=raw["urgency"], approval_actions=raw["approval_actions"],
        replacement_faults=raw["replacement_faults"], lubrication_part=raw["lubrication_part"],
        personnel=raw["personnel"], llm_rationale_enabled=llm.get("enabled", False),
        llm_max_characters=llm.get("max_characters", 800),
        log_recommendations=raw["logging"]["log_recommendations"],
    )
    cfg.validate()
    return cfg
# ***********************


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@dataclass
class ExecutorConfig:
    allowed_actions: List[str]
    priority_by_urgency: Dict[str, str]
    escalate_urgencies: List[str]
    repository_path: str
    reject_duplicate_cases: bool
    log_execution: bool

    def validate(self):
        if not self.allowed_actions or len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("executor allowed_actions must be unique and non-empty")
        if set(self.priority_by_urgency) != {"immediate", "urgent", "planned", "monitor"}:
            raise ValueError("executor priority map must cover every urgency")
        if not set(self.escalate_urgencies) <= set(self.priority_by_urgency):
            raise ValueError("executor escalation urgency is unsupported")


def load_executor_config(path: str = _EXECUTOR_CONFIG) -> ExecutorConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Executor config not found: {os.path.abspath(path)}")
    with open(path, "r", encoding="utf-8") as fh: raw = json.load(fh)
    repo = raw["repository"]
    cfg = ExecutorConfig(raw["allowed_actions"], raw["priority_by_urgency"],
        raw["escalate_urgencies"], repo["sqlite_path"], repo["reject_duplicate_cases"],
        raw["logging"]["log_execution"])
    cfg.validate(); return cfg
# ***********************


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@dataclass
class LearningConfig:
    repository_path: str
    training_export_path: str
    allowed_execution_statuses: List[str]
    require_timezone: bool
    require_root_cause: bool
    llm_narrative_enabled: bool
    llm_max_characters: int
    minimum_score: float
    top_k: int
    log_learned_cases: bool
    def validate(self):
        if not self.allowed_execution_statuses or not set(self.allowed_execution_statuses) <= {"success", "partial"}:
            raise ValueError("learning execution statuses are invalid")
        if self.llm_max_characters <= 0 or self.top_k <= 0 or not 0 <= self.minimum_score <= 1:
            raise ValueError("learning numeric policy is invalid")


def load_learning_config(path: str = _LEARNING_CONFIG) -> LearningConfig:
    if not os.path.exists(path): raise FileNotFoundError(f"Learning config not found: {os.path.abspath(path)}")
    with open(path, "r", encoding="utf-8") as fh: raw = json.load(fh)
    val, llm, ret = raw["validation"], raw["llm_narrative"], raw["retrieval"]
    cfg = LearningConfig(raw["repository"]["json_path"], raw["training_export"]["jsonl_path"],
        val["allowed_execution_statuses"], val["require_timezone"], val["require_root_cause"],
        llm["enabled"], llm["max_characters"], ret["minimum_score"], ret["top_k"],
        raw["logging"]["log_learned_cases"])
    cfg.validate(); return cfg
# ***********************