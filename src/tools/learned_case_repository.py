"""Durable local learned-case store and lexical search adapter for Agent 8."""
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src.tools.postgres_runtime import ensure_runtime_tables, use_postgres_tables

# ************** Added by Prateek Mittal on 20th July 2026 ******************
class JSONLearnedCaseRepository:
    def __init__(self, path): self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
    def _rows(self):
        if not self.path.exists(): return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            return rows if isinstance(rows, list) else []
        except (json.JSONDecodeError, OSError): return []
    def exists(self, case_id): return any(r.get("case_id") == case_id for r in self._rows())
    def save(self, document):
        rows = self._rows()
        if any(r.get("case_id") == document.case_id for r in rows): raise ValueError("learned case already exists")
        rows.append(document.to_dict()); self.path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    def get(self, case_id): return next((r for r in self._rows() if r.get("case_id") == case_id), None)
    def count(self): return len(self._rows())
    def list_recent(self, limit=3, asset_id=""):
        """Return newest valid learned cases without exposing mutable store rows."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        rows = [row for row in self._rows() if isinstance(row, dict)]
        if asset_id:
            rows = [row for row in rows if (
                row.get("asset_id") == asset_id
                or (not row.get("asset_id") and asset_id in str(row.get("content", "")))
            )]
        rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        return [dict(row) for row in rows[:limit]]
    def search(self, query, top_k=5, minimum_score=0.15, **_):
        terms = set(re.findall(r"[a-z0-9]+", str(query).lower()))
        hits = []
        for row in self._rows():
            words = set(re.findall(r"[a-z0-9]+", (row.get("content", "") + " " + row.get("fault_mode", "")).lower()))
            score = len(terms & words) / max(1, len(terms))
            if score >= minimum_score:
                hits.append({"source": f"Learned case {row['case_id']}", "text": row["content"],
                    "score": score, "fault_mode": row.get("fault_mode", ""),
                    "asset_type": row.get("asset_type", ""), "iso_stage": row.get("iso_stage", 0)})
        return sorted(hits, key=lambda h: h["score"], reverse=True)[:top_k]

    def search_cases(self, asset_id="", fault_mode="", top_k=3):
        """Return cited case records ranked by exact fault/asset evidence."""
        hits = []
        for row in self._rows():
            if not isinstance(row, dict):
                continue
            fault_match = bool(fault_mode and row.get("fault_mode") == fault_mode)
            asset_match = bool(asset_id and (
                row.get("asset_id") == asset_id
                or asset_id in str(row.get("content", ""))
            ))
            if fault_mode and not fault_match:
                continue
            score = (0.7 if fault_match else 0.0) + (0.3 if asset_match else 0.0)
            hits.append({
                "case_id": str(row.get("case_id", "")),
                "source": f"Learned case {row.get('case_id', '')}",
                "fault_mode": str(row.get("fault_mode", "")),
                "action_taken": _action_from_row(row),
                "outcome": str(row.get("outcome", "")),
                "recorded_at": str(row.get("created_at", "")),
                "relevance_score": round(score, 4),
            })
        hits.sort(
            key=lambda item: (item["relevance_score"], item["recorded_at"]),
            reverse=True,
        )
        return hits[:max(0, int(top_k))]


class PostgreSQLLearnedCaseRepository:
    """PostgreSQL store for validated Agent 8 outcome memories."""
    def __init__(self):
        ensure_runtime_tables(("learned_cases",))
    def _payload_rows(self, where="", args=()):
        from src.tools.postgres_runtime import connect
        sql = "SELECT payload_json::text FROM learned_cases"
        if where:
            sql += f" WHERE {where}"
        sql += " ORDER BY created_at DESC NULLS LAST"
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(sql, args)
                rows = cursor.fetchall()
        return [json.loads(row[0]) for row in rows]
    def exists(self, case_id):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT 1 FROM learned_cases WHERE case_id=%s", (case_id,))
                return cursor.fetchone() is not None
    def save(self, document):
        payload = document.to_dict()
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO learned_cases(case_id,asset_id,fault_mode,created_at,payload_json) "
                    "VALUES (%s,%s,%s,%s,%s::jsonb)",
                    (
                        document.case_id,
                        document.asset_id,
                        document.fault_mode,
                        document.created_at or None,
                        json.dumps(payload, sort_keys=True),
                    ),
                )
            db.commit()
    def get(self, case_id):
        rows = self._payload_rows("case_id=%s", (case_id,))
        return rows[0] if rows else None
    def count(self):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM learned_cases")
                return int(cursor.fetchone()[0])
    def list_recent(self, limit=3, asset_id=""):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        where, args = "", ()
        if asset_id:
            where, args = "asset_id=%s", (asset_id,)
        return self._payload_rows(where, args)[:limit]
    def search(self, query, top_k=5, minimum_score=0.15, **_):
        terms = set(re.findall(r"[a-z0-9]+", str(query).lower()))
        hits = []
        for row in self._payload_rows():
            words = set(re.findall(
                r"[a-z0-9]+",
                (row.get("content", "") + " " + row.get("fault_mode", "")).lower(),
            ))
            score = len(terms & words) / max(1, len(terms))
            if score >= minimum_score:
                hits.append({"source": f"Learned case {row['case_id']}",
                    "text": row["content"], "score": score,
                    "fault_mode": row.get("fault_mode", ""),
                    "asset_type": row.get("asset_type", ""),
                    "iso_stage": row.get("iso_stage", 0)})
        return sorted(hits, key=lambda h: h["score"], reverse=True)[:top_k]
    def search_cases(self, asset_id="", fault_mode="", top_k=3):
        rows = self._payload_rows()
        hits = []
        for row in rows:
            fault_match = bool(fault_mode and row.get("fault_mode") == fault_mode)
            asset_match = bool(asset_id and (
                row.get("asset_id") == asset_id
                or asset_id in str(row.get("content", ""))
            ))
            if fault_mode and not fault_match:
                continue
            score = (0.7 if fault_match else 0.0) + (0.3 if asset_match else 0.0)
            hits.append({
                "case_id": str(row.get("case_id", "")),
                "source": f"Learned case {row.get('case_id', '')}",
                "fault_mode": str(row.get("fault_mode", "")),
                "action_taken": _action_from_row(row),
                "outcome": str(row.get("outcome", "")),
                "recorded_at": str(row.get("created_at", "")),
                "relevance_score": round(score, 4),
            })
        hits.sort(key=lambda item: (item["relevance_score"], item["recorded_at"]), reverse=True)
        return hits[:max(0, int(top_k))]


def _action_from_row(row):
    tags = row.get("tags", [])
    if isinstance(tags, list) and len(tags) > 1:
        return str(tags[1])
    outcome = str(row.get("outcome", ""))
    return outcome.split(":", 1)[0] if ":" in outcome else ""


class JSONRejectionRepository:
    """Durable decision sequence used for the 30-day double-rejection rule."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rows(self):
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            return rows if isinstance(rows, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def append(self, row):
        rows = self._rows()
        rows.append(dict(row))
        self.path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    def exists_decision(self, case_id, decision):
        return any(
            isinstance(row, dict)
            and row.get("case_id") == case_id
            and row.get("decision") == decision
            for row in self._rows()
        )

    def list_recent(self, limit=3, asset_id="", decision="rejected"):
        """Return newest matching decision records without mutable store rows."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        rows = [row for row in self._rows() if isinstance(row, dict)]
        if decision:
            rows = [row for row in rows if row.get("decision") == decision]
        if asset_id:
            rows = [row for row in rows if row.get("asset_id") == asset_id]
        rows.sort(key=lambda row: str(row.get("decided_at", "")), reverse=True)
        return [dict(row) for row in rows[:limit]]

    def update_decision(self, case_id, decision, changes):
        rows = self._rows()
        updated = False
        for row in reversed(rows):
            if (isinstance(row, dict) and row.get("case_id") == case_id
                    and row.get("decision") == decision):
                row.update(dict(changes))
                updated = True
                break
        if updated:
            self.path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return updated

    def consecutive_rejections(self, asset_id, fault_mode, now, days=30):
        cutoff = now - timedelta(days=days)
        relevant = []
        for sequence, row in enumerate(self._rows()):
            if not isinstance(row, dict):
                continue
            if row.get("asset_id") != asset_id or row.get("fault_mode") != fault_mode:
                continue
            try:
                timestamp = datetime.fromisoformat(
                    str(row.get("decided_at", "")).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
            if timestamp.tzinfo is None:
                continue
            if timestamp >= cutoff:
                relevant.append((timestamp, sequence, row))
        # JSON array order is the durable tie-breaker when two decisions share
        # the same timestamp. The most recently appended decision must win.
        relevant.sort(key=lambda item: (item[0], item[1]), reverse=True)
        count = 0
        for _, _, row in relevant:
            if row.get("decision") != "rejected":
                break
            count += 1
        return count


class PostgreSQLRejectionRepository:
    """PostgreSQL store for Agent 8 approval/rejection decision learning."""
    def __init__(self):
        ensure_runtime_tables(("rejection_feedback",))
    def append(self, row):
        payload = dict(row)
        decided_at = payload.get("decided_at") or payload.get("rejected_at")
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO rejection_feedback "
                    "(case_id,asset_id,fault_mode,decision,decided_at,payload_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s::jsonb) "
                    "ON CONFLICT(case_id, decision) DO NOTHING",
                    (
                        payload.get("case_id", ""),
                        payload.get("asset_id", ""),
                        payload.get("fault_mode", ""),
                        payload.get("decision", ""),
                        decided_at,
                        json.dumps(payload, sort_keys=True),
                    ),
                )
            db.commit()
    def exists_decision(self, case_id, decision):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM rejection_feedback WHERE case_id=%s AND decision=%s",
                    (case_id, decision),
                )
                return cursor.fetchone() is not None
    def list_recent(self, limit=3, asset_id="", decision="rejected"):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        args = []
        where = []
        if decision:
            where.append("decision=%s"); args.append(decision)
        if asset_id:
            where.append("asset_id=%s"); args.append(asset_id)
        sql = "SELECT payload_json::text FROM rejection_feedback"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY decided_at DESC, rejection_id DESC LIMIT %s"
        args.append(limit)
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(sql, args)
                rows = cursor.fetchall()
        return [json.loads(row[0]) for row in rows]
    def update_decision(self, case_id, decision, changes):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT payload_json::text FROM rejection_feedback "
                    "WHERE case_id=%s AND decision=%s ORDER BY rejection_id DESC LIMIT 1",
                    (case_id, decision),
                )
                row = cursor.fetchone()
                if not row:
                    return False
                payload = json.loads(row[0]); payload.update(dict(changes))
                cursor.execute(
                    "UPDATE rejection_feedback SET payload_json=%s::jsonb "
                    "WHERE case_id=%s AND decision=%s",
                    (json.dumps(payload, sort_keys=True), case_id, decision),
                )
            db.commit()
        return True
    def consecutive_rejections(self, asset_id, fault_mode, now, days=30):
        cutoff = now - timedelta(days=days)
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT decision, EXTRACT(EPOCH FROM decided_at) "
                    "FROM rejection_feedback "
                    "WHERE asset_id=%s AND fault_mode=%s AND decided_at>=%s "
                    "ORDER BY decided_at DESC, rejection_id DESC",
                    (asset_id, fault_mode, cutoff),
                )
                rows = cursor.fetchall()
        count = 0
        for decision, _ in rows:
            if decision != "rejected":
                break
            count += 1
        return count


class JSONChatLearningRepository:
    """Durable Agent 8 memory for useful chat observations."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rows(self):
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            return rows if isinstance(rows, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def exists_fingerprint(self, fingerprint):
        return any(
            isinstance(row, dict) and row.get("fingerprint") == fingerprint
            for row in self._rows()
        )

    def save(self, row):
        rows = self._rows()
        fingerprint = row.get("fingerprint")
        if fingerprint and any(
            isinstance(existing, dict)
            and existing.get("fingerprint") == fingerprint
            for existing in rows
        ):
            raise ValueError("chat observation already exists")
        rows.append(dict(row))
        self.path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    def list_recent(self, limit=3, asset_id="", learning_kind=""):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        rows = [row for row in self._rows() if isinstance(row, dict)]
        if asset_id:
            rows = [
                row for row in rows
                if asset_id in row.get("asset_ids", [])
                or row.get("asset_id") == asset_id
            ]
        if learning_kind:
            rows = [
                row for row in rows
                if row.get("learning_kind") == learning_kind
            ]
        rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        return [dict(row) for row in rows[:limit]]


class PostgreSQLChatLearningRepository:
    """PostgreSQL store for useful Agent 8 chat observations."""
    def __init__(self):
        ensure_runtime_tables(("chat_learning_observations",))
    def exists_fingerprint(self, fingerprint):
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM chat_learning_observations WHERE fingerprint=%s",
                    (fingerprint,),
                )
                return cursor.fetchone() is not None
    def save(self, row):
        payload = dict(row)
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO chat_learning_observations "
                    "(observation_id,fingerprint,asset_id,asset_ids,learning_kind,intent,created_at,payload_json) "
                    "VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb)",
                    (
                        payload.get("observation_id", ""),
                        payload.get("fingerprint", ""),
                        payload.get("asset_id", ""),
                        json.dumps(payload.get("asset_ids", [])),
                        payload.get("learning_kind", ""),
                        payload.get("intent", ""),
                        payload.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        json.dumps(payload, sort_keys=True),
                    ),
                )
            db.commit()
    def list_recent(self, limit=3, asset_id="", learning_kind=""):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        args = []
        where = []
        if asset_id:
            where.append("(asset_id=%s OR asset_ids ? %s)")
            args.extend([asset_id, asset_id])
        if learning_kind:
            where.append("learning_kind=%s"); args.append(learning_kind)
        sql = "SELECT payload_json::text FROM chat_learning_observations"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT %s"
        args.append(limit)
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(sql, args)
                rows = cursor.fetchall()
        return [json.loads(row[0]) for row in rows]


def build_learned_case_repository(json_path):
    if use_postgres_tables():
        return PostgreSQLLearnedCaseRepository()
    return JSONLearnedCaseRepository(json_path)


def build_rejection_repository(json_path):
    if use_postgres_tables():
        return PostgreSQLRejectionRepository()
    return JSONRejectionRepository(json_path)


def build_chat_learning_repository(json_path):
    if use_postgres_tables():
        return PostgreSQLChatLearningRepository()
    return JSONChatLearningRepository(json_path)
# ***********************
