"""
scripts/run_risk_demo.py

Run the Predictive Risk Agent end-to-end (Data Foundation → Monitoring →
Failure Intelligence → Predictive Risk) over the fault scenarios and print
each RiskAssessment.

Usage:
    python scripts/run_risk_demo.py                       # all default scenarios
    python scripts/run_risk_demo.py gearbox_fault         # one scenario (last row)
    python scripts/run_risk_demo.py outer_race_fault 4
    python scripts/run_risk_demo.py --hitl                # enable interactive HITL review

When --hitl is passed, LLM fallback is activated and any advisory generated for
an undetermined or low-confidence diagnosis is shown to the operator before being
applied. Credentials are read from .env (AZURE_AI_ENDPOINT / AZURE_AI_KEY /
AZURE_AI_DEPLOYMENT).

EWMA state is pointed at a throwaway temp file so this never touches
data/ewma_state.json.
"""
import argparse
import os
import sys
import tempfile
from copy import deepcopy

# Make the repo root importable when run as `python scripts/run_risk_demo.py`
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Print the ×, °C, – glyphs correctly on Windows consoles (cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.tools.data_loader import load_telemetry_rows
from src.tools.config_loader import load_monitoring_config, load_risk_config

# scenario -> row index to assess (the last row of each fault progression)
DEFAULT_SCENARIOS = {
    "gearbox_fault":     2,
    "outer_race_fault":  4,
    "inner_race_fault":  4,
    "lubrication_issue": 3,
}

# ── HITL handler ──────────────────────────────────────────────────────────


def _interactive_hitl(llm_result, assessment, diagnosis, anomaly, actx):
    """Prompt the operator to Accept / Modify / Reject an LLM advisory."""
    print()
    print("  ┌─ LLM ADVISORY — HUMAN REVIEW REQUIRED " + "─" * 18)
    if not diagnosis.fault_code:
        situation = "Undetermined fault (no taxonomy signature matched)"
    else:
        situation = (f"{diagnosis.fault_mode} — "
                     f"low confidence ({diagnosis.confidence:.2f})")
    print(f"  │  Asset / Bearing : {diagnosis.asset_id} / {diagnosis.bearing_id}")
    print(f"  │  Situation       : {situation}")
    print(f"  │  Anomaly score   : {anomaly.anomaly_score}")
    print("  │")
    print("  │  LLM recommends:")
    print(f"  │    risk_level         : {llm_result.get('risk_level', 'n/a')}")
    print(f"  │    recommended_action : {llm_result.get('recommended_action', 'n/a')}")
    print(f"  │    rationale          : {llm_result.get('rationale', 'n/a')}")
    print("  └" + "─" * 58)
    print()
    while True:
        raw = input("  [A]ccept  [M]odify  [R]eject → ").strip().upper()
        if raw in ("A", ""):
            return llm_result
        if raw == "R":
            return None
        if raw == "M":
            print("  Enter modified values (blank = keep LLM value):")
            risk   = input(f"    risk_level  [{llm_result.get('risk_level', '')}]: ").strip()
            action = input(f"    action      [{llm_result.get('recommended_action', '')}]: ").strip()
            rat    = input(f"    rationale   [{llm_result.get('rationale', '')}]: ").strip()
            mod = dict(llm_result)
            if risk:   mod["risk_level"]          = risk
            if action: mod["recommended_action"]  = action
            if rat:    mod["rationale"]            = rat
            return mod
        print("  Please type A, M, or R.")


def _load_env():
    """Load .env from the project root into os.environ."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def build_agents(hitl: bool = False):
    tmp = os.path.join(tempfile.gettempdir(), "_risk_demo_ewma.json")
    if os.path.exists(tmp):
        os.remove(tmp)
    dfa = DataFoundationAgent.from_data_files()
    mon_cfg = load_monitoring_config()
    mon_cfg.ewma_state_file = tmp          # isolate from the shared state file
    mon = MonitoringAgent(mon_cfg)
    fia = FailureIntelligenceAgent.from_data_files()

    if hitl:
        _load_env()
        risk_cfg = load_risk_config()
        if not risk_cfg.llm_enabled:
            print("  [HITL] llm_fallback.enabled is false in risk_config.json — "
                  "HITL has no effect without LLM.")
        pra = PredictiveRiskAgent.from_data_files(),
        # Rebuild with HITL handler injected
        from src.tools.data_loader import load_fault_taxonomy
        from src.tools.llm_client import LLMClient
        llm = LLMClient(timeout_seconds=risk_cfg.llm_timeout_seconds)
        if not llm.is_configured():
            print("  [HITL] LLM credentials not found in .env — "
                  "running without LLM fallback.")
            hitl_handler = None
        else:
            print(f"  [HITL] LLM endpoint: {llm.endpoint}")
            hitl_handler = _interactive_hitl
        pra = PredictiveRiskAgent(
            load_fault_taxonomy(), risk_cfg,
            llm_client=llm, hitl_handler=hitl_handler,
        )
    else:
        pra = PredictiveRiskAgent.from_data_files()

    return dfa, mon, fia, pra


def assess(dfa, mon, fia, pra, scenario, index):
    """Warm rows [0..index] through DFA→Monitoring→FI; assess row `index`."""
    rows = load_telemetry_rows(scenario)
    event = trusted = None
    for i in range(index + 1):
        t = dfa.process(deepcopy(rows[i]))
        e = mon.process(t)
        if i == index:
            trusted, event = t, e
    if event is None:
        return None, None, None
    diag = fia.process(event, trusted)
    risk = pra.process(diag, event, trusted)
    return risk, diag, event


def show(scenario, index, risk, diag, event):
    print("=" * 78)
    print(f"SCENARIO: {scenario}[{index}]")
    print(f"diagnosis : {diag.fault_mode} ({diag.fault_code or 'none'}) "
          f"stage {diag.iso_stage}  anomaly_score={event.anomaly_score}")
    print("=" * 78)
    if risk is None:
        print("  Monitoring Agent returned None (healthy / suppressed) — no risk assessment.\n")
        return
    print(risk)   # RiskAssessment.__str__ — all fields, no omissions
    print()


def main(argv):
    parser = argparse.ArgumentParser(description="Predictive Risk Agent demo.")
    parser.add_argument("scenario", nargs="?", help="Scenario name (default: all)")
    parser.add_argument("index", nargs="?", type=int, default=-1,
                        help="Row index within scenario (-1 = last)")
    parser.add_argument("--hitl", action="store_true",
                        help="Enable interactive LLM fallback + HITL review")
    args = parser.parse_args(argv[1:])

    dfa, mon, fia, pra = build_agents(hitl=args.hitl)

    if args.scenario:
        scenarios = {args.scenario: args.index}
    else:
        scenarios = DEFAULT_SCENARIOS

    for scenario, index in scenarios.items():
        if index < 0:
            index = len(load_telemetry_rows(scenario)) - 1
        risk, diag, event = assess(dfa, mon, fia, pra, scenario, index)
        show(scenario, index, risk, diag, event)


if __name__ == "__main__":
    main(sys.argv)
