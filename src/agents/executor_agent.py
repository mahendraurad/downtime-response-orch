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
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List

from src.schemas.recommendation import MaintenanceRecommendation
from src.schemas.execution import ExecutionResult
from src.tools.cmms_mock_service import create_work_order
from src.tools.inventory_mock_service import reserve_part
from src.tools.config_loader import ExecutorConfig, load_executor_config

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

    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    def __init__(self, cfg: ExecutorConfig = None, repository=None,
                 cmms_fn=None, inventory_fn=None, notification_fn=None,
                 now_fn=None):
        self._cfg = cfg or load_executor_config(); self._cfg.validate()
        self._repository = repository
        self._cmms_fn = cmms_fn
        self._inventory_fn = inventory_fn
        self._notification_fn = notification_fn
        self._now_fn = now_fn or (lambda: datetime.now(tz=timezone.utc))
        self._version = hashlib.sha256(json.dumps(asdict(self._cfg), sort_keys=True).encode()).hexdigest()[:16]

    def _base(self, recommendation):
        return dict(
            executor_config_version=self._version,
            source_recommendation_schema_version=getattr(recommendation, "schema_version", ""),
            source_prescriptive_config_version=getattr(recommendation, "prescriptive_config_version", ""),
            linked_recommendation_case_id=getattr(recommendation, "case_id", ""),
        )
    # ***********************

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
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        # Validate the typed, eligible, allowlisted recommendation before any side effect.
        if not isinstance(recommendation, MaintenanceRecommendation):
            return ExecutionResult(status="invalid_input", execution_eligible=False,
                                   blocked_reason="typed MaintenanceRecommendation required",
                                   executed_at=self._now_fn().isoformat(),
                                   executor_config_version=self._version)
        executed_at = self._now_fn().isoformat()
        audit_ref   = f"AUDIT-{uuid.uuid4().hex[:10].upper()}"
        action_name = recommendation.recommended_action.name
        base = self._base(recommendation)
        if not getattr(recommendation, "recommendation_eligible", True):
            return ExecutionResult(case_id=recommendation.case_id, action_taken=action_name,
                status="invalid_input", execution_eligible=False,
                blocked_reason="recommendation is not execution eligible", notification_status="skipped",
                audit_reference=audit_ref, executed_at=executed_at, **base)
        if action_name not in self._cfg.allowed_actions:
            return ExecutionResult(case_id=recommendation.case_id, action_taken=action_name,
                status="invalid_input", execution_eligible=False,
                blocked_reason="recommended action is not allowlisted", notification_status="skipped",
                audit_reference=audit_ref, executed_at=executed_at, **base)
        if recommendation.approval_status == "rejected":
            approved = False
        # ***********************

        # ── Step 1: approval guard ──────────────────────────────────────
        if recommendation.approval_status == "rejected" or not _is_approved(recommendation, approved):
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
                execution_eligible=False,
                **base,
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
                **base,
            )

        # ── Step 3: create work order ───────────────────────────────────
        priority    = self._cfg.priority_by_urgency.get(recommendation.urgency, "P3-Medium")
        description = (
            f"{recommendation.recommended_action.description or action_name} — "
            f"{recommendation.rationale[:200]}"
        ).strip(" —")

        try:
            wo = (self._cmms_fn or create_work_order)(
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
                execution_eligible=False,
                **base,
            )

        # ── Step 4: reserve required parts ──────────────────────────────
        reservation_ids: List[str] = []
        parts_status    = []
        has_shortage    = False

        for part in recommendation.required_parts:
            try:
                result = (self._inventory_fn or reserve_part)(
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
            **base,
        )

    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    def process_and_store(self, recommendation, approved=False):
        if self._repository is None:
            raise RuntimeError("process_and_store requires an execution repository")
        case_id = getattr(recommendation, "case_id", "")
        if self._cfg.reject_duplicate_cases and case_id and self._repository.exists(case_id):
            return ExecutionResult(case_id=case_id, status="duplicate", execution_eligible=False,
                duplicate_detected=True, persistence_status="duplicate_not_stored",
                blocked_reason="case already executed", executed_at=self._now_fn().isoformat(),
                executor_config_version=self._version, linked_recommendation_case_id=case_id)
        result = self.process(recommendation, approved)
        try:
            result.persistence_status = "stored"
            self._repository.save(result)
        except Exception as exc:
            result.persistence_status = "failed"
            result.status = "partial" if result.work_order_id else "failed"
            result.blocked_reason = f"execution audit persistence failed: {exc}"
        return result
    # ***********************

    # ── internal helpers ────────────────────────────────────────────────

    def _notify(self, recommendation: MaintenanceRecommendation,
                work_order_id: str) -> str:
        """
        Log-based notification mock.
        In Phase 11, replace with email / Teams / PagerDuty calls.
        Always returns 'sent' in dev (notifications are fire-and-forget).
        """
        # The default adapter powers the frontend inbox. Injection keeps this
        # boundary replaceable and makes notifier failures independently testable.
        from src.tools.notification_mock_service import send_notifications

        contributors = ", ".join(
            f"{c.name} ({c.role})" for c in recommendation.contributors
        ) or "on-call team"
        escalate = recommendation.urgency in self._cfg.escalate_urgencies

        logger.info(
            "[executor][notify] WO=%s action=%s responsible=%s contributors=%s "
            "asset=%s escalate=%s",
            work_order_id or "N/A",
            recommendation.recommended_action.name,
            recommendation.responsible_person,
            contributors,
            recommendation.asset_id,
            escalate,
        )
        if escalate:
            logger.info(
                "[executor][notify] SUPERVISOR ALERT — %s urgency on asset %s "
                "approver=%s",
                recommendation.urgency,
                recommendation.asset_id,
                recommendation.responsible_approver,
            )
        try:
            (self._notification_fn or send_notifications)(recommendation, work_order_id)
            return "sent"
        except Exception as exc:
            logger.error("[executor][notify] delivery failed: %s", exc)
            return "failed"
