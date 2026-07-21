"""
tests/test_monitoring.py  —  Phase 3 (Hotelling T²)

Tests for the Monitoring Agent using Hotelling T² anomaly detection.

T² is more sensitive than the original per-signal z-score composite.
It catches early-stage faults that individually look borderline — three
records that previously returned None now correctly fire anomalies:
  TEL_0001 (outer race first reading):  T²=17.7 → score 0.9986
  TEL_0039 (inner race first reading):  T²=7.5  → score 0.8863
  TEL_0011 (lubrication early reading): T²=16.8 → score 0.9979

Zero false positives on healthy readings.

Run with:  python -m unittest tests.test_monitoring -v
"""

import sys
import os
import json
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.schemas.anomaly import AnomalyEvent
from src.tools.baseline_features import (
    build_correlation_matrix, build_covariance_matrix,
    compute_hotelling_t2, t2_anomaly_score, chi2_cdf,
)
from src.tools.data_loader import load_telemetry_rows
from src.tools.config_loader import load_monitoring_config
from src.tools.remediation import remediate, IMPUTE

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
# Isolated EWMA state so this suite cold-starts and never reads/writes the
# shared data/ewma_state.json (which a demo run would otherwise pollute).
_TEST_EWMA_STATE = os.path.join(_HERE, "_monitoring_test_ewma_state.json")


def _row(scenario, index=0):
    return deepcopy(load_telemetry_rows(scenario)[index])


class TestMonitoringAgent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.path.exists(_TEST_EWMA_STATE):
            os.remove(_TEST_EWMA_STATE)
        cls.dfa = DataFoundationAgent.from_data_files()
        mon_cfg = load_monitoring_config()
        mon_cfg.ewma_state_file = _TEST_EWMA_STATE   # isolate from shared state
        cls.mon = MonitoringAgent(mon_cfg)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(_TEST_EWMA_STATE):
            os.remove(_TEST_EWMA_STATE)

    def _assess(self, scenario, index=0):
        return self.mon.process(self.dfa.process(_row(scenario, index)))

    # ── Healthy: no anomaly ───────────────────────────────────────────

    def test_healthy_returns_none(self):
        self.assertIsNone(self._assess("healthy", 0))

    def test_no_false_positives_on_any_healthy_row(self):
        for i in range(len(load_telemetry_rows("healthy"))):
            self.assertIsNone(self._assess("healthy", i),
                              f"healthy row {i} wrongly flagged")

    # ── Outer race ────────────────────────────────────────────────────

    def test_outer_race_worst_is_anomaly(self):
        ev = self._assess("outer_race_fault", -1)    # TEL_0005
        self.assertIsNotNone(ev)
        self.assertGreater(ev.anomaly_score, 0.85)

    def test_outer_race_first_is_anomaly_with_t2(self):
        """T² catches TEL_0001 (early stage) — individual z-scores missed this."""
        ev = self._assess("outer_race_fault", 0)    # TEL_0001
        self.assertIsNotNone(ev)
        self.assertGreater(ev.anomaly_score, 0.85)

    def test_outer_race_evidence(self):
        ev = self._assess("outer_race_fault", -1)
        self.assertEqual(ev.bearing_id, "BRG_001")
        self.assertIn("t2_statistic", ev.evidence)
        self.assertIn("t2_degrees_freedom", ev.evidence)
        self.assertEqual(ev.evidence["method"], "hotelling_t2")
        self.assertGreater(ev.evidence["t2_statistic"], 0)
        self.assertGreater(ev.z_score, 0)
        self.assertIn("signals", ev.evidence)

    # ── Inner race ────────────────────────────────────────────────────

    def test_inner_race_early_detection(self):
        """T² catches TEL_0039 (first inner-race reading) — z-score didn't."""
        ev = self._assess("inner_race_fault", 0)    # TEL_0039
        self.assertIsNotNone(ev)
        self.assertGreater(ev.anomaly_score, 0.85)

    def test_inner_race_worst_is_anomaly(self):
        ev = self._assess("inner_race_fault", -1)   # TEL_0043
        self.assertIsNotNone(ev)
        self.assertGreater(ev.anomaly_score, 0.85)

    # ── Lubrication ───────────────────────────────────────────────────

    def test_lubrication_earliest_healthy(self):
        """TEL_0010 (genuinely healthy first reading) must still return None."""
        self.assertIsNone(self._assess("lubrication_issue", 0))

    def test_lubrication_fault_detected(self):
        ev = self._assess("lubrication_issue", -1)  # TEL_0013
        self.assertIsNotNone(ev)
        self.assertGreater(ev.anomaly_score, 0.85)

    # ── Gearbox ───────────────────────────────────────────────────────

    def test_gearbox_all_anomalous(self):
        for i in range(len(load_telemetry_rows("gearbox_fault"))):
            ev = self._assess("gearbox_fault", i)
            self.assertIsNotNone(ev, f"gearbox row {i} missed")
            self.assertGreater(ev.anomaly_score, 0.85)

    # ── Suppressions ──────────────────────────────────────────────────

    def test_startup_is_suppressed(self):
        self.assertIsNone(self._assess("startup_filter", 0))

    def test_idle_machine_is_suppressed(self):
        row = _row("healthy", 0)
        row["machine_state"] = "idle"
        row["rpm"] = 0
        self.assertIsNone(self.mon.process(self.dfa.process(row)))

    # ── Imputed fields excluded from T² ───────────────────────────────

    def test_imputed_field_excluded_from_scoring(self):
        trusted = self.dfa.process(_row("outer_race_fault", -1))
        trusted.imputed_fields = ["temp_c"]
        ev = self.mon.process(trusted)
        self.assertIsNotNone(ev)
        self.assertIn("temp_c", ev.evidence["excluded_imputed"])
        self.assertNotIn("temp_c", ev.evidence["signals"])

    def test_fully_imputed_record_raises_no_anomaly(self):
        """Imputed values are excluded — no real signal left to score → None."""
        trusted = self.dfa.process(_row("signal_dropout", 1))
        fixed   = remediate(trusted, IMPUTE, self.dfa, self.dfa._cfg)
        self.assertIsNone(self.mon.process(fixed))

    # ── Reduced T² for missing signals ───────────────────────────────

    def test_reduced_t2_when_signal_missing(self):
        """Removing one signal drops to p=3 — agent still produces a verdict."""
        row = _row("outer_race_fault", -1)
        row["bpfo_energy"] = None              # remove one signal
        trusted = self.dfa.process(row)
        ev = self.mon.process(trusted)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.evidence["t2_degrees_freedom"], 3)

    # ── Event structure & serialisation ───────────────────────────────

    def test_event_confidence_in_range(self):
        ev = self._assess("outer_race_fault", -1)
        self.assertGreaterEqual(ev.confidence_score, 0.0)
        self.assertLessEqual(ev.confidence_score, 1.0)

    def test_event_is_serialisable(self):
        ev = self._assess("outer_race_fault", -1)
        json.dumps(ev.to_dict())

    def test_regime_label_populated(self):
        ev = self._assess("outer_race_fault", -1)
        self.assertTrue(ev.regime)

    # ── Batch ─────────────────────────────────────────────────────────

    def test_batch_returns_only_anomaly_events(self):
        trusted = [self.dfa.process(r) for r in load_telemetry_rows("outer_race_fault")]
        events  = self.mon.process_batch(trusted)
        self.assertTrue(all(isinstance(e, AnomalyEvent) for e in events))
        self.assertGreater(len(events), 0)

    def test_batch_healthy_produces_no_events(self):
        trusted = [self.dfa.process(r) for r in load_telemetry_rows("healthy")]
        self.assertEqual(self.mon.process_batch(trusted), [])


class TestHotellingT2Math(unittest.TestCase):
    """Unit tests for the T² math functions, independent of the agent."""

    def test_chi2_cdf_known_percentiles(self):
        """Validate against known chi-square percentiles (p=4)."""
        # 90th percentile of chi2(4) = 7.779
        self.assertAlmostEqual(chi2_cdf(7.779, 4), 0.90, places=2)
        # 99th percentile of chi2(4) = 13.277
        self.assertAlmostEqual(chi2_cdf(13.277, 4), 0.99, places=2)

    def test_chi2_cdf_zero_returns_zero(self):
        self.assertEqual(chi2_cdf(0, 4), 0.0)

    def test_chi2_cdf_all_p_values(self):
        """chi2_CDF must be monotonically increasing for all implemented p."""
        for p in [1, 2, 3, 4]:
            prev = 0.0
            for x in [0.5, 1.0, 2.0, 5.0, 10.0]:
                cur = chi2_cdf(x, p)
                self.assertGreater(cur, prev, f"non-monotone at p={p}, x={x}")
                prev = cur

    def test_t2_healthy_below_threshold(self):
        """A near-zero deviation vector must score well below 0.85."""
        # Diagonal identity covariance (unit variances)
        cov_inv = np.eye(4)
        x  = np.array([0.1, -0.1, 0.2, 0.0])
        mu = np.zeros(4)
        t2    = compute_hotelling_t2(x, mu, cov_inv)
        score = t2_anomaly_score(t2, 4)
        self.assertLess(score, 0.85)

    def test_t2_fault_above_threshold(self):
        """A large deviation vector must score above 0.85."""
        cov_inv = np.eye(4)
        x  = np.array([5.0, 4.0, 3.0, 6.0])
        mu = np.zeros(4)
        t2    = compute_hotelling_t2(x, mu, cov_inv)
        score = t2_anomaly_score(t2, 4)
        self.assertGreater(score, 0.85)

    def test_covariance_matrix_is_symmetric(self):
        stds = [0.3, 0.4, 2.0, 0.2]
        corr = np.array([
            [1.0, 0.65, 0.55, 0.45],
            [0.65, 1.0, 0.30, 0.35],
            [0.55, 0.30, 1.0, 0.25],
            [0.45, 0.35, 0.25, 1.0],
        ])
        cov = build_covariance_matrix(stds, corr)
        np.testing.assert_array_almost_equal(cov, cov.T)

    def test_t2_greater_than_diagonal_for_correlated_deviation(self):
        """
        When a deviation is PERPENDICULAR to the main correlation direction,
        T² with correlations is HIGHER than diagonal T² — the covariance
        structure amplifies unusual patterns.
        """
        stds = [1.0, 1.0]
        # High positive correlation (both signals normally move together)
        corr_high = np.array([[1.0, 0.9], [0.9, 1.0]])
        cov_high = build_covariance_matrix(stds, corr_high)
        inv_high = np.linalg.inv(cov_high)

        # Diagonal covariance (independent signals)
        inv_diag = np.eye(2)

        # Deviation orthogonal to correlation direction: signal_1 up, signal_2 down
        x  = np.array([1.0, -1.0])
        mu = np.zeros(2)

        t2_corr = compute_hotelling_t2(x, mu, inv_high)
        t2_diag = compute_hotelling_t2(x, mu, inv_diag)
        # T² with high positive correlation catches anti-correlated deviations
        # better than independent scoring
        self.assertGreater(t2_corr, t2_diag)


if __name__ == "__main__":
    unittest.main(verbosity=2)
