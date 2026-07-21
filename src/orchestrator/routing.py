"""
orchestrator/routing.py  —  Phase 8

Routing functions for LangGraph conditional edges.
Each function receives the current graph state and returns the name of the
next node to execute (or END to terminate the graph).
"""
from __future__ import annotations

from langgraph.graph import END


def _intent(state: dict) -> str:
    """Return the pipeline intent; default to 'full' if not set."""
    return state.get("intent") or "full"


def route_after_foundation(state: dict) -> str:
    """
    After Data Foundation Agent:
      REJECTED     → END  (unusable signal)
      intent=status → END  (caller only needed current readings)
      else         → "monitoring"
    """
    trusted = state.get("trusted_signal")
    if trusted is None:
        return END
    status = getattr(trusted, "validation_status", None)
    if status is not None:
        name = status.name if hasattr(status, "name") else str(status)
        if name == "REJECTED":
            return END
    if _intent(state) == "status":
        return END
    return "monitoring"


def route_after_monitoring(state: dict) -> str:
    """
    After Monitoring Agent:
      no anomaly    → END  (healthy — nothing to diagnose)
      intent=anomaly → END  (caller only needed anomaly yes/no)
      else          → "failure_intelligence"
    """
    if state.get("anomaly_event") is None:
        return END
    if _intent(state) == "anomaly":
        return END
    return "failure_intelligence"


def route_after_diagnosis(state: dict) -> str:
    """
    After Failure Intelligence Agent:
      intent=diagnosis → END  (caller only needed fault ID)
      else             → "predictive_risk"
    """
    if _intent(state) == "diagnosis":
        return END
    return "predictive_risk"


def route_after_risk(state: dict) -> str:
    """
    After Predictive Risk Agent:
      intent=risk → END  (caller only needed RUL / risk level)
      else        → "knowledge"
    """
    if _intent(state) == "risk":
        return END
    return "knowledge"


def route_after_knowledge(state: dict) -> str:
    """After Knowledge Agent → prescriptive (Phase 7)."""
    return "prescriptive"
