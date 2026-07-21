"""Focused integration tests for the Agent 2 -> Agent 3 boundary."""
from copy import deepcopy

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows


# ************** Added by Prateek Mittal on 20th July 2026 ******************
# Agent 3 is invoked only for an explicit Agent 2 anomaly. Healthy, suppressed,
# and Agent-1-ineligible decisions are verified at the same typed boundary.
@pytest.fixture()
def pipeline(tmp_path):
    dfa = DataFoundationAgent.from_data_files()
    config = load_monitoring_config()
    config.ewma_state_file = str(tmp_path / "agent23-ewma.json")
    return dfa, MonitoringAgent(config), FailureIntelligenceAgent.from_data_files()


def _row(scenario, index=0):
    return deepcopy(load_telemetry_rows(scenario)[index])


def test_anomaly_decision_is_diagnosed_with_linked_identity(pipeline):
    dfa, monitoring, failure = pipeline
    trusted = dfa.process(_row("outer_race_fault", -1))
    decision = monitoring.assess(trusted)
    diagnosis = failure.process(decision.anomaly_event, trusted)
    assert decision.status == "anomaly"
    assert diagnosis.diagnosis_status == "diagnosed"
    assert diagnosis.case_id == decision.anomaly_event.case_id
    assert diagnosis.asset_id == decision.anomaly_event.asset_id
    assert diagnosis.bearing_id == decision.anomaly_event.bearing_id


def test_agent2_provenance_reaches_agent3(pipeline):
    dfa, monitoring, failure = pipeline
    trusted = dfa.process(_row("inner_race_fault", -1))
    decision = monitoring.assess(trusted)
    diagnosis = failure.process(decision.anomaly_event, trusted)
    assert diagnosis.source_monitoring_config_version == decision.monitoring_config_version
    assert diagnosis.source_detector_version == decision.anomaly_event.detector_version
    assert diagnosis.source_config_version == trusted.config_version
    assert diagnosis.source_master_data_version == trusted.master_data_version


def test_healthy_decision_does_not_create_diagnosis(pipeline):
    dfa, monitoring, _ = pipeline
    decision = monitoring.assess(dfa.process(_row("healthy")))
    assert decision.status == "healthy"
    assert decision.anomaly_event is None


def test_suppressed_decision_does_not_create_diagnosis(pipeline):
    dfa, monitoring, _ = pipeline
    decision = monitoring.assess(dfa.process(_row("startup_filter")))
    assert decision.status == "suppressed"
    assert decision.anomaly_event is None


def test_ineligible_agent1_record_does_not_create_diagnosis(pipeline):
    dfa, monitoring, _ = pipeline
    decision = monitoring.assess(dfa.process(_row("signal_dropout", 1)))
    assert decision.status == "ineligible"
    assert decision.anomaly_event is None


def test_bsf_on_gearbox_asset_maps_to_rolling_element_fault(pipeline):
    dfa, monitoring, failure = pipeline
    trusted = dfa.process(_row("gearbox_fault", -1))
    decision = monitoring.assess(trusted)
    diagnosis = failure.process(decision.anomaly_event, trusted)
    assert diagnosis.fault_code == "FT_007"
    assert diagnosis.fault_mode == "rolling_element_fault"
    assert diagnosis.evidence["dominant_band"] == "BSF"
# ***********************
