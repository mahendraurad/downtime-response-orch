"""Contract certification for the complete Agent 1 -> Agent 2 -> Agent 3 chain."""
from copy import deepcopy
import json

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.orchestrator.routing import route_after_foundation, route_after_monitoring
from src.schemas.bearing_signal import ValidationStatus
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows
from src.tools.monitoring_decision_repository import SQLiteMonitoringDecisionRepository
from src.tools.trusted_signal_repository import SQLiteTrustedSignalRepository


# ************** Added by Prateek Mittal on 20th July 2026 ******************
# This suite treats the three agents as one typed subsystem. It asserts both
# positive outputs and every legitimate stop/suppression boundary so a caller
# cannot accidentally diagnose data that Agent 1 or Agent 2 rejected.
def _row(scenario, index=0):
    return deepcopy(load_telemetry_rows(scenario)[index])


@pytest.fixture()
def chain(tmp_path):
    trusted_repo = SQLiteTrustedSignalRepository(str(tmp_path / "trusted.db"))
    monitoring_repo = SQLiteMonitoringDecisionRepository(str(tmp_path / "monitoring.db"))
    foundation = DataFoundationAgent.from_data_files(repository=trusted_repo)
    config = load_monitoring_config()
    config.ewma_state_file = str(tmp_path / "ewma.json")
    monitoring = MonitoringAgent(config, decision_repository=monitoring_repo)
    failure = FailureIntelligenceAgent.from_data_files()
    return foundation, monitoring, failure, trusted_repo, monitoring_repo


def _run(chain, row, durable=False):
    foundation, monitoring, failure, _, _ = chain
    trusted = foundation.process_and_store(row) if durable else foundation.process(row)
    decision = monitoring.assess_and_store(trusted) if durable else monitoring.assess(trusted)
    diagnosis = (
        failure.process(decision.anomaly_event, trusted)
        if decision.anomaly_event is not None
        else None
    )
    return trusted, decision, diagnosis


@pytest.mark.parametrize(
    ("scenario", "index", "fault_code", "fault_mode", "stage", "band"),
    [
        ("outer_race_fault", -1, "FT_001", "outer_race_fault", 3, "BPFO"),
        ("inner_race_fault", -1, "FT_002", "inner_race_fault", 3, "BPFI"),
        ("lubrication_issue", -1, "FT_003", "lubrication_issue", 2, "broadband"),
        ("gearbox_fault", -1, "FT_007", "rolling_element_fault", 3, "BSF"),
    ],
)
def test_supported_fault_matrix(chain, scenario, index, fault_code, fault_mode, stage, band):
    trusted, decision, diagnosis = _run(chain, _row(scenario, index))
    assert trusted.validation_status == ValidationStatus.VALID
    assert trusted.downstream_eligible is True
    assert trusted.next_route == "monitoring"
    assert decision.status == "anomaly"
    assert decision.assessed is True
    assert decision.anomaly_event is not None
    assert diagnosis.diagnosis_status == "diagnosed"
    assert diagnosis.diagnostic_eligible is True
    assert diagnosis.fault_code == fault_code
    assert diagnosis.fault_mode == fault_mode
    assert diagnosis.iso_stage == stage
    assert diagnosis.evidence["dominant_band"] == band
    assert diagnosis.recommended_checks
    assert diagnosis.narrative


def test_healthy_terminal_path(chain):
    trusted, decision, diagnosis = _run(chain, _row("healthy"))
    assert trusted.validation_status == ValidationStatus.VALID
    assert decision.status == "healthy"
    assert decision.assessed is True
    assert decision.anomaly_event is None
    assert diagnosis is None
    assert route_after_foundation({"trusted_signal": trusted}) == "monitoring"
    assert route_after_monitoring({"anomaly_event": None}) != "failure_intelligence"


def test_startup_suppression_terminal_path(chain):
    trusted, decision, diagnosis = _run(chain, _row("startup_filter"))
    assert trusted.downstream_eligible is True
    assert decision.status == "suppressed"
    assert decision.suppression_reason == "startup_shutdown"
    assert decision.assessed is False
    assert diagnosis is None


def test_stopped_machine_suppression_terminal_path(chain):
    payload = _row("healthy")
    payload.update(machine_state="idle", rpm=0, load_pct=0, vib_rms_mm_s=0.5)
    trusted, decision, diagnosis = _run(chain, payload)
    assert trusted.downstream_eligible is True
    assert decision.status == "suppressed"
    assert decision.suppression_reason == "non_running_regime"
    assert diagnosis is None


def test_repeated_anomaly_is_cooldown_suppressed_before_agent3(chain):
    foundation, monitoring, failure, _, _ = chain
    monitoring._cfg.alert_cooldown_seconds = 300
    first_trusted = foundation.process(_row("outer_race_fault", -1))
    first_decision = monitoring.assess(first_trusted)
    first_diagnosis = failure.process(first_decision.anomaly_event, first_trusted)
    repeated = _row("outer_race_fault", -1)
    repeated["telemetry_id"] = "COOLDOWN-UNIQUE"
    second_trusted = foundation.process(repeated)
    second_decision = monitoring.assess(second_trusted)
    assert first_diagnosis.fault_mode == "outer_race_fault"
    assert second_decision.status == "cooldown"
    assert second_decision.anomaly_event is None


def test_flagged_quality_record_stops_before_detection(chain):
    trusted, decision, diagnosis = _run(chain, _row("signal_dropout", 1))
    assert trusted.validation_status == ValidationStatus.FLAGGED
    assert trusted.downstream_eligible is False
    assert trusted.next_route == "data_review"
    assert decision.status == "ineligible"
    assert decision.anomaly_event is None
    assert diagnosis is None
    assert route_after_foundation({"trusted_signal": trusted}) != "monitoring"


def test_stale_live_record_stops_before_monitoring(chain):
    payload = _row("outer_race_fault", -1)
    payload["data_source"] = "live"
    trusted, decision, diagnosis = _run(chain, payload)
    assert trusted.validation_status == ValidationStatus.FLAGGED
    assert trusted.validation.freshness_valid is False
    assert trusted.next_route == "data_review"
    assert decision.status == "ineligible"
    assert diagnosis is None


def test_rejected_unknown_asset_stops_before_detection(chain):
    trusted, decision, diagnosis = _run(chain, _row("unknown_asset"))
    assert trusted.validation_status == ValidationStatus.REJECTED
    assert trusted.next_route == "stop"
    assert decision.status == "ineligible"
    assert diagnosis is None


@pytest.mark.parametrize("payload", [{}, None, "not-json-object", 42, []])
def test_malformed_payloads_never_escape_agent1(chain, payload):
    trusted, decision, diagnosis = _run(chain, payload)
    assert trusted.validation_status == ValidationStatus.REJECTED
    assert trusted.downstream_eligible is False
    assert decision.status == "ineligible"
    assert decision.assessed is False
    assert decision.anomaly_event is None
    assert diagnosis is None


def test_valid_anomaly_without_fault_signature_is_undetermined(chain):
    trusted, decision, diagnosis = _run(chain, _row("fi_hitl_test"))
    assert trusted.validation_status == ValidationStatus.VALID
    assert decision.status == "anomaly"
    assert diagnosis.diagnosis_status == "undetermined"
    assert diagnosis.diagnostic_eligible is True
    assert diagnosis.fault_mode == "undetermined"
    assert diagnosis.iso_stage == 0
    assert diagnosis.recommended_checks


def test_global_alias_input_survives_all_three_agents(chain):
    payload = _row("outer_race_fault", -1)
    original_vibration = payload.pop("vib_rms_mm_s")
    original_temperature = payload.pop("temp_c")
    original_rpm = payload.pop("rpm")
    payload["vibration_rms"] = original_vibration
    payload["temperature_f"] = original_temperature * 9 / 5 + 32
    payload["speed_rps"] = original_rpm / 60
    trusted, decision, diagnosis = _run(chain, payload)
    assert trusted.raw.vib_rms_mm_s == pytest.approx(original_vibration)
    assert trusted.raw.temp_c == pytest.approx(original_temperature)
    assert trusted.raw.rpm == pytest.approx(original_rpm)
    assert len(trusted.normalization_actions) == 3
    assert decision.status == "anomaly"
    assert diagnosis.fault_mode == "outer_race_fault"


def test_canonical_value_wins_over_conflicting_alias_through_chain(chain):
    payload = _row("outer_race_fault", -1)
    canonical = payload["vib_rms_mm_s"]
    payload["vibration_rms"] = 0.01
    trusted, decision, diagnosis = _run(chain, payload)
    assert trusted.raw.vib_rms_mm_s == canonical
    assert "vibration_rms" in trusted.unknown_fields
    assert decision.status == "anomaly"
    assert diagnosis.fault_mode == "outer_race_fault"


def test_input_dictionary_is_not_mutated_by_full_chain(chain):
    payload = _row("inner_race_fault", -1)
    original = deepcopy(payload)
    _run(chain, payload)
    assert payload == original


def test_identity_and_event_ids_remain_linked(chain):
    trusted, decision, diagnosis = _run(chain, _row("outer_race_fault", -1))
    event = decision.anomaly_event
    assert event.asset_id == trusted.raw.asset_id == diagnosis.asset_id
    assert event.bearing_id == trusted.raw.bearing_id == diagnosis.bearing_id
    assert event.channel_id == trusted.raw.channel_id
    assert event.timestamp_utc == trusted.raw.timestamp_utc
    assert diagnosis.case_id == event.case_id


def test_provenance_chain_is_complete(chain):
    trusted, decision, diagnosis = _run(chain, _row("outer_race_fault", -1))
    event = decision.anomaly_event
    assert decision.source_schema_version == trusted.schema_version
    assert decision.source_config_version == trusted.config_version
    assert decision.source_master_data_version == trusted.master_data_version
    assert event.source_schema_version == trusted.schema_version
    assert event.source_config_version == trusted.config_version
    assert diagnosis.source_schema_version == trusted.schema_version
    assert diagnosis.source_config_version == trusted.config_version
    assert diagnosis.source_master_data_version == trusted.master_data_version
    assert diagnosis.source_monitoring_config_version == decision.monitoring_config_version
    assert diagnosis.source_detector_version == event.detector_version
    assert diagnosis.fi_config_version
    assert diagnosis.taxonomy_version


def test_all_three_outputs_have_json_contracts(chain):
    trusted, decision, diagnosis = _run(chain, _row("outer_race_fault", -1))
    payloads = [trusted.to_dict(), decision.to_dict(), diagnosis.to_dict()]
    encoded = json.dumps(payloads)
    assert "outer_race_fault" in encoded
    assert payloads[0]["next_route"] == "monitoring"
    assert payloads[1]["status"] == "anomaly"
    assert payloads[2]["diagnosis_status"] == "diagnosed"


def test_compatibility_process_apis_match_structured_path(chain):
    foundation, monitoring, failure, _, _ = chain
    trusted = foundation.process(_row("outer_race_fault", -1))
    event = monitoring.process(trusted)
    diagnosis = failure.process(event, trusted)
    assert event is not None
    assert diagnosis.fault_code == "FT_001"


def test_duplicate_durable_record_stops_agents_2_and_3(chain):
    foundation, monitoring, _, trusted_repo, monitoring_repo = chain
    payload = _row("outer_race_fault", -1)
    first = foundation.process_and_store(deepcopy(payload))
    first_decision = monitoring.assess_and_store(first)
    duplicate = foundation.process_and_store(deepcopy(payload))
    duplicate_decision = monitoring.assess_and_store(duplicate)
    assert first_decision.status == "anomaly"
    assert duplicate.duplicate_detected is True
    assert duplicate_decision.status == "duplicate"
    assert duplicate_decision.anomaly_event is None
    assert trusted_repo.count() == 1
    assert monitoring_repo.count() == 2


def test_out_of_order_durable_record_stops_agents_2_and_3(chain):
    foundation, monitoring, _, _, _ = chain
    newer = _row("outer_race_fault", -1)
    newer.update(telemetry_id="CHAIN-NEW", timestamp_utc="2026-05-21T10:00:00Z")
    monitoring.assess(foundation.process_and_store(newer))
    older = _row("outer_race_fault", -1)
    older.update(telemetry_id="CHAIN-OLD", timestamp_utc="2026-05-21T09:00:00Z")
    late = foundation.process_and_store(older)
    decision = monitoring.assess(late)
    assert late.out_of_order is True
    assert late.next_route == "data_review"
    assert decision.status == "out_of_order"
    assert decision.anomaly_event is None


def test_equal_timestamp_with_unique_ids_remains_processable(chain):
    foundation, monitoring, _, _, _ = chain
    first = _row("outer_race_fault", -1)
    first["telemetry_id"] = "CHAIN-EQUAL-1"
    second = deepcopy(first)
    second["telemetry_id"] = "CHAIN-EQUAL-2"
    monitoring.assess(foundation.process_and_store(first))
    trusted = foundation.process_and_store(second)
    decision = monitoring.assess(trusted)
    assert trusted.duplicate_detected is False
    assert trusted.out_of_order is False
    assert decision.status == "anomaly"


def test_durable_rejection_is_audited_but_not_diagnosed(chain):
    trusted, decision, diagnosis = _run(chain, _row("unknown_asset"), durable=True)
    _, _, _, trusted_repo, monitoring_repo = chain
    assert trusted.persistence_status == "stored"
    assert decision.persistence_status == "stored"
    assert trusted_repo.count() == 1
    assert monitoring_repo.count() == 1
    assert diagnosis is None


def test_agent1_persistence_failure_blocks_agents_2_and_3(tmp_path):
    class BrokenTrustedRepository:
        def exists(self, telemetry_id):
            return False

        def latest_event_epoch(self, bearing_id):
            return None

        def save(self, trusted_signal, event_epoch):
            raise OSError("trusted database unavailable")

    foundation = DataFoundationAgent.from_data_files(repository=BrokenTrustedRepository())
    config = load_monitoring_config()
    config.ewma_state_file = str(tmp_path / "broken-foundation-ewma.json")
    monitoring = MonitoringAgent(config)
    trusted = foundation.process_and_store(_row("outer_race_fault", -1))
    decision = monitoring.assess(trusted)
    assert trusted.persistence_status == "failed"
    assert trusted.downstream_eligible is False
    assert trusted.next_route == "data_review"
    assert decision.status == "ineligible"
    assert decision.anomaly_event is None


def test_agent2_audit_failure_is_visible_without_erasing_anomaly(tmp_path):
    class BrokenMonitoringRepository:
        def save(self, result):
            raise OSError("monitoring database unavailable")

    foundation = DataFoundationAgent.from_data_files()
    config = load_monitoring_config()
    config.ewma_state_file = str(tmp_path / "broken-monitoring-ewma.json")
    monitoring = MonitoringAgent(config, decision_repository=BrokenMonitoringRepository())
    failure = FailureIntelligenceAgent.from_data_files()
    trusted = foundation.process(_row("outer_race_fault", -1))
    decision = monitoring.assess_and_store(trusted)
    diagnosis = failure.process(decision.anomaly_event, trusted)
    assert decision.status == "anomaly"
    assert decision.persistence_status == "failed"
    assert "monitoring database unavailable" in decision.suppression_reason
    assert decision.anomaly_event is not None
    assert diagnosis.fault_mode == "outer_race_fault"


def test_mixed_batch_isolation_across_three_agents(chain):
    foundation, monitoring, failure, _, _ = chain
    inputs = [
        _row("healthy"),
        _row("outer_race_fault", -1),
        _row("signal_dropout", 1),
        _row("gearbox_fault", -1),
        {},
    ]
    trusted_batch = foundation.process_batch(inputs)
    decisions = [monitoring.assess(item) for item in trusted_batch]
    diagnoses = [
        failure.process(decision.anomaly_event, trusted)
        if decision.anomaly_event else None
        for trusted, decision in zip(trusted_batch, decisions)
    ]
    assert [d.status for d in decisions] == [
        "healthy", "anomaly", "ineligible", "anomaly", "ineligible"
    ]
    assert [d.fault_mode if d else None for d in diagnoses] == [
        None, "outer_race_fault", None, "rolling_element_fault", None
    ]


def test_repeated_stateless_agent3_call_is_deterministic_except_timestamp(chain):
    trusted, decision, first = _run(chain, _row("outer_race_fault", -1))
    failure = chain[2]
    second = failure.process(decision.anomaly_event, trusted)
    first_payload = first.to_dict()
    second_payload = second.to_dict()
    first_payload.pop("processed_at")
    second_payload.pop("processed_at")
    assert first_payload == second_payload
# ***********************
