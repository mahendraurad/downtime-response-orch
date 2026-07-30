"""
Asset-level quality comparison.

Same scenario for two different assets, but Asset A receives mostly clean
records while Asset B receives records with progressively more nulls. Both
get the same per-record validation, but the aggregator rolls them up so you
can see which asset has the worse incoming data quality.

This is a tiny preview of the Layer 3 aggregator you'd build in production.
"""
import sys, os
from copy import deepcopy
from collections import Counter
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.schemas.bearing_signal import ValidationStatus
from src.tools.data_loader import load_telemetry_rows

agent = DataFoundationAgent.from_data_files()

# Two real assets/bearings from the master tables.
ASSET_A = ("AST_MTR_002", "BRG_003", "CH_003")   # healthy motor
ASSET_B = ("AST_MTR_001", "BRG_001", "CH_001")   # outer-race-fault motor

# Same operating values for both — only the null pattern changes.
def make_record(tel_id, asset_id, bearing_id, channel_id, fields_to_null):
    base = deepcopy(load_telemetry_rows("healthy")[0])
    base["telemetry_id"] = tel_id
    base["asset_id"]     = asset_id
    base["bearing_id"]   = bearing_id
    base["channel_id"]   = channel_id
    for f in fields_to_null:
        base[f] = None
    return base

# Null patterns per asset — Asset B's feed is much messier.
null_patterns_A = [
    [],                              # clean
    [],                              # clean
    [],                              # clean
    ["vib_rms_mm_s"],                # one null
    [],                              # clean
]
null_patterns_B = [
    ["vib_rms_mm_s", "kurtosis"],                       # 2 nulls
    ["vib_rms_mm_s", "kurtosis", "temp_c"],             # 3 nulls
    ["temp_c"],                                          # 1 null
    ["vib_rms_mm_s", "kurtosis", "temp_c", "bpfo_energy"],  # 4 nulls
    ["vib_rms_mm_s"],                                    # 1 null
]

def run_asset(label, asset_id, bearing_id, channel_id, null_patterns):
    results = []
    for i, nulls in enumerate(null_patterns):
        tel_id = f"{label}_REC_{i+1:03d}"
        record = make_record(tel_id, asset_id, bearing_id, channel_id, nulls)
        results.append(agent.process(record))
    return results


def aggregate(results):
    scores  = [r.data_quality_score for r in results]
    statuses = Counter(r.validation_status.value for r in results)

    n = len(results)
    return {
        "record_count":  n,
        "mean_score":    round(mean(scores), 4),
        "min_score":     round(min(scores), 4),
        "max_score":     round(max(scores), 4),
        "pct_valid":     round(100 * statuses.get("VALID", 0) / n, 1),
        "pct_flagged":   round(100 * statuses.get("FLAGGED", 0) / n, 1),
        "pct_rejected":  round(100 * statuses.get("REJECTED", 0) / n, 1),
    }


def per_dimension(results):
    """Mean per-dimension score across all records, from the QualityReport."""
    if not results or results[0].quality_report is None:
        return {}
    dims = {}
    for r in results:
        for d in r.quality_report.dimensions:
            dims.setdefault(d.name, []).append(d.score)
    return {name: round(mean(scores), 4) for name, scores in dims.items()}


# ── Run both assets ───────────────────────────────────────────────────────
print("\n" + "=" * 76)
print("  PER-RECORD RESULTS")
print("=" * 76)

for label, (asset, bearing, channel), patterns in [
    ("ASSET_A", ASSET_A, null_patterns_A),
    ("ASSET_B", ASSET_B, null_patterns_B),
]:
    print(f"\n--- {label} ({asset} / {bearing}) ---")
    results = run_asset(label, asset, bearing, channel, patterns)
    for r, nulls in zip(results, patterns):
        n_null = len(nulls)
        print(f"  {r.raw.telemetry_id:<18}  nulls={n_null}  "
              f"score={r.data_quality_score:.4f}  "
              f"status={r.validation_status.value}")
    if label == "ASSET_A":
        results_A = results
    else:
        results_B = results

# ── Asset-level rollup ────────────────────────────────────────────────────
print("\n" + "=" * 76)
print("  ASSET-LEVEL ROLLUP")
print("=" * 76)

rollup_A = aggregate(results_A)
rollup_B = aggregate(results_B)

print(f"\n{'Metric':<22} {'ASSET_A':>12} {'ASSET_B':>12}  {'Delta':>10}")
print("-" * 60)
for key in ["record_count", "mean_score", "min_score", "max_score",
            "pct_valid", "pct_flagged", "pct_rejected"]:
    a, b = rollup_A[key], rollup_B[key]
    delta = b - a if isinstance(a, (int, float)) else ""
    delta_str = f"{delta:+.4f}" if isinstance(delta, float) else (f"{delta:+d}" if isinstance(delta, int) else "")
    print(f"{key:<22} {a:>12} {b:>12}  {delta_str:>10}")

# ── Per-dimension comparison ───────────────────────────────────────────────
print("\n" + "=" * 76)
print("  PER-DIMENSION MEAN SCORES (which dimension hurts each asset most?)")
print("=" * 76)

dims_A = per_dimension(results_A)
dims_B = per_dimension(results_B)

print(f"\n{'Dimension':<20} {'ASSET_A':>10} {'ASSET_B':>10}  {'Delta':>10}")
print("-" * 56)
for name in dims_A:
    a, b = dims_A[name], dims_B[name]
    print(f"{name:<20} {a:>10.4f} {b:>10.4f}  {b - a:>+10.4f}")

print("\nInterpretation: lower mean score on a dimension = that dimension is")
print("the weak link for that asset's incoming data quality.\n")
