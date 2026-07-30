"""Deterministic Agent 4 handoff validation and explanation utilities."""
from __future__ import annotations

import math
from typing import Dict, List

from src.schemas.anomaly import AnomalyEvent
from src.schemas.bearing_signal import TrustedBearingSignal
from src.schemas.diagnosis import FaultDiagnosis


# ************** Added by Prateek Mittal on 20th July 2026 ******************
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


def _finite_unit(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def validate_risk_handoff(
    diagnosis,
    anomaly,
    trusted,
    taxonomy_by_code: Dict[str, dict],
    taxonomy_version: str,
) -> str:
    """Return an empty string for a safe handoff, otherwise an audit reason."""
    if not isinstance(diagnosis, FaultDiagnosis):
        return "diagnosis must be a FaultDiagnosis"
    if not isinstance(anomaly, AnomalyEvent):
        return "anomaly must be an AnomalyEvent"
    if not isinstance(trusted, TrustedBearingSignal):
        return "trusted must be a TrustedBearingSignal"
    if getattr(diagnosis, "diagnosis_status", "diagnosed") == "invalid_input":
        return "Agent 3 diagnosis is invalid_input"
    if not getattr(diagnosis, "diagnostic_eligible", True):
        return "Agent 3 diagnosis is not risk eligible"
    if not trusted.downstream_eligible:
        return "Agent 1 signal is not downstream eligible"
    if not diagnosis.case_id or diagnosis.case_id != anomaly.case_id:
        return "diagnosis and anomaly case identities do not match"

    raw = trusted.raw
    identities = (raw.asset_id, raw.bearing_id)
    if (diagnosis.asset_id, diagnosis.bearing_id) != identities:
        return "diagnosis and trusted signal identities do not match"
    if (anomaly.asset_id, anomaly.bearing_id) != identities:
        return "anomaly and trusted signal identities do not match"
    if anomaly.channel_id and anomaly.channel_id != raw.channel_id:
        return "anomaly and trusted signal channel identities do not match"
    if anomaly.timestamp_utc and anomaly.timestamp_utc != raw.timestamp_utc:
        return "anomaly and trusted signal timestamps do not match"

    if not _finite_unit(anomaly.anomaly_score):
        return "anomaly_score must be a finite value between 0 and 1"
    if not _finite_unit(anomaly.confidence_score):
        return "anomaly confidence must be a finite value between 0 and 1"
    if not _finite_unit(diagnosis.confidence):
        return "diagnosis confidence must be a finite value between 0 and 1"
    if isinstance(diagnosis.iso_stage, bool) or diagnosis.iso_stage not in (0, 1, 2, 3):
        return "diagnosis iso_stage must be 0, 1, 2, or 3"
    if diagnosis.severity not in VALID_RISK_LEVELS:
        return "diagnosis severity is not a supported risk level"

    if diagnosis.iso_stage == 0:
        if getattr(diagnosis, "diagnosis_status", "undetermined") != "undetermined":
            return "stage-0 diagnosis must have diagnosis_status undetermined"
        if diagnosis.fault_code:
            return "stage-0 diagnosis must not carry a fault_code"
        if diagnosis.fault_mode != "undetermined":
            return "stage-0 diagnosis must use fault_mode undetermined"
    else:
        if getattr(diagnosis, "diagnosis_status", "diagnosed") != "diagnosed":
            return "stage 1-3 diagnosis must have diagnosis_status diagnosed"
        if not diagnosis.fault_code:
            return "stage 1-3 diagnosis must carry a fault_code"
        rule = taxonomy_by_code.get(diagnosis.fault_code)
        if rule is None:
            return "diagnosis fault_code is not present in the Agent 4 taxonomy"
        if diagnosis.fault_mode != rule.get("fault_mode"):
            return "diagnosis fault_mode does not match its taxonomy rule"
        if not rule.get("detection"):
            return "diagnosis fault_code is dormant and cannot carry a risk stage"

    source_taxonomy = getattr(diagnosis, "taxonomy_version", "")
    if source_taxonomy and source_taxonomy != taxonomy_version:
        return "Agent 3 and Agent 4 taxonomy versions do not match"

    actx = trusted.asset_ctx
    if actx is not None:
        cost = actx.downtime_cost_per_hour
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(cost)
            or cost < 0
        ):
            return "downtime_cost_per_hour must be finite and non-negative"
    return ""


def build_risk_explanation(
    diagnosis: FaultDiagnosis,
    failure_probability: float,
    health_index: float,
    band_label: str,
    business_impact: bool,
    financial_exposure: float,
) -> str:
    """Create a deterministic, operations-readable explanation."""
    mode = diagnosis.fault_mode.replace("_", " ")
    if diagnosis.iso_stage == 0:
        return (
            f"The anomaly remains undetermined, so no near-term failure window "
            f"is asserted. Continue enhanced monitoring and complete the Agent 3 "
            f"recommended checks. Failure probability is {failure_probability:.0%} "
            f"and health index is {health_index:.0%}."
        )
    impact = (
        f" Business impact is flagged; exposure over the RUL upper bound is "
        f"{financial_exposure:,.0f}."
        if business_impact else " Business impact is not flagged by current policy."
    )
    return (
        f"{mode.title()} at ISO stage {diagnosis.iso_stage} maps to RUL band "
        f"{band_label}. Failure probability is {failure_probability:.0%} and "
        f"health index is {health_index:.0%}.{impact}"
    )
# ***********************
