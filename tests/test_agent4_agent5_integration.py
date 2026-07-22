"""Focused Agent 4 -> Agent 5 grounding and provenance integration tests."""
from copy import deepcopy

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.knowledge_agent import KnowledgeAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def pipeline(tmp_path):
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "agent45-ewma.json")
    return (
        DataFoundationAgent.from_data_files(), MonitoringAgent(cfg),
        FailureIntelligenceAgent.from_data_files(), PredictiveRiskAgent.from_data_files(),
        KnowledgeAgent(),
    )


def _run(pipeline, scenario, index=-1):
    foundation, monitoring, failure, risk_agent, knowledge = pipeline
    trusted = foundation.process(deepcopy(load_telemetry_rows(scenario)[index]))
    anomaly = monitoring.assess(trusted).anomaly_event
    diagnosis = failure.process(anomaly, trusted)
    risk = risk_agent.process(diagnosis, anomaly, trusted)
    guidance = knowledge.process(diagnosis, trusted, risk)
    return trusted, anomaly, diagnosis, risk, guidance


@pytest.mark.parametrize(
    ("scenario", "fault"),
    [
        ("outer_race_fault", "outer_race_fault"),
        ("inner_race_fault", "inner_race_fault"),
        ("lubrication_issue", "lubrication_issue"),
        ("gearbox_fault", "rolling_element_fault"),
    ],
)
def test_assessed_risk_retrieves_grounded_fault_guidance(pipeline, scenario, fault):
    _, _, diagnosis, risk, guidance = _run(pipeline, scenario)
    assert diagnosis.fault_mode == fault
    assert risk.risk_eligible is True
    assert guidance.guidance_status == "grounded"
    assert guidance.case_id == risk.case_id == diagnosis.case_id
    assert guidance.source_documents
    assert guidance.grounded_items


def test_monitor_risk_produces_explicit_no_guidance(pipeline):
    _, _, diagnosis, risk, guidance = _run(pipeline, "fi_hitl_test", 0)
    assert diagnosis.diagnosis_status == "undetermined"
    assert risk.assessment_status == "monitor"
    assert guidance.guidance_status == "no_guidance"
    assert guidance.guidance_eligible is False


def test_invalid_risk_is_rejected_by_knowledge(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    invalid = risk.model_copy(update={
        "assessment_status": "invalid_input", "risk_eligible": False,
    })
    guidance = pipeline[4].process(diagnosis, trusted, invalid)
    assert guidance.guidance_status == "invalid_input"


def test_risk_context_is_present_in_retrieval_query(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "gearbox_fault")
    captured = {}
    agent = KnowledgeAgent(retriever_fn=lambda **kwargs: captured.update(kwargs) or [])
    agent.process(diagnosis, trusted, risk)
    assert f"risk {risk.risk_level}" in captured["query"]


def test_agent4_to_agent5_provenance_is_complete(pipeline):
    trusted, anomaly, diagnosis, risk, guidance = _run(pipeline, "outer_race_fault")
    assert guidance.source_risk_schema_version == risk.schema_version
    assert guidance.source_risk_config_version == risk.risk_config_version
    assert guidance.source_diagnosis_schema_version == diagnosis.schema_version
    assert guidance.source_fi_config_version == diagnosis.fi_config_version
    assert guidance.source_taxonomy_version == diagnosis.taxonomy_version
    assert guidance.source_monitoring_config_version == anomaly.monitoring_config_version
    assert guidance.source_data_config_version == trusted.config_version
    assert guidance.source_master_data_version == trusted.master_data_version


def test_agent5_does_not_mutate_agent4_or_earlier_outputs(pipeline):
    trusted, anomaly, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    before = (trusted.to_dict(), anomaly.to_dict(), diagnosis.to_dict(), risk.to_dict())
    pipeline[4].process(diagnosis, trusted, risk)
    after = (trusted.to_dict(), anomaly.to_dict(), diagnosis.to_dict(), risk.to_dict())
    assert before == after
# ***********************
