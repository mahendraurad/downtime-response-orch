"""
agents/executor_agent.py  —  Phase 9

Executor Agent — turns an approved MaintenanceRecommendation into concrete actions.

Question it answers:
  "What work orders, part reservations, and notifications need to be raised?"

Entry point:
  ExecutorAgent.process(recommendation, approved) -> ExecutionResult

Pipeline:
  1. Guard: return 'blocked' if recommendation is not approved.
             Approved when: approved=True (from LangGraph state) OR
             recommendation.approval_status == 'approved'.
  2. urgency=='monitor' or action.name=='continue_monitoring' → log-only;
     skip CMMS and inventory.
  3. All other actions → create work order in mock CMMS.
     Priority is derived from urgency: immediate→P1, urgent→P2, planned→P3, monitor→P4.
  4. Reserve each part in required_parts using its actual part_number and quantity.
     Collect shortage alerts without aborting the rest of execution.
  5. Emit notification log (responsible_person + contributors;
     supervisor escalation for immediate/urgent actions).
  6. Write structured audit log entry.
  7. Return ExecutionResult with overall status:
       success  — WO created, all parts reserved, notification sent
       partial  — WO created but ≥1 part is on shortage
       blocked  — approval gate not cleared
       failed   — CMMS call raised an exception

Real CMMS/ERP connectors replace the mock tools in Phase 11.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List

from src.schemas.recommendation import MaintenanceRecommendation
from src.schemas.execution import ExecutionResult
from src.tools.cmms_mock_service import create_work_order
from src.tools.inventory_mock_service import reserve_part

logger = logging.getLogger(__name__)

# urgency → CMMS priority label
_URGENCY_PRIORITY = {
    "immediate": "P1-Critical",
    "urgent":    "P2-High",
    "planned":   "P3-Medium",
    "monitor":   "P4-Low",
}

# Urgency levels that escalate notification to include supervisor
_ESCALATE_URGENCIES = {"immediate", "urgent"}


def _is_approved(recommendation: MaintenanceRecommendation, approved: bool) -> bool:
    """True when either the caller grants approval or the recommendation is pre-approved."""
    return approved or recommendation.approval_status == "approved"


def _is_log_only(recommendation: MaintenanceRecommendation) -> bool:
    """True for monitoring-only recommendations that need no CMMS work order."""
    return (
        recommendation.urgency == "monitor"
        or recommendation.recommended_action.name == "continue_monitoring"
    )


class ExecutorAgent:
    """
    Stateless executor. All I/O goes through the injected tool functions so
    Phase 11 can swap mock_cmms / mock_inventory for real connectors by
    changing only the tool layer — this class stays unchanged.
    """

    def process(self, recommendation: MaintenanceRecommendation,
                approved: bool = False) -> ExecutionResult:
        """
        Execute an approved recommendation. Never raises — all failures are
        captured in ExecutionResult.status and logged.

        Args:
            recommendation: output from Prescriptive Optimization Agent (Phase 7).
            approved: True when LangGraph state.approval_status == 'approved'.
                      Also proceeds when recommendation.approval_status == 'approved'.
        """
        executed_at = datetime.now(tz=timezone.utc).isoformat()
        audit_ref   = f"AUDIT-{uuid.uuid4().hex[:10].upper()}"
        action_name = recommendation.recommended_action.name

        # ── Step 1: approval guard ──────────────────────────────────────
        if not _is_approved(recommendation, approved):
            logger.warning(
                "[executor] BLOCKED case_id=%s action=%s approval_status=%s",
                recommendation.case_id, action_name, recommendation.approval_status,
            )
            return ExecutionResult(
                case_id=recommendation.case_id,
                action_taken=action_name,
                status="blocked",
                blocked_reason=(
                    f"approval_status='{recommendation.approval_status}' "
                    "and approved=False — awaiting sign-off"
                ),
                notification_status="skipped",
                audit_reference=audit_ref,
                executed_at=executed_at,
            )

        # ── Step 2: log-only actions (monitor / continue_monitoring) ────
        if _is_log_only(recommendation):
            notif = self._notify(recommendation, work_order_id="")
            logger.info(
                "[executor] case_id=%s action=%s urgency=%s — log-only, no WO raised",
                recommendation.case_id, action_name, recommendation.urgency,
            )
            logger.info(
                "[executor] audit=%s case=%s action=%s wo=None parts=[] notif=%s",
                audit_ref, recommendation.case_id, action_name, notif,
            )
            return ExecutionResult(
                case_id=recommendation.case_id,
                action_taken=action_name,
                status="success",
                notification_status=notif,
                audit_reference=audit_ref,
                executed_at=executed_at,
            )

        # ── Step 3: create work order ───────────────────────────────────
        priority    = _URGENCY_PRIORITY.get(recommendation.urgency, "P3-Medium")
        description = (
            f"{recommendation.recommended_action.description or action_name} — "
            f"{recommendation.rationale[:200]}"
        ).strip(" —")

        try:
            wo = create_work_order(
                asset_id=recommendation.asset_id,
                fault_mode=action_name,
                priority=priority,
                description=description,
            )
            work_order_id = wo["work_order_id"]
            logger.info(
                "[executor] WO created: %s asset=%s urgency=%s priority=%s",
                work_order_id, recommendation.asset_id,
                recommendation.urgency, priority,
            )
        except Exception as exc:
            logger.error("[executor] CMMS failure case_id=%s: %s",
                         recommendation.case_id, exc)
            return ExecutionResult(
                case_id=recommendation.case_id,
                action_taken=action_name,
                status="failed",
                blocked_reason=f"CMMS error: {exc}",
                notification_status="skipped",
                audit_reference=audit_ref,
                executed_at=executed_at,
            )

        # ── Step 4: reserve required parts ──────────────────────────────
        reservation_ids: List[str] = []
        parts_status    = []
        has_shortage    = False

        for part in recommendation.required_parts:
            try:
                result = reserve_part(
                    part_number=part.part_number,
                    quantity=part.quantity,
                    work_order_id=work_order_id,
                )
                parts_status.append(result)
                if result["status"] == "reserved":
                    reservation_ids.append(result["reservation_id"])
                else:
                    has_shortage = True
                    logger.warning(
                        "[executor] part shortage: %s (qty %d) for WO %s "
                        "— lead_time %d days, trigger procurement",
                        part.part_number, part.quantity,
                        work_order_id, part.lead_time_days,
                    )
            except Exception as exc:
                logger.error("[executor] inventory error part=%s: %s",
                             part.part_number, exc)
                parts_status.append({
                    "part_number": part.part_number,
                    "status":      "error",
                    "detail":      str(exc),
                })
                has_shortage = True

        # ── Step 5: notification ────────────────────────────────────────
        notif = self._notify(recommendation, work_order_id=work_order_id)

        # ── Step 6: audit log ───────────────────────────────────────────
        logger.info(
            "[executor] audit=%s case=%s action=%s urgency=%s wo=%s "
            "reservations=%s shortage=%s notif=%s approver=%s",
            audit_ref, recommendation.case_id, action_name,
            recommendation.urgency, work_order_id,
            reservation_ids, has_shortage, notif,
            recommendation.responsible_approver,
        )

        # ── Step 7: return result ───────────────────────────────────────
        overall_status = "partial" if has_shortage else "success"
        return ExecutionResult(
            case_id=recommendation.case_id,
            action_taken=action_name,
            status=overall_status,
            work_order_id=work_order_id,
            work_order_details=wo,
            reservation_ids=reservation_ids,
            parts_status=parts_status,
            notification_status=notif,
            audit_reference=audit_ref,
            executed_at=executed_at,
        )

    # ── internal helpers ────────────────────────────────────────────────

    def _notify(self, recommendation: MaintenanceRecommendation,
                work_order_id: str) -> str:
        """
        Route persona-specific notifications via notification_mock_service.
        In Phase 11, replace send_notifications() with real channel adapters.
        Always returns 'sent' (notifications are fire-and-forget).
        """
        from src.tools.notification_mock_service import send_notifications
        notified = send_notifications(recommendation, work_order_id)

        escalate = recommendation.urgency in _ESCALATE_URGENCIES
        logger.info(
            "[executor][notify] WO=%s action=%s asset=%s urgency=%s notified=%s",
            work_order_id or "N/A",
            recommendation.recommended_action.name,
            recommendation.asset_id,
            recommendation.urgency,
            notified,
        )
        if escalate:
            logger.info(
                "[executor][notify] SUPERVISOR ALERT — %s urgency on asset %s "
                "approver=%s",
                recommendation.urgency,
                recommendation.asset_id,
                recommendation.responsible_approver,
            )
        return "sent"
