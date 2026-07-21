"""
tests/test_predictive_risk.py  —  Phase 5

Tests for the Predictive Risk Agent.

Stage-3 cases run the real pipeline (Data Foundation → Monitoring → Failure
Intelligence → Predictive Risk) so the contracts are exercised end-to-end.
The synthetic telemetry ramps kurtosis up in lockstep with band energy, so it
only ever produces stage-3 diagnoses through the pipeline; the stage-1, band-
label, business-impact, and undetermined cases are therefore unit tests that
construct the agent inputs directly — the correct granularity for those rules.

Design A (taxonomy-driven RUL bands): band labels come from the per-fault
rul_days_stage_N values, so FT_001 stage 1 reads "30–90 days", not a fixed
"21–45 days".

Run with:  python -m unittest tests.test_predictive_risk -v
"""

import sys
import os
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.schemas.anomaly import AnomalyEvent
from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.bearing_signal import (
    BearingSignalFact, AssetContext, TrustedBearingSignal,
)
from src.tools.data_loader import load_fault_taxonomy, load_telemetry_rows
from src.tools.config_loader import load_monitoring_config, load_risk_config
from src.tools.rul_calculator import get_rul_band

_HERE = os.path.dirname(os.path.abspath(__file__))
# Isolated EWMA state so this suite never pollutes the shared data/ewma_state.json.
_TEST_EWMA_STATE = os.path.join(_HERE, "_risk_test_ewma_state.json")


# ── Helpers for unit-level inputs ──────────────────────────────────────

def _asset_ctx(asset_id, criticality, is_bottleneck, downtime_cost):
    """Minimal AssetContext carrying only what the risk agent reads."""
    return AssetContext(
        asset_id=asset_id, asset_name=asset_id, asset_type="motor",
        bearing_type="SKF6310", rated_rpm=1780, power_kw=55.0,
        production_line_id="LINE_001", criticality=criticality,
        is_bottleneck=is_bottleneck, downtime_cost_per_hour=downtime_cost,
        iso_10816_zone="C", status="running",
    )


def _trusted(asset_ctx):
    raw = BearingSignalFact(
        telemetry_id="TEL_UNIT", timestamp_utc="2026-05-20T12:00:00Z",
        asset_id=asset_ctx.asset_id, bearing_id="BRG_TEST", channel_id="CH_TEST",
    )
    return TrustedBearingSignal(raw=raw, asset_ctx=asset_ctx)


def _diagnosis(iso_stage, fault_code, severity, case_id="CASE_UNIT",
               asset_id="AST_TEST", confidence=0.85):
    return FaultDiagnosis(
        case_id=case_id, asset_id=asset_id, bearing_id="BRG_TEST",
        fault_mode="outer_race_fault" if fault_code else "undetermined",
        fault_code=fault_code, iso_stage=iso_stage, severity=severity,
        confidence=confidence,
    )


def _anomaly(score=0.5):
    return AnomalyEvent(case_id="CASE_UNIT", anomaly_score=score,
                        confidence_score=0.9)


class TestPredictiveRiskAgent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(_TEST_EWMA_STATE):
            os.remove(_TEST_EWMA_STATE)
        cls.dfa = DataFoundationAgent.from_data_files()
        mon_cfg = load_monitoring_config()
        mon_cfg.ewma_state_file = _TEST_EWMA_STATE  # isolate from shared state
        cls.mon = MonitoringAgent(mon_cfg)
        cls.fia = FailureIntelligenceAgent.from_data_files()
        cls.pra = PredictiveRiskAgent.from_data_files()
        cls.rules = load_fault_taxonomy()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(_TEST_EWMA_STATE):
            os.remove(_TEST_EWMA_STATE)

    def _assess(self, scenario, index):
        """Run rows [0..index] through the full pipeline; assess row `index`."""
        rows = load_telemetry_rows(scenario)
        event, trusted = None, None
        for i in range(index + 1):
            t = self.dfa.process(deepcopy(rows[i]))
            e = self.mon.process(t)
            if i == index:
                trusted, event = t, e
        self.assertIsNotNone(
            event, f"Monitoring Agent did not fire for {scenario}[{index}]"
        )
        diag = self.fia.process(event, trusted)
        return self.pra.process(diag, event, trusted), diag, event

    # ── End-to-end: stage-3 gearbox bottleneck → CRITICAL ──────────────

    def test_gearbox_stage3_critical(self):
        risk, diag, _ = self._assess("gearbox_fault", 2)   # TEL_0020, AST_GBX_001
        self.assertEqual(diag.iso_stage, 3)
        self.assertEqual(risk.risk_level, "critical")
        self.assertEqual(risk.rul_min_days, 0)
        self.assertEqual(risk.rul_max_days, 7)             # FT_001 rul_days_stage_3
        self.assertEqual(risk.rul_band_label, "0–7 days")
        self.assertTrue(risk.business_impact_flag)
        # 7 days × 24h × ₹18,000/hr = ₹30,24,000
        self.assertEqual(risk.financial_exposure, 7 * 24 * 18000)
        self.assertGreaterEqual(risk.financial_exposure, 3024000)

    # ── End-to-end: stage-3 MTR_001 → exact exposure (no multiplier) ───

    def test_mtr001_stage3_exposure(self):
        risk, diag, _ = self._assess("outer_race_fault", 4)  # TEL_0005, AST_MTR_001
        self.assertEqual(diag.iso_stage, 3)
        # 7 × 24 × 12000 = 2,016,000  (no ×1.5 high-criticality multiplier)
        self.assertEqual(risk.financial_exposure, 7 * 24 * 12000)

    # ── Calibration: CASE_001 stage-3 outer race → RUL ≤ 7 days ────────

    def test_calibration_case001_rul_le_7(self):
        risk, diag, _ = self._assess("outer_race_fault", 4)
        self.assertEqual(diag.fault_code, "FT_001")
        self.assertLessEqual(risk.rul_max_days, 7)

    # ── health_index is the inverse of failure_probability ─────────────

    def test_health_index_is_inverse(self):
        risk, _, _ = self._assess("gearbox_fault", 2)
        self.assertAlmostEqual(
            risk.health_index, round(1.0 - risk.failure_probability, 4), places=4
        )

    # ── Unit: stage-1 medium-criticality, taxonomy-driven band ─────────

    def test_stage1_medium_criticality(self):
        diag = _diagnosis(iso_stage=1, fault_code="FT_001", severity="low",
                          asset_id="AST_MTR_002")
        trusted = _trusted(_asset_ctx("AST_MTR_002", "medium", False, 8500))
        risk = self.pra.process(diag, _anomaly(0.5), trusted)

        self.assertIn(risk.risk_level, ("low", "medium"))
        # Design A: FT_001 stage 1 → (rul_days_stage_2, rul_days_stage_1) = (30, 90)
        self.assertEqual((risk.rul_min_days, risk.rul_max_days), (30, 90))
        self.assertEqual(risk.rul_band_label, "30–90 days")
        self.assertFalse(risk.business_impact_flag)   # medium, not a bottleneck
        # failure_probability = 0.6*0.3 + 0.4*0.5 = 0.38
        self.assertAlmostEqual(risk.failure_probability, 0.38, places=4)

    # ── Unit: business_impact_flag rules ───────────────────────────────

    def test_business_impact_flag(self):
        anom = _anomaly(0.5)
        diag = _diagnosis(1, "FT_001", "low")

        bottleneck = self.pra.process(
            diag, anom, _trusted(_asset_ctx("A", "medium", True, 9000)))
        self.assertTrue(bottleneck.business_impact_flag)   # bottleneck → True

        high_crit = self.pra.process(
            diag, anom, _trusted(_asset_ctx("A", "high", False, 10000)))
        self.assertTrue(high_crit.business_impact_flag)    # high criticality → True

        neither = self.pra.process(
            diag, anom, _trusted(_asset_ctx("A", "medium", False, 7000)))
        self.assertFalse(neither.business_impact_flag)

    # ── Unit: undetermined diagnosis → monitor band, zero exposure ─────

    def test_undetermined_monitor_band(self):
        diag = _diagnosis(iso_stage=0, fault_code="", severity="medium")
        trusted = _trusted(_asset_ctx("A", "high", True, 18000))
        risk = self.pra.process(diag, _anomaly(0.9), trusted)

        self.assertEqual(risk.rul_band_label, "monitor")
        self.assertEqual(risk.financial_exposure, 0.0)   # no quantified timeline
        self.assertEqual((risk.rul_min_days, risk.rul_max_days), (45, 999))

    # ── Unit: missing asset context degrades gracefully ────────────────

    def test_missing_asset_ctx(self):
        diag = _diagnosis(3, "FT_001", "high")
        trusted = TrustedBearingSignal(
            raw=BearingSignalFact(
                telemetry_id="T", timestamp_utc="t", asset_id="A",
                bearing_id="B", channel_id="C"))
        risk = self.pra.process(diag, _anomaly(0.8), trusted)
        self.assertEqual(risk.financial_exposure, 0.0)
        self.assertFalse(risk.business_impact_flag)

    # ── Pure unit: get_rul_band across fault modes ─────────────────────

    def test_get_rul_band_taxonomy_driven(self):
        # FT_001: s1=90, s2=30, s3=7
        self.assertEqual(get_rul_band(3, "FT_001", self.rules), (0, 7, "0–7 days"))
        self.assertEqual(get_rul_band(2, "FT_001", self.rules), (7, 30, "7–30 days"))
        self.assertEqual(get_rul_band(1, "FT_001", self.rules), (30, 90, "30–90 days"))
        # FT_006 cage: s3=4 → band differs from the fixed "0–7"
        self.assertEqual(get_rul_band(3, "FT_006", self.rules), (0, 4, "0–4 days"))
        # undetermined / unknown → monitor band
        self.assertEqual(get_rul_band(0, "", self.rules), (45, 999, "monitor"))
        self.assertEqual(get_rul_band(3, "FT_999", self.rules), (45, 999, "monitor"))


class _FakeLLM:
    """
    Stand-in for LLMClient — returns a canned JSON dict, no network. Lets us
    test the fallback wiring (triggers, merge, risk-level override, graceful
    degradation) without Azure credentials or the `openai` package.
    """
    def __init__(self, response, configured=True):
        self._response   = response
        self._configured = configured
        self.calls       = 0

    def is_configured(self):
        return self._configured

    def complete_json(self, system_prompt, user_prompt,
                      temperature=0.2, max_tokens=500):
        self.calls += 1
        return self._response


class TestPredictiveRiskLLMFallback(unittest.TestCase):
    """LLM advisory fallback — exercised end-to-end with an injected fake client."""

    @classmethod
    def setUpClass(cls):
        cls.rules = load_fault_taxonomy()

    def _agent(self, fake, *, enabled=True, on_undetermined=True,
               low_conf_below=0.0):
        cfg = load_risk_config()
        cfg.llm_enabled                 = enabled
        cfg.llm_trigger_on_undetermined = on_undetermined
        cfg.llm_low_confidence_below    = low_conf_below
        return PredictiveRiskAgent(self.rules, cfg, llm_client=fake)

    # ── Trigger 1: undetermined / monitor band ─────────────────────────

    def test_undetermined_triggers_llm_sets_advisory_and_risk(self):
        fake = _FakeLLM({
            "risk_level": "high",
            "recommended_action": "Schedule inspection within 48h",
            "rationale": "Unmatched anomaly on a critical bottleneck asset.",
        })
        pra  = self._agent(fake, on_undetermined=True)
        diag = _diagnosis(iso_stage=0, fault_code="", severity="medium")
        risk = pra.process(diag, _anomaly(0.9),
                           _trusted(_asset_ctx("A", "high", True, 18000)))

        self.assertEqual(fake.calls, 1)
        self.assertEqual(risk.assessment_source, "rules+llm_fallback")
        self.assertIn("Schedule inspection within 48h", risk.advisory_note)
        self.assertIn("Unmatched anomaly", risk.advisory_note)
        self.assertEqual(risk.risk_level, "high")        # overridden for monitor band
        self.assertEqual(risk.rul_band_label, "monitor") # RUL stays deterministic
        self.assertEqual(risk.financial_exposure, 0.0)

    # ── Trigger 2: low confidence (a fault DID match) ──────────────────

    def test_low_confidence_triggers_llm_but_keeps_rule_risk_level(self):
        fake = _FakeLLM({
            "risk_level": "critical",   # must be IGNORED — rules had a verdict
            "recommended_action": "Verify sensor calibration",
            "rationale": "Borderline signature.",
        })
        pra  = self._agent(fake, low_conf_below=0.5)
        diag = _diagnosis(iso_stage=1, fault_code="FT_001",
                          severity="low", confidence=0.3)
        risk = pra.process(diag, _anomaly(0.5),
                           _trusted(_asset_ctx("AST_MTR_002", "medium", False, 8500)))

        self.assertEqual(fake.calls, 1)
        self.assertEqual(risk.assessment_source, "rules+llm_fallback")
        self.assertIn("Verify sensor calibration", risk.advisory_note)
        # matched fault → rule risk_level preserved, NOT overridden by the LLM
        self.assertEqual(risk.risk_level, "low")

    # ── No trigger: confident, determinate diagnosis ───────────────────

    def test_confident_determinate_does_not_call_llm(self):
        fake = _FakeLLM({"risk_level": "critical", "rationale": "x"})
        pra  = self._agent(fake, low_conf_below=0.5)
        diag = _diagnosis(iso_stage=3, fault_code="FT_001",
                          severity="critical", confidence=0.95)
        risk = pra.process(diag, _anomaly(0.9),
                           _trusted(_asset_ctx("A", "high", True, 18000)))

        self.assertEqual(fake.calls, 0)
        self.assertEqual(risk.assessment_source, "rules")
        self.assertEqual(risk.advisory_note, "")

    # ── Disabled: never consulted even when undetermined ───────────────

    def test_llm_disabled_never_calls(self):
        fake = _FakeLLM({"risk_level": "high"})
        pra  = self._agent(fake, enabled=False, on_undetermined=True)
        diag = _diagnosis(iso_stage=0, fault_code="", severity="medium")
        risk = pra.process(diag, _anomaly(0.9),
                           _trusted(_asset_ctx("A", "high", True, 18000)))

        self.assertEqual(fake.calls, 0)
        self.assertEqual(risk.assessment_source, "rules")

    # ── Graceful degradation: client not configured ────────────────────

    def test_unconfigured_llm_degrades_gracefully(self):
        fake = _FakeLLM({"risk_level": "high"}, configured=False)
        pra  = self._agent(fake, on_undetermined=True)
        diag = _diagnosis(iso_stage=0, fault_code="", severity="medium")
        risk = pra.process(diag, _anomaly(0.9),
                           _trusted(_asset_ctx("A", "high", True, 18000)))

        self.assertEqual(fake.calls, 0)                 # is_configured() gate
        self.assertEqual(risk.assessment_source, "rules")
        self.assertEqual(risk.advisory_note, "")

    # ── Graceful degradation: LLM returns nothing usable ───────────────

    def test_malformed_llm_response_degrades_gracefully(self):
        fake = _FakeLLM(None)   # complete_json returns None
        pra  = self._agent(fake, on_undetermined=True)
        diag = _diagnosis(iso_stage=0, fault_code="", severity="medium")
        risk = pra.process(diag, _anomaly(0.9),
                           _trusted(_asset_ctx("A", "high", True, 18000)))

        self.assertEqual(fake.calls, 1)                 # called, returned None
        self.assertEqual(risk.assessment_source, "rules")
        self.assertEqual(risk.advisory_note, "")

    # ── Partial response: advisory set, no valid risk_level → keep rules ─

    def test_partial_llm_response_keeps_rule_risk_level(self):
        fake = _FakeLLM({"rationale": "Needs review", "recommended_action": "Inspect"})
        pra  = self._agent(fake, on_undetermined=True)
        diag = _diagnosis(iso_stage=0, fault_code="", severity="medium")
        risk = pra.process(diag, _anomaly(0.9),
                           _trusted(_asset_ctx("A", "high", True, 18000)))

        self.assertEqual(risk.assessment_source, "rules+llm_fallback")
        self.assertIn("Inspect", risk.advisory_note)
        self.assertEqual(risk.risk_level, "medium")     # no valid level → keep rules'


if __name__ == "__main__":
    unittest.main()
