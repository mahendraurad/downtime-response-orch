"""
orchestrator/state.py  —  Phase 8

LangGraph graph state that flows through every node.
Each agent reads its inputs from state and writes its output back.
"""
from __future__ import annotations

from typing import TypedDict, Any, Optional


class DROGraphState(TypedDict, total=False):
    """
    Fields populated progressively as the pipeline runs.

    Phase 2: raw_signal, trusted_signal
    Phase 3: anomaly_event
    Phase 4: fault_diagnosis
    Phase 5: risk_assessment
    Phase 6: knowledge_guidance
    Phase 8: approval_status, run_id, case_id
    Phases 7/9/10: handled by other developers
    """
    run_id:             str
    case_id:            str
    intent:             str   # status | anomaly | diagnosis | risk | full (query-aware routing)
    raw_signal:         Any   # raw dict from historian / API caller
    trusted_signal:     Any   # TrustedBearingSignal | None
    anomaly_event:      Any   # AnomalyEvent | None
    fault_diagnosis:    Any   # FaultDiagnosis | None
    risk_assessment:    Any   # RiskAssessment | None
    knowledge_guidance: Any   # KnowledgeGuidance | None
    recommendation:     Any   # MaintenanceRecommendation | None  (Phase 7)
    approval_status:    str   # pending | approved | rejected
    execution_result:   Any   # ExecutionResult | None  (Phase 9)
    feedback_event:     Any
    learned_case:       Any
    inventory_lookup:   dict
    context_lookup:     dict
    error:              str   # non-empty if any agent raised
    pipeline_log:       list  # list of {"node", "status", "latency_ms"} entries
