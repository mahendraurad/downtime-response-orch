"""Complete Agent 1->8 approval, execution, and learning certification."""
from copy import deepcopy
import json
import pytest
from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.agents.knowledge_agent import KnowledgeAgent
from src.agents.prescriptive_optimization_agent import PrescriptiveOptimizationAgent
from src.agents.executor_agent import ExecutorAgent
from src.agents.learning_memory_agent import LearningMemoryAgent
from src.schemas.feedback import FeedbackEvent
from src.tools.config_loader import load_monitoring_config, load_learning_config
from src.tools.data_loader import load_telemetry_rows

# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.fixture
def chain(tmp_path):
    mc=load_monitoring_config(); mc.ewma_state_file=str(tmp_path/"ewma.json")
    lc=load_learning_config(); lc.repository_path=str(tmp_path/"learned.json"); lc.training_export_path=str(tmp_path/"train.jsonl")
    cmms=lambda **x:{**x,"work_order_id":"WO-FULL","status":"open"}
    reserve=lambda **x:{**x,"reservation_id":"RES-FULL","status":"reserved"}
    return (DataFoundationAgent.from_data_files(),MonitoringAgent(mc),FailureIntelligenceAgent.from_data_files(),
        PredictiveRiskAgent.from_data_files(),KnowledgeAgent(),PrescriptiveOptimizationAgent(),
        ExecutorAgent(cmms_fn=cmms,inventory_fn=reserve),LearningMemoryAgent(lc))

def _run(chain,scenario="outer_race_fault",approve=True,feedback=True):
    a1=chain[0].process(deepcopy(load_telemetry_rows(scenario)[-1])); a2=chain[1].assess(a1)
    if not a2.anomaly_event:return (a1,a2,None,None,None,None,None,None)
    a3=chain[2].process(a2.anomaly_event,a1); a4=chain[3].process(a3,a2.anomaly_event,a1); a5=chain[4].process(a3,a1,a4)
    inventory={a5.bearing_type:{"qty_on_hand":2,"lead_time_days":3},"MOBIL-DTE-25":{"qty_on_hand":2}}
    context={"planned_stop_windows":[{"window_id":"WIN-FULL","duration_hours":8}]}
    a6=chain[5].process(a4,a3,a5,inventory,context); a7=chain[6].process(a6,approve); a8=None
    if feedback:
        event=FeedbackEvent(case_id=a7.case_id,asset_id=a6.asset_id,bearing_id=a6.bearing_id,
            confirmed_fault_mode=a3.fault_mode,root_cause="confirmed during inspection",action_taken=a7.action_taken,
            recommendation_followed=True,technician_notes="work completed",post_repair_vib_mm_s=1.0,
            post_repair_temp_c=40,closed_at="2026-07-19T10:00:00Z")
        a8=chain[7].process(a7,event)
    return a1,a2,a3,a4,a5,a6,a7,a8

@pytest.mark.parametrize("scenario",["outer_race_fault","inner_race_fault","lubrication_issue","gearbox_fault"])
def test_supported_faults_complete_all_eight_agents(chain,scenario):
    o=_run(chain,scenario); assert o[4].guidance_status=="grounded"
    assert o[5].recommendation_status=="ok" and o[6].status=="success"
    assert o[7].learning_status=="learned" and o[7].index_status=="indexed"

@pytest.mark.parametrize("scenario",["healthy","startup_filter","signal_dropout","unknown_asset"])
def test_terminal_paths_never_create_work(chain,scenario):
    o=_run(chain,scenario); assert o[6] is None and o[7] is None

def test_pending_approval_blocks_execution_and_learning(chain):
    o=_run(chain,approve=False); assert o[6].status=="blocked" and o[7].learning_status=="invalid_input"

def test_complete_provenance_chain_reaches_agent8(chain):
    o=_run(chain); assert o[5].source_risk_config_version==o[3].risk_config_version
    assert o[6].source_prescriptive_config_version==o[5].prescriptive_config_version
    assert o[7].source_executor_config_version==o[6].executor_config_version

def test_all_eight_outputs_are_json_serializable(chain):
    encoded=json.dumps([x.to_dict() for x in _run(chain) if x is not None])
    assert "learning_status" in encoded and "recommendation_status" in encoded

def test_learned_outcome_is_searchable(chain):
    output=_run(chain)[7]; hits=chain[7]._repo.search("outer race confirmed inspection")
    assert hits and output.case_id in hits[0]["source"]

def test_agent5_no_guidance_stops_agent6_and_agent7(chain):
    o=_run(chain,feedback=False); guidance=o[4].model_copy(deep=True)
    guidance.guidance_status="no_guidance"; guidance.guidance_eligible=False
    rec=chain[5].process(o[3],o[2],guidance,{},{}); execution=chain[6].process(rec,True)
    assert not rec.recommendation_eligible and execution.status=="invalid_input"

def test_tampered_agent6_action_is_stopped_by_agent7(chain):
    o=_run(chain,feedback=False); rec=o[5].model_copy(deep=True); rec.recommended_action.name="run_without_guard"
    assert chain[6].process(rec,True).status=="invalid_input"

def test_failed_execution_cannot_poison_learning_memory(chain):
    o=_run(chain,feedback=False); failed=o[6].model_copy(deep=True); failed.status="failed"
    event=FeedbackEvent(case_id=failed.case_id,asset_id=o[5].asset_id,bearing_id=o[5].bearing_id,
        confirmed_fault_mode=o[2].fault_mode,root_cause="x",action_taken="x",closed_at="2026-07-19T10:00:00Z")
    assert not chain[7].process(failed,event).learning_eligible
# ***********************
