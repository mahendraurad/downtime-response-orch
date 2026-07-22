"""
Quick comparison: same record, different number of nulls in critical fields.
Confirms that data_quality_score drops by the expected amount per null.
"""
import sys, os, json
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.tools.data_loader import load_telemetry_rows

agent = DataFoundationAgent.from_data_files()

# Start from a known-good healthy record and inject nulls into critical fields.
base = deepcopy(load_telemetry_rows("healthy")[0])

cases = {
    "0 nulls (baseline)":   [],
    "1 null  (vib only)":   ["vib_rms_mm_s"],
    "2 nulls (vib + kurt)": ["vib_rms_mm_s", "kurtosis"],
    "3 nulls (vib+kurt+temp)": ["vib_rms_mm_s", "kurtosis", "temp_c"],
    "5 nulls (all critical)": ["vib_rms_mm_s", "kurtosis", "temp_c", "bpfo_energy", "bpfi_energy"],
}

print(f"\n{'Case':<28} {'Score':>8}  {'Status':<10}  Missing")
print("-" * 90)

prev = None
for label, fields_to_null in cases.items():
    record = deepcopy(base)
    record["telemetry_id"] = f"TEST_{label.split()[0]}_NULLS"
    for f in fields_to_null:
        record[f] = None

    trusted = agent.process(record)
    score = trusted.data_quality_score
    delta = "" if prev is None else f"  (delta {score - prev:+.4f})"
    missing = [r for r in trusted.validation.reasons if "Missing critical" in r]
    missing_str = missing[0].replace("Missing critical fields: ", "") if missing else "—"
    print(f"{label:<28} {score:>8.4f}  {trusted.validation_status.value:<10}  {missing_str}{delta}")
    prev = score

print("\nExpected per-null drop in composite: 0.20 / 5 = 0.04")
