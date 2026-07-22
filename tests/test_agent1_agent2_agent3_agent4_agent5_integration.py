"""Complete Agent 1 -> Agent 5 deterministic backend certification."""
from copy import deepcopy
import json
from datetime import datetime, timezone

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.knowledge_agent import KnowledgeAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.tools.config_loader import load_monitoring_config
from src.tools.data_loader import load_telemetry_rows
from src.tools.trusted_signal_repository import SQLiteTrustedSignalRepository


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def chain(tmp_path):
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "agent12345-ewma.json")
    return (
        DataFoundationAgent.from_data_files(), MonitoringAgent(cfg),
        FailureIntelligenceAgent.from_data_files(), PredictiveRiskAgent.from_data_files(),
        KnowledgeAgent(),
    )


def _row(scenario, index=0):
    return deepcopy(load_telemetry_rows(scenario)[index])


def _run(chain, payload):
    foundation, monitoring, failure, risk_agent, knowledge = chain
    trusted = foundation.process(payload)
    decision = monitoring.assess(trusted)
    if decision.anomaly_event is None:
        return trusted, decision, None, None, None
    diagnosis = failure.process(decision.anomaly_event, trusted)
    risk = risk_agent.process(diagnosis, decision.anomaly_event, trusted)
    guidance = knowledge.process(diagnosis, trusted, risk)
    return trusted, decision, diagnosis, risk, guidance


@pytest.mark.parametrize(
    ("scenario", "fault", "status"),
    [
        ("outer_race_fault", "outer_race_fault", "grounded"),
        ("inner_race_fault", "inner_race_fault", "grounded"),
        ("lubrication_issue", "lubrication_issue", "grounded"),
        ("gearbox_fault", "rolling_element_fault", "grounded"),
    ],
)
def test_all_supported_faults_reach_source_grounded_guidance(
    chain, scenario, fault, status
):
    trusted, decision, diagnosis, risk, guidance = _run(chain, _row(scenario, -1))
    assert trusted.downstream_eligible is True
    assert decision.status == "anomaly"
    assert diagnosis.fault_mode == fault
    assert risk.assessment_status == "assessed"
    assert guidance.guidance_status == status
    assert guidance.inspection_steps
    assert all(item.source_document for item in guidance.grounded_items)


@pytest.mark.parametrize("scenario", ["healthy", "startup_filter", "signal_dropout", "unknown_asset"])
def test_terminal_non_anomaly_paths_never_reach_knowledge(chain, scenario):
    _, decision, diagnosis, risk, guidance = _run(chain, _row(scenario, -1))
    assert decision.anomaly_event is None
    assert diagnosis is None and risk is None and guidance is None


@pytest.mark.parametrize("payload", [None, {}, "bad", 42, []])
def test_malformed_inputs_never_reach_knowledge(chain, payload):
    trusted, decision, diagnosis, risk, guidance = _run(chain, payload)
    assert trusted.downstream_eligible is False
    assert decision.status == "ineligible"
    assert diagnosis is None and risk is None and guidance is None


def test_undetermined_full_flow_returns_no_guidance_for_hitl(chain):
    _, decision, diagnosis, risk, guidance = _run(chain, _row("fi_hitl_test"))
    assert decision.status == "anomaly"
    assert diagnosis.diagnosis_status == "undetermined"
    assert risk.assessment_status == "monitor"
    assert guidance.guidance_status == "no_guidance"
    assert guidance.source_documents == []


def test_opcua_normalized_fault_reaches_grounded_guidance(chain):
    payload = _row("outer_race_fault", -1)
    payload["data_source"] = "opcua"
    payload["MotorSpeed"] = payload.pop("rpm")
    payload["BearingTemp"] = payload.pop("temp_c")
    payload["VibrationRMS"] = payload.pop("vib_rms_mm_s")
    trusted, _, diagnosis, risk, guidance = _run(chain, payload)
    assert trusted.source_profile == "opcua"
    assert diagnosis.fault_code == "FT_001"
    assert risk.rul_max_days == 7
    assert guidance.guidance_status == "grounded"


def test_full_agent1_to_agent5_provenance_chain(chain):
    trusted, decision, diagnosis, risk, guidance = _run(
        chain, _row("outer_race_fault", -1)
    )
    assert guidance.source_data_config_version == trusted.config_version
    assert guidance.source_master_data_version == trusted.master_data_version
    assert guidance.source_monitoring_config_version == decision.monitoring_config_version
    assert guidance.source_fi_config_version == diagnosis.fi_config_version
    assert guidance.source_taxonomy_version == diagnosis.taxonomy_version
    assert guidance.source_risk_config_version == risk.risk_config_version
    assert guidance.knowledge_config_version
    assert guidance.knowledge_index_version


def test_full_output_chain_is_json_serializable(chain):
    outputs = _run(chain, _row("gearbox_fault", -1))
    encoded = json.dumps([
        output.to_dict() for output in outputs if output is not None
    ], ensure_ascii=False)
    assert "rolling_element_fault" in encoded
    assert "grounded_items" in encoded
    assert "source_document" in encoded


def test_mixed_batch_keeps_knowledge_outputs_isolated(chain):
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
    assert [item[4].guidance_status if item[4] else None for item in results] == [
        None, "grounded", None, "grounded", None
    ]


def test_every_full_flow_instruction_is_cited(chain):
    for scenario in (
        "outer_race_fault", "inner_race_fault", "lubrication_issue", "gearbox_fault"
    ):
        guidance = _run(chain, _row(scenario, -1))[4]
        sources = set(guidance.source_documents)
        assert guidance.grounded_items
        assert all(item.source_document in sources for item in guidance.grounded_items)


# Final stitched-pipeline edge certification. These tests deliberately cross
# ownership boundaries so a local success cannot hide an unsafe downstream flow.
@pytest.mark.parametrize(
    ("mutation", "reason_fragment"),
    [
        ({"data_source": "live", "timestamp_utc": "2000-01-01T00:00:00Z"}, "stale"),
        ({"timestamp_utc": "2026-07-20T10:00:00"}, "timezone"),
        ({"signal_quality_score": 0.1}, "quality"),
        ({"rpm": -1}, "rpm"),
    ],
)
def test_agent1_quality_failures_stop_the_complete_chain(chain, mutation, reason_fragment):
    payload = _row("outer_race_fault", -1)
    payload.update(mutation)
    trusted, decision, diagnosis, risk, guidance = _run(chain, payload)
    assert trusted.downstream_eligible is False
    assert reason_fragment in " ".join(trusted.validation.reasons).lower()
    assert decision.status == "ineligible"
    assert diagnosis is None and risk is None and guidance is None


def test_canonical_value_wins_over_conflicting_alias_through_agent5(chain):
    payload = _row("outer_race_fault", -1)
    canonical = payload["vib_rms_mm_s"]
    payload["vibration_rms"] = 0.0
    trusted, _, diagnosis, _, guidance = _run(chain, payload)
    assert trusted.raw.vib_rms_mm_s == canonical
    assert "vibration_rms" in trusted.unknown_fields
    assert diagnosis.fault_mode == "outer_race_fault"
    assert guidance.guidance_status == "grounded"


def test_unknown_source_field_is_audited_without_corrupting_full_flow(chain):
    payload = _row("inner_race_fault", -1)
    payload["vendor_debug_tag"] = "retain-for-audit"
    trusted, _, diagnosis, _, guidance = _run(chain, payload)
    assert trusted.unknown_fields == ["vendor_debug_tag"]
    assert diagnosis.fault_mode == "inner_race_fault"
    assert guidance.guidance_status == "grounded"


def test_complete_chain_does_not_mutate_caller_payload(chain):
    payload = _row("lubrication_issue", -1)
    before = deepcopy(payload)
    _run(chain, payload)
    assert payload == before


def test_duplicate_durable_event_cannot_generate_second_guidance(tmp_path):
    repository = SQLiteTrustedSignalRepository(str(tmp_path / "trusted.db"))
    foundation = DataFoundationAgent.from_data_files(repository=repository)
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "duplicate-ewma.json")
    monitoring = MonitoringAgent(cfg)
    payload = _row("outer_race_fault", -1)

    first = foundation.process_and_store(payload)
    assert monitoring.assess(first).status == "anomaly"
    duplicate = foundation.process_and_store(deepcopy(payload))
    decision = monitoring.assess(duplicate)
    assert repository.count() == 1
    assert duplicate.duplicate_detected is True
    assert decision.status == "duplicate"
    assert decision.anomaly_event is None


def test_out_of_order_durable_event_cannot_reach_agent3(tmp_path):
    repository = SQLiteTrustedSignalRepository(str(tmp_path / "ordered.db"))
    foundation = DataFoundationAgent.from_data_files(repository=repository)
    newer = _row("outer_race_fault", -1)
    newer["telemetry_id"] = "ORDER-NEW"
    newer["timestamp_utc"] = "2026-07-20T10:01:00Z"
    older = deepcopy(newer)
    older["telemetry_id"] = "ORDER-OLD"
    older["timestamp_utc"] = "2026-07-20T10:00:00Z"
    foundation.process_and_store(newer)
    late = foundation.process_and_store(older)
    decision = MonitoringAgent(load_monitoring_config()).assess(late)
    assert repository.count() == 2
    assert late.out_of_order is True
    assert decision.status == "out_of_order"
    assert decision.anomaly_event is None


def test_equal_timestamp_unique_events_remain_independently_processable(tmp_path):
    repository = SQLiteTrustedSignalRepository(str(tmp_path / "equal.db"))
    foundation = DataFoundationAgent.from_data_files(repository=repository)
    one = _row("outer_race_fault", -1)
    one["telemetry_id"] = "EQUAL-1"
    two = deepcopy(one)
    two["telemetry_id"] = "EQUAL-2"
    first = foundation.process_and_store(one)
    second = foundation.process_and_store(two)
    assert first.downstream_eligible and second.downstream_eligible
    assert not second.duplicate_detected and not second.out_of_order
    assert repository.count() == 2


def test_agent1_repository_failure_fails_closed_before_monitoring(tmp_path):
    class BrokenRepository:
        def exists(self, _): return False
        def latest_event_epoch(self, _): return None
        def save(self, *_): raise OSError("disk unavailable")

    foundation = DataFoundationAgent.from_data_files(repository=BrokenRepository())
    trusted = foundation.process_and_store(_row("outer_race_fault", -1))
    decision = MonitoringAgent(load_monitoring_config()).assess(trusted)
    assert trusted.persistence_status == "failed"
    assert trusted.downstream_eligible is False
    assert decision.status == "ineligible"
    assert decision.anomaly_event is None


def test_agent2_repository_failure_is_visible_but_preserves_computed_anomaly(chain):
    class BrokenRepository:
        def save(self, _): raise OSError("monitoring store unavailable")

    trusted = chain[0].process(_row("outer_race_fault", -1))
    cfg = load_monitoring_config()
    cfg.ewma_state_file = chain[1]._cfg.ewma_state_file
    result = MonitoringAgent(cfg, decision_repository=BrokenRepository()).assess_and_store(trusted)
    assert result.status == "anomaly"
    assert result.persistence_status == "failed"
    assert result.anomaly_event is not None
    assert "persistence failed" in result.suppression_reason


def test_risk_case_tampering_is_rejected_by_agent5(chain):
    trusted, _, diagnosis, risk, _ = _run(chain, _row("outer_race_fault", -1))
    risk.case_id = "CASE-TAMPERED"
    guidance = chain[4].process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "invalid_input"
    assert guidance.guidance_eligible is False
    assert guidance.source_documents == []


@pytest.mark.parametrize(
    ("retriever", "expected"),
    [
        (lambda **_: (_ for _ in ()).throw(RuntimeError("index offline")), "retrieval_failed"),
        (lambda **_: [{"source": "", "text": "1. unsafe unsourced step", "score": 1.0,
                       "fault_mode": "outer_race_fault"}], "no_guidance"),
        (lambda **_: [{"source": "Wrong SOP", "text": "1. wrong repair", "score": 1.0,
                       "fault_mode": "inner_race_fault"}], "no_guidance"),
        (lambda **_: [{"source": "Bad Score", "text": "1. invalid score", "score": float("nan"),
                       "fault_mode": "outer_race_fault"}], "no_guidance"),
    ],
)
def test_agent5_retrieval_failures_never_leak_ungrounded_instructions(
    chain, retriever, expected
):
    trusted, _, diagnosis, risk, _ = _run(chain, _row("outer_race_fault", -1))
    guidance = KnowledgeAgent(retriever_fn=retriever).process(diagnosis, trusted, risk)
    assert guidance.guidance_status == expected
    assert guidance.guidance_eligible is False
    assert guidance.inspection_steps == []
    assert guidance.grounded_items == []


def test_replayed_agent5_decision_is_deterministic_except_processing_time(chain):
    trusted, _, diagnosis, risk, guidance1 = _run(chain, _row("gearbox_fault", -1))
    guidance2 = chain[4].process(diagnosis, trusted, risk)
    first, second = guidance1.to_dict(), guidance2.to_dict()
    first.pop("processed_at")
    second.pop("processed_at")
    assert first == second


def test_fresh_event_hub_identity_aliases_complete_the_full_flow(chain):
    payload = _row("outer_race_fault", -1)
    payload["data_source"] = "event_hub"
    payload["assetId"] = payload.pop("asset_id")
    payload["bearingId"] = payload.pop("bearing_id")
    payload["channelId"] = payload.pop("channel_id")
    payload["eventTime"] = datetime.now(timezone.utc).isoformat()
    payload.pop("timestamp_utc")
    trusted, decision, diagnosis, risk, guidance = _run(chain, payload)
    assert trusted.source_profile == "event_hub"
    assert trusted.validation.freshness_valid is True
    assert decision.status == "anomaly"
    assert diagnosis.diagnosis_status == "diagnosed"
    assert risk.assessment_status == "assessed"
    assert guidance.guidance_status == "grounded"


def test_monitoring_cooldown_blocks_repeat_anomaly_before_agent3(tmp_path):
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "cooldown-ewma.json")
    cfg.alert_cooldown_seconds = 3600
    local_chain = (
        DataFoundationAgent.from_data_files(), MonitoringAgent(cfg),
        FailureIntelligenceAgent.from_data_files(), PredictiveRiskAgent.from_data_files(),
        KnowledgeAgent(),
    )
    first = _row("outer_race_fault", -1)
    first["telemetry_id"] = "COOLDOWN-1"
    second = deepcopy(first)
    second["telemetry_id"] = "COOLDOWN-2"
    assert _run(local_chain, first)[1].status == "anomaly"
    _, decision, diagnosis, risk, guidance = _run(local_chain, second)
    assert decision.status == "cooldown"
    assert decision.anomaly_event is None
    assert diagnosis is None and risk is None and guidance is None


def test_agent3_rejects_tampered_agent2_identity(chain):
    trusted = chain[0].process(_row("outer_race_fault", -1))
    anomaly = chain[1].assess(trusted).anomaly_event
    anomaly.asset_id = "AST-TAMPERED"
    diagnosis = chain[2].process(anomaly, trusted)
    assert diagnosis.diagnosis_status == "invalid_input"
    assert diagnosis.diagnostic_eligible is False
    assert "identit" in diagnosis.status_reason.lower()


def test_agent4_rejects_tampered_agent3_taxonomy_result(chain):
    trusted, decision, diagnosis, _, _ = _run(chain, _row("outer_race_fault", -1))
    diagnosis.fault_code = "FT_UNKNOWN"
    risk = chain[3].process(diagnosis, decision.anomaly_event, trusted)
    guidance = chain[4].process(diagnosis, trusted, risk)
    assert risk.assessment_status == "invalid_input"
    assert risk.risk_eligible is False
    assert guidance.guidance_status == "invalid_input"
    assert guidance.guidance_eligible is False
# ***********************
