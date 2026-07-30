"""Focused durable integration tests for the Agent 1 -> Agent 2 boundary."""

# ************** Added by Prateek Mittal on 17th July 2026 ******************
# These tests certify the real typed, routed, persisted, and provenance-aware
# handoff between the Data Foundation Agent and Monitoring Agent.

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.schemas.bearing_signal import ValidationStatus
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows
from src.tools.monitoring_decision_repository import SQLiteMonitoringDecisionRepository
from src.tools.trusted_signal_repository import SQLiteTrustedSignalRepository


def row(scenario: str, index: int = 0) -> dict:
    return deepcopy(load_telemetry_rows(scenario)[index])


@pytest.fixture
def pipeline(tmp_path):
    trusted_repo = SQLiteTrustedSignalRepository(str(tmp_path / "trusted.db"))
    decision_repo = SQLiteMonitoringDecisionRepository(str(tmp_path / "monitoring.db"))
    dfa = DataFoundationAgent.from_data_files(repository=trusted_repo)
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "ewma.json")
    monitoring = MonitoringAgent(cfg, decision_repository=decision_repo)
    return dfa, monitoring, trusted_repo, decision_repo, Path(cfg.ewma_state_file)


def test_healthy_durable_flow(pipeline):
    dfa, monitoring, trusted_repo, decision_repo, _ = pipeline
    trusted = dfa.process_and_store(row("healthy"))
    decision = monitoring.assess_and_store(trusted)
    assert trusted.validation_status == ValidationStatus.VALID
    assert decision.status == "healthy"
    assert decision.assessed is True
    assert trusted_repo.count() == 1
    assert decision_repo.count() == 1


def test_fault_durable_flow_emits_anomaly(pipeline):
    dfa, monitoring, _, decision_repo, _ = pipeline
    trusted = dfa.process_and_store(row("outer_race_fault", -1))
    decision = monitoring.assess_and_store(trusted)
    assert decision.status == "anomaly"
    assert decision.anomaly_event is not None
    assert decision.anomaly_event.bearing_id == trusted.raw.bearing_id
    assert decision_repo.count() == 1


def test_flagged_agent1_record_is_not_assessed(pipeline):
    dfa, monitoring, _, _, state_path = pipeline
    trusted = dfa.process_and_store(row("signal_dropout", 1))
    decision = monitoring.assess(trusted)
    assert trusted.validation_status == ValidationStatus.FLAGGED
    assert decision.status == "ineligible"
    assert decision.assessed is False
    assert not state_path.exists()


def test_rejected_agent1_record_stops_before_detection(pipeline):
    dfa, monitoring, trusted_repo, _, state_path = pipeline
    trusted = dfa.process_and_store(row("unknown_asset"))
    decision = monitoring.assess(trusted)
    assert trusted.validation_status == ValidationStatus.REJECTED
    assert decision.status == "ineligible"
    assert trusted_repo.count() == 1  # rejection remains auditable
    assert not state_path.exists()


def test_startup_is_explicitly_suppressed(pipeline):
    dfa, monitoring, _, _, state_path = pipeline
    trusted = dfa.process_and_store(row("startup_filter"))
    decision = monitoring.assess(trusted)
    assert trusted.downstream_eligible is True
    assert decision.status == "suppressed"
    assert decision.suppression_reason == "startup_shutdown"
    assert not state_path.exists()


def test_duplicate_durable_event_does_not_change_monitoring_state(pipeline):
    dfa, monitoring, trusted_repo, _, state_path = pipeline
    first = dfa.process_and_store(row("outer_race_fault", -1))
    monitoring.assess(first)
    before = state_path.read_text(encoding="utf-8")
    duplicate = dfa.process_and_store(row("outer_race_fault", -1))
    decision = monitoring.assess(duplicate)
    after = state_path.read_text(encoding="utf-8")
    assert duplicate.duplicate_detected is True
    assert decision.status == "duplicate"
    assert before == after
    assert trusted_repo.count() == 1


def test_out_of_order_durable_event_does_not_change_monitoring_state(pipeline):
    dfa, monitoring, _, _, state_path = pipeline
    newest = row("outer_race_fault", -1)
    newest["telemetry_id"] = "A12-NEW"
    newest["timestamp_utc"] = "2026-05-21T10:05:00Z"
    first = dfa.process_and_store(newest)
    monitoring.assess(first)
    before = state_path.read_text(encoding="utf-8")

    older = row("outer_race_fault", -1)
    older["telemetry_id"] = "A12-OLD"
    older["timestamp_utc"] = "2026-05-21T10:00:00Z"
    late = dfa.process_and_store(older)
    decision = monitoring.assess(late)
    after = state_path.read_text(encoding="utf-8")
    assert late.out_of_order is True
    assert decision.status == "out_of_order"
    assert before == after


def test_provenance_and_repository_records_are_linked(pipeline):
    dfa, monitoring, trusted_repo, decision_repo, _ = pipeline
    trusted = dfa.process_and_store(row("outer_race_fault", -1))
    decision = monitoring.assess_and_store(trusted)
    stored_trusted = trusted_repo.get(trusted.raw.telemetry_id)
    stored_decision = decision_repo.latest(trusted.raw.telemetry_id)
    assert decision.source_schema_version == trusted.schema_version
    assert decision.source_config_version == trusted.config_version
    assert decision.source_master_data_version == trusted.master_data_version
    assert stored_trusted["raw"]["telemetry_id"] == stored_decision["telemetry_id"]
    assert stored_decision["anomaly_event"]["source_config_version"] == trusted.config_version

# ***********************
