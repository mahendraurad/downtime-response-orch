"""
tests/test_failure_intelligence.py  —  Phase 4

End-to-end tests for the Failure Intelligence Agent.  Each case runs the real
pipeline (Data Foundation Agent → Monitoring Agent → Failure Intelligence
Agent) so the contracts are exercised exactly as they are in production.

Run with:  python -m unittest tests.test_failure_intelligence -v
"""

import sys
import os
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.schemas.anomaly import AnomalyEvent
from src.tools.data_loader import load_telemetry_rows, load_fault_taxonomy
from src.tools.config_loader import load_monitoring_config
from src.tools.fault_matcher import determine_iso_stage, match_fault_taxonomy

_HERE = os.path.dirname(os.path.abspath(__file__))
# Isolated EWMA state so this suite never pollutes the shared data/ewma_state.json
# (which test_monitoring relies on being cold-started).
_TEST_EWMA_STATE = os.path.join(_HERE, "_fi_test_ewma_state.json")


class TestFailureIntelligenceAgent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(_TEST_EWMA_STATE):
            os.remove(_TEST_EWMA_STATE)
        cls.dfa = DataFoundationAgent.from_data_files()
        mon_cfg = load_monitoring_config()
        mon_cfg.ewma_state_file = _TEST_EWMA_STATE  # isolate from shared state
        cls.mon = MonitoringAgent(mon_cfg)
        cls.fia = FailureIntelligenceAgent.from_data_files()
        cls.rules = load_fault_taxonomy()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(_TEST_EWMA_STATE):
            os.remove(_TEST_EWMA_STATE)

    def _diagnose(self, scenario, index):
        """Run rows [0..index] through DFA→Monitoring; classify row `index`."""
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
        return self.fia.process(event, trusted), event

    # ── TEL_0005: outer race, stage 3, high confidence ─────────────────

    def test_outer_race_tel_0005(self):
        diag, _ = self._diagnose("outer_race_fault", 4)
        self.assertEqual(diag.fault_mode, "outer_race_fault")
        self.assertEqual(diag.fault_code, "FT_001")
        self.assertEqual(diag.iso_stage, 3)
        self.assertGreater(diag.confidence, 0.85)
        self.assertEqual(diag.bpfo_multiple, 5.9)
        self.assertEqual(diag.kurtosis_at_detection, 7.2)

    # ── TEL_0043: inner race, stage 3, bpfo sub-threshold ──────────────

    def test_inner_race_tel_0043(self):
        diag, _ = self._diagnose("inner_race_fault", 4)
        self.assertEqual(diag.fault_mode, "inner_race_fault")
        self.assertEqual(diag.fault_code, "FT_002")
        self.assertEqual(diag.iso_stage, 3)
        self.assertEqual(diag.bpfi_multiple, 6.2)
        # BPFO present but sub-threshold → should surface as a differential
        diff_codes = [d["fault_code"]
                      for d in diag.evidence["differential_diagnoses"]]
        self.assertIn("FT_001", diff_codes)

    # ── TEL_0013: lubrication issue (broadband, no dominant band) ──────

    def test_lubrication_tel_0013(self):
        diag, _ = self._diagnose("lubrication_issue", 3)
        self.assertEqual(diag.fault_mode, "lubrication_issue")
        self.assertEqual(diag.fault_code, "FT_003")
        self.assertTrue(diag.evidence["broadband_pattern"])
        self.assertGreater(diag.evidence["temp_rise_c"], 0)

    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    # The scenario name identifies a gearbox asset. Its dominant BSF signature
    # identifies a rolling-element bearing fault per the developer roadmap.

    def test_gearbox_tel_0020(self):
        diag, _ = self._diagnose("gearbox_fault", 2)
        self.assertEqual(diag.fault_mode, "rolling_element_fault")
        self.assertEqual(diag.fault_code, "FT_007")
        self.assertEqual(diag.iso_stage, 3)
        self.assertTrue(diag.is_bottleneck)
        # high-criticality bottleneck at stage 3 → escalated to critical
        self.assertEqual(diag.severity, "critical")

    # ── TEL_0006: healthy → Monitoring returns None, FI never called ───

    def test_healthy_tel_0006_not_classified(self):
        row = deepcopy(load_telemetry_rows("healthy")[0])
        event = self.mon.process(self.dfa.process(row))
        self.assertIsNone(event)

    # ── Undetermined path: anomaly with no matching signature ──────────

    def test_undetermined_when_no_rule_matches(self):
        trusted = self.dfa.process(deepcopy(load_telemetry_rows("healthy")[0]))
        fake_anomaly = AnomalyEvent(
            case_id="ANOM-TEST", asset_id=trusted.raw.asset_id,
            bearing_id=trusted.raw.bearing_id, anomaly_score=0.9,
            confidence_score=0.9,
        )
        diag = self.fia.process(fake_anomaly, trusted)
        self.assertEqual(diag.fault_mode, "undetermined")
        self.assertEqual(diag.iso_stage, 0)
        self.assertTrue(diag.recommended_checks)

    # ── Pure matcher unit checks ───────────────────────────────────────

    def test_iso_stage_boundary_is_inclusive(self):
        rule = next(r for r in self.rules if r["fault_code"] == "FT_001")
        self.assertEqual(determine_iso_stage(3.5, rule), 3)   # == stage_3
        self.assertEqual(determine_iso_stage(2.5, rule), 2)   # == stage_2
        self.assertEqual(determine_iso_stage(1.5, rule), 1)   # == stage_1
        self.assertEqual(determine_iso_stage(1.4, rule), 0)   # below stage_1

    def test_match_priority_kurtosis_gate(self):
        # BPFO crosses a stage but kurtosis below 5.0 → no outer-race match
        result = match_fault_taxonomy(
            bpfo_energy=2.0, bpfi_energy=0.5, bsf_energy=0.2,
            kurtosis=3.0, temp_rise=2.0, broadband_pattern=False,
            taxonomy_rules=self.rules,
        )
        self.assertIsNone(result)

    # ── Full sweep: every fault scenario classifies coherently ─────────

    def test_all_fault_scenarios_classify(self):
        expected = {
            "outer_race_fault":  "outer_race_fault",
            "inner_race_fault":  "inner_race_fault",
            "lubrication_issue": "lubrication_issue",
            "gearbox_fault":     "rolling_element_fault",
        }
        for scenario, fault_mode in expected.items():
            rows = load_telemetry_rows(scenario)
            diag, _ = self._diagnose(scenario, len(rows) - 1)
            self.assertEqual(
                diag.fault_mode, fault_mode,
                f"{scenario} misclassified as {diag.fault_mode}",
            )
            self.assertIn(diag.iso_stage, (1, 2, 3))
            self.assertIn(diag.severity, ("low", "medium", "high", "critical"))
    # ***********************


if __name__ == "__main__":
    unittest.main()
