"""
api/main.py  —  DRO FastAPI server

Endpoints:
  GET  /api/health               subsystem health check (config, assets, audit, SOP corpus, LLM)
  POST /api/pipeline/run         run full DFA→Monitoring→FI→Risk→Knowledge pipeline
  GET  /api/pipeline/scenarios   list available demo scenarios
  POST /api/pipeline/hitl/remediation   resolve Agent 1 DFA gate (IMPUTE / DROP / KEEP)
  POST /api/pipeline/hitl/monitoring    resolve Agent 2 EWMA gate (SUPPRESS / CONFIRM)
  POST /api/pipeline/hitl/diagnosis     resolve Agent 3 low-confidence gate (CONFIRM / MARK_UNDETERMINED)
  POST /api/pipeline/hitl/knowledge     resolve Agent 5 missing-SOP gate (FLAG_MANUAL / ACCEPT_EMPTY)
  POST /api/executor/run         execute a typed maintenance recommendation
  POST /api/chat                 persona-aware chat (intent classification + pipeline or LLM routing)
  GET  /api/assets               all assets from asset_master.json
  GET  /api/assets/{asset_id}    single asset detail
  GET  /api/assets/{asset_id}/export/pre-repair  pre-repair sensor CSV download
  GET  /api/notifications/counts unread notification counts for all personas
  GET  /api/notifications/{persona_id}         persona notification inbox
  POST /api/notifications/{persona_id}/read    mark all notifications read
  GET  /api/workorders           runtime work-order store
  POST /api/workorders           create work order
  PATCH /api/workorders/{wo_id}  update WO status / checklist
  WS   /ws/sensors/{asset_id}    stream simulated live sensor ticks

CORS is open for local dev (frontend HTML served from any origin).
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import random
import re
import sys
import time
import uuid
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional, Literal

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from langsmith import traceable

# Make repo root importable when run as: python -m src.api.main
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.tools.environment import load_project_environment
load_project_environment()

from src.orchestrator.graph import (
    close_graph_resources,
    graph_checkpoint_health,
    run_pipeline,
)
from src.api.auth import (
    LoginRequest, LoginResponse,
    authenticate_user, create_access_token,
    get_current_user, require_persona,
    _EXPIRE_HOURS as _JWT_EXPIRE_HOURS,
)
from src.api.persona_formatter import format_for_persona
from src.tools.data_loader import (
    list_telemetry_scenarios,
    load_asset_master,
    load_bearing_master,
    load_demo_work_orders,
    load_fleet_scenario_map,
    load_telemetry_rows,
)
from src.orchestrator.query_router import plan_query
from src.agents.reflexion_agent import ReflexionAgent
from src.tools.orchestrator_audit import write_audit
from src.tools.llm_client import LLMClient
from src.tools.hitl_repository import build_hitl_repository
from src.tools.work_order_repository import build_work_order_repository
from src.tools.chat_context_repository import load_context as load_durable_chat_context
from src.tools.chat_context_repository import save_context as save_durable_chat_context
from src.tools.chat_context_repository import clear_context as clear_durable_chat_context
from src.tools.chat_context_repository import append_history as append_chat_history
from src.tools.chat_context_repository import load_history as load_chat_history
from src.tools.chat_context_repository import clear_history as clear_chat_history
from src.tools.persona_formatter import build_persona_context, persona_response_prompt
from src.tools.governed_rag import GovernedKnowledgeRAG
from src.tools.observability import flush_observability


@asynccontextmanager
async def _application_lifespan(_app: FastAPI):
    yield
    if hasattr(_HITL_REPOSITORY, "close"):
        _HITL_REPOSITORY.close()
    close_graph_resources()
    flush_observability()


app = FastAPI(title="DRO API", version="1.0.0", lifespan=_application_lifespan)


@app.middleware("http")
async def _flush_trace_after_request(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        flush_observability()
    return response

_ORCH_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "orchestrator_config.json")
with open(_ORCH_CONFIG_PATH, "r", encoding="utf-8") as _fh:
    _ORCH_CONFIG = json.load(_fh)
_AUDIT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", _ORCH_CONFIG["audit"]["jsonl_path"])
_REFLEXION = ReflexionAgent(
    max_characters=_ORCH_CONFIG["reflection"]["max_response_characters"],
    max_iterations=_ORCH_CONFIG["reflection"].get("max_refinement_iterations", 3),
)
_CHAT_LLM = LLMClient()   # reads AZURE_AI_* env vars; silently skipped if not configured

_INTENT_CLASSIFIER_SYSTEM = (
    "You are an intent classifier for an industrial reliability maintenance chat. "
    "Given conversation history and the latest user message, return one of three intent types.\n\n"
    "Return JSON: {\"intent_type\": \"pipeline\" | \"conversational\" | \"general\", \"reasoning\": \"<one sentence>\"}\n\n"
    "Rules:\n"
    "- \"pipeline\": use this when the user needs specific technical data about an asset — fault type, "
    "risk level, RUL, sensor readings, financial impact, anomaly status, or any numeric/diagnostic fact. "
    "This includes follow-up technical questions even if the asset was recently discussed. "
    "The system will re-run only the minimal agents needed to answer precisely. "
    "Examples: 'check M-104', 'what is the fault type?', 'can it run until Saturday?', "
    "'production impact if it fails', 'what is the risk level?', 'what is the RUL?'.\n"
    "- \"conversational\": ONLY use this when the user is asking about the outcome of a past "
    "OPERATOR DECISION — an approval, rejection, HITL action, or work order they explicitly acted on. "
    "Do NOT use for any question that requires technical asset data. "
    "Examples: 'I rejected M-104 — how will that affect production?', "
    "'what happens if I approve this work order?', 'why did the system recommend replacement?'.\n"
    "- \"general\": user is asking a conceptual or educational question with no asset data needed. "
    "Examples: 'what is RUL?', 'explain ISO 13373', 'how does vibration analysis work'.\n\n"
    "CRITICAL: questions about technical facts (fault type, vibration reading, risk, RUL, cost) "
    "are ALWAYS 'pipeline' — even as follow-ups. Only past operator decisions are 'conversational'."
)

_CONVERSATIONAL_LLM_SYSTEM = (
    "You are the DRO plant-maintenance assistant in a role-aware team chat. "
    "The user is asking a follow-up question about something that already happened in the conversation. "
    "Use the conversation history to understand the context (e.g. a recommendation was made, "
    "HITL was triggered, the user approved or rejected something) and answer concisely. "
    "Do not suggest running a new analysis — just answer the follow-up question directly. "
    "Never fabricate specific sensor readings, RUL values, or telemetry not mentioned in the history. "
    "Return JSON with exactly two keys: "
    "{\"answer\": \"<plain text answer>\", \"requires_telemetry\": false}"
)


def _llm_chat_classify(message: str, conversation_history: list | None) -> dict:
    """LLM-based intent classification: pipeline | conversational | general.
    Falls back to 'pipeline' if LLM is unavailable so existing routing is preserved.
    Swap `conversation_history` source here when migrating to Cosmos DB episodes.
    """
    if not _CHAT_LLM.is_configured():
        return {"intent_type": "pipeline", "reasoning": "LLM unavailable, using pipeline routing"}
    history_text = ""
    if conversation_history:
        recent = conversation_history[-6:]
        history_text = "\n".join(
            f"{msg.get('role', 'user').upper()}: {str(msg.get('content', ''))[:300]}"
            for msg in recent
            if str(msg.get("content", "")).strip()
        )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\nLatest message: {message}"
        if history_text else f"Latest message: {message}"
    )
    try:
        result = _CHAT_LLM.complete_json(
            system_prompt=_INTENT_CLASSIFIER_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=120,
        )
        if result and result.get("intent_type") in {"pipeline", "conversational", "general"}:
            return result
    except Exception:
        pass
    return {"intent_type": "pipeline", "reasoning": "classification failed, defaulting to pipeline"}


def _conversational_draft(message: str, conversation_history: list | None, persona: str, pipeline_context: dict | None = None) -> dict:
    """Build a response for a conversational follow-up using LLM with history context."""
    persona_context = build_persona_context(persona)
    if _CHAT_LLM.is_configured():
        try:
            history_text = ""
            if conversation_history:
                recent = conversation_history[-8:]
                history_text = "\n".join(
                    f"{msg.get('role', 'user').upper()}: {str(msg.get('content', ''))[:400]}"
                    for msg in recent
                    if str(msg.get("content", "")).strip()
                )
            asset_context_block = ""
            if pipeline_context:
                asset_id = pipeline_context.get("asset_id", "asset")
                asset_context_block = (
                    f"\n\n[LAST PIPELINE ANALYSIS — {asset_id}]\n"
                    f"{pipeline_context.get('summary', '')}"
                )
            user_prompt = (
                f"Conversation history:\n{history_text}{asset_context_block}\n\nUser follow-up: {message}"
                if history_text else f"{asset_context_block}\n\nUser follow-up: {message}".strip()
            )
            llm_resp = _CHAT_LLM.complete_json(
                system_prompt=(
                    _CONVERSATIONAL_LLM_SYSTEM + "\n\n"
                    + persona_response_prompt(persona_context)
                ),
                user_prompt=user_prompt,
                temperature=0.4,
                max_tokens=400,
            )
            if llm_resp and isinstance(llm_resp.get("answer"), str) and llm_resp["answer"].strip():
                return {"persona": persona_context.id, "persona_context": persona_context.model_dump(),
                        "response": llm_resp["answer"].strip(),
                        "call_plan": [], "needs_context": False, "clarification_required": False,
                        "source_type": "llm_conversational",
                        "agent_outputs": {"recommendation": None, "execution_result": None}}
        except Exception:
            pass
    return _clarification_draft(persona, "conversational", ["context"],
        ["Could you clarify what you'd like to know about the prior result?"],
        "I need a bit more context to answer that follow-up.")


def _learning_agent():
    """Factory kept patchable so tests and deployments can inject storage."""
    from src.agents.learning_memory_agent import LearningMemoryAgent
    return LearningMemoryAgent()


@traceable(name="Agent 8 - Learning History Query", run_type="retriever", tags=["dro", "agent-8"])
def _learning_history_draft(persona: str, limit: int = 3,
                            asset_id: str = "", asset_label: str = ""):
    persona_context = build_persona_context(persona)
    agent = _learning_agent()
    cases = agent.recent_cases(limit, asset_id=asset_id)
    rejections = agent.recent_rejections(limit, asset_id=asset_id)
    observations = agent.recent_chat_observations(limit, asset_id=asset_id)
    records = [
        {**case, "learning_type": "validated_outcome",
         "recorded_at": str(case.get("created_at", "")),
         "valid_closure": True, "closure_status": "completed",
         "physical_outcome_confirmed": True, "confirmed_outcome": True}
        for case in cases
    ] + list(rejections) + [
        {**observation, "learning_type": "chat_observation",
         "recorded_at": str(observation.get("created_at", "")),
         "valid_closure": False, "closure_status": "observed",
         "physical_outcome_confirmed": False, "confirmed_outcome": False}
        for observation in observations
    ]
    records.sort(key=lambda row: str(row.get("recorded_at", "")), reverse=True)
    records = records[:limit]
    label = asset_label or asset_id
    if not records:
        scope = f" for {label}" if label else ""
        return ({"persona": persona_context.id,
            "persona_context": persona_context.model_dump(),
            "response": (f"No completed or rejected closure records are available "
                f"in the operational learning history{scope}. The system learns "
                "maintenance completion and recommendation rejection."),
            "details": [], "actions": [], "case_references": [],
            "call_plan": ["agent_8"], "needs_context": False,
            "agent_outputs": {"learned_cases": [], "rejection_learnings": [],
                              "learning_records": []}}, records)
    details = []
    references = []
    for record in records:
        case_id = record.get("case_id", "unknown case")
        if record.get("learning_type") == "rejection_feedback":
            details.append(
                f"Learning for {label or record.get('asset_id', 'this asset')}: "
                f"{record.get('lesson', 'verify the recommendation against current evidence before proceeding')}"
            )
            references.append(f"Operational lesson {case_id}")
        elif record.get("learning_type") == "chat_observation":
            details.append(
                f"{record.get('observation_id', case_id)}: "
                f"{record.get('summary', 'chat observation recorded')}"
            )
            references.append(
                f"Chat learning observation {record.get('observation_id', case_id)}"
            )
        else:
            details.append(
                f"{case_id}: {record.get('fault_mode', 'unknown fault')}; "
                f"outcome: {record.get('outcome', 'not recorded')}. "
                f"Learning: {record.get('content', 'not recorded')}"
            )
            references.append(f"Learned case {case_id}")
    audience = f"{persona_context.role} learning context"
    outcome_count = sum(row.get("learning_type") == "validated_outcome" for row in records)
    rejection_count = sum(row.get("learning_type") == "rejection_feedback" for row in records)
    observation_count = sum(row.get("learning_type") == "chat_observation" for row in records)
    if rejection_count:
        # Put the lesson in the headline as well as in ``details``.  The
        # frontend and API consumers are allowed to render only ``response``;
        # a count-only headline made a valid Agent 8 learning look empty.
        lesson = next(
            (str(row.get("lesson", "")).strip() for row in records
             if row.get("learning_type") == "rejection_feedback"
             and str(row.get("lesson", "")).strip()),
            "verify the recommendation against current evidence before proceeding",
        )
        response = (f"{audience}: Learning update{' for ' + label if label else ''}: "
                    f"that {lesson} Future recommendations will apply this "
                    "guidance and request the required validation before execution.")
    elif observation_count:
        summaries = [
            str(row.get("summary", "")).strip() for row in records
            if row.get("learning_type") == "chat_observation"
            and str(row.get("summary", "")).strip()
        ]
        observed = "; ".join(summaries[:3]) if summaries else "a reusable operational pattern"
        response = (f"{audience}: Learning update{' for ' + label if label else ''} "
                    f"from {observation_count} recent observation(s): {observed}")
    else:
        response = (f"{audience}: the last {outcome_count} validated closed failure "
                    f"case{'s' if outcome_count != 1 else ''} are summarized below.")
    actions = ["Use confirmed outcomes to review recurring causes and maintenance effectiveness."]
    if any(row.get("reliability_review_required") for row in records):
        actions.append("Reliability Engineering review is required for repeated rejection feedback.")
    return ({"persona": persona_context.id,
        "persona_context": persona_context.model_dump(),
        "response": response,
        "details": details,
        "actions": actions,
        "case_references": references,
        "call_plan": ["agent_8"], "needs_context": False,
        "agent_outputs": {"learned_cases": cases,
                          "rejection_learnings": rejections,
                          "chat_observations": observations,
                          "learning_records": records}}, records)


@traceable(name="Agent 8 - Chat Learning Observer", run_type="chain", tags=["dro", "agent-8", "chat-learning"])
def _observe_chat_learning(event: dict, persona_context):
    return _learning_agent().observe_chat(
        event, persona_context=persona_context
    ).model_dump()


def _attach_chat_learning(result: Dict[str, Any], req: "ChatRequest",
                          run_id: str, conversation_id: str, intent: str,
                          persona_context, requested_assets=None,
                          supplied_asset: str = "") -> Dict[str, Any]:
    assets = {
        str(asset_id).upper()
        for _, asset_id, _ in (requested_assets or [])
        if str(asset_id).strip()
    }
    if supplied_asset:
        assets.add(str(supplied_asset).upper())
    event = {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "message": req.message,
        "response": result.get("response", ""),
        "intent": intent,
        "persona": persona_context.id,
        "asset_ids": sorted(assets),
        "result": result,
    }
    try:
        observation = _observe_chat_learning(event, persona_context)
    except Exception as exc:
        observation = {
            "status": "failed",
            "learning_type": "chat_observation",
            "learning_recorded": False,
            "reason": f"Agent 8 chat observation failed: {exc}",
            "persistence_status": "failed",
        }
    result["learning_observation"] = observation
    agent_outputs = result.get("agent_outputs")
    if not isinstance(agent_outputs, dict):
        agent_outputs = {}
        result["agent_outputs"] = agent_outputs
    agent_outputs["agent8_observation"] = observation
    return result


_MULTI_ASSET_SCENARIOS = load_fleet_scenario_map()

Persona = Literal["supervisor", "engineer", "maintenance", "manager",
                  "executive", "md", "ot", "safety"]
_PERSONAS = {"supervisor", "engineer", "maintenance", "manager",
             "executive", "md", "ot", "safety"}
_PERSONA_LABELS = {
    "supervisor":  "Plant Supervisor",
    "engineer":    "Reliability Engineer",
    "maintenance": "Maintenance Planner",
    "manager":     "Plant Manager",
    "executive":   "VP Operations",
    "md":          "Managing Director",
    "ot":          "OT / Controls",
    "safety":      "Safety Officer",
}


_GENERAL_RAG = None


def _general_rag_service():
    """Lazy and patchable so startup and tests do not require cloud retrieval."""
    global _GENERAL_RAG
    if _GENERAL_RAG is None:
        _GENERAL_RAG = GovernedKnowledgeRAG()
    return _GENERAL_RAG


def _general_knowledge_draft(message: str, persona: str):
    started = time.perf_counter()
    persona_context = build_persona_context(persona)
    result = _general_rag_service().answer(
        message, llm_client=_CHAT_LLM, persona_context=persona_context,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    source_type = {
        "grounded": "approved_rag",
        "no_match": "rag_no_match",
        "retrieval_failed": "rag_error",
        "blocked": "rag_blocked",
    }.get(result.status, "rag_error")
    result_dict = result.to_dict()
    draft = {
        "persona": persona_context.id,
        "persona_context": persona_context.model_dump(),
        "response": result.answer,
        "details": [],
        "actions": [],
        "call_plan": ["knowledge_rag"],
        "needs_context": False,
        "clarification_required": False,
        "source_type": source_type,
        "citations": [item.model_dump() for item in result.citations],
        "rag": {
            "status": result.status,
            "status_reason": result.status_reason,
            "retrieval_query": result.retrieval_query,
            "retrieval_hit_count": result.retrieval_hit_count,
            "index_version": result.index_version,
            "llm_used": result.llm_used,
        },
        "agent_outputs": {"knowledge_rag": result_dict},
    }
    state = {"pipeline_log": [{
        "node": "knowledge_rag",
        "status": result.status,
        "latency_ms": elapsed_ms,
        "retrieval_hit_count": result.retrieval_hit_count,
    }]}
    return draft, state


def _is_general_knowledge_request(message: str, plan, classified: str,
                                  signal, supplied_asset: str,
                                  requested_assets: list) -> bool:
    if signal is not None or supplied_asset or requested_assets:
        return False
    if plan.intent in {"concept", "general"}:
        return True
    if classified != "general" or plan.intent in {"fleet", "learning_history"}:
        return False
    text = str(message).lower()
    return any(marker in text for marker in (
        "what is", "what does", "define", "explain", "how does",
        "meaning of", "common causes", "best practices",
    ))


def _clarification_draft(persona: str, intent: str, missing: list[str],
                         questions: list[str], reason: str, asset_id: str = ""):
    context = build_persona_context(persona)
    return {"persona": context.id, "persona_context": context.model_dump(),
        "response": reason, "details": [], "actions": [],
        "call_plan": [], "needs_context": True, "clarification_required": True,
        "clarification": {"intent": intent, "missing_fields": missing,
                           "questions": questions, "asset_id": asset_id},
        "agent_outputs": {"recommendation": None, "execution_result": None}}


def _asset_from_message(message: str) -> str:
    upper = str(message).upper()
    display_match = next((asset_id for display,(asset_id,_) in _MULTI_ASSET_SCENARIOS.items()
                          if display in upper), "")
    if display_match:
        return display_match
    known = next((asset_id for asset_id in load_asset_master() if asset_id in upper), "")
    if known:
        return known
    match = re.search(r"\bAST_[A-Z0-9_]+\b", upper)
    if match:
        return match.group(0)
    # Capture short asset-ID patterns like G-115, E-501, L-701 that may not yet be registered
    short_match = re.search(r"\b([A-Z]{1,3}-\d{3,4})\b", upper)
    return short_match.group(1) if short_match else ""


def _scenario_for_asset(asset_id: str) -> str:
    return next((scenario for _,(canonical,scenario) in _MULTI_ASSET_SCENARIOS.items()
                 if canonical == asset_id), "")


def _assets_from_message(message: str) -> list[tuple[str,str,str]]:
    """Return all ordered asset mentions, including unregistered identifiers."""
    upper = str(message).upper()
    positions = []
    for display,(asset_id,scenario) in _MULTI_ASSET_SCENARIOS.items():
        candidates = [(upper.find(display),display)]
        candidates.append((upper.find(asset_id),display))
        valid = [(pos,label) for pos,label in candidates if pos >= 0]
        if valid: positions.append((min(pos for pos,_ in valid),display,asset_id,scenario))
    known_aliases = {
        alias
        for display, (asset_id, _) in _MULTI_ASSET_SCENARIOS.items()
        for alias in (display, asset_id)
    }
    for match in re.finditer(r"\b(?:[A-Z]{1,3}-\d{3,4}|AST_[A-Z0-9_]+)\b", upper):
        identifier = match.group(0)
        if identifier not in known_aliases:
            positions.append((match.start(), identifier, identifier, ""))
    positions.sort(key=lambda item:item[0])
    result = []
    seen = set()
    for _, display, asset_id, scenario in positions:
        key = asset_id or display
        if key in seen:
            continue
        seen.add(key)
        result.append((display, asset_id, scenario))
    return result


def _deadline_for_recommendation(recommendation) -> str:
    if recommendation.window_chosen: return recommendation.window_chosen
    return {"immediate":"today","urgent":"within 48 hours","planned":"this week",
            "monitor":"continue monitoring"}.get(recommendation.urgency,"review this week")


def _multi_asset_plan(requested_assets, fleet_snapshot: dict, persona: str,
                      run_id: str, checkpoint_thread_id: str = ""):
    """Evaluate named assets only from explicitly supplied fleet evidence."""
    persona_context = build_persona_context(persona)
    results=[]; combined_log=[]; details=[]; actions=[]
    for display,asset_id,scenario in requested_assets:
        evidence=(fleet_snapshot.get(display) or fleet_snapshot.get(asset_id)
                  if isinstance(fleet_snapshot,dict) else None)
        if not isinstance(evidence,dict):
            results.append({"display_asset_id":display,"asset_id":asset_id,
                "status":"unavailable","action":"provide validated telemetry",
                "deadline":"before planning","reason":"no fleet evidence was supplied for this asset",
                "recommendation":None,"pipeline_log":[]})
            details.append(f"{display}: no validated fleet evidence supplied; no agents were run.")
            actions.append(f"{display}: provide validated telemetry before planning")
            continue
        try:
            signal=evidence.get("signal")
            selected_scenario=evidence.get("scenario")
            if signal is None and selected_scenario:
                signal=_get_demo_signal(str(selected_scenario),int(evidence.get("row_index",-1)))
            if not isinstance(signal,dict):
                raise ValueError("fleet evidence must include signal or scenario")
            if str(signal.get("asset_id","")) != asset_id:
                results.append({"display_asset_id":display,"asset_id":asset_id,
                    "scenario":selected_scenario,"status":"data_review",
                    "action":"correct fleet evidence","deadline":"before planning",
                    "reason":"fleet evidence asset identity does not match the requested asset",
                    "recommendation":None,"pipeline_log":[]})
                details.append(f"{display}: supplied evidence belongs to another asset; no agents were run.")
                actions.append(f"{display}: correct fleet evidence before planning")
                continue
            state=run_pipeline(
                deepcopy(signal), run_id=f"{run_id}-{display}", intent="full",
                approval_status="pending", persona=persona,
                thread_id=(f"{checkpoint_thread_id}:{display}"
                           if checkpoint_thread_id else ""),
            )
            log=[{**row,"asset":display} for row in state.get("pipeline_log",[])]
            combined_log.extend(log)
            recommendation=state.get("recommendation")
            trusted=state.get("trusted_signal")
            if recommendation is not None and getattr(recommendation,"recommendation_eligible",False):
                deadline=_deadline_for_recommendation(recommendation)
                action=recommendation.recommended_action.name.replace("_"," ")
                status="action_required"
                reason=recommendation.rationale
                actions.append(f"{display}: {action} — {deadline}")
            elif trusted is not None and not getattr(trusted,"downstream_eligible",False):
                deadline="before any maintenance decision"
                action="resolve telemetry/data-quality issue"
                status="data_review"
                reason=getattr(trusted,"routing_reason","") or "signal did not pass Agent 1"
                actions.append(f"{display}: {action} — {deadline}")
            else:
                deadline="ongoing"
                action="continue monitoring"
                status="no_action_required"
                reason="no actionable recommendation was produced"
                actions.append(f"{display}: {action}")
            details.append(f"{display}: {action}; due {deadline}. {reason}")
            results.append({"display_asset_id":display,"asset_id":asset_id,
                "scenario":selected_scenario,
                "status":status,"action":action,"deadline":deadline,"reason":reason,
                "recommendation":recommendation.to_dict() if recommendation else None,
                "pipeline_log":log})
        except Exception:
            results.append({"display_asset_id":display,"asset_id":asset_id,
                "scenario":evidence.get("scenario"),
                "status":"unavailable","action":"manual review","deadline":"before planning",
                "reason":"asset analysis was unavailable","recommendation":None,"pipeline_log":[]})
            details.append(f"{display}: analysis unavailable; manual review required before planning.")
            actions.append(f"{display}: manual review — before planning")
    evaluated=sum(bool(row["pipeline_log"]) for row in results)
    draft={"persona":persona_context.id,
        "persona_context": persona_context.model_dump(),
        "response":f"{persona_context.role} context: evidence-based action plan evaluated {evaluated} of {len(results)} requested assets.",
        "details":details,"actions":actions,"call_plan":["agents_1_to_6_per_asset"],
        "needs_context":False,"clarification_required":False,
        "multi_asset_results":results,"agent_outputs":{"recommendation":None,"execution_result":None}}
    return draft,{"pipeline_log":combined_log}

# ************** Added by Prateek Mittal on 20th July 2026 ******************
@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    error_id = f"ERR-{uuid.uuid4().hex[:10].upper()}"
    try: write_audit(_AUDIT_PATH,event="api_error",status="unexpected",error_code=error_id)
    except Exception: pass
    return JSONResponse(status_code=500,content={"error":{"code":"INTERNAL_ERROR",
        "message":"The request could not be completed safely.","error_id":error_id,"retryable":True}})
# ***********************

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve the React frontend (production build) ───────────────────────────────
_FRONTEND_DIST = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "react-app", "dist"
)


# ── Health endpoint ───────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    """
    Probe critical subsystems and return an aggregated health status.

    HTTP 200 → status "ok" or "degraded" (pipeline can still serve requests).
    HTTP 503 → status "unhealthy" (one or more critical checks failed).

    Checks
    ------
    config      : orchestrator_config.json loaded at startup (always ok if server is up)
    asset_master: asset_master.json readable and non-empty  [critical]
    audit_log   : audit directory is writable               [critical]
    sop_corpus  : data/sops/ directory exists and has files [degraded if missing]
    llm         : Azure LLM credentials present             [informational only]
    hitl_store  : durable human-decision repository         [critical]
    checkpoints : LangGraph checkpoint repository           [critical when configured]
    """
    checks: Dict[str, Any] = {}
    is_unhealthy = False
    is_degraded = False

    # 1. Config — always passes if the server started successfully
    checks["config"] = {"status": "ok", "detail": "orchestrator_config.json loaded"}

    # 2. Asset master
    try:
        assets = load_asset_master()
        asset_count = len(assets) if assets else 0
        if asset_count == 0:
            checks["asset_master"] = {"status": "error", "detail": "asset_master.json is empty", "asset_count": 0}
            is_unhealthy = True
        else:
            checks["asset_master"] = {"status": "ok", "detail": f"{asset_count} assets loaded", "asset_count": asset_count}
    except Exception as exc:
        checks["asset_master"] = {"status": "error", "detail": str(exc), "asset_count": 0}
        is_unhealthy = True

    # 3. Audit log directory writable
    try:
        audit_dir = os.path.dirname(os.path.abspath(_AUDIT_PATH))
        if not os.path.isdir(audit_dir):
            checks["audit_log"] = {"status": "error", "detail": f"audit directory missing: {audit_dir}"}
            is_unhealthy = True
        else:
            probe = os.path.join(audit_dir, ".health_probe")
            with open(probe, "w") as _f:
                _f.write("")
            os.remove(probe)
            checks["audit_log"] = {"status": "ok", "detail": "audit directory writable"}
    except Exception as exc:
        checks["audit_log"] = {"status": "error", "detail": str(exc)}
        is_unhealthy = True

    # 4. SOP corpus (degraded, not unhealthy — synthetic fallbacks exist)
    try:
        sop_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sops")
        sop_dir = os.path.normpath(sop_dir)
        if not os.path.isdir(sop_dir):
            checks["sop_corpus"] = {"status": "warning", "detail": "data/sops/ directory not found; using synthetic fallbacks", "document_count": 0}
            is_degraded = True
        else:
            docs = [f for f in os.listdir(sop_dir) if not f.startswith(".") and f.lower().endswith((".pdf", ".txt"))]
            if not docs:
                checks["sop_corpus"] = {"status": "warning", "detail": "data/sops/ is empty; using synthetic fallbacks", "document_count": 0}
                is_degraded = True
            else:
                checks["sop_corpus"] = {"status": "ok", "detail": f"{len(docs)} document(s) available", "document_count": len(docs)}
    except Exception as exc:
        checks["sop_corpus"] = {"status": "warning", "detail": str(exc), "document_count": 0}
        is_degraded = True

    # 5. LLM — informational only; pipeline is fully deterministic without it
    if _CHAT_LLM.is_configured():
        checks["llm"] = {"status": "ok", "detail": "Azure LLM credentials present"}
    else:
        checks["llm"] = {"status": "not_configured", "detail": "Azure LLM credentials absent; deterministic fallbacks active"}

    # 6. Durable state stores. Probes deliberately omit hostnames and URLs.
    hitl_probe = (_HITL_REPOSITORY.probe()
                  if hasattr(_HITL_REPOSITORY, "probe")
                  else {"status": "error", "backend": "unknown"})
    checks["hitl_store"] = hitl_probe
    if hitl_probe["status"] != "ok":
        is_unhealthy = True

    checkpoint_probe = graph_checkpoint_health()
    checks["checkpoints"] = checkpoint_probe
    if (checkpoint_probe.get("backend") == "postgres"
            and checkpoint_probe["status"] != "ok"):
        is_unhealthy = True

    # Aggregate
    if is_unhealthy:
        overall = "unhealthy"
    elif is_degraded:
        overall = "degraded"
    else:
        overall = "ok"

    response_body = {
        "status": overall,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "version": app.version,
        "checks": checks,
    }

    if is_unhealthy:
        return JSONResponse(status_code=503, content=response_body)
    return response_body


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    """
    Authenticate with username + password. Returns a JWT valid for DRO_JWT_EXPIRE_HOURS.

    The token encodes the user's role and the persona list they are permitted to act as.
    Include it as  Authorization: Bearer <token>  on all protected requests.
    """
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )
    token = create_access_token(user)
    from src.api.auth import _load_roles
    allowed_personas = _load_roles().get(user.get("role", ""), [])
    return LoginResponse(
        access_token=token,
        username=user["username"],
        display_name=user.get("display_name", user["username"]),
        role=user.get("role", ""),
        allowed_personas=allowed_personas,
        expires_in_hours=_JWT_EXPIRE_HOURS,
    )


@app.get("/api/auth/me")
def auth_me(user: Dict = Depends(get_current_user)):
    """Return the current user's profile decoded from their JWT. No DB hit."""
    return {
        "username":        user.get("sub"),
        "display_name":    user.get("display_name"),
        "role":            user.get("role"),
        "allowed_personas": user.get("allowed_personas", []),
    }


# ── Runtime work-order store ─────────────────────────────────────────────────
# Starts empty; work orders are created in real time by the executor pipeline.
_WORKORDER_REPOSITORY = build_work_order_repository()
_WORKORDERS: List[Dict] = []
_WO_INDEX: Dict[str, Dict] = {}

# In-memory HITL session store — keyed by run_id, holds partial pipeline state
_HITL_STORE: Dict[str, Dict[str, Any]] = {}
_HITL_RESOLVED: Dict[str, float] = {}
_CHAT_CONTEXT_STORE: Dict[str, Dict[str, Any]] = {}
_MAX_CHAT_CONTEXTS = int(_ORCH_CONFIG["chat"].get("max_context_sessions",500))
_MAX_CHAT_TURNS = int(_ORCH_CONFIG["chat"].get("max_context_turns",12))
_CHAT_CONTEXT_TTL = int(_ORCH_CONFIG["chat"].get("context_ttl_seconds",1800))
_HITL_TTL = int(_ORCH_CONFIG.get("hitl",{}).get("session_ttl_seconds",1800))
_HITL_PERMISSIONS = _ORCH_CONFIG.get("hitl",{}).get("permissions",{})
_HITL_LEASE_SECONDS = int(_ORCH_CONFIG.get("hitl",{}).get("claim_lease_seconds",120))
_HITL_REPOSITORY = build_hitl_repository(_ORCH_CONFIG)


def _list_workorders() -> List[Dict]:
    return _WORKORDER_REPOSITORY.list_all()


def _get_workorder(wo_id: str) -> Optional[Dict]:
    return _WORKORDER_REPOSITORY.get(wo_id)


def _save_workorder(work_order: Dict[str, Any]) -> Dict[str, Any]:
    saved = _WORKORDER_REPOSITORY.upsert(work_order)
    _WO_INDEX[saved["id"]] = saved
    return saved


def _asset_lifecycle(asset_id: str) -> Optional[Dict[str, Any]]:
    """Return the latest backend work-order lifecycle for an asset.

    Chat status answers must reflect durable work-order state, not browser
    session overrides.  A pending/scheduled/in-progress intervention means
    the asset is under controlled maintenance (warning); a closed intervention
    means the repair is complete (healthy).
    """
    key = str(asset_id or "").strip().upper()
    if not key:
        return None
    lookup_keys = {key}
    for display_id, (canonical_id, _) in _MULTI_ASSET_SCENARIOS.items():
        if str(display_id).strip().upper() == key:
            lookup_keys.add(str(canonical_id).strip().upper())
    rows = []
    for row in _list_workorders():
        candidates = (row.get("asset_id"), row.get("asset_display"), row.get("as"))
        if any(str(value or "").strip().upper() in lookup_keys for value in candidates):
            rows.append(row)
    row = max(rows, key=lambda item: str(item.get("updated_at", ""))) if rows else {}

    # A recommendation can be approved before the executor/CMMS reports its
    # first operational status.  Treat all known active states consistently so
    # a restart cannot expose the raw pre-intervention critical projection.
    status = str(row.get("status", "")).strip().lower().replace("-", " ")
    closed_statuses = {"closed", "completed", "complete", "done"}
    active_statuses = {
        "pending", "approved", "open", "active", "queued", "assigned",
        "scheduled", "in progress", "in_progress", "in review",
    }
    if status in closed_statuses:
        lifecycle = "healthy"
    elif status in active_statuses:
        lifecycle = "warning"
    elif status in {"rejected", "declined", "deferred", "cancelled", "canceled"}:
        # Some CMMS adapters materialise a rejected decision as a non-action
        # work-order row.  It is not a repair and must not be shown healthy.
        return {
            "status": "critical", "work_order_id": row.get("id", ""),
            "work_order_status": row.get("status", ""),
            "decision": "rejected",
        }
    else:
        lifecycle = None

    # A rejection is a valid Agent 8 closure, but it is not a physical repair.
    # If it is newer than the last work-order state, preserve the unresolved
    # risk in status answers rather than allowing the status-depth pipeline to
    # turn it into a false healthy result.
    rejection_rows = []
    try:
        learning_agent = _learning_agent()
        for rejection_key in lookup_keys:
            try:
                rows_for_key = learning_agent.recent_rejections(
                    limit=1, asset_id=rejection_key
                )
            except Exception:
                # A repository may support only one identifier form; continue
                # with the display/canonical aliases that are available.
                rows_for_key = []
            rejection_rows.extend(
                rows_for_key
            )
    except Exception:
        rejection_rows = []
    rejection_rows.sort(
        key=lambda item: str(
            item.get("recorded_at")
            or item.get("decided_at")
            or item.get("rejected_at")
            or ""
        ),
        reverse=True,
    )
    latest_rejection = rejection_rows[0] if rejection_rows else None
    if latest_rejection:
        row_time = str(row.get("updated_at", ""))
        rejection_time = str(
            latest_rejection.get("recorded_at")
            or latest_rejection.get("decided_at")
            or latest_rejection.get("rejected_at")
            or ""
        )
        if not row_time or rejection_time >= row_time:
            return {
                "status": "critical", "work_order_id": row.get("id", "") if lifecycle else "",
                "work_order_status": row.get("status", "") if lifecycle else "Rejected",
                "decision": "rejected",
                "fault_mode": latest_rejection.get("fault_mode", ""),
                "decision_at": rejection_time,
            }

    if lifecycle is None:
        return None
    return {"status": lifecycle, "work_order_id": row.get("id", ""),
            "work_order_status": row.get("status", "")}


def _remember_chat_context(conversation_id: str, context: dict) -> None:
    if conversation_id not in _CHAT_CONTEXT_STORE and len(_CHAT_CONTEXT_STORE) >= _MAX_CHAT_CONTEXTS:
        _CHAT_CONTEXT_STORE.pop(next(iter(_CHAT_CONTEXT_STORE)))
    stored=deepcopy(context)
    meta=stored.setdefault("_meta",{})
    meta["updated_at"]=time.time()
    meta["turns"]=min(int(meta.get("turns",0))+1,_MAX_CHAT_TURNS+1)
    save_durable_chat_context(conversation_id, stored, _CHAT_CONTEXT_STORE)


def _load_chat_context(conversation_id: str) -> dict:
    context=load_durable_chat_context(conversation_id, _CHAT_CONTEXT_STORE)
    meta=context.get("_meta",{})
    if context and (time.time()-float(meta.get("updated_at",0))>_CHAT_CONTEXT_TTL
                    or int(meta.get("turns",0))>=_MAX_CHAT_TURNS):
        # Expire only transient pipeline context. The durable transcript must
        # remain available for the history view and audit trail.
        history = context.get("_history", [])
        clear_durable_chat_context(conversation_id, _CHAT_CONTEXT_STORE)
        if isinstance(history, list) and history:
            save_durable_chat_context(
                conversation_id, {"_history": history}, _CHAT_CONTEXT_STORE
            )
        return {"_history": history} if isinstance(history, list) else {}
    return context


def _persist_chat_turn(conversation_id: str, req: "ChatRequest",
                       user: Dict[str, Any], response: Dict[str, Any]) -> None:
    """Persist the visible transcript independently of short-lived context."""
    append_chat_history(
        conversation_id,
        str(user.get("sub", "")),
        {
            "run_id": response.get("run_id", ""),
            "persona": req.persona,
            "user_message": req.message,
            "response": response,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        _CHAT_CONTEXT_STORE,
        max_turns=int(_ORCH_CONFIG["chat"].get("max_history_turns", 500)),
    )


def _public_context(context: Optional[Dict]) -> dict:
    """Reject client attempts to write reserved conversation state keys."""
    return {str(key):deepcopy(value) for key,value in (context or {}).items()
            if not str(key).startswith("_")}


def _authorize_hitl(persona: str, gate: str) -> None:
    if persona not in _PERSONAS:
        raise HTTPException(422, f"Unknown persona: {persona}")
    permitted = _HITL_PERMISSIONS.get(gate, [])
    if persona not in set(permitted):
        labels = [_PERSONA_LABELS.get(p, p) for p in permitted]
        who = " and ".join(labels) if len(labels) <= 2 else ", ".join(labels[:-1]) + " and " + labels[-1]
        raise HTTPException(403, f"Only the {who} can resolve this gate. Please contact them to get this action taken.")


def _hitl_access(gate: str, persona: str) -> Dict[str, Any]:
    """Return the UI authorization contract from the backend permission source."""
    permitted = list(dict.fromkeys(_HITL_PERMISSIONS.get(gate, [])))
    permitted_names = [_PERSONA_LABELS.get(item, item) for item in permitted]
    can_resolve = persona in set(permitted)
    return {
        "gate": gate,
        "can_resolve": can_resolve,
        "mode": "actionable" if can_resolve else "read_only",
        "selected_persona": persona,
        "allowed_personas": permitted,
        "allowed_persona_names": permitted_names,
        "message": (
            "You can resolve this decision."
            if can_resolve
            else "Decision controls are hidden because this persona is not "
                 "authorized to resolve this gate."
        ),
    }


def _with_hitl_access(payload: Optional[Dict], gate: str,
                      persona: str) -> Optional[Dict]:
    """Attach read/action authorization without changing the gate evidence."""
    if payload is None:
        return None
    result = dict(payload)
    result["access"] = _hitl_access(gate, persona)
    return result


def _recommendation_payload(recommendation, persona: str) -> Optional[Dict]:
    """Serialize a recommendation and attach its execution-gate permissions."""
    if recommendation is None:
        return None
    try:
        if hasattr(recommendation, "model_dump"):
            result = recommendation.model_dump()
        elif hasattr(recommendation, "to_dict"):
            result = recommendation.to_dict()
        elif isinstance(recommendation, dict):
            result = dict(recommendation)
        else:
            return None
    except Exception:
        return None
    result["hitl_access"] = _hitl_access("execution", persona)
    return result


def _claim_hitl(run_id: str, gate: str, action: str, allowed_actions: set[str],
                persona: str, rationale: str = "") -> dict:
    """Validate a decision before consuming its one-shot HITL session."""
    action=action.upper()
    if action not in allowed_actions:
        raise HTTPException(422,f"Invalid {gate} action. Allowed values: {', '.join(sorted(allowed_actions))}.")
    _authorize_hitl(persona,gate)
    memory=_HITL_STORE.get(run_id)
    persisted=_HITL_REPOSITORY.get_session(run_id)
    if memory and (not persisted or float(memory.get("created_at",0))>=float(persisted.get("updated_at",0))):
        created=float(memory.get("created_at",time.time()))
        _HITL_REPOSITORY.save_session(run_id,memory.get("type",""),memory.get("persona",persona),
            memory,created,created+_HITL_TTL)
    claim=_HITL_REPOSITORY.claim(run_id,gate,persona,action,_HITL_LEASE_SECONDS)
    outcome=claim.get("outcome")
    if outcome=="not_found":
        raise HTTPException(404,f"HITL session '{run_id}' was not found.")
    if outcome=="wrong_gate":
        raise HTTPException(409,f"HITL session '{run_id}' is for {claim.get('actual_gate')}, not {gate}.")
    if outcome=="expired":
        _HITL_STORE.pop(run_id,None)
        raise HTTPException(410,f"HITL session '{run_id}' has expired.")
    if outcome in {"resolved","processing"}:
        raise HTTPException(409,f"HITL session '{run_id}' is already {outcome}.")
    if outcome!="claimed":
        raise HTTPException(409,f"HITL session '{run_id}' cannot be resolved from state {outcome}.")
    stored=claim["payload"]
    if not _HITL_REPOSITORY.complete(run_id,claim["claim_token"],gate,action,persona,
                                     rationale[:1000]):
        raise HTTPException(409,f"HITL session '{run_id}' lost its decision lease.")
    _HITL_STORE.pop(run_id,None)
    _HITL_RESOLVED[run_id]=time.time()
    return stored


def _stamp_hitl_sessions() -> None:
    """Backfill timestamps for sessions created by existing gate code."""
    now=time.time()
    for value in _HITL_STORE.values():
        value.setdefault("created_at",now)
    for run_id,value in _HITL_STORE.items():
        try:
            _HITL_REPOSITORY.save_session(run_id,value.get("type",""),
                value.get("persona","supervisor"),value,float(value["created_at"]),
                float(value["created_at"])+_HITL_TTL)
        except (TypeError,ValueError) as exc:
            raise HTTPException(500,f"HITL session could not be serialized: {exc}") from exc
        except Exception as exc:
            raise HTTPException(503,"Durable HITL persistence is unavailable.") from exc


def _hitl_public_session(session: dict) -> dict:
    """Expose operational metadata without leaking the stored pipeline payload."""
    return {key: session.get(key) for key in (
        "run_id", "gate", "persona", "status", "created_at", "expires_at",
        "claimed_at", "claimed_by", "action", "updated_at",
    )}


def _create_advisory_gate(run_id: str, risk, persona: str, blocked: bool = False):
    if (blocked or risk is None
            or getattr(risk,"assessment_source","")!="rules+llm_fallback"
            or not getattr(risk,"advisory_note","")):
        return None
    _HITL_STORE[run_id]={"type":"advisory","risk_assessment":risk,
        "persona":persona,"created_at":time.time()}
    return _with_hitl_access(
        {"type":"advisory","advisory_note":risk.advisory_note,"run_id":run_id},
        "advisory", persona,
    )

# Confidence threshold below which the FI HITL gate fires (Agent 3)
_DIAGNOSIS_CONFIDENCE_THRESHOLD = 0.60


def _build_hitl_gates(state: Dict, run_id: str, persona: str, intent: str) -> Dict:
    """Compute HITL gate payloads from raw pipeline state and register with _HITL_STORE.
    Used by both /api/pipeline/run and inline pipeline execution in /api/chat."""
    trusted  = state.get("trusted_signal")
    anomaly  = state.get("anomaly_event")
    diagnosis= state.get("fault_diagnosis")
    knowledge= state.get("knowledge_guidance")

    hitl_required = None
    if trusted is not None:
        from src.schemas.bearing_signal import ValidationStatus as _VS
        if getattr(trusted, "validation_status", None) == _VS.FLAGGED:
            from src.tools.remediation import missing_critical_fields as _mcf, imputable_fields as _impf
            from src.tools.config_loader import load_config as _lcfg
            _dfa_cfg = _lcfg()
            _HITL_STORE[run_id] = {"type":"remediation","trusted":trusted,"persona":persona,"intent":intent}
            hitl_required = {
                "type":"remediation","run_id":run_id,
                "missing_fields":_mcf(trusted,_dfa_cfg),
                "imputable_fields":_impf(trusted,_dfa_cfg),
                "quality_score":(trusted.quality_report.overall_score
                                 if getattr(trusted,"quality_report",None) else None),
            }

    hitl_monitoring = None
    if hitl_required is None and anomaly is not None:
        _ev = anomaly.evidence or {}
        _ewma = _ev.get("ewma", {})
        if _ewma.get("fired", False) and not _ev.get("t2_fired", True):
            _HITL_STORE[run_id] = {"type":"monitoring","trusted":trusted,
                                   "anomaly_event":anomaly,"persona":persona,"intent":intent}
            hitl_monitoring = {
                "type":"monitoring","run_id":run_id,
                "anomaly_score":anomaly.anomaly_score,
                "triggered_features":anomaly.triggered_features,
                "reason":anomaly.reason,"regime":anomaly.regime,
                "ewma_triggered":_ewma.get("triggered_features",[]),
                "ewma_score":_ewma.get("score",0.0),
            }

    hitl_diagnosis = None
    if hitl_required is None and hitl_monitoring is None and diagnosis is not None:
        if diagnosis.confidence < _DIAGNOSIS_CONFIDENCE_THRESHOLD:
            _HITL_STORE[run_id] = {"type":"diagnosis","trusted":trusted,"anomaly_event":anomaly,
                                   "fault_diagnosis":diagnosis,"persona":persona,"intent":intent}
            hitl_diagnosis = {
                "type":"diagnosis","run_id":run_id,
                "fault_mode":diagnosis.fault_mode,"fault_code":diagnosis.fault_code,
                "confidence":diagnosis.confidence,"iso_stage":diagnosis.iso_stage,
                "narrative":diagnosis.narrative,
            }

    hitl_knowledge = None
    if (hitl_required is None and hitl_monitoring is None
            and hitl_diagnosis is None and knowledge is not None):
        if not knowledge.source_documents:
            _HITL_STORE[run_id] = {"type":"knowledge","trusted":trusted,"anomaly_event":anomaly,
                                   "fault_diagnosis":diagnosis,"risk_assessment":state.get("risk_assessment"),
                                   "knowledge_guidance":knowledge,"persona":persona,"intent":intent}
            hitl_knowledge = {
                "type":"knowledge","run_id":run_id,
                "fault_mode":diagnosis.fault_mode if diagnosis else "unknown",
                "asset_id":(trusted.raw.asset_id if trusted and hasattr(trusted,"raw") else ""),
            }

    _risk = state.get("risk_assessment")
    hitl_advisory = _create_advisory_gate(run_id, _risk, persona,
                    blocked=any((hitl_required,hitl_monitoring,hitl_diagnosis,hitl_knowledge)))

    recommendation = _recommendation_payload(state.get("recommendation"), persona)

    return {
        "hitl_required":  _with_hitl_access(hitl_required,"remediation",persona),
        "hitl_monitoring":_with_hitl_access(hitl_monitoring,"monitoring",persona),
        "hitl_diagnosis": _with_hitl_access(hitl_diagnosis,"diagnosis",persona),
        "hitl_knowledge": _with_hitl_access(hitl_knowledge,"knowledge",persona),
        "hitl_advisory":  hitl_advisory,
        "recommendation": recommendation,
    }


# ── Pydantic request/response models ─────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    signal: Dict[str, Any]
    persona: Persona = "supervisor"
    scenario: Optional[str] = None   # convenience: run a named demo scenario
    row_index: int = -1              # row index within scenario (-1 = last)
    query: Optional[str] = None      # natural-language query → controls pipeline depth
    thread_id: Optional[str] = None  # stable caller/conversation identity


class PipelineRunResponse(BaseModel):
    run_id: str
    persona: str
    persona_context: Optional[Dict] = None
    headline: str
    details: List[str]
    actions: List[str]
    tags: List[str]
    pipeline_log: List[Dict]
    risk_assessment: Optional[Dict] = None
    knowledge_guidance: Optional[Dict] = None
    fault_diagnosis: Optional[Dict] = None
    recommendation: Optional[Dict] = None
    execution_result: Optional[Dict] = None
    learned_case: Optional[Dict] = None
    hitl_required: Optional[Dict] = None    # Agent 1 DFA: FLAGGED → IMPUTE/DROP/KEEP
    hitl_advisory: Optional[Dict] = None    # Agent 4 PRA: LLM advisory → Accept/Reject
    hitl_monitoring: Optional[Dict] = None  # Agent 2 Monitoring: borderline EWMA → Suppress/Confirm
    hitl_diagnosis: Optional[Dict] = None   # Agent 3 FI: low confidence → Confirm/Override
    hitl_knowledge: Optional[Dict] = None   # Agent 5 Knowledge: no SOP → Flag/Accept
    raw_output: Optional[Dict] = None


class HITLRemediationRequest(BaseModel):
    run_id: str
    action: str        # IMPUTE | DROP | KEEP
    persona: Persona = "supervisor"
    rationale: str = ""


class HITLMonitoringRequest(BaseModel):
    run_id: str
    action: str        # SUPPRESS | CONFIRM
    persona: Persona = "supervisor"
    rationale: str = ""


class HITLDiagnosisRequest(BaseModel):
    run_id: str
    action: str        # CONFIRM | MARK_UNDETERMINED
    persona: Persona = "engineer"
    rationale: str = ""


class HITLKnowledgeRequest(BaseModel):
    run_id: str
    action: str        # FLAG_MANUAL | ACCEPT_EMPTY
    persona: Persona = "maintenance"
    manual_sop_note: Optional[str] = None
    rationale: str = ""


class HITLAdvisoryRequest(BaseModel):
    run_id: str
    action: str        # ACCEPT | REJECT | MODIFY
    persona: Persona = "engineer"
    revised_note: Optional[str] = None
    rationale: str = ""


class RecommendationRejectionRequest(BaseModel):
    case_id: str
    asset_id: str
    fault_mode: str
    reason_code: Literal[
        "diagnosis_wrong", "parts_concern", "second_opinion",
        "wrong_window", "other",
    ]
    free_text: str = ""
    persona: Persona = "supervisor"


@app.get("/api/pipeline/hitl/pending")
def hitl_pending(persona: Persona = "supervisor", gate: str = ""):
    """Return only HITL work the selected persona is authorized to resolve."""
    if gate and gate not in _HITL_PERMISSIONS:
        raise HTTPException(422, f"Unknown HITL gate: {gate}")
    allowed = {name for name, personas in _HITL_PERMISSIONS.items()
               if persona in set(personas)}
    rows = _HITL_REPOSITORY.list_pending(persona=persona, gate=gate)
    return {
        "persona": persona,
        "items": [_hitl_public_session(row) for row in rows
                  if row.get("gate") in allowed],
    }


@app.get("/api/pipeline/hitl/{run_id}")
def hitl_status(run_id: str, persona: Persona = "supervisor"):
    """Return durable session state and its audit decisions."""
    session = _HITL_REPOSITORY.get_session(run_id)
    if not session:
        raise HTTPException(404, f"HITL session '{run_id}' was not found.")
    _authorize_hitl(persona, session["gate"])
    return {
        "session": _hitl_public_session(session),
        "decisions": _HITL_REPOSITORY.decisions(run_id),
    }


class ChatRequest(BaseModel):
    message: str
    persona: Persona = "supervisor"
    asset_id: Optional[str] = None
    context: Optional[Dict] = None
    conversation_id: Optional[str] = None
    # Sent by frontend from its messages state; swap source for Cosmos DB later:
    #   history = await cosmos.read(conversation_id)
    conversation_history: Optional[List[Dict]] = None


class WOUpdateRequest(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    checklist_index: Optional[int] = None
    checklist_done: Optional[bool] = None
    updated_by: Optional[str] = None


class ExecutorRunRequest(BaseModel):
    recommendation: Dict[str, Any]
    approved: bool = False
    persona: Persona = "supervisor"


# ── Helper: load demo scenario signal ────────────────────────────────────────

def _get_demo_signal(scenario: str, row_index: int = -1) -> Dict[str, Any]:
    rows = load_telemetry_rows(scenario)
    if not rows:
        raise HTTPException(404, f"Scenario '{scenario}' not found or empty.")
    idx = row_index if 0 <= row_index < len(rows) else len(rows) - 1
    return deepcopy(rows[idx])


# ── Helper: warm EWMA for demo scenarios ─────────────────────────────────────

def _warm_and_run(scenario: str, row_index: int) -> Dict[str, Any]:
    """Warm up EWMA state by running rows 0..row_index-1, then run row_index."""
    from src.orchestrator.graph import run_pipeline as rp
    rows = load_telemetry_rows(scenario)
    if not rows:
        raise HTTPException(404, f"Scenario '{scenario}' not found.")
    idx = row_index if 0 <= row_index < len(rows) else len(rows) - 1

    # Warm up (run prior rows through the pipeline to build EWMA state)
    for i in range(idx):
        rp(deepcopy(rows[i]))

    return rp(deepcopy(rows[idx]))


# ── Query-intent classifier ───────────────────────────────────────────────────

_INTENT_RULES = [
    # (intent, keywords) — first match wins
    ("status",    ["vibration", "temperature", "reading", "sensor", "current value",
                   "what is the", "live data", "status", "how is", "signal"]),
    ("anomaly",   ["anomaly", "alert", "is there a fault", "any issue", "normal",
                   "triggered", "threshold", "any alert"]),
    ("diagnosis", ["fault", "root cause", "what type", "what kind", "identify",
                   "diagnose", "which fault", "bearing fault"]),
    ("risk",      ["rul", "remaining useful life", "how long", "days", "how urgent",
                   "risk level", "probability", "failure probability", "when will"]),
    ("full",      []),  # default
]

def _classify_intent(query: str) -> str:
    """Map a natural-language query to a pipeline depth intent."""
    return plan_query(query or "recommend full maintenance analysis", True).pipeline_intent


# ── Pipeline endpoint ─────────────────────────────────────────────────────────

@app.post("/api/pipeline/run", response_model=PipelineRunResponse)
def pipeline_run(req: PipelineRunRequest, user: Dict = Depends(get_current_user)):
    """Run the DRO pipeline on a signal; depth controlled by query intent."""
    require_persona(req.persona, user)
    if req.thread_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", req.thread_id):
        raise HTTPException(422, "thread_id contains unsupported characters or is too long.")
    intent = _classify_intent(req.query)
    try:
        if req.scenario:
            rows = load_telemetry_rows(req.scenario)
            if not rows:
                raise HTTPException(404, f"Scenario '{req.scenario}' not found.")
            idx = req.row_index if 0 <= req.row_index < len(rows) else len(rows) - 1
            signal = deepcopy(rows[idx])
            for i in range(idx):
                run_pipeline(deepcopy(rows[i]), persona=req.persona)
            state = run_pipeline(
                signal, intent=intent, persona=req.persona,
                thread_id=req.thread_id or "",
            )
        else:
            state = run_pipeline(
                req.signal, intent=intent, persona=req.persona,
                thread_id=req.thread_id or "",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Pipeline error: {exc}")

    formatted = format_for_persona(state, req.persona)

    def _to_dict(obj):
        if obj is None:
            return None
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        return str(obj)

    run_id = state.get("run_id", "")
    trusted = state.get("trusted_signal")

    # ── Gate 1: DFA HITL — signal FLAGGED, missing critical fields ────────────
    hitl_required = None
    if trusted is not None:
        from src.schemas.bearing_signal import ValidationStatus as _VS
        if getattr(trusted, "validation_status", None) == _VS.FLAGGED:
            from src.tools.remediation import (
                missing_critical_fields as _mcf,
                imputable_fields as _imputable_flds,
            )
            from src.tools.config_loader import load_config as _load_dfa
            _dfa_cfg = _load_dfa()
            _HITL_STORE[run_id] = {
                "type":    "remediation",
                "trusted": trusted,
                "persona": req.persona,
                "intent":  intent,
            }
            hitl_required = {
                "type":             "remediation",
                "run_id":           run_id,
                "missing_fields":   _mcf(trusted, _dfa_cfg),
                "imputable_fields": _imputable_flds(trusted, _dfa_cfg),
                "quality_score":    (trusted.quality_report.overall_score
                                     if getattr(trusted, "quality_report", None) else None),
            }

    # ── Gate 2: Monitoring HITL — borderline EWMA-only anomaly ───────────────
    # Fires when EWMA control chart triggered but Hotelling T² did not — the
    # signal is statistically anomalous on a single dimension but not jointly,
    # which often indicates a startup transient rather than a real defect.
    hitl_monitoring = None
    anomaly = state.get("anomaly_event")
    if hitl_required is None and anomaly is not None:
        _ev = anomaly.evidence or {}
        _ewma = _ev.get("ewma", {})
        if _ewma.get("fired", False) and not _ev.get("t2_fired", True):
            _HITL_STORE[run_id] = {
                "type":         "monitoring",
                "trusted":      trusted,
                "anomaly_event": anomaly,
                "persona":      req.persona,
                "intent":       intent,
            }
            hitl_monitoring = {
                "type":              "monitoring",
                "run_id":            run_id,
                "anomaly_score":     anomaly.anomaly_score,
                "triggered_features": anomaly.triggered_features,
                "reason":            anomaly.reason,
                "regime":            anomaly.regime,
                "ewma_triggered":    _ewma.get("triggered_features", []),
                "ewma_score":        _ewma.get("score", 0.0),
            }

    # ── Gate 3: FI HITL — fault confidence below threshold ───────────────────
    # Fires when the failure intelligence agent matched a fault but the combined
    # confidence (anomaly × match strength) is below 60%, meaning the evidence
    # is insufficient for automated risk dispatch.
    hitl_diagnosis = None
    diagnosis = state.get("fault_diagnosis")
    if hitl_required is None and hitl_monitoring is None and diagnosis is not None:
        if diagnosis.confidence < _DIAGNOSIS_CONFIDENCE_THRESHOLD:
            _HITL_STORE[run_id] = {
                "type":           "diagnosis",
                "trusted":        trusted,
                "anomaly_event":  anomaly,
                "fault_diagnosis": diagnosis,
                "persona":        req.persona,
                "intent":         intent,
            }
            hitl_diagnosis = {
                "type":       "diagnosis",
                "run_id":     run_id,
                "fault_mode": diagnosis.fault_mode,
                "fault_code": diagnosis.fault_code,
                "confidence": diagnosis.confidence,
                "iso_stage":  diagnosis.iso_stage,
                "narrative":  diagnosis.narrative,
            }

    # ── Gate 5: Knowledge HITL — no SOP found in catalog ────────────────────
    # Fires when the knowledge agent returned zero source documents, meaning
    # no SOP exists for this fault/asset combination. Safety-critical assets
    # must not proceed without a confirmed procedure.
    hitl_knowledge = None
    knowledge = state.get("knowledge_guidance")
    if (hitl_required is None and hitl_monitoring is None
            and hitl_diagnosis is None and knowledge is not None):
        if not knowledge.source_documents:
            _HITL_STORE[run_id] = {
                "type":             "knowledge",
                "trusted":          trusted,
                "anomaly_event":    anomaly,
                "fault_diagnosis":  diagnosis,
                "risk_assessment":  state.get("risk_assessment"),
                "knowledge_guidance": knowledge,
                "persona":          req.persona,
                "intent":           intent,
            }
            hitl_knowledge = {
                "type":      "knowledge",
                "run_id":    run_id,
                "fault_mode": diagnosis.fault_mode if diagnosis else "unknown",
                "asset_id":  (trusted.raw.asset_id
                              if trusted and hasattr(trusted, "raw") else ""),
            }

    # ── Gate 4: PRA advisory HITL — LLM generated advisory note ─────────────
    # Not a true pause gate — shown alongside the pipeline result as an
    # accept/reject prompt. The deterministic RUL numbers are always valid;
    # only the LLM-generated text is subject to operator review.
    _risk = state.get("risk_assessment")
    hitl_advisory = _create_advisory_gate(run_id,_risk,req.persona,
        blocked=any((hitl_required,hitl_monitoring,hitl_diagnosis,hitl_knowledge)))

    # True-pause gates hide downstream results until the operator resolves them
    _mon_paused  = hitl_monitoring is not None
    _diag_paused = hitl_diagnosis is not None

    # Build persona-appropriate headline/details for gate pause cards
    _headline = formatted.get("headline", "")
    _details  = formatted.get("details", [])
    _actions  = formatted.get("actions", [])
    _tags     = formatted.get("tags", [])

    if _mon_paused:
        _headline = (f"Borderline EWMA anomaly on {anomaly.bearing_id} "
                     "— operator confirmation required before diagnosis proceeds")
        _details = [
            f"Anomaly score: {anomaly.anomaly_score:.2f} — EWMA control chart fired; "
            "Hotelling T² multivariate detector did not.",
            f"EWMA triggered on: {', '.join(hitl_monitoring['ewma_triggered']) or 'unknown signals'}.",
            f"Operating regime: {hitl_monitoring['regime']}.",
            "This pattern may be a startup transient or sensor noise rather than a real defect.",
            "Suppress to dismiss, or Confirm to proceed with fault diagnosis.",
        ]
        _actions = [
            "Review EWMA signal history for this bearing before deciding.",
            "Suppress if the reading coincides with a known startup / shutdown transient.",
            "Confirm to run fault diagnosis and risk assessment.",
        ]
        _tags = ["EWMA Only", "Monitoring Gate", "Operator Review Required"]

    elif _diag_paused:
        _headline = (f"Low-confidence fault classification on {diagnosis.bearing_id} "
                     "— engineer confirmation required")
        _details = [
            f"Detected: {diagnosis.fault_mode.replace('_', ' ')} "
            f"(ISO Stage {diagnosis.iso_stage}) — confidence {diagnosis.confidence:.0%}.",
            "Confidence is below the 60 % automated-dispatch threshold.",
            diagnosis.narrative or "Insufficient signal evidence for a conclusive classification.",
            "Confirm to proceed, or override to undetermined for manual inspection.",
        ]
        _actions = [
            "Review the fault evidence before deciding.",
            "Confirm if you agree with the classification.",
            "Override to undetermined if the evidence is insufficient for a specific fault type.",
        ]
        _tags = ["Low Confidence", "FI Gate", "Engineer Review Required"]

    _stamp_hitl_sessions()
    return PipelineRunResponse(
        run_id=run_id,
        persona=req.persona,
        persona_context=build_persona_context(req.persona).model_dump(),
        headline=_headline,
        details=_details,
        actions=_actions,
        tags=_tags,
        pipeline_log=state.get("pipeline_log", []),
        # Suppress downstream results during true-pause gates so operators
        # act on partial context rather than potentially wrong downstream analysis
        fault_diagnosis=(None if _mon_paused
                         else _to_dict(state.get("fault_diagnosis"))),
        risk_assessment=(None if (_mon_paused or _diag_paused)
                         else _to_dict(state.get("risk_assessment"))),
        knowledge_guidance=(None if (_mon_paused or _diag_paused)
                            else _to_dict(state.get("knowledge_guidance"))),
        recommendation=(None if (_mon_paused or _diag_paused)
                        else _recommendation_payload(
                            state.get("recommendation"), req.persona
                        )),
        execution_result=_to_dict(state.get("execution_result")),
        learned_case=_to_dict(state.get("learned_case")),
        hitl_required=_with_hitl_access(
            hitl_required, "remediation", req.persona
        ),
        hitl_advisory=hitl_advisory,
        hitl_monitoring=_with_hitl_access(
            hitl_monitoring, "monitoring", req.persona
        ),
        hitl_diagnosis=_with_hitl_access(
            hitl_diagnosis, "diagnosis", req.persona
        ),
        hitl_knowledge=_with_hitl_access(
            hitl_knowledge, "knowledge", req.persona
        ),
    )


@app.post("/api/pipeline/hitl/remediation")
def hitl_remediation(req: HITLRemediationRequest, user: Dict = Depends(get_current_user)):
    """Resolve a DFA HITL gate: apply IMPUTE/DROP/KEEP and resume the pipeline."""
    require_persona(req.persona, user)
    action = req.action.upper()
    stored = _claim_hitl(req.run_id,"remediation",action,{"IMPUTE","DROP","KEEP"},
                         req.persona,req.rationale)
    persona = req.persona
    intent  = stored.get("intent", "full")
    trusted = stored["trusted"]

    def _to_dict(obj):
        if obj is None:
            return None
        return obj.model_dump() if hasattr(obj, "model_dump") else str(obj)

    if action == "DROP":
        return PipelineRunResponse(
            run_id=req.run_id, persona=persona,
            persona_context=build_persona_context(persona).model_dump(),
            headline="Signal discarded — operator dropped this reading.",
            details=[
                "Missing critical fields could not be recovered.",
                "No downstream analysis was performed.",
                "Sensor health review is recommended.",
            ],
            actions=[
                "Review sensor calibration and wiring for this asset.",
                "Check historian for signal drop frequency.",
                "Raise maintenance ticket if drops are recurring.",
            ],
            tags=["DROPPED", "Data Quality Gate"],
            pipeline_log=[{"node": "data_foundation", "status": "dropped", "latency_ms": 0}],
        )

    if action == "IMPUTE":
        from src.tools.remediation import remediate
        from src.tools.config_loader import load_config as _load_dfa
        from src.orchestrator.graph import _get_agents
        dfa, *_ = _get_agents()
        dfa_cfg = _load_dfa()
        trusted = remediate(trusted, "IMPUTE", dfa, dfa_cfg)
        if trusted is None:
            raise HTTPException(400, "No imputable fields available — cannot impute.")

    # KEEP or successfully IMPUTEd: resume pipeline from monitoring
    from src.orchestrator.graph import run_from_trusted_signal
    state = run_from_trusted_signal(
        trusted, intent=intent, run_id=req.run_id, persona=persona
    )
    formatted = format_for_persona(state, persona)

    risk      = state.get("risk_assessment")
    knowledge = state.get("knowledge_guidance")
    diagnosis = state.get("fault_diagnosis")

    # Propagate downstream HITL gates that may fire after DFA remediation
    hitl_advisory  = None
    hitl_diagnosis = None
    hitl_knowledge = None

    if diagnosis is not None and diagnosis.confidence < _DIAGNOSIS_CONFIDENCE_THRESHOLD:
        _HITL_STORE[req.run_id] = {
            "type":           "diagnosis",
            "trusted":        trusted,
            "anomaly_event":  state.get("anomaly_event"),
            "fault_diagnosis": diagnosis,
            "persona":        persona,
            "intent":         intent,
        }
        hitl_diagnosis = {
            "type":       "diagnosis",
            "run_id":     req.run_id,
            "fault_mode": diagnosis.fault_mode,
            "fault_code": diagnosis.fault_code,
            "confidence": diagnosis.confidence,
            "iso_stage":  diagnosis.iso_stage,
            "narrative":  diagnosis.narrative,
        }

    if hitl_diagnosis is None and knowledge is not None and not knowledge.source_documents:
        _HITL_STORE[req.run_id] = {
            "type":             "knowledge",
            "trusted":          trusted,
            "anomaly_event":    state.get("anomaly_event"),
            "fault_diagnosis":  diagnosis,
            "risk_assessment":  risk,
            "knowledge_guidance": knowledge,
            "persona":          persona,
            "intent":           intent,
        }
        hitl_knowledge = {
            "type":      "knowledge",
            "run_id":    req.run_id,
            "fault_mode": diagnosis.fault_mode if diagnosis else "unknown",
            "asset_id":  (trusted.raw.asset_id
                          if trusted and hasattr(trusted, "raw") else ""),
        }

    hitl_advisory=_create_advisory_gate(req.run_id,risk,persona,
        blocked=bool(hitl_diagnosis or hitl_knowledge))

    _diag_paused = hitl_diagnosis is not None
    _stamp_hitl_sessions()
    return PipelineRunResponse(
        run_id=req.run_id, persona=persona,
        persona_context=build_persona_context(persona).model_dump(),
        headline=formatted.get("headline", ""),
        details=formatted.get("details", []),
        actions=formatted.get("actions", []),
        tags=formatted.get("tags", []),
        pipeline_log=state.get("pipeline_log", []),
        fault_diagnosis=_to_dict(diagnosis),
        risk_assessment=None if _diag_paused else _to_dict(risk),
        knowledge_guidance=None if _diag_paused else _to_dict(knowledge),
        hitl_advisory=hitl_advisory if not _diag_paused else None,
        hitl_diagnosis=_with_hitl_access(
            hitl_diagnosis, "diagnosis", persona
        ),
        hitl_knowledge=_with_hitl_access(
            hitl_knowledge, "knowledge", persona
        ),
    )


# ── Monitoring HITL: resolve borderline EWMA anomaly ─────────────────────────

@app.post("/api/pipeline/hitl/monitoring")
def hitl_monitoring_resolution(req: HITLMonitoringRequest, user: Dict = Depends(get_current_user)):
    """Resolve Monitoring HITL gate: SUPPRESS or CONFIRM a borderline EWMA anomaly."""
    require_persona(req.persona, user)
    action = req.action.upper()
    stored = _claim_hitl(req.run_id,"monitoring",action,{"SUPPRESS","CONFIRM"},
                         req.persona,req.rationale)
    persona = req.persona
    intent  = stored.get("intent", "full")
    trusted = stored["trusted"]
    anomaly = stored["anomaly_event"]

    def _to_dict(obj):
        if obj is None:
            return None
        return obj.model_dump() if hasattr(obj, "model_dump") else str(obj)

    if action == "SUPPRESS":
        return PipelineRunResponse(
            run_id=req.run_id, persona=persona,
            persona_context=build_persona_context(persona).model_dump(),
            headline="EWMA anomaly suppressed — operator identified as expected transient.",
            details=[
                "The borderline EWMA alert has been suppressed by operator decision.",
                "No fault diagnosis will be performed for this reading.",
                "Continue monitoring. Escalate if the pattern persists across readings.",
            ],
            actions=[
                "Document suppression decision with justification in the maintenance log.",
                "Monitor next 3 sensor readings for trend continuation.",
                "If EWMA fires again within 30 minutes, escalate to engineering review.",
            ],
            tags=["SUPPRESSED", "Monitoring Gate", "Operator Decision"],
            pipeline_log=[
                {"node": "data_foundation", "status": "ok", "latency_ms": 0},
                {"node": "monitoring", "status": "suppressed by operator", "latency_ms": 0},
            ],
        )

    # CONFIRM — resume pipeline from FI → Risk → Knowledge
    from src.orchestrator.graph import run_from_anomaly_event
    state = run_from_anomaly_event(
        anomaly, trusted, intent=intent, run_id=req.run_id, persona=persona
    )
    formatted = format_for_persona(state, persona)

    diagnosis = state.get("fault_diagnosis")
    risk      = state.get("risk_assessment")
    knowledge = state.get("knowledge_guidance")

    hitl_advisory  = None
    hitl_diagnosis = None
    hitl_knowledge = None

    if diagnosis is not None and diagnosis.confidence < _DIAGNOSIS_CONFIDENCE_THRESHOLD:
        _HITL_STORE[req.run_id] = {
            "type":           "diagnosis",
            "trusted":        trusted,
            "anomaly_event":  anomaly,
            "fault_diagnosis": diagnosis,
            "persona":        persona,
            "intent":         intent,
        }
        hitl_diagnosis = {
            "type":       "diagnosis",
            "run_id":     req.run_id,
            "fault_mode": diagnosis.fault_mode,
            "fault_code": diagnosis.fault_code,
            "confidence": diagnosis.confidence,
            "iso_stage":  diagnosis.iso_stage,
            "narrative":  diagnosis.narrative,
        }

    if hitl_diagnosis is None and knowledge is not None and not knowledge.source_documents:
        _HITL_STORE[req.run_id] = {
            "type":             "knowledge",
            "trusted":          trusted,
            "anomaly_event":    anomaly,
            "fault_diagnosis":  diagnosis,
            "risk_assessment":  risk,
            "knowledge_guidance": knowledge,
            "persona":          persona,
            "intent":           intent,
        }
        hitl_knowledge = {
            "type":      "knowledge",
            "run_id":    req.run_id,
            "fault_mode": diagnosis.fault_mode if diagnosis else "unknown",
            "asset_id":  (trusted.raw.asset_id
                          if trusted and hasattr(trusted, "raw") else ""),
        }


    hitl_advisory=_create_advisory_gate(req.run_id,risk,persona,
        blocked=bool(hitl_diagnosis or hitl_knowledge))

    _diag_paused = hitl_diagnosis is not None
    _stamp_hitl_sessions()
    _headline    = formatted.get("headline", "")
    _details     = formatted.get("details", [])
    _actions     = formatted.get("actions", [])
    _tags        = formatted.get("tags", [])

    if _diag_paused:
        _headline = (f"Low-confidence fault classification on {diagnosis.bearing_id} "
                     "— engineer confirmation required")
        _details = [
            f"Detected: {diagnosis.fault_mode.replace('_', ' ')} "
            f"(ISO Stage {diagnosis.iso_stage}) — confidence {diagnosis.confidence:.0%}.",
            "Confidence is below the 60 % automated-dispatch threshold.",
            diagnosis.narrative or "",
        ]
        _actions = [
            "Confirm if you agree with the classification.",
            "Override to undetermined if evidence is insufficient.",
        ]
        _tags = ["Low Confidence", "FI Gate", "Engineer Review Required"]

    return PipelineRunResponse(
        run_id=req.run_id, persona=persona,
        persona_context=build_persona_context(persona).model_dump(),
        headline=_headline, details=_details, actions=_actions, tags=_tags,
        pipeline_log=state.get("pipeline_log", []),
        fault_diagnosis=None if _diag_paused else _to_dict(diagnosis),
        risk_assessment=None if _diag_paused else _to_dict(risk),
        knowledge_guidance=None if _diag_paused else _to_dict(knowledge),
        hitl_advisory=hitl_advisory if not _diag_paused else None,
        hitl_diagnosis=_with_hitl_access(
            hitl_diagnosis, "diagnosis", persona
        ),
        hitl_knowledge=_with_hitl_access(
            hitl_knowledge, "knowledge", persona
        ),
    )


# ── FI HITL: confirm or override low-confidence fault classification ──────────

@app.post("/api/pipeline/hitl/diagnosis")
def hitl_diagnosis_resolution(req: HITLDiagnosisRequest, user: Dict = Depends(get_current_user)):
    """Resolve FI HITL gate: CONFIRM or MARK_UNDETERMINED a low-confidence fault."""
    require_persona(req.persona, user)
    action = req.action.upper()
    stored = _claim_hitl(req.run_id,"diagnosis",action,
                         {"CONFIRM","MARK_UNDETERMINED"},req.persona,req.rationale)
    persona           = req.persona
    intent            = stored.get("intent", "full")
    trusted           = stored["trusted"]
    anomaly           = stored["anomaly_event"]
    original_diagnosis = stored["fault_diagnosis"]

    def _to_dict(obj):
        if obj is None:
            return None
        return obj.model_dump() if hasattr(obj, "model_dump") else str(obj)

    if action == "MARK_UNDETERMINED":
        from src.schemas.diagnosis import FaultDiagnosis as _FD
        from datetime import datetime as _dt, timezone as _tz
        diagnosis = _FD(
            case_id              = original_diagnosis.case_id,
            asset_id             = original_diagnosis.asset_id,
            bearing_id           = original_diagnosis.bearing_id,
            fault_mode           = "undetermined",
            fault_code           = "",
            iso_stage            = 0,
            severity             = "medium",
            confidence           = 0.0,
            bpfo_multiple        = original_diagnosis.bpfo_multiple,
            bpfi_multiple        = original_diagnosis.bpfi_multiple,
            kurtosis_at_detection = original_diagnosis.kurtosis_at_detection,
            evidence             = original_diagnosis.evidence,
            recommended_checks   = [
                "Manual inspection required — fault classification overridden to "
                "undetermined by engineer.",
            ],
            narrative=(
                f"Fault classification overridden to undetermined by operator. "
                f"Original: {original_diagnosis.fault_mode} at "
                f"{original_diagnosis.confidence:.0%} confidence."
            ),
            processed_at = _dt.now(_tz.utc).isoformat(),
        )
    else:  # CONFIRM — use original diagnosis as-is
        diagnosis = original_diagnosis

    from src.orchestrator.graph import run_from_diagnosis
    state = run_from_diagnosis(
        diagnosis, anomaly, trusted, intent=intent,
        run_id=req.run_id, persona=persona,
    )
    formatted = format_for_persona(state, persona)

    risk      = state.get("risk_assessment")
    knowledge = state.get("knowledge_guidance")

    hitl_advisory  = None
    hitl_knowledge = None

    if knowledge is not None and not knowledge.source_documents:
        _HITL_STORE[req.run_id] = {
            "type":             "knowledge",
            "trusted":          trusted,
            "anomaly_event":    anomaly,
            "fault_diagnosis":  diagnosis,
            "risk_assessment":  risk,
            "knowledge_guidance": knowledge,
            "persona":          persona,
            "intent":           intent,
        }
        hitl_knowledge = {
            "type":      "knowledge",
            "run_id":    req.run_id,
            "fault_mode": diagnosis.fault_mode,
            "asset_id":  (trusted.raw.asset_id
                          if trusted and hasattr(trusted, "raw") else ""),
        }


    hitl_advisory=_create_advisory_gate(req.run_id,risk,persona,blocked=bool(hitl_knowledge))

    _stamp_hitl_sessions()
    return PipelineRunResponse(
        run_id=req.run_id, persona=persona,
        persona_context=build_persona_context(persona).model_dump(),
        headline=formatted.get("headline", ""),
        details=formatted.get("details", []),
        actions=formatted.get("actions", []),
        tags=formatted.get("tags", []),
        pipeline_log=state.get("pipeline_log", []),
        fault_diagnosis=_to_dict(diagnosis),
        risk_assessment=_to_dict(risk),
        knowledge_guidance=_to_dict(knowledge),
        hitl_advisory=hitl_advisory,
        hitl_knowledge=_with_hitl_access(
            hitl_knowledge, "knowledge", persona
        ),
    )


# ── Knowledge HITL: resolve missing SOP ──────────────────────────────────────

@app.post("/api/pipeline/hitl/knowledge")
def hitl_knowledge_resolution(req: HITLKnowledgeRequest, user: Dict = Depends(get_current_user)):
    """Resolve Knowledge HITL gate: FLAG_MANUAL or ACCEPT_EMPTY when no SOP found."""
    require_persona(req.persona, user)
    action = req.action.upper()
    stored = _claim_hitl(req.run_id,"knowledge",action,
                         {"FLAG_MANUAL","ACCEPT_EMPTY"},req.persona,req.rationale)
    persona   = req.persona
    diagnosis = stored.get("fault_diagnosis")
    risk      = stored.get("risk_assessment")
    trusted   = stored.get("trusted")

    def _to_dict(obj):
        if obj is None:
            return None
        return obj.model_dump() if hasattr(obj, "model_dump") else str(obj)

    asset_id   = (trusted.raw.asset_id
                  if trusted and hasattr(trusted, "raw") else "unknown")
    fault_mode = diagnosis.fault_mode if diagnosis else "unknown"

    if action == "FLAG_MANUAL":
        note = req.manual_sop_note or "Manual SOP review ticket raised."
        return PipelineRunResponse(
            run_id=req.run_id, persona=persona,
            persona_context=build_persona_context(persona).model_dump(),
            headline=f"No SOP found — {asset_id} flagged for manual procedure lookup.",
            details=[
                f"No standard operating procedure exists in the catalog for: "
                f"{fault_mode.replace('_', ' ')}.",
                "This asset / fault combination has been flagged for manual SOP review.",
                note,
                "Do NOT perform maintenance until the correct procedure is confirmed.",
            ],
            actions=[
                "Contact Safety Officer to identify the applicable LOTO procedure.",
                "Consult Maintenance lead for the correct inspection steps.",
                "Add the confirmed SOP to the catalog to prevent future gaps.",
                "Log this SOP gap in the maintenance management system.",
            ],
            tags=["No SOP", "Manual Lookup Required", "Safety Gate"],
            pipeline_log=[
                {"node": "knowledge", "status": "no_sop — flagged for manual review",
                 "latency_ms": 0},
            ],
            risk_assessment=_to_dict(risk),
            fault_diagnosis=_to_dict(diagnosis),
        )

    # ACCEPT_EMPTY — proceed with the existing risk/diagnosis, no SOP
    return PipelineRunResponse(
        run_id=req.run_id, persona=persona,
        persona_context=build_persona_context(persona).model_dump(),
        headline=f"Analysis complete — no SOP retrieved for {asset_id}.",
        details=[
            f"Fault: {fault_mode.replace('_', ' ')}. Risk assessment is available.",
            "No SOP was found in the catalog; apply the general procedure for this asset type.",
            "Ensure all site-specific LOTO and safety requirements are followed.",
        ],
        actions=[
            "Apply the general bearing replacement / inspection procedure for this asset type.",
            "Follow site-specific LOTO requirements (verify locally).",
            "Log the maintenance event in CMMS with a note: no specific SOP found.",
        ],
        tags=["No SOP", "Accepted", "Proceed with Caution"],
        pipeline_log=[
            {"node": "knowledge", "status": "no_sop — accepted by operator", "latency_ms": 0},
        ],
        risk_assessment=_to_dict(risk),
        fault_diagnosis=_to_dict(diagnosis),
    )


@app.post("/api/pipeline/hitl/advisory")
def hitl_advisory_resolution(req: HITLAdvisoryRequest, user: Dict = Depends(get_current_user)):
    """Review an LLM advisory without changing deterministic risk facts."""
    require_persona(req.persona, user)
    action=req.action.upper()
    revised=str(req.revised_note or "").strip()
    if action=="MODIFY" and not revised:
        raise HTTPException(422,"revised_note is required when action is MODIFY.")
    if len(revised)>1000:
        raise HTTPException(422,"revised_note exceeds 1000 characters.")
    stored=_claim_hitl(req.run_id,"advisory",action,{"ACCEPT","REJECT","MODIFY"},
                       req.persona,req.rationale)
    risk=stored.get("risk_assessment")
    original=str(getattr(risk,"advisory_note","") or "")
    if action=="MODIFY":
        advisory_note=revised
    elif action=="ACCEPT":
        advisory_note=original
    else:
        advisory_note=""
    return {"run_id":req.run_id,"type":"advisory","status":action.lower(),
        "advisory_note":advisory_note,"original_advisory_note":original,
        "deterministic_assessment_unchanged":True,"persona":req.persona}


@app.post("/api/executor/run")
def executor_run(req: ExecutorRunRequest, user: Dict = Depends(get_current_user)):
    """
    Execute an approved (or rejected) MaintenanceRecommendation.

    Pass approved=True to execute, approved=False to test the blocked path.
    The recommendation dict must match the MaintenanceRecommendation schema
    (RecommendedAction + RequiredPart sub-models).
    """
    require_persona(req.persona, user)
    from src.schemas.recommendation import MaintenanceRecommendation
    from src.agents.executor_agent import ExecutorAgent
    from src.tools.config_loader import load_executor_config
    from src.tools.execution_repository import build_execution_repository
    from src.tools.postgres_runtime import use_postgres_tables
    from src.orchestrator.graph import (
        graph_checkpointing_enabled, resume_pipeline,
    )
    if req.approved:
        _authorize_hitl(req.persona,"execution")
    try:
        rec = MaintenanceRecommendation(**req.recommendation)
        if (
            req.approved and rec.checkpoint_thread_id
            and rec.checkpoint_namespace and graph_checkpointing_enabled()
        ):
            try:
                resumed = resume_pipeline(
                    rec.checkpoint_thread_id,
                    rec.checkpoint_namespace,
                    approval_status="approved",
                )
            except LookupError as exc:
                raise HTTPException(
                    409, "The persisted approval workflow could not be found."
                ) from exc
            result = resumed.get("execution_result")
            if result is None:
                raise HTTPException(
                    409, "The persisted approval workflow did not execute."
                )
        else:
            if use_postgres_tables():
                exec_cfg = load_executor_config()
                result = ExecutorAgent(
                    exec_cfg,
                    repository=build_execution_repository(exec_cfg.repository_path),
                ).process_and_store(rec, approved=req.approved)
            else:
                result = ExecutorAgent().process(rec, approved=req.approved)
        if req.approved and result.status in {"success", "partial"}:
            _learning_agent().record_approval(
                rec.case_id, rec.asset_id, rec.condition.fault_type
            )
            # Bridge HITL-approved WOs into the work-order store so the frontend
            # picks them up via the /api/workorders poll.
            wo_id = result.work_order_id
            if wo_id and _get_workorder(wo_id) is None:
                _ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                _new_wo: Dict[str, Any] = {
                    "id": wo_id,
                    "priority": (rec.urgency or "medium").upper(),
                    "status": "Pending",
                    "title": (
                        (rec.recommended_action.description if rec.recommended_action else None)
                        or rec.condition.fault_type
                        or "Maintenance Work Order"
                    )[:120],
                    "asset_id": rec.asset_id,
                    "asset_display": rec.asset_id,
                    "type": "Corrective",
                    "est_hours": None,
                    "parts": ", ".join(
                        p.part_number for p in (rec.required_parts or [])
                    ) or "",
                    "assigned_to": "Pending",
                    "due": "TBD",
                    "created_by": "DRO Agent",
                    "rul": None,
                    "risk": (rec.urgency or "medium").upper(),
                    "cost": None,
                    "checklist": [],
                    "timeline": [
                        {
                            "dot": "var(--pu)",
                            "ev": (
                                f"WO auto-created by Executor Agent — "
                                f"{rec.condition.fault_type or rec.asset_id}"
                            ),
                            "tm": _ts,
                        }
                    ],
                }
                _save_workorder(_new_wo)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Executor error: {exc}")


@app.post("/api/recommendations/reject")
def reject_recommendation(
    req: RecommendationRejectionRequest,
    user: Dict = Depends(get_current_user),
):
    """Persist structured rejection feedback through Agent 8 memory."""
    require_persona(req.persona, user)
    from src.schemas.feedback import RejectionFeedback
    canonical_asset = _asset_from_message(req.asset_id) or req.asset_id
    feedback = RejectionFeedback(
        case_id=req.case_id, asset_id=canonical_asset,
        fault_mode=req.fault_mode, reason_code=req.reason_code,
        free_text=req.free_text, persona_id=build_persona_context(req.persona).id,
        rejected_at=datetime.now(timezone.utc).isoformat(),
    )
    return _learning_agent().record_rejection(feedback).model_dump()


@app.get("/api/notifications/counts")
def notification_counts():
    """Return unread notification counts for every supported frontend persona."""
    from src.tools.notification_mock_service import get_unread_counts
    return get_unread_counts()


@app.get("/api/notifications/{persona_id}")
def persona_notifications(persona_id: str):
    from src.tools.notification_mock_service import PERSONAS, get_notifications
    if persona_id not in PERSONAS:
        raise HTTPException(404, f"Unknown persona: {persona_id}")
    notifications = get_notifications(persona_id)
    return {"persona_id": persona_id, "notifications": notifications, "count": len(notifications)}


@app.post("/api/notifications/{persona_id}/read")
def read_persona_notifications(persona_id: str):
    from src.tools.notification_mock_service import PERSONAS, mark_all_read
    if persona_id not in PERSONAS:
        raise HTTPException(404, f"Unknown persona: {persona_id}")
    mark_all_read(persona_id)
    return {"status": "ok", "persona_id": persona_id}


@app.get("/api/pipeline/scenarios")
def list_scenarios():
    """Return available demo scenario names."""
    return {"scenarios": list_telemetry_scenarios()}


# ── Asset endpoints ───────────────────────────────────────────────────────────

@app.get("/api/assets")
def get_assets():
    """Return all assets from asset_master.json."""
    try:
        assets = load_asset_master()
        rows = assets.values() if isinstance(assets, dict) else assets
        return {"assets": [a.model_dump() if hasattr(a, "model_dump") else a for a in rows]}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str):
    """Return a single asset by ID."""
    try:
        assets = load_asset_master()
        rows = assets.values() if isinstance(assets, dict) else assets
        for a in rows:
            aid = a.asset_id if hasattr(a, "asset_id") else a.get("asset_id", "")
            if aid == asset_id:
                return a.model_dump() if hasattr(a, "model_dump") else a
        raise HTTPException(404, f"Asset '{asset_id}' not found.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/assets/{asset_id}/export/pre-repair")
def export_pre_repair_data(asset_id: str, user: Dict = Depends(get_current_user)):
    """Export pre-repair sensor readings and bearing baselines for an asset as CSV."""
    fleet_map = load_fleet_scenario_map()          # display_id (upper) -> (canonical_id, scenario)
    upper = asset_id.strip().upper()
    entry = fleet_map.get(upper)
    if not entry:
        # also accept canonical asset_id (e.g. AST_MTR_001)
        for disp, (cid, sc) in fleet_map.items():
            if cid.upper() == upper:
                entry = (cid, sc)
                upper = disp
                break
    if not entry:
        raise HTTPException(404, f"Asset '{asset_id}' not found in fleet map.")
    canonical_id, scenario = entry

    rows        = load_telemetry_rows(scenario)
    by_bearing, _ = load_bearing_master()
    asset_master  = load_asset_master()
    asset = asset_master.get(canonical_id) or asset_master.get(canonical_id.upper())

    fieldnames = [
        "timestamp_utc", "display_asset_id", "asset_id", "bearing_id", "channel_id",
        "rpm", "load_pct", "machine_state",
        "vib_rms_mm_s", "vib_peak_g", "kurtosis", "temp_c",
        "bpfo_energy", "bpfi_energy", "bsf_energy", "ftf_energy",
        "motor_current_a", "voltage_v", "signal_quality_score",
        "baseline_vib_rms_mean", "baseline_vib_rms_std",
        "baseline_kurtosis_mean", "baseline_temp_mean", "baseline_bpfo_mean",
        "iso_10816_zone", "asset_criticality",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        bm = by_bearing.get(row.get("bearing_id", ""))
        writer.writerow({
            "timestamp_utc":         row.get("timestamp_utc", ""),
            "display_asset_id":      upper,
            "asset_id":              canonical_id,
            "bearing_id":            row.get("bearing_id", ""),
            "channel_id":            row.get("channel_id", ""),
            "rpm":                   row.get("rpm", ""),
            "load_pct":              row.get("load_pct", ""),
            "machine_state":         row.get("machine_state", ""),
            "vib_rms_mm_s":          row.get("vib_rms_mm_s", ""),
            "vib_peak_g":            row.get("vib_peak_g", ""),
            "kurtosis":              row.get("kurtosis", ""),
            "temp_c":                row.get("temp_c", ""),
            "bpfo_energy":           row.get("bpfo_energy", ""),
            "bpfi_energy":           row.get("bpfi_energy", ""),
            "bsf_energy":            row.get("bsf_energy", ""),
            "ftf_energy":            row.get("ftf_energy", ""),
            "motor_current_a":       row.get("motor_current_a", ""),
            "voltage_v":             row.get("voltage_v", ""),
            "signal_quality_score":  row.get("signal_quality_score", ""),
            "baseline_vib_rms_mean": bm.baseline_vib_rms_mean if bm else "",
            "baseline_vib_rms_std":  bm.baseline_vib_rms_std  if bm else "",
            "baseline_kurtosis_mean":bm.baseline_kurtosis_mean if bm else "",
            "baseline_temp_mean":    bm.baseline_temp_mean     if bm else "",
            "baseline_bpfo_mean":    bm.baseline_bpfo_mean     if bm else "",
            "iso_10816_zone":        getattr(asset, "iso_10816_zone", ""),
            "asset_criticality":     getattr(asset, "criticality", ""),
        })

    filename = f"{upper}_pre-repair_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/dashboard/assets")
def dashboard_assets(user: Dict = Depends(get_current_user)):
    """Return frontend-ready fleet health derived from backend agent evidence."""
    from src.tools.dashboard_service import build_asset_dashboard
    payload = build_asset_dashboard(
        load_asset_master(), _MULTI_ASSET_SCENARIOS, load_telemetry_rows,
        lambda signal, intent: run_pipeline(
            signal, intent=intent, persona="supervisor"
        ),
    )
    # Work-order lifecycle is the durable source of post-intervention status.
    # Overlay it on the analytical projection so the registry and chat agree
    # after approval or closure, without browser session overrides.
    for row in payload.get("assets", []):
        lifecycle = _asset_lifecycle(row.get("asset_id") or row.get("id"))
        if lifecycle:
            row["status"] = lifecycle["status"]
            row["lifecycle_source"] = "work_order_repository"
            row["work_order_id"] = lifecycle["work_order_id"]
            row["work_order_status"] = lifecycle["work_order_status"]
    return payload


# ── Chat endpoint ─────────────────────────────────────────────────────────────

@app.get("/api/chat/history/{conversation_id}")
def get_chat_history(conversation_id: str, user: Dict = Depends(get_current_user)):
    """Return the durable transcript for the authenticated user's conversation."""
    if len(conversation_id) > 80 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", conversation_id):
        raise HTTPException(422, "conversation_id contains unsupported characters or is too long.")
    rows = load_chat_history(conversation_id, str(user.get("sub", "")), _CHAT_CONTEXT_STORE)
    return {"conversation_id": conversation_id, "messages": rows}


@app.post("/api/chat")
@traceable(name="DRO Chat API", run_type="chain", tags=["dro", "chat-api"])
def chat(req: ChatRequest, user: Dict = Depends(get_current_user)):
    """Route persona-aware natural language through the backend orchestrator."""
    require_persona(req.persona, user)
    selected_persona = build_persona_context(req.persona)
    run_id=f"CHAT-{uuid.uuid4().hex[:10].upper()}"
    conversation_id=req.conversation_id or f"CONV-{uuid.uuid4().hex[:10].upper()}"
    if len(conversation_id)>80 or not re.fullmatch(r"[A-Za-z0-9_.:-]+",conversation_id):
        raise HTTPException(422,"conversation_id contains unsupported characters or is too long.")
    if req.message.strip().lower() in {"reset","start over","new conversation","cancel this request"}:
        clear_durable_chat_context(conversation_id, _CHAT_CONTEXT_STORE)
        clear_chat_history(conversation_id, str(user.get("sub", "")), _CHAT_CONTEXT_STORE)
        _reset={"run_id":run_id,"conversation_id":conversation_id,"intent":"reset",
            "persona":selected_persona.id,"persona_context":selected_persona.model_dump(),
            "response":"Conversation context has been cleared.",
            "details":[],"actions":[],"call_plan":[],"pipeline_log":[],"sources":[],
            "needs_context":False,"clarification_required":False,"reflection_status":"accepted"}
        _reset_result = _attach_chat_learning(
            _reset, req, run_id, conversation_id, "reset", selected_persona
        )
        _persist_chat_turn(conversation_id, req, user, _reset_result)
        return _reset_result
    context=_load_chat_context(conversation_id)
    owner_id = str(user.get("sub", ""))
    if context.get("_owner_id") not in {None, owner_id}:
        # Conversation identifiers are client-held; never reuse another user's
        # transient context if an identifier is copied or guessed.
        context = {}
    context["_owner_id"] = owner_id
    incoming=_public_context(req.context)
    explicit_asset=req.asset_id or _asset_from_message(req.message)
    existing_signal=context.get("signal")
    existing_scenario=context.get("scenario")
    _ctx_scenario_asset=next((aid for _,(aid,sc) in _MULTI_ASSET_SCENARIOS.items() if sc==existing_scenario),"")
    if explicit_asset and (
        (isinstance(existing_signal,dict) and existing_signal.get("asset_id")!=explicit_asset) or
        (_ctx_scenario_asset and _ctx_scenario_asset!=explicit_asset)
    ):
        # Asset-bound evidence and prior synthesis cannot cross an explicit
        # asset switch. Keeping either can repeat or contaminate the answer.
        for field in ("signal", "scenario", "row_index", "last_pipeline_result",
                      "per_asset_signals"):
            context.pop(field,None)
    context.update(incoming)
    supplied_asset=explicit_asset or context.get("asset_id")
    requested_assets=_assets_from_message(req.message)
    if supplied_asset: context["asset_id"]=supplied_asset
    if not req.message or not req.message.strip(): raise HTTPException(422,"Chat message must not be empty.")
    if len(req.message)>_ORCH_CONFIG["chat"]["max_message_characters"]: raise HTTPException(422,"Chat message exceeds the configured length limit.")
    signal=context.get("signal"); scenario=context.get("scenario")
    pending=context.get("_pending_message","")
    if pending and "fleet" in pending.lower() and not context.get("timeframe"):
        timeframe=re.search(r"\b(today|this week|next week|last \d+ days?|next \d+ days?)\b",req.message.lower())
        if timeframe: context["timeframe"]=timeframe.group(1)
    if scenario and signal is None:
        try: signal=_get_demo_signal(str(scenario),int(context.get("row_index",-1)))
        except (KeyError,ValueError,TypeError) as exc: raise HTTPException(404,f"Scenario unavailable: {scenario}") from exc
    _preload_plan=plan_query(pending or req.message,signal is not None)
    # Registered UI demo assets have an explicit backend scenario mapping.
    # Loading that mapped fixture is evidence-backed demo behavior, not an
    # inference from the asset name. Deployments can disable it in config.
    if (signal is None and not scenario and len(requested_assets) == 1
            and _preload_plan.intent != "learning_history"
            and bool(requested_assets[0][2])
            and _ORCH_CONFIG["chat"].get("auto_load_registered_demo_scenarios", False)):
        _, mapped_asset, mapped_scenario = requested_assets[0]
        if supplied_asset == mapped_asset:
            signal = _get_demo_signal(mapped_scenario, -1)
            scenario = mapped_scenario
            context.update({
                "scenario": mapped_scenario,
                "row_index": -1,
                "evidence_source": "registered_demo_scenario",
            })
    effective_message=req.message
    if pending and (incoming or explicit_asset or scenario or context.get("timeframe")):
        effective_message=context["_pending_message"]
    plan=plan_query(effective_message,signal is not None); state={"pipeline_log":[]}
    # ── Social / acknowledgment short-circuit ────────────────────────────────
    _SOCIAL_PHRASES=frozenset({"thanks","thank you","ok","okay","got it","understood","noted",
                               "great","awesome","perfect","sounds good","alright","sure",
                               "hi","hello","hey","bye","goodbye","cheers","cool","np","nice",
                               "good","excellent","brilliant","appreciate it","appreciated"})
    if effective_message.strip().lower().rstrip(" !.,") in _SOCIAL_PHRASES:
        _soc={"run_id":run_id,"conversation_id":conversation_id,"intent":"conversational",
              "persona":selected_persona.id,"persona_context":selected_persona.model_dump(),
              "reflection_status":"accepted",
              "response":"You're welcome! Let me know if there's anything else I can help you with.",
              "details":[],"actions":[],"call_plan":[],"pipeline_log":[],
              "needs_context":False,"clarification_required":False}
        _remember_chat_context(conversation_id,context)
        _soc_result = _attach_chat_learning(
            _soc, req, run_id, conversation_id, "conversational",
            selected_persona, requested_assets, supplied_asset or "",
        )
        _persist_chat_turn(conversation_id, req, user, _soc_result)
        return _soc_result

    # ── Intent classification ─────────────────────────────────────────────────
    # LLM reads message + conversation history to distinguish:
    #   "pipeline"       → new analysis requested → evidence-gated routing below
    #   "conversational" → follow-up on a prior result → LLM answers with history context
    #   "general"        → conceptual question → falls through to plan_query routing below
    _intent_class=_llm_chat_classify(effective_message,req.conversation_history)
    _classified=_intent_class.get("intent_type","pipeline")

    if (_classified=="conversational" and not plan.needs_signal
            and not requested_assets and plan.intent not in {"fleet", "learning_history"}):
        # Early return: answer follow-up with LLM and history, skip all pipeline routing
        _conv_draft=_conversational_draft(req.message,req.conversation_history,req.persona,
                                          pipeline_context=context.get("last_pipeline_result"))
        _conv_reflected=_REFLEXION.process(_conv_draft,state,plan)
        _remember_chat_context(conversation_id,context)
        _conv_result={**_conv_reflected.response,"run_id":run_id,"conversation_id":conversation_id,
                      "intent":"conversational","reflection_status":_conv_reflected.status,"pipeline_log":[]}
        _conv_result = _attach_chat_learning(
            _conv_result, req, run_id, conversation_id, "conversational",
            selected_persona, requested_assets, supplied_asset or "",
        )
        try: _conv_result["audit"]=write_audit(_AUDIT_PATH,event="chat",run_id=run_id,status="ok",intent="conversational",agents=())
        except Exception: _conv_result["audit"]={"status":"audit_write_failed"}
        _persist_chat_turn(conversation_id, req, user, _conv_result)
        return _conv_result

    _hitl_fields: Dict = {}  # populated when pipeline runs inline
    if len(requested_assets)>1:
        # Promote every supplied or conversation-cached signal into a partial
        # fleet snapshot. Missing/unknown assets remain explicit unavailable
        # rows; they must not suppress results backed by valid evidence.
        _snapshot=(deepcopy(context.get("fleet_snapshot"))
                   if isinstance(context.get("fleet_snapshot"),dict) else {})
        _per_asset=context.get("per_asset_signals",{})
        if isinstance(_per_asset,dict):
            for display,canonical,_ in requested_assets:
                cached=_per_asset.get(canonical)
                if isinstance(cached,dict) and display not in _snapshot:
                    _snapshot[display]=(deepcopy(cached) if (
                        "signal" in cached or "scenario" in cached
                    ) else {"signal":deepcopy(cached)})
        if isinstance(signal,dict):
            signal_asset=str(signal.get("asset_id","")).upper()
            for display,canonical,_ in requested_assets:
                if canonical==signal_asset and display not in _snapshot:
                    _snapshot[display]={"signal":deepcopy(signal)}
                    break
        if _snapshot:
            context["fleet_snapshot"]=_snapshot
    if len(requested_assets)>1 and not context.get("fleet_snapshot"):
        draft=_clarification_draft(req.persona,"multi_asset_plan",["fleet_snapshot"],
            ["Please provide approved telemetry or an explicitly selected scenario for at least one named asset."],
            "Asset names alone are not evidence. No asset result has been generated.")
        plan_intent="multi_asset_plan"
    elif len(requested_assets)>1:
        draft,state=_multi_asset_plan(
            requested_assets, context["fleet_snapshot"], req.persona, run_id,
            checkpoint_thread_id=conversation_id,
        )
        plan_intent="multi_asset_plan"
    elif _is_general_knowledge_request(
            effective_message, plan, _classified, signal,
            supplied_asset or "", requested_assets):
        draft,state=_general_knowledge_draft(effective_message,req.persona)
        plan_intent=plan.intent
    elif plan.intent == "fleet":
        missing=[]
        if not context.get("timeframe"): missing.append("timeframe")
        if not context.get("fleet_snapshot"): missing.append("fleet_snapshot")
        if not missing: missing.append("fleet_aggregation_service")
        draft=_clarification_draft(req.persona,"fleet",missing,
            ["What timeframe should be assessed?",
             "Which approved fleet snapshot or live fleet source should be used?",
             "Connect the approved fleet aggregation service before requesting a fleet ranking."],
            "A fleet comparison requires validated fleet evidence and an aggregation path. No ranking has been fabricated.")
        plan_intent=plan.intent
    elif plan.intent == "learning_history":
        asset_label=(requested_assets[0][0] if len(requested_assets)==1 else supplied_asset or "")
        draft,records=_learning_history_draft(
            req.persona,3,asset_id=supplied_asset or "",asset_label=asset_label
        )
        state={"pipeline_log":[{"node":"learning_memory","status":"history_query",
            "latency_ms":0,"count":len(records),"asset_id":supplied_asset or ""}]}
        plan_intent=plan.intent
    elif plan.needs_signal and signal is None:
        if supplied_asset and supplied_asset not in load_asset_master():
            draft=_clarification_draft(req.persona,plan.intent,["asset_registration"],
                [f"Please register and validate {supplied_asset} with its bearing and channel mappings before resubmitting."],
                f"Asset {supplied_asset} is not registered. No analytical agent has been run.",supplied_asset)
        else:
            missing=["telemetry_or_scenario"]
            questions=["Please provide current telemetry or a validated scenario for the asset."]
            if not supplied_asset:
                missing.insert(0,"asset_id"); questions.insert(0,"Which asset should be assessed?")
            draft=_clarification_draft(req.persona,plan.intent,missing,questions,
                f"I need clarification before answering this {plan.intent} question. No agent decision was fabricated.",supplied_asset or "")
        plan_intent=plan.intent
    elif signal is not None:
        required={"telemetry_id","timestamp_utc","asset_id","bearing_id","channel_id"}
        missing=sorted(field for field in required if not signal.get(field)) if isinstance(signal,dict) else sorted(required)
        if missing:
            draft=_clarification_draft(req.persona,plan.intent,missing,
                [f"Please provide {field}." for field in missing],
                "The telemetry payload is incomplete, so the agent pipeline has not been started.",
                str(signal.get("asset_id",supplied_asset or "")) if isinstance(signal,dict) else supplied_asset or "")
        else:
            state=run_pipeline(
                deepcopy(signal), run_id=run_id, intent=plan.pipeline_intent,
                inventory_lookup=context.get("inventory_lookup"),
                context_lookup=context.get("operations_context"),
                approval_status="pending", persona=req.persona,
                thread_id=conversation_id,
            )
            context.setdefault("per_asset_signals", {})[signal.get("asset_id", "")] = deepcopy(signal)
            formatted=format_for_persona(state,req.persona)
            lifecycle = _asset_lifecycle(supplied_asset or signal.get("asset_id", ""))
            if lifecycle and plan.intent == "status":
                asset_label = supplied_asset or signal.get("asset_id", "")
                if lifecycle.get("decision") == "rejected":
                    lifecycle_label = (
                        "critical — the latest maintenance recommendation was rejected "
                        "and the underlying risk remains unresolved"
                    )
                    lifecycle_action = (
                        "Obtain fresh telemetry or Reliability Engineer review before "
                        "continuing operation"
                    )
                elif lifecycle["status"] == "healthy":
                    lifecycle_label = "healthy after the completed maintenance"
                    lifecycle_action = None
                else:
                    lifecycle_label = "warning while the approved maintenance is pending"
                    lifecycle_action = (
                        "Complete the approved maintenance work order and verify "
                        "post-repair readings"
                    )
                formatted["headline"] = (
                    f"{asset_label} — Current status is {lifecycle_label}. "
                    f"Work order {lifecycle['work_order_id'] or 'on record'} is "
                    f"{lifecycle['work_order_status'] or 'active'}."
                )
                formatted["actions"] = [lifecycle_action] if lifecycle_action else []
            _pipeline_headline=formatted.get("headline","Analysis complete.")
            _persona_context = build_persona_context(req.persona)
            draft={"persona":_persona_context.id,"persona_context":_persona_context.model_dump(),"response":_pipeline_headline,"details":formatted.get("details",[]),"actions":formatted.get("actions",[]),"call_plan":list(plan.agents),"needs_context":False,"clarification_required":False,
                   "agent_outputs":{"recommendation":state["recommendation"].to_dict() if state.get("recommendation") else None,
                                    "execution_result":state["execution_result"].to_dict() if state.get("execution_result") else None}}
            context["last_pipeline_result"] = {
                "asset_id": signal.get("asset_id", "") if isinstance(signal, dict) else "",
                "pipeline_intent": plan.pipeline_intent,
                "headline": _pipeline_headline,
                "details": formatted.get("details", []),
                "actions": formatted.get("actions", []),
                "summary": (
                    f"Headline: {_pipeline_headline}\n"
                    f"Details: {'; '.join(formatted.get('details', []))}\n"
                    f"Recommended actions: {'; '.join(formatted.get('actions', []))}"
                ),
            }
            # LLM synthesis: convert structured pipeline results into a direct answer
            # Never let conversational synthesis fill gaps when the pipeline
            # stopped before a valid risk result. An incomplete run must remain
            # explicit rather than being rewritten as an asset conclusion.
            _risk_ready = bool(
                state.get("risk_assessment")
                and getattr(state.get("risk_assessment"), "risk_eligible", False)
            )
            if _CHAT_LLM.is_configured() and _risk_ready:
                try:
                    _pipe_summary=(
                        f"Headline: {_pipeline_headline}\n"
                        f"Details: {'; '.join(formatted.get('details',[]))}\n"
                        f"Recommended actions: {'; '.join(formatted.get('actions',[]))}"
                    )
                    _synthesis_q = re.sub(
                        r'\s*\[(?:Session context|Context for)[^\]]*\]', '', req.message
                    ).strip()
                    _synth=_CHAT_LLM.complete_json(
                        system_prompt=(
                            "You are the DRO plant-maintenance assistant. "
                            "Use ONLY the pipeline data provided to answer the user's question directly. "
                            "If the question is yes/no, start with YES or NO. "
                            "Write 3-4 connected prose sentences — not a list of measurements. "
                            "IMPORTANT: do not mention any value that is exactly 0, '0–0', '0%', or '$0' — "
                            "these mean the agent did not run and the figure is unavailable. "
                            "Imputed or baseline values ARE valid — report them and note '(imputed from baseline)' if relevant. "
                            "Never fabricate data not in the pipeline result. "
                            'Return JSON: {"answer": "<synthesized plain-text answer>"}.\n\n'
                            + persona_response_prompt(_persona_context)
                        ),
                        user_prompt=f"User question: {_synthesis_q}\n\nPipeline data:\n{_pipe_summary}",
                        temperature=0.2,
                        max_tokens=200,
                    )
                    if _synth and isinstance(_synth.get("answer"),str) and _synth["answer"].strip():
                        draft["response"]=_synth["answer"].strip()
                        # Targeted queries: synthesis already contains the key facts,
                        # so suppress the raw bullet details to avoid duplication.
                        # Full-pipeline intent keeps them for the richer HITL cards.
                        if plan.pipeline_intent != "full":
                            draft["details"] = []
                            draft["actions"] = []
                except Exception:
                    pass  # keep structured headline on LLM failure
            # Only engage HITL gates for explicit full-pipeline requests
            # (e.g. "Analyse M-104"). Targeted questions (risk, status, diagnosis)
            # get a synthesized answer without operator interruption.
            if plan.pipeline_intent == "full":
                _hitl_fields = _build_hitl_gates(state, run_id, req.persona, plan.pipeline_intent)
        plan_intent=plan.intent
    else:
        draft=_clarification_draft(req.persona,"general",["objective","timeframe"],
            ["What decision or explanation do you need?","What timeframe or operational scope applies?"],
            "This question is broad or requires operational evidence. Please clarify the objective and scope before I answer.")
        plan_intent=plan.intent
    reflected=_REFLEXION.process(draft,state,plan)
    if draft.get("clarification_required"):
        context["_pending_message"]=effective_message
    else:
        context.pop("_pending_message",None)
    _remember_chat_context(conversation_id,context)
    result={**reflected.response,"run_id":run_id,"conversation_id":conversation_id,"intent":plan_intent,
        "reflection_status":reflected.status,"reflection_iterations":reflected.iterations,
        "reflection_termination_reason":reflected.termination_reason,
        "pipeline_log":state.get("pipeline_log",[]),
        "context_status":{"retained_fields":sorted(
            key for key in context
            if not key.startswith("_")
            and key not in {"last_pipeline_result", "per_asset_signals"}
        ),
                          "pending":bool(context.get("_pending_message"))}}
    if _hitl_fields:
        result.update(_hitl_fields)
    result = _attach_chat_learning(
        result, req, run_id, conversation_id, plan_intent,
        selected_persona, requested_assets, supplied_asset or "",
    )
    try: result["audit"]=write_audit(_AUDIT_PATH,event="chat",run_id=run_id,status="ok",intent=plan.intent,agents=draft.get("call_plan",plan.agents))
    except Exception: result["audit"]={"status":"audit_write_failed"}
    _persist_chat_turn(conversation_id, req, user, result)
    return result


# ── Work-order endpoints ──────────────────────────────────────────────────────

@app.get("/api/workorders")
def get_workorders():
    return {"workorders": _list_workorders()}


@app.post("/api/workorders")
def create_workorder(payload: Dict[str, Any]):
    wo_id = f"WO-{datetime.now(timezone.utc).strftime('%Y-%m%d-%H%M%S')}"
    wo = {"id": wo_id, "status": "Pending", "created_by": "DRO Agent", **payload}
    return _save_workorder(wo)


@app.patch("/api/workorders/{wo_id}")
def update_workorder(wo_id: str, req: WOUpdateRequest):
    wo = _get_workorder(wo_id)
    if not wo:
        raise HTTPException(404, f"Work order '{wo_id}' not found.")
    if req.status:
        wo["status"] = req.status
        if req.updated_by:
            from datetime import datetime, timezone
            wo["updated_by"] = req.updated_by
            wo["updated_at"] = datetime.now(timezone.utc).isoformat()
            wo.setdefault("status_log", []).append({
                "status": req.status,
                "by": req.updated_by,
                "at": wo["updated_at"],
            })
    if req.assigned_to:
        wo["assigned_to"] = req.assigned_to
    if req.checklist_index is not None and req.checklist_done is not None:
        checklist = wo.get("checklist", [])
        if 0 <= req.checklist_index < len(checklist):
            checklist[req.checklist_index]["done"] = req.checklist_done
    return _save_workorder(wo)


# ── WebSocket: live sensor stream ─────────────────────────────────────────────

_DEFAULT_SENSOR = {"vib": 2.0, "temp": 55.0, "bpfo": 1.0, "rpm": 1480}


def _sensor_baselines() -> Dict[str, Dict[str, float]]:
    """Build websocket baselines from the same fleet evidence as the API."""
    result = {}
    for display, (_, scenario) in _MULTI_ASSET_SCENARIOS.items():
        rows = load_telemetry_rows(scenario)
        if not rows:
            continue
        signal = rows[-1]
        result[display] = {
            "vib": signal.get("vib_rms_mm_s") or _DEFAULT_SENSOR["vib"],
            "temp": signal.get("temp_c") or _DEFAULT_SENSOR["temp"],
            "bpfo": signal.get("bpfo_energy") or _DEFAULT_SENSOR["bpfo"],
            "rpm": signal.get("rpm") or _DEFAULT_SENSOR["rpm"],
        }
    return result


_SENSOR_BASELINES = _sensor_baselines()


@app.websocket("/ws/sensors/{asset_id}")
async def sensor_stream(websocket: WebSocket, asset_id: str):
    """Stream simulated live sensor ticks for a given asset (1 tick / 4s)."""
    await websocket.accept()
    base = _SENSOR_BASELINES.get(asset_id, _DEFAULT_SENSOR)
    # Critical assets trend upward slightly; healthy assets stay flat
    trend = 0.02 if asset_id in ("M-104",) else 0.0
    t = 0
    try:
        while True:
            vib  = round(base["vib"]  + trend * t + random.gauss(0, 0.15), 2)
            temp = round(base["temp"] + trend * t * 0.5 + random.gauss(0, 0.4), 1)
            tick = {
                "asset_id": asset_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "vib_rms_mms": max(0.1, vib),
                "temp_c":      max(20.0, temp),
                "bpfo_ratio":  round(base["bpfo"] + random.gauss(0, 0.03), 3),
                "rpm":         int(base["rpm"] + random.gauss(0, 5)),
            }
            await websocket.send_json(tick)
            await asyncio.sleep(4)
            t += 1
    except WebSocketDisconnect:
        pass


# ── Serve React build — must be last so /api/* routes take priority ──────────
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
