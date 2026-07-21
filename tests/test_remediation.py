"""
tests/test_remediation.py

Tests for the remediation step that sits between the Data Foundation Agent
and the Monitoring Agent. Covers the impute / drop / keep decision, the
imputability rule, baseline-mean imputation + re-validation, and provenance.

The signal_dropout scenario is the natural fixture: TEL_0015 has all five
critical signal fields null and a low signal_quality_score.

Run with:  python -m unittest tests.test_remediation -v
"""

import sys
import os
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.schemas.bearing_signal import ValidationStatus
from src.tools.data_loader import load_telemetry_rows
from src.tools.remediation import (
    remediate, decide_action, imputable_fields, missing_critical_fields,
    IMPUTE, DROP, KEEP,
)
from src.tools.curated_store import build_curated_rows, curated_row


def _agent():
    return DataFoundationAgent.from_data_files()


def _dropout(agent):
    """The all-null dropout record (TEL_0015) processed by the DFA."""
    row = deepcopy(load_telemetry_rows("signal_dropout")[1])
    return agent.process(row)


def _healthy(agent):
    return agent.process(deepcopy(load_telemetry_rows("healthy")[0]))


def _unknown(agent):
    return agent.process(deepcopy(load_telemetry_rows("unknown_asset")[0]))


class TestRemediation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.agent = _agent()
        cls.cfg   = cls.agent._cfg

    # ── Inspection ────────────────────────────────────────────────────

    def test_dropout_reports_all_missing(self):
        r = _dropout(self.agent)
        missing = missing_critical_fields(r, self.cfg)
        self.assertEqual(set(missing), set(self.cfg.critical_signal_fields))

    def test_only_fields_with_baselines_are_imputable(self):
        """bpfi_energy has no baseline → reported missing but NOT imputable."""
        r = _dropout(self.agent)
        canfix = imputable_fields(r, self.cfg)
        self.assertIn("vib_rms_mm_s", canfix)
        self.assertIn("kurtosis", canfix)
        self.assertIn("temp_c", canfix)
        self.assertIn("bpfo_energy", canfix)
        self.assertNotIn("bpfi_energy", canfix)   # no baseline_bpfi in master

    # ── Policy ────────────────────────────────────────────────────────

    def test_policy_keeps_valid(self):
        self.assertEqual(decide_action(_healthy(self.agent), self.cfg), KEEP)

    def test_policy_drops_rejected(self):
        self.assertEqual(decide_action(_unknown(self.agent), self.cfg), DROP)

    # ── DROP ──────────────────────────────────────────────────────────

    def test_drop_returns_none(self):
        r = _dropout(self.agent)
        self.assertIsNone(remediate(r, DROP, self.agent, self.cfg))

    # ── KEEP ──────────────────────────────────────────────────────────

    def test_keep_returns_record_tagged(self):
        r = _dropout(self.agent)
        kept = remediate(r, KEEP, self.agent, self.cfg)
        self.assertIsNotNone(kept)
        self.assertEqual(kept.remediation_action, KEEP)

    # ── IMPUTE ────────────────────────────────────────────────────────

    def test_impute_fills_fields_from_baseline(self):
        r = _dropout(self.agent)
        before = r.data_quality_score
        fixed  = remediate(r, IMPUTE, self.agent, self.cfg)

        # The four baseline-backed fields are now populated...
        self.assertIsNotNone(fixed.raw.vib_rms_mm_s)
        self.assertIsNotNone(fixed.raw.temp_c)
        # ...filled with the bearing's baseline mean.
        self.assertEqual(fixed.raw.temp_c, r.bearing_ctx.baseline_temp_mean)
        # bpfi has no baseline, so it stays missing.
        self.assertIsNone(fixed.raw.bpfi_energy)

    def test_impute_records_provenance(self):
        fixed = remediate(_dropout(self.agent), IMPUTE, self.agent, self.cfg)
        self.assertEqual(fixed.remediation_action, IMPUTE)
        self.assertEqual(fixed.imputation_method, self.cfg.auto_impute_method)
        self.assertIn("temp_c", fixed.imputed_fields)
        self.assertNotIn("bpfi_energy", fixed.imputed_fields)

    def test_impute_raises_score_after_revalidation(self):
        r = _dropout(self.agent)
        before = r.data_quality_score
        fixed  = remediate(r, IMPUTE, self.agent, self.cfg)
        self.assertGreater(fixed.data_quality_score, before)

    def test_impute_result_is_serialisable(self):
        import json
        fixed = remediate(_dropout(self.agent), IMPUTE, self.agent, self.cfg)
        json.dumps(fixed.to_dict())   # must not raise

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            remediate(_dropout(self.agent), "BOGUS", self.agent, self.cfg)

    # ── Two-step: impute, then decide on the still-incomplete record ──

    def test_record_still_incomplete_after_impute(self):
        """bpfi_energy has no baseline, so the record is still missing a field."""
        fixed = remediate(_dropout(self.agent), IMPUTE, self.agent, self.cfg)
        still = missing_critical_fields(fixed, self.cfg)
        self.assertEqual(still, ["bpfi_energy"])

    def test_keep_after_impute_preserves_provenance(self):
        """A KEEP applied after IMPUTE must not erase the imputation record."""
        fixed = remediate(_dropout(self.agent), IMPUTE, self.agent, self.cfg)
        kept  = remediate(fixed, KEEP, self.agent, self.cfg)
        self.assertEqual(kept.remediation_action, IMPUTE)   # not overwritten
        self.assertIn("temp_c", kept.imputed_fields)

    def test_drop_after_impute_removes_record(self):
        """The two-step drop path: impute what we can, then drop the rest."""
        fixed = remediate(_dropout(self.agent), IMPUTE, self.agent, self.cfg)
        self.assertIsNone(remediate(fixed, DROP, self.agent, self.cfg))

    # ── Curated table persistence ─────────────────────────────────────

    def test_curated_excludes_dropped(self):
        r = _dropout(self.agent)
        tid = r.raw.telemetry_id
        rows = build_curated_rows([r], {tid: None})   # decided -> dropped
        self.assertEqual(rows, [])

    def test_curated_excludes_rejected_when_undecided(self):
        rej = _unknown(self.agent)                     # REJECTED, not decided
        rows = build_curated_rows([rej], {})
        self.assertEqual(rows, [])

    def test_curated_includes_valid_as_is(self):
        h = _healthy(self.agent)
        rows = build_curated_rows([h], {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remediation_action"], "KEEP")

    def test_curated_row_carries_imputed_values_and_provenance(self):
        r = _dropout(self.agent)
        fixed = remediate(r, IMPUTE, self.agent, self.cfg)
        rows = build_curated_rows([r], {r.raw.telemetry_id: fixed})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNotNone(row["temp_c"])            # imputed value present in the table
        self.assertEqual(row["remediation_action"], IMPUTE)
        self.assertIn("temp_c", row["imputed_fields"])

    def test_curated_row_is_flat_and_serialisable(self):
        import json
        row = curated_row(_healthy(self.agent))
        json.dumps(row)                                # must not raise
        # flat row: no nested dicts/objects
        self.assertNotIn("raw", row)
        self.assertIn("data_quality_score", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
