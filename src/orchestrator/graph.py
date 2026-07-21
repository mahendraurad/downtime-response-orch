"""
orchestrator/graph.py  —  Phase 8

Wires the live agents (DFA → Monitoring → Failure Intelligence →
Predictive Risk → Knowledge → Prescriptive Optimisation) into a LangGraph StateGraph.

The graph is compiled once at import time.  Call run_pipeline(raw_signal)
from the API layer.

Phases 9/10 (Executor, Learning & Memory) are stubs pending implementation.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from copy import deepcopy
from typing import Any, Dict

from langgraph.graph import StateGraph, END

from src.orchestrator.state import DROGraphState
from src.orchestrator.routing import (
    route_after_foundation,
    route_after_monitoring,
    route_after_diagnosis,
    route_after_risk,
    route_after_knowledge,
)

logger = logging.getLogger(__name__)

# ── Build agent instances (singleton — shared across all graph runs) ──────────


def _build_agents():
    """Construct and return all agent instances."""
    from src.agents.data_foundation_agent import DataFoundationAgent
    from src.agents.monitoring_agent import MonitoringAgent
    from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
    from src.agents.predictive_risk_agent import PredictiveRiskAgent
    from src.agents.knowledge_agent import KnowledgeAgent
    from src.tools.config_loader import load_monitoring_config, load_risk_config

    dfa = DataFoundationAgent.from_data_files()

    mon_cfg = load_monitoring_config()
    mon_cfg.ewma_state_file = os.path.join(
        tempfile.gettempdir(), "_dro_api_ewma.json"
    )
    mon = MonitoringAgent(mon_cfg)

    fia = FailureIntelligenceAgent.from_data_files()

    # LLM fallback: read env vars at startup; disabled if not configured
    risk_cfg = load_risk_config()
    from src.tools.llm_client import LLMClient
    llm = LLMClient(timeout_seconds=risk_cfg.llm_timeout_seconds)
    pra = PredictiveRiskAgent(
        _load_taxonomy(), risk_cfg,
        llm_client=llm if llm.is_configured() else None,
    )

    ka = KnowledgeAgent()

    from src.agents.prescriptive_optimization_agent import PrescriptiveOptimizationAgent
    poa = PrescriptiveOptimizationAgent()

    return dfa, mon, fia, pra, ka, poa


def _load_taxonomy():
    from src.tools.data_loader import load_fault_taxonomy
    return load_fault_taxonomy()


# Lazy initialisation — agents built on first graph invocation
_agents = None


def _get_agents():
    global _agents
    if _agents is None:
        _agents = _build_agents()
    return _agents


# ── Node functions ────────────────────────────────────────────────────────────

def _timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, round((time.monotonic() - t0) * 1000)


def node_data_foundation(state: DROGraphState) -> DROGraphState:
    dfa, *_ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        trusted, ms = _timed(dfa.process, deepcopy(state["raw_signal"]))
        log.append({"node": "data_foundation", "status": "ok", "latency_ms": ms})
        return {**state, "trusted_signal": trusted, "pipeline_log": log}
    except Exception as exc:
        logger.error("data_foundation failed: %s", exc)
        log.append({"node": "data_foundation", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


def node_monitoring(state: DROGraphState) -> DROGraphState:
    _, mon, *_ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        anomaly, ms = _timed(mon.process, state["trusted_signal"])
        log.append({"node": "monitoring", "status": "ok", "latency_ms": ms,
                    "triggered": anomaly is not None})
        return {**state, "anomaly_event": anomaly, "pipeline_log": log}
    except Exception as exc:
        logger.error("monitoring failed: %s", exc)
        log.append({"node": "monitoring", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


def node_failure_intelligence(state: DROGraphState) -> DROGraphState:
    _, _, fia, *_ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        diagnosis, ms = _timed(fia.process, state["anomaly_event"], state["trusted_signal"])
        log.append({"node": "failure_intelligence", "status": "ok", "latency_ms": ms})
        return {**state, "fault_diagnosis": diagnosis, "pipeline_log": log}
    except Exception as exc:
        logger.error("failure_intelligence failed: %s", exc)
        log.append({"node": "failure_intelligence", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


def node_predictive_risk(state: DROGraphState) -> DROGraphState:
    _, _, _, pra, *_ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        risk, ms = _timed(
            pra.process, state["fault_diagnosis"],
            state["anomaly_event"], state["trusted_signal"]
        )
        log.append({"node": "predictive_risk", "status": "ok", "latency_ms": ms})
        return {**state, "risk_assessment": risk, "pipeline_log": log}
    except Exception as exc:
        logger.error("predictive_risk failed: %s", exc)
        log.append({"node": "predictive_risk", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


def node_knowledge(state: DROGraphState) -> DROGraphState:
    *_, ka, _ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        guidance, ms = _timed(
            ka.process, state["fault_diagnosis"], state["trusted_signal"]
        )
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms})
        return {**state, "knowledge_guidance": guidance, "pipeline_log": log}
    except Exception as exc:
        logger.error("knowledge failed: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


def node_prescriptive(state: DROGraphState) -> DROGraphState:
    *_, poa = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        rec, ms = _timed(
            poa.process,
            state["risk_assessment"],
            state["fault_diagnosis"],
            state["knowledge_guidance"],
        )
        log.append({"node": "prescriptive", "status": "ok", "latency_ms": ms,
                    "recommendation_status": rec.recommendation_status})
        return {**state, "recommendation": rec, "pipeline_log": log}
    except Exception as exc:
        logger.error("prescriptive failed: %s", exc)
        log.append({"node": "prescriptive", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


# ── Build and compile graph ───────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(DROGraphState)

    g.add_node("data_foundation",      node_data_foundation)
    g.add_node("monitoring",           node_monitoring)
    g.add_node("failure_intelligence", node_failure_intelligence)
    g.add_node("predictive_risk",      node_predictive_risk)
    g.add_node("knowledge",            node_knowledge)
    g.add_node("prescriptive",         node_prescriptive)

    g.set_entry_point("data_foundation")

    g.add_conditional_edges(
        "data_foundation", route_after_foundation,
        {"monitoring": "monitoring", END: END},
    )
    g.add_conditional_edges(
        "monitoring", route_after_monitoring,
        {"failure_intelligence": "failure_intelligence", END: END},
    )
    g.add_conditional_edges(
        "failure_intelligence", route_after_diagnosis,
        {"predictive_risk": "predictive_risk", END: END},
    )
    g.add_conditional_edges(
        "predictive_risk", route_after_risk,
        {"knowledge": "knowledge", END: END},
    )
    g.add_conditional_edges(
        "knowledge", route_after_knowledge,
        {"prescriptive": "prescriptive", END: END},
    )
    g.add_edge("prescriptive", END)

    return g.compile()


_graph = _build_graph()


# ── Public entry point ────────────────────────────────────────────────────────

def run_from_trusted_signal(trusted_signal, intent: str = "full",
                             run_id: str = "") -> DROGraphState:
    """
    Skip DFA; run monitoring→FI→risk→knowledge→prescriptive on a pre-validated signal.
    Called by the HITL resolution endpoint after an IMPUTE or KEEP decision.
    """
    import uuid
    rid = run_id or str(uuid.uuid4())[:8]
    _, mon, fia, pra, ka, poa = _get_agents()

    log: list = [{"node": "data_foundation", "status": "ok (remediated)", "latency_ms": 0}]
    state: DROGraphState = {
        "run_id": rid, "case_id": rid, "intent": intent,
        "trusted_signal": trusted_signal, "pipeline_log": log,
    }

    try:
        anomaly, ms = _timed(mon.process, trusted_signal)
        log.append({"node": "monitoring", "status": "ok", "latency_ms": ms,
                    "triggered": anomaly is not None})
        state["anomaly_event"] = anomaly
    except Exception as exc:
        logger.error("monitoring failed in HITL resume: %s", exc)
        log.append({"node": "monitoring", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    if anomaly is None or intent == "anomaly":
        return state

    try:
        diag, ms = _timed(fia.process, anomaly, trusted_signal)
        log.append({"node": "failure_intelligence", "status": "ok", "latency_ms": ms})
        state["fault_diagnosis"] = diag
    except Exception as exc:
        logger.error("failure_intelligence failed in HITL resume: %s", exc)
        log.append({"node": "failure_intelligence", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    if intent == "diagnosis":
        return state

    try:
        risk, ms = _timed(pra.process, diag, anomaly, trusted_signal)
        log.append({"node": "predictive_risk", "status": "ok", "latency_ms": ms})
        state["risk_assessment"] = risk
    except Exception as exc:
        logger.error("predictive_risk failed in HITL resume: %s", exc)
        log.append({"node": "predictive_risk", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    if intent == "risk":
        return state

    try:
        guidance, ms = _timed(ka.process, diag, trusted_signal)
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms})
        state["knowledge_guidance"] = guidance
    except Exception as exc:
        logger.error("knowledge failed in HITL resume: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    try:
        rec, ms = _timed(poa.process, risk, diag, guidance)
        log.append({"node": "prescriptive", "status": "ok", "latency_ms": ms})
        state["recommendation"] = rec
    except Exception as exc:
        logger.error("prescriptive failed in HITL resume: %s", exc)
        log.append({"node": "prescriptive", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)

    return state


def run_from_anomaly_event(anomaly_event, trusted_signal, intent: str = "full",
                           run_id: str = "") -> DROGraphState:
    """
    Skip DFA + Monitoring; run FI → Risk → Knowledge → Prescriptive on a confirmed anomaly.
    Called by the Monitoring HITL resolution endpoint after a CONFIRM decision.
    """
    import uuid as _uuid
    rid = run_id or str(_uuid.uuid4())[:8]
    _, _, fia, pra, ka, poa = _get_agents()

    log: list = [
        {"node": "data_foundation", "status": "ok (resumed)", "latency_ms": 0},
        {"node": "monitoring", "status": "confirmed by operator", "latency_ms": 0},
    ]
    state: DROGraphState = {
        "run_id": rid, "case_id": rid, "intent": intent,
        "trusted_signal": trusted_signal,
        "anomaly_event": anomaly_event,
        "pipeline_log": log,
    }

    try:
        diag, ms = _timed(fia.process, anomaly_event, trusted_signal)
        log.append({"node": "failure_intelligence", "status": "ok", "latency_ms": ms})
        state["fault_diagnosis"] = diag
    except Exception as exc:
        logger.error("failure_intelligence failed in monitoring HITL resume: %s", exc)
        log.append({"node": "failure_intelligence", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    if intent == "diagnosis":
        return state

    try:
        risk, ms = _timed(pra.process, diag, anomaly_event, trusted_signal)
        log.append({"node": "predictive_risk", "status": "ok", "latency_ms": ms})
        state["risk_assessment"] = risk
    except Exception as exc:
        logger.error("predictive_risk failed in monitoring HITL resume: %s", exc)
        log.append({"node": "predictive_risk", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    if intent == "risk":
        return state

    try:
        guidance, ms = _timed(ka.process, diag, trusted_signal)
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms})
        state["knowledge_guidance"] = guidance
    except Exception as exc:
        logger.error("knowledge failed in monitoring HITL resume: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    try:
        rec, ms = _timed(poa.process, risk, diag, guidance)
        log.append({"node": "prescriptive", "status": "ok", "latency_ms": ms})
        state["recommendation"] = rec
    except Exception as exc:
        logger.error("prescriptive failed in monitoring HITL resume: %s", exc)
        log.append({"node": "prescriptive", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)

    return state


def run_from_diagnosis(diagnosis, anomaly_event, trusted_signal,
                       intent: str = "full", run_id: str = "") -> DROGraphState:
    """
    Skip DFA + Monitoring + FI; run Risk → Knowledge → Prescriptive on a confirmed diagnosis.
    Called by the FI HITL resolution endpoint after a CONFIRM or MARK_UNDETERMINED decision.
    """
    import uuid as _uuid
    rid = run_id or str(_uuid.uuid4())[:8]
    _, _, _, pra, ka, poa = _get_agents()

    log: list = [
        {"node": "data_foundation", "status": "ok (resumed)", "latency_ms": 0},
        {"node": "monitoring", "status": "ok (resumed)", "latency_ms": 0},
        {"node": "failure_intelligence", "status": "confirmed by operator", "latency_ms": 0},
    ]
    state: DROGraphState = {
        "run_id": rid, "case_id": rid, "intent": intent,
        "trusted_signal": trusted_signal,
        "anomaly_event": anomaly_event,
        "fault_diagnosis": diagnosis,
        "pipeline_log": log,
    }

    if intent == "diagnosis":
        return state

    try:
        risk, ms = _timed(pra.process, diagnosis, anomaly_event, trusted_signal)
        log.append({"node": "predictive_risk", "status": "ok", "latency_ms": ms})
        state["risk_assessment"] = risk
    except Exception as exc:
        logger.error("predictive_risk failed in diagnosis HITL resume: %s", exc)
        log.append({"node": "predictive_risk", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    if intent == "risk":
        return state

    try:
        guidance, ms = _timed(ka.process, diagnosis, trusted_signal)
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms})
        state["knowledge_guidance"] = guidance
    except Exception as exc:
        logger.error("knowledge failed in diagnosis HITL resume: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)
        return state

    try:
        rec, ms = _timed(poa.process, risk, diagnosis, guidance)
        log.append({"node": "prescriptive", "status": "ok", "latency_ms": ms})
        state["recommendation"] = rec
    except Exception as exc:
        logger.error("prescriptive failed in diagnosis HITL resume: %s", exc)
        log.append({"node": "prescriptive", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)

    return state


def run_pipeline(raw_signal: Dict[str, Any], run_id: str = "",
                 intent: str = "full") -> DROGraphState:
    """
    Run the DRO pipeline for a single signal reading.

    intent controls how far the pipeline runs:
      status    → stop after Data Foundation (current readings only)
      anomaly   → stop after Monitoring (anomaly yes/no)
      diagnosis → stop after Failure Intelligence (fault identified)
      risk      → stop after Predictive Risk (RUL / risk level)
      full      → run all agents through Prescriptive Optimisation (default)
    """
    import uuid
    rid = run_id or str(uuid.uuid4())[:8]
    initial: DROGraphState = {
        "run_id": rid,
        "case_id": rid,
        "intent": intent,
        "raw_signal": raw_signal,
        "pipeline_log": [],
    }
    final_state = _graph.invoke(initial)
    return final_state
