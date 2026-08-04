"""Group A backend response-quality and learning certification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.knowledge_agent import KnowledgeAgent
from src.agents.learning_memory_agent import LearningMemoryAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.agents.prescriptive_optimization_agent import PrescriptiveOptimizationAgent
from src.schemas.feedback import LearnedCaseDocument, RejectionFeedback
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows
from src.tools.learned_case_repository import (
    JSONLearnedCaseRepository, JSONRejectionRepository,
)
from src.tools.persona_formatter import (
    build_persona_context, normalize_persona_id,
)


@pytest.fixture()
def upstream(tmp_path):
    trusted = DataFoundationAgent.from_data_files().process(
        deepcopy(load_telemetry_rows("outer_race_fault")[-1])
    )
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "group-a-ewma.json")
    anomaly = MonitoringAgent(cfg).assess(trusted).anomaly_event
    diagnosis = FailureIntelligenceAgent.from_data_files().process(
        anomaly, trusted
    )
    risk = PredictiveRiskAgent.from_data_files().process(
        diagnosis, anomaly, trusted
    )
    guidance = KnowledgeAgent().process(diagnosis, trusted, risk)
    return trusted, diagnosis, risk, guidance


def _recommend(upstream, **kwargs):
    trusted, diagnosis, risk, guidance = upstream
    inventory = {
        guidance.bearing_type: {"qty_on_hand": 0, "lead_time_days": 2}
    }
    return PrescriptiveOptimizationAgent(**kwargs).process(
        risk, diagnosis, guidance, inventory, {},
        persona_context="supervisor", trusted_signal=trusted,
    )


def test_registry_has_exactly_seven_personas():
    ids = {
        build_persona_context(name).id for name in (
            "supervisor", "engineer", "maintenance", "manager",
            "executive", "ot", "safety",
        )
    }
    assert ids == {
        "supervisor", "engineer", "maintenance", "manager",
        "executive", "ot", "safety",
    }


def test_md_is_backward_compatible_executive_alias():
    context = build_persona_context("md")
    assert normalize_persona_id("md") == "executive"
    assert context.id == "executive"
    assert context.requested_id == "md"
    assert context.display_name == "VP Operations — Michael Osei"


def test_unknown_persona_is_rejected_not_silently_defaulted():
    with pytest.raises(ValueError, match="unsupported persona"):
        build_persona_context("invented-role")


def test_prescriptive_contract_is_verdict_first_and_grounded(upstream):
    result = _recommend(upstream)
    assert result.verdict.headline.startswith(result.asset_id)
    assert result.verdict.reasoning
    assert result.condition.fault_type == upstream[1].fault_mode
    assert result.condition.rul_max_days == upstream[2].rul_max_days
    assert result.condition.vibration_mms == upstream[0].raw.vib_rms_mm_s
    assert [item.type for item in result.consequences] == [
        "time", "financial", "cascade",
    ]
    assert all(item.rationale for item in result.prescriptive_actions)
    assert all(item.urgency in {
        "now", "this_shift", "before_handover", "this_week",
    } for item in result.prescriptive_actions)


def test_demo_cost_and_supervisor_authority_are_configured(upstream):
    result = _recommend(upstream)
    support = result.decision_support
    assert support.cost_if_approved == 48_000
    assert support.cost_if_deferred == 2_016_000
    assert support.deferred_cost_per_hour == 7_500
    assert support.cost_data_status == "configured_demo"
    assert support.authority_check == "within_authority"
    assert support.authority_limit == 100_000
    assert support.decision_support_config_version
    financial = next(x for x in result.consequences if x.type == "financial")
    assert financial.evidence_status == "configured_demo"
    assert "48,000" in financial.value


@pytest.mark.parametrize("persona,limit", [
    ("supervisor", 100_000),
    ("manager", 1_000_000),
    ("executive", 5_000_000),
])
def test_authority_limits_are_persona_specific(upstream, persona, limit):
    trusted, diagnosis, risk, guidance = upstream
    result = PrescriptiveOptimizationAgent().process(
        risk, diagnosis, guidance,
        {guidance.bearing_type: {"qty_on_hand": 1, "lead_time_days": 1}},
        {}, persona_context=persona, trusted_signal=trusted,
    )
    assert result.decision_support.authority_limit == limit
    assert result.decision_support.authority_check == "within_authority"


def test_parts_eta_is_compared_to_rul_without_cost_assumption(upstream):
    result = _recommend(upstream)
    comparison = result.decision_support.parts_vs_rul
    assert comparison.part_number
    assert comparison.eta_days == 2
    assert comparison.rul_max_days == upstream[2].rul_max_days
    assert comparison.status in {"within_rul_window", "outside_rul_window"}


def test_sop_sources_remain_separate_citations(upstream):
    result = _recommend(upstream)
    assert result.sop_citations == upstream[3].source_documents
    assert all(source for source in result.sop_citations)


def test_agent8_historical_cases_are_cited(upstream):
    rows = [{
        "case_id": "CASE-H1", "source": "Learned case CASE-H1",
        "fault_mode": upstream[1].fault_mode,
        "action_taken": "stop_and_replace", "outcome": "successful repair",
        "recorded_at": "2026-07-01T00:00:00+00:00",
        "relevance_score": 1.0,
    }]
    result = _recommend(
        upstream, historical_case_fn=lambda asset, fault, limit: rows
    )
    assert result.historical_cases[0].case_id == "CASE-H1"
    assert result.historical_cases[0].source == "Learned case CASE-H1"
    assert result.decision_support.historical_cases == result.historical_cases


def test_agent8_repository_returns_traceable_case_citations(tmp_path):
    repo = JSONLearnedCaseRepository(tmp_path / "cases.json")
    repo.save(LearnedCaseDocument(
        case_id="CASE-1", content="AST_MTR_001 outer race repaired",
        fault_mode="outer_race_fault", outcome="stop_and_replace: successful",
        tags=["outer_race_fault", "stop_and_replace"],
        created_at="2026-07-01T00:00:00+00:00",
    ))
    agent = LearningMemoryAgent(repository=repo)
    rows = agent.matching_cases("AST_MTR_001", "outer_race_fault", 3)
    assert rows[0]["source"] == "Learned case CASE-1"
    assert rows[0]["action_taken"] == "stop_and_replace"
    assert rows[0]["relevance_score"] == 1.0


def _rejection(case_id, at):
    return RejectionFeedback(
        case_id=case_id, asset_id="AST_MTR_001",
        fault_mode="outer_race_fault", reason_code="second_opinion",
        persona_id="supervisor", rejected_at=at.isoformat(),
    )


def test_second_consecutive_rejection_within_30_days_flags_review(tmp_path):
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    agent = LearningMemoryAgent(
        rejection_repository=JSONRejectionRepository(tmp_path / "reject.json"),
        now_fn=lambda: now,
    )
    first = agent.record_rejection(_rejection("R1", now - timedelta(days=2)))
    second = agent.record_rejection(_rejection("R2", now - timedelta(days=1)))
    assert first.reliability_review_required is False
    assert second.reliability_review_required is True
    assert second.consecutive_rejections == 2


def test_rejections_older_than_30_days_do_not_trigger(tmp_path):
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    agent = LearningMemoryAgent(
        rejection_repository=JSONRejectionRepository(tmp_path / "reject.json"),
        now_fn=lambda: now,
    )
    agent.record_rejection(_rejection("OLD", now - timedelta(days=31)))
    current = agent.record_rejection(_rejection("NOW", now))
    assert current.consecutive_rejections == 1
    assert current.reliability_review_required is False


def test_approval_breaks_consecutive_rejection_sequence(tmp_path):
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    agent = LearningMemoryAgent(
        rejection_repository=JSONRejectionRepository(tmp_path / "reject.json"),
        now_fn=lambda: now,
    )
    agent.record_rejection(_rejection("R1", now - timedelta(days=2)))
    agent.record_approval("A1", "AST_MTR_001", "outer_race_fault")
    result = agent.record_rejection(_rejection("R2", now))
    assert result.consecutive_rejections == 1
    assert result.reliability_review_required is False


def test_rejection_api_routes_md_feedback_to_agent8(tmp_path, monkeypatch):
    import src.api.main as api

    agent = LearningMemoryAgent(
        rejection_repository=JSONRejectionRepository(tmp_path / "reject.json")
    )
    monkeypatch.setattr(api, "_learning_agent", lambda: agent)
    response = TestClient(api.app).post("/api/recommendations/reject", json={
        "case_id": "CASE-API-1",
        "asset_id": "AST_MTR_001",
        "fault_mode": "outer_race_fault",
        "reason_code": "diagnosis_wrong",
        "persona": "md",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "recorded"
    stored = JSONRejectionRepository(tmp_path / "reject.json")._rows()
    assert stored[0]["persona_id"] == "executive"


def test_rejection_api_rejects_reason_outside_five_codes():
    from src.api.main import app

    response = TestClient(app).post("/api/recommendations/reject", json={
        "case_id": "CASE-API-2",
        "asset_id": "AST_MTR_001",
        "fault_mode": "outer_race_fault",
        "reason_code": "not_a_supported_reason",
        "persona": "supervisor",
    })
    assert response.status_code == 422
