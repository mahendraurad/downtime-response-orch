"""Deterministic validation and provenance utilities for Agent 3."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
from typing import Any, Dict, Iterable, List


# ************** Added by Prateek Mittal on 20th July 2026 ******************
# Agent 3 reads only these canonical measurements. Validating taxonomy signal
# names at startup prevents a misspelled field from silently becoming 0.0 and
# making a real fault rule impossible to match.
SUPPORTED_DETECTION_SIGNALS = {
    "bpfo_energy",
    "bpfi_energy",
    "bsf_energy",
    "ftf_energy",
    "vib_ratio",
}
SUPPORTED_KURTOSIS_MODES = {"above", "below"}


def stable_version(value: Any) -> str:
    """Return a stable, compact version identifier for JSON-like data."""
    if is_dataclass(value):
        value = asdict(value)
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _resolve_reference(reference: Any, rules: List[Dict]) -> bool:
    if not isinstance(reference, dict):
        return reference is not None
    if "signal" in reference:
        owner = next(
            (r for r in rules if r.get("detection", {}).get("signal") == reference["signal"]),
            None,
        )
    else:
        owner = next(
            (r for r in rules if r.get("fault_code") == reference.get("fault_code")),
            None,
        )
    return bool(owner and reference.get("field") in owner)


def validate_taxonomy(rules: Iterable[Dict]) -> None:
    """Fail fast when active Agent 3 taxonomy rules are contradictory."""
    rules = list(rules)
    if not rules:
        raise ValueError("fault taxonomy must contain at least one rule")

    codes = [r.get("fault_code") for r in rules]
    modes = [r.get("fault_mode") for r in rules]
    if any(not value for value in codes):
        raise ValueError("every taxonomy rule must define fault_code")
    if any(not value for value in modes):
        raise ValueError("every taxonomy rule must define fault_mode")
    if len(codes) != len(set(codes)):
        raise ValueError("fault taxonomy contains duplicate fault_code values")
    if len(modes) != len(set(modes)):
        raise ValueError("fault taxonomy contains duplicate fault_mode values")

    for rule in rules:
        if "detection" in rule and rule["detection"] is not None \
                and not isinstance(rule["detection"], dict):
            raise ValueError(f"{rule['fault_code']} detection must be an object")

    active = [r for r in rules if r.get("detection")]
    priorities = [r["detection"].get("priority") for r in active]
    if any(not isinstance(p, int) or p < 1 for p in priorities):
        raise ValueError("active taxonomy priorities must be positive integers")
    if len(priorities) != len(set(priorities)):
        raise ValueError("active taxonomy rules must have unique priorities")

    for rule in active:
        code = rule["fault_code"]
        detection = rule["detection"]
        signal = detection.get("signal")
        if signal not in SUPPORTED_DETECTION_SIGNALS:
            raise ValueError(f"{code} uses unsupported detection signal: {signal}")

        stages = [rule.get(f"stage_{stage}_vib_multiple") for stage in (1, 2, 3)]
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in stages
        ):
            raise ValueError(f"{code} must define finite positive stage thresholds")
        if stages != sorted(stages):
            raise ValueError(f"{code} stage thresholds must be monotonic")

        ruls = []
        for stage in (1, 2, 3):
            rul = rule.get(f"rul_days_stage_{stage}")
            if (
                isinstance(rul, bool)
                or not isinstance(rul, (int, float))
                or not math.isfinite(rul)
                or rul <= 0
            ):
                raise ValueError(f"{code} must define positive RUL for stage {stage}")
            ruls.append(rul)
        if not (ruls[0] >= ruls[1] >= ruls[2]):
            raise ValueError(f"{code} RUL must not increase with fault stage")

        kurtosis = detection.get("kurtosis", {})
        mode = kurtosis.get("mode", "above")
        if mode not in SUPPORTED_KURTOSIS_MODES:
            raise ValueError(f"{code} uses unsupported kurtosis mode: {mode}")
        if mode == "above":
            field = kurtosis.get("threshold_field", "kurtosis_threshold")
            if isinstance(field, dict):
                valid = _resolve_reference(field, rules)
            else:
                valid = field in rule
            if not valid:
                raise ValueError(f"{code} has an unresolved kurtosis threshold")
        elif not _resolve_reference(kurtosis.get("ceiling"), rules):
            raise ValueError(f"{code} has an unresolved kurtosis ceiling")

        if detection.get("require_dominant"):
            among = detection.get("dominant_among", [])
            if (
                signal not in among
                or len(among) != len(set(among))
                or any(v not in SUPPORTED_DETECTION_SIGNALS for v in among)
            ):
                raise ValueError(f"{code} has an invalid dominant_among list")
# ***********************
