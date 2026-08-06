"""Cross-persona response-context certification."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import src.api.main as api
import src.orchestrator.graph as graph
from src.api.persona_formatter import format_for_persona
from src.orchestrator.graph import run_pipeline
from src.tools.data_loader import load_fleet_scenario_map, load_telemetry_rows
from src.tools.governed_rag import GovernedKnowledgeRAG
from src.tools.persona_formatter import (
    build_persona_context,
    persona_response_prompt,
)


PERSONAS = (
    "supervisor", "engineer", "maintenance", "manager",
    "executive", "ot", "safety",
)


@pytest.fixture(scope="module", autouse=True)
def isolated_monitoring_state(tmp_path_factory):
    previous_agents = graph._agents
    agents = graph._build_agents()
    agents[1]._cfg.ewma_state_file = str(
        tmp_path_factory.mktemp("persona-context") / "ewma.json"
    )
    graph._agents = agents
    try:
        yield
    finally:
        graph._agents = previous_agents


@pytest.fixture(scope="module")
def risk_state():
    _, scenario = load_fleet_scenario_map()["M-121"]
    return run_pipeline(
        deepcopy(load_telemetry_rows(scenario)[-1]),
        intent="risk", persona="supervisor", run_id="PERSONA-CONTEXT-RISK",
    )


def test_all_persona_response_prompts_are_distinct_and_evidence_safe():
    prompts = {}
    for persona in PERSONAS:
        context = build_persona_context(persona)
        prompt = persona_response_prompt(context)
        prompts[persona] = prompt
        assert context.role in prompt
        assert context.domain in prompt
        assert context.response_depth in prompt
        assert "Keep authoritative measurements, decisions, citations, and confidence unchanged" in prompt
        assert "rather than inventing it" in prompt
    assert len(set(prompts.values())) == len(PERSONAS)


@pytest.mark.parametrize(("persona", "expected_detail"), [
    ("supervisor", "RUL window"),
    ("engineer", "RUL estimate"),
    ("maintenance", "Action type"),
    ("manager", "Cost if deferred"),
    ("executive", "Financial exposure"),
    ("ot", "Data quality"),
    ("safety", "ISO 10816-3 Zone"),
])
def test_same_pipeline_evidence_is_framed_for_each_persona(
    persona, expected_detail, risk_state,
):
    response = format_for_persona(risk_state, persona)
    context = response["persona_context"]
    assert response["persona"] == persona
    assert context["id"] == persona
    assert context["role"] == build_persona_context(persona).role
    assert any(expected_detail in detail for detail in response["details"])
    assert response["headline"]
    assert response["actions"]


def test_persona_headlines_and_action_sets_are_not_one_generic_template(risk_state):
    responses = {
        persona: format_for_persona(risk_state, persona)
        for persona in PERSONAS
    }
    assert len({row["headline"] for row in responses.values()}) == len(PERSONAS)
    assert len({tuple(row["actions"]) for row in responses.values()}) == len(PERSONAS)


def test_expanded_asset_uses_configured_display_id_in_every_persona(risk_state):
    for persona in PERSONAS:
        response = format_for_persona(risk_state, persona)
        assert "M-121" in response["headline"]
        assert "AST_SYN_MTR_121" not in response["headline"]


def test_conversational_llm_receives_selected_persona_contract(monkeypatch):
    prompts = []

    class CaptureLLM:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def complete_json(**kwargs):
            prompts.append(kwargs["system_prompt"])
            return {"answer": "Evidence-bound follow-up response.", "requires_telemetry": False}

    monkeypatch.setattr(api, "_CHAT_LLM", CaptureLLM())
    for persona in PERSONAS:
        draft = api._conversational_draft(
            "Why was that decision recommended?",
            [{"role": "assistant", "content": "A recommendation was produced."}],
            persona,
        )
        assert draft["persona_context"]["id"] == persona
    assert len(prompts) == len(PERSONAS)
    for persona, prompt in zip(PERSONAS, prompts):
        assert build_persona_context(persona).role in prompt
    assert not all("Reliability Engineer" in prompt for prompt in prompts)


def test_pipeline_llm_synthesis_uses_selected_persona_not_fixed_engineer(monkeypatch):
    synthesis_prompts = []

    class CaptureLLM:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def complete_json(**kwargs):
            prompt = kwargs["system_prompt"]
            if "intent_type" in prompt:
                return {"intent_type": "pipeline", "reasoning": "asset analysis requested"}
            synthesis_prompts.append(prompt)
            return {"answer": "Evidence-bound pipeline answer."}

    monkeypatch.setattr(api, "_CHAT_LLM", CaptureLLM())
    client = TestClient(api.app)
    for persona in ("manager", "safety"):
        result = client.post("/api/chat", json={
            "message": "What is the RUL risk for M-121?", "persona": persona,
        }).json()
        assert result["response"] == "Evidence-bound pipeline answer."
        assert result["persona_context"]["id"] == persona

    assert len(synthesis_prompts) == 2
    assert "Role: Plant Manager" in synthesis_prompts[0]
    assert "Role: Safety Officer" in synthesis_prompts[1]
    assert "Suppress fields: sensor_ids, vibration_spectrum_detail" in synthesis_prompts[0]
    assert "Suppress fields: financial_detail, vibration_spectrum_detail" in synthesis_prompts[1]


def test_grounded_rag_keeps_citation_but_changes_audience_frame():
    hit = {
        "source": "DRO_APPROVED_RUL_GUIDE",
        "source_type": "general_knowledge",
        "text": "Remaining useful life estimates time to a defined maintenance threshold.",
        "score": 0.95,
        "chunk_id": "rul-1",
        "source_uri": "kb://rul/1",
    }
    answers = {}
    for persona in PERSONAS:
        result = GovernedKnowledgeRAG(retriever_fn=lambda **_: [hit]).answer(
            "What is remaining useful life?",
            persona_context=build_persona_context(persona),
        )
        assert result.status == "grounded"
        assert "[1]" in result.answer
        assert result.citations[0].chunk_id == "rul-1"
        answers[persona] = result.answer
    assert len(set(answers.values())) == len(PERSONAS)


@pytest.mark.parametrize("persona", PERSONAS)
def test_chat_concept_response_exposes_selected_persona_context(persona):
    data = TestClient(api.app).post("/api/chat", json={
        "message": "What is RUL?", "persona": persona,
    }).json()
    assert data["persona"] == persona
    assert data["persona_context"]["id"] == persona
    assert data["persona_context"]["domain"] == build_persona_context(persona).domain
    assert data["source_type"] == "approved_rag"
    assert data["citations"]


def test_clarification_and_multi_asset_outputs_carry_persona_context():
    client = TestClient(api.app)
    clarification = client.post("/api/chat", json={
        "message": "What is the RUL of this bearing?", "persona": "maintenance",
    }).json()
    assert clarification["clarification_required"]
    assert clarification["persona_context"]["id"] == "maintenance"

    multi = client.post("/api/chat", json={
        "message": "Plan M-104 and P-207 this week",
        "persona": "manager",
        "context": {"fleet_snapshot": {
            "M-104": {"scenario": "outer_race_fault"},
            "P-207": {"scenario": "lubrication_issue"},
        }},
    }).json()
    assert multi["persona_context"]["id"] == "manager"
    assert multi["response"].startswith("Plant Manager context:")


def test_reset_and_social_shortcuts_retain_selected_persona_context():
    client = TestClient(api.app)
    reset = client.post("/api/chat", json={
        "message": "start over", "persona": "manager",
    }).json()
    social = client.post("/api/chat", json={
        "message": "thanks", "persona": "safety",
    }).json()
    assert reset["persona_context"]["id"] == "manager"
    assert social["persona_context"]["id"] == "safety"
