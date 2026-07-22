"""
Monitoring Agent — asset-level anomaly rollup.

Runs every telemetry scenario through DFA -> Monitoring and produces one
row per asset (worst-record-wins) plus a per-asset evidence block.

Run from project root:
    python test_monitoring_agent.py
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.tools.data_loader import _load_json, load_telemetry_rows


def reset_state(path: str = "data/ewma_state.json") -> None:
    """Wipe ewma_state.json so the run starts cold."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if os.path.exists(p):
        os.remove(p)


def run_scenario(dfa, mon, scenario):
    """Process one scenario end-to-end and return (trusted_list, events_list)."""
    raw_rows = load_telemetry_rows(scenario)
    trusted  = [dfa.process(r) for r in raw_rows]
    events   = [mon.process(t) if t.is_processable else None for t in trusted]
    return trusted, events


# ── Setup ────────────────────────────────────────────────────────────────
dfa = DataFoundationAgent.from_data_files()
reset_state()
mon = MonitoringAgent.from_config()

SCENARIOS = [s for s in _load_json("telemetry_scenarios.json") if not s.startswith("_")]

# Collect every (trusted, event) pair across all scenarios
all_pairs = []
for sc in SCENARIOS:
    trusted, events = run_scenario(dfa, mon, sc)
    for t, e in zip(trusted, events):
        all_pairs.append((t, e, sc))

# Group by asset (worst-record-wins)
by_asset = {}
for t, e, sc in all_pairs:
    asset = t.raw.asset_id
    bucket = by_asset.setdefault(asset, {
        "records": 0, "routable": 0, "anomalies": [],
        "scenarios": set(), "worst": None,
    })
    bucket["records"] += 1
    if t.is_processable:
        bucket["routable"] += 1
    if e is not None:
        bucket["anomalies"].append(e)
        bucket["scenarios"].add(sc)
        if bucket["worst"] is None or e.anomaly_score > bucket["worst"].anomaly_score:
            bucket["worst"] = e

# =========================================================================
# Asset-level anomaly rollup
# =========================================================================
print("=" * 100)
print("  ASSET-LEVEL ANOMALY ROLLUP  (one row per asset, worst-record-wins)")
print("=" * 100)

print(f"\n{'Asset':<15}{'Records':>9}{'Routable':>10}{'Anomalies':>11}"
      f"{'Score':>8}{'Confidence':>12}  {'Methods':<18} Triggered signals")
print("-" * 100)

for asset in sorted(by_asset):
    b = by_asset[asset]
    worst = b["worst"]
    if worst is None:
        print(f"{asset:<15}{b['records']:>9}{b['routable']:>10}{0:>11}"
              f"{'-':>8}{'-':>12}  {'-':<18} -")
        continue
    methods = "+".join(worst.evidence["triggered_methods"])
    triggered = ", ".join(worst.triggered_features) if worst.triggered_features else "-"
    print(f"{asset:<15}{b['records']:>9}{b['routable']:>10}"
          f"{len(b['anomalies']):>11}{worst.anomaly_score:>8.3f}"
          f"{worst.confidence_score:>12.3f}  {methods:<18} {triggered}")

# ── Per-asset evidence detail ─────────────────────────────────────────────
print("\n" + "-" * 100)
print("  PER-ASSET EVIDENCE DETAIL")
print("-" * 100)

for asset in sorted(by_asset):
    b = by_asset[asset]
    worst = b["worst"]
    print(f"\n--- {asset} ---")
    print(f"  Records seen        : {b['records']}  (routable: {b['routable']})")
    print(f"  Anomalies emitted   : {len(b['anomalies'])}")
    if worst is None:
        print(f"  Verdict             : HEALTHY (no anomalies)")
        continue
    ev = worst.evidence
    print(f"  Verdict             : ANOMALY ({worst.case_id})")
    print(f"  Worst anomaly score : {worst.anomaly_score:.4f}")
    print(f"  Confidence          : {worst.confidence_score:.4f}")
    print(f"  Method(s) fired     : {', '.join(ev['triggered_methods'])}")
    print(f"  Regime              : {worst.regime}")
    print(f"  Triggered signals   : {', '.join(worst.triggered_features) or '-'}")
    print(f"  Primary z-score     : {worst.z_score}")
    print(f"  Hotelling T2        : {ev['t2_statistic']} "
          f"(df={ev['t2_degrees_freedom']}, fired={ev['t2_fired']}, "
          f"score={ev['t2_anomaly_score']})")
    ewma = ev.get("ewma", {})
    if ewma.get("fired"):
        print(f"  EWMA (alpha={ewma['alpha']}, L={ewma['control_limit_L']})")
        print(f"    fired: yes, score={ewma['score']}")
        for sig, det in ewma.get("signals", {}).items():
            print(f"      {sig:<14} value={det['value']:<7} "
                  f"ewma={det['ewma']:<8} z={det['z']}")
    else:
        print(f"  EWMA                : not fired")
    sigs = ev.get("signals", {})
    if sigs:
        print(f"  Per-signal z-scores :")
        for sig, det in sigs.items():
            print(f"    {sig:<14} value={det['value']:<7} mean={det['mean']:<7} z={det['z']}")
    print(f"  Source data quality : {ev['data_quality_score']} "
          f"({ev['validation_status']})")
    print(f"  Source scenario(s)  : {', '.join(sorted(b['scenarios']))}")

print()
