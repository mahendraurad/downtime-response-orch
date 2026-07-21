"""
tools/fault_matcher.py  —  Phase 4

Data-driven threshold matching against the fault taxonomy
(data/fault_taxonomy.json). All functions are pure — taxonomy rules are
injected, there is no global state and no I/O. The Failure Intelligence Agent
computes the derived inputs (temp_rise, broadband_pattern, vib_ratio) and calls
these to classify a fault.

Data-driven matching (no hardcoded per-fault logic):
  Each matchable taxonomy rule carries a structured ``detection`` block, e.g.

      "detection": {
        "priority": 1,
        "signal": "bpfo_energy",
        "kurtosis": { "mode": "above", "threshold_field": "kurtosis_threshold" },
        "require_dominant": false,
        "dominant_among": [],
        "require_broadband": false,
        "require_temp_rise": false
      }

  ``evaluate_candidates`` reads this block for every rule and applies a single
  generic evaluator. Nothing about a specific fault code is baked into the code:
  to add a new fault mode, add a rule with a ``detection`` block — no code change.
  A rule WITHOUT a detection block is dormant (skipped) — used for fault modes
  whose signal is not in the telemetry schema yet (e.g. 1X/2X imbalance).

  ``severity_logic`` remains in the taxonomy as the human-readable doc string;
  it is no longer parsed or trusted by the code — the structured block is the
  single source of truth, so a reworded sentence can never change behaviour.

Unit note (critical):
  bpfo_energy / bpfi_energy / ftf_energy values in telemetry are *direct*
  threshold values — compared straight to stage_N_vib_multiple in the taxonomy.
  No baseline multiplication. (vib_ratio for the broadband rule IS a ratio,
  computed by the agent as vib_rms / baseline_vib.)

Stage boundary semantics:
  Stage comparison uses ``>=`` — a value sitting exactly on a threshold belongs
  to the higher stage (e.g. bpfo == 3.5 == stage_3_vib_multiple → 3).
"""
from __future__ import annotations

from typing import List, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Rule value extractors  (single place that reads values out of a rule)
# ---------------------------------------------------------------------------

def get_detection(rule: Dict) -> Optional[Dict]:
    """The structured detection block, or None if the rule is dormant."""
    return rule.get("detection")


def is_matchable(rule: Dict) -> bool:
    """True if the rule carries a detection block (and so can be matched)."""
    return bool(get_detection(rule))


def kurtosis_threshold(rule: Dict) -> float:
    """The rule's kurtosis gate value."""
    return float(rule.get("kurtosis_threshold", 0.0))


def stage_multiples(rule: Dict) -> Tuple[float, float, float]:
    """(stage_1, stage_2, stage_3) vib multiples for the rule."""
    return (
        float(rule.get("stage_1_vib_multiple", 0.0)),
        float(rule.get("stage_2_vib_multiple", 0.0)),
        float(rule.get("stage_3_vib_multiple", 0.0)),
    )


def find_rule_for_signal(rules: List[Dict], signal_field: str) -> Optional[Dict]:
    """
    The matchable rule whose detection keys off ``signal_field`` (e.g. find the
    rule that detects via 'bpfo_energy'). Used to look up a band's thresholds
    without hardcoding fault codes. Returns None if no rule owns that signal.
    """
    for rule in rules:
        det = get_detection(rule)
        if det and det.get("signal") == signal_field:
            return rule
    return None


def band_stage1_threshold(rules: List[Dict], signal_field: str,
                          default: float = float("inf")) -> float:
    """
    Stage-1 vib multiple of the rule that detects via ``signal_field`` — i.e.
    the level at which that band counts as a dominant discrete defect. The value
    is extracted from the owning rule (single source of truth), never hardcoded.
    Falls back to ``default`` only if no rule owns the signal; the default is
    +inf so an unowned band simply can't count as dominant (it never blocks
    broadband classification) rather than introducing a magic threshold.
    """
    rule = find_rule_for_signal(rules, signal_field)
    if rule is None:
        return default
    return stage_multiples(rule)[0]


def resolve_threshold(value, rules: List[Dict], default=None):
    """
    Resolve a threshold that is either a literal number or a *reference* that
    extracts the value from another rule. A reference identifies the owning rule
    by either its detection ``signal`` (preferred — survives a fault_code rename)
    or its ``fault_code``:

        { "signal": "bpfo_energy", "field": "kurtosis_threshold" }
        { "fault_code": "FT_001",  "field": "kurtosis_threshold" }

    A reference keeps ONE source of truth — change the value on the owning rule
    and every rule that references it follows automatically, with no duplicated
    literal to keep in sync. Returns ``default`` if the reference can't resolve.
    """
    if isinstance(value, dict):
        if "signal" in value:
            owner = find_rule_for_signal(rules, value["signal"])
        else:
            owner = next(
                (r for r in rules if r.get("fault_code") == value.get("fault_code")),
                None,
            )
        if owner is None:
            return default
        return owner.get(value.get("field"), default)
    return default if value is None else value


# ---------------------------------------------------------------------------
# ISO stage
# ---------------------------------------------------------------------------

def determine_iso_stage(energy_value: Optional[float], rule: Dict) -> int:
    """
    ISO stage (3 / 2 / 1) from an energy (or ratio) value and a taxonomy rule.
    Returns 0 when the value is below the stage-1 threshold (no stage crossed).

    Boundary belongs to the higher stage (``>=``).
    """
    if energy_value is None:
        return 0
    s1, s2, s3 = stage_multiples(rule)
    if energy_value >= s3:
        return 3
    if energy_value >= s2:
        return 2
    if energy_value >= s1:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Derived inputs
# ---------------------------------------------------------------------------

def compute_temp_rise(temp_c: Optional[float],
                      baseline_temp_mean: Optional[float]) -> float:
    """Temperature rise above the bearing's healthy baseline (°C). 0.0 if unknown."""
    if temp_c is None or baseline_temp_mean is None:
        return 0.0
    return round(temp_c - baseline_temp_mean, 4)


def detect_broadband_pattern(
    vib_rms: Optional[float],
    baseline_vib_mean: Optional[float],
    bpfo_energy: Optional[float],
    bpfi_energy: Optional[float],
    bpfo_stage_1: float,
    bpfi_stage_1: float,
    vib_elevation_ratio: float,
) -> bool:
    """
    Broadband pattern = overall vibration elevated vs baseline AND no single
    defect band dominates (neither BPFO nor BPFI crosses its own stage-1
    multiple). This is the lubrication-issue fingerprint: energy rises across
    the spectrum without a discrete defect frequency standing out.

    The two stage-1 thresholds are looked up from the taxonomy by the caller
    (via band_stage1_threshold) — not hardcoded.
    """
    if vib_rms is None or not baseline_vib_mean:
        return False
    elevated = vib_rms >= baseline_vib_mean * vib_elevation_ratio
    no_dominant_band = (bpfo_energy or 0.0) < bpfo_stage_1 and \
                       (bpfi_energy or 0.0) < bpfi_stage_1
    return elevated and no_dominant_band


# ---------------------------------------------------------------------------
# Generic candidate evaluation (drives both the verdict and the differentials)
# ---------------------------------------------------------------------------

def _signal_values(
    bpfo_energy: Optional[float],
    bpfi_energy: Optional[float],
    bsf_energy: Optional[float],
    ftf_energy: Optional[float],
    vib_ratio: float,
) -> Dict[str, float]:
    """
    The signal values a detection block may key off. Raw band energies plus the
    derived vib_ratio (overall vibration / baseline) used by the broadband rule.
    A detection.signal naming a field absent here resolves to 0.0 — so a rule
    for an unpublished signal simply never matches.
    """
    return {
        "bpfo_energy": bpfo_energy or 0.0,
        "bpfi_energy": bpfi_energy or 0.0,
        "bsf_energy":  bsf_energy or 0.0,
        "ftf_energy":  ftf_energy or 0.0,
        "vib_ratio":   vib_ratio or 0.0,
    }


def _kurtosis_ok(detection: Dict, rule: Dict, kurtosis: float,
                 rules: List[Dict]) -> Tuple[bool, str]:
    """
    Evaluate the detection's kurtosis gate. Returns (ok, human_description).
      mode 'above': kurtosis must exceed the threshold named by threshold_field
                    (a field on this rule, or a reference object).
      mode 'below': kurtosis must stay below the ceiling (literal or reference).
    All threshold values are extracted, never hardcoded here.
    """
    spec = detection.get("kurtosis", {})
    mode = spec.get("mode", "above")
    if mode == "below":
        ceiling = resolve_threshold(spec.get("ceiling"), rules)
        if ceiling is None:
            return True, "no kurtosis ceiling"
        ok = kurtosis < ceiling
        return ok, f"kurtosis {kurtosis} {'<' if ok else '≥'} {ceiling}"
    # default: "above". threshold_field may name a field on this rule (str) or
    # be a {signal|fault_code, field} reference object.
    field = spec.get("threshold_field", "kurtosis_threshold")
    thr = float(resolve_threshold(field if isinstance(field, dict) else rule.get(field),
                                  rules, default=0.0))
    ok = kurtosis > thr
    return ok, f"kurtosis {kurtosis} {'>' if ok else '≤'} {thr}"


def _evaluate_one(rule: Dict, values: Dict[str, float], kurtosis: float,
                  temp_rise: float, broadband_pattern: bool,
                  rules: List[Dict]) -> Dict:
    """Evaluate a single rule against the observed signals using its detection block."""
    det        = get_detection(rule)
    signal     = det.get("signal", "")
    signal_val = values.get(signal, 0.0)
    stage      = determine_iso_stage(signal_val, rule)

    kurt_ok, kurt_desc = _kurtosis_ok(det, rule, kurtosis, rules)

    # Dominance: the signal must be the largest band in dominant_among.
    require_dominant = det.get("require_dominant", False)
    dominant = True
    if require_dominant:
        among = det.get("dominant_among", [])
        peak  = max((values.get(f, 0.0) for f in among), default=0.0)
        dominant = signal_val == peak and signal_val > 0

    require_broadband = det.get("require_broadband", False)
    require_temp_rise = det.get("require_temp_rise", False)

    conditions = [stage > 0, kurt_ok]
    if require_dominant:
        conditions.append(dominant)
    if require_broadband:
        conditions.append(broadband_pattern)
    if require_temp_rise:
        conditions.append(temp_rise > 0)
    matched = all(conditions)

    # has_evidence: worth surfacing as a differential even when not the primary.
    if require_broadband:
        has_evidence = matched or broadband_pattern
    elif require_dominant:
        has_evidence = matched or (dominant and stage > 0)
    else:
        has_evidence = matched or stage > 0 or kurt_ok

    band_label     = rule.get("dominant_band", signal)
    dominant_value = round(signal_val, 4) if signal == "vib_ratio" else signal_val

    reason = _build_reason(
        band_label, signal_val, stage, kurt_desc, matched,
        require_dominant, dominant, require_broadband, broadband_pattern,
        require_temp_rise, temp_rise,
    )

    return {
        "fault_code":     rule["fault_code"],
        "fault_mode":     rule["fault_mode"],
        "matched":        matched,
        "iso_stage":      stage,
        "dominant_band":  band_label,
        "dominant_value": dominant_value,
        "reason":         reason,
        "has_evidence":   has_evidence,
    }


def _build_reason(band_label, signal_val, stage, kurt_desc, matched,
                  require_dominant, dominant, require_broadband,
                  broadband_pattern, require_temp_rise, temp_rise) -> str:
    """Human-readable reason for the (non-)match, assembled from the outcome."""
    if matched:
        bits = [f"{band_label} {signal_val} crossed stage-{stage}", kurt_desc]
        if require_dominant:
            bits.append(f"{band_label} is the dominant band")
        if require_broadband:
            bits.append("broadband rise with no dominant defect band")
        if require_temp_rise:
            bits.append(f"temp rise {temp_rise}°C")
        return "matched: " + ", ".join(bits)

    # Not matched — report the first failing condition.
    if require_broadband and not broadband_pattern:
        return "a discrete defect band dominates — not broadband"
    if require_temp_rise and temp_rise <= 0:
        return "no temperature rise above baseline"
    if require_dominant and not dominant:
        return f"{band_label} is not the dominant band"
    if stage == 0:
        return f"{band_label} {signal_val} below stage-1 threshold"
    # stage > 0 but kurtosis gate failed
    return f"{band_label} crossed stage-{stage} but {kurt_desc}"


def evaluate_candidates(
    bpfo_energy: Optional[float],
    bpfi_energy: Optional[float],
    bsf_energy: Optional[float],
    ftf_energy: Optional[float],
    kurtosis: Optional[float],
    temp_rise: float,
    broadband_pattern: bool,
    vib_ratio: float,
    taxonomy_rules: List[Dict],
) -> List[Dict]:
    """
    Evaluate every matchable fault mode (those with a detection block) in
    priority order and return one candidate dict per mode:

        {
          "fault_code", "fault_mode", "matched": bool, "iso_stage": int,
          "dominant_band": str, "dominant_value": float, "reason": str,
          "has_evidence": bool
        }

    Priority comes from each rule's detection.priority (lower = tried first);
    the first matched candidate is the primary diagnosis, the rest with partial
    evidence become differentials. Adding/removing/reordering faults is a pure
    taxonomy edit — no code change here.
    """
    k      = kurtosis if kurtosis is not None else 0.0
    values = _signal_values(bpfo_energy, bpfi_energy, bsf_energy, ftf_energy, vib_ratio)

    matchable = [r for r in taxonomy_rules if is_matchable(r)]
    matchable.sort(key=lambda r: get_detection(r).get("priority", 999))

    return [
        _evaluate_one(rule, values, k, temp_rise, broadband_pattern, taxonomy_rules)
        for rule in matchable
    ]


def match_fault_taxonomy(
    bpfo_energy: float,
    bpfi_energy: float,
    bsf_energy: float,
    kurtosis: float,
    temp_rise: float,
    broadband_pattern: bool,
    taxonomy_rules: List[Dict],
    ftf_energy: float = 0.0,
    vib_ratio: float = 0.0,
) -> Optional[Tuple[str, str, int]]:
    """
    Return (fault_code, fault_mode, iso_stage) for the best match, or None if
    no rule's signature is crossed. Thin wrapper over evaluate_candidates() —
    returns the first matched candidate in priority order.
    """
    candidates = evaluate_candidates(
        bpfo_energy, bpfi_energy, bsf_energy, ftf_energy, kurtosis,
        temp_rise, broadband_pattern, vib_ratio, taxonomy_rules,
    )
    for c in candidates:
        if c["matched"]:
            return c["fault_code"], c["fault_mode"], c["iso_stage"]
    return None
