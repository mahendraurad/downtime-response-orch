"""Durable local learned-case store and lexical search adapter for Agent 8."""
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

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
    def list_recent(self, limit=3):
        """Return newest valid learned cases without exposing mutable store rows."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        rows = [row for row in self._rows() if isinstance(row, dict)]
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

    def consecutive_rejections(self, asset_id, fault_mode, now, days=30):
        cutoff = now - timedelta(days=days)
        relevant = []
        for sequence, row in enumerate(self._rows()):
            if row.get("asset_id") != asset_id or row.get("fault_mode") != fault_mode:
                continue
            try:
                timestamp = datetime.fromisoformat(
                    str(row.get("decided_at", "")).replace("Z", "+00:00")
                )
            except ValueError:
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
# ***********************
