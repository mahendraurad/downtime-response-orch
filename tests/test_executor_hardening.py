"""Agent 7 safety, idempotency, provenance, and Agent 6->7 tests."""
import json
import pytest
from src.agents.executor_agent import ExecutorAgent
from src.schemas.recommendation import MaintenanceRecommendation, RecommendedAction, RequiredPart
from src.tools.execution_repository import SQLiteExecutionRepository
from src.tools.config_loader import load_executor_config

# ************** Added by Prateek Mittal on 20th July 2026 ******************
def _rec(**kw):
    values = dict(case_id="CASE-A7", asset_id="AST_MTR_001", bearing_id="BRG_001",
        recommended_action=RecommendedAction(name="stop_and_replace", description="replace", estimated_duration_hours=6),
        urgency="immediate", required_parts=[RequiredPart(part_number="SKF6310")],
        recommendation_status="ok", recommendation_eligible=True, approval_required=True,
        approval_status="pending", schema_version="1.1", prescriptive_config_version="PCFG")
    values.update(kw); return MaintenanceRecommendation(**values)

@pytest.mark.parametrize("bad", [None, {}, "bad", 1, []])
def test_malformed_recommendation_has_no_side_effect(bad):
    calls=[]; result=ExecutorAgent(cmms_fn=lambda **x: calls.append(x)).process(bad, True)
    assert result.status=="invalid_input" and not result.execution_eligible and calls==[]

def test_ineligible_recommendation_cannot_execute():
    calls=[]; rec=_rec(recommendation_eligible=False)
    result=ExecutorAgent(cmms_fn=lambda **x: calls.append(x)).process(rec, True)
    assert result.status=="invalid_input" and calls==[]

def test_non_allowlisted_llm_style_action_cannot_execute():
    calls=[]; rec=_rec(recommended_action=RecommendedAction(name="disable_safety_and_run"))
    result=ExecutorAgent(cmms_fn=lambda **x: calls.append(x)).process(rec, True)
    assert result.status=="invalid_input" and "allowlisted" in result.blocked_reason and calls==[]

def test_rejected_status_cannot_be_overridden_by_approved_argument():
    calls=[]; result=ExecutorAgent(cmms_fn=lambda **x: calls.append(x)).process(_rec(approval_status="rejected"), True)
    assert result.status=="blocked" and calls==[]

def test_connector_injection_creates_auditable_work_order():
    cmms=lambda **x: {**x,"work_order_id":"WO-INJECTED","status":"open"}
    inv=lambda **x: {**x,"reservation_id":"RES-I","status":"reserved"}
    result=ExecutorAgent(cmms_fn=cmms, inventory_fn=inv).process(_rec(), True)
    assert result.status=="success" and result.work_order_id=="WO-INJECTED"
    assert result.reservation_ids==["RES-I"] and result.executor_config_version

def test_execution_trace_is_backend_owned_and_complete():
    cmms=lambda **x: {**x,"work_order_id":"WO-TRACE","status":"open"}
    inv=lambda **x: {**x,"reservation_id":"RES-TRACE","status":"reserved"}
    result=ExecutorAgent(cmms_fn=cmms, inventory_fn=inv).process(_rec(), True)
    assert [step.step_name for step in result.execution_steps] == [
        "Work order created", "Notifications dispatched",
        "Parts procurement", "Maintenance window",
    ]
    assert result.total_steps == 4
    assert result.completed_steps == 3
    assert result.execution_steps[0].owner == "Maintenance Planner"
    assert result.execution_steps[1].deadline == "within 15 minutes"
    assert result.execution_steps[2].deadline == "before RUL minimum"
    assert result.execution_steps[3].escalates_to == "Plant Manager"
    assert "VP Operations" in result.execution_steps[3].escalation_rule
    assert result.execution_policy_version

def test_inventory_exception_is_partial_not_pipeline_exception():
    cmms=lambda **x: {**x,"work_order_id":"WO-I","status":"open"}
    result=ExecutorAgent(cmms_fn=cmms, inventory_fn=lambda **_: (_ for _ in ()).throw(TimeoutError())).process(_rec(), True)
    assert result.status=="partial" and result.parts_status[0]["status"]=="error"

def test_agent6_provenance_is_preserved():
    result=ExecutorAgent(cmms_fn=lambda **x:{**x,"work_order_id":"WO-P","status":"open"},
        inventory_fn=lambda **x:{**x,"reservation_id":"R","status":"reserved"}).process(_rec(), True)
    assert result.source_recommendation_schema_version=="1.1"
    assert result.source_prescriptive_config_version=="PCFG"
    assert result.linked_recommendation_case_id=="CASE-A7"

def test_execution_result_is_json_serializable():
    result=ExecutorAgent().process(_rec(), False)
    assert "executor_config_version" in json.dumps(result.to_dict())

def test_durable_execution_is_idempotent(tmp_path):
    repo=SQLiteExecutionRepository(tmp_path/"exec.db")
    agent=ExecutorAgent(repository=repo, cmms_fn=lambda **x:{**x,"work_order_id":"WO-1","status":"open"},
        inventory_fn=lambda **x:{**x,"reservation_id":"R-1","status":"reserved"})
    first=agent.process_and_store(_rec(), True); second=agent.process_and_store(_rec(), True)
    assert first.persistence_status=="stored" and second.status=="duplicate"
    assert second.duplicate_detected and repo.count()==1

def test_execution_repository_roundtrip(tmp_path):
    repo=SQLiteExecutionRepository(tmp_path/"exec.db"); agent=ExecutorAgent(repository=repo)
    result=agent.process_and_store(_rec(approval_status="rejected"), False)
    assert repo.get("CASE-A7")["status"]=="blocked" and result.persistence_status=="stored"

def test_repository_failure_is_explicit_after_side_effect():
    class Repo:
        def exists(self,_): return False
        def save(self,_): raise OSError("audit disk down")
    result=ExecutorAgent(repository=Repo(), cmms_fn=lambda **x:{**x,"work_order_id":"WO-X","status":"open"},
        inventory_fn=lambda **x:{**x,"reservation_id":"R","status":"reserved"}).process_and_store(_rec(), True)
    assert result.status=="partial" and result.persistence_status=="failed" and result.work_order_id=="WO-X"

def test_process_and_store_requires_repository():
    with pytest.raises(RuntimeError): ExecutorAgent().process_and_store(_rec(), True)

def test_executor_config_rejects_missing_urgency():
    cfg=load_executor_config(); cfg.priority_by_urgency.pop("urgent")
    with pytest.raises(ValueError): cfg.validate()
# ***********************
