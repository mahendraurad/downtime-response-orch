"""
tools/uncertainty_detector.py  —  Phase 7

Detects partial or uncertain inputs and produces human-readable flags.
Also provides apply_safe_defaults() to fill missing optional fields before
the pipeline runs — does NOT modify originals (uses model_copy()).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class UncertaintyResult:
    is_partial: bool
    flags: List[str] = field(default_factory=list)


def apply_safe_defaults(diagnosis, risk) -> tuple:
    """
    Apply safe defaults to missing optional fields.
    Returns (diagnosis_copy, risk_copy, list_of_defaulted_fields).
    Uses model_copy() to avoid mutating the originals.
    """
    defaulted = []
    diag_updates = {}
    risk_updates = {}

    if diagnosis.confidence is None:
        diag_updates["confidence"] = 0.60
        defaulted.append(
            "confidence was None — defaulted to 60% "
            "(treat recommendation with caution)")

    if risk.rul_min_days is None:
        risk_updates["rul_min_days"] = 14
        defaulted.append(
            "RUL min was not provided — defaulted to 14 days "
            "(conservative assumption; actual urgency may be higher)")

    if risk.rul_max_days is None:
        rul_min = risk_updates.get("rul_min_days", risk.rul_min_days) or 14
        risk_updates["rul_max_days"] = rul_min + 7
        defaulted.append(f"RUL max was not provided — defaulted to {rul_min + 7} days")

    if risk.risk_level is None:
        risk_updates["risk_level"] = "medium"
        defaulted.append(
            "risk_level was not provided — defaulted to 'medium' "
            "(actual risk may be higher or lower)")

    if risk.failure_probability is None:
        risk_updates["failure_probability"] = 0.50
        defaulted.append("failure_probability was not provided — defaulted to 50%")

    diag_out = (diagnosis.model_copy(update=diag_updates) if diag_updates else diagnosis)
    risk_out = (risk.model_copy(update=risk_updates) if risk_updates else risk)
    return diag_out, risk_out, defaulted


def detect_uncertainty(
    diagnosis,
    risk,
    defaulted_fields: Optional[List[str]] = None,
) -> UncertaintyResult:
    """Detect and flag uncertainty in diagnosis/risk inputs."""
    flags = list(defaulted_fields or [])

    # 1. Low confidence
    if diagnosis.confidence is not None and diagnosis.confidence < 0.70:
        flags.append(
            f"Low diagnostic confidence ({diagnosis.confidence:.0%}) — "
            f"fault classification may not be reliable. "
            f"Recommend physical inspection to confirm.")

    # 2. Wide RUL range
    if (risk.rul_min_days is not None and risk.rul_max_days is not None
            and risk.rul_max_days > 2 * risk.rul_min_days):
        flags.append(
            f"Wide RUL uncertainty range ({risk.rul_min_days}–"
            f"{risk.rul_max_days} days) — remaining useful life estimate "
            f"is imprecise.")

    # 3. Missing evidence
    evidence = getattr(diagnosis, "evidence", None) or {}
    if not evidence:
        flags.append(
            "No supporting signal evidence provided by upstream agents — "
            "recommendation is based on fault classification alone.")

    # 4. Severity/risk inconsistency — uses DRO severity scale
    severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    risk_rank     = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    sev = severity_rank.get(diagnosis.severity, -1)
    rsk = risk_rank.get(risk.risk_level, -1)
    if rsk - sev >= 2:
        flags.append(
            f"Severity/risk inconsistency — fault severity is "
            f"'{diagnosis.severity}' but risk level is '{risk.risk_level}'. "
            f"May indicate a rapidly degrading asset. Treat urgency as '{risk.risk_level}'.")

    return UncertaintyResult(is_partial=len(flags) > 0, flags=flags)
