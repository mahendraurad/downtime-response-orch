"""Agent 8 validation, persistence, retrieval, export, and LLM safety tests."""
from datetime import datetime, timezone, timedelta
import json
import pytest
from src.agents.learning_memory_agent import LearningMemoryAgent
from src.schemas.execution import ExecutionResult
from src.schemas.feedback import FeedbackEvent, RejectionFeedback
from src.tools.config_loader import load_learning_config
from src.tools.learned_case_repository import JSONLearnedCaseRepository

# ************** Added by Prateek Mittal on 20th July 2026 ******************
def _execution(**kw):
    values=dict(case_id="CASE-L1",action_taken="stop_and_replace",status="success",
        audit_reference="AUDIT-1",execution_eligible=True,schema_version="1.1",executor_config_version="ECFG")
    values.update(kw); return ExecutionResult(**values)
def _feedback(**kw):
    values=dict(case_id="CASE-L1",asset_id="AST_MTR_001",bearing_id="BRG_001",
        confirmed_fault_mode="outer_race_fault",root_cause="contamination",
        action_taken="bearing_replacement",recommendation_followed=True,
        technician_notes="Pitting confirmed on outer race",post_repair_vib_mm_s=1.1,
        post_repair_temp_c=42,days_to_failure_actual=2,closed_at="2026-07-19T10:00:00Z")
    values.update(kw); return FeedbackEvent(**values)
def _agent(tmp_path, llm=None):
    cfg=load_learning_config(); cfg.repository_path=str(tmp_path/"cases.json"); cfg.training_export_path=str(tmp_path/"rows.jsonl"); cfg.rejection_repository_path=str(tmp_path/"rejections.json")
    cfg.chat_observation_repository_path=str(tmp_path/"chat_observations.json")
    return LearningMemoryAgent(cfg, llm_client=llm)

def test_closed_execution_creates_learned_case(tmp_path):
    doc=_agent(tmp_path).process(_execution(),_feedback())
    assert doc.learning_status=="learned" and doc.persistence_status=="stored" and doc.index_status=="indexed"
    assert "outer_race_fault" in doc.content and doc.linked_execution_case_id=="CASE-L1"
    assert doc.asset_id=="AST_MTR_001" and doc.bearing_id=="BRG_001"


def test_recent_closed_cases_can_be_filtered_by_exact_asset(tmp_path):
    agent=_agent(tmp_path)
    agent.process(_execution(),_feedback())
    agent.process(_execution(case_id="CASE-L2"),_feedback(
        case_id="CASE-L2",asset_id="AST_PMP_001",bearing_id="BRG_004"))
    assert [row["case_id"] for row in agent.recent_cases(
        3,asset_id="AST_MTR_001")]==["CASE-L1"]


def test_duplicate_rejection_is_idempotent_and_not_counted_twice(tmp_path):
    now=datetime(2026,8,26,12,0,tzinfo=timezone.utc)
    agent=_agent(tmp_path); agent._now=lambda:now
    feedback=RejectionFeedback(case_id="REC-DUP",asset_id="AST_MTR_001",
        fault_mode="outer_race_fault",reason_code="diagnosis_wrong",
        free_text="wrong diagnosis",persona_id="supervisor",
        rejected_at="2026-08-26T10:00:00Z")
    first=agent.record_rejection(feedback); second=agent.record_rejection(feedback)
    assert first.status=="recorded" and first.consecutive_rejections==1
    assert first.valid_closure and first.closure_status=="rejected"
    assert second.status=="duplicate" and not second.learning_recorded
    assert second.persistence_status=="duplicate_not_stored"
    assert len(agent.recent_rejections(5,"AST_MTR_001"))==1


def test_invalid_rejection_is_not_reported_as_a_valid_closure(tmp_path):
    now=datetime(2026,8,26,12,0,tzinfo=timezone.utc)
    agent=_agent(tmp_path); agent._now=lambda:now
    result=agent.record_rejection(RejectionFeedback(
        case_id="REC-FUTURE",asset_id="AST_MTR_001",
        fault_mode="outer_race_fault",reason_code="other",
        persona_id="supervisor",rejected_at="2026-08-27T10:00:00Z"))
    assert result.status=="invalid_input"
    assert not result.learning_recorded and not result.valid_closure
    assert result.closure_status==""


def test_two_distinct_rejections_trigger_review_and_are_retrievable(tmp_path):
    now=datetime(2026,8,26,12,0,tzinfo=timezone.utc)
    agent=_agent(tmp_path); agent._now=lambda:now
    for case_id,stamp in (("REC-1","2026-08-25T10:00:00Z"),
                          ("REC-2","2026-08-26T10:00:00Z")):
        result=agent.record_rejection(RejectionFeedback(
            case_id=case_id,asset_id="AST_MTR_001",fault_mode="outer_race_fault",
            reason_code="second_opinion",free_text="review requested",
            persona_id="supervisor",rejected_at=stamp))
    assert result.reliability_review_required and result.consecutive_rejections==2
    rows=agent.recent_rejections(3,"AST_MTR_001")
    assert [row["case_id"] for row in rows]==["REC-2","REC-1"]
    assert rows[0]["reliability_review_required"] is True
    assert all(row["valid_closure"] is True for row in rows)
    assert all(row["closure_status"]=="rejected" for row in rows)
    assert all(row["confirmed_outcome"] is False for row in rows)


def test_other_asset_or_fault_does_not_inflate_rejection_sequence(tmp_path):
    now=datetime(2026,8,26,12,0,tzinfo=timezone.utc)
    agent=_agent(tmp_path); agent._now=lambda:now
    rows=(("REC-1","AST_MTR_001","outer_race_fault"),
          ("REC-2","AST_PMP_001","outer_race_fault"),
          ("REC-3","AST_MTR_001","inner_race_fault"))
    results=[]
    for case_id,asset_id,fault in rows:
        results.append(agent.record_rejection(RejectionFeedback(
            case_id=case_id,asset_id=asset_id,fault_mode=fault,
            reason_code="second_opinion",persona_id="supervisor",
            rejected_at="2026-08-26T10:00:00Z")))
    assert all(result.consecutive_rejections==1 for result in results)
    assert not any(result.reliability_review_required for result in results)


def test_malformed_legacy_rejection_rows_are_ignored_safely(tmp_path):
    path=tmp_path/"rejections.json"
    path.write_text(json.dumps([None,"bad",{
        "case_id":"NAIVE","asset_id":"AST_MTR_001",
        "fault_mode":"outer_race_fault","decision":"rejected",
        "decided_at":"2026-08-01T10:00:00",
    }]))
    cfg=load_learning_config(); cfg.repository_path=str(tmp_path/"cases.json")
    cfg.training_export_path=str(tmp_path/"rows.jsonl")
    cfg.rejection_repository_path=str(path)
    agent=LearningMemoryAgent(cfg,now_fn=lambda:datetime(
        2026,8,26,12,0,tzinfo=timezone.utc))
    result=agent.record_rejection(RejectionFeedback(
        case_id="VALID",asset_id="AST_MTR_001",fault_mode="outer_race_fault",
        reason_code="other",persona_id="supervisor",
        rejected_at="2026-08-26T10:00:00Z"))
    assert result.status=="recorded" and result.consecutive_rejections==1

@pytest.mark.parametrize("status",["success","partial"])
def test_allowed_execution_outcomes_are_learned(tmp_path,status):
    assert _agent(tmp_path).process(_execution(status=status),_feedback()).learning_eligible

@pytest.mark.parametrize("status",["blocked","failed","duplicate","invalid_input",""])
def test_noncompleted_execution_never_enters_memory(tmp_path,status):
    doc=_agent(tmp_path).process(_execution(status=status),_feedback())
    assert doc.learning_status=="invalid_input" and not doc.learning_eligible

@pytest.mark.parametrize("bad",[None,{},"bad",3,[]])
def test_malformed_execution_is_safely_rejected(tmp_path,bad):
    assert _agent(tmp_path).process(bad,_feedback()).learning_status=="invalid_input"

@pytest.mark.parametrize("bad",[None,{},"bad",3,[]])
def test_malformed_feedback_is_safely_rejected(tmp_path,bad):
    assert _agent(tmp_path).process(_execution(),bad).learning_status=="invalid_input"

def test_case_mismatch_is_rejected(tmp_path):
    doc=_agent(tmp_path).process(_execution(),_feedback(case_id="OTHER"))
    assert not doc.learning_eligible and "identities" in doc.status_reason

@pytest.mark.parametrize("field",["asset_id","bearing_id","confirmed_fault_mode","action_taken","closed_at"])
def test_required_feedback_fields_are_enforced(tmp_path,field):
    doc=_agent(tmp_path).process(_execution(),_feedback(**{field:""}))
    assert doc.learning_status=="invalid_input"

def test_root_cause_is_required(tmp_path):
    assert "root cause" in _agent(tmp_path).process(_execution(),_feedback(root_cause="")).status_reason

@pytest.mark.parametrize("stamp",["bad-date","2026-07-19T10:00:00"])
def test_invalid_or_naive_closure_timestamp_is_rejected(tmp_path,stamp):
    assert not _agent(tmp_path).process(_execution(),_feedback(closed_at=stamp)).learning_eligible

def test_future_closure_is_rejected(tmp_path):
    future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    assert "future" in _agent(tmp_path).process(_execution(),_feedback(closed_at=future)).status_reason

@pytest.mark.parametrize("changes",[{"post_repair_vib_mm_s":-1},{"post_repair_temp_c":-300},{"days_to_failure_actual":-1}])
def test_physically_invalid_outcomes_are_rejected(tmp_path,changes):
    assert not _agent(tmp_path).process(_execution(),_feedback(**changes)).learning_eligible

def test_duplicate_case_is_not_stored_twice(tmp_path):
    agent=_agent(tmp_path); agent.process(_execution(),_feedback()); duplicate=agent.process(_execution(),_feedback())
    assert duplicate.learning_status=="duplicate" and agent._repo.count()==1

def test_repository_roundtrip_and_searchability(tmp_path):
    agent=_agent(tmp_path); agent.process(_execution(),_feedback())
    row=agent._repo.get("CASE-L1"); hits=agent._repo.search("outer race contamination bearing replacement")
    assert row["fault_mode"]=="outer_race_fault" and hits[0]["source"]=="Learned case CASE-L1"


def test_chat_observation_noops_when_no_learning_signal(tmp_path):
    result=_agent(tmp_path).observe_chat({
        "run_id":"CHAT-1","conversation_id":"CONV-1","message":"thanks",
        "intent":"conversational","response":"You're welcome!",
        "result":{"intent":"conversational","clarification_required":False},
    })
    assert result.status=="no_new_learning"
    assert result.learning_recorded is False
    assert _agent(tmp_path).recent_chat_observations(3)==[]


def test_chat_observation_records_asset_registration_gap(tmp_path):
    agent=_agent(tmp_path)
    result=agent.observe_chat({
        "run_id":"CHAT-2","conversation_id":"CONV-1",
        "message":"Analyse X-999","intent":"risk",
        "response":"Asset X-999 is not registered.",
        "asset_ids":["X-999"],
        "result":{"clarification_required":True,
            "clarification":{"missing_fields":["asset_registration"],
                "asset_id":"X-999"}},
    })
    assert result.status=="learned" and result.learning_recorded
    assert result.learning_kind=="asset_registration_gap"
    assert agent.recent_chat_observations(3,"X-999")[0]["observation_id"]==result.observation_id


def test_duplicate_chat_observation_is_not_recorded_twice(tmp_path):
    agent=_agent(tmp_path)
    event={"run_id":"CHAT-3","conversation_id":"CONV-1",
        "message":"Rank fleet","intent":"fleet","response":"Need aggregation.",
        "result":{"clarification_required":True,
            "clarification":{"missing_fields":["fleet_aggregation_service"]}}}
    first=agent.observe_chat(event); second=agent.observe_chat(dict(event,run_id="CHAT-4"))
    assert first.status=="learned"
    assert second.status=="duplicate" and not second.learning_recorded
    assert len(agent.recent_chat_observations(5))==1


def test_chat_observation_records_knowledge_gap(tmp_path):
    result=_agent(tmp_path).observe_chat({
        "run_id":"CHAT-5","conversation_id":"CONV-1",
        "message":"Explain obscure SOP","intent":"concept",
        "response":"No approved source matched.",
        "result":{"clarification_required":False,
            "rag":{"status":"no_match","retrieval_query":"obscure SOP"}},
    })
    assert result.status=="learned"
    assert result.learning_kind=="knowledge_gap"

@pytest.mark.parametrize("limit",[0,-1,True,"3",None])
def test_recent_history_rejects_invalid_limits(tmp_path,limit):
    agent=_agent(tmp_path); agent.process(_execution(),_feedback())
    assert agent.recent_cases(limit)==[]

def test_recent_history_ignores_malformed_rows(tmp_path):
    path=tmp_path/"cases.json"
    path.write_text(json.dumps([None,"bad",{"case_id":"VALID","created_at":"2026-07-20T00:00:00Z"}]))
    cfg=load_learning_config(); cfg.repository_path=str(path); cfg.training_export_path=str(tmp_path/"rows.jsonl")
    assert LearningMemoryAgent(cfg).recent_cases(3)==[{"case_id":"VALID","created_at":"2026-07-20T00:00:00Z"}]

def test_training_row_is_exported(tmp_path):
    agent=_agent(tmp_path); agent.process(_execution(),_feedback())
    row=json.loads((tmp_path/"rows.jsonl").read_text().strip())
    assert row["confirmed_fault_mode"]=="outer_race_fault" and row["execution_status"]=="success"

def test_output_and_repository_are_json_serializable(tmp_path):
    doc=_agent(tmp_path).process(_execution(),_feedback()); assert "learning_status" in json.dumps(doc.to_dict())

def test_execution_provenance_is_linked(tmp_path):
    doc=_agent(tmp_path).process(_execution(),_feedback())
    assert doc.source_execution_schema_version=="1.1" and doc.source_executor_config_version=="ECFG"
    assert doc.learning_config_version

class FakeLLM:
    def __init__(self,response=None,error=None): self.response,self.error,self.calls=response,error,0
    def is_configured(self): return True
    def complete_json(self,**_):
        self.calls+=1
        if self.error: raise self.error
        return self.response
def _llm_agent(tmp_path,fake):
    cfg=load_learning_config(); cfg.repository_path=str(tmp_path/"llm.json"); cfg.training_export_path=str(tmp_path/"llm.jsonl"); cfg.llm_narrative_enabled=True
    return LearningMemoryAgent(cfg,llm_client=fake)

def test_llm_can_rewrite_narrative_only_when_labels_preserved(tmp_path):
    text="outer_race_fault confirmed after bearing_replacement; contamination observed."
    doc=_llm_agent(tmp_path,FakeLLM({"narrative":text})).process(_execution(),_feedback())
    assert doc.content==text and doc.fault_mode=="outer_race_fault" and doc.narrative_source=="template+llm_narrative"

@pytest.mark.parametrize("response",[None,{}, {"narrative":"hallucinated unrelated outcome"},{"fault_mode":"wrong"}])
def test_malformed_or_label_dropping_llm_falls_back_to_template(tmp_path,response):
    doc=_llm_agent(tmp_path,FakeLLM(response)).process(_execution(),_feedback())
    assert doc.narrative_source=="template" and "outer_race_fault" in doc.content

def test_llm_timeout_falls_back(tmp_path):
    doc=_llm_agent(tmp_path,FakeLLM(error=TimeoutError())).process(_execution(),_feedback())
    assert doc.learning_status=="learned" and doc.narrative_source=="template"

def test_invalid_input_never_calls_llm(tmp_path):
    fake=FakeLLM({"narrative":"x"}); doc=_llm_agent(tmp_path,fake).process(_execution(status="failed"),_feedback())
    assert fake.calls==0 and not doc.learning_eligible

def test_repository_failure_is_explicit(tmp_path):
    class Repo:
        def exists(self,_): return False
        def save(self,_): raise OSError("disk down")
    cfg=load_learning_config(); cfg.training_export_path=str(tmp_path/"r.jsonl")
    doc=LearningMemoryAgent(cfg,repository=Repo()).process(_execution(),_feedback())
    assert doc.learning_status=="persistence_failed" and doc.persistence_status=="failed"

def test_learning_config_validation_rejects_bad_score():
    cfg=load_learning_config(); cfg.minimum_score=2
    with pytest.raises(ValueError): cfg.validate()
# ***********************
