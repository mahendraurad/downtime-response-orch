"""Durable local learned-case store and lexical search adapter for Agent 8."""
import json
import re
from pathlib import Path

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
# ***********************
