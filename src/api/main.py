"""
api/main.py  —  DRO FastAPI server

Endpoints:
  POST /api/pipeline/run         run full DFA→Monitoring→FI→Risk→Knowledge pipeline
  GET  /api/pipeline/scenarios   list available demo scenarios
  GET  /api/assets               all assets from asset_master.json
  GET  /api/assets/{asset_id}    single asset detail
  POST /api/chat                 persona-aware chat (routes to pipeline or canned responses)
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
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Make repo root importable when run as: python -m src.api.main
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.orchestrator.graph import run_pipeline
from src.api.persona_formatter import format_for_persona
from src.tools.data_loader import load_telemetry_rows, load_asset_master
from src.orchestrator.query_router import plan_query
from src.agents.reflexion_agent import ReflexionAgent
from src.tools.orchestrator_audit import write_audit

app = FastAPI(title="DRO API", version="1.0.0")

_ORCH_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "orchestrator_config.json")
with open(_ORCH_CONFIG_PATH, "r", encoding="utf-8") as _fh:
    _ORCH_CONFIG = json.load(_fh)
_AUDIT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", _ORCH_CONFIG["audit"]["jsonl_path"])
_REFLEXION = ReflexionAgent(max_characters=_ORCH_CONFIG["reflection"]["max_response_characters"])


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    error_id = f"ERR-{uuid.uuid4().hex[:10].upper()}"
    try:
        write_audit(_AUDIT_PATH, event="api_error", status="unexpected", error_code=error_id)
    except Exception:
        pass
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR",
        "message": "The request could not be completed safely.", "error_id": error_id, "retryable": True}})
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

# Confidence threshold below which the FI HITL gate fires (Agent 3)
_DIAGNOSIS_CONFIDENCE_THRESHOLD = 0.60


# ── Pydantic request/response models ─────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    signal: Dict[str, Any]
    persona: str = "supervisor"
    scenario: Optional[str] = None   # convenience: run a named demo scenario
    row_index: int = -1              # row index within scenario (-1 = last)
    query: Optional[str] = None      # natural-language query → controls pipeline depth


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
    recommendation: Optional[Dict] = None   # Agent 7 POA: MaintenanceRecommendation
    hitl_required: Optional[Dict] = None    # Agent 1 DFA: FLAGGED → IMPUTE/DROP/KEEP
    hitl_advisory: Optional[Dict] = None    # Agent 4 PRA: LLM advisory → Accept/Reject
    hitl_monitoring: Optional[Dict] = None  # Agent 2 Monitoring: borderline EWMA → Suppress/Confirm
    hitl_diagnosis: Optional[Dict] = None   # Agent 3 FI: low confidence → Confirm/Override
    hitl_knowledge: Optional[Dict] = None   # Agent 5 Knowledge: no SOP → Flag/Accept
    raw_output: Optional[Dict] = None


class HITLRemediationRequest(BaseModel):
    run_id: str
    action: str        # IMPUTE | DROP | KEEP
    persona: str = "supervisor"


class HITLMonitoringRequest(BaseModel):
    run_id: str
    action: str        # SUPPRESS | CONFIRM
    persona: str = "supervisor"


class HITLDiagnosisRequest(BaseModel):
    run_id: str
    action: str        # CONFIRM | MARK_UNDETERMINED
    persona: str = "supervisor"


class HITLKnowledgeRequest(BaseModel):
    run_id: str
    action: str        # FLAG_MANUAL | ACCEPT_EMPTY
    persona: str = "supervisor"
    manual_sop_note: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    persona: str = "supervisor"
    asset_id: Optional[str] = None
    context: Optional[Dict] = None


class WOUpdateRequest(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    checklist_index: Optional[int] = None
    checklist_done: Optional[bool] = None


class ExecutorRunRequest(BaseModel):
    recommendation: Dict[str, Any]
    approved: bool = False


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
def pipeline_run(req: PipelineRunRequest):
    """Run the DRO pipeline on a signal; depth controlled by query intent."""
    intent = _classify_intent(req.query)
    try:
        if req.scenario:
            rows = load_telemetry_rows(req.scenario)
            if not rows:
                raise HTTPException(404, f"Scenario '{req.scenario}' not found.")
            idx = req.row_index if 0 <= req.row_index < len(rows) else len(rows) - 1
            signal = deepcopy(rows[idx])
            for i in range(idx):
                run_pipeline(deepcopy(rows[i]))
            state = run_pipeline(signal, intent=intent)
        else:
            state = run_pipeline(req.signal, intent=intent)
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
    hitl_advisory = None
    _risk = state.get("risk_assessment")
    if (_risk
            and getattr(_risk, "assessment_source", "") == "rules+llm_fallback"
            and getattr(_risk, "advisory_note", "")
            and hitl_monitoring is None and hitl_diagnosis is None):
        hitl_advisory = {
            "advisory_note": _risk.advisory_note,
            "run_id":        run_id,
        }

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
        hitl_required=hitl_required,
        hitl_advisory=hitl_advisory,
        hitl_monitoring=hitl_monitoring,
        hitl_diagnosis=hitl_diagnosis,
        hitl_knowledge=hitl_knowledge,
    )


@app.post("/api/pipeline/hitl/remediation")
def hitl_remediation(req: HITLRemediationRequest):
    """Resolve a DFA HITL gate: apply IMPUTE/DROP/KEEP and resume the pipeline."""
    stored = _HITL_STORE.pop(req.run_id, None)
    if not stored:
        raise HTTPException(404, f"HITL session '{req.run_id}' not found or already resolved.")

    persona = req.persona or stored.get("persona", "supervisor")
    intent  = stored.get("intent", "full")
    action  = req.action.upper()
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
    state = run_from_trusted_signal(trusted, intent=intent, run_id=req.run_id)
    formatted = format_for_persona(state, persona)

    risk      = state.get("risk_assessment")
    knowledge = state.get("knowledge_guidance")
    diagnosis = state.get("fault_diagnosis")

    # Propagate downstream HITL gates that may fire after DFA remediation
    hitl_advisory  = None
    hitl_diagnosis = None
    hitl_knowledge = None

    if (risk and getattr(risk, "assessment_source", "") == "rules+llm_fallback"
            and getattr(risk, "advisory_note", "")):
        hitl_advisory = {"advisory_note": risk.advisory_note, "run_id": req.run_id}

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

    _diag_paused = hitl_diagnosis is not None
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
        recommendation=None if _diag_paused else _to_dict(state.get("recommendation")),
        hitl_advisory=hitl_advisory if not _diag_paused else None,
        hitl_diagnosis=hitl_diagnosis,
        hitl_knowledge=hitl_knowledge,
    )


# ── Monitoring HITL: resolve borderline EWMA anomaly ─────────────────────────

@app.post("/api/pipeline/hitl/monitoring")
def hitl_monitoring_resolution(req: HITLMonitoringRequest):
    """Resolve Monitoring HITL gate: SUPPRESS or CONFIRM a borderline EWMA anomaly."""
    stored = _HITL_STORE.pop(req.run_id, None)
    if not stored:
        raise HTTPException(404, f"HITL session '{req.run_id}' not found or already resolved.")

    persona = req.persona or stored.get("persona", "supervisor")
    intent  = stored.get("intent", "full")
    trusted = stored["trusted"]
    anomaly = stored["anomaly_event"]
    action  = req.action.upper()

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
    state     = run_from_anomaly_event(anomaly, trusted, intent=intent, run_id=req.run_id)
    formatted = format_for_persona(state, persona)

    diagnosis = state.get("fault_diagnosis")
    risk      = state.get("risk_assessment")
    knowledge = state.get("knowledge_guidance")

    hitl_advisory  = None
    hitl_diagnosis = None
    hitl_knowledge = None

    if (risk and getattr(risk, "assessment_source", "") == "rules+llm_fallback"
            and getattr(risk, "advisory_note", "")):
        hitl_advisory = {"advisory_note": risk.advisory_note, "run_id": req.run_id}

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

    _diag_paused = hitl_diagnosis is not None
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
        recommendation=None if _diag_paused else _to_dict(state.get("recommendation")),
        hitl_advisory=hitl_advisory if not _diag_paused else None,
        hitl_diagnosis=hitl_diagnosis,
        hitl_knowledge=hitl_knowledge,
    )


# ── FI HITL: confirm or override low-confidence fault classification ──────────

@app.post("/api/pipeline/hitl/diagnosis")
def hitl_diagnosis_resolution(req: HITLDiagnosisRequest):
    """Resolve FI HITL gate: CONFIRM or MARK_UNDETERMINED a low-confidence fault."""
    stored = _HITL_STORE.pop(req.run_id, None)
    if not stored:
        raise HTTPException(404, f"HITL session '{req.run_id}' not found or already resolved.")

    persona           = req.persona or stored.get("persona", "supervisor")
    intent            = stored.get("intent", "full")
    trusted           = stored["trusted"]
    anomaly           = stored["anomaly_event"]
    original_diagnosis = stored["fault_diagnosis"]
    action            = req.action.upper()

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
    state     = run_from_diagnosis(diagnosis, anomaly, trusted, intent=intent, run_id=req.run_id)
    formatted = format_for_persona(state, persona)

    risk      = state.get("risk_assessment")
    knowledge = state.get("knowledge_guidance")

    hitl_advisory  = None
    hitl_knowledge = None

    if (risk and getattr(risk, "assessment_source", "") == "rules+llm_fallback"
            and getattr(risk, "advisory_note", "")):
        hitl_advisory = {"advisory_note": risk.advisory_note, "run_id": req.run_id}

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
        recommendation=_to_dict(state.get("recommendation")),
        hitl_advisory=hitl_advisory,
        hitl_knowledge=hitl_knowledge,
    )


# ── Knowledge HITL: resolve missing SOP ──────────────────────────────────────

@app.post("/api/pipeline/hitl/knowledge")
def hitl_knowledge_resolution(req: HITLKnowledgeRequest):
    """Resolve Knowledge HITL gate: FLAG_MANUAL or ACCEPT_EMPTY when no SOP found."""
    stored = _HITL_STORE.pop(req.run_id, None)
    if not stored:
        raise HTTPException(404, f"HITL session '{req.run_id}' not found or already resolved.")

    persona   = req.persona or stored.get("persona", "supervisor")
    diagnosis = stored.get("fault_diagnosis")
    risk      = stored.get("risk_assessment")
    trusted   = stored.get("trusted")
    action    = req.action.upper()

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


@app.post("/api/executor/run")
def executor_run(req: ExecutorRunRequest):
    """
    Execute an approved (or rejected) MaintenanceRecommendation.
    Pass approved=True to execute, approved=False to test the blocked path.
    """
    from src.schemas.recommendation import MaintenanceRecommendation
    from src.agents.executor_agent import ExecutorAgent
    try:
        rec = MaintenanceRecommendation(**req.recommendation)
        result = ExecutorAgent().process(rec, approved=req.approved)
        return result.model_dump()
    except Exception as exc:
        raise HTTPException(400, f"Executor error: {exc}")


class LearningFeedbackRequest(BaseModel):
    execution_result: Dict[str, Any]
    feedback: Dict[str, Any]


@app.post("/api/learning/feedback")
def record_learning_feedback(req: LearningFeedbackRequest):
    """Submit closed-loop feedback to the Learning & Memory Agent."""
    from src.schemas.execution import ExecutionResult
    from src.schemas.feedback import FeedbackEvent
    from src.agents.learning_memory_agent import LearningMemoryAgent
    try:
        execution = ExecutionResult(**req.execution_result)
        feedback = FeedbackEvent(**req.feedback)
        doc = LearningMemoryAgent().process(execution, feedback)
        return doc.model_dump()
    except Exception as exc:
        raise HTTPException(400, f"Learning feedback error: {exc}")


@app.get("/api/learning/cases")
def get_learned_cases():
    """Return all learned cases stored by the Learning & Memory Agent."""
    from src.tools.learned_case_repository import JSONLearnedCaseRepository
    from src.tools.config_loader import load_learning_config
    try:
        cfg = load_learning_config()
        repo = JSONLearnedCaseRepository(cfg.repository_path)
        rows = repo._rows()
        return {"count": len(rows), "cases": rows}
    except Exception:
        return {"count": 0, "cases": []}


@app.get("/api/learning/cases/{case_id}")
def get_learned_case(case_id: str):
    """Return a single learned case by ID."""
    from src.tools.learned_case_repository import JSONLearnedCaseRepository
    from src.tools.config_loader import load_learning_config
    try:
        cfg = load_learning_config()
        repo = JSONLearnedCaseRepository(cfg.repository_path)
        doc = repo.get(case_id)
        if doc is None:
            raise HTTPException(404, f"Case '{case_id}' not found")
        return doc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/notifications/counts")
def notification_counts():
    """Return unread notification count per persona_id."""
    from src.tools.notification_mock_service import get_unread_counts
    return get_unread_counts()


@app.get("/api/notifications/{persona_id}")
def get_persona_notifications(persona_id: str):
    """Return all notifications for a persona (newest first)."""
    from src.tools.notification_mock_service import get_notifications
    notifs = get_notifications(persona_id)
    return {"persona_id": persona_id, "notifications": notifs, "count": len(notifs)}


@app.post("/api/notifications/{persona_id}/read")
def mark_notifications_read(persona_id: str):
    """Mark all notifications for a persona as read."""
    from src.tools.notification_mock_service import mark_all_read
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
            {"name": "gearbox_fault",       "description": "Gearbox bearing fault"},
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
        return {"assets": [a.model_dump() if hasattr(a, "model_dump") else a for a in assets]}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str):
    """Return a single asset by ID."""
    try:
        assets = load_asset_master()
        for a in assets:
            aid = a.asset_id if hasattr(a, "asset_id") else a.get("asset_id", "")
            if aid == asset_id:
                return a.model_dump() if hasattr(a, "model_dump") else a
        raise HTTPException(404, f"Asset '{asset_id}' not found.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Chat endpoint ─────────────────────────────────────────────────────────────

# Pre-baked canned responses keyed by keyword patterns
_CANNED = {
    "outer race": "Outer race spall Stage 3 confirmed. BPFO 4.02× baseline. RUL 5–8 days. Replacement mandatory at Stage 3.",
    "rul":        "RUL estimate: 5–8 days (median 6.4d). P10: 4.1d, P90: 9.3d. 82% confidence.",
    "cost":       "Planned intervention: $18K. Emergency failure: $619K+. Net avoidance: $601K (33× ROI).",
    "loto":       "LOTO EL-104-A is current (Nov 2023, valid Nov 2024). Covers MCB-104A, MCB-104B, IL-104-M.",
    "parts":      "SKF 6310-2RS confirmed at Bin A-14 (Qty 3). Mobil SHC 100 in stock. All tools available.",
    "sop":        "SOP M-104-REP-04 Rev 4.1 applies. Torque: 85 Nm. Lubricant: Mobil SHC 100 (80–100 g).",
    "risk":       "Risk level: CRITICAL. Failure probability by Saturday: 67%. Act before Wednesday.",
    "approve":    "WO-2024-1847 approved. SAP PM: RELEASED. Crew notified. Parts reserved at Bin A-14.",
    "safe":       "ISO 10816-3 Zone D. Mandatory LOTO before any approach. Class B hearing + vibration gloves.",
}


@app.post("/api/chat")
def chat(req: ChatRequest):
    """Persona-aware chat — QueryRouter + pipeline context + ReflexionAgent validation."""
    run_id = f"CHAT-{uuid.uuid4().hex[:10].upper()}"
    context = req.context or {}
    if not req.message or not req.message.strip():
        raise HTTPException(422, "Chat message must not be empty.")
    if len(req.message) > _ORCH_CONFIG["chat"]["max_message_characters"]:
        raise HTTPException(422, "Chat message exceeds the configured length limit.")

    signal = context.get("signal")
    scenario = context.get("scenario")
    if scenario and signal is None:
        try:
            signal = _get_demo_signal(str(scenario), int(context.get("row_index", -1)))
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(404, f"Scenario unavailable: {scenario}") from exc

    plan = plan_query(req.message, signal is not None)
    state: dict = {"pipeline_log": []}

    if plan.needs_signal and signal is None:
        draft = {"persona": req.persona,
                 "response": f"I can answer this {plan.intent} question once telemetry or a named scenario is supplied. No agent decision was fabricated.",
                 "call_plan": list(plan.agents), "needs_context": True}
    elif signal is not None:
        state = run_pipeline(deepcopy(signal), run_id=run_id, intent=plan.pipeline_intent,
            inventory_lookup=context.get("inventory_lookup"),
            context_lookup=context.get("operations_context"),
            approval_status="pending")
        formatted = format_for_persona(state, req.persona)
        rec = state.get("recommendation")
        exec_result = state.get("execution_result")
        draft = {"persona": req.persona,
                 "response": formatted.get("headline", "Analysis complete."),
                 "details": formatted.get("details", []),
                 "actions": formatted.get("actions", []),
                 "call_plan": list(plan.agents),
                 "needs_context": False,
                 "agent_outputs": {
                     "recommendation": rec.to_dict() if rec and hasattr(rec, "to_dict") else (rec.model_dump() if rec else None),
                     "execution_result": exec_result.to_dict() if exec_result and hasattr(exec_result, "to_dict") else None,
                 }}
    else:
        # Canned response fallback
        msg_lower = req.message.lower()
        canned_response = next((v for k, v in _CANNED.items() if k in msg_lower), None)
        if canned_response:
            draft = {"persona": req.persona, "response": canned_response,
                     "sources": ["Knowledge Agent"], "call_plan": list(plan.agents), "needs_context": False}
        else:
            draft = {"persona": req.persona,
                     "response": "This is an open-ended reliability question. I can explain the workflow, but an asset-specific decision requires telemetry or a named scenario.",
                     "call_plan": list(plan.agents), "needs_context": True}

    reflected = _REFLEXION.process(draft, state, plan)
    result = {**reflected.response, "run_id": run_id, "intent": plan.intent,
              "reflection_status": reflected.status, "pipeline_log": state.get("pipeline_log", [])}
    try:
        result["audit"] = write_audit(_AUDIT_PATH, event="chat", run_id=run_id,
                                      status="ok", intent=plan.intent, agents=plan.agents)
    except Exception:
        result["audit"] = {"status": "audit_write_failed"}
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
