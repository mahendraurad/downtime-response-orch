"""input_validator.py — Validates the three inputs before the pipeline runs.

Checks required fields, plausible values, and cross-object consistency (matching
IDs and master-data cross-checks), returning a ValidationResult with errors and
warnings. Hard errors block the pipeline; warnings do not."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked_at_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))


VALID_FAULT_MODES = {
    "outer_race_fault", "inner_race_fault", "lubrication_issue",
    "imbalance", "misalignment", "cage_fault", "unknown"
}
VALID_SEVERITIES = {"monitor", "stage_1", "stage_2", "stage_3"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


def validate_inputs(diagnosis, risk, guidance) -> ValidationResult:
    errors = []
    warnings = []

    # CATEGORY 1 — Required fields present
    # TIER 1: hard errors — pipeline cannot function without these fields
    for field_name in ["asset_id", "fault_mode", "severity", "fault_code"]:
        if not getattr(diagnosis, field_name, None):
            errors.append(f"diagnosis.{field_name} is missing or null")
    if not getattr(risk, "asset_id", None):
        errors.append("risk.asset_id is missing or null")

    # TIER 2: soft missing — pipeline applies safe defaults, flags as partial
    if not getattr(diagnosis, "bearing_id", None):
        warnings.append(
            "diagnosis.bearing_id is None — bearing checks will be skipped")
    if getattr(diagnosis, "confidence", None) is None:
        warnings.append(
            "diagnosis.confidence is None — defaulting to 0.60")
    if getattr(risk, "rul_min_days", None) is None:
        warnings.append(
            "risk.rul_min_days is None — defaulting to 14 days (conservative)")
    if getattr(risk, "rul_max_days", None) is None:
        warnings.append(
            "risk.rul_max_days is None — defaulting to rul_min + 7")
    if getattr(risk, "risk_level", None) is None:
        warnings.append(
            "risk.risk_level is None — defaulting to 'medium'")
    if getattr(risk, "failure_probability", None) is None:
        warnings.append(
            "risk.failure_probability is None — defaulting to 50%")

    # CATEGORY 2 — Implausible values (only check when field is present)
    if diagnosis.confidence is not None:
        if not 0.0 <= diagnosis.confidence <= 1.0:
            errors.append(
                f"diagnosis.confidence out of range: {diagnosis.confidence}")
    if risk.failure_probability is not None:
        if not 0.0 <= risk.failure_probability <= 1.0:
            errors.append(
                f"risk.failure_probability out of range: "
                f"{risk.failure_probability}")
    if risk.rul_min_days is not None and risk.rul_min_days < 0:
        errors.append(f"risk.rul_min_days is negative: {risk.rul_min_days}")
    if (risk.rul_min_days is not None and risk.rul_max_days is not None
            and risk.rul_max_days < risk.rul_min_days):
        errors.append(
            f"risk.rul_max_days ({risk.rul_max_days}) < "
            f"rul_min_days ({risk.rul_min_days})")
    if (diagnosis.fault_mode is not None
            and diagnosis.fault_mode not in VALID_FAULT_MODES):
        errors.append(
            f"diagnosis.fault_mode invalid: '{diagnosis.fault_mode}'. "
            f"Must be one of {sorted(VALID_FAULT_MODES)}")
    if (diagnosis.severity is not None
            and diagnosis.severity not in VALID_SEVERITIES):
        errors.append(f"diagnosis.severity invalid: '{diagnosis.severity}'")
    if risk.risk_level is not None and risk.risk_level not in VALID_RISK_LEVELS:
        errors.append(f"risk.risk_level invalid: '{risk.risk_level}'")
    if (hasattr(risk, 'estimated_downtime_cost_per_hour')
            and risk.estimated_downtime_cost_per_hour is not None
            and risk.estimated_downtime_cost_per_hour < 0):
        errors.append(
            f"risk.estimated_downtime_cost_per_hour is negative: "
            f"{risk.estimated_downtime_cost_per_hour}")

    # CATEGORY 3 — Cross-object consistency
    if (diagnosis.asset_id and risk.asset_id
            and diagnosis.asset_id != risk.asset_id):
        errors.append(
            f"asset_id mismatch: diagnosis='{diagnosis.asset_id}' "
            f"vs risk='{risk.asset_id}'")
    if (diagnosis.bearing_id and risk.bearing_id
            and diagnosis.bearing_id != risk.bearing_id):
        errors.append(
            f"bearing_id mismatch: diagnosis='{diagnosis.bearing_id}' "
            f"vs risk='{risk.bearing_id}'")
    if (diagnosis.case_id and risk.case_id
            and diagnosis.case_id != risk.case_id):
        warnings.append(
            f"case_id mismatch: diagnosis='{diagnosis.case_id}' "
            f"vs risk='{risk.case_id}' — may be intentional")

    # Cross-check against master data
    from tools.data_loader import load_assets, load_bearings
    asset_ids = {a["asset_id"] for a in load_assets()}
    if diagnosis.asset_id and diagnosis.asset_id not in asset_ids:
        errors.append(
            f"diagnosis.asset_id '{diagnosis.asset_id}' not found in "
            f"asset_master — unknown asset")
    bearing_records = load_bearings()
    bearing_ids = {b["bearing_id"] for b in bearing_records}
    if diagnosis.bearing_id and diagnosis.bearing_id not in bearing_ids:
        warnings.append(
            f"diagnosis.bearing_id '{diagnosis.bearing_id}' not found in "
            f"bearing_master — results may be incomplete")
    # Check bearing belongs to the stated asset
    if diagnosis.asset_id and diagnosis.bearing_id:
        asset_bearings = {b["bearing_id"] for b in bearing_records
                         if b["asset_id"] == diagnosis.asset_id}
        if (diagnosis.bearing_id in bearing_ids
                and diagnosis.bearing_id not in asset_bearings):
            errors.append(
                f"bearing_id '{diagnosis.bearing_id}' does not belong to "
                f"asset '{diagnosis.asset_id}' in bearing_master")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )
