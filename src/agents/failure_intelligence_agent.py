"""
agents/failure_intelligence_agent.py  —  Phase 4

Failure Intelligence Agent — classifies an AnomalyEvent into a named fault.

Question it answers:  "What exactly is wrong, and how severe?"

Entry point:  FailureIntelligenceAgent.process(anomaly, trusted) -> FaultDiagnosis

Pipeline:
  1. Pull band energies + kurtosis from trusted.raw; baselines from bearing_ctx.
  2. Derive temp_rise and broadband_pattern.
  3. Evaluate every taxonomy candidate (fault_matcher.evaluate_candidates).
  4. Primary = first matched candidate in priority order; the rest with
     partial evidence become differential diagnoses.
  5. No match → 'undetermined' diagnosis (manual inspection), never raises.
  6. Severity from ISO stage escalated by criticality / bottleneck.
  7. Confidence = blend of the anomaly's confidence and match strength.
  8. Recommended checks + deterministic narrative + RUL/causes hand-off.

Unit note:
  bpfo_energy / bpfi_energy are direct threshold values — compared straight to
  the stage_N_vib_multiple values in fault_taxonomy.json. No baseline scaling.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

from src.schemas.anomaly import AnomalyEvent
from src.schemas.bearing_signal import TrustedBearingSignal
from src.schemas.diagnosis import FaultDiagnosis
from src.tools.fault_matcher import (
    evaluate_candidates,
    compute_temp_rise,
    detect_broadband_pattern,
    band_stage1_threshold,
)
from src.tools.config_loader import FailureIntelligenceConfig, load_fi_config

logger = logging.getLogger(__name__)

_UNDETERMINED = "undetermined"


class FailureIntelligenceAgent:
    """Stateless agent. Taxonomy rules + config injected at construction."""

    def __init__(self, taxonomy_rules: List[Dict],
                 cfg: FailureIntelligenceConfig = None):
        self._rules = taxonomy_rules
        self._by_code = {r["fault_code"]: r for r in taxonomy_rules}
        self._cfg = cfg or load_fi_config()

    @classmethod
    def from_data_files(cls) -> "FailureIntelligenceAgent":
        """Factory — load taxonomy from data/ and config from config/."""
        from src.tools.data_loader import load_fault_taxonomy
        return cls(load_fault_taxonomy(), load_fi_config())

    # ------------------------------------------------------------------

    def process(self, anomaly: AnomalyEvent,
                trusted: TrustedBearingSignal) -> FaultDiagnosis:
        """Classify one anomaly into a FaultDiagnosis. Never raises."""
        cfg  = self._cfg
        raw  = trusted.raw
        bctx = trusted.bearing_ctx
        actx = trusted.asset_ctx
        processed_at = datetime.now(tz=timezone.utc).isoformat()

        # ── Step 1: gather observed signals ─────────────────────────────
        bpfo = raw.bpfo_energy
        bpfi = raw.bpfi_energy
        bsf  = raw.bsf_energy
        ftf  = raw.ftf_energy
        kurt = raw.kurtosis if raw.kurtosis is not None else 0.0

        baseline_temp = getattr(bctx, "baseline_temp_mean", None)
        baseline_vib  = getattr(bctx, "baseline_vib_rms_mean", None)

        # ── Step 2: derived inputs ──────────────────────────────────────
        temp_rise = compute_temp_rise(raw.temp_c, baseline_temp)
        vib_ratio = (raw.vib_rms_mm_s / baseline_vib) if (
            raw.vib_rms_mm_s is not None and baseline_vib
        ) else 0.0

        # Dominant-band thresholds are looked up from the taxonomy by signal,
        # not hardcoded to fault codes: the level at which BPFO / BPFI counts as
        # a discrete defect is whatever stage-1 multiple their detecting rule
        # defines. Change the rule data → this follows automatically.
        bpfo_stage_1 = band_stage1_threshold(self._rules, "bpfo_energy")
        bpfi_stage_1 = band_stage1_threshold(self._rules, "bpfi_energy")
        broadband = detect_broadband_pattern(
            raw.vib_rms_mm_s, baseline_vib, bpfo, bpfi,
            bpfo_stage_1, bpfi_stage_1, cfg.broadband_vib_elevation_ratio,
        )

        # ── Step 3: evaluate candidates ─────────────────────────────────
        candidates = evaluate_candidates(
            bpfo, bpfi, bsf, ftf, kurt, temp_rise, broadband, vib_ratio,
            self._rules,
        )
        primary = next((c for c in candidates if c["matched"]), None)

        # ── Step 4: no match → undetermined ─────────────────────────────
        if primary is None:
            return self._undetermined(anomaly, raw, kurt, bpfo, bpfi,
                                      candidates, processed_at)

        # ── Step 5: look up the matched rule ────────────────────────────
        rule       = self._by_code[primary["fault_code"]]
        iso_stage  = primary["iso_stage"]
        fault_code = primary["fault_code"]
        fault_mode = primary["fault_mode"]
        rul_days   = rule.get(f"rul_days_stage_{iso_stage}", 0)
        causes     = list(rule.get("typical_causes", []))

        is_bottleneck = bool(actx.is_bottleneck) if actx else False
        criticality   = actx.criticality if actx else ""

        # ── Step 6: severity ────────────────────────────────────────────
        severity = self._severity(iso_stage, criticality, is_bottleneck)

        # ── Step 7: confidence ──────────────────────────────────────────
        match_strength = self._match_strength(primary, rule, kurt, vib_ratio)
        w = cfg.confidence_weights
        confidence = round(
            w["anomaly_confidence"] * anomaly.confidence_score
            + w["match_strength"] * match_strength,
            4,
        )

        # ── Step 8: differentials, checks, narrative ────────────────────
        differentials = self._differentials(candidates, fault_code)
        checks = self._recommended_checks(fault_code, causes)
        narrative = self._narrative(
            fault_mode, fault_code, iso_stage, severity, primary, rule,
            kurt, temp_rise, rul_days, confidence, raw, actx,
        )

        evidence = {
            "matched_rule":          fault_code,
            "dominant_band":         primary["dominant_band"],
            "dominant_value":        primary["dominant_value"],
            "thresholds_crossed": {
                "band":      primary["dominant_band"],
                "value":     primary["dominant_value"],
                "stage_1":   rule.get("stage_1_vib_multiple"),
                "stage_2":   rule.get("stage_2_vib_multiple"),
                "stage_3":   rule.get("stage_3_vib_multiple"),
                "iso_stage": iso_stage,
            },
            "kurtosis": {
                "value":     kurt,
                "threshold": rule.get("kurtosis_threshold"),
                "exceeded":  kurt > rule.get("kurtosis_threshold", 0.0),
            },
            "temp_rise_c":           temp_rise,
            "expected_temp_rise":    rule.get("expected_temp_rise"),
            "broadband_pattern":     broadband,
            "vib_ratio":             round(vib_ratio, 4),
            "degradation_curve":     rule.get("degradation_curve"),
            "match_strength":        match_strength,
            "anomaly_score":         anomaly.anomaly_score,
            "anomaly_confidence":    anomaly.confidence_score,
            "anomaly_triggered_features": anomaly.triggered_features,
            "differential_diagnoses": differentials,
        }

        diagnosis = FaultDiagnosis(
            case_id               = anomaly.case_id,
            asset_id              = raw.asset_id,
            bearing_id            = raw.bearing_id,
            fault_mode            = fault_mode,
            fault_code            = fault_code,
            iso_stage             = iso_stage,
            severity              = severity,
            confidence            = confidence,
            bpfo_multiple         = bpfo or 0.0,
            bpfi_multiple         = bpfi or 0.0,
            kurtosis_at_detection = kurt,
            evidence              = evidence,
            recommended_checks    = checks,
            narrative             = narrative,
            processed_at          = processed_at,
            is_bottleneck         = is_bottleneck,
            typical_causes        = causes,
            rul_days_estimate     = rul_days,
        )

        if cfg.log_diagnoses:
            logger.info(
                "DIAGNOSIS [%s] %s/%s fault=%s stage=%d severity=%s conf=%.2f rul=%dd",
                raw.telemetry_id, raw.asset_id, raw.bearing_id,
                fault_code, iso_stage, severity, confidence, rul_days,
            )
        return diagnosis

    # ------------------------------------------------------------------

    def process_batch(self, pairs: list) -> list:
        """Classify a list of (anomaly, trusted) pairs → list[FaultDiagnosis]."""
        return [self.process(a, t) for a, t in pairs]

    # ── Helpers ────────────────────────────────────────────────────────

    def _undetermined(self, anomaly, raw, kurt, bpfo, bpfi,
                      candidates, processed_at) -> FaultDiagnosis:
        differentials = self._differentials(candidates, primary_code=None)
        checks = list(self._cfg.fault_checks.get(_UNDETERMINED, []))
        narrative = (
            f"An anomaly was detected on bearing {raw.bearing_id} "
            f"(anomaly score {anomaly.anomaly_score:.2f}) but the telemetry "
            f"pattern did not cross any known fault signature in the taxonomy. "
            f"Manual inspection by a reliability engineer is recommended."
        )
        # confidence: only the anomaly half contributes — match strength is 0.
        confidence = round(
            self._cfg.confidence_weights["anomaly_confidence"]
            * anomaly.confidence_score,
            4,
        )
        if self._cfg.log_diagnoses:
            logger.info(
                "DIAGNOSIS [%s] %s/%s fault=undetermined conf=%.2f",
                raw.telemetry_id, raw.asset_id, raw.bearing_id, confidence,
            )
        return FaultDiagnosis(
            case_id               = anomaly.case_id,
            asset_id              = raw.asset_id,
            bearing_id            = raw.bearing_id,
            fault_mode            = _UNDETERMINED,
            fault_code            = "",
            iso_stage             = 0,
            severity              = self._cfg.undetermined_severity,
            confidence            = confidence,
            bpfo_multiple         = bpfo or 0.0,
            bpfi_multiple         = bpfi or 0.0,
            kurtosis_at_detection = kurt,
            evidence              = {
                "matched_rule":           None,
                "anomaly_score":          anomaly.anomaly_score,
                "anomaly_confidence":     anomaly.confidence_score,
                "anomaly_triggered_features": anomaly.triggered_features,
                "differential_diagnoses": differentials,
            },
            recommended_checks    = checks,
            narrative             = narrative,
            processed_at          = processed_at,
        )

    def _severity(self, iso_stage: int, criticality: str,
                  is_bottleneck: bool) -> str:
        cfg   = self._cfg
        order = cfg.severity_escalation_order
        base  = cfg.severity_stage_base.get(str(iso_stage),
                                            cfg.undetermined_severity)
        if base not in order:
            return base
        idx = order.index(base)
        escalate = (
            (cfg.escalate_if_high_criticality
             and criticality == cfg.high_criticality_label)
            or (cfg.escalate_if_bottleneck and is_bottleneck)
        )
        if escalate:
            idx = min(idx + 1, len(order) - 1)
        return order[idx]

    @staticmethod
    def _match_strength(primary: Dict, rule: Dict, kurt: float,
                        vib_ratio: float) -> float:
        """
        How decisively the fault signature was crossed, 0–1.  Blends band
        dominance (value vs stage-3 threshold) with the kurtosis margin above
        the rule's kurtosis threshold.  For broadband (lubrication) faults the
        vib ratio stands in for the band term.
        """
        stage_3 = rule.get("stage_3_vib_multiple", 1.0) or 1.0
        kurt_thr = rule.get("kurtosis_threshold", 1.0) or 1.0

        if primary["dominant_band"] == "broadband":
            band_score = min(1.0, vib_ratio / stage_3)
            # broadband kurtosis sits *below* the FT_001 threshold by design,
            # so reward proximity to the rule's own (lower) kurtosis threshold.
            kurt_score = min(1.0, kurt / kurt_thr) if kurt_thr else 0.0
        else:
            band_score = min(1.0, (primary["dominant_value"] or 0.0) / stage_3)
            margin = (kurt - kurt_thr) / kurt_thr if kurt_thr else 0.0
            kurt_score = max(0.0, min(1.0, 0.5 + margin))

        return round(0.5 * band_score + 0.5 * kurt_score, 4)

    @staticmethod
    def _differentials(candidates: List[Dict],
                       primary_code: Optional[str]) -> List[Dict]:
        """
        Runner-up fault modes worth noting.  Includes any *other* candidate
        that showed supporting evidence (matched, a stage crossed, or kurtosis
        elevated), each with the reason it was not the primary diagnosis.
        """
        diffs = []
        for c in candidates:
            if c["fault_code"] == primary_code:
                continue
            if c.get("has_evidence"):
                diffs.append({
                    "fault_code": c["fault_code"],
                    "fault_mode": c["fault_mode"],
                    "reason":     c["reason"],
                })
        return diffs

    def _recommended_checks(self, fault_code: str,
                            causes: List[str]) -> List[str]:
        checks = list(self._cfg.fault_checks.get(fault_code, []))
        for cause in causes:
            checks.append(f"Investigate likely root cause: {cause}")
        return checks

    @staticmethod
    def _narrative(fault_mode, fault_code, iso_stage, severity, primary, rule,
                   kurt, temp_rise, rul_days, confidence, raw, actx) -> str:
        pretty = fault_mode.replace("_", " ")
        asset  = actx.asset_name if actx else raw.asset_id
        band   = primary["dominant_band"]
        if band == "broadband":
            signature = (
                f"Broadband vibration rose to ×{primary['dominant_value']} the "
                f"healthy baseline with a {temp_rise}°C temperature rise and "
                f"kurtosis {kurt} — no single defect frequency dominates"
            )
        else:
            signature = (
                f"{band} band energy {primary['dominant_value']} crossed the "
                f"stage-{iso_stage} threshold "
                f"({rule.get(f'stage_{iso_stage}_vib_multiple')}) with kurtosis "
                f"{kurt} (> {rule.get('kurtosis_threshold')})"
            )
        return (
            f"{pretty.title()} (code {fault_code}) detected on bearing "
            f"{raw.bearing_id} of {asset} at ISO stage {iso_stage} "
            f"({severity} severity). {signature}. Estimated {rul_days} days to "
            f"functional failure at the current {rule.get('degradation_curve')} "
            f"degradation rate. Classification confidence {confidence:.0%}."
        )
