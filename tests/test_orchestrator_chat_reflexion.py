"""Query routing, Reflexion, audit, chat API, and graceful-error tests."""
import json
import pytest
from fastapi.testclient import TestClient
from src.agents.reflexion_agent import ReflexionAgent
from src.orchestrator.query_router import plan_query
from src.tools.orchestrator_audit import write_audit
from src.api.main import app

# ************** Added by Prateek Mittal on 20th July 2026 ******************
@pytest.mark.parametrize(("question","intent","count"),[("What is vibration status?","status",1),("Is there an anomaly?","anomaly",2),("Diagnose the fault","diagnosis",3),("What is the RUL risk?","risk",4),("Show SOP and LOTO steps","guidance",5),("What should maintenance do?","recommendation",6),("Execute and create work order","execution",7)])
def test_open_question_call_plans(question,intent,count):
    plan=plan_query(question,True); assert plan.intent==intent and len(plan.agents)==count

def test_unclassified_question_requests_context_without_fabrication():
    plan=plan_query("Tell me about reliability culture"); assert plan.intent=="general" and plan.agents==("reflexion",)
def test_empty_question_has_invalid_plan(): assert plan_query("  ").intent=="invalid"
@pytest.mark.parametrize("question",["Is this reading anomalous?","Could this be anomalous?"])
def test_natural_anomaly_phrasing_routes_to_monitoring(question): assert plan_query(question,True).intent=="anomaly"
@pytest.mark.parametrize("question",["What maintenance action should we take?","Should we replace this bearing?"])
def test_natural_action_phrasing_routes_to_recommendation(question): assert plan_query(question,True).intent=="recommendation"
def test_reflexion_accepts_supported_response():
    result=ReflexionAgent().process({"response":"Risk assessed."},{"pipeline_log":[{"node":"predictive_risk","status":"ok"}]})
    assert result.status=="accepted" and result.response["sources"]==["predictive_risk"]
def test_reflexion_removes_unsupported_execution_claim():
    result=ReflexionAgent().process({"response":"Work order created and parts reserved."},{"pipeline_log":[]})
    assert result.status=="refined" and "has not occurred" in result.response["response"]
def test_reflexion_blocks_malformed_draft(): assert ReflexionAgent().process("bad").status=="blocked"
def test_reflexion_caps_response_length(): assert ReflexionAgent(max_characters=10).process({"response":"x"*30}).status=="refined"
def test_audit_is_append_only_jsonl(tmp_path):
    path=tmp_path/"audit.jsonl"; write_audit(path,event="chat",run_id="1",status="ok"); write_audit(path,event="chat",run_id="2",status="ok")
    rows=[json.loads(x) for x in path.read_text().splitlines()]; assert [x["run_id"] for x in rows]==["1","2"] and all("timestamp_utc" in x for x in rows)

@pytest.fixture
def client(): return TestClient(app,raise_server_exceptions=False)

def test_chat_defined_empty_error_is_422(client):
    response=client.post("/api/chat",json={"message":""}); assert response.status_code==422 and "empty" in response.json()["detail"]
def test_chat_oversize_error_is_422(client): assert client.post("/api/chat",json={"message":"x"*2001}).status_code==422
def test_chat_missing_signal_returns_context_request(client):
    data=client.post("/api/chat",json={"message":"What is the RUL?"}).json(); assert data["intent"]=="risk" and data["needs_context"] and data["pipeline_log"]==[]
def test_chat_general_question_is_graceful(client):
    data=client.post("/api/chat",json={"message":"How should we improve reliability culture?"}).json(); assert data["intent"]=="general" and data["reflection_status"]=="accepted"
def test_chat_scenario_runs_required_agents_and_reflects(client):
    data=client.post("/api/chat",json={"message":"What is the RUL risk?","context":{"scenario":"outer_race_fault"}}).json()
    expected=["data_foundation","monitoring","failure_intelligence","predictive_risk"]
    assert [x["node"] for x in data["pipeline_log"]]==expected and data["sources"]==expected
def test_chat_execution_request_never_auto_approves(client):
    data=client.post("/api/chat",json={"message":"Execute and create work order","context":{"scenario":"outer_race_fault"}}).json()
    assert "executor" not in [x["node"] for x in data["pipeline_log"]] and data["pipeline_log"][-1]["node"]=="prescriptive"
    assert data["agent_outputs"]["recommendation"]["approval_status"]=="pending"
    assert data["agent_outputs"]["execution_result"] is None
def test_pipeline_api_exposes_agent6_recommendation(client):
    data=client.post("/api/pipeline/run",json={"signal":{},"scenario":"outer_race_fault","query":"recommend action"}).json()
    assert data["recommendation"]["recommendation_status"]=="ok" and data["execution_result"] is None
def test_chat_validation_schema_error_is_graceful(client): assert client.post("/api/chat",json={"persona":"supervisor"}).status_code==422
def test_undefined_chat_error_has_generic_envelope(client,monkeypatch):
    import src.api.main as api
    monkeypatch.setattr(api._REFLEXION,"process",lambda *_: (_ for _ in ()).throw(RuntimeError("database password secret")))
    response=client.post("/api/chat",json={"message":"culture question"}); error=response.json()["error"]
    assert response.status_code==500 and error["code"]=="INTERNAL_ERROR" and error["error_id"].startswith("ERR-") and "password" not in error["message"]
def test_pipeline_unknown_scenario_is_graceful(client): assert client.post("/api/pipeline/run",json={"signal":{},"scenario":"does_not_exist"}).status_code in {404,500}
# ***********************
