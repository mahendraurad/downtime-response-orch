"""
tools/validators.py

Field-level validation functions called by the Data Foundation Agent.
Each function is pure: takes only what it needs, returns a (bool, reason_str) tuple.

All thresholds and limits come from DFAConfig (loaded from config/dfa_config.json).
No magic numbers live in this file — if you need to change a threshold, edit the
config file and restart the agent.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.tools.config_loader import DFAConfig

# Type alias
CheckResult = Tuple[bool, str]


# ---------------------------------------------------------------------------
# 1. Asset mapping
# ---------------------------------------------------------------------------

def validate_asset_id(asset_id: str, asset_lookup: Dict) -> CheckResult:
    """Hard rejection if asset_id not in master — no context can be joined."""
    if not asset_id or not asset_id.strip():
        return False, "asset_id is empty or null"
    if asset_id not in asset_lookup:
        return False, (
            f"asset_id '{asset_id}' not found in asset master — record cannot be routed. "
            f"Fix: register this asset in asset_master.json (Table 3)"
        )
    return True, ""


# ---------------------------------------------------------------------------
# 2. Bearing / channel mapping
# ---------------------------------------------------------------------------

def validate_bearing_id(
    bearing_id: str, channel_id: str,
    bearing_lookup: Dict, channel_lookup: Dict
) -> CheckResult:
    """Hard rejection if bearing_id or channel_id missing or mismatched."""
    if not bearing_id or not bearing_id.strip():
        return False, "bearing_id is empty or null"
    if not channel_id or not channel_id.strip():
        return False, "channel_id is empty or null"

    b_by_bearing = bearing_lookup.get(bearing_id)
    b_by_channel = channel_lookup.get(channel_id)

    if b_by_bearing is None:
        return False, (
            f"bearing_id '{bearing_id}' not found in bearing master. "
            f"Fix: register this bearing in bearing_master.json (Table 2)"
        )
    if b_by_channel is None:
        return False, (
            f"channel_id '{channel_id}' not found in bearing master. "
            f"Fix: register this channel_id in bearing_master.json (Table 2)"
        )
    if b_by_bearing.bearing_id != b_by_channel.bearing_id:
        return False, (
            f"bearing_id '{bearing_id}' and channel_id '{channel_id}' "
            f"resolve to different bearing records — possible sensor mis-labelling. "
            f"Fix: verify channel_id assignment in bearing_master.json"
        )
    return True, ""


# ---------------------------------------------------------------------------
# 3. Value range checks  (Validity / Conformity)
# ---------------------------------------------------------------------------

def validate_ranges(
    signal: Dict,
    cfg: DFAConfig,
    bearing_record=None,
) -> Tuple[bool, List[str]]:
    """
    Checks all numeric signal values are within valid operating bounds.
    Per-bearing bounds from BearingMaster take priority over config fallbacks.
    """
    reasons = []

    # RPM
    rpm = signal.get("rpm")
    if rpm is not None:
        if rpm < 0:
            reasons.append(
                f"rpm={rpm} is negative — physically impossible. "
                f"Fix: check tachometer wiring and historian scaling"
            )
        elif rpm > cfg.max_rpm:
            reasons.append(
                f"rpm={rpm} exceeds maximum ({cfg.max_rpm}) — likely a scaling error. "
                f"Fix: verify historian engineering unit conversion "
                f"(config: validation.max_rpm)"
            )

    # Load %
    load_pct = signal.get("load_pct")
    if load_pct is not None:
        if load_pct < 0:
            reasons.append(
                f"load_pct={load_pct} is negative. "
                f"Fix: check load sensor wiring"
            )
        elif load_pct > cfg.max_load_pct:
            reasons.append(
                f"load_pct={load_pct} exceeds {cfg.max_load_pct}% — likely sensor error. "
                f"Fix: check load sensor calibration "
                f"(config: validation.max_load_pct)"
            )

    # Vibration — prefer BearingMaster bounds, fall back to config
    vib_min = getattr(bearing_record, "vib_min_valid", cfg.fallback_vib_min)
    vib_max = getattr(bearing_record, "vib_max_valid", cfg.fallback_vib_max)
    vib = signal.get("vib_rms_mm_s")
    if vib is not None:
        if vib < vib_min:
            reasons.append(
                f"vib_rms_mm_s={vib} below sensor minimum ({vib_min} mm/s). "
                f"Fix: check accelerometer wiring or historian zero-offset"
            )
        elif vib > vib_max:
            reasons.append(
                f"vib_rms_mm_s={vib} exceeds sensor maximum ({vib_max} mm/s). "
                f"Fix: check accelerometer range and historian scaling"
            )

    # Temperature — prefer BearingMaster bounds, fall back to config
    temp_min = getattr(bearing_record, "temp_min_valid", cfg.fallback_temp_min)
    temp_max = getattr(bearing_record, "temp_max_valid", cfg.fallback_temp_max)
    temp = signal.get("temp_c")
    if temp is not None:
        if temp < temp_min:
            reasons.append(
                f"temp_c={temp} below sensor minimum ({temp_min}°C). "
                f"Fix: check thermocouple wiring"
            )
        elif temp > temp_max:
            reasons.append(
                f"temp_c={temp} exceeds sensor maximum ({temp_max}°C). "
                f"Fix: check thermocouple calibration and historian scaling"
            )

    # Kurtosis physical floor
    kurtosis = signal.get("kurtosis")
    if kurtosis is not None and kurtosis < cfg.min_kurtosis:
        reasons.append(
            f"kurtosis={kurtosis} is below {cfg.min_kurtosis} — physically impossible. "
            f"Fix: check vibration signal processing settings in historian "
            f"(config: validation.min_kurtosis)"
        )

    # Band energies — must be non-negative
    for band in ["bpfo_energy", "bpfi_energy", "bsf_energy", "ftf_energy"]:
        val = signal.get(band)
        if val is not None and val < 0:
            reasons.append(
                f"{band}={val} is negative — band energy cannot be negative. "
                f"Fix: check FFT processing and band extraction settings in historian"
            )

    return len(reasons) == 0, reasons


# ---------------------------------------------------------------------------
# 4. Accuracy  (sensor fidelity, from the historian's own quality score)
# ---------------------------------------------------------------------------

def compute_accuracy_score(
    signal: Dict,
    cfg: DFAConfig,
) -> Tuple[float, List[str]]:
    """
    Accuracy dimension — how much the reading can be trusted, using the
    historian-reported signal_quality_score (0.0–1.0) as a proxy for sensor
    fidelity (cable noise, dropout, saturation).

    Returns (score, [reason, ...]).
      - score is the signal_quality_score itself (clamped to [0, 1]).
      - if the field is absent we assume full accuracy (1.0); a missing
        payload is penalised by the completeness dimension, not here.
      - an out-of-range value scores 0.0; a value below the configured
        floor keeps its (low) score and raises a substantive reason.
    """
    sq = signal.get("signal_quality_score")
    if sq is None:
        return 1.0, []

    if sq < 0 or sq > 1.0:
        return 0.0, [
            f"signal_quality_score={sq} is outside 0.0–1.0 range. "
            f"Fix: check historian signal quality calculation"
        ]

    if sq < cfg.min_signal_quality_score:
        return round(sq, 4), [
            f"signal_quality_score={sq:.2f} below quality floor "
            f"({cfg.min_signal_quality_score}) — sensor may be malfunctioning. "
            f"Fix: inspect sensor cable and connection at junction box "
            f"(config: validation.min_signal_quality_score)"
        ]

    return round(sq, 4), []


# ---------------------------------------------------------------------------
# 5. Consistency  (cross-field logical agreement)
# ---------------------------------------------------------------------------

def check_consistency(
    signal: Dict,
    cfg: DFAConfig,
) -> Tuple[bool, List[str]]:
    """
    Consistency dimension — checks that independent fields tell the same
    physical story. A record can pass every per-field range check and still
    be self-contradictory (e.g. state='running' but rpm≈0). All thresholds
    come from the config 'consistency' block.

    Returns (is_consistent, [reason, ...]).
    """
    reasons = []

    state = signal.get("machine_state")
    rpm   = signal.get("rpm")
    vib   = signal.get("vib_rms_mm_s")

    state_norm = state.lower() if isinstance(state, str) else None

    # Rule 1 — declared running but barely turning
    if (state_norm in cfg.running_states
            and rpm is not None and rpm < cfg.running_min_rpm):
        reasons.append(
            f"machine_state='{state}' but rpm={rpm} is near zero "
            f"(< {cfg.running_min_rpm}) — state and speed disagree. "
            f"Fix: confirm rpm and machine_state come from the same asset"
        )

    # Rule 2 — declared stopped/idle but still spinning
    if (state_norm in cfg.stopped_states
            and rpm is not None and rpm > cfg.stopped_rpm_threshold):
        reasons.append(
            f"machine_state='{state}' but rpm={rpm} is high "
            f"(> {cfg.stopped_rpm_threshold}) — state and speed disagree. "
            f"Fix: confirm rpm and machine_state come from the same asset"
        )

    # Rule 3 — effectively stopped but still vibrating strongly
    if (rpm is not None and rpm < cfg.stopped_rpm_threshold
            and vib is not None and vib > cfg.stopped_max_vib_mm_s):
        reasons.append(
            f"rpm={rpm} indicates the machine is stopped but "
            f"vib_rms_mm_s={vib} exceeds {cfg.stopped_max_vib_mm_s} mm/s — "
            f"speed and vibration disagree. "
            f"Fix: check the vibration channel for a stuck/frozen value"
        )

    return len(reasons) == 0, reasons


# ---------------------------------------------------------------------------
# 6. Completeness
# ---------------------------------------------------------------------------

def compute_completeness_score(
    signal: Dict,
    cfg: DFAConfig,
) -> Tuple[float, List[str]]:
    """
    Returns (fraction, [missing_field, ...]).
    Uses cfg.critical_signal_fields — edit config to change which fields matter.
    """
    missing = [
        f for f in cfg.critical_signal_fields
        if signal.get(f) is None
    ]
    score = 1.0 - (len(missing) / len(cfg.critical_signal_fields))
    return round(score, 4), missing


# ---------------------------------------------------------------------------
# 7. Composite quality score
# ---------------------------------------------------------------------------

def compute_data_quality_score(
    completeness:   float,
    ranges_valid:   bool,
    accuracy:       float,
    consistency:    bool,
    asset_mapped:   bool,
    bearing_mapped: bool,
    cfg:            DFAConfig,
) -> float:
    """
    Weighted composite score [0.0, 1.0] across the six data-quality
    dimensions. Weights come from cfg.weights — edit config/dfa_config.json
    to rebalance.
    """
    w = cfg.weights
    score = (
        w["asset_mapping"]   * (1.0 if asset_mapped   else 0.0) +
        w["bearing_mapping"] * (1.0 if bearing_mapped else 0.0) +
        w["completeness"]    * completeness +
        w["ranges_valid"]    * (1.0 if ranges_valid   else 0.0) +
        w["accuracy"]        * accuracy +
        w["consistency"]     * (1.0 if consistency    else 0.0)
    )
    return round(score, 4)