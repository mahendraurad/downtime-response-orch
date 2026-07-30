"""Durability, concurrency, expiry, recovery, and audit tests for HITL."""
from concurrent.futures import ThreadPoolExecutor
import time

from src.schemas.risk import RiskAssessment
from src.tools.hitl_repository import (
    SQLiteHITLRepository,
    deserialize_payload,
    serialize_payload,
)


def _save(repo, run_id="RUN-1", gate="advisory", expires_in=60):
    now=time.time()
    payload={
        "type":gate,
        "persona":"engineer",
        "risk_assessment":RiskAssessment(
            asset_id="M-104",
            advisory_note="Inspect within one shift.",
            assessment_source="rules+llm_fallback",
        ),
    }
    repo.save_session(run_id,gate,"engineer",payload,now,now+expires_in)
    return payload


def test_typed_payload_round_trip_preserves_model():
    payload={"risk":RiskAssessment(asset_id="M-104",risk_level="high")}
    restored=deserialize_payload(serialize_payload(payload))
    assert isinstance(restored["risk"],RiskAssessment)
    assert restored["risk"].asset_id=="M-104"


def test_restart_recovers_pending_session(tmp_path):
    path=tmp_path / "hitl.db"
    first=SQLiteHITLRepository(path)
    _save(first)
    restarted=SQLiteHITLRepository(path)
    session=restarted.get_session("RUN-1")
    assert session["status"]=="pending"
    assert session["payload"]["risk_assessment"].asset_id=="M-104"


def test_concurrent_claim_has_exactly_one_winner(tmp_path):
    repo=SQLiteHITLRepository(tmp_path / "hitl.db")
    _save(repo)

    def claim(persona):
        return repo.claim("RUN-1","advisory",persona,"ACCEPT",120)["outcome"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(claim,("engineer","supervisor")))
    assert outcomes.count("claimed")==1
    assert outcomes.count("processing")==1


def test_completion_is_durable_and_replay_is_rejected(tmp_path):
    repo=SQLiteHITLRepository(tmp_path / "hitl.db")
    _save(repo)
    claim=repo.claim("RUN-1","advisory","engineer","ACCEPT")
    assert repo.complete("RUN-1",claim["claim_token"],"advisory","ACCEPT",
                         "engineer","Reviewed evidence")
    assert repo.get_session("RUN-1")["status"]=="resolved"
    assert repo.claim("RUN-1","advisory","engineer","ACCEPT")["outcome"]=="resolved"
    decisions=repo.decisions("RUN-1")
    assert len(decisions)==1 and decisions[0]["rationale"]=="Reviewed evidence"


def test_released_failed_claim_can_be_retried(tmp_path):
    repo=SQLiteHITLRepository(tmp_path / "hitl.db")
    _save(repo)
    claim=repo.claim("RUN-1","advisory","engineer","ACCEPT")
    assert repo.release("RUN-1",claim["claim_token"])
    retry=repo.claim("RUN-1","advisory","engineer","REJECT")
    assert retry["outcome"]=="claimed"


def test_expired_session_cannot_be_claimed(tmp_path):
    repo=SQLiteHITLRepository(tmp_path / "hitl.db")
    _save(repo,expires_in=-1)
    assert repo.claim("RUN-1","advisory","engineer","ACCEPT")["outcome"]=="expired"
    assert repo.get_session("RUN-1")["status"]=="expired"
    assert repo.list_pending()==[]


def test_wrong_gate_does_not_consume_session(tmp_path):
    repo=SQLiteHITLRepository(tmp_path / "hitl.db")
    _save(repo)
    result=repo.claim("RUN-1","diagnosis","engineer","CONFIRM")
    assert result=={"outcome":"wrong_gate","actual_gate":"advisory"}
    assert repo.get_session("RUN-1")["status"]=="pending"


def test_stale_processing_lease_can_be_reclaimed(tmp_path,monkeypatch):
    repo=SQLiteHITLRepository(tmp_path / "hitl.db")
    _save(repo)
    first=repo.claim("RUN-1","advisory","engineer","ACCEPT",lease_seconds=1)
    assert first["outcome"]=="claimed"
    original=time.time
    monkeypatch.setattr("src.tools.hitl_repository.time.time",lambda:original()+2)
    second=repo.claim("RUN-1","advisory","supervisor","REJECT",lease_seconds=1)
    assert second["outcome"]=="claimed"
    assert second["claim_token"]!=first["claim_token"]
