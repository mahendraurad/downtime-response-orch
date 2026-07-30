"""
tools/rul_calculator.py  —  Phase 5

RUL band logic — rule-based, no ML required for MVP.

Design A (taxonomy-driven): the RUL window for a diagnosis is read straight
from the per-fault rul_days_stage_N values in fault_taxonomy.json, NOT from
fixed ISO-stage bands.  A bearing AT a given stage has between the next
(worse) stage's RUL and this stage's RUL left:

    stage 3 → (0,                rul_days_stage_3)   e.g. FT_001 → (0, 7)
    stage 2 → (rul_days_stage_3, rul_days_stage_2)   e.g. FT_001 → (7, 30)
    stage 1 → (rul_days_stage_2, rul_days_stage_1)   e.g. FT_001 → (30, 90)

The band label is generated from those numbers, so it stays faithful to the
taxonomy across fault modes (a cage fault at stage 3 reads "0–4 days", not a
hardcoded "0–7 days").

Calibration: CASE_001 confirmed RUL = 7 days at Stage 3 outer_race_fault.
FT_001 rul_days_stage_3 == 7, so the taxonomy-driven band is exact.
"""
from typing import Dict, List, Optional, Tuple

# Default monitor band when iso_stage is 0 (undetermined / no matched fault).
_DEFAULT_MONITOR_BAND: Tuple[int, int, str] = (45, 999, "monitor")


def get_rul_band(
    iso_stage: int,
    fault_code: str,
    taxonomy_rules: List[Dict],
    monitor_band: Tuple[int, int, str] = _DEFAULT_MONITOR_BAND,
) -> Tuple[int, int, str]:
    """
    Return (rul_min_days, rul_max_days, band_label) for a diagnosis.

    Taxonomy-driven: pulls rul_days_stage_N from the matched fault rule.
    For iso_stage 0 / unknown fault_code / unmatched code, returns the
    monitor_band unchanged (no imminent timeline).
    """
    if iso_stage not in (1, 2, 3) or not fault_code:
        return monitor_band

    rule = next(
        (r for r in taxonomy_rules if r.get("fault_code") == fault_code), None
    )
    if rule is None:
        return monitor_band

    s1 = int(rule.get("rul_days_stage_1", 0))
    s2 = int(rule.get("rul_days_stage_2", 0))
    s3 = int(rule.get("rul_days_stage_3", 0))

    if iso_stage == 3:
        rul_min, rul_max = 0, s3
    elif iso_stage == 2:
        rul_min, rul_max = s3, s2
    else:  # stage 1
        rul_min, rul_max = s2, s1

    label = f"{rul_min}–{rul_max} days"   # en-dash, e.g. "0–7 days"
    return rul_min, rul_max, label


def compute_financial_exposure(rul_days: int,
                               downtime_cost_per_hour: float) -> float:
    """
    Financial exposure = rul_days * 24h * cost_per_hour.

    `rul_days` is the band's upper bound (rul_max) — the worst-case
    time-to-failure horizon for the assigned stage.  For an outer-race
    stage-3 fault that is 7 days, so a ₹18,000/hr bottleneck yields
    7 * 24 * 18000 = ₹30,24,000.
    """
    return rul_days * 24 * downtime_cost_per_hour
