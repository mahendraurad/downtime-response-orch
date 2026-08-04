"""
schemas/execution.py  —  Phase 9 (Executor Agent output)
"""
from __future__ import annotations

from typing import Any, Dict, List
from pydantic import BaseModel, Field


class ExecutionStep(BaseModel):
    step_name: str
    status: str
    detail: str = ""
    owner: str = ""
    deadline: str = ""
    escalates_to: str = ""
    escalation_rule: str = ""


class ExecutionResult(BaseModel):
    """Executor Agent output."""
    case_id:            str                  = ""
    action_taken:       str                  = ""   # the recommended_action that was executed
    status:             str                  = ""   # success | blocked | partial | failed
    blocked_reason:     str                  = ""   # non-empty only when status == blocked
    work_order_id:      str                  = ""
    work_order_details: Dict[str, Any]       = Field(default_factory=dict)
    reservation_ids:    List[str]            = Field(default_factory=list)
    parts_status:       List[Dict[str, Any]] = Field(default_factory=list)
    notification_status: str                 = ""   # sent | skipped | failed
    audit_reference:    str                  = ""
    executed_at:        str                  = ""
    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    execution_eligible: bool                 = True
    duplicate_detected: bool                 = False
    persistence_status: str                  = "not_requested"
    schema_version:     str                  = "1.1"
    executor_config_version: str             = ""
    source_recommendation_schema_version: str = ""
    source_prescriptive_config_version: str  = ""
    linked_recommendation_case_id: str       = ""
    execution_steps:    List[ExecutionStep]  = Field(default_factory=list)
    completed_steps:    int                  = 0
    total_steps:        int                  = 0
    execution_policy_version: str            = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
    # ***********************
