"""
tools/poa_input_validator.py  —  Phase 7

Validates FaultDiagnosis, RiskAssessment, and KnowledgeGuidance objects before
the Prescriptive Optimization Agent runs. Hard errors block execution; warnings
allow continuation with degraded functionality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

# DRO-native value sets
VALID_FAULT_MODES = {
    "outer_race_fault", "inner_race_fault", "lubrication_issue",
    "imbalance", "misalignment", "cage_fault", "gearbox_fault",
    "undetermined", "unknown",
}
VALID_SEVERITIES  = {"low", "medium", "high", "critical",
                     "stage_0", "stage_1", "stage_2", "stage_3"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked_at_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))


def validate_inputs(diagnosis, risk, guidance) -> ValidationResult:
    """
    Validate all three upstream agent outputs before POA runs.
    Returns ValidationResult with valid=True only when there are no hard errors.
    """
    errors: List[str]   = []
    warnings: List[str] = []

    # ── Category 1: Required fields ──────────────────────────────────────────

    for field_name in ["asset_id", "fault_mode", "severity", "fault_code"]:
        if not getattr(diagnosis, field_name, None):
            errors.append(f"diagnosis.{field_name} is missing or null")
    if not getattr(risk, "asset_id", None):
        errors.append("risk.asset_id is missing or null")

    if not getattr(diagnosis, "bearing_id", None):
        warnings.append("diagnosis.bearing_id is None — bearing checks will be skipped")
    if getattr(diagnosis, "confidence", None) is None:
        warnings.append("diagnosis.confidence is None — defaulting to 0.60")
    if getattr(risk, "rul_min_days", None) is None:
        warnings.append("risk.rul_min_days is None — defaulting to 14 days (conservative)")
    if getattr(risk, "rul_max_days", None) is None:
        warnings.append("risk.rul_max_days is None — defaulting to rul_min + 7")
    if getattr(risk, "risk_level", None) is None:
        warnings.append("risk.risk_level is None — defaulting to 'medium'")
    if getattr(risk, "failure_probability", None) is None:
        warnings.append("risk.failure_probability is None — defaulting to 50%")

    # ── Category 2: Plausibility ─────────────────────────────────────────────

    confidence = getattr(diagnosis, "confidence", None)
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        errors.append(f"diagnosis.confidence out of range: {confidence}")

    fp = getattr(risk, "failure_probability", None)
    if fp is not None and not 0.0 <= fp <= 1.0:
        errors.append(f"risk.failure_probability out of range: {fp}")

    rul_min = getattr(risk, "rul_min_days", None)
    rul_max = getattr(risk, "rul_max_days", None)
    if rul_min is not None and rul_min < 0:
        errors.append(f"risk.rul_min_days is negative: {rul_min}")
    if rul_min is not None and rul_max is not None and rul_max < rul_min:
        errors.append(f"risk.rul_max_days ({rul_max}) < rul_min_days ({rul_min})")

    fault_mode = getattr(diagnosis, "fault_mode", None)
    if fault_mode and fault_mode not in VALID_FAULT_MODES:
        errors.append(
            f"diagnosis.fault_mode invalid: '{fault_mode}'. "
            f"Must be one of {sorted(VALID_FAULT_MODES)}")

    severity = getattr(diagnosis, "severity", None)
    if severity and severity not in VALID_SEVERITIES:
        errors.append(f"diagnosis.severity invalid: '{severity}'")

    risk_level = getattr(risk, "risk_level", None)
    if risk_level and risk_level not in VALID_RISK_LEVELS:
        errors.append(f"risk.risk_level invalid: '{risk_level}'")

    # ── Category 3: Cross-object consistency ─────────────────────────────────

    diag_asset = getattr(diagnosis, "asset_id", None)
    risk_asset = getattr(risk, "asset_id", None)
    if diag_asset and risk_asset and diag_asset != risk_asset:
        errors.append(
            f"asset_id mismatch: diagnosis='{diag_asset}' vs risk='{risk_asset}'")

    diag_brg = getattr(diagnosis, "bearing_id", None)
    risk_brg  = getattr(risk, "bearing_id", None)
    if diag_brg and risk_brg and diag_brg != risk_brg:
        errors.append(
            f"bearing_id mismatch: diagnosis='{diag_brg}' vs risk='{risk_brg}'")

    diag_case = getattr(diagnosis, "case_id", None)
    risk_case  = getattr(risk, "case_id", None)
    if diag_case and risk_case and diag_case != risk_case:
        warnings.append(
            f"case_id mismatch: diagnosis='{diag_case}' vs risk='{risk_case}' "
            f"— may be intentional")

    # ── Category 4: Master-data cross-reference ──────────────────────────────

    try:
        from src.tools.data_loader import load_assets, load_bearings
        asset_ids = {a.get("asset_id") for a in load_assets()}
        if diag_asset and diag_asset not in asset_ids:
            errors.append(
                f"diagnosis.asset_id '{diag_asset}' not found in asset_master — unknown asset")

        bearing_records = load_bearings()
        bearing_ids = {b.get("bearing_id") for b in bearing_records}
        if diag_brg and diag_brg not in bearing_ids:
            warnings.append(
                f"diagnosis.bearing_id '{diag_brg}' not found in bearing_master "
                f"— results may be incomplete")

        if diag_asset and diag_brg and diag_brg in bearing_ids:
            asset_bearings = {
                b.get("bearing_id") for b in bearing_records
                if b.get("asset_id") == diag_asset
            }
            if diag_brg not in asset_bearings:
                errors.append(
                    f"bearing_id '{diag_brg}' does not belong to "
                    f"asset '{diag_asset}' in bearing_master")
    except Exception:
        warnings.append("Master-data cross-reference skipped (data files unavailable)")

    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)
