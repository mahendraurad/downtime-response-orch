"""Focused integration certification for the Agent 3 -> Agent 4 boundary."""
from copy import deepcopy

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def pipeline(tmp_path):
    foundation = DataFoundationAgent.from_data_files()
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "agent34-ewma.json")
    return (
        foundation,
        MonitoringAgent(cfg),
        FailureIntelligenceAgent.from_data_files(),
        PredictiveRiskAgent.from_data_files(),
    )


def _run(pipeline, scenario, index=-1):
    foundation, monitoring, failure, risk = pipeline
    row = deepcopy(load_telemetry_rows(scenario)[index])
    trusted = foundation.process(row)
    anomaly = monitoring.assess(trusted).anomaly_event
    diagnosis = failure.process(anomaly, trusted)
    return trusted, anomaly, diagnosis, risk.process(diagnosis, anomaly, trusted)


@pytest.mark.parametrize(
    ("scenario", "fault", "rul_max"),
    [
        ("outer_race_fault", "outer_race_fault", 7),
        ("inner_race_fault", "inner_race_fault", 5),
        ("lubrication_issue", "lubrication_issue", 20),
        ("gearbox_fault", "rolling_element_fault", 10),
    ],
)
def test_diagnosed_faults_produce_taxonomy_risk_cards(pipeline, scenario, fault, rul_max):
    _, anomaly, diagnosis, risk = _run(pipeline, scenario)
    assert diagnosis.fault_mode == fault
    assert risk.assessment_status == "assessed"
    assert risk.risk_eligible is True
    assert risk.case_id == diagnosis.case_id == anomaly.case_id
    assert risk.rul_max_days == rul_max
    assert risk.risk_level == diagnosis.severity
    assert risk.confidence == diagnosis.confidence
    assert risk.failure_probability > 0
    assert risk.risk_explanation


def test_undetermined_diagnosis_produces_monitor_card(pipeline):
    _, _, diagnosis, risk = _run(pipeline, "fi_hitl_test", 0)
    assert diagnosis.diagnosis_status == "undetermined"
    assert risk.assessment_status == "monitor"
    assert risk.risk_eligible is True
    assert risk.rul_band_label == "monitor"
    assert risk.financial_exposure == 0
    assert "no near-term failure window" in risk.risk_explanation


def test_agent3_invalid_input_is_blocked_by_agent4(pipeline):
    foundation, monitoring, failure, risk_agent = pipeline
    trusted = foundation.process(deepcopy(load_telemetry_rows("outer_race_fault")[-1]))
    anomaly = monitoring.assess(trusted).anomaly_event
    bad_anomaly = anomaly.model_copy(update={"bearing_id": "BRG-WRONG"})
    diagnosis = failure.process(bad_anomaly, trusted)
    risk = risk_agent.process(diagnosis, bad_anomaly, trusted)
    assert diagnosis.diagnosis_status == "invalid_input"
    assert risk.assessment_status == "invalid_input"
    assert risk.risk_eligible is False


def test_provenance_links_agent3_and_agent4(pipeline):
    trusted, anomaly, diagnosis, risk = _run(pipeline, "outer_race_fault")
    assert risk.source_diagnosis_schema_version == diagnosis.schema_version
    assert risk.source_fi_config_version == diagnosis.fi_config_version
    assert risk.source_taxonomy_version == diagnosis.taxonomy_version
    assert risk.taxonomy_version == diagnosis.taxonomy_version
    assert risk.source_monitoring_config_version == anomaly.monitoring_config_version
    assert risk.source_data_config_version == trusted.config_version
    assert risk.source_master_data_version == trusted.master_data_version


def test_high_critical_bottleneck_is_flagged(pipeline):
    trusted, _, diagnosis, risk = _run(pipeline, "gearbox_fault")
    assert trusted.asset_ctx.is_bottleneck is True
    assert diagnosis.severity == "critical"
    assert risk.business_impact_flag is True
    assert risk.financial_exposure == 10 * 24 * trusted.asset_ctx.downtime_cost_per_hour


def test_high_critical_non_bottleneck_is_still_business_flagged(pipeline):
    trusted, _, _, risk = _run(pipeline, "lubrication_issue")
    assert trusted.asset_ctx.is_bottleneck is False
    assert trusted.asset_ctx.criticality == "high"
    assert risk.business_impact_flag is True


def test_agent4_does_not_mutate_agent3_or_upstream_inputs(pipeline):
    foundation, monitoring, failure, risk_agent = pipeline
    trusted = foundation.process(deepcopy(load_telemetry_rows("outer_race_fault")[-1]))
    anomaly = monitoring.assess(trusted).anomaly_event
    diagnosis = failure.process(anomaly, trusted)
    before = (trusted.to_dict(), anomaly.to_dict(), diagnosis.to_dict())
    risk_agent.process(diagnosis, anomaly, trusted)
    after = (trusted.to_dict(), anomaly.to_dict(), diagnosis.to_dict())
    assert before == after
# ***********************
