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
  GET  /api/notifications/counts unread notification counts for all personas
  GET  /api/notifications/{persona_id}         persona notification inbox
  POST /api/notifications/{persona_id}/read    mark all notifications read
  GET  /api/workorders           in-memory work-order store
  POST /api/workorders           create work order
  PATCH /api/workorders/{wo_id}  update WO status / checklist
  WS   /ws/sensors/{asset_id}    stream simulated live sensor ticks

CORS is open for local dev (frontend HTML served from any origin).
"""
from __future__ import annotations

import asyncio
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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
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
from src.tools.data_loader import load_telemetry_rows, load_asset_master
from src.orchestrator.query_router import plan_query
from src.agents.reflexion_agent import ReflexionAgent
from src.tools.orchestrator_audit import write_audit
from src.tools.llm_client import LLMClient
from src.tools.hitl_repository import build_hitl_repository
from src.tools.persona_formatter import build_persona_context
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
    "You are a rotating-equipment reliability engineer in a plant maintenance team chat. "
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


def _conversational_draft(message: str, conversation_history: list | None, persona: str) -> dict:
    """Build a response for a conversational follow-up using LLM with history context."""
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
            user_prompt = (
                f"Conversation history:\n{history_text}\n\nUser follow-up: {message}"
                if history_text else message
            )
            llm_resp = _CHAT_LLM.complete_json(
                system_prompt=_CONVERSATIONAL_LLM_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.4,
                max_tokens=400,
            )
            if llm_resp and isinstance(llm_resp.get("answer"), str) and llm_resp["answer"].strip():
                return {"persona": persona, "response": llm_resp["answer"].strip(),
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
def _learning_history_draft(persona: str, limit: int = 3):
    cases = _learning_agent().recent_cases(limit)
    if not cases:
        return ({"persona": persona,
            "response": "No validated closed failure cases are available in Agent 8 memory. Learning is recorded only after successful execution and confirmed closure feedback.",
            "details": [], "actions": [], "case_references": [],
            "call_plan": ["agent_8"], "needs_context": False,
            "agent_outputs": {"learned_cases": []}}, cases)
    details = [
        f"{case.get('case_id', 'unknown case')}: {case.get('fault_mode', 'unknown fault')}; outcome: {case.get('outcome', 'not recorded')}. Learning: {case.get('content', 'not recorded')}"
        for case in cases
    ]
    audience = "Executive summary" if str(persona).lower() in {"md", "executive", "manager"} else "Failure history"
    return ({"persona": persona,
        "response": f"{audience}: the last {len(cases)} validated closed failure cases are summarized below.",
        "details": details,
        "actions": ["Use these confirmed outcomes to review recurring causes and maintenance effectiveness."],
        "case_references": [f"Learned case {case.get('case_id', '')}" for case in cases],
        "call_plan": ["agent_8"], "needs_context": False,
        "agent_outputs": {"learned_cases": cases}}, cases)


_MULTI_ASSET_SCENARIOS = {
    "M-104": ("AST_MTR_001", "outer_race_fault"),
    "P-207": ("AST_PMP_001", "lubrication_issue"),
    "C-301": ("AST_CON_001", "signal_dropout"),
    "M-089": ("AST_MTR_002", "healthy"),
    "G-112": ("AST_GBX_001", "gearbox_fault"),
}

Persona = Literal["supervisor", "engineer", "maintenance", "manager",
                  "executive", "md", "ot", "safety"]
_PERSONAS = {"supervisor", "engineer", "maintenance", "manager",
             "executive", "md", "ot", "safety"}


_GENERAL_RAG = None


def _general_rag_service():
    """Lazy and patchable so startup and tests do not require cloud retrieval."""
    global _GENERAL_RAG
    if _GENERAL_RAG is None:
        _GENERAL_RAG = GovernedKnowledgeRAG()
    return _GENERAL_RAG


def _general_knowledge_draft(message: str, persona: str):
    started = time.perf_counter()
    result = _general_rag_service().answer(message, llm_client=_CHAT_LLM)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    source_type = {
        "grounded": "approved_rag",
        "no_match": "rag_no_match",
        "retrieval_failed": "rag_error",
        "blocked": "rag_blocked",
    }.get(result.status, "rag_error")
    result_dict = result.to_dict()
    draft = {
        "persona": persona,
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
    return {"persona": persona, "response": reason, "details": [], "actions": [],
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
    """Return ordered, deduplicated (display, canonical, scenario) matches."""
    upper = str(message).upper()
    positions = []
    for display,(asset_id,scenario) in _MULTI_ASSET_SCENARIOS.items():
        candidates = [(upper.find(display),display)]
        candidates.append((upper.find(asset_id),display))
        valid = [(pos,label) for pos,label in candidates if pos >= 0]
        if valid: positions.append((min(pos for pos,_ in valid),display,asset_id,scenario))
    positions.sort(key=lambda item:item[0])
    return [(display,asset_id,scenario) for _,display,asset_id,scenario in positions]


def _deadline_for_recommendation(recommendation) -> str:
    if recommendation.window_chosen: return recommendation.window_chosen
    return {"immediate":"today","urgent":"within 48 hours","planned":"this week",
            "monitor":"continue monitoring"}.get(recommendation.urgency,"review this week")


def _multi_asset_plan(requested_assets, fleet_snapshot: dict, persona: str,
                      run_id: str, checkpoint_thread_id: str = ""):
    """Evaluate named assets only from explicitly supplied fleet evidence."""
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
    draft={"persona":persona,
        "response":f"Evidence-based action plan evaluated {evaluated} of {len(results)} requested assets.",
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

# ── Serve the frontend HTML ───────────────────────────────────────────────────
_FRONTEND_HTML = os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "index.html"
)


@app.get("/")
def root():
    if os.path.exists(_FRONTEND_HTML):
        return FileResponse(_FRONTEND_HTML)
    return {"message": "DRO API running. Frontend not found at frontend/index.html."}


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


# ── In-memory work-order store ────────────────────────────────────────────────
_WORKORDERS: List[Dict] = [
    {
        "id": "WO-2024-1847", "priority": "URGENT", "status": "Pending",
        "title": "M-104 Drive-End Bearing Replacement",
        "asset_id": "M-104", "type": "Corrective", "est_hours": 4,
        "parts": "SKF 6310-2RS (Bin A-14)", "assigned_to": "Pending",
        "due": "Wed 06:00", "created_by": "DRO Agent",
        "checklist": [
            {"text": "LOTO permit EL-104-A obtained and valid", "done": True, "tag": "Safety"},
            {"text": "Assign crew: T.Rodriguez (lead) + K.Mensah", "done": False, "tag": "Crew"},
            {"text": "Confirm SKF 6310-2RS at Bin A-14", "done": False, "tag": "Parts"},
            {"text": "Brief crew on SOP M-104-REP-04 Rev 4.1", "done": False, "tag": "Safety"},
            {"text": "Isolate M-104 per LOTO EL-104-A", "done": False, "tag": "LOTO"},
            {"text": "Replace bearing, torque 85 Nm", "done": False, "tag": "Technical"},
            {"text": "Post-repair vibration baseline ≤2.5 mm/s", "done": False, "tag": "QA"},
        ],
    },
    {
        "id": "WO-2024-1831", "priority": "MEDIUM", "status": "Scheduled",
        "title": "P-207 Feed Pump – Lubrication & Vibration Check",
        "asset_id": "P-207", "type": "Preventive", "est_hours": 2,
        "parts": "Mobil SHC 100 (1L)", "assigned_to": "T. Rodriguez",
        "due": "Wed 09:00", "created_by": "DRO Agent",
        "checklist": [
            {"text": "LOTO IL-207-B issued and current", "done": True, "tag": "Safety"},
            {"text": "Mobil SHC 100 collected from stores", "done": True, "tag": "Parts"},
            {"text": "Inspect bearing and lubrication state", "done": False, "tag": "Technical"},
            {"text": "Re-lubricate and reassemble", "done": False, "tag": "Technical"},
            {"text": "Post-lube vibration check – log baseline", "done": False, "tag": "QA"},
        ],
    },
]

_WO_INDEX = {wo["id"]: wo for wo in _WORKORDERS}

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


def _remember_chat_context(conversation_id: str, context: dict) -> None:
    if conversation_id not in _CHAT_CONTEXT_STORE and len(_CHAT_CONTEXT_STORE) >= _MAX_CHAT_CONTEXTS:
        _CHAT_CONTEXT_STORE.pop(next(iter(_CHAT_CONTEXT_STORE)))
    stored=deepcopy(context)
    meta=stored.setdefault("_meta",{})
    meta["updated_at"]=time.time()
    meta["turns"]=min(int(meta.get("turns",0))+1,_MAX_CHAT_TURNS+1)
    _CHAT_CONTEXT_STORE[conversation_id] = stored


def _load_chat_context(conversation_id: str) -> dict:
    context=deepcopy(_CHAT_CONTEXT_STORE.get(conversation_id,{}))
    meta=context.get("_meta",{})
    if context and (time.time()-float(meta.get("updated_at",0))>_CHAT_CONTEXT_TTL
                    or int(meta.get("turns",0))>=_MAX_CHAT_TURNS):
        _CHAT_CONTEXT_STORE.pop(conversation_id,None)
        return {}
    return context


def _public_context(context: Optional[Dict]) -> dict:
    """Reject client attempts to write reserved conversation state keys."""
    return {str(key):deepcopy(value) for key,value in (context or {}).items()
            if not str(key).startswith("_")}


def _authorize_hitl(persona: str, gate: str) -> None:
    if persona not in _PERSONAS:
        raise HTTPException(422,f"Unknown persona: {persona}")
    if persona not in set(_HITL_PERMISSIONS.get(gate,[])):
        raise HTTPException(403,f"Persona '{persona}' is not permitted to resolve the {gate} gate.")


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
    return {"type":"advisory","advisory_note":risk.advisory_note,"run_id":run_id}

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

    rec = state.get("recommendation")
    recommendation = None
    if rec is not None:
        try:
            recommendation = (rec.model_dump() if hasattr(rec,"model_dump")
                              else rec.to_dict() if hasattr(rec,"to_dict") else None)
        except Exception:
            recommendation = None

    return {
        "hitl_required":  hitl_required,
        "hitl_monitoring":hitl_monitoring,
        "hitl_diagnosis": hitl_diagnosis,
        "hitl_knowledge": hitl_knowledge,
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
                        else _to_dict(state.get("recommendation"))),
        execution_result=_to_dict(state.get("execution_result")),
        learned_case=_to_dict(state.get("learned_case")),
        hitl_required=hitl_required,
        hitl_advisory=hitl_advisory,
        hitl_monitoring=hitl_monitoring,
        hitl_diagnosis=hitl_diagnosis,
        hitl_knowledge=hitl_knowledge,
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
        headline=formatted.get("headline", ""),
        details=formatted.get("details", []),
        actions=formatted.get("actions", []),
        tags=formatted.get("tags", []),
        pipeline_log=state.get("pipeline_log", []),
        fault_diagnosis=_to_dict(diagnosis),
        risk_assessment=None if _diag_paused else _to_dict(risk),
        knowledge_guidance=None if _diag_paused else _to_dict(knowledge),
        hitl_advisory=hitl_advisory if not _diag_paused else None,
        hitl_diagnosis=hitl_diagnosis,
        hitl_knowledge=hitl_knowledge,
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
        headline=_headline, details=_details, actions=_actions, tags=_tags,
        pipeline_log=state.get("pipeline_log", []),
        fault_diagnosis=None if _diag_paused else _to_dict(diagnosis),
        risk_assessment=None if _diag_paused else _to_dict(risk),
        knowledge_guidance=None if _diag_paused else _to_dict(knowledge),
        hitl_advisory=hitl_advisory if not _diag_paused else None,
        hitl_diagnosis=hitl_diagnosis,
        hitl_knowledge=hitl_knowledge,
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
        headline=formatted.get("headline", ""),
        details=formatted.get("details", []),
        actions=formatted.get("actions", []),
        tags=formatted.get("tags", []),
        pipeline_log=state.get("pipeline_log", []),
        fault_diagnosis=_to_dict(diagnosis),
        risk_assessment=_to_dict(risk),
        knowledge_guidance=_to_dict(knowledge),
        hitl_advisory=hitl_advisory,
        hitl_knowledge=hitl_knowledge,
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
            result = ExecutorAgent().process(rec, approved=req.approved)
        if req.approved and result.status in {"success", "partial"}:
            _learning_agent().record_approval(
                rec.case_id, rec.asset_id, rec.condition.fault_type
            )
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
    feedback = RejectionFeedback(
        case_id=req.case_id, asset_id=req.asset_id,
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
    return {
        "scenarios": [
            {"name": "outer_race_fault",    "description": "Motor outer race spall — Stage 1→3"},
            {"name": "inner_race_fault",    "description": "Motor inner race degradation"},
            {"name": "lubrication_issue",   "description": "Pump lubrication degradation"},
            {"name": "rolling_element_fault", "description": "Ball or roller bearing fault (BSF signature)"},
            {"name": "healthy",             "description": "Healthy motor — no fault"},
            {"name": "fi_hitl_test",        "description": "HITL test: early inner-race, low confidence → Gate 3"},
            {"name": "knowledge_hitl_test", "description": "HITL test: undetermined fault → Gate 5 (no SOP)"},
        ]
    }


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


@app.get("/api/dashboard/assets")
def dashboard_assets(user: Dict = Depends(get_current_user)):
    """Return frontend-ready fleet health derived from backend agent evidence."""
    from src.tools.dashboard_service import build_asset_dashboard
    return build_asset_dashboard(
        load_asset_master(), _MULTI_ASSET_SCENARIOS, load_telemetry_rows,
        lambda signal, intent: run_pipeline(
            signal, intent=intent, persona="supervisor"
        ),
    )


# ── Chat endpoint ─────────────────────────────────────────────────────────────

@app.post("/api/chat")
@traceable(name="DRO Chat API", run_type="chain", tags=["dro", "chat-api"])
def chat(req: ChatRequest, user: Dict = Depends(get_current_user)):
    """Route persona-aware natural language through the backend orchestrator."""
    require_persona(req.persona, user)
    run_id=f"CHAT-{uuid.uuid4().hex[:10].upper()}"
    conversation_id=req.conversation_id or f"CONV-{uuid.uuid4().hex[:10].upper()}"
    if len(conversation_id)>80 or not re.fullmatch(r"[A-Za-z0-9_.:-]+",conversation_id):
        raise HTTPException(422,"conversation_id contains unsupported characters or is too long.")
    if req.message.strip().lower() in {"reset","start over","new conversation","cancel this request"}:
        _CHAT_CONTEXT_STORE.pop(conversation_id,None)
        return {"run_id":run_id,"conversation_id":conversation_id,"intent":"reset",
            "persona":req.persona,"response":"Conversation context has been cleared.",
            "details":[],"actions":[],"call_plan":[],"pipeline_log":[],"sources":[],
            "needs_context":False,"clarification_required":False,"reflection_status":"accepted"}
    context=_load_chat_context(conversation_id)
    incoming=_public_context(req.context)
    explicit_asset=req.asset_id or _asset_from_message(req.message)
    existing_signal=context.get("signal")
    existing_scenario=context.get("scenario")
    _ctx_scenario_asset=next((aid for _,(aid,sc) in _MULTI_ASSET_SCENARIOS.items() if sc==existing_scenario),"")
    if explicit_asset and (
        (isinstance(existing_signal,dict) and existing_signal.get("asset_id")!=explicit_asset) or
        (_ctx_scenario_asset and _ctx_scenario_asset!=explicit_asset)
    ):
        context.pop("signal",None); context.pop("scenario",None); context.pop("row_index",None)
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
    # Registered UI demo assets have an explicit backend scenario mapping.
    # Loading that mapped fixture is evidence-backed demo behavior, not an
    # inference from the asset name. Deployments can disable it in config.
    if (signal is None and not scenario and len(requested_assets) == 1
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
              "persona":req.persona,"reflection_status":"accepted",
              "response":"You're welcome! Let me know if there's anything else I can help you with.",
              "details":[],"actions":[],"call_plan":[],"pipeline_log":[],
              "needs_context":False,"clarification_required":False}
        _remember_chat_context(conversation_id,context)
        return _soc

    # ── Intent classification ─────────────────────────────────────────────────
    # LLM reads message + conversation history to distinguish:
    #   "pipeline"       → new analysis requested → evidence-gated routing below
    #   "conversational" → follow-up on a prior result → LLM answers with history context
    #   "general"        → conceptual question → falls through to plan_query routing below
    _intent_class=_llm_chat_classify(effective_message,req.conversation_history)
    _classified=_intent_class.get("intent_type","pipeline")

    if (_classified=="conversational" and not plan.needs_signal
            and not requested_assets and plan.intent not in {"fleet"}):
        # Early return: answer follow-up with LLM and history, skip all pipeline routing
        _conv_draft=_conversational_draft(req.message,req.conversation_history,req.persona)
        _conv_reflected=_REFLEXION.process(_conv_draft,state,plan)
        _remember_chat_context(conversation_id,context)
        _conv_result={**_conv_reflected.response,"run_id":run_id,"conversation_id":conversation_id,
                      "intent":"conversational","reflection_status":_conv_reflected.status,"pipeline_log":[]}
        try: _conv_result["audit"]=write_audit(_AUDIT_PATH,event="chat",run_id=run_id,status="ok",intent="conversational",agents=())
        except Exception: _conv_result["audit"]={"status":"audit_write_failed"}
        return _conv_result

    _hitl_fields: Dict = {}  # populated when pipeline runs inline
    if len(requested_assets)>1 and not isinstance(context.get("fleet_snapshot"),dict):
        draft=_clarification_draft(req.persona,"multi_asset_plan",["fleet_snapshot"],
            ["Please provide an approved fleet snapshot containing telemetry or an explicitly selected scenario for each named asset."],
            "Asset names alone are not evidence. No fleet or asset result has been generated.")
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
        draft,cases=_learning_history_draft(req.persona,3)
        state={"pipeline_log":[{"node":"learning_memory","status":"history_query","latency_ms":0,"count":len(cases)}]}
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
            formatted=format_for_persona(state,req.persona)
            _pipeline_headline=formatted.get("headline","Analysis complete.")
            draft={"persona":req.persona,"response":_pipeline_headline,"details":formatted.get("details",[]),"actions":formatted.get("actions",[]),"call_plan":list(plan.agents),"needs_context":False,"clarification_required":False,
                   "agent_outputs":{"recommendation":state["recommendation"].to_dict() if state.get("recommendation") else None,
                                    "execution_result":state["execution_result"].to_dict() if state.get("execution_result") else None}}
            # LLM synthesis: convert structured pipeline results into a direct answer
            if _CHAT_LLM.is_configured():
                try:
                    _pipe_summary=(
                        f"Headline: {_pipeline_headline}\n"
                        f"Details: {'; '.join(formatted.get('details',[]))}\n"
                        f"Recommended actions: {'; '.join(formatted.get('actions',[]))}"
                    )
                    _synth=_CHAT_LLM.complete_json(
                        system_prompt=(
                            "You are a rotating-equipment reliability engineer. "
                            "Use ONLY the pipeline data provided to answer the user's question directly. "
                            "If the question is yes/no, start with YES or NO. "
                            "Be concise (2-4 sentences). Never fabricate data not in the pipeline result. "
                            'Return JSON: {"answer": "<synthesized plain-text answer>"}'
                        ),
                        user_prompt=f"User question: {req.message}\n\nPipeline data:\n{_pipe_summary}",
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
        "context_status":{"retained_fields":sorted(key for key in context if not key.startswith("_")),
                          "pending":bool(context.get("_pending_message"))}}
    if _hitl_fields:
        result.update(_hitl_fields)
    try: result["audit"]=write_audit(_AUDIT_PATH,event="chat",run_id=run_id,status="ok",intent=plan.intent,agents=draft.get("call_plan",plan.agents))
    except Exception: result["audit"]={"status":"audit_write_failed"}
    return result


# ── Work-order endpoints ──────────────────────────────────────────────────────

@app.get("/api/workorders")
def get_workorders():
    return {"workorders": _WORKORDERS}


@app.post("/api/workorders")
def create_workorder(payload: Dict[str, Any]):
    wo_id = f"WO-{datetime.now(timezone.utc).strftime('%Y-%m%d-%H%M%S')}"
    wo = {"id": wo_id, "status": "Pending", "created_by": "DRO Agent", **payload}
    _WORKORDERS.append(wo)
    _WO_INDEX[wo_id] = wo
    return wo


@app.patch("/api/workorders/{wo_id}")
def update_workorder(wo_id: str, req: WOUpdateRequest):
    wo = _WO_INDEX.get(wo_id)
    if not wo:
        raise HTTPException(404, f"Work order '{wo_id}' not found.")
    if req.status:
        wo["status"] = req.status
    if req.assigned_to:
        wo["assigned_to"] = req.assigned_to
    if req.checklist_index is not None and req.checklist_done is not None:
        checklist = wo.get("checklist", [])
        if 0 <= req.checklist_index < len(checklist):
            checklist[req.checklist_index]["done"] = req.checklist_done
    return wo


# ── WebSocket: live sensor stream ─────────────────────────────────────────────

# Baseline sensor values per asset (vibration, temperature)
_SENSOR_BASELINES = {
    "M-104": {"vib": 14.7, "temp": 87.4, "bpfo": 4.02, "rpm": 1474},
    "P-207": {"vib": 4.1,  "temp": 61.2, "bpfo": 0.46, "rpm": 2900},
    "C-301": {"vib": 3.2,  "temp": 54.8, "bpfo": 0.82, "rpm": 960},
    "M-089": {"vib": 1.8,  "temp": 48.2, "bpfo": 0.9,  "rpm": 1480},
    "G-112": {"vib": 1.2,  "temp": 45.1, "bpfo": 0.7,  "rpm": 720},
}
_DEFAULT_SENSOR = {"vib": 2.0, "temp": 55.0, "bpfo": 1.0, "rpm": 1480}


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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
