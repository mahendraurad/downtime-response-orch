"""Strict numerical, boundary, ambiguity, and malformed-input tests for Agent 3."""
from copy import deepcopy
import math

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.schemas.anomaly import AnomalyEvent
from src.tools.config_loader import load_fi_config
from src.tools.data_loader import load_fault_taxonomy, load_telemetry_rows
from src.tools.failure_intelligence_utilities import validate_taxonomy
from src.tools.fault_matcher import evaluate_candidates, match_fault_taxonomy


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def rules():
    return load_fault_taxonomy()


@pytest.fixture()
def agent(rules):
    return FailureIntelligenceAgent(deepcopy(rules), load_fi_config())


def _trusted(scenario="outer_race_fault", index=-1):
    row = deepcopy(load_telemetry_rows(scenario)[index])
    return DataFoundationAgent.from_data_files().process(row)


def _anomaly(trusted, **updates):
    values = dict(
        case_id="CASE-EDGE",
        asset_id=trusted.raw.asset_id,
        bearing_id=trusted.raw.bearing_id,
        channel_id=trusted.raw.channel_id,
        timestamp_utc=trusted.raw.timestamp_utc,
        anomaly_score=0.9,
        confidence_score=0.8,
        triggered_features=["vib_rms_mm_s"],
    )
    values.update(updates)
    return AnomalyEvent(**values)


@pytest.mark.parametrize("field", ["anomaly_score", "confidence_score"])
@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_anomaly_score_or_confidence_is_rejected(agent, field, value):
    trusted = _trusted()
    result = agent.process(_anomaly(trusted, **{field: value}), trusted)
    assert result.diagnosis_status == "invalid_input"
    assert field in result.status_reason


def test_missing_case_id_is_rejected(agent):
    trusted = _trusted()
    result = agent.process(_anomaly(trusted, case_id=""), trusted)
    assert result.diagnosis_status == "invalid_input"
    assert "case_id" in result.status_reason


def test_channel_mismatch_is_rejected(agent):
    trusted = _trusted()
    result = agent.process(_anomaly(trusted, channel_id="CH-WRONG"), trusted)
    assert result.diagnosis_status == "invalid_input"
    assert "channel" in result.status_reason


def test_timestamp_mismatch_is_rejected(agent):
    trusted = _trusted()
    result = agent.process(
        _anomaly(trusted, timestamp_utc="2026-05-20T00:00:00Z"), trusted
    )
    assert result.diagnosis_status == "invalid_input"
    assert "timestamps" in result.status_reason


@pytest.mark.parametrize(
    "field",
    ["bpfo_energy", "bpfi_energy", "bsf_energy", "ftf_energy", "kurtosis", "temp_c", "vib_rms_mm_s"],
)
def test_non_finite_telemetry_is_rejected_even_if_forged_eligible(agent, field):
    trusted = _trusted()
    setattr(trusted.raw, field, float("nan"))
    result = agent.process(_anomaly(trusted), trusted)
    assert result.diagnosis_status == "invalid_input"
    assert field in result.status_reason


def test_optional_unrelated_band_may_be_missing(agent):
    trusted = _trusted()
    trusted.raw.bsf_energy = None
    result = agent.process(_anomaly(trusted), trusted)
    assert result.diagnosis_status == "diagnosed"
    assert result.fault_mode == "outer_race_fault"


def test_kurtosis_gate_is_strictly_above_not_inclusive(rules):
    candidates = evaluate_candidates(
        3.5, 0.0, 0.0, 0.0, 5.0, 0.0, False, 1.0, rules
    )
    outer = next(c for c in candidates if c["fault_code"] == "FT_001")
    assert outer["iso_stage"] == 3
    assert outer["matched"] is False


def test_kurtosis_just_above_gate_matches(rules):
    result = match_fault_taxonomy(
        3.5, 0.0, 0.0, 5.000001, 0.0, False, rules
    )
    assert result == ("FT_001", "outer_race_fault", 3)


def test_equal_dominant_bands_are_resolved_by_documented_priority(rules):
    result = match_fault_taxonomy(
        bpfo_energy=0.0,
        bpfi_energy=0.0,
        bsf_energy=3.5,
        ftf_energy=3.5,
        kurtosis=6.0,
        temp_rise=0.0,
        broadband_pattern=False,
        taxonomy_rules=rules,
    )
    assert result == ("FT_006", "cage_fault", 3)


def test_below_every_stage_returns_undetermined(agent):
    trusted = _trusted("healthy", 0)
    trusted.raw.vib_rms_mm_s = trusted.bearing_ctx.baseline_vib_rms_mean
    trusted.raw.bpfo_energy = 0.1
    trusted.raw.bpfi_energy = 0.1
    trusted.raw.bsf_energy = 0.1
    trusted.raw.ftf_energy = 0.1
    trusted.raw.kurtosis = 2.0
    result = agent.process(_anomaly(trusted), trusted)
    assert result.diagnosis_status == "undetermined"
    assert result.fault_code == ""
    assert result.iso_stage == 0


def test_missing_vibration_baseline_prevents_false_lubrication_match(agent):
    trusted = _trusted("healthy", 0)
    trusted.bearing_ctx.baseline_vib_rms_mean = 0.0
    trusted.raw.vib_rms_mm_s = 10.0
    trusted.raw.temp_c = trusted.bearing_ctx.baseline_temp_mean + 20
    trusted.raw.bpfo_energy = 0.1
    trusted.raw.bpfi_energy = 0.1
    trusted.raw.kurtosis = 3.0
    result = agent.process(_anomaly(trusted), trusted)
    assert result.fault_mode == "undetermined"


def test_no_temperature_rise_prevents_lubrication_match(agent):
    trusted = _trusted("healthy", 0)
    trusted.raw.vib_rms_mm_s = trusted.bearing_ctx.baseline_vib_rms_mean * 2
    trusted.raw.temp_c = trusted.bearing_ctx.baseline_temp_mean
    trusted.raw.bpfo_energy = 0.1
    trusted.raw.bpfi_energy = 0.1
    trusted.raw.kurtosis = 3.0
    result = agent.process(_anomaly(trusted), trusted)
    assert result.fault_mode == "undetermined"


def test_malformed_batch_item_does_not_abort_valid_item(agent):
    trusted = _trusted()
    results = agent.process_batch([(_anomaly(trusted), trusted), "bad-item", (None,)])
    assert len(results) == 3
    assert results[0].diagnosis_status == "diagnosed"
    assert [r.diagnosis_status for r in results[1:]] == ["invalid_input", "invalid_input"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda r: r[0].update(detection="not-an-object"), "detection must be an object"),
        (lambda r: r[0].update(stage_1_vib_multiple=float("nan")), "finite positive"),
        (lambda r: r[0].update(stage_1_vib_multiple=True), "finite positive"),
        (lambda r: r[0].update(rul_days_stage_2=float("inf")), "positive RUL"),
        (lambda r: r[0].update(rul_days_stage_2=1000), "must not increase"),
        (lambda r: r[5]["detection"].update(dominant_among=["ftf_energy", "ftf_energy"]), "invalid dominant_among"),
    ],
)
def test_additional_malformed_taxonomy_edges_fail_fast(rules, mutation, message):
    broken = deepcopy(rules)
    mutation(broken)
    with pytest.raises(ValueError, match=message):
        validate_taxonomy(broken)


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.confidence_weights.update({"extra": 0.0}),
        lambda c: c.confidence_weights.update(anomaly_confidence=-0.1, match_strength=1.1),
        lambda c: c.severity_stage_base.update({"1": "unknown-label"}),
        lambda c: setattr(c, "undetermined_severity", "unknown-label"),
    ],
)
def test_invalid_agent_policy_fails_validation(change):
    config = deepcopy(load_fi_config())
    change(config)
    with pytest.raises(ValueError):
        config.validate()
# ***********************
