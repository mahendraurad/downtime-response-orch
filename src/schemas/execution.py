"""
schemas/execution.py  —  Phase 9 (Executor Agent output)
"""
from __future__ import annotations

from typing import Any, Dict, List
from pydantic import BaseModel, Field


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
