"""
orchestrator/graph.py  —  Phase 8

Wires the four live agents (DFA → Monitoring → Failure Intelligence →
Predictive Risk → Knowledge) into a LangGraph StateGraph.

The graph is compiled once at import time.  Call run_pipeline(raw_signal)
from the API layer.

Phases 7/9/10 (Prescriptive Optimisation, Executor, Learning & Memory) are
stubbed as pass-through nodes; other developers implement them.
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
from langsmith import traceable

from src.orchestrator.state import DROGraphState
from src.orchestrator.routing import (
    route_after_foundation,
    route_after_monitoring,
    route_after_diagnosis,
    route_after_risk,
    route_after_knowledge,
    route_after_prescriptive,
    route_after_executor,
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
    return dfa, mon, fia, pra, ka


def _load_taxonomy():
    from src.tools.data_loader import load_fault_taxonomy
    return load_fault_taxonomy()


# Lazy initialisation — agents built on first graph invocation
_agents = None
_action_agents = None


def _get_agents():
    global _agents
    if _agents is None:
        _agents = _build_agents()
    return _agents


# ************** Added by Prateek Mittal on 20th July 2026 ******************
def _get_action_agents():
    global _action_agents
    if _action_agents is None:
        from src.agents.prescriptive_optimization_agent import PrescriptiveOptimizationAgent
        from src.agents.executor_agent import ExecutorAgent
        from src.agents.learning_memory_agent import LearningMemoryAgent
        from src.tools.llm_client import LLMClient
        llm = LLMClient()
        optional_llm = llm if llm.is_configured() else None
        learning = LearningMemoryAgent(llm_client=optional_llm)
        _action_agents = (
            PrescriptiveOptimizationAgent(
                llm_client=optional_llm,
                historical_case_fn=learning.matching_cases,
            ),
            ExecutorAgent(),
            learning,
        )
    return _action_agents
# ***********************


# ── Node functions ────────────────────────────────────────────────────────────

def _timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, round((time.monotonic() - t0) * 1000)


def _sources_data_foundation(trusted) -> str:
    parts = []
    tag = None
    try:
        tag = trusted.bearing_ctx.historian_tag
    except AttributeError:
        pass
    if tag:
        parts.append(tag)
    try:
        sq = trusted.raw.signal_quality_score
        if sq is not None:
            parts.append(f"quality {sq:.0%}")
    except AttributeError:
        pass
    return " · ".join(parts) or "historian signal"


def _sources_monitoring(trusted) -> str:
    channels = []
    try:
        raw = trusted.raw
        if getattr(raw, "vib_rms_mm_s", None) is not None:
            channels.append("vibration")
        if getattr(raw, "temp_c", None) is not None:
            channels.append("temperature")
        if getattr(raw, "motor_current_a", None) is not None:
            channels.append("current")
    except AttributeError:
        pass
    return " · ".join(channels) or "sensor channels"


def _sources_failure_intelligence(diagnosis) -> str:
    parts = ["FFT analysis", "bearing DB"]
    try:
        conf = diagnosis.confidence
        if conf is not None:
            parts.append(f"{conf:.0%} conf.")
    except AttributeError:
        pass
    return " · ".join(parts)


def _sources_predictive_risk(risk) -> str:
    parts = []
    try:
        lo, hi = risk.rul_min_days, risk.rul_max_days
        if lo is not None and hi is not None:
            parts.append(f"RUL {lo}–{hi}d")
        src = getattr(risk, "assessment_source", None)
        if src:
            parts.append(src)
    except AttributeError:
        pass
    return " · ".join(parts) or "RUL model · cohort DB"


def _sources_knowledge(guidance) -> str:
    try:
        details = getattr(guidance, "source_details", None) or []
        titles = [d.title for d in details[:2] if getattr(d, "title", None)]
        if titles:
            return " · ".join(titles)
        docs = getattr(guidance, "source_documents", None) or []
        if docs:
            return f"{len(docs)} SOP doc{'s' if len(docs) != 1 else ''}"
        hit = getattr(guidance, "retrieval_hit_count", None)
        if hit:
            return f"{hit} sections matched"
    except AttributeError:
        pass
    return "retrieval returned no cited source"


def _sources_prescriptive(state, recommendation) -> str:
    sources = []
    support = getattr(recommendation, "decision_support", None)
    if support and support.cost_data_status == "configured_demo":
        sources.append("configured demo cost model")
    if state.get("inventory_lookup"):
        sources.append("supplied inventory lookup")
    if (state.get("context_lookup") or {}).get("planned_stop_windows"):
        sources.append("supplied maintenance windows")
    return " · ".join(sources) or "configured prescriptive rules"


def _sources_executor(result) -> str:
    sources = ["mock CMMS"] if result.work_order_id else []
    if result.parts_status:
        sources.append("mock parts inventory")
    if result.notification_status == "sent":
        sources.append("mock notification service")
    return " · ".join(sources) or "execution guard only"


@traceable(name="Agent 1 - Data Foundation", run_type="chain", tags=["dro", "agent-1"])
def node_data_foundation(state: DROGraphState) -> DROGraphState:
    dfa, *_ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        trusted, ms = _timed(
            dfa.process, deepcopy(state["raw_signal"]),
            persona_context=state.get("persona_context"),
        )
        log.append({"node": "data_foundation", "status": "ok", "latency_ms": ms,
                    "data_sources": _sources_data_foundation(trusted)})
        return {**state, "trusted_signal": trusted, "pipeline_log": log}
    except Exception as exc:
        logger.error("data_foundation failed: %s", exc)
        log.append({"node": "data_foundation", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


@traceable(name="Agent 2 - Monitoring", run_type="chain", tags=["dro", "agent-2"])
def node_monitoring(state: DROGraphState) -> DROGraphState:
    _, mon, *_ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        anomaly, ms = _timed(
            mon.process, state["trusted_signal"],
            persona_context=state.get("persona_context"),
        )
        log.append({"node": "monitoring", "status": "ok", "latency_ms": ms,
                    "triggered": anomaly is not None,
                    "data_sources": _sources_monitoring(state["trusted_signal"])})
        return {**state, "anomaly_event": anomaly, "pipeline_log": log}
    except Exception as exc:
        logger.error("monitoring failed: %s", exc)
        log.append({"node": "monitoring", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


@traceable(name="Agent 3 - Failure Intelligence", run_type="chain", tags=["dro", "agent-3"])
def node_failure_intelligence(state: DROGraphState) -> DROGraphState:
    _, _, fia, *_ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        diagnosis, ms = _timed(
            fia.process, state["anomaly_event"], state["trusted_signal"],
            persona_context=state.get("persona_context"),
        )
        log.append({"node": "failure_intelligence", "status": "ok", "latency_ms": ms,
                    "data_sources": _sources_failure_intelligence(diagnosis)})
        return {**state, "fault_diagnosis": diagnosis, "pipeline_log": log}
    except Exception as exc:
        logger.error("failure_intelligence failed: %s", exc)
        log.append({"node": "failure_intelligence", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


@traceable(name="Agent 4 - Predictive Risk", run_type="chain", tags=["dro", "agent-4", "llm-optional"])
def node_predictive_risk(state: DROGraphState) -> DROGraphState:
    _, _, _, pra, _ = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        risk, ms = _timed(
            pra.process, state["fault_diagnosis"],
            state["anomaly_event"], state["trusted_signal"],
            persona_context=state.get("persona_context"),
        )
        log.append({"node": "predictive_risk", "status": "ok", "latency_ms": ms,
                    "data_sources": _sources_predictive_risk(risk)})
        return {**state, "risk_assessment": risk, "pipeline_log": log}
    except Exception as exc:
        logger.error("predictive_risk failed: %s", exc)
        log.append({"node": "predictive_risk", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


@traceable(name="Agent 5 - Knowledge", run_type="retriever", tags=["dro", "agent-5", "rag"])
def node_knowledge(state: DROGraphState) -> DROGraphState:
    *_, ka = _get_agents()
    log = list(state.get("pipeline_log") or [])
    try:
        guidance, ms = _timed(
            ka.process, state["fault_diagnosis"], state["trusted_signal"],
            state.get("risk_assessment"),
            persona_context=state.get("persona_context"),
        )
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms,
                    "data_sources": _sources_knowledge(guidance)})
        return {**state, "knowledge_guidance": guidance, "pipeline_log": log}
    except Exception as exc:
        logger.error("knowledge failed: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        return {**state, "error": str(exc), "pipeline_log": log}


# ************** Added by Prateek Mittal on 20th July 2026 ******************
@traceable(name="Agent 6 - Prescriptive Optimization", run_type="chain", tags=["dro", "agent-6", "llm-optional"])
def node_prescriptive(state):
    agent, _, _ = _get_action_agents(); log = list(state.get("pipeline_log") or [])
    try:
        result, ms = _timed(
            agent.process, state["risk_assessment"], state["fault_diagnosis"],
            state["knowledge_guidance"], state.get("inventory_lookup") or {},
            state.get("context_lookup") or {},
            persona_context=state.get("persona_context"),
            trusted_signal=state.get("trusted_signal"),
        )
        log.append({"node":"prescriptive","status":"ok","latency_ms":ms,
                    "data_sources":_sources_prescriptive(state, result)})
        return {**state,"recommendation":result,"pipeline_log":log}
    except Exception as exc:
        log.append({"node":"prescriptive","status":"error","latency_ms":0})
        return {**state,"error":str(exc),"pipeline_log":log}

@traceable(name="Agent 7 - Executor", run_type="tool", tags=["dro", "agent-7"])
def node_executor(state):
    _, agent, _ = _get_action_agents(); log = list(state.get("pipeline_log") or [])
    result, ms = _timed(
        agent.process, state["recommendation"],
        state.get("approval_status") == "approved",
        persona_context=state.get("persona_context"),
    )
    log.append({"node":"executor","status":result.status,"latency_ms":ms,
                "data_sources":_sources_executor(result)})
    return {**state,"execution_result":result,"pipeline_log":log}

@traceable(name="Agent 8 - Learning and Memory", run_type="chain", tags=["dro", "agent-8", "llm-optional"])
def node_learning(state):
    _, _, agent = _get_action_agents(); log = list(state.get("pipeline_log") or [])
    result, ms = _timed(
        agent.process, state["execution_result"], state["feedback_event"],
        persona_context=state.get("persona_context"),
    )
    log.append({"node":"learning","status":result.learning_status,"latency_ms":ms})
    return {**state,"learned_case":result,"pipeline_log":log}
# ***********************


# ── Build and compile graph ───────────────────────────────────────────────────

def _build_graph(checkpointer=None):
    g = StateGraph(DROGraphState)

    g.add_node("data_foundation",      node_data_foundation)
    g.add_node("monitoring",           node_monitoring)
    g.add_node("failure_intelligence", node_failure_intelligence)
    g.add_node("predictive_risk",      node_predictive_risk)
    g.add_node("knowledge",            node_knowledge)
    g.add_node("prescriptive",         node_prescriptive)
    g.add_node("executor",             node_executor)
    g.add_node("learning",             node_learning)

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
    g.add_conditional_edges("prescriptive", route_after_prescriptive, {"executor":"executor", END:END})
    g.add_conditional_edges("executor", route_after_executor, {"learning":"learning", END:END})
    g.add_edge("learning", END)

    return g.compile(checkpointer=checkpointer)


from src.tools.checkpointing import build_checkpoint_resources
_checkpoint_resources = build_checkpoint_resources()
_graph = _build_graph(checkpointer=_checkpoint_resources.saver)


# ── Public entry point ────────────────────────────────────────────────────────

def run_from_trusted_signal(trusted_signal, intent: str = "full",
                             run_id: str = "", persona="supervisor") -> DROGraphState:
    """
    Skip DFA; run monitoring→FI→risk→knowledge on a pre-validated/remediated signal.
    Called by the HITL resolution endpoint after an IMPUTE or KEEP decision.
    """
    import uuid
    rid = run_id or str(uuid.uuid4())[:8]
    _, mon, fia, pra, ka = _get_agents()
    from src.tools.persona_formatter import build_persona_context
    persona_context = build_persona_context(persona)

    log: list = [{"node": "data_foundation", "status": "ok (remediated)", "latency_ms": 0}]
    state: DROGraphState = {
        "run_id": rid, "case_id": rid, "intent": intent,
        "trusted_signal": trusted_signal, "pipeline_log": log,
    }

    try:
        anomaly, ms = _timed(mon.process, trusted_signal, persona_context=persona_context)
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
        diag, ms = _timed(fia.process, anomaly, trusted_signal, persona_context=persona_context)
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
        risk, ms = _timed(pra.process, diag, anomaly, trusted_signal, persona_context=persona_context)
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
        guidance, ms = _timed(ka.process, diag, trusted_signal, risk, persona_context=persona_context)
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms})
        state["knowledge_guidance"] = guidance
    except Exception as exc:
        logger.error("knowledge failed in HITL resume: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)

    return state


def run_from_anomaly_event(anomaly_event, trusted_signal, intent: str = "full",
                           run_id: str = "", persona="supervisor") -> DROGraphState:
    """
    Skip DFA + Monitoring; run FI → Risk → Knowledge on an operator-confirmed anomaly.
    Called by the Monitoring HITL resolution endpoint after a CONFIRM decision.
    """
    import uuid as _uuid
    rid = run_id or str(_uuid.uuid4())[:8]
    _, _, fia, pra, ka = _get_agents()
    from src.tools.persona_formatter import build_persona_context
    persona_context = build_persona_context(persona)

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
        diag, ms = _timed(fia.process, anomaly_event, trusted_signal, persona_context=persona_context)
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
        risk, ms = _timed(pra.process, diag, anomaly_event, trusted_signal, persona_context=persona_context)
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
        guidance, ms = _timed(ka.process, diag, trusted_signal, risk, persona_context=persona_context)
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms})
        state["knowledge_guidance"] = guidance
    except Exception as exc:
        logger.error("knowledge failed in monitoring HITL resume: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)

    return state


def run_from_diagnosis(diagnosis, anomaly_event, trusted_signal,
                       intent: str = "full", run_id: str = "",
                       persona="supervisor") -> DROGraphState:
    """
    Skip DFA + Monitoring + FI; run Risk → Knowledge on an operator-confirmed diagnosis.
    Called by the FI HITL resolution endpoint after a CONFIRM or MARK_UNDETERMINED decision.
    """
    import uuid as _uuid
    rid = run_id or str(_uuid.uuid4())[:8]
    _, _, _, pra, ka = _get_agents()
    from src.tools.persona_formatter import build_persona_context
    persona_context = build_persona_context(persona)

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
        risk, ms = _timed(pra.process, diagnosis, anomaly_event, trusted_signal, persona_context=persona_context)
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
        guidance, ms = _timed(ka.process, diagnosis, trusted_signal, risk, persona_context=persona_context)
        log.append({"node": "knowledge", "status": "ok", "latency_ms": ms})
        state["knowledge_guidance"] = guidance
    except Exception as exc:
        logger.error("knowledge failed in diagnosis HITL resume: %s", exc)
        log.append({"node": "knowledge", "status": "error", "latency_ms": 0})
        state["error"] = str(exc)

    return state


@traceable(name="DRO Agent Orchestrator", run_type="chain", tags=["dro", "orchestrator"])
def run_pipeline(raw_signal: Dict[str, Any], run_id: str = "",
                 intent: str = "full", inventory_lookup: dict = None,
                 context_lookup: dict = None, approval_status: str = "pending",
                 feedback_event=None, persona="supervisor") -> DROGraphState:
    """
    Run the DRO pipeline for a single signal reading.

    intent controls how far the pipeline runs:
      status    → stop after Data Foundation (current readings only)
      anomaly   → stop after Monitoring (anomaly yes/no)
      diagnosis → stop after Failure Intelligence (fault identified)
      risk      → stop after Predictive Risk (RUL / risk level)
      full      → run all agents through Knowledge (default)
    """
    import uuid
    rid = run_id or str(uuid.uuid4())[:8]
    from src.tools.persona_formatter import build_persona_context
    initial: DROGraphState = {
        "run_id": rid,
        "case_id": rid,
        "intent": intent,
        "raw_signal": raw_signal,
        "inventory_lookup": inventory_lookup or {}, "context_lookup": context_lookup or {},
        "approval_status": approval_status, "feedback_event": feedback_event,
        "pipeline_log": [],
        "persona_context": build_persona_context(persona),
    }
    invoke_config = {"configurable": {"thread_id": rid}}
    final_state = _graph.invoke(initial, config=invoke_config)
    return final_state
