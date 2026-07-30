"""Agent 6 unit, edge, LLM-safety, and Agent 5->6 boundary certification."""
from copy import deepcopy
import json

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.knowledge_agent import KnowledgeAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.agents.prescriptive_optimization_agent import PrescriptiveOptimizationAgent
from src.tools.config_loader import load_monitoring_config, load_prescriptive_config
from src.tools.data_loader import load_telemetry_rows


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def upstream(tmp_path):
    payload = deepcopy(load_telemetry_rows("outer_race_fault")[-1])
    trusted = DataFoundationAgent.from_data_files().process(payload)
    cfg = load_monitoring_config(); cfg.ewma_state_file = str(tmp_path / "a6-ewma.json")
    anomaly = MonitoringAgent(cfg).assess(trusted).anomaly_event
    diagnosis = FailureIntelligenceAgent.from_data_files().process(anomaly, trusted)
    risk = PredictiveRiskAgent.from_data_files().process(diagnosis, anomaly, trusted)
    guidance = KnowledgeAgent().process(diagnosis, trusted, risk)
    return risk, diagnosis, guidance


def _run(upstream, **context):
    risk, diagnosis, guidance = upstream
    inventory = {guidance.bearing_type: {"qty_on_hand": 2, "lead_time_days": 5}}
    default_context = {"planned_stop_windows": [
        {"window_id": "WIN-1", "duration_hours": 8, "available": True}
    ]}
    default_context.update(context)
    return PrescriptiveOptimizationAgent().process(
        risk, diagnosis, guidance, inventory, default_context
    )


def test_valid_agent5_handoff_returns_ranked_recommendation(upstream):
    result = _run(upstream)
    assert result.recommendation_status == "ok"
    assert result.recommendation_eligible is True
    assert result.recommended_action.name
    assert result.ranked_alternatives[0]["rank"] == 1


@pytest.mark.parametrize(
    ("level", "action", "urgency"),
    [("low", "continue_monitoring", "monitor"),
     ("medium", "inspect_next_shift", "planned"),
     ("high", "repair_next_planned_stop", "urgent"),
     ("critical", "stop_and_replace", "immediate")],
)
def test_each_risk_level_has_expected_primary_policy(upstream, level, action, urgency):
    risk = upstream[0].model_copy(deep=True)
    risk.risk_level = level
    result = PrescriptiveOptimizationAgent().process(
        risk, upstream[1], upstream[2],
        {upstream[2].bearing_type: {"qty_on_hand": 2}},
        {"planned_stop_windows": [{"window_id": "W", "duration_hours": 8}]},
    )
    assert result.recommended_action.name == action
    assert result.urgency == urgency


def test_critical_business_impact_forces_stop_and_replace(upstream):
    risk = upstream[0].model_copy(deep=True)
    risk.risk_level = "critical"; risk.business_impact_flag = True
    result = PrescriptiveOptimizationAgent().process(risk, upstream[1], upstream[2],
        {upstream[2].bearing_type: 1}, {})
    assert result.recommended_action.name == "stop_and_replace"
    assert result.approval_required is True


def test_missing_part_flags_procurement_and_preserves_lead_time(upstream):
    result = PrescriptiveOptimizationAgent().process(
        upstream[0], upstream[1], upstream[2],
        {upstream[2].bearing_type: {"qty_on_hand": 0, "lead_time_days": 12}},
        {"planned_stop_windows": [{"window_id": "W", "duration_hours": 8}]},
    )
    assert result.procurement_required is True
    assert result.required_parts[0].lead_time_days == 12
    assert "procurement" in result.rationale.lower()


def test_short_window_is_rejected_and_safe_alternative_selected(upstream):
    risk = upstream[0].model_copy(deep=True); risk.risk_level = "high"
    result = PrescriptiveOptimizationAgent().process(
        risk, upstream[1], upstream[2], {upstream[2].bearing_type: 2},
        {"planned_stop_windows": [
            {"window_id": "TOO-SHORT", "duration_hours": 1, "available": True}
        ]},
    )
    assert result.window_chosen == ""
    assert result.recommended_action.name == "derate_and_monitor"


def test_unavailable_window_is_not_selected(upstream):
    result = _run(upstream, planned_stop_windows=[
        {"window_id": "BUSY", "duration_hours": 10, "available": False}
    ])
    assert result.window_chosen == ""


def test_valid_window_is_selected(upstream):
    assert _run(upstream).window_chosen == "WIN-1"


def test_monitor_action_is_preapproved(upstream):
    risk = upstream[0].model_copy(deep=True); risk.risk_level = "low"
    result = PrescriptiveOptimizationAgent().process(risk, upstream[1], upstream[2], {}, {})
    assert result.approval_required is False
    assert result.approval_status == "approved"


def test_repair_action_requires_human_approval(upstream):
    result = _run(upstream)
    assert result.approval_required is True
    assert result.approval_status == "pending"


@pytest.mark.parametrize("bad", [None, {}, "bad", 4, []])
def test_malformed_risk_returns_explicit_invalid_result(upstream, bad):
    result = PrescriptiveOptimizationAgent().process(
        bad, upstream[1], upstream[2], {}, {}
    )
    assert result.recommendation_status == "invalid_input"
    assert result.recommendation_eligible is False
    assert result.approval_status == "rejected"


def test_ungrounded_guidance_cannot_create_work_plan(upstream):
    guidance = upstream[2].model_copy(deep=True)
    guidance.guidance_status = "no_guidance"; guidance.guidance_eligible = False
    result = PrescriptiveOptimizationAgent().process(upstream[0], upstream[1], guidance, {}, {})
    assert result.recommendation_eligible is False
    assert result.recommended_action.name == ""


def test_tampered_case_identity_is_rejected(upstream):
    guidance = upstream[2].model_copy(deep=True); guidance.case_id = "TAMPER"
    result = PrescriptiveOptimizationAgent().process(upstream[0], upstream[1], guidance, {}, {})
    assert result.recommendation_status == "invalid_input"


def test_invalid_risk_status_is_rejected(upstream):
    risk = upstream[0].model_copy(deep=True); risk.risk_eligible = False
    result = PrescriptiveOptimizationAgent().process(risk, upstream[1], upstream[2], {}, {})
    assert result.recommendation_eligible is False


def test_unsupported_risk_level_is_rejected(upstream):
    risk = upstream[0].model_copy(deep=True); risk.risk_level = "catastrophic"
    result = PrescriptiveOptimizationAgent().process(risk, upstream[1], upstream[2], {}, {})
    assert result.recommendation_eligible is False


def test_provenance_links_agent4_and_agent5(upstream):
    result = _run(upstream)
    assert result.linked_risk_case_id == upstream[0].case_id
    assert result.source_risk_config_version == upstream[0].risk_config_version
    assert result.source_knowledge_config_version == upstream[2].knowledge_config_version
    assert result.source_knowledge_index_version == upstream[2].knowledge_index_version
    assert result.prescriptive_config_version


def test_output_is_json_serializable(upstream):
    assert "ranked_alternatives" in json.dumps(_run(upstream).to_dict())


def test_inputs_are_not_mutated(upstream):
    before = [item.model_dump() for item in upstream]
    _run(upstream)
    assert [item.model_dump() for item in upstream] == before


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, 0
    def is_configured(self): return True
    def complete_json(self, **_):
        self.calls += 1
        if self.error: raise self.error
        return self.response


def _llm_agent(fake):
    cfg = load_prescriptive_config(); cfg.llm_rationale_enabled = True
    return PrescriptiveOptimizationAgent(cfg, llm_client=fake)


def test_llm_may_rewrite_rationale_but_not_decision(upstream):
    fake = FakeLLM({"rationale": "Grounded concise manager explanation."})
    baseline = _run(upstream)
    result = _llm_agent(fake).process(upstream[0], upstream[1], upstream[2],
        {upstream[2].bearing_type: 2},
        {"planned_stop_windows": [{"window_id": "WIN-1", "duration_hours": 8}]})
    assert result.rationale == "Grounded concise manager explanation."
    assert result.recommended_action == baseline.recommended_action
    assert result.approval_required == baseline.approval_required
    assert result.rationale_source == "rules+llm_rationale"


@pytest.mark.parametrize("response", [None, {}, {"action": "unsafe"}, {"rationale": 123}])
def test_malformed_llm_response_falls_back_to_rules(upstream, response):
    result = _llm_agent(FakeLLM(response)).process(
        upstream[0], upstream[1], upstream[2], {upstream[2].bearing_type: 2}, {})
    assert result.rationale_source == "rules"
    assert result.rationale


def test_llm_exception_falls_back_without_raising(upstream):
    result = _llm_agent(FakeLLM(error=TimeoutError("provider timeout"))).process(
        upstream[0], upstream[1], upstream[2], {upstream[2].bearing_type: 2}, {})
    assert result.recommendation_status == "ok"
    assert result.rationale_source == "rules"


def test_invalid_input_never_calls_llm(upstream):
    fake = FakeLLM({"rationale": "must not be used"})
    result = _llm_agent(fake).process(None, upstream[1], upstream[2], {}, {})
    assert fake.calls == 0
    assert result.recommendation_status == "invalid_input"


def test_config_validation_rejects_unsupported_action():
    cfg = load_prescriptive_config(); cfg.risk_actions["high"] = ["invented_action"]
    with pytest.raises(ValueError): cfg.validate()
# ***********************
