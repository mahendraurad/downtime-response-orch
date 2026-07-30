"""
tests/test_executor_agent.py  —  Phase 9

Unit tests for the Executor Agent.

The mock CMMS and inventory services are real (not patched) — they are cheap
in-memory operations, and testing them together validates the full execution
path without requiring external systems.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from src.agents.executor_agent import ExecutorAgent
from src.schemas.recommendation import (
    MaintenanceRecommendation, RecommendedAction, RequiredPart, Contributor,
)
from src.schemas.execution import ExecutionResult


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_rec(**overrides) -> MaintenanceRecommendation:
    """Return a minimal MaintenanceRecommendation with sensible defaults."""
    defaults = dict(
        case_id="CASE-001",
        asset_id="AST_PMP_001",
        bearing_id="BRG_005",
        recommended_action=RecommendedAction(
            name="inspection",
            description="Inspect bearing outer race per SOP_001",
            estimated_duration_hours=2.0,
        ),
        urgency="planned",
        required_parts=[],
        window_chosen="WIN_005",
        rationale="Stage-2 outer race fault detected on drive-end bearing.",
        approval_status="pending",
        responsible_person="Plant Supervisor – James Kowalski",
        responsible_person_id="PERSONA_SUP",
        responsible_approver="Plant Supervisor – James Kowalski",
        responsible_approver_id="PERSONA_SUP",
        contributors=[
            Contributor(role="Maintenance Planner", name="Tom Rodriguez",
                        concern="confirms parts and crew readiness"),
        ],
    )
    defaults.update(overrides)
    return MaintenanceRecommendation(**defaults)


@pytest.fixture
def agent() -> ExecutorAgent:
    return ExecutorAgent()


# ── Approval guard ────────────────────────────────────────────────────────────

class TestApprovalGuard:

    def test_blocked_when_status_pending_and_approved_false(self, agent):
        rec = _make_rec(approval_status="pending")
        result = agent.process(rec, approved=False)

        assert result.status == "blocked"
        assert result.work_order_id == ""
        assert result.reservation_ids == []
        assert result.notification_status == "skipped"
        assert "pending" in result.blocked_reason

    def test_blocked_when_status_rejected(self, agent):
        rec = _make_rec(approval_status="rejected")
        result = agent.process(rec, approved=False)

        assert result.status == "blocked"

    def test_executes_when_approved_param_true(self, agent):
        rec = _make_rec(approval_status="pending")
        result = agent.process(rec, approved=True)

        assert result.status in ("success", "partial")
        assert result.work_order_id.startswith("WO-")

    def test_executes_when_recommendation_pre_approved(self, agent):
        rec = _make_rec(approval_status="approved")
        result = agent.process(rec, approved=False)

        assert result.status in ("success", "partial")
        assert result.work_order_id.startswith("WO-")


# ── Log-only actions ──────────────────────────────────────────────────────────

class TestLogOnlyActions:

    def test_monitor_urgency_raises_no_work_order(self, agent):
        rec = _make_rec(
            urgency="monitor",
            approval_status="approved",
            recommended_action=RecommendedAction(name="continue_monitoring"),
        )
        result = agent.process(rec)

        assert result.status == "success"
        assert result.work_order_id == ""
        assert result.work_order_details == {}
        assert result.reservation_ids == []
        assert result.parts_status == []

    def test_continue_monitoring_action_name_raises_no_work_order(self, agent):
        rec = _make_rec(
            urgency="planned",   # urgency is not monitor, but action name is
            approval_status="approved",
            recommended_action=RecommendedAction(name="continue_monitoring"),
        )
        result = agent.process(rec)

        assert result.work_order_id == ""

    def test_notification_sent_for_log_only(self, agent):
        rec = _make_rec(
            urgency="monitor",
            approval_status="approved",
            recommended_action=RecommendedAction(name="continue_monitoring"),
        )
        result = agent.process(rec)

        assert result.notification_status == "sent"

    def test_audit_ref_always_set(self, agent):
        rec = _make_rec(
            urgency="monitor",
            approval_status="approved",
            recommended_action=RecommendedAction(name="continue_monitoring"),
        )
        result = agent.process(rec)

        assert result.audit_reference.startswith("AUDIT-")


# ── Work order creation ───────────────────────────────────────────────────────

class TestWorkOrderCreation:

    @pytest.mark.parametrize("urgency,expected_priority", [
        ("immediate", "P1-Critical"),
        ("urgent",    "P2-High"),
        ("planned",   "P3-Medium"),
    ])
    def test_urgency_maps_to_priority(self, agent, urgency, expected_priority):
        rec = _make_rec(urgency=urgency, approval_status="approved")
        result = agent.process(rec)

        assert result.work_order_details["priority"] == expected_priority

    def test_work_order_id_format(self, agent):
        rec = _make_rec(approval_status="approved")
        result = agent.process(rec)

        assert result.work_order_id.startswith("WO-")
        assert len(result.work_order_id) == 11  # "WO-" + 8 hex chars

    def test_work_order_details_populated(self, agent):
        rec = _make_rec(approval_status="approved", urgency="planned")
        result = agent.process(rec)

        wo = result.work_order_details
        assert wo["asset_id"] == "AST_PMP_001"
        assert wo["status"] == "open"
        assert "created_at" in wo

    def test_case_id_and_action_propagated(self, agent):
        rec = _make_rec(
            case_id="DEMO-004",
            approval_status="approved",
            recommended_action=RecommendedAction(name="lubrication_service"),
        )
        result = agent.process(rec)

        assert result.case_id == "DEMO-004"
        assert result.action_taken == "lubrication_service"


# ── Part reservation ──────────────────────────────────────────────────────────

class TestPartReservation:

    def test_known_part_reserved_with_correct_qty(self, agent):
        rec = _make_rec(
            approval_status="approved",
            required_parts=[RequiredPart(part_number="MOBIL-DTE-25", quantity=1)],
        )
        result = agent.process(rec)

        assert len(result.reservation_ids) == 1
        assert result.reservation_ids[0].startswith("RES-")
        assert result.parts_status[0]["part_number"] == "MOBIL-DTE-25"
        assert result.parts_status[0]["status"] == "reserved"
        assert result.parts_status[0]["reserved_qty"] == 1

    def test_quantity_from_part_object_used(self, agent):
        rec = _make_rec(
            approval_status="approved",
            required_parts=[RequiredPart(part_number="GREASE_HP222", quantity=2)],
        )
        result = agent.process(rec)

        assert result.parts_status[0]["reserved_qty"] == 2

    def test_unknown_part_triggers_shortage(self, agent):
        rec = _make_rec(
            approval_status="approved",
            required_parts=[RequiredPart(part_number="UNKNOWN_PART_XYZ", quantity=1)],
        )
        result = agent.process(rec)

        assert result.status == "partial"
        assert result.reservation_ids == []
        assert result.parts_status[0]["status"] == "shortage"

    def test_mixed_parts_gives_partial_status(self, agent):
        rec = _make_rec(
            approval_status="approved",
            required_parts=[
                RequiredPart(part_number="SEAL_NEOPRENE_65MM", quantity=1),
                RequiredPart(part_number="UNKNOWN_PART_999", quantity=1),
            ],
        )
        result = agent.process(rec)

        assert result.status == "partial"
        statuses = {p["part_number"]: p["status"] for p in result.parts_status}
        assert statuses["SEAL_NEOPRENE_65MM"] == "reserved"
        assert statuses["UNKNOWN_PART_999"] == "shortage"

    def test_all_parts_reserved_gives_success(self, agent):
        rec = _make_rec(
            approval_status="approved",
            required_parts=[
                RequiredPart(part_number="COUPLING_INSERT", quantity=1),
                RequiredPart(part_number="OIL_MOBIL_SHC629", quantity=1),
            ],
        )
        result = agent.process(rec)

        assert result.status == "success"
        assert len(result.reservation_ids) == 2

    def test_no_parts_required_gives_success(self, agent):
        rec = _make_rec(approval_status="approved", required_parts=[])
        result = agent.process(rec)

        assert result.status == "success"
        assert result.reservation_ids == []
        assert result.parts_status == []


# ── Sample recommendation from prescriptive agent ────────────────────────────

class TestSampleRecommendation:
    """Validates the exact output shape from sample_recommendation_ok.json."""

    def _sample_rec(self) -> MaintenanceRecommendation:
        return MaintenanceRecommendation(
            case_id="DEMO-004",
            asset_id="AST_PMP_001",
            bearing_id="BRG_005",
            recommended_action=RecommendedAction(
                name="lubrication_service",
                description="lubrication_service (in_window) per SOP_003",
                estimated_duration_hours=3.0,
            ),
            urgency="planned",
            required_parts=[
                RequiredPart(part_number="MOBIL-DTE-25", quantity=1, lead_time_days=0),
            ],
            window_chosen="WIN_005",
            rationale="Lubrication Issue detected at severity 'stage_2' on bearing BRG_005.",
            approval_status="approved",
            responsible_person="Plant Supervisor – James Kowalski",
            responsible_person_id="PERSONA_SUP",
            responsible_approver="Plant Supervisor – James Kowalski",
            responsible_approver_id="PERSONA_SUP",
            contributors=[
                Contributor(role="Maintenance Planner", name="Tom Rodriguez",
                            concern="confirms parts and crew readiness"),
            ],
        )

    def test_sample_executes_successfully(self, agent):
        result = agent.process(self._sample_rec())

        assert result.status == "success"
        assert result.case_id == "DEMO-004"
        assert result.action_taken == "lubrication_service"
        assert result.work_order_id.startswith("WO-")
        assert result.work_order_details["priority"] == "P3-Medium"
        assert len(result.reservation_ids) == 1
        assert result.notification_status == "sent"

    def test_sample_part_reserved(self, agent):
        result = agent.process(self._sample_rec())

        assert result.parts_status[0]["part_number"] == "MOBIL-DTE-25"
        assert result.parts_status[0]["status"] == "reserved"


# ── CMMS failure handling ─────────────────────────────────────────────────────

class TestCMMSFailure:

    def test_cmms_exception_returns_failed_status(self, agent):
        with patch(
            "src.agents.executor_agent.create_work_order",
            side_effect=RuntimeError("CMMS unreachable"),
        ):
            rec = _make_rec(approval_status="approved")
            result = agent.process(rec)

        assert result.status == "failed"
        assert "CMMS error" in result.blocked_reason
        assert result.work_order_id == ""
        assert result.notification_status == "skipped"

    def test_cmms_exception_does_not_raise(self, agent):
        with patch(
            "src.agents.executor_agent.create_work_order",
            side_effect=ConnectionError("timeout"),
        ):
            rec = _make_rec(approval_status="approved")
            result = agent.process(rec)  # must not raise

        assert isinstance(result, ExecutionResult)


# ── Output contract ───────────────────────────────────────────────────────────

class TestOutputContract:

    def test_all_fields_present(self, agent):
        rec = _make_rec(approval_status="approved")
        result = agent.process(rec)

        assert isinstance(result, ExecutionResult)
        assert result.executed_at != ""
        assert result.audit_reference.startswith("AUDIT-")
        assert result.status in ("success", "partial", "blocked", "failed")

    def test_unique_audit_refs_per_call(self, agent):
        rec = _make_rec(approval_status="approved")
        r1 = agent.process(rec)
        r2 = agent.process(rec)

        assert r1.audit_reference != r2.audit_reference

    def test_unique_work_order_ids_per_call(self, agent):
        rec = _make_rec(approval_status="approved")
        r1 = agent.process(rec)
        r2 = agent.process(rec)

        assert r1.work_order_id != r2.work_order_id
