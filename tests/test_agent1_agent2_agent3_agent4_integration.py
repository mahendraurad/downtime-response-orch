"""Complete deterministic backend certification through Predictive Risk."""
from copy import deepcopy
import json

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.orchestrator.routing import route_after_diagnosis, route_after_risk
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows
from src.tools.monitoring_decision_repository import SQLiteMonitoringDecisionRepository
from src.tools.trusted_signal_repository import SQLiteTrustedSignalRepository


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def chain(tmp_path):
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "agent1234-ewma.json")
    return (
        DataFoundationAgent.from_data_files(),
        MonitoringAgent(cfg),
        FailureIntelligenceAgent.from_data_files(),
        PredictiveRiskAgent.from_data_files(),
    )


def _row(name, index=0):
    return deepcopy(load_telemetry_rows(name)[index])


def _run(chain, payload):
    foundation, monitoring, failure, risk_agent = chain
    trusted = foundation.process(payload)
    decision = monitoring.assess(trusted)
    if decision.anomaly_event is None:
        return trusted, decision, None, None
    diagnosis = failure.process(decision.anomaly_event, trusted)
    risk = risk_agent.process(diagnosis, decision.anomaly_event, trusted)
    return trusted, decision, diagnosis, risk


@pytest.mark.parametrize(
    ("scenario", "fault", "risk_level", "rul_max"),
    [
        ("outer_race_fault", "outer_race_fault", "critical", 7),
        ("inner_race_fault", "inner_race_fault", "critical", 5),
        ("lubrication_issue", "lubrication_issue", "high", 20),
        ("gearbox_fault", "rolling_element_fault", "critical", 10),
    ],
)
def test_supported_faults_reach_complete_risk_card(chain, scenario, fault, risk_level, rul_max):
    trusted, decision, diagnosis, risk = _run(chain, _row(scenario, -1))
    assert trusted.next_route == "monitoring"
    assert decision.status == "anomaly"
    assert diagnosis.fault_mode == fault
    assert risk.assessment_status == "assessed"
    assert risk.risk_level == risk_level
    assert risk.rul_max_days == rul_max
    assert risk.case_id == diagnosis.case_id == decision.anomaly_event.case_id


def test_undetermined_anomaly_reaches_monitor_risk(chain):
    _, decision, diagnosis, risk = _run(chain, _row("fi_hitl_test"))
    assert decision.status == "anomaly"
    assert diagnosis.diagnosis_status == "undetermined"
    assert risk.assessment_status == "monitor"
    assert risk.rul_band_label == "monitor"


@pytest.mark.parametrize("scenario", ["healthy", "startup_filter", "signal_dropout", "unknown_asset"])
def test_non_anomaly_terminal_paths_never_create_risk(chain, scenario):
    _, decision, diagnosis, risk = _run(chain, _row(scenario, -1))
    assert decision.anomaly_event is None
    assert diagnosis is None
    assert risk is None


@pytest.mark.parametrize("payload", [None, {}, "bad-payload", 42, []])
def test_malformed_payload_never_creates_risk(chain, payload):
    trusted, decision, diagnosis, risk = _run(chain, payload)
    assert trusted.downstream_eligible is False
    assert decision.status == "ineligible"
    assert diagnosis is None and risk is None


def test_normalized_input_reaches_same_risk_semantics(chain):
    payload = _row("outer_race_fault", -1)
    vibration = payload.pop("vib_rms_mm_s")
    temperature = payload.pop("temp_c")
    payload["vibration_rms"] = vibration
    payload["temperature_f"] = temperature * 9 / 5 + 32
    trusted, _, diagnosis, risk = _run(chain, payload)
    assert trusted.normalization_actions
    assert diagnosis.fault_code == "FT_001"
    assert risk.rul_band_label == "0–7 days"


def test_opcua_source_profile_reaches_same_risk_semantics(chain):
    payload = _row("outer_race_fault", -1)
    payload["data_source"] = "opcua"
    payload["MotorSpeed"] = payload.pop("rpm")
    payload["BearingTemp"] = payload.pop("temp_c")
    payload["VibrationRMS"] = payload.pop("vib_rms_mm_s")
    trusted, decision, diagnosis, risk = _run(chain, payload)
    assert trusted.source_profile == "opcua"
    assert decision.status == "anomaly"
    assert diagnosis.fault_code == "FT_001"
    assert risk.rul_band_label == "0–7 days"


def test_canonical_field_wins_over_conflicting_alias_through_risk(chain):
    payload = _row("outer_race_fault", -1)
    canonical = payload["vib_rms_mm_s"]
    payload["vibration_rms"] = 0.01
    trusted, decision, diagnosis, risk = _run(chain, payload)
    assert trusted.raw.vib_rms_mm_s == canonical
    assert "vibration_rms" in trusted.unknown_fields
    assert decision.status == "anomaly"
    assert diagnosis.fault_code == "FT_001"
    assert risk.assessment_status == "assessed"


def test_unknown_noncanonical_field_is_audited_without_changing_risk(chain):
    payload = _row("gearbox_fault", -1)
    payload["connector_debug_tag"] = "source-only"
    trusted, _, diagnosis, risk = _run(chain, payload)
    assert "connector_debug_tag" in trusted.unknown_fields
    assert diagnosis.fault_mode == "rolling_element_fault"
    assert risk.rul_max_days == 10


def test_stale_live_input_stops_before_monitoring_and_risk(chain):
    payload = _row("outer_race_fault", -1)
    payload["data_source"] = "live"
    trusted, decision, diagnosis, risk = _run(chain, payload)
    assert trusted.validation.freshness_valid is False
    assert trusted.next_route == "data_review"
    assert decision.status == "ineligible"
    assert diagnosis is None and risk is None


def test_timezone_free_timestamp_stops_before_monitoring_and_risk(chain):
    payload = _row("outer_race_fault", -1)
    payload["timestamp_utc"] = "2026-05-20T10:00:00"
    trusted, decision, diagnosis, risk = _run(chain, payload)
    assert trusted.validation.freshness_valid is False
    assert decision.status == "ineligible"
    assert diagnosis is None and risk is None


def test_low_signal_quality_stops_before_monitoring_and_risk(chain):
    payload = _row("outer_race_fault", -1)
    payload["signal_quality_score"] = 0.1
    trusted, decision, diagnosis, risk = _run(chain, payload)
    assert trusted.validation.accuracy_ok is False
    assert decision.status == "ineligible"
    assert diagnosis is None and risk is None


def test_cooldown_prevents_repeated_risk_card(chain):
    foundation, monitoring, failure, risk_agent = chain
    monitoring._cfg.alert_cooldown_seconds = 300
    first = _run(chain, _row("outer_race_fault", -1))
    repeated = _row("outer_race_fault", -1)
    repeated["telemetry_id"] = "RISK-COOLDOWN-UNIQUE"
    second = _run(chain, repeated)
    assert first[3].assessment_status == "assessed"
    assert second[1].status == "cooldown"
    assert second[2] is None and second[3] is None


def test_complete_provenance_chain_reaches_agent4(chain):
    trusted, decision, diagnosis, risk = _run(chain, _row("outer_race_fault", -1))
    assert risk.source_data_schema_version == trusted.schema_version
    assert risk.source_data_config_version == trusted.config_version
    assert risk.source_master_data_version == trusted.master_data_version
    assert risk.source_monitoring_config_version == decision.monitoring_config_version
    assert risk.source_fi_config_version == diagnosis.fi_config_version
    assert risk.source_taxonomy_version == diagnosis.taxonomy_version
    assert risk.risk_config_version


def test_agent4_rejects_tampered_agent3_taxonomy_provenance(chain):
    trusted, decision, diagnosis, _ = _run(chain, _row("outer_race_fault", -1))
    tampered = diagnosis.model_copy(update={"taxonomy_version": "wrong-taxonomy"})
    risk = chain[3].process(tampered, decision.anomaly_event, trusted)
    assert risk.assessment_status == "invalid_input"
    assert risk.risk_eligible is False
    assert "taxonomy versions" in risk.status_reason


def test_agent3_and_agent4_reject_tampered_event_identity(chain):
    foundation, monitoring, failure, risk_agent = chain
    trusted = foundation.process(_row("outer_race_fault", -1))
    anomaly = monitoring.assess(trusted).anomaly_event
    tampered = anomaly.model_copy(update={"channel_id": "CH-WRONG"})
    diagnosis = failure.process(tampered, trusted)
    risk = risk_agent.process(diagnosis, tampered, trusted)
    assert diagnosis.diagnosis_status == "invalid_input"
    assert risk.assessment_status == "invalid_input"


def test_agent4_does_not_mutate_any_upstream_output(chain):
    foundation, monitoring, failure, risk_agent = chain
    trusted = foundation.process(_row("inner_race_fault", -1))
    decision = monitoring.assess(trusted)
    diagnosis = failure.process(decision.anomaly_event, trusted)
    before = (trusted.to_dict(), decision.to_dict(), diagnosis.to_dict())
    risk_agent.process(diagnosis, decision.anomaly_event, trusted)
    after = (trusted.to_dict(), decision.to_dict(), diagnosis.to_dict())
    assert before == after


def test_complete_output_chain_is_json_serializable(chain):
    trusted, decision, diagnosis, risk = _run(chain, _row("gearbox_fault", -1))
    encoded = json.dumps([
        trusted.to_dict(), decision.to_dict(), diagnosis.to_dict(), risk.to_dict()
    ], ensure_ascii=False)
    assert "rolling_element_fault" in encoded
    assert "0\u201310 days" in encoded or "0–10 days" in encoded


def test_invalid_diagnosis_and_risk_stop_orchestrator_routing(chain):
    trusted, decision, diagnosis, _ = _run(chain, _row("outer_race_fault", -1))
    invalid_diagnosis = diagnosis.model_copy(update={
        "diagnosis_status": "invalid_input", "diagnostic_eligible": False
    })
    invalid_risk = chain[3].process(
        invalid_diagnosis, decision.anomaly_event, trusted
    )
    assert route_after_diagnosis({"fault_diagnosis": invalid_diagnosis}) != "predictive_risk"
    assert route_after_risk({"risk_assessment": invalid_risk}) != "knowledge"


def test_valid_diagnosis_and_risk_follow_orchestrator_routes(chain):
    _, _, diagnosis, risk = _run(chain, _row("outer_race_fault", -1))
    assert route_after_diagnosis({"fault_diagnosis": diagnosis}) == "predictive_risk"
    assert route_after_risk({"risk_assessment": risk}) == "knowledge"


def test_mixed_batch_keeps_risk_cards_isolated(chain):
    results = [
        _run(chain, _row("healthy")),
        _run(chain, _row("outer_race_fault", -1)),
        _run(chain, _row("signal_dropout", 1)),
        _run(chain, _row("gearbox_fault", -1)),
        _run(chain, {}),
    ]
    assert [item[1].status for item in results] == [
        "healthy", "anomaly", "ineligible", "anomaly", "ineligible"
    ]
    assert [item[3].risk_level if item[3] else None for item in results] == [
        None, "critical", None, "critical", None
    ]


def test_duplicate_durable_event_never_creates_second_risk(tmp_path):
    trusted_repo = SQLiteTrustedSignalRepository(str(tmp_path / "risk-trusted.db"))
    monitoring_repo = SQLiteMonitoringDecisionRepository(str(tmp_path / "risk-monitoring.db"))
    foundation = DataFoundationAgent.from_data_files(repository=trusted_repo)
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "risk-duplicate-ewma.json")
    monitoring = MonitoringAgent(cfg, decision_repository=monitoring_repo)
    failure = FailureIntelligenceAgent.from_data_files()
    risk_agent = PredictiveRiskAgent.from_data_files()
    payload = _row("outer_race_fault", -1)

    first_trusted = foundation.process_and_store(deepcopy(payload))
    first_decision = monitoring.assess_and_store(first_trusted)
    first_diagnosis = failure.process(first_decision.anomaly_event, first_trusted)
    first_risk = risk_agent.process(first_diagnosis, first_decision.anomaly_event, first_trusted)

    duplicate = foundation.process_and_store(deepcopy(payload))
    duplicate_decision = monitoring.assess_and_store(duplicate)
    assert first_risk.assessment_status == "assessed"
    assert duplicate.duplicate_detected is True
    assert duplicate_decision.status == "duplicate"
    assert duplicate_decision.anomaly_event is None
    assert trusted_repo.count() == 1


def test_out_of_order_durable_event_never_creates_risk(tmp_path):
    repository = SQLiteTrustedSignalRepository(str(tmp_path / "risk-order.db"))
    foundation = DataFoundationAgent.from_data_files(repository=repository)
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "risk-order-ewma.json")
    monitoring = MonitoringAgent(cfg)
    newer = _row("outer_race_fault", -1)
    newer.update(telemetry_id="RISK-NEW", timestamp_utc="2026-05-21T10:00:00Z")
    monitoring.assess(foundation.process_and_store(newer))
    older = _row("outer_race_fault", -1)
    older.update(telemetry_id="RISK-OLD", timestamp_utc="2026-05-21T09:00:00Z")
    trusted = foundation.process_and_store(older)
    decision = monitoring.assess(trusted)
    assert trusted.out_of_order is True
    assert decision.status == "out_of_order"
    assert decision.anomaly_event is None


def test_equal_timestamp_unique_ids_can_both_reach_risk(tmp_path):
    repository = SQLiteTrustedSignalRepository(str(tmp_path / "risk-equal.db"))
    foundation = DataFoundationAgent.from_data_files(repository=repository)
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "risk-equal-ewma.json")
    chain = (
        foundation, MonitoringAgent(cfg), FailureIntelligenceAgent.from_data_files(),
        PredictiveRiskAgent.from_data_files(),
    )
    first = _row("outer_race_fault", -1)
    first["telemetry_id"] = "RISK-EQUAL-1"
    second = deepcopy(first)
    second["telemetry_id"] = "RISK-EQUAL-2"

    outputs = []
    for payload in (first, second):
        trusted = foundation.process_and_store(payload)
        decision = chain[1].assess(trusted)
        diagnosis = chain[2].process(decision.anomaly_event, trusted)
        outputs.append(chain[3].process(diagnosis, decision.anomaly_event, trusted))
    assert all(result.assessment_status == "assessed" for result in outputs)
# ***********************
