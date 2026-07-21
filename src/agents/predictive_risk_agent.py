"""
agents/predictive_risk_agent.py  —  Phase 5

Predictive Risk Agent — turns a named fault + ISO stage into a remaining-
useful-life window, a failure probability, a risk level, and the financial
exposure of letting the asset fail.

Question it answers:  "How long do we have and what does it cost?"

Entry point:  PredictiveRiskAgent.process(diagnosis, anomaly, trusted) -> RiskAssessment

Pipeline:
  1. RUL band — taxonomy-driven via rul_calculator.get_rul_band (Design A):
       stage 3 → (0, rul_days_stage_3),  stage 2 → (s3, s2),  stage 1 → (s2, s1)
       iso_stage 0 / undetermined        → the configured monitor band.
  2. failure_probability — config blend of stage_base[iso_stage] and the
     anomaly_score.  health_index = 1 - failure_probability.
  3. business_impact_flag — raised for bottleneck and/or high-criticality
     assets (from asset_ctx).
  4. financial_exposure = rul_max_days * 24 * downtime_cost_per_hour.
     Forced to 0 for the monitor band (no quantified timeline).
  5. risk_level — reuses the already-escalated FaultDiagnosis.severity so it
     never diverges from the upstream agent.

Never raises: a missing asset context degrades exposure/flag gracefully; an
undetermined diagnosis (iso_stage 0) returns a monitor-band assessment.

LLM fallback (optional, config-gated, disabled by default):
  When the rule engine cannot assess confidently — an undetermined/monitor-band
  diagnosis, or a match with confidence below a configured threshold — and
  llm_fallback.enabled is true with Azure OpenAI credentials present, an LLM is
  asked for an advisory judgement. The deterministic RUL/exposure numbers remain
  the auditable source of truth; the LLM adds a recommended_action + rationale
  (advisory_note) and, for the undetermined case only, may set risk_level. If the
  LLM is unavailable or errors, the deterministic assessment is returned unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, List, Dict, Optional

from src.schemas.anomaly import AnomalyEvent
from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.bearing_signal import TrustedBearingSignal
from src.schemas.risk import RiskAssessment
from src.tools.rul_calculator import get_rul_band, compute_financial_exposure
from src.tools.config_loader import RiskConfig, load_risk_config
from src.tools.llm_client import LLMClient
from src.tools.failure_intelligence_utilities import stable_version, validate_taxonomy
from src.tools.predictive_risk_utilities import (
    VALID_RISK_LEVELS,
    build_risk_explanation,
    validate_risk_handoff,
)

logger = logging.getLogger(__name__)

_VALID_RISK_LEVELS = VALID_RISK_LEVELS


class PredictiveRiskAgent:
    """Stateless agent. Taxonomy rules + config injected at construction."""

    def __init__(self, taxonomy_rules: List[Dict],
                 cfg: RiskConfig = None,
                 llm_client: LLMClient = None,
                 hitl_handler: Optional[Callable] = None):
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        # Agent 4 shares Agent 3's taxonomy and independently validates it because
        # it may be instantiated or deployed separately from Agent 3.
        validate_taxonomy(taxonomy_rules)
        self._rules = taxonomy_rules
        self._by_code = {rule["fault_code"]: rule for rule in taxonomy_rules}
        self._cfg = cfg or load_risk_config()
        self._cfg.validate()
        self._config_version = stable_version(self._cfg)
        self._taxonomy_version = stable_version(taxonomy_rules)
        # ***********************
        # Lazily usable LLM client; only ever called when the fallback triggers
        # and llm_enabled is true. is_configured() gates the actual API call.
        self._llm = llm_client or LLMClient(
            timeout_seconds=self._cfg.llm_timeout_seconds
        )
        # Optional HITL callback: hitl_handler(llm_result, assessment, diagnosis,
        # anomaly, actx) -> dict | None.  Return the (possibly modified) dict to
        # accept, or None to reject the LLM advisory (deterministic result kept).
        # Tests leave this None; interactive runners inject an operator-review fn.
        self._hitl = hitl_handler

    @classmethod
    def from_data_files(cls) -> "PredictiveRiskAgent":
        """Factory — load taxonomy from data/ and config from config/."""
        from src.tools.data_loader import load_fault_taxonomy
        return cls(load_fault_taxonomy(), load_risk_config())

    # ------------------------------------------------------------------

    def process(self, diagnosis: FaultDiagnosis,
                anomaly: AnomalyEvent,
                trusted: TrustedBearingSignal) -> RiskAssessment:
        """Assess one diagnosis. Never raises."""
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        # Reject inconsistent Agent 1-3 handoffs explicitly. A runtime input error
        # is not the same as a valid undetermined diagnosis/monitor assessment.
        invalid_reason = validate_risk_handoff(
            diagnosis, anomaly, trusted, self._by_code, self._taxonomy_version
        )
        if invalid_reason:
            return self._invalid(diagnosis, anomaly, trusted, invalid_reason)
        # ***********************
        cfg  = self._cfg
        actx = trusted.asset_ctx
        processed_at = datetime.now(tz=timezone.utc).isoformat()

        iso_stage  = diagnosis.iso_stage
        fault_code = diagnosis.fault_code
        is_monitor = iso_stage not in (1, 2, 3) or not fault_code

        # ── Step 1: RUL band (taxonomy-driven) ──────────────────────────
        rul_min, rul_max, band_label = get_rul_band(
            iso_stage, fault_code, self._rules,
            monitor_band=(cfg.monitor_rul_min, cfg.monitor_rul_max,
                          cfg.monitor_label),
        )

        # ── Step 2: failure probability + health index ──────────────────
        stage_base = cfg.fp_stage_base.get(str(iso_stage),
                                           cfg.fp_stage_base.get("0", 0.0))
        w = cfg.fp_weights
        failure_probability = round(
            w["stage_base"] * stage_base
            + w["anomaly_score"] * anomaly.anomaly_score,
            4,
        )
        health_index = round(1.0 - failure_probability, 4)

        # ── Step 3: asset context + business impact ─────────────────────
        is_bottleneck = bool(actx.is_bottleneck) if actx else False
        criticality   = actx.criticality if actx else ""
        downtime_cost = actx.downtime_cost_per_hour if actx else 0.0
        business_impact_flag = (
            (cfg.flag_if_bottleneck and is_bottleneck)
            or (cfg.flag_if_high_criticality
                and criticality == cfg.high_criticality_label)
        )

        # ── Step 4: financial exposure (rul_max horizon) ────────────────
        financial_exposure = (
            0.0 if is_monitor
            else compute_financial_exposure(rul_max, downtime_cost)
        )

        # ── Step 5: risk level — reuse the escalated diagnosis severity ──
        risk_level = diagnosis.severity or cfg.risk_level_fallback

        assessment_status = "monitor" if is_monitor else "assessed"
        status_reason = (
            "valid anomaly has no classified fault; monitor and complete diagnostic checks"
            if is_monitor else "risk calculated from validated diagnosis and taxonomy"
        )
        evidence = {
            "iso_stage": iso_stage,
            "fault_code": fault_code,
            "failure_probability": {
                "stage_base": stage_base,
                "stage_weight": w["stage_base"],
                "anomaly_score": anomaly.anomaly_score,
                "anomaly_weight": w["anomaly_score"],
            },
            "diagnosis_confidence": diagnosis.confidence,
            "anomaly_confidence": anomaly.confidence_score,
            "history_coverage": "not_available_in_current_typed_contract",
            "rul_source": "fault_taxonomy" if not is_monitor else "configured_monitor_band",
            "downtime_cost_per_hour": downtime_cost,
        }
        explanation = build_risk_explanation(
            diagnosis, failure_probability, health_index, band_label,
            business_impact_flag, financial_exposure,
        )

        assessment = RiskAssessment(
            case_id              = diagnosis.case_id,
            asset_id             = diagnosis.asset_id,
            bearing_id           = diagnosis.bearing_id,
            failure_probability  = failure_probability,
            risk_level           = risk_level,
            rul_min_days         = rul_min,
            rul_max_days         = rul_max,
            rul_band_label       = band_label,
            confidence           = diagnosis.confidence,
            health_index         = health_index,
            business_impact_flag = business_impact_flag,
            financial_exposure   = financial_exposure,
            processed_at         = processed_at,
            assessment_status    = assessment_status,
            risk_eligible        = True,
            status_reason        = status_reason,
            risk_explanation     = explanation,
            evidence             = evidence,
            **self._provenance(diagnosis, anomaly),
        )

        # ── Step 6: LLM advisory fallback when rules are insufficient ───
        if self._should_fallback(is_monitor, diagnosis.confidence):
            self._apply_llm_fallback(assessment, diagnosis, anomaly, actx, is_monitor)

        if cfg.log_assessments:
            logger.info(
                "RISK [%s] %s/%s risk=%s rul=%s p_fail=%.2f exposure=₹%.0f impact=%s src=%s",
                diagnosis.case_id, diagnosis.asset_id, diagnosis.bearing_id,
                assessment.risk_level, band_label, failure_probability,
                financial_exposure, business_impact_flag,
                assessment.assessment_source,
            )
        return assessment

    # ── LLM fallback helpers ───────────────────────────────────────────

    def _should_fallback(self, is_monitor: bool, confidence: float) -> bool:
        """
        Decide whether to consult the LLM. Cheap, no API call.

        The LLM is consulted only when the deterministic rules genuinely have
        NO coverage for this case — i.e. iso_stage == 0 / no fault_code matched.
        A matched fault with lower confidence still means the rules DO have an
        answer; the LLM should not second-guess a rule-derived result.

        The low-confidence trigger (llm_low_confidence_below) is kept in config
        for experimental use but defaults to 0 (disabled).
        """
        cfg = self._cfg
        if not cfg.llm_enabled:
            return False
        # Primary gate: no rule coverage at all (undetermined / monitor band).
        if is_monitor and cfg.llm_trigger_on_undetermined:
            return True
        # Secondary gate (disabled by default): matched fault but low confidence.
        # Only active when explicitly set > 0 in config.
        if cfg.llm_low_confidence_below > 0 and confidence < cfg.llm_low_confidence_below:
            return True
        return False

    def _apply_llm_fallback(self, assessment: RiskAssessment,
                            diagnosis: FaultDiagnosis,
                            anomaly: AnomalyEvent,
                            actx, is_monitor: bool) -> None:
        """
        Mutate `assessment` in place with an LLM advisory. Deterministic RUL/
        exposure numbers are preserved; only advisory_note (always) and, for the
        undetermined/monitor case, risk_level may be set. No-op if the LLM is
        unavailable or returns nothing usable — the deterministic result stands.

        If a hitl_handler is configured, it is called after the LLM responds but
        before the advisory is written to the assessment. The handler may accept,
        modify, or reject (return None) the LLM result. A rejection leaves the
        deterministic assessment unchanged.
        """
        try:
            result = self._call_llm(diagnosis, anomaly, actx, is_monitor)
        except Exception as exc:
            logger.warning("LLM advisory failed (%s) — deterministic result kept.", exc)
            return
        if not result:
            return

        # HITL gate — let an operator review before the advisory is committed.
        if self._hitl is not None:
            try:
                result = self._hitl(result, assessment, diagnosis, anomaly, actx)
            except Exception as exc:
                logger.warning("HITL handler raised (%s) — LLM advisory discarded.", exc)
                return
            if result is None:
                logger.info(
                    "HITL: operator rejected LLM advisory for %s/%s "
                    "— deterministic result kept.",
                    diagnosis.asset_id, diagnosis.bearing_id,
                )
                return

        rationale = str(result.get("rationale", "")).strip()
        action    = str(result.get("recommended_action", "")).strip()
        note_bits = [b for b in (action, rationale) if b]
        level = str(result.get("risk_level", "")).strip().lower()
        valid_monitor_level = is_monitor and level in _VALID_RISK_LEVELS
        if not note_bits and not valid_monitor_level:
            return

        assessment.assessment_source = "rules+llm_fallback"
        if note_bits:
            assessment.advisory_note = " — ".join(note_bits)

        # Only let the LLM set risk_level where the rules genuinely had nothing
        # to say (undetermined/monitor band). Never override a rule-derived level.
        if is_monitor:
            if level in _VALID_RISK_LEVELS:
                assessment.risk_level = level

    def _call_llm(self, diagnosis: FaultDiagnosis, anomaly: AnomalyEvent,
                  actx, is_monitor: bool) -> Optional[Dict]:
        """Build the prompt and call the LLM. Returns parsed dict or None."""
        cfg = self._cfg
        if not self._llm.is_configured():
            return None

        situation = (
            "an anomaly fired but no known fault signature matched (undetermined)"
            if is_monitor
            else f"a '{diagnosis.fault_mode}' fault matched but with low "
                 f"classification confidence ({diagnosis.confidence})"
        )
        asset_name  = getattr(actx, "asset_name", None) or diagnosis.asset_id
        criticality = getattr(actx, "criticality", "unknown") if actx else "unknown"
        bottleneck  = bool(getattr(actx, "is_bottleneck", False)) if actx else False

        system_prompt = (
            "You are a rotating-equipment reliability engineer assisting a "
            "predictive-maintenance system. The deterministic rule engine could "
            "not produce a confident risk assessment for a bearing. Using the "
            "evidence provided, give a concise, practical risk judgement for an "
            "operations audience. Respond with ONLY a JSON object with keys: "
            '"risk_level" (one of low, medium, high, critical), '
            '"recommended_action" (one short sentence), '
            '"rationale" (one or two sentences, plain language).'
        )
        user_prompt = (
            f"Situation: {situation}.\n"
            f"Asset: {asset_name} (criticality: {criticality}, "
            f"production bottleneck: {bottleneck}).\n"
            f"Bearing: {diagnosis.bearing_id}.\n"
            f"Anomaly score: {anomaly.anomaly_score} "
            f"(confidence {anomaly.confidence_score}).\n"
            f"Why the anomaly fired: {anomaly.reason or 'n/a'}\n"
            f"Triggered signals: {', '.join(anomaly.triggered_features) or 'none'}.\n"
            f"ISO stage (0 = undetermined): {diagnosis.iso_stage}.\n"
        )
        return self._llm.complete_json(
            system_prompt = system_prompt,
            user_prompt   = user_prompt,
            temperature   = cfg.llm_temperature,
            max_tokens    = cfg.llm_max_tokens,
        )

    # ------------------------------------------------------------------

    def process_batch(self, triples: list) -> list:
        """Assess a list of (diagnosis, anomaly, trusted) → list[RiskAssessment]."""
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        # Preserve one auditable output per item; malformed entries cannot abort
        # otherwise valid risk cards in the same batch.
        results = []
        for item in triples:
            if not isinstance(item, (tuple, list)) or len(item) != 3:
                results.append(self._invalid(
                    None, None, None,
                    "batch item must contain diagnosis, anomaly, and trusted signal",
                ))
            else:
                results.append(self.process(item[0], item[1], item[2]))
        return results

    def _provenance(self, diagnosis, anomaly) -> Dict[str, str]:
        return {
            "risk_config_version": self._config_version,
            "taxonomy_version": self._taxonomy_version,
            "source_diagnosis_schema_version": getattr(diagnosis, "schema_version", ""),
            "source_fi_config_version": getattr(diagnosis, "fi_config_version", ""),
            "source_taxonomy_version": getattr(diagnosis, "taxonomy_version", ""),
            "source_monitoring_config_version": getattr(diagnosis, "source_monitoring_config_version", ""),
            "source_detector_version": getattr(diagnosis, "source_detector_version", ""),
            "source_data_schema_version": getattr(diagnosis, "source_schema_version", ""),
            "source_data_config_version": getattr(diagnosis, "source_config_version", ""),
            "source_master_data_version": getattr(diagnosis, "source_master_data_version", ""),
            "linked_anomaly_case_id": getattr(anomaly, "case_id", ""),
            "linked_diagnosis_case_id": getattr(diagnosis, "case_id", ""),
        }

    def _invalid(self, diagnosis, anomaly, trusted, reason: str) -> RiskAssessment:
        processed_at = datetime.now(tz=timezone.utc).isoformat()
        raw = getattr(trusted, "raw", None)
        return RiskAssessment(
            case_id=getattr(diagnosis, "case_id", "") or getattr(anomaly, "case_id", ""),
            asset_id=getattr(diagnosis, "asset_id", "") or getattr(raw, "asset_id", ""),
            bearing_id=getattr(diagnosis, "bearing_id", "") or getattr(raw, "bearing_id", ""),
            risk_level=self._cfg.risk_level_fallback,
            confidence=0.0,
            assessment_status="invalid_input",
            risk_eligible=False,
            status_reason=reason,
            risk_explanation=f"Predictive Risk did not assess this input: {reason}.",
            evidence={"input_validation": reason},
            processed_at=processed_at,
            **self._provenance(diagnosis, anomaly),
        )
        # ***********************
