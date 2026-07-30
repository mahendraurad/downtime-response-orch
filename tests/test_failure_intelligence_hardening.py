"""Comprehensive contract, validation, and provenance tests for Agent 3."""
from copy import deepcopy

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.schemas.anomaly import AnomalyEvent
from src.tools.config_loader import load_fi_config
from src.tools.data_loader import load_fault_taxonomy, load_telemetry_rows
from src.tools.failure_intelligence_utilities import validate_taxonomy


# ************** Added by Prateek Mittal on 20th July 2026 ******************
# These tests cover Agent 3 independently from the existing end-to-end scenario
# tests. Together they verify both the decision logic and its public boundary.
@pytest.fixture()
def rules():
    return load_fault_taxonomy()


@pytest.fixture()
def agent(rules):
    return FailureIntelligenceAgent(deepcopy(rules), load_fi_config())


@pytest.fixture()
def trusted():
    row = deepcopy(load_telemetry_rows("outer_race_fault")[4])
    return DataFoundationAgent.from_data_files().process(row)


@pytest.fixture()
def anomaly(trusted):
    return AnomalyEvent(
        case_id="CASE-A3-001",
        asset_id=trusted.raw.asset_id,
        bearing_id=trusted.raw.bearing_id,
        channel_id=trusted.raw.channel_id,
        timestamp_utc=trusted.raw.timestamp_utc,
        anomaly_score=0.95,
        confidence_score=0.9,
        triggered_features=["bpfo_energy", "kurtosis"],
        monitoring_config_version="mon-version",
        detector_version="detector-version",
        source_schema_version=trusted.schema_version,
        source_config_version=trusted.config_version,
        source_master_data_version=trusted.master_data_version,
    )


def test_invalid_anomaly_type_is_explicit(agent, trusted):
    result = agent.process(None, trusted)
    assert result.diagnosis_status == "invalid_input"
    assert result.diagnostic_eligible is False
    assert result.confidence == 0.0
    assert "AnomalyEvent" in result.status_reason


def test_invalid_trusted_type_is_explicit(agent, anomaly):
    result = agent.process(anomaly, None)
    assert result.diagnosis_status == "invalid_input"
    assert result.diagnostic_eligible is False
    assert "TrustedBearingSignal" in result.status_reason


def test_ineligible_agent1_signal_is_rejected(agent, anomaly, trusted):
    trusted.downstream_eligible = False
    result = agent.process(anomaly, trusted)
    assert result.diagnosis_status == "invalid_input"
    assert "not downstream eligible" in result.status_reason


def test_identity_mismatch_is_rejected(agent, anomaly, trusted):
    bad = anomaly.model_copy(update={"bearing_id": "BRG-WRONG"})
    result = agent.process(bad, trusted)
    assert result.diagnosis_status == "invalid_input"
    assert "identities do not match" in result.status_reason


def test_diagnosed_result_has_stable_provenance(agent, anomaly, trusted, rules):
    first = agent.process(anomaly, trusted)
    second_agent = FailureIntelligenceAgent(deepcopy(rules), load_fi_config())
    second = second_agent.process(anomaly, trusted)
    assert first.diagnosis_status == "diagnosed"
    assert first.fi_config_version == second.fi_config_version
    assert first.taxonomy_version == second.taxonomy_version
    assert len(first.fi_config_version) == 16
    assert first.source_monitoring_config_version == "mon-version"
    assert first.source_detector_version == "detector-version"
    assert first.source_master_data_version == trusted.master_data_version


def test_taxonomy_change_changes_version(agent, anomaly, trusted, rules):
    changed = deepcopy(rules)
    changed[0]["typical_causes"].append("test-only cause")
    changed_agent = FailureIntelligenceAgent(changed, load_fi_config())
    assert (
        agent.process(anomaly, trusted).taxonomy_version
        != changed_agent.process(anomaly, trusted).taxonomy_version
    )


def test_output_serializes_new_contract_fields(agent, anomaly, trusted):
    payload = agent.process(anomaly, trusted).to_dict()
    for field in (
        "diagnosis_status", "diagnostic_eligible", "status_reason",
        "schema_version", "fi_config_version", "taxonomy_version",
        "source_monitoring_config_version", "source_detector_version",
    ):
        assert field in payload


def test_rolling_element_rule_has_roadmap_vocabulary(rules):
    rule = next(r for r in rules if r["fault_code"] == "FT_007")
    assert rule["fault_mode"] == "rolling_element_fault"
    assert rule["detection"]["signal"] == "bsf_energy"


def test_rolling_element_checks_are_configured(agent):
    checks = agent._recommended_checks("FT_007", [])
    assert checks
    assert any("BSF" in check for check in checks)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda r: r[1].update(fault_code=r[0]["fault_code"]), "duplicate fault_code"),
        (lambda r: r[1].update(fault_mode=r[0]["fault_mode"]), "duplicate fault_mode"),
        (lambda r: r[1]["detection"].update(priority=r[0]["detection"]["priority"]), "unique priorities"),
        (lambda r: r[0]["detection"].update(signal="misspelled_band"), "unsupported detection signal"),
        (lambda r: r[0].update(stage_1_vib_multiple=4.0), "monotonic"),
        (lambda r: r[0].update(rul_days_stage_3=0), "positive RUL"),
        (lambda r: r[0]["detection"]["kurtosis"].update(mode="sideways"), "unsupported kurtosis mode"),
        (lambda r: r[2]["detection"]["kurtosis"].update(ceiling={"fault_code": "MISSING", "field": "x"}), "unresolved kurtosis ceiling"),
    ],
)
def test_invalid_taxonomy_fails_fast(rules, mutation, message):
    broken = deepcopy(rules)
    mutation(broken)
    with pytest.raises(ValueError, match=message):
        validate_taxonomy(broken)


def test_empty_taxonomy_fails_fast():
    with pytest.raises(ValueError, match="at least one rule"):
        validate_taxonomy([])


def test_batch_preserves_one_result_per_pair(agent, anomaly, trusted):
    invalid = anomaly.model_copy(update={"bearing_id": "BRG-WRONG"})
    results = agent.process_batch([(anomaly, trusted), (invalid, trusted)])
    assert [r.diagnosis_status for r in results] == ["diagnosed", "invalid_input"]
# ***********************
