"""
tests/test_data_foundation.py

Tests for the Data Foundation Agent.

Covers every acceptance criterion from the Phase 2 roadmap plus all
7 demo scenarios where the agent is the first gate:

  S1 – healthy signal      → VALID, high quality score
  S2 – outer race fault    → VALID (signal is clean; fault detected later)
  S3 – inner race fault    → VALID
  S4 – lubrication issue   → VALID
  S5 – unknown asset_id    → REJECTED (the core missing-asset-mapping scenario)
  S6 – high-risk bottleneck → VALID (asset mapping, enrichment, bottleneck flag present)
  S7 – part unavailable    → VALID (agent doesn't see inventory; just validates signal)

  + startup filter         → VALID with startup note in reasons
  + signal dropout         → FLAGGED (null fields + low historian quality score)
  + invalid ranges         → FLAGGED
  + stale timestamp        → FLAGGED (in live mode; bypassed for historical data)
  + malformed record       → REJECTED

Run with:  python -m pytest tests/ -v        (from the dro/ root)
       or: python -m unittest tests.test_data_foundation -v
"""

import sys
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

# Make src importable when running from the dro/ root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.schemas.bearing_signal import ValidationStatus
from src.tools.data_loader import load_telemetry_rows, load_asset_master, load_bearing_master
from src.tools.config_loader import load_config
from src.tools.trusted_signal_repository import SQLiteTrustedSignalRepository


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_agent() -> DataFoundationAgent:
    return DataFoundationAgent.from_data_files()


def _row(scenario: str, index: int = 0) -> dict:
    """Load a specific row from a named scenario."""
    return deepcopy(load_telemetry_rows(scenario)[index])


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestDataFoundationAgent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Build the agent once for all tests."""
        cls.agent = _make_agent()

    # ── Core acceptance criteria ──────────────────────────────────────

    def test_valid_signal_returns_valid_status(self):
        """Phase 2 AC: valid records include asset context and quality score."""
        result = self.agent.process(_row("healthy"))
        self.assertEqual(result.validation_status, ValidationStatus.VALID)
        self.assertIsNotNone(result.asset_ctx)
        self.assertIsNotNone(result.bearing_ctx)
        self.assertGreater(result.data_quality_score, 0.80)

    def test_valid_signal_has_quality_score(self):
        """Phase 2 AC: every output has a data_quality_score."""
        result = self.agent.process(_row("healthy"))
        self.assertIsInstance(result.data_quality_score, float)
        self.assertGreaterEqual(result.data_quality_score, 0.0)
        self.assertLessEqual(result.data_quality_score, 1.0)

    def test_processed_at_is_populated(self):
        """Every output must have a processed_at timestamp."""
        result = self.agent.process(_row("healthy"))
        self.assertTrue(len(result.processed_at) > 0)

    def test_to_dict_is_serialisable(self):
        """TrustedBearingSignal.to_dict() must be JSON-serialisable."""
        import json
        result = self.agent.process(_row("healthy"))
        d = result.to_dict()
        # Should not raise
        json.dumps(d)

    # ── S5: Missing asset mapping ─────────────────────────────────────

    def test_unknown_asset_id_is_rejected(self):
        """S5: unknown asset_id → REJECTED, no context joined."""
        row = _row("unknown_asset")
        result = self.agent.process(row)
        self.assertEqual(result.validation_status, ValidationStatus.REJECTED)
        self.assertIsNone(result.asset_ctx)
        self.assertIsNone(result.bearing_ctx)
        self.assertEqual(result.data_quality_score, 0.0)

    def test_unknown_asset_rejection_reason_is_informative(self):
        """Rejection reason must name the asset_id."""
        row = _row("unknown_asset")
        result = self.agent.process(row)
        reasons = " ".join(result.validation.reasons)
        self.assertIn("AST_UNKNOWN_001", reasons)

    def test_unknown_asset_is_not_processable(self):
        """is_processable must be False for REJECTED records."""
        result = self.agent.process(_row("unknown_asset"))
        self.assertFalse(result.is_processable)

    # ── S1: Healthy asset ─────────────────────────────────────────────

    def test_healthy_signal_passes_all_checks(self):
        """S1: stable healthy signal — all validation sub-checks must pass."""
        result = self.agent.process(_row("healthy"))
        v = result.validation
        self.assertTrue(v.asset_mapping)
        self.assertTrue(v.bearing_mapping)
        self.assertTrue(v.fields_complete)
        self.assertTrue(v.ranges_valid)

    def test_healthy_signal_asset_context_correct(self):
        """S1: AST_MTR_002 is motor, LINE_001, medium criticality."""
        result = self.agent.process(_row("healthy"))
        ctx = result.asset_ctx
        self.assertEqual(ctx.asset_id, "AST_MTR_002")
        self.assertEqual(ctx.asset_type, "motor")
        self.assertEqual(ctx.production_line_id, "LINE_001")
        self.assertEqual(ctx.criticality, "medium")
        self.assertFalse(ctx.is_bottleneck)

    def test_healthy_signal_bearing_context_has_baselines(self):
        """S1: bearing context must carry baseline mean/std for Monitoring Agent."""
        result = self.agent.process(_row("healthy"))
        bctx = result.bearing_ctx
        self.assertGreater(bctx.baseline_vib_rms_mean, 0)
        self.assertGreater(bctx.baseline_vib_rms_std, 0)
        self.assertGreater(bctx.baseline_kurtosis_mean, 0)
        self.assertGreater(bctx.baseline_temp_mean, 0)

    # ── S2: Outer race fault ──────────────────────────────────────────

    def test_outer_race_signal_is_valid(self):
        """S2: outer race telemetry is clean data — agent passes it through."""
        result = self.agent.process(_row("outer_race_fault", index=-1))  # last (worst) reading
        self.assertIn(result.validation_status,
                      [ValidationStatus.VALID, ValidationStatus.FLAGGED])
        self.assertTrue(result.is_processable)

    def test_outer_race_asset_is_bottleneck(self):
        """S2/S6: AST_MTR_001 is_bottleneck=True must be present in asset context."""
        result = self.agent.process(_row("outer_race_fault"))
        self.assertTrue(result.asset_ctx.is_bottleneck)
        self.assertEqual(result.asset_ctx.iso_10816_zone, "C")

    # ── S3: Inner race fault ──────────────────────────────────────────

    def test_inner_race_signal_is_valid(self):
        """S3: inner race telemetry (BRG_002/CH_002) passes through cleanly."""
        result = self.agent.process(_row("inner_race_fault"))
        self.assertTrue(result.is_processable)
        self.assertEqual(result.raw.bearing_id, "BRG_002")
        self.assertEqual(result.raw.channel_id, "CH_002")

    def test_inner_race_bearing_context_correct(self):
        """S3: BRG_002 is NDE position on AST_MTR_001."""
        result = self.agent.process(_row("inner_race_fault"))
        self.assertEqual(result.bearing_ctx.bearing_position, "NDE")

    # ── S4: Lubrication issue ─────────────────────────────────────────

    def test_lubrication_signal_is_valid(self):
        """S4: lubrication issue signal is structurally valid."""
        result = self.agent.process(_row("lubrication_issue"))
        self.assertTrue(result.is_processable)

    def test_lubrication_asset_is_pump(self):
        """S4: AST_PMP_001 must be asset_type=pump."""
        result = self.agent.process(_row("lubrication_issue"))
        self.assertEqual(result.asset_ctx.asset_type, "pump")

    # ── S6: High-risk bottleneck (part available) ─────────────────────

    def test_bottleneck_asset_flags_correctly(self):
        """S6: gearbox (bottleneck + Zone D) enrichment fields must be present."""
        result = self.agent.process(_row("gearbox_fault"))
        ctx = result.asset_ctx
        self.assertTrue(ctx.is_bottleneck)
        self.assertEqual(ctx.iso_10816_zone, "D")
        self.assertEqual(ctx.criticality, "high")
        self.assertEqual(ctx.downtime_cost_per_hour, 18000)

    # ── S7: Part unavailable ──────────────────────────────────────────

    def test_gearbox_fault_signal_is_valid(self):
        """S7: agent validates the signal; inventory check is not its concern."""
        result = self.agent.process(_row("gearbox_fault"))
        self.assertTrue(result.is_processable)

    # ── Startup filter ────────────────────────────────────────────────

    def test_startup_flag_is_passed_through(self):
        """Startup records: must be processable with startup flag noted."""
        result = self.agent.process(_row("startup_filter"))
        self.assertTrue(result.is_processable)
        self.assertTrue(result.raw.startup_shutdown_flag)
        # Monitoring Agent reads this flag from result.raw
        self.assertTrue(any("startup" in r.lower() for r in result.validation.reasons))

    # ── Signal dropout ────────────────────────────────────────────────

    def test_dropout_record_is_flagged(self):
        """Signal dropout (null vib fields + low historian score) → FLAGGED."""
        # TEL_0015 has null vib, kurtosis, temp and signal_quality_score=0.15
        dropout_rows = load_telemetry_rows("signal_dropout")
        dropout_row = dropout_rows[1]  # TEL_0015 (index 1)
        result = self.agent.process(dropout_row)
        # Should be FLAGGED (not REJECTED — asset and bearing still map)
        self.assertEqual(result.validation_status, ValidationStatus.FLAGGED)
        self.assertIsNotNone(result.asset_ctx)   # context still joined
        self.assertIsNotNone(result.bearing_ctx)
        self.assertFalse(result.is_processable)  # requires review/remediation first
        self.assertEqual(result.next_route, "data_review")
        # Quality score must be lower than a healthy record
        self.assertLess(result.data_quality_score, 0.80)

    def test_dropout_reasons_mention_missing_fields(self):
        """Dropout FLAGGED reason must call out missing signal fields."""
        dropout_rows = load_telemetry_rows("signal_dropout")
        result = self.agent.process(dropout_rows[1])
        all_reasons = " ".join(result.validation.reasons).lower()
        self.assertIn("missing", all_reasons)

    # ── Invalid ranges ────────────────────────────────────────────────

    def test_negative_rpm_is_flagged(self):
        """Negative RPM → FLAGGED with range violation reason."""
        row = _row("healthy")
        row["rpm"] = -100
        result = self.agent.process(row)
        self.assertIn(result.validation_status,
                      [ValidationStatus.FLAGGED, ValidationStatus.REJECTED])
        reasons = " ".join(result.validation.reasons).lower()
        self.assertIn("rpm", reasons)
        self.assertIn("negative", reasons)

    def test_load_over_110_is_flagged(self):
        """Load % > 110 → flagged as sensor error."""
        row = _row("healthy")
        row["load_pct"] = 150
        result = self.agent.process(row)
        self.assertIn(result.validation_status,
                      [ValidationStatus.FLAGGED, ValidationStatus.REJECTED])
        reasons = " ".join(result.validation.reasons).lower()
        self.assertIn("load_pct", reasons)

    def test_temperature_above_limit_is_flagged(self):
        """temp_c > sensor max → flagged."""
        row = _row("healthy")
        row["temp_c"] = 200  # exceeds BRG_003 temp_max_valid=150
        result = self.agent.process(row)
        self.assertIn(result.validation_status,
                      [ValidationStatus.FLAGGED, ValidationStatus.REJECTED])
        reasons = " ".join(result.validation.reasons).lower()
        self.assertIn("temp", reasons)

    # ── Malformed input ───────────────────────────────────────────────

    def test_missing_required_fields_is_rejected(self):
        """Record missing asset_id, bearing_id, channel_id → REJECTED."""
        result = self.agent.process({"rpm": 1500})
        self.assertEqual(result.validation_status, ValidationStatus.REJECTED)

    def test_empty_dict_is_rejected(self):
        """Empty dict → REJECTED gracefully (no exception)."""
        result = self.agent.process({})
        self.assertEqual(result.validation_status, ValidationStatus.REJECTED)

    def test_non_dict_input_handled(self):
        """process() should not crash on non-dict input."""
        result = self.agent.process("not a dict")
        self.assertEqual(result.validation_status, ValidationStatus.REJECTED)

    # ── Batch processing ──────────────────────────────────────────────

    def test_batch_processes_all_scenarios(self):
        """process_batch returns one result per input record."""
        rows = (
            load_telemetry_rows("healthy") +
            load_telemetry_rows("outer_race_fault") +
            load_telemetry_rows("unknown_asset")
        )
        results = self.agent.process_batch(rows)
        self.assertEqual(len(results), len(rows))

    def test_batch_unknown_asset_correctly_rejected(self):
        """In a batch, unknown asset rows are rejected while others are processable."""
        rows = (
            load_telemetry_rows("healthy") +
            load_telemetry_rows("unknown_asset")
        )
        results = self.agent.process_batch(rows)
        processable = [r for r in results if r.is_processable]
        rejected    = [r for r in results if r.validation_status == ValidationStatus.REJECTED]
        self.assertTrue(len(processable) > 0, "Expected at least one processable record")
        self.assertTrue(len(rejected) > 0,    "Expected at least one rejected record")

    # ── Factory method ────────────────────────────────────────────────

    def test_from_data_files_loads_correctly(self):
        """DataFoundationAgent.from_data_files() must produce a working agent."""
        agent = DataFoundationAgent.from_data_files()
        result = agent.process(_row("healthy"))
        self.assertTrue(result.is_processable)
        self.assertIsNotNone(result.asset_ctx)

    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Comprehensive tests for the Agent 1 utility-hardening and completion work.
    # -- Timestamp and freshness utility ---------------------------------

    def test_valid_timestamp_is_normalized_for_audit(self):
        result = self.agent.process(_row("healthy"))
        self.assertTrue(result.validation.freshness_valid)
        self.assertIsInstance(result.record_age_seconds, float)

    def test_invalid_timestamp_is_flagged(self):
        row = _row("healthy")
        row["timestamp_utc"] = "not-a-timestamp"
        result = self.agent.process(row)
        self.assertEqual(result.validation_status, ValidationStatus.FLAGGED)
        self.assertFalse(result.validation.freshness_valid)
        self.assertEqual(result.next_route, "data_review")
        self.assertIn("ISO-8601", " ".join(result.validation.reasons))

    def test_timezone_is_required(self):
        row = _row("healthy")
        row["timestamp_utc"] = "2026-05-20T08:00:00"
        result = self.agent.process(row)
        self.assertEqual(result.validation_status, ValidationStatus.FLAGGED)
        self.assertIn("timezone", " ".join(result.validation.reasons).lower())

    def test_stale_live_record_routes_to_review(self):
        now = datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)
        assets = load_asset_master()
        bearings, channels = load_bearing_master()
        agent = DataFoundationAgent(assets, bearings, channels, load_config(), now_fn=lambda: now)
        row = _row("healthy")
        row["data_source"] = "live"
        result = agent.process(row)
        self.assertEqual(result.validation_status, ValidationStatus.FLAGGED)
        self.assertFalse(result.validation.freshness_valid)
        self.assertEqual(result.next_route, "data_review")

    def test_fresh_live_record_routes_to_monitoring(self):
        now = datetime(2026, 5, 20, 8, 0, 10, tzinfo=timezone.utc)
        assets = load_asset_master()
        bearings, channels = load_bearing_master()
        agent = DataFoundationAgent(assets, bearings, channels, load_config(), now_fn=lambda: now)
        row = _row("healthy")
        row["data_source"] = "live"
        result = agent.process(row)
        self.assertEqual(result.validation_status, ValidationStatus.VALID)
        self.assertTrue(result.is_processable)
        self.assertEqual(result.next_route, "monitoring")
        self.assertAlmostEqual(result.record_age_seconds, 10.0)

    def test_historical_record_does_not_fail_age_limit(self):
        result = self.agent.process(_row("healthy"))
        self.assertTrue(result.validation.freshness_valid)
        self.assertEqual(result.validation_status, ValidationStatus.VALID)

    def test_future_record_is_flagged(self):
        now = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)
        assets = load_asset_master()
        bearings, channels = load_bearing_master()
        agent = DataFoundationAgent(assets, bearings, channels, load_config(), now_fn=lambda: now)
        row = _row("healthy")
        row["timestamp_utc"] = (now + timedelta(minutes=2)).isoformat()
        result = agent.process(row)
        self.assertEqual(result.validation_status, ValidationStatus.FLAGGED)
        self.assertIn("future", " ".join(result.validation.reasons).lower())

    # -- Normalization utility -------------------------------------------

    def test_field_alias_is_normalized_and_audited(self):
        row = _row("healthy")
        row["vibration_rms"] = row.pop("vib_rms_mm_s")
        result = self.agent.process(row)
        self.assertEqual(result.raw.vib_rms_mm_s, 1.9)
        self.assertIn("renamed vibration_rms to vib_rms_mm_s", result.normalization_actions)

    def test_temperature_fahrenheit_is_converted(self):
        row = _row("healthy")
        row.pop("temp_c")
        row["temperature_f"] = 127.4
        result = self.agent.process(row)
        self.assertAlmostEqual(result.raw.temp_c, 53.0, places=2)
        self.assertIn("converted temperature_f to temp_c", result.normalization_actions)

    def test_speed_rps_is_converted(self):
        row = _row("healthy")
        row.pop("rpm")
        row["speed_rps"] = 20
        result = self.agent.process(row)
        self.assertEqual(result.raw.rpm, 1200.0)

    def test_canonical_field_wins_over_alias(self):
        row = _row("healthy")
        row["vibration_rms"] = 99.0
        result = self.agent.process(row)
        self.assertEqual(result.raw.vib_rms_mm_s, 1.9)
        self.assertIn("vibration_rms", result.unknown_fields)

    def test_input_dictionary_is_not_mutated(self):
        row = _row("healthy")
        row["vibration_rms"] = row.pop("vib_rms_mm_s")
        original = deepcopy(row)
        self.agent.process(row)
        self.assertEqual(row, original)

    def test_unknown_fields_are_reported(self):
        row = _row("healthy")
        row["unexpected_sensor_tag"] = 123
        result = self.agent.process(row)
        self.assertIn("unexpected_sensor_tag", result.unknown_fields)

    # -- Context relationship and routing utilities ----------------------

    def test_bearing_must_belong_to_asset(self):
        assets = load_asset_master()
        bearings, channels = load_bearing_master()
        original = bearings["BRG_003"]
        wrong = original.model_copy(update={"asset_id": "AST_MTR_001"})
        bearings["BRG_003"] = wrong
        channels[wrong.channel_id] = wrong
        agent = DataFoundationAgent(assets, bearings, channels, load_config())
        result = agent.process(_row("healthy"))
        self.assertEqual(result.validation_status, ValidationStatus.REJECTED)
        self.assertFalse(result.is_processable)
        self.assertIn("belongs to asset_id", " ".join(result.validation.reasons))

    def test_valid_record_has_explicit_monitoring_route(self):
        result = self.agent.process(_row("healthy"))
        self.assertTrue(result.downstream_eligible)
        self.assertEqual(result.next_route, "monitoring")

    def test_rejected_record_has_stop_route(self):
        result = self.agent.process(_row("unknown_asset"))
        self.assertFalse(result.downstream_eligible)
        self.assertEqual(result.next_route, "stop")

    def test_new_audit_fields_are_serialized(self):
        payload = self.agent.process(_row("healthy")).to_dict()
        for key in ("normalization_actions", "unknown_fields", "record_age_seconds",
                    "downstream_eligible", "next_route", "routing_reason"):
            self.assertIn(key, payload)

    # -- Provenance and source profiles ----------------------------------

    def test_provenance_versions_are_populated(self):
        result = self.agent.process(_row("healthy"))
        self.assertEqual(result.schema_version, "1.2")
        self.assertEqual(len(result.config_version), 16)
        self.assertEqual(len(result.master_data_version), 16)

    def test_provenance_versions_are_stable(self):
        first = DataFoundationAgent.from_data_files().process(_row("healthy"))
        second = DataFoundationAgent.from_data_files().process(_row("healthy"))
        self.assertEqual(first.config_version, second.config_version)
        self.assertEqual(first.master_data_version, second.master_data_version)

    def test_master_data_change_changes_version(self):
        assets = load_asset_master()
        bearings, channels = load_bearing_master()
        baseline = DataFoundationAgent(assets, bearings, channels, load_config()).process(_row("healthy"))
        assets["AST_MTR_002"] = assets["AST_MTR_002"].model_copy(update={"criticality": "high"})
        changed = DataFoundationAgent(assets, bearings, channels, load_config()).process(_row("healthy"))
        self.assertNotEqual(baseline.master_data_version, changed.master_data_version)

    def test_opcua_source_profile_normalizes_fields(self):
        row = _row("healthy")
        row["data_source"] = "opcua"
        row["MotorSpeed"] = row.pop("rpm")
        row["BearingTemp"] = row.pop("temp_c")
        row["VibrationRMS"] = row.pop("vib_rms_mm_s")
        result = self.agent.process(row)
        self.assertEqual(result.source_profile, "opcua")
        self.assertEqual(result.raw.rpm, 1768.0)
        self.assertEqual(result.raw.temp_c, 53.0)
        self.assertEqual(result.raw.vib_rms_mm_s, 1.9)

    def test_event_hub_source_profile_normalizes_identity(self):
        row = _row("healthy")
        row["data_source"] = "event_hub"
        row["assetId"] = row.pop("asset_id")
        row["bearingId"] = row.pop("bearing_id")
        row["channelId"] = row.pop("channel_id")
        row["eventTime"] = row.pop("timestamp_utc")
        result = self.agent.process(row)
        self.assertEqual(result.source_profile, "event_hub")
        self.assertEqual(result.raw.asset_id, "AST_MTR_002")
        self.assertTrue(result.validation.asset_mapping)

    # -- Durable repository, idempotency, and ordering -------------------

    def test_process_and_store_requires_repository(self):
        with self.assertRaises(RuntimeError):
            self.agent.process_and_store(_row("healthy"))

    def test_process_and_store_persists_and_can_retrieve(self):
        with TemporaryDirectory() as directory:
            repository = SQLiteTrustedSignalRepository(str(Path(directory) / "signals.db"))
            agent = DataFoundationAgent.from_data_files(repository=repository)
            result = agent.process_and_store(_row("healthy"))
            self.assertEqual(result.persistence_status, "stored")
            self.assertEqual(repository.count(), 1)
            stored = repository.get(result.raw.telemetry_id)
            self.assertEqual(stored["raw"]["asset_id"], "AST_MTR_002")
            self.assertEqual(stored["persistence_status"], "stored")

    def test_duplicate_telemetry_id_is_not_stored_twice(self):
        with TemporaryDirectory() as directory:
            repository = SQLiteTrustedSignalRepository(str(Path(directory) / "signals.db"))
            agent = DataFoundationAgent.from_data_files(repository=repository)
            first = agent.process_and_store(_row("healthy"))
            duplicate = agent.process_and_store(_row("healthy"))
            self.assertEqual(first.persistence_status, "stored")
            self.assertTrue(duplicate.duplicate_detected)
            self.assertEqual(duplicate.persistence_status, "duplicate_not_stored")
            self.assertFalse(duplicate.is_processable)
            self.assertEqual(duplicate.next_route, "stop")
            self.assertEqual(repository.count(), 1)

    def test_out_of_order_event_is_stored_but_routed_to_review(self):
        with TemporaryDirectory() as directory:
            repository = SQLiteTrustedSignalRepository(str(Path(directory) / "signals.db"))
            agent = DataFoundationAgent.from_data_files(repository=repository)
            newest = _row("healthy")
            newest["telemetry_id"] = "ORDER-NEW"
            newest["timestamp_utc"] = "2026-05-20T09:00:00Z"
            older = _row("healthy")
            older["telemetry_id"] = "ORDER-OLD"
            older["timestamp_utc"] = "2026-05-20T08:00:00Z"
            agent.process_and_store(newest)
            result = agent.process_and_store(older)
            self.assertTrue(result.out_of_order)
            self.assertEqual(result.validation_status, ValidationStatus.FLAGGED)
            self.assertEqual(result.next_route, "data_review")
            self.assertEqual(result.persistence_status, "stored")
            self.assertEqual(repository.count(), 2)

    def test_in_order_events_remain_eligible(self):
        with TemporaryDirectory() as directory:
            repository = SQLiteTrustedSignalRepository(str(Path(directory) / "signals.db"))
            agent = DataFoundationAgent.from_data_files(repository=repository)
            first = _row("healthy")
            first["telemetry_id"] = "ORDER-1"
            second = _row("healthy")
            second["telemetry_id"] = "ORDER-2"
            second["timestamp_utc"] = "2026-05-20T08:01:00Z"
            agent.process_and_store(first)
            result = agent.process_and_store(second)
            self.assertFalse(result.out_of_order)
            self.assertTrue(result.is_processable)
            self.assertEqual(result.next_route, "monitoring")

    def test_rejected_record_is_persisted_for_audit(self):
        with TemporaryDirectory() as directory:
            repository = SQLiteTrustedSignalRepository(str(Path(directory) / "signals.db"))
            agent = DataFoundationAgent.from_data_files(repository=repository)
            result = agent.process_and_store(_row("unknown_asset"))
            self.assertEqual(result.validation_status, ValidationStatus.REJECTED)
            self.assertEqual(result.persistence_status, "stored")
            self.assertEqual(repository.count(), 1)

    def test_persistence_failure_routes_to_review(self):
        class BrokenRepository:
            def exists(self, telemetry_id): return False
            def latest_event_epoch(self, bearing_id): return None
            def save(self, trusted_signal, event_epoch): raise OSError("disk unavailable")

        agent = DataFoundationAgent.from_data_files(repository=BrokenRepository())
        result = agent.process_and_store(_row("healthy"))
        self.assertEqual(result.persistence_status, "failed")
        self.assertFalse(result.is_processable)
        self.assertEqual(result.next_route, "data_review")
        self.assertIn("disk unavailable", result.routing_reason)

    def test_process_and_store_batch_persists_all_unique_records(self):
        with TemporaryDirectory() as directory:
            repository = SQLiteTrustedSignalRepository(str(Path(directory) / "signals.db"))
            agent = DataFoundationAgent.from_data_files(repository=repository)
            rows = [_row("healthy"), _row("outer_race_fault")]
            results = agent.process_and_store_batch(rows)
            self.assertEqual(len(results), 2)
            self.assertEqual(repository.count(), 2)

    # ***********************


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
