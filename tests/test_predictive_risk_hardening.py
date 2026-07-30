"""Strict contract, numerical, provenance, and policy tests for Agent 4."""
from copy import deepcopy

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.tools.config_loader import load_monitoring_config, load_risk_config
from src.tools.data_loader import load_fault_taxonomy, load_telemetry_rows
from src.tools.rul_calculator import compute_financial_exposure, get_rul_band


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def pipeline(tmp_path):
    foundation = DataFoundationAgent.from_data_files()
    mon_cfg = load_monitoring_config()
    mon_cfg.ewma_state_file = str(tmp_path / "risk-hardening-ewma.json")
    monitoring = MonitoringAgent(mon_cfg)
    failure = FailureIntelligenceAgent.from_data_files()
    risk = PredictiveRiskAgent.from_data_files()
    return foundation, monitoring, failure, risk


@pytest.fixture()
def valid_inputs(pipeline):
    foundation, monitoring, failure, _ = pipeline
    row = deepcopy(load_telemetry_rows("outer_race_fault")[-1])
    trusted = foundation.process(row)
    anomaly = monitoring.assess(trusted).anomaly_event
    diagnosis = failure.process(anomaly, trusted)
    return diagnosis, anomaly, trusted


def test_valid_result_has_explicit_status_and_explanation(pipeline, valid_inputs):
    result = pipeline[3].process(*valid_inputs)
    assert result.assessment_status == "assessed"
    assert result.risk_eligible is True
    assert result.status_reason
    assert "ISO stage 3" in result.risk_explanation
    assert result.evidence["rul_source"] == "fault_taxonomy"


@pytest.mark.parametrize("position", [0, 1, 2])
def test_wrong_input_type_returns_invalid(pipeline, valid_inputs, position):
    values = list(valid_inputs)
    values[position] = None
    result = pipeline[3].process(*values)
    assert result.assessment_status == "invalid_input"
    assert result.risk_eligible is False
    assert result.confidence == 0.0


def test_invalid_agent3_diagnosis_is_not_treated_as_monitor(pipeline, valid_inputs):
    diagnosis, anomaly, trusted = valid_inputs
    diagnosis = diagnosis.model_copy(update={
        "diagnosis_status": "invalid_input", "diagnostic_eligible": False
    })
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"
    assert result.rul_band_label == ""


def test_ineligible_agent1_signal_is_rejected(pipeline, valid_inputs):
    diagnosis, anomaly, trusted = valid_inputs
    trusted.downstream_eligible = False
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"
    assert "Agent 1" in result.status_reason


@pytest.mark.parametrize(
    ("target", "changes", "message"),
    [
        ("anomaly", {"case_id": "CASE-WRONG"}, "case identities"),
        ("diagnosis", {"asset_id": "ASSET-WRONG"}, "diagnosis and trusted"),
        ("anomaly", {"bearing_id": "BRG-WRONG"}, "anomaly and trusted"),
        ("anomaly", {"channel_id": "CH-WRONG"}, "channel identities"),
        ("anomaly", {"timestamp_utc": "2026-01-01T00:00:00Z"}, "timestamps"),
    ],
)
def test_cross_agent_identity_mismatch_is_rejected(
    pipeline, valid_inputs, target, changes, message
):
    diagnosis, anomaly, trusted = valid_inputs
    if target == "diagnosis":
        diagnosis = diagnosis.model_copy(update=changes)
    else:
        anomaly = anomaly.model_copy(update=changes)
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"
    assert message in result.status_reason


@pytest.mark.parametrize("field", ["anomaly_score", "confidence_score"])
@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_anomaly_numbers_are_rejected(pipeline, valid_inputs, field, value):
    diagnosis, anomaly, trusted = valid_inputs
    anomaly = anomaly.model_copy(update={field: value})
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_diagnosis_confidence_is_rejected(pipeline, valid_inputs, value):
    diagnosis, anomaly, trusted = valid_inputs
    diagnosis = diagnosis.model_copy(update={"confidence": value})
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"iso_stage": 4}, "iso_stage"),
        ({"iso_stage": 0}, "diagnosis_status undetermined"),
        ({"fault_code": ""}, "must carry a fault_code"),
        ({"fault_code": "FT_999"}, "not present"),
        ({"fault_mode": "inner_race_fault"}, "does not match"),
        ({"severity": "urgent"}, "severity"),
        ({"diagnosis_status": "undetermined"}, "diagnosis_status diagnosed"),
    ],
)
def test_incoherent_diagnosis_is_rejected(pipeline, valid_inputs, changes, message):
    diagnosis, anomaly, trusted = valid_inputs
    diagnosis = diagnosis.model_copy(update=changes)
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"
    assert message in result.status_reason


def test_taxonomy_version_mismatch_is_rejected(pipeline, valid_inputs):
    diagnosis, anomaly, trusted = valid_inputs
    diagnosis = diagnosis.model_copy(update={"taxonomy_version": "stale-version"})
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"
    assert "taxonomy versions" in result.status_reason


@pytest.mark.parametrize("cost", [-1, float("nan"), float("inf")])
def test_invalid_downtime_cost_is_rejected(pipeline, valid_inputs, cost):
    diagnosis, anomaly, trusted = valid_inputs
    trusted.asset_ctx.downtime_cost_per_hour = cost
    result = pipeline[3].process(diagnosis, anomaly, trusted)
    assert result.assessment_status == "invalid_input"
    assert "downtime_cost" in result.status_reason


def test_provenance_is_populated_and_stable(pipeline, valid_inputs):
    first = pipeline[3].process(*valid_inputs)
    second = PredictiveRiskAgent.from_data_files().process(*valid_inputs)
    assert first.risk_config_version == second.risk_config_version
    assert first.taxonomy_version == second.taxonomy_version
    assert len(first.risk_config_version) == 16
    assert first.source_taxonomy_version == first.taxonomy_version
    assert first.linked_anomaly_case_id == first.linked_diagnosis_case_id == first.case_id


def test_all_new_fields_serialize(pipeline, valid_inputs):
    payload = pipeline[3].process(*valid_inputs).to_dict()
    for field in (
        "assessment_status", "risk_eligible", "status_reason", "risk_explanation",
        "evidence", "schema_version", "risk_config_version", "taxonomy_version",
        "source_diagnosis_schema_version", "linked_anomaly_case_id",
    ):
        assert field in payload


def test_batch_isolates_malformed_items(pipeline, valid_inputs):
    results = pipeline[3].process_batch([valid_inputs, "bad", (None,), [1, 2, 3]])
    assert results[0].assessment_status == "assessed"
    assert [r.assessment_status for r in results[1:]] == [
        "invalid_input", "invalid_input", "invalid_input"
    ]


def test_failure_probability_and_health_index_are_bounded(pipeline, valid_inputs):
    result = pipeline[3].process(*valid_inputs)
    assert 0 <= result.failure_probability <= 1
    assert 0 <= result.health_index <= 1
    assert result.failure_probability + result.health_index == pytest.approx(1.0)


def test_financial_exposure_rejects_no_values_but_math_is_exact():
    assert compute_financial_exposure(10, 18000) == 4_320_000
    assert compute_financial_exposure(0, 18000) == 0


@pytest.mark.parametrize("fault_code", ["FT_001", "FT_002", "FT_003", "FT_006", "FT_007"])
def test_rul_gets_shorter_as_stage_increases(fault_code):
    rules = load_fault_taxonomy()
    stage1 = get_rul_band(1, fault_code, rules)
    stage2 = get_rul_band(2, fault_code, rules)
    stage3 = get_rul_band(3, fault_code, rules)
    assert stage3[1] <= stage2[1] <= stage1[1]
    assert stage3[0] <= stage2[0] <= stage1[0]


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.fp_stage_base.pop("0"),
        lambda c: c.fp_stage_base.update({"2": float("nan")}),
        lambda c: c.fp_stage_base.update({"2": 0.1}),
        lambda c: c.fp_weights.update({"extra": 0.0}),
        lambda c: c.fp_weights.update(stage_base=-0.1, anomaly_score=1.1),
        lambda c: setattr(c, "risk_level_source", "something_else"),
        lambda c: setattr(c, "risk_level_fallback", "urgent"),
        lambda c: setattr(c, "monitor_rul_min", -1),
        lambda c: setattr(c, "monitor_rul_max", 1),
        lambda c: setattr(c, "monitor_label", ""),
        lambda c: setattr(c, "llm_low_confidence_below", 2.0),
        lambda c: setattr(c, "llm_timeout_seconds", 0),
    ],
)
def test_invalid_risk_configuration_fails_fast(change):
    cfg = deepcopy(load_risk_config())
    change(cfg)
    with pytest.raises(ValueError):
        cfg.validate()
# ***********************
