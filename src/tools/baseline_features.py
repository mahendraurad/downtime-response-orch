"""
tools/baseline_features.py  —  Phase 3

Pure signal-feature functions for the Monitoring Agent.
No side effects, no DB calls. All thresholds come from MonitoringConfig.

Primary approach: Hotelling T² (multivariate) with z-scores for evidence.

Why T²?
  Z-score treats each signal independently. T² treats the four baseline-backed
  signals (vib_rms, kurtosis, temp, bpfo) as a correlated vector and scores
  deviations using the full covariance structure. It catches:
    * Correlated rises that are individually borderline but jointly anomalous.
    * Unusual deviation PATTERNS (e.g. temp rising faster than vib) that
      per-signal z-scores can't distinguish from normal correlated variation.

Architecture:
  * T² is the PRIMARY anomaly score, normalised to 0–1 via the chi-square CDF.
  * Z-scores are kept for evidence only (triggered_features, per-signal detail).
  * Reduced T² is used when some signals are null or imputed (sub-matrix).
  * Chi-square CDF: exact closed-form for p=1,2,3,4; series expansion for p>4.
    No scipy needed — uses only Python's math stdlib.
  * numpy is used only for the covariance matrix inversion.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Z-score  (kept for evidence / triggered_features)
# ---------------------------------------------------------------------------

def compute_z_score(value: float, mean: float, std: float) -> float:
    """Signed standard deviations from the baseline mean. Returns 0 if std=0."""
    if std == 0:
        return 0.0
    return (value - mean) / std


# ---------------------------------------------------------------------------
# EWMA control chart  (slow-drift detector, parallel to T²)
# ---------------------------------------------------------------------------

def update_ewma(prev_ewma: float, x: float, alpha: float) -> float:
    """Standard EWMA update: alpha * x + (1 - alpha) * prev_ewma."""
    return alpha * x + (1.0 - alpha) * prev_ewma


def ewma_control_sigma(std: float, alpha: float) -> float:
    """
    Steady-state standard deviation of an EWMA series given the underlying
    signal's std and the smoothing factor alpha.
        sigma_ewma = std * sqrt(alpha / (2 - alpha))
    """
    return std * math.sqrt(alpha / (2.0 - alpha))


def ewma_z(ewma: float, mean: float, std: float, alpha: float) -> float:
    """How many EWMA-control-sigmas the smoothed value is from baseline mean."""
    sigma = ewma_control_sigma(std, alpha)
    if sigma == 0:
        return 0.0
    return (ewma - mean) / sigma


def ewma_anomaly_score(max_abs_z: float, control_limit_L: float,
                       at_limit_score: float) -> float:
    """
    Map the worst EWMA |z| to a 0-1 anomaly score on the same scale as T².
    Reaches `at_limit_score` exactly at |z| = L (the firing threshold) and
    saturates at 1.0 by |z| = 2L. Returns 0.0 below L.
    """
    if max_abs_z < control_limit_L:
        return 0.0
    overshoot = (max_abs_z - control_limit_L) / control_limit_L  # 0 at L, 1 at 2L
    return min(1.0, at_limit_score + (1.0 - at_limit_score) * overshoot)


# ---------------------------------------------------------------------------
# Business-readable anomaly explanation  (deterministic — no LLM)
# ---------------------------------------------------------------------------

# Non-technical names for the raw signal fields, written for business / ops
# readers — no acronyms (BPFO/kurtosis) or units. What each reading *means* in
# plain terms, so the explanation reads like a sentence, not a sensor dump.
SIGNAL_LABELS: Dict[str, str] = {
    "vib_rms_mm_s": "the overall vibration",
    "kurtosis":     "the vibration's spikiness (sharp, knocking vibration)",
    "temp_c":       "the bearing temperature",
    "bpfo_energy":  "a vibration pattern that points to outer-ring bearing wear",
    "bpfi_energy":  "a vibration pattern that points to inner-ring bearing wear",
    "bsf_energy":   "a vibration pattern that points to rolling-element wear",
    "ftf_energy":   "a vibration pattern that points to bearing-cage wear",
}

# Operating-regime labels (load_band@speed_band) → plain words.
_LOAD_WORDS  = {"low_load": "light load", "normal_load": "normal load",
                "high_load": "heavy load"}
_SPEED_WORDS = {"below_rated": "below its rated speed", "rated": "its rated speed",
                "above_rated": "above its rated speed"}


def _signal_label(field: str) -> str:
    return SIGNAL_LABELS.get(field, field)


def _join_clauses(items: List[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _plain_severity(z: float) -> str:
    """Translate a z-score magnitude into plain words — no σ, no numbers."""
    direction = "higher than normal" if z >= 0 else "lower than normal"
    a = abs(z)
    if a >= 5:
        return "far " + direction
    if a >= 3:
        return "well " + direction
    if a >= 2:
        return direction
    return "slightly " + direction


def _regime_sentence(regime_label: str) -> str:
    """Plain-words confirmation that the reading was taken while running."""
    load, _, speed = (regime_label or "").partition("@")
    lw, sw = _LOAD_WORDS.get(load), _SPEED_WORDS.get(speed)
    if lw and sw:
        return (f" This was recorded while the machine was running normally "
                f"(at {lw} and {sw}), so it is a genuine reading — not a "
                f"start-up, shutdown, or idle blip.")
    return (" This was recorded while the machine was running, so it is a "
            "genuine reading — not a start-up, shutdown, or idle blip.")


def _confidence_sentence(confidence: float) -> str:
    if confidence >= 0.8:
        return " We are highly confident this is a real problem and not a sensor glitch."
    if confidence >= 0.5:
        return " Confidence in this finding is moderate — worth keeping an eye on."
    return " Confidence is low, so a manual check is recommended before acting."


def build_anomaly_reason(
    z_scores: Dict[str, float],
    t2_fired: bool,
    ewma_triggered: List[str],
    regime_label: str,
    confidence: float,
    notable_sigma: float = 2.0,
    max_signals: int = 3,
) -> str:
    """
    Build a plain-language explanation of *why* a reading was flagged, written
    for business / operations users — no statistics jargon (σ, T², EWMA),
    acronyms, or units. Deterministic — assembled from the same evidence the
    score uses (z-scores, which detector fired, drift, regime, confidence).

    Example:
      "This bearing was flagged because the overall vibration is far higher than
       normal, a vibration pattern that points to outer-ring bearing wear is well
       higher than normal, and the vibration's spikiness is higher than normal.
       Together these readings form a pattern that healthy equipment does not
       show. They have also been steadily worsening over recent readings, not a
       one-off spike. This was recorded while the machine was running normally
       (at normal load and its rated speed), so it is a genuine reading — not a
       start-up, shutdown, or idle blip. We are highly confident this is a real
       problem and not a sensor glitch."
    """
    # Rank deviating signals by magnitude; describe the most significant few.
    ranked  = sorted(z_scores.items(), key=lambda kv: abs(kv[1]), reverse=True)
    notable = [(f, z) for f, z in ranked if abs(z) >= notable_sigma][:max_signals]

    parts: List[str] = []

    if notable:
        phrases = [f"{_signal_label(f)} is {_plain_severity(z)}" for f, z in notable]
        parts.append("This bearing was flagged because " + _join_clauses(phrases) + ".")
        if t2_fired and len(notable) > 1:
            parts.append(" Together these readings form a pattern that healthy "
                         "equipment does not show.")
    else:
        # T²-only / subtle case: nothing stands out alone, but the combination is off.
        parts.append("This bearing was flagged because several readings drifted "
                     "together into a pattern that healthy equipment does not show, "
                     "even though no single reading stands out on its own.")

    if ewma_triggered:
        parts.append(" These readings have also been steadily worsening over "
                     "recent readings, not a one-off spike.")

    parts.append(_regime_sentence(regime_label))
    parts.append(_confidence_sentence(confidence))

    return "".join(parts)


# ---------------------------------------------------------------------------
# Covariance matrix construction  (from per-bearing baseline stats)
# ---------------------------------------------------------------------------

def build_correlation_matrix(bctx) -> np.ndarray:
    """
    Build the 4×4 symmetric correlation matrix R from the six baseline
    pairwise correlations stored on BearingContext.

    Signal order: [vib_rms_mm_s, kurtosis, temp_c, bpfo_energy] — matches
    the signal_order in monitoring_config.json.
    """
    vc = bctx.baseline_corr_vib_kurtosis
    vt = bctx.baseline_corr_vib_temp
    vb = bctx.baseline_corr_vib_bpfo
    kt = bctx.baseline_corr_kurtosis_temp
    kb = bctx.baseline_corr_kurtosis_bpfo
    tb = bctx.baseline_corr_temp_bpfo

    return np.array([
        [1.0,  vc,   vt,   vb ],
        [vc,   1.0,  kt,   kb ],
        [vt,   kt,   1.0,  tb ],
        [vb,   kb,   tb,   1.0],
    ], dtype=float)


def build_covariance_matrix(stds: List[float], corr: np.ndarray) -> np.ndarray:
    """
    Build the covariance matrix Σ from standard deviations and the correlation
    matrix R:  Σ[i,j] = R[i,j] × σ_i × σ_j.
    """
    s = np.array(stds, dtype=float)
    return corr * np.outer(s, s)


# ---------------------------------------------------------------------------
# Hotelling T²
# ---------------------------------------------------------------------------

def compute_hotelling_t2(
    x: np.ndarray,
    mu: np.ndarray,
    cov_inv: np.ndarray,
) -> float:
    """
    T² = (x − μ)ᵀ  Σ⁻¹  (x − μ).

    Under the null hypothesis (healthy operation with n baseline samples),
    (n−1)²/n × T² / p  ~  F(p, n−p), which for large n approximates χ²(p)/p.
    We use the chi-square approximation to normalise to 0–1.
    """
    diff = x - mu
    return float(diff @ cov_inv @ diff)


def invert_covariance(cov: np.ndarray) -> Optional[np.ndarray]:
    """
    Invert the covariance matrix. Returns None if singular (fallback to
    diagonal inverse will be used by the caller).
    """
    try:
        inv = np.linalg.inv(cov)
        # Sanity: result must be finite and positive semi-definite on the diagonal
        if not np.all(np.isfinite(inv)) or np.any(np.diag(inv) < 0):
            return None
        return inv
    except np.linalg.LinAlgError:
        return None


def diagonal_inverse(stds: List[float]) -> np.ndarray:
    """
    Fallback: diagonal Σ⁻¹ (treats signals as independent — equivalent to
    scaled z-score composite). Used when the full matrix is singular.
    """
    return np.diag([1.0 / (s ** 2) if s > 0 else 0.0 for s in stds])


# ---------------------------------------------------------------------------
# Chi-square CDF  (pure stdlib — no scipy needed)
# ---------------------------------------------------------------------------

def chi2_cdf(x: float, p: int) -> float:
    """
    Cumulative distribution function of the chi-square distribution with
    p degrees of freedom.  P(χ²_p ≤ x).

    Exact closed-form for p = 1, 2, 3, 4 (the only values used here).
    Falls back to the regularised lower incomplete gamma function for p > 4.

    p = 2: 1 − e^(−x/2)
    p = 4: 1 − e^(−x/2)(1 + x/2)           (general even: subtract Poisson sum)
    p = 1: erf(√(x/2))
    p = 3: erf(√(x/2)) − √(2x/π) e^(−x/2)
    """
    if x <= 0:
        return 0.0
    t = x / 2.0
    if p == 1:
        return math.erf(math.sqrt(t))
    if p == 2:
        return 1.0 - math.exp(-t)
    if p == 3:
        e = math.exp(-t)
        return math.erf(math.sqrt(t)) - math.sqrt(x / math.pi) * e
    if p == 4:
        return 1.0 - math.exp(-t) * (1.0 + t)
    # General: regularised lower incomplete gamma P(p/2, x/2)
    return _regularised_gamma(p / 2.0, t)


def _regularised_gamma(a: float, x: float) -> float:
    """
    Regularised lower incomplete gamma function P(a, x) via series expansion.
    Converges rapidly for x ≤ a + 1; for larger x use a continued fraction,
    but for our use case (p ≤ 4, x mostly < 50) the series is sufficient.
    """
    if x <= 0:
        return 0.0
    term = 1.0 / a
    total = term
    for k in range(1, 300):
        term *= x / (a + k)
        total += term
        if abs(term) < 1e-14 * abs(total):
            break
    try:
        return min(1.0, total * math.exp(-x + a * math.log(x) - math.lgamma(a)))
    except (OverflowError, ValueError):
        return 1.0


def t2_anomaly_score(t2: float, p: int) -> float:
    """
    Normalise T² to a 0–1 anomaly score via the chi-square CDF.

    score = P(χ²_p ≤ T²)

    Interpretation:
      score ≈ 0.06  →  barely unusual (typical healthy noise)
      score ≈ 0.85  →  at the detection threshold (~5% false-alarm boundary)
      score ≈ 0.99  →  clearly anomalous
      score ≈ 1.00  →  extremely anomalous (>99.9th percentile)
    """
    return round(chi2_cdf(float(t2), int(p)), 4)


# ---------------------------------------------------------------------------
# Regime classifier
# ---------------------------------------------------------------------------

def classify_regime(
    rpm: Optional[float],
    load_pct: Optional[float],
    machine_state: Optional[str],
    rated_rpm: Optional[float],
    cfg,
) -> Dict:
    """
    Classify the operating regime. Detection is suppressed when not running.

    Returns:
      { "label": "normal_load@rated", "running": bool,
        "load_band": str, "speed_band": str }
    """
    state_norm = machine_state.lower() if isinstance(machine_state, str) else None
    running = (
        state_norm in cfg.running_states
        and rpm is not None and rpm >= cfg.running_min_rpm
    )

    if load_pct is None:
        load_band = "unknown_load"
    elif load_pct < cfg.low_load_max_pct:
        load_band = "low_load"
    elif load_pct >= cfg.high_load_min_pct:
        load_band = "high_load"
    else:
        load_band = "normal_load"

    if rpm is None or not rated_rpm:
        speed_band = "unknown_speed"
    else:
        tol = cfg.rated_rpm_tolerance_pct / 100.0
        if rpm < rated_rpm * (1 - tol):
            speed_band = "below_rated"
        elif rpm > rated_rpm * (1 + tol):
            speed_band = "above_rated"
        else:
            speed_band = "rated"

    return {
        "label":      f"{load_band}@{speed_band}",
        "running":    running,
        "load_band":  load_band,
        "speed_band": speed_band,
    }


# ---------------------------------------------------------------------------
# Confidence score
# ---------------------------------------------------------------------------

def compute_confidence(
    signal_quality: Optional[float],
    coverage: float,
    data_quality: float,
    weights: Dict[str, float],
) -> float:
    """
    Confidence that the T² verdict is trustworthy — blend of sensor fidelity,
    signal coverage (fraction of expected signals actually scored), and the
    DFA data-quality score.
    """
    sq = 1.0 if signal_quality is None else signal_quality
    score = (
        weights.get("signal_quality", 0.0) * sq +
        weights.get("coverage", 0.0)       * coverage +
        weights.get("data_quality", 0.0)   * data_quality
    )
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# Rolling trend  (ready for history-backed builds)
# ---------------------------------------------------------------------------

def compute_rolling_trend(values: List[float], window: int = 5) -> Optional[float]:
    """
    Least-squares slope over the last `window` values.  Positive = rising trend.
    Returns None if fewer than 2 points are available.
    Not wired into the per-record agent (MVP uses static baselines); provided
    for the future EWMA / CUSUM extension.
    """
    if not values or len(values) < 2:
        return None
    pts = values[-window:]
    n = len(pts)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(pts) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    num = sum((xs[i] - mean_x) * (pts[i] - mean_y) for i in range(n))
    return num / denom
