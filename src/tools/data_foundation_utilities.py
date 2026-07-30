"""Deterministic utility functions used by the Data Foundation Agent."""

# ************** Added by Prateek Mittal on 16th July 2026 ******************
# Agent 1 utility layer: normalization, freshness, relationship validation,
# and explicit routing are kept deterministic and independently testable.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class NormalizationResult:
    record: Dict[str, Any]
    actions: List[str] = field(default_factory=list)
    unknown_fields: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class FreshnessResult:
    valid: bool
    parsed_timestamp: Optional[datetime]
    age_seconds: Optional[float]
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RoutingDecision:
    downstream_eligible: bool
    route: str
    reason: str


def normalize_record(raw: Dict[str, Any], aliases: Dict[str, str],
                     conversions: Dict[str, Dict[str, Any]],
                     known_fields: set[str]) -> NormalizationResult:
    """Return a normalized copy; the caller's dictionary is never mutated."""
    normalized = dict(raw)
    actions: List[str] = []

    for source, target in aliases.items():
        if source in normalized and target not in normalized:
            normalized[target] = normalized.pop(source)
            actions.append(f"renamed {source} to {target}")

    for source, spec in conversions.items():
        if source not in normalized:
            continue
        target = spec["target"]
        if target in normalized:
            continue
        value = normalized.pop(source)
        if value is not None:
            value = float(value) * float(spec.get("multiply", 1.0)) + float(spec.get("add", 0.0))
        normalized[target] = value
        actions.append(f"converted {source} to {target}")

    unknown = sorted(key for key in normalized if key not in known_fields)
    return NormalizationResult(normalized, actions, unknown)


def validate_timestamp(timestamp_utc: str, data_source: str, now: datetime,
                       require_timezone: bool, max_future_seconds: float,
                       max_age_seconds: float, live_sources: List[str]) -> FreshnessResult:
    reasons: List[str] = []
    try:
        text = str(timestamp_utc).strip()
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except (TypeError, ValueError):
        return FreshnessResult(False, None, None, ["timestamp_utc must be a valid ISO-8601 timestamp"])

    if parsed.tzinfo is None:
        if require_timezone:
            return FreshnessResult(False, parsed, None, ["timestamp_utc must include a timezone"])
        parsed = parsed.replace(tzinfo=timezone.utc)

    parsed_utc = parsed.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    age = (now_utc - parsed_utc).total_seconds()
    if age < -max_future_seconds:
        reasons.append(f"timestamp_utc is {-age:.1f}s in the future")
    if str(data_source).lower() in {source.lower() for source in live_sources} and age > max_age_seconds:
        reasons.append(f"live record is stale by {age:.1f}s (maximum {max_age_seconds:.1f}s)")
    return FreshnessResult(not reasons, parsed_utc, round(age, 3), reasons)


def validate_asset_bearing_relationship(asset_id: str, bearing_record: Any) -> tuple[bool, str]:
    """Ensure the resolved bearing actually belongs to the supplied asset."""
    owner = getattr(bearing_record, "asset_id", None)
    if owner != asset_id:
        return False, (
            f"bearing_id '{getattr(bearing_record, 'bearing_id', '')}' belongs to asset_id "
            f"'{owner}', not '{asset_id}'"
        )
    return True, ""


def evaluate_routing(status: str, routes: Dict[str, str]) -> RoutingDecision:
    route = routes.get(status, "stop")
    eligible = route == "monitoring"
    reasons = {
        "monitoring": "record passed the Data Foundation quality gate",
        "data_review": "record requires review or remediation before Monitoring",
        "stop": "record is unusable and processing must stop",
    }
    return RoutingDecision(eligible, route, reasons.get(route, f"route selected: {route}"))

# ***********************
