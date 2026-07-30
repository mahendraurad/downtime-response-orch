"""Comprehensive deterministic retrieval and grounding tests for Agent 5."""
from copy import deepcopy
import json

import pytest

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.knowledge_agent import (
    KnowledgeAgent, _extract_loto, _extract_safety, _extract_steps,
)
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.tools.config_loader import load_knowledge_config, load_monitoring_config
from src.tools.data_loader import load_telemetry_rows
from src.tools.retriever import (
    knowledge_index_stats, knowledge_index_version, retrieve,
)


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture()
def pipeline(tmp_path):
    cfg = load_monitoring_config()
    cfg.ewma_state_file = str(tmp_path / "knowledge-ewma.json")
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
    ("scenario", "fault_mode", "source_fragment"),
    [
        ("outer_race_fault", "outer_race_fault", "outer_race"),
        ("inner_race_fault", "inner_race_fault", "inner_race"),
        ("lubrication_issue", "lubrication_issue", "lubrication"),
        ("gearbox_fault", "rolling_element_fault", "gearbox"),
    ],
)
def test_every_pipeline_fault_retrieves_relevant_sop(
    pipeline, scenario, fault_mode, source_fragment
):
    _, _, diagnosis, _, guidance = _run(pipeline, scenario)
    assert diagnosis.fault_mode == fault_mode
    assert guidance.guidance_status == "grounded"
    assert guidance.guidance_eligible is True
    assert guidance.source_documents
    assert any(source_fragment in title.lower() for title in guidance.source_documents)
    assert guidance.inspection_steps
    assert guidance.relevant_sections


def test_every_instruction_has_per_item_source(pipeline):
    _, _, _, _, guidance = _run(pipeline, "outer_race_fault")
    cited = {(item.item_type, item.text): item.source_document for item in guidance.grounded_items}
    for step in guidance.inspection_steps:
        assert cited[("inspection_step", step)] in guidance.source_documents
    for note in guidance.safety_notes:
        assert cited[("safety_note", note)] in guidance.source_documents


def test_source_details_include_document_title_and_score(pipeline):
    _, _, _, _, guidance = _run(pipeline, "gearbox_fault")
    assert guidance.source_details
    assert all(item.title in guidance.source_documents for item in guidance.source_details)
    assert all(item.retrieval_score >= 0 for item in guidance.source_details)


def test_undetermined_returns_no_guidance_without_calling_retriever(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "fi_hitl_test", 0)
    calls = []
    agent = KnowledgeAgent(retriever_fn=lambda **kwargs: calls.append(kwargs) or [])
    guidance = agent.process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "no_guidance"
    assert guidance.guidance_eligible is False
    assert not guidance.source_documents
    assert not calls


def test_unsourced_hit_never_produces_instruction(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    agent = KnowledgeAgent(retriever_fn=lambda **kwargs: [{
        "source": "", "text": "Step 1: Unsafe unsourced instruction", "score": 1.0,
    }])
    guidance = agent.process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "no_guidance"
    assert guidance.inspection_steps == []
    assert guidance.grounded_items == []


def test_mismatched_fault_metadata_is_filtered(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    agent = KnowledgeAgent(retriever_fn=lambda **kwargs: [{
        "source": "wrong.pdf", "text": "Step 1: Wrong fault instruction",
        "score": 1.0, "fault_mode": "inner_race_fault",
    }])
    guidance = agent.process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "no_guidance"


def test_retriever_failure_is_distinct_from_no_match(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")

    def broken(**kwargs):
        raise OSError("index unavailable")

    guidance = KnowledgeAgent(retriever_fn=broken).process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "retrieval_failed"
    assert "index unavailable" in guidance.status_reason


@pytest.mark.parametrize("hits", [
    None, [], [None, "bad"], [{"source": "x.pdf"}],
    [{"text": "Step 1: x"}],
    [{"source": "x.pdf", "text": "Step 1: Valid-looking step", "score": float("nan")}],
    [{"source": "x.pdf", "text": "Step 1: Below threshold step", "score": 0.1}],
    [{"source": 123, "text": "Step 1: Invalid source type", "score": 1.0}],
])
def test_empty_or_malformed_hits_return_no_guidance(pipeline, hits):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    guidance = KnowledgeAgent(retriever_fn=lambda **kwargs: hits).process(
        diagnosis, trusted, risk
    )
    assert guidance.guidance_status == "no_guidance"
    assert not guidance.inspection_steps


@pytest.mark.parametrize("position", [0, 1])
def test_wrong_required_input_type_is_explicit(pipeline, position):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    args = [diagnosis, trusted, risk]
    args[position] = None
    guidance = pipeline[4].process(*args)
    assert guidance.guidance_status == "invalid_input"
    assert guidance.guidance_eligible is False


def test_invalid_agent3_diagnosis_is_rejected(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    diagnosis = diagnosis.model_copy(update={
        "diagnosis_status": "invalid_input", "diagnostic_eligible": False,
    })
    guidance = pipeline[4].process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "invalid_input"


def test_ineligible_agent1_signal_is_rejected(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    trusted.downstream_eligible = False
    guidance = pipeline[4].process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "invalid_input"


def test_diagnosis_identity_mismatch_is_rejected(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    diagnosis = diagnosis.model_copy(update={"bearing_id": "BRG-WRONG"})
    guidance = pipeline[4].process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "invalid_input"
    assert "identities" in guidance.status_reason


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"risk_eligible": False}, "not guidance eligible"),
        ({"case_id": "CASE-WRONG"}, "case identities"),
        ({"asset_id": "ASSET-WRONG"}, "asset identities"),
    ],
)
def test_invalid_agent4_handoff_is_rejected(pipeline, change, message):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    risk = risk.model_copy(update=change)
    guidance = pipeline[4].process(diagnosis, trusted, risk)
    assert guidance.guidance_status == "invalid_input"
    assert message in guidance.status_reason


def test_non_risk_object_is_rejected_when_supplied(pipeline):
    trusted, _, diagnosis, _, _ = _run(pipeline, "outer_race_fault")
    guidance = pipeline[4].process(diagnosis, trusted, "bad-risk")
    assert guidance.guidance_status == "invalid_input"


def test_query_contains_fault_asset_bearing_stage_and_severity(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return []

    KnowledgeAgent(retriever_fn=fake).process(diagnosis, trusted, risk)
    query = captured["query"].lower()
    assert "outer race fault" in query
    assert trusted.asset_ctx.asset_type in query
    assert "bearing" in query
    assert "stage 3" in query
    assert diagnosis.severity in query


def test_retrieval_config_is_passed_to_adapter(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    cfg = load_knowledge_config()
    captured = {}
    KnowledgeAgent(retriever_fn=lambda **kwargs: captured.update(kwargs) or [], cfg=cfg).process(
        diagnosis, trusted, risk
    )
    assert captured["top_k"] == cfg.top_k
    assert captured["minimum_score"] == cfg.minimum_score
    assert captured["fault_mode_boost"] == cfg.fault_mode_boost


def test_output_caps_and_deduplication_are_enforced(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    cfg = load_knowledge_config()
    cfg.max_inspection_steps = 2
    cfg.max_safety_notes = 1
    text = (
        "Step 1: Inspect the bearing carefully and document the result. "
        "Step 2: Replace the damaged bearing using the approved procedure. "
        "Step 3: Record post-repair vibration after commissioning.\n"
        "Safety: Wear gloves and safety glasses.\n"
        "Safety: Wear gloves and safety glasses."
    )
    hit = {"source": "approved.pdf", "text": text, "score": 0.9,
           "fault_mode": diagnosis.fault_mode}
    guidance = KnowledgeAgent(retriever_fn=lambda **kwargs: [hit, hit], cfg=cfg).process(
        diagnosis, trusted, risk
    )
    assert len(guidance.inspection_steps) == 2
    assert len(guidance.safety_notes) == 1
    assert guidance.source_documents == ["approved.pdf"]


def test_provenance_is_populated_and_stable(pipeline):
    _, _, _, _, first = _run(pipeline, "outer_race_fault")
    _, _, _, _, second = _run(pipeline, "outer_race_fault")
    assert first.knowledge_config_version == second.knowledge_config_version
    assert first.knowledge_index_version == second.knowledge_index_version
    assert len(first.knowledge_index_version) == 16
    assert first.linked_risk_case_id == first.case_id


def test_complete_guidance_serializes_to_json(pipeline):
    _, _, _, _, guidance = _run(pipeline, "gearbox_fault")
    encoded = json.dumps(guidance.to_dict())
    assert "rolling_element_fault" in encoded
    assert "source_document" in encoded


def test_agent5_does_not_mutate_inputs(pipeline):
    trusted, _, diagnosis, risk, _ = _run(pipeline, "outer_race_fault")
    before = (trusted.to_dict(), diagnosis.to_dict(), risk.to_dict())
    pipeline[4].process(diagnosis, trusted, risk)
    assert before == (trusted.to_dict(), diagnosis.to_dict(), risk.to_dict())


def test_step_extraction_primary_and_numbered_fallback():
    assert _extract_steps("Step 1: Inspect carefully. Step 2: Record vibration.") == [
        "Inspect carefully.", "Record vibration."
    ]
    numbered = "1 Inspect the bearing housing carefully\n2 Record vibration after repair"
    assert len(_extract_steps(numbered)) == 2


def test_safety_and_loto_extraction():
    text = "Safety: Wear PPE and gloves. Obtain LOTO EL-104-A before work."
    assert _extract_safety(text)
    assert _extract_loto(text) == "EL-104-A"


@pytest.mark.parametrize(
    ("query", "fault", "asset", "stage"),
    [
        ("outer race replacement motor", "outer_race_fault", "motor", 3),
        ("outer race inspection motor", "outer_race_fault", "motor", 1),
        ("inner race replacement motor", "inner_race_fault", "motor", 3),
        ("inner race lubrication pump", "inner_race_fault", "pump", 2),
        ("lubrication pump inspection", "lubrication_issue", "pump", 2),
        ("cage inspection conveyor", "cage_fault", "conveyor", 1),
        ("rolling element gearbox bearing", "rolling_element_fault", "gearbox", 3),
        ("motor post repair bearing QA", "outer_race_fault", "motor", 3),
        ("pump bearing safety lubrication", "lubrication_issue", "pump", 2),
        ("gearbox bearing BSF safety", "rolling_element_fault", "gearbox", 3),
    ],
)
def test_ten_synthetic_retrieval_scenarios(query, fault, asset, stage):
    hits = retrieve(query, top_k=5, fault_mode=fault, asset_type=asset, iso_stage=stage)
    assert hits
    assert any(hit.get("fault_mode") in ("", fault) for hit in hits)
    assert all(hit["source"] for hit in hits)


def test_index_version_and_stats_are_available():
    stats = knowledge_index_stats()
    assert len(knowledge_index_version()) == 16
    assert stats["chunks"] >= 10
    assert stats["documents"] >= 5


@pytest.mark.parametrize(
    "change",
    [
        lambda c: setattr(c, "top_k", 0),
        lambda c: setattr(c, "minimum_score", -1),
        lambda c: setattr(c, "fault_mode_boost", float("nan")),
        lambda c: setattr(c, "max_inspection_steps", 0),
        lambda c: setattr(c, "section_preview_characters", -1),
    ],
)
def test_invalid_knowledge_configuration_fails_fast(change):
    cfg = deepcopy(load_knowledge_config())
    change(cfg)
    with pytest.raises(ValueError):
        cfg.validate()
# ***********************
