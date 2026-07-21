"""Failure-safe optional LLM advisory and HITL tests for Agent 4."""
from copy import deepcopy

import pytest

from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.tools.config_loader import load_risk_config
from src.tools.data_loader import load_fault_taxonomy
from tests.test_predictive_risk import _anomaly, _asset_ctx, _diagnosis, _trusted


# ************** Added by Prateek Mittal on 20th July 2026 ******************
class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def is_configured(self):
        return True

    def complete_json(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _agent(fake, handler=None):
    cfg = load_risk_config()
    cfg.llm_enabled = True
    cfg.llm_trigger_on_undetermined = True
    return PredictiveRiskAgent(
        load_fault_taxonomy(), cfg, llm_client=fake, hitl_handler=handler
    )


def _inputs():
    return (
        _diagnosis(0, "", "medium"),
        _anomaly(0.9),
        _trusted(_asset_ctx("A", "high", True, 18000)),
    )


def test_default_config_disables_optional_llm():
    assert load_risk_config().llm_enabled is False


def test_llm_exception_preserves_deterministic_monitor_card():
    fake = FakeLLM(error=TimeoutError("provider timeout"))
    result = _agent(fake).process(*_inputs())
    assert fake.calls == 1
    assert result.assessment_status == "monitor"
    assert result.assessment_source == "rules"
    assert result.rul_band_label == "monitor"


def test_hitl_rejection_preserves_rules():
    fake = FakeLLM({"risk_level": "high", "recommended_action": "Inspect"})
    result = _agent(fake, handler=lambda *args: None).process(*_inputs())
    assert result.assessment_source == "rules"
    assert result.advisory_note == ""


def test_hitl_modification_is_applied_only_to_advisory_fields():
    fake = FakeLLM({"risk_level": "critical", "recommended_action": "Original"})

    def modify(*args):
        return {
            "risk_level": "high",
            "recommended_action": "Operator-approved inspection",
            "rationale": "Reviewed by reliability engineer",
        }

    result = _agent(fake, handler=modify).process(*_inputs())
    assert result.assessment_source == "rules+llm_fallback"
    assert result.risk_level == "high"
    assert "Operator-approved" in result.advisory_note
    assert result.rul_band_label == "monitor"
    assert result.financial_exposure == 0


def test_hitl_exception_discards_advisory():
    fake = FakeLLM({"risk_level": "high", "recommended_action": "Inspect"})

    def broken(*args):
        raise RuntimeError("review system unavailable")

    result = _agent(fake, handler=broken).process(*_inputs())
    assert result.assessment_source == "rules"
    assert result.advisory_note == ""


@pytest.mark.parametrize("response", [{}, {"risk_level": "urgent"}, {"unexpected": "value"}])
def test_unusable_llm_response_does_not_claim_fallback(response):
    result = _agent(FakeLLM(response)).process(*_inputs())
    assert result.assessment_source == "rules"
    assert result.advisory_note == ""


def test_invalid_input_never_calls_llm():
    fake = FakeLLM({"risk_level": "high"})
    diagnosis, anomaly, trusted = _inputs()
    diagnosis = diagnosis.model_copy(update={
        "diagnosis_status": "invalid_input", "diagnostic_eligible": False
    })
    result = _agent(fake).process(diagnosis, anomaly, trusted)
    assert fake.calls == 0
    assert result.assessment_status == "invalid_input"
# ***********************
