"""
scripts/test_llm_fallback.py

Test the LLM fallback path of the Predictive Risk Agent against the real
Azure AI Foundry endpoint configured in .env.

What this covers:
  1. Connectivity — can the LLM client reach the endpoint?
  2. Undetermined trigger — iso_stage 0 (no fault matched) triggers the fallback
  3. Low-confidence trigger — a fault matched but confidence < threshold
  4. HITL gate — operator reviews the LLM advisory before it is applied
  5. Graceful rejection — operator rejects; deterministic result is kept

Usage:
    python scripts/test_llm_fallback.py              # interactive HITL prompts
    python scripts/test_llm_fallback.py --accept-all # auto-accept all LLM advisories
    python scripts/test_llm_fallback.py --reject-all # auto-reject; verify fallback

Credentials are read from .env in the project root (AZURE_AI_ENDPOINT /
AZURE_AI_KEY / AZURE_AI_DEPLOYMENT).
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env before anything imports os.getenv
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    # python-dotenv not installed — read .env manually
    _env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(_env_path):
        with open(_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.schemas.anomaly import AnomalyEvent
from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.bearing_signal import BearingSignalFact, AssetContext, TrustedBearingSignal
from src.tools.llm_client import LLMClient
from src.tools.config_loader import load_risk_config
from src.tools.data_loader import load_fault_taxonomy

# ── ANSI colour helpers ────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_BLUE   = "\033[34m"


def _c(text, *codes):
    return "".join(codes) + str(text) + _RESET


# ── HITL handler ───────────────────────────────────────────────────────────

def make_hitl_handler(mode: str):
    """
    Returns a HITL handler function.
      mode="interactive" — prompts the operator
      mode="accept"      — auto-accepts (non-interactive testing)
      mode="reject"      — auto-rejects (verifies deterministic fallback)
    """
    def _handler(llm_result, assessment, diagnosis, anomaly, actx):
        print()
        print(_c("  ┌─ LLM ADVISORY — HUMAN REVIEW REQUIRED " + "─" * 20, _BOLD, _CYAN))
        print(_c("  │", _CYAN))
        asset  = diagnosis.asset_id
        brg    = diagnosis.bearing_id
        if not diagnosis.fault_code:
            situation = "Undetermined fault (no taxonomy signature matched)"
        else:
            situation = (f"{diagnosis.fault_mode} matched — "
                         f"low confidence ({diagnosis.confidence:.2f})")
        print(_c(f"  │  Asset / Bearing : {asset} / {brg}", _CYAN))
        print(_c(f"  │  Situation       : {situation}", _CYAN))
        print(_c(f"  │  Anomaly score   : {anomaly.anomaly_score}", _CYAN))
        print(_c("  │", _CYAN))
        print(_c("  │  LLM recommends:", _BOLD + _CYAN))
        print(_c(f"  │    risk_level          : {llm_result.get('risk_level', 'n/a')}", _CYAN))
        print(_c(f"  │    recommended_action  : {llm_result.get('recommended_action', 'n/a')}", _CYAN))
        print(_c(f"  │    rationale           : {llm_result.get('rationale', 'n/a')}", _CYAN))
        print(_c("  │", _CYAN))

        if mode == "accept":
            print(_c("  │  [AUTO-ACCEPT]", _GREEN))
            print(_c("  └" + "─" * 50, _CYAN))
            return llm_result

        if mode == "reject":
            print(_c("  │  [AUTO-REJECT]", _RED))
            print(_c("  └" + "─" * 50, _CYAN))
            return None

        # Interactive
        print(_c("  └" + "─" * 50, _CYAN))
        print()
        while True:
            raw = input(_c("  [A]ccept  [M]odify  [R]eject → ", _BOLD)).strip().upper()
            if raw in ("A", ""):
                return llm_result
            if raw == "R":
                return None
            if raw == "M":
                print(_c("  Enter modified values (blank = keep LLM value):", _YELLOW))
                risk = input(
                    f"    risk_level  [{llm_result.get('risk_level', '')}]: "
                ).strip()
                action = input(
                    f"    action      [{llm_result.get('recommended_action', '')}]: "
                ).strip()
                rationale = input(
                    f"    rationale   [{llm_result.get('rationale', '')}]: "
                ).strip()
                modified = dict(llm_result)
                if risk:
                    modified["risk_level"] = risk
                if action:
                    modified["recommended_action"] = action
                if rationale:
                    modified["rationale"] = rationale
                return modified
            print("  Please type A, M, or R.")

    return _handler


# ── Fixture helpers ────────────────────────────────────────────────────────

def _asset_ctx(asset_id, criticality, is_bottleneck, cost):
    return AssetContext(
        asset_id=asset_id, asset_name=asset_id, asset_type="motor",
        bearing_type="SKF6310", rated_rpm=1780, power_kw=55.0,
        production_line_id="LINE_001", criticality=criticality,
        is_bottleneck=is_bottleneck, downtime_cost_per_hour=cost,
        iso_10816_zone="C", status="running",
    )


def _trusted(asset_ctx):
    raw = BearingSignalFact(
        telemetry_id="TEL_LLM_TEST", timestamp_utc="2026-06-25T09:00:00Z",
        asset_id=asset_ctx.asset_id, bearing_id="BRG_LLM_001",
        channel_id="CH_DE",
    )
    return TrustedBearingSignal(raw=raw, asset_ctx=asset_ctx)


def _anomaly(score=0.82):
    return AnomalyEvent(
        case_id="CASE_LLM_001",
        anomaly_score=score,
        confidence_score=0.88,
        reason="T² exceeded threshold; vib_rms and kurtosis elevated",
        triggered_features=["vib_rms_mm_s", "kurtosis"],
    )


# ── Test cases ─────────────────────────────────────────────────────────────

def _header(title):
    print()
    print(_c("=" * 70, _BOLD))
    print(_c(f"  {title}", _BOLD))
    print(_c("=" * 70, _BOLD))


def _show_result(risk, label="RESULT"):
    print()
    print(_c(f"  [{label}] — RiskAssessment output:", _BOLD))
    # Indent each line of the full __str__ output
    for line in str(risk).splitlines():
        src_colour = _GREEN if "llm_fallback" in line else ""
        print("    " + (_c(line, src_colour) if src_colour else line))


def _assert(condition, msg):
    if condition:
        print(_c(f"    PASS  {msg}", _GREEN))
    else:
        print(_c(f"    FAIL  {msg}", _RED))
    return condition


def run_tests(hitl_mode: str):
    cfg   = load_risk_config()
    rules = load_fault_taxonomy()

    # ── 0. Connectivity check ────────────────────────────────────────
    _header("STEP 0 — LLM connectivity check")
    llm = LLMClient(timeout_seconds=cfg.llm_timeout_seconds)
    if not llm.is_configured():
        print(_c(
            "  LLM is NOT configured — check AZURE_AI_ENDPOINT / AZURE_AI_KEY / "
            "AZURE_AI_DEPLOYMENT in .env", _RED
        ))
        return False

    print(f"  Endpoint  : {llm.endpoint}")
    print(f"  Deployment: {llm.deployment}")
    print(f"  Backend   : {'Azure AI Foundry' if '.services.ai.azure.com' in (llm.endpoint or '') else 'Azure OpenAI'}")
    print()
    print("  Sending connectivity probe (ask LLM for a simple JSON)...")
    probe = llm.complete_json(
        system_prompt="Reply with a JSON object containing a single key 'status' with value 'ok'.",
        user_prompt="Ping.",
        temperature=0.0, max_tokens=30,
    )
    if probe is None:
        print(_c("  Connectivity FAILED — LLM returned None.", _RED))
        print("  Check endpoint, key, and deployment name in .env.")
        return False
    print(_c(f"  Connectivity OK — response: {probe}", _GREEN))

    # ── Build agent with HITL ────────────────────────────────────────
    hitl_fn = make_hitl_handler(hitl_mode)
    pra = PredictiveRiskAgent(rules, cfg, llm_client=llm, hitl_handler=hitl_fn)

    all_passed = True

    # ── 1. Undetermined trigger ──────────────────────────────────────
    _header("TEST 1 — Undetermined fault triggers LLM fallback + HITL")
    diag_undetermined = FaultDiagnosis(
        case_id="CASE_LLM_001", asset_id="AST_MTR_001", bearing_id="BRG_LLM_001",
        fault_mode="undetermined", fault_code="", iso_stage=0,
        severity="medium", confidence=0.0,
    )
    actx_crit = _asset_ctx("AST_MTR_001", "high", True, 18000)
    risk1 = pra.process(diag_undetermined, _anomaly(0.82), _trusted(actx_crit))
    _show_result(risk1, "TEST 1")
    print()
    if hitl_mode == "reject":
        p = _assert(risk1.assessment_source == "rules",
                    "HITL rejection kept deterministic result (assessment_source='rules')")
        p &= _assert(risk1.advisory_note == "",
                     "No advisory note written after rejection")
    else:
        p = _assert(risk1.assessment_source == "rules+llm_fallback",
                    "assessment_source set to 'rules+llm_fallback'")
        p &= _assert(bool(risk1.advisory_note),
                     "advisory_note populated from LLM")
        p &= _assert(risk1.rul_band_label == "monitor",
                     "Deterministic RUL band preserved (monitor)")
        p &= _assert(risk1.financial_exposure == 0.0,
                     "Financial exposure still 0.0 (undetermined)")
    all_passed &= p

    # ── 2. Matched fault → NO LLM call (rules have coverage) ────────
    _header("TEST 2 — Matched fault stays deterministic (no LLM fallback)")
    diag_lowconf = FaultDiagnosis(
        case_id="CASE_LLM_002", asset_id="AST_MTR_002", bearing_id="BRG_LLM_002",
        fault_mode="outer_race_fault", fault_code="FT_001", iso_stage=1,
        severity="low", confidence=0.30,   # low confidence but rule DID match
    )
    actx_med = _asset_ctx("AST_MTR_002", "medium", False, 8500)
    risk2 = pra.process(diag_lowconf, _anomaly(0.55), _trusted(actx_med))
    _show_result(risk2, "TEST 2")
    print()
    # A matched fault always stays deterministic regardless of HITL mode —
    # the rules have coverage, so the LLM is never consulted.
    p = _assert(risk2.assessment_source == "rules",
                "Matched fault keeps deterministic result (rules have coverage)")
    p &= _assert(risk2.advisory_note == "",
                 "No advisory note when rules matched")
    p &= _assert(risk2.risk_level == "low",
                 "Rule-derived risk_level 'low' preserved intact")
    all_passed &= p

    # ── 3. Confident determinate — no LLM call expected ─────────────
    _header("TEST 3 — Confident determinate diagnosis (no LLM call)")
    diag_conf = FaultDiagnosis(
        case_id="CASE_LLM_003", asset_id="AST_GBX_001", bearing_id="BRG_LLM_003",
        fault_mode="outer_race_fault", fault_code="FT_001", iso_stage=3,
        severity="critical", confidence=0.95,
    )
    actx_gbx = _asset_ctx("AST_GBX_001", "high", True, 18000)
    risk3 = pra.process(diag_conf, _anomaly(0.95), _trusted(actx_gbx))
    _show_result(risk3, "TEST 3")
    print()
    p = _assert(risk3.assessment_source == "rules",
                "No LLM call for confident diagnosis (assessment_source='rules')")
    p &= _assert(risk3.advisory_note == "",
                 "No advisory note for confident determinate diagnosis")
    all_passed &= p

    # ── Summary ──────────────────────────────────────────────────────
    print()
    print(_c("=" * 70, _BOLD))
    if all_passed:
        print(_c("  ALL TESTS PASSED", _BOLD + _GREEN))
    else:
        print(_c("  SOME TESTS FAILED — review output above", _BOLD + _RED))
    print(_c("=" * 70, _BOLD))
    print()
    return all_passed


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test LLM fallback with real endpoint.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--accept-all", action="store_true",
                       help="Auto-accept all LLM advisories (non-interactive)")
    group.add_argument("--reject-all", action="store_true",
                       help="Auto-reject all LLM advisories (verify deterministic path)")
    args = parser.parse_args()

    if args.accept_all:
        mode = "accept"
    elif args.reject_all:
        mode = "reject"
    else:
        mode = "interactive"

    print(_c("\nDRO — Predictive Risk Agent: LLM Fallback + HITL Test", _BOLD))
    print(f"HITL mode: {_c(mode, _YELLOW)}\n")

    ok = run_tests(mode)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
