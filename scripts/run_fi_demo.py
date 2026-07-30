"""
scripts/run_fi_demo.py

Run the Failure Intelligence Agent end-to-end (Data Foundation → Monitoring →
Failure Intelligence) over the fault scenarios and print each FaultDiagnosis.

Usage:
    python scripts/run_fi_demo.py                 # all default scenarios
    python scripts/run_fi_demo.py gearbox_fault   # one scenario (last row)

EWMA state is pointed at a throwaway temp file so this never touches
data/ewma_state.json.
"""
import os
import sys
import json
import tempfile
from copy import deepcopy

# Make the repo root importable when run as `python scripts/run_fi_demo.py`
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Print the narrative's ×, °C, — glyphs correctly on Windows consoles (cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.tools.data_loader import load_telemetry_rows
from src.tools.config_loader import load_monitoring_config

# scenario -> row index to classify (the last row of each fault progression)
DEFAULT_SCENARIOS = {
    "gearbox_fault":     2,
    "outer_race_fault":  4,
    "inner_race_fault":  4,
    "lubrication_issue": 3,
}


def build_agents():
    tmp = os.path.join(tempfile.gettempdir(), "_fi_demo_ewma.json")
    if os.path.exists(tmp):
        os.remove(tmp)
    dfa = DataFoundationAgent.from_data_files()
    mon_cfg = load_monitoring_config()
    mon_cfg.ewma_state_file = tmp          # isolate from the shared state file
    mon = MonitoringAgent(mon_cfg)
    fia = FailureIntelligenceAgent.from_data_files()
    return dfa, mon, fia


def diagnose(dfa, mon, fia, scenario, index):
    """Warm rows [0..index] through DFA→Monitoring; classify row `index`."""
    rows = load_telemetry_rows(scenario)
    event = trusted = None
    for i in range(index + 1):
        t = dfa.process(deepcopy(rows[i]))
        e = mon.process(t)
        if i == index:
            trusted, event = t, e
    if event is None:
        return None, None
    return fia.process(event, trusted), event


def show(scenario, index, diag, event):
    print("=" * 78)
    print(f"SCENARIO: {scenario}[{index}]   case={diag.case_id}")
    print("=" * 78)
    if diag is None:
        print("  Monitoring Agent returned None (healthy / suppressed) — FI not called.\n")
        return
    print(f"  fault_mode        : {diag.fault_mode}  ({diag.fault_code})")
    print(f"  iso_stage         : {diag.iso_stage}")
    print(f"  severity          : {diag.severity}")
    print(f"  confidence        : {diag.confidence}")
    print(f"  bpfo / bpfi       : {diag.bpfo_multiple} / {diag.bpfi_multiple}")
    print(f"  kurtosis          : {diag.kurtosis_at_detection}")
    print(f"  rul_days_estimate : {diag.rul_days_estimate}")
    print(f"  is_bottleneck     : {diag.is_bottleneck}")
    print(f"  anomaly_score     : {event.anomaly_score}")
    print(f"  narrative         : {diag.narrative}")
    print("  recommended_checks:")
    for c in diag.recommended_checks:
        print(f"      - {c}")
    diffs = diag.evidence.get("differential_diagnoses", [])
    if diffs:
        print("  differentials     :")
        for d in diffs:
            print(f"      - {d['fault_code']} ({d['fault_mode']}): {d['reason']}")
    print()


def main(argv):
    dfa, mon, fia = build_agents()
    if len(argv) > 1:
        scenarios = {argv[1]: int(argv[2]) if len(argv) > 2 else -1}
    else:
        scenarios = DEFAULT_SCENARIOS
    for scenario, index in scenarios.items():
        if index < 0:  # -1 → last row of the scenario
            index = len(load_telemetry_rows(scenario)) - 1
        diag, event = diagnose(dfa, mon, fia, scenario, index)
        show(scenario, index, diag, event)


if __name__ == "__main__":
    main(sys.argv)
