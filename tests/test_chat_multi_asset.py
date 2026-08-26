"""Multi-asset Chat API planning and failure-isolation tests."""
from copy import deepcopy
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import src.api.main as api
from src.tools.data_loader import load_telemetry_rows


QUERY="Give me the complete action plan for this week across M-104, P-207 and C-301 — what do I need to do and by when?"


@pytest.fixture
def client(): return TestClient(api.app,raise_server_exceptions=False)


def _fleet_snapshot():
    return {
        "M-104":{"scenario":"outer_race_fault"},
        "P-207":{"scenario":"lubrication_issue"},
        "C-301":{"scenario":"signal_dropout"},
    }


def _ask(client,message=QUERY,**extra):
    payload={"message":message,"context":{"fleet_snapshot":_fleet_snapshot()},**extra}
    return client.post("/api/chat",json=payload).json()


def test_pm_query_without_fleet_evidence_requests_clarification(client):
    data=client.post("/api/chat",json={"message":QUERY,"persona":"manager"}).json()
    assert data["intent"]=="multi_asset_plan" and data["clarification_required"]
    assert data["pipeline_log"]==[]
    assert data["clarification"]["missing_fields"]==["fleet_snapshot"]
    assert "Asset names alone are not evidence" in data["response"]
    assert "multi_asset_results" not in data


def test_pm_query_returns_every_evidenced_asset_in_order(client):
    data=_ask(client,persona="manager")
    assert data["intent"]=="multi_asset_plan" and data["clarification_required"] is False
    assert [row["display_asset_id"] for row in data["multi_asset_results"]]==["M-104","P-207","C-301"]
    assert len(data["details"])==3 and len(data["actions"])==3


def test_multi_asset_plan_has_action_and_deadline_for_each_asset(client):
    rows=_ask(client)["multi_asset_results"]
    assert all(row["action"] and row["deadline"] and row["status"] for row in rows)
    assert rows[0]["action"]=="stop and replace" and rows[0]["deadline"]=="today"
    assert rows[1]["action"]=="lubrication service"
    assert rows[2]["status"]=="data_review" and rows[2]["recommendation"] is None


def test_single_asset_signal_produces_partial_multi_asset_result(client):
    signal=deepcopy(load_telemetry_rows("outer_race_fault")[-1])
    data=client.post("/api/chat",json={"message":QUERY,"context":{"signal":signal}}).json()
    assert data["clarification_required"] is False
    rows=data["multi_asset_results"]
    assert rows[0]["display_asset_id"]=="M-104" and rows[0]["pipeline_log"]
    assert [row["status"] for row in rows[1:]]==["unavailable","unavailable"]
    assert all(log["asset"]=="M-104" for log in data["pipeline_log"])


def test_unknown_asset_does_not_suppress_evidenced_known_asset(client):
    signal=deepcopy(load_telemetry_rows("outer_race_fault")[-1])
    data=client.post("/api/chat",json={
        "message":"Plan M-104 and X-999 for this week",
        "context":{"signal":signal},
    }).json()
    assert data["intent"]=="multi_asset_plan"
    assert data["clarification_required"] is False
    rows=data["multi_asset_results"]
    assert [row["display_asset_id"] for row in rows]==["M-104","X-999"]
    assert rows[0]["status"]=="action_required" and rows[0]["pipeline_log"]
    assert rows[1]["status"]=="unavailable"
    assert rows[1]["recommendation"] is None and rows[1]["pipeline_log"]==[]
    assert rows[1]["reason"]=="no fleet evidence was supplied for this asset"
    assert "evaluated 1 of 2 requested assets" in data["response"]


def test_unknown_asset_order_is_preserved_in_partial_plan(client):
    data=_ask(client,"Plan Z-998, M-104 and P-207 for this week")
    assert [row["display_asset_id"] for row in data["multi_asset_results"]]==[
        "Z-998","M-104","P-207",
    ]
    assert data["multi_asset_results"][0]["status"]=="unavailable"


def test_duplicate_asset_mentions_are_deduplicated(client):
    data=_ask(client,"Plan M-104, P-207, M-104 and P-207 for this week")
    assert [row["display_asset_id"] for row in data["multi_asset_results"]]==["M-104","P-207"]


def test_canonical_ids_also_form_multi_asset_plan(client):
    data=_ask(client,"Give an action plan for AST_MTR_001 and AST_PMP_001")
    assert [row["asset_id"] for row in data["multi_asset_results"]]==["AST_MTR_001","AST_PMP_001"]


def test_one_asset_failure_does_not_suppress_other_results(client,monkeypatch):
    real=api._get_demo_signal
    def selective(scenario,row_index=-1):
        if scenario=="lubrication_issue": raise OSError("source unavailable")
        return real(scenario,row_index)
    monkeypatch.setattr(api,"_get_demo_signal",selective)
    rows=_ask(client)["multi_asset_results"]
    assert [row["display_asset_id"] for row in rows]==["M-104","P-207","C-301"]
    assert rows[1]["status"]=="unavailable" and rows[0]["status"]=="action_required"


def test_each_pipeline_log_entry_identifies_its_asset(client):
    log=_ask(client)["pipeline_log"]
    assert log and all(row.get("asset") in {"M-104","P-207","C-301"} for row in log)


def test_frontend_forwards_multi_asset_query_to_chat_api():
    source=Path("frontend/react-app/src/components/ChatView/ChatView.jsx").read_text(encoding="utf-8")
    assert "if (CHAT_KB[t])" not in source
    assert "conversationId: conversationIdRef.current" in source
    assert '<div className="act-cards" style={{ display: \'none\' }}>' in source


def test_main_chat_has_no_single_asset_pipeline_or_offline_shortcut():
    source=Path("frontend/react-app/src/components/ChatView/ChatView.jsx").read_text(encoding="utf-8")
    assert "runRealPipeline" not in source
    assert "ASSET_SCENARIO" not in source
    assert "patchWorkOrder" not in source
    assert "const CHAT_KB" not in source
    assert "const DFLT" not in source
    assert "askChat({" in source


def test_asset_detail_chat_also_uses_orchestrated_chat_api():
    source=Path(
        "frontend/react-app/src/components/AssetRegistryView/AssetChat.jsx"
    ).read_text(encoding="utf-8")
    assert "askChat({" in source
    assert "assetId: asset.id" in source
    assert "chatResp" not in source


def test_chat_transport_has_one_natural_language_endpoint():
    source=Path("frontend/react-app/src/api/chat.js").read_text(encoding="utf-8")
    assert "`${API}/api/chat`" in source
    assert "/api/pipeline/run" not in source


def test_topbar_live_analysis_enters_chat_orchestrator():
    source=Path("frontend/react-app/src/components/Topbar.jsx").read_text(encoding="utf-8")
    assert "new CustomEvent('dro-sq'" in source
    assert "dro-run-pipeline" not in source
