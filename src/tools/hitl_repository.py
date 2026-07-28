"""Durable, atomic persistence boundary for human-in-the-loop decisions."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from src.schemas.anomaly import AnomalyEvent
from src.schemas.bearing_signal import TrustedBearingSignal
from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.knowledge import KnowledgeGuidance
from src.schemas.risk import RiskAssessment


_MODELS = {
    cls.__name__: cls for cls in (
        TrustedBearingSignal, AnomalyEvent, FaultDiagnosis,
        RiskAssessment, KnowledgeGuidance,
    )
}


def _encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {"__hitl_model__": value.__class__.__name__,
                "data": value.model_dump(mode="json")}
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported HITL payload type: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and "__hitl_model__" in value:
        model = _MODELS.get(value["__hitl_model__"])
        if model is None:
            raise ValueError(f"Unsupported persisted HITL model: {value['__hitl_model__']}")
        return model.model_validate(value["data"])
    if isinstance(value, dict):
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def serialize_payload(payload: dict) -> str:
    return json.dumps(_encode(payload), sort_keys=True, separators=(",", ":"))


def deserialize_payload(payload_json: str) -> dict:
    result = _decode(json.loads(payload_json))
    if not isinstance(result, dict):
        raise ValueError("Persisted HITL payload must be an object")
    return result


class SQLiteHITLRepository:
    """Local durable repository with atomic one-winner claim semantics."""

    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self):
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS hitl_sessions (
                    run_id TEXT PRIMARY KEY,
                    gate TEXT NOT NULL,
                    persona TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    claim_token TEXT,
                    claimed_at REAL,
                    claimed_by TEXT,
                    action TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_hitl_pending
                    ON hitl_sessions(status, gate, expires_at);
                CREATE TABLE IF NOT EXISTS hitl_decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    action TEXT NOT NULL,
                    persona TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decided_at REAL NOT NULL,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_hitl_decision_run
                    ON hitl_decisions(run_id, decided_at);
            """)

    def save_session(self, run_id: str, gate: str, persona: str, payload: dict,
                     created_at: float, expires_at: float):
        now = time.time()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                INSERT INTO hitl_sessions
                    (run_id, gate, persona, payload_json, status, created_at,
                     expires_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    gate=excluded.gate, persona=excluded.persona,
                    payload_json=excluded.payload_json, status='pending',
                    created_at=excluded.created_at, expires_at=excluded.expires_at,
                    claim_token=NULL, claimed_at=NULL, claimed_by=NULL,
                    action=NULL, updated_at=excluded.updated_at
            """, (run_id, gate, persona, serialize_payload(payload),
                  created_at, expires_at, now))
            db.commit()

    def get_session(self, run_id: str) -> Optional[dict]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM hitl_sessions WHERE run_id=?", (run_id,)).fetchone()
        return self._session_row(row) if row else None

    def claim(self, run_id: str, gate: str, persona: str, action: str,
              lease_seconds: int = 120) -> dict:
        now = time.time()
        token = uuid.uuid4().hex
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM hitl_sessions WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                db.rollback()
                return {"outcome": "not_found"}
            if row["gate"] != gate:
                db.rollback()
                return {"outcome": "wrong_gate", "actual_gate": row["gate"]}
            if row["expires_at"] <= now:
                db.execute("UPDATE hitl_sessions SET status='expired', updated_at=? WHERE run_id=?",
                           (now, run_id))
                db.commit()
                return {"outcome": "expired"}
            claim_stale = (row["status"] == "processing"
                           and float(row["claimed_at"] or 0) + lease_seconds <= now)
            if row["status"] == "resolved":
                db.rollback()
                return {"outcome": "resolved"}
            if row["status"] == "processing" and not claim_stale:
                db.rollback()
                return {"outcome": "processing"}
            if row["status"] not in {"pending", "processing"}:
                db.rollback()
                return {"outcome": row["status"]}
            db.execute("""
                UPDATE hitl_sessions SET status='processing', claim_token=?,
                    claimed_at=?, claimed_by=?, action=?, updated_at=?
                WHERE run_id=?
            """, (token, now, persona, action, now, run_id))
            db.commit()
        return {"outcome": "claimed", "claim_token": token,
                "payload": deserialize_payload(row["payload_json"])}

    def complete(self, run_id: str, claim_token: str, gate: str, action: str,
                 persona: str, rationale: str = "", result: Optional[dict] = None) -> bool:
        now = time.time()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""
                UPDATE hitl_sessions SET status='resolved', updated_at=?
                WHERE run_id=? AND status='processing' AND claim_token=?
            """, (now, run_id, claim_token)).rowcount
            if changed != 1:
                db.rollback()
                return False
            db.execute("""
                INSERT INTO hitl_decisions
                    (decision_id, run_id, gate, action, persona, rationale,
                     status, decided_at, result_json)
                VALUES (?, ?, ?, ?, ?, ?, 'resolved', ?, ?)
            """, (uuid.uuid4().hex, run_id, gate, action, persona,
                  rationale, now, json.dumps(result, sort_keys=True) if result else None))
            db.commit()
        return True

    def release(self, run_id: str, claim_token: str) -> bool:
        with closing(self._connect()) as db:
            result = db.execute("""
                UPDATE hitl_sessions SET status='pending', claim_token=NULL,
                    claimed_at=NULL, claimed_by=NULL, action=NULL, updated_at=?
                WHERE run_id=? AND status='processing' AND claim_token=?
            """, (time.time(), run_id, claim_token))
            db.commit()
            return result.rowcount == 1

    def list_pending(self, persona: str = "", gate: str = "") -> list[dict]:
        sql = "SELECT * FROM hitl_sessions WHERE status='pending' AND expires_at>?"
        args: list[Any] = [time.time()]
        if gate:
            sql += " AND gate=?"; args.append(gate)
        sql += " ORDER BY created_at"
        with closing(self._connect()) as db:
            rows = db.execute(sql, args).fetchall()
        return [self._session_row(row, include_payload=False) for row in rows]

    def decisions(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT decision_id,run_id,gate,action,persona,rationale,status,decided_at
                FROM hitl_decisions WHERE run_id=? ORDER BY decided_at
            """, (run_id,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _session_row(row, include_payload=True):
        result = {key: row[key] for key in (
            "run_id", "gate", "persona", "status", "created_at", "expires_at",
            "claimed_at", "claimed_by", "action", "updated_at",
        )}
        if include_payload:
            result["payload"] = deserialize_payload(row["payload_json"])
        return result


class PostgreSQLHITLRepository:
    """Azure PostgreSQL implementation. Requires psycopg[binary]."""

    def __init__(self, connection_string: str):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary] to use PostgreSQL HITL persistence") from exc
        self._psycopg = psycopg
        self.connection_string = connection_string
        self._initialize()

    def _connect(self):
        return self._psycopg.connect(self.connection_string)

    def _initialize(self):
        migration = Path(__file__).resolve().parents[2] / "migrations" / "001_hitl_postgres.sql"
        with self._connect() as db:
            with db.cursor() as cursor:
                cursor.execute(migration.read_text(encoding="utf-8"))
            db.commit()

    def save_session(self, run_id, gate, persona, payload, created_at, expires_at):
        now = time.time()
        with self._connect() as db:
            with db.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO hitl_sessions
                        (run_id,gate,persona,payload_json,status,created_at,expires_at,updated_at)
                    VALUES (%s,%s,%s,%s,'pending',to_timestamp(%s),to_timestamp(%s),to_timestamp(%s))
                    ON CONFLICT(run_id) DO UPDATE SET gate=EXCLUDED.gate,
                        persona=EXCLUDED.persona,payload_json=EXCLUDED.payload_json,
                        status='pending',created_at=EXCLUDED.created_at,
                        expires_at=EXCLUDED.expires_at,claim_token=NULL,
                        claimed_at=NULL,claimed_by=NULL,action=NULL,updated_at=EXCLUDED.updated_at
                """, (run_id, gate, persona, serialize_payload(payload), created_at, expires_at, now))
            db.commit()

    def get_session(self, run_id):
        with self._connect() as db:
            with db.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute("SELECT * FROM hitl_sessions WHERE run_id=%s", (run_id,))
                row = cursor.fetchone()
        if not row:
            return None
        row["payload"] = deserialize_payload(row.pop("payload_json"))
        for key in ("created_at", "expires_at", "claimed_at", "updated_at"):
            if row.get(key) is not None:
                row[key] = row[key].timestamp()
        return row

    def claim(self, run_id, gate, persona, action, lease_seconds=120):
        now = time.time(); token = uuid.uuid4().hex
        with self._connect() as db:
            with db.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute("SELECT * FROM hitl_sessions WHERE run_id=%s FOR UPDATE", (run_id,))
                row = cursor.fetchone()
                if not row: return {"outcome": "not_found"}
                if row["gate"] != gate: return {"outcome": "wrong_gate", "actual_gate": row["gate"]}
                if row["expires_at"].timestamp() <= now:
                    cursor.execute("UPDATE hitl_sessions SET status='expired',updated_at=NOW() WHERE run_id=%s",(run_id,))
                    db.commit(); return {"outcome": "expired"}
                stale = (row["status"]=="processing" and row["claimed_at"]
                         and row["claimed_at"].timestamp()+lease_seconds<=now)
                if row["status"]=="resolved": return {"outcome":"resolved"}
                if row["status"]=="processing" and not stale: return {"outcome":"processing"}
                cursor.execute("""UPDATE hitl_sessions SET status='processing',claim_token=%s,
                    claimed_at=NOW(),claimed_by=%s,action=%s,updated_at=NOW() WHERE run_id=%s
                """,(token,persona,action,run_id))
            db.commit()
        return {"outcome":"claimed","claim_token":token,
                "payload":deserialize_payload(row["payload_json"])}

    def complete(self, run_id, claim_token, gate, action, persona, rationale="", result=None):
        with self._connect() as db:
            with db.cursor() as cursor:
                cursor.execute("""UPDATE hitl_sessions SET status='resolved',updated_at=NOW()
                    WHERE run_id=%s AND status='processing' AND claim_token=%s
                """,(run_id,claim_token))
                if cursor.rowcount!=1: db.rollback(); return False
                cursor.execute("""INSERT INTO hitl_decisions
                    (decision_id,run_id,gate,action,persona,rationale,status,decided_at,result_json)
                    VALUES (%s,%s,%s,%s,%s,%s,'resolved',NOW(),%s)
                """,(uuid.uuid4().hex,run_id,gate,action,persona,rationale,
                     json.dumps(result,sort_keys=True) if result else None))
            db.commit()
        return True

    def release(self, run_id, claim_token):
        with self._connect() as db:
            with db.cursor() as cursor:
                cursor.execute("""UPDATE hitl_sessions SET status='pending',claim_token=NULL,
                    claimed_at=NULL,claimed_by=NULL,action=NULL,updated_at=NOW()
                    WHERE run_id=%s AND status='processing' AND claim_token=%s
                """,(run_id,claim_token))
                changed=cursor.rowcount
            db.commit()
        return changed==1

    def list_pending(self, persona="", gate=""):
        query="SELECT run_id,gate,persona,status,created_at,expires_at,claimed_at,claimed_by,action,updated_at FROM hitl_sessions WHERE status='pending' AND expires_at>NOW()"
        args=[]
        if gate: query+=" AND gate=%s"; args.append(gate)
        query+=" ORDER BY created_at"
        with self._connect() as db:
            with db.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute(query,args); rows=cursor.fetchall()
        for row in rows:
            for key in ("created_at","expires_at","claimed_at","updated_at"):
                if row.get(key) is not None: row[key]=row[key].timestamp()
        return rows

    def decisions(self, run_id):
        with self._connect() as db:
            with db.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute("""SELECT decision_id,run_id,gate,action,persona,rationale,status,
                    EXTRACT(EPOCH FROM decided_at) AS decided_at
                    FROM hitl_decisions WHERE run_id=%s ORDER BY decided_at""",(run_id,))
                return cursor.fetchall()


def build_hitl_repository(config: dict):
    hitl = config.get("hitl", {})
    postgres_url = os.getenv("POSTGRES_URL", "").strip()
    backend = os.getenv(
        "HITL_REPOSITORY", str(hitl.get("repository_backend", "auto"))
    ).strip().lower()
    if backend not in {"auto", "sqlite", "postgres"}:
        raise RuntimeError("HITL_REPOSITORY must be auto, sqlite, or postgres")
    if backend == "postgres" or (backend == "auto" and postgres_url):
        if not postgres_url:
            raise RuntimeError("POSTGRES_URL is required for PostgreSQL HITL persistence")
        return PostgreSQLHITLRepository(postgres_url)
    return SQLiteHITLRepository(hitl.get("sqlite_path", "data/hitl_sessions.db"))
