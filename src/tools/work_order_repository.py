"""Runtime repository for frontend-visible work orders."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from src.tools.postgres_runtime import ensure_runtime_tables, use_postgres_tables


class InMemoryWorkOrderRepository:
    """Process-local fallback used when Postgres is not configured."""
    def __init__(self):
        self._rows: list[dict] = []

    def list_all(self) -> list[dict]:
        return [deepcopy(row) for row in self._rows]

    def get(self, work_order_id: str) -> Optional[dict]:
        row = next((row for row in self._rows if row.get("id") == work_order_id), None)
        return deepcopy(row) if row else None

    def upsert(self, work_order: dict) -> dict:
        stored = deepcopy(work_order)
        for index, row in enumerate(self._rows):
            if row.get("id") == stored.get("id"):
                self._rows[index] = stored
                return deepcopy(stored)
        self._rows.append(stored)
        return deepcopy(stored)

    def update(self, work_order_id: str, changes: dict[str, Any]) -> Optional[dict]:
        current = self.get(work_order_id)
        if current is None:
            return None
        current.update(deepcopy(changes))
        return self.upsert(current)


class PostgreSQLWorkOrderRepository:
    """PostgreSQL store for work orders shown and edited by the frontend."""
    def __init__(self):
        ensure_runtime_tables(("work_orders",))

    def list_all(self) -> list[dict]:
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT payload_json::text FROM work_orders ORDER BY updated_at DESC, id"
                )
                rows = cursor.fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, work_order_id: str) -> Optional[dict]:
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT payload_json::text FROM work_orders WHERE id=%s",
                    (work_order_id,),
                )
                row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def upsert(self, work_order: dict) -> dict:
        stored = deepcopy(work_order)
        now = datetime.now(timezone.utc).isoformat()
        stored.setdefault("updated_at", now)
        from src.tools.postgres_runtime import connect
        with connect() as db:
            with db.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO work_orders(id,status,asset_id,updated_at,payload_json) "
                    "VALUES (%s,%s,%s,%s,%s::jsonb) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "status=EXCLUDED.status, asset_id=EXCLUDED.asset_id, "
                    "updated_at=EXCLUDED.updated_at, payload_json=EXCLUDED.payload_json",
                    (
                        stored.get("id", ""),
                        stored.get("status", ""),
                        stored.get("asset_id") or stored.get("as") or "",
                        stored.get("updated_at"),
                        json.dumps(stored, sort_keys=True),
                    ),
                )
            db.commit()
        return deepcopy(stored)

    def update(self, work_order_id: str, changes: dict[str, Any]) -> Optional[dict]:
        current = self.get(work_order_id)
        if current is None:
            return None
        current.update(deepcopy(changes))
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self.upsert(current)


def build_work_order_repository():
    if use_postgres_tables():
        return PostgreSQLWorkOrderRepository()
    return InMemoryWorkOrderRepository()
