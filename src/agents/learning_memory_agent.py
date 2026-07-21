"""
agents/learning_memory_agent.py  —  Phase 10

Learning & Memory Agent: transforms confirmed execution outcomes into
retrievable institutional knowledge.

Pipeline (adapted from github.com/mahendraurad/downtime-response-orch
 branch Learning_and_Memory_Agent, without FAISS / Azure OpenAI):

  Step 1 — feedback_capture   Validate + enrich feedback dict
  Step 2 — retrieval           TF-IDF search in lma_case_store
  Step 3 — coverage_assessment Heuristic 5-dimension evaluation
  Step 4 — routing             PATH A (existing) / B (new) / C (partial)
  Step 5 — action
      PATH A → cite matched case, no write
      PATH B → generate LearnedCaseDocument, persist to store
      PATH C → surface gaps, flag for enrichment, no write yet

Returns a dict consumed by the executor endpoint and the frontend card.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.execution import ExecutionResult
from src.schemas.feedback import FeedbackEvent, LearnedCaseDocument

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.70   # score >= this + fault match → PATH A
_PARTIAL_THRESHOLD    = 0.35   # score >= this + fault match → PATH C


class LearningMemoryAgent:
    """Phase 10 — Learning & Memory Agent."""

    # ── Public entry point ────────────────────────────────────────────────────

    def process(
        self,
        execution: ExecutionResult,
        recommendation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run the learning pipeline and return a result dict:
          path             "A" | "B" | "C"
          case_id          ID written (PATH B) or None
          matched_case_id  ID matched (PATH A/C) or None
          similarity_score top candidate score (0.0 if no candidates)
          document         LearnedCaseDocument dict (PATH B) or None
          summary          human-readable outcome sentence
          dimensions       coverage assessment detail
        """
        feedback = self._build_feedback(execution, recommendation)
        candidates = self._retrieve(feedback)
        path, best = self._assess_and_route(feedback, candidates)

        if path == "A":
            return self._path_a(feedback, best)
        if path == "C":
            return self._path_c(feedback, best)
        return self._path_b(feedback)

    # ── Step 1: build feedback dict ───────────────────────────────────────────

    @staticmethod
    def _build_feedback(
        execution: ExecutionResult,
        rec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge ExecutionResult + recommendation into a uniform feedback dict."""
        rec_action = rec.get("recommended_action") or {}
        if isinstance(rec_action, dict):
            action_name = rec_action.get("name") or rec_action.get("action", "")
        else:
            action_name = getattr(rec_action, "name", "")

        parts = rec.get("required_parts") or []
        bearing_type = ""
        for p in parts:
            pn = (p.get("part_number") or p.get("part_model", "")) if isinstance(p, dict) else ""
            if pn:
                bearing_type = _extract_bearing_model(pn)
                break
        if not bearing_type:
            bearing_type = rec.get("bearing_type", "SKF6310")

        return {
            "case_id":           execution.case_id or rec.get("case_id", ""),
            "asset_id":          rec.get("asset_id", ""),
            "asset_type":        rec.get("asset_type", "motor"),
            "bearing_type":      bearing_type,
            "fault_mode":        rec.get("fault_mode", ""),
            "iso_stage":         rec.get("iso_stage", 0),
            "urgency":           rec.get("urgency", ""),
            "action_taken":      action_name,
            "work_order_id":     execution.work_order_id,
            "execution_status":  execution.status,
            "recommendation_id": rec.get("recommendation_id", ""),
            "rationale":         rec.get("rationale", ""),
        }

    # ── Step 2: retrieval ─────────────────────────────────────────────────────

    @staticmethod
    def _retrieve(feedback: Dict[str, Any]) -> List[Dict]:
        from src.tools.lma_case_store import search
        query = _build_query(feedback)
        return search(
            query,
            fault_mode=feedback.get("fault_mode", ""),
            asset_type=feedback.get("asset_type", ""),
            top_k=3,
        )

    # ── Step 3 + 4: coverage assessment + routing ────────────────────────────

    @staticmethod
    def _assess_and_route(
        feedback: Dict[str, Any],
        candidates: List[Dict],
    ) -> Tuple[str, Optional[Dict]]:
        """
        Heuristic coverage assessment — no LLM required.

        Dimensions checked:
          D1 fault_mode match
          D2 asset_type match
          D3 action_taken present in matched case content
          D4 outcome / result present
          D5 lessons / lesson present in matched case content
        """
        if not candidates:
            return "B", None

        best = candidates[0]
        score = best["score"]
        doc   = best["doc"]

        fault_match = doc.get("fault_mode", "") == feedback.get("fault_mode", "")
        if not fault_match:
            return "B", None

        if score < _PARTIAL_THRESHOLD:
            return "B", None

        action = (feedback.get("action_taken") or "").lower()
        content = (doc.get("content") or "").lower()
        d3 = bool(action) and (action.replace("_", " ") in content or action in content)
        d4 = "outcome" in content or "result" in content or "success" in content
        d5 = "lesson" in content or "recommend" in content

        if score >= _SIMILARITY_THRESHOLD and d3 and d4 and d5:
            return "A", best    # all dimensions complete
        return "C", best        # partial — some gaps

    # ── Path A: existing knowledge ────────────────────────────────────────────

    @staticmethod
    def _path_a(feedback: Dict, best: Dict) -> Dict[str, Any]:
        doc    = best["doc"]
        cid    = doc.get("case_id", "")
        score  = best["score"]
        dims   = _dim_names_all()
        summary = (
            f"Existing institutional knowledge matched (case {cid}, "
            f"similarity {score:.2f}). Fault mode: {doc.get('fault_mode','—')}, "
            f"asset: {doc.get('asset_type','—')}. "
            f"No new case written — retrieval path active."
        )
        logger.info("[lma][path_A] matched=%s score=%.3f", cid, score)
        return {
            "path":             "A",
            "case_id":          None,
            "matched_case_id":  cid,
            "similarity_score": round(score, 3),
            "document":         None,
            "summary":          summary,
            "dimensions":       {d: "COMPLETE" for d in dims},
        }

    # ── Path C: partial match ─────────────────────────────────────────────────

    @staticmethod
    def _path_c(feedback: Dict, best: Dict) -> Dict[str, Any]:
        doc    = best["doc"]
        cid    = doc.get("case_id", "")
        score  = best["score"]
        content = (doc.get("content") or "").lower()
        action  = (feedback.get("action_taken") or "").lower().replace("_", " ")
        gaps: List[str] = []
        if not (action and action in content):
            gaps.append("action_and_outcome")
        if "lesson" not in content and "recommend" not in content:
            gaps.append("lessons_and_future_reference")
        if not gaps:
            gaps.append("post_repair_validation")

        dims: Dict[str, str] = {d: "COMPLETE" for d in _dim_names_all()}
        for g in gaps:
            dims[g] = "MISSING"

        gap_text = ", ".join(g.replace("_", " ") for g in gaps)
        summary = (
            f"Partial knowledge match found (case {cid}, similarity {score:.2f}). "
            f"Fault mode and asset type align, but gaps identified: {gap_text}. "
            f"Case flagged for human enrichment — no new case written."
        )
        logger.info("[lma][path_C] matched=%s score=%.3f gaps=%s", cid, score, gaps)
        return {
            "path":             "C",
            "case_id":          None,
            "matched_case_id":  cid,
            "similarity_score": round(score, 3),
            "document":         None,
            "summary":          summary,
            "dimensions":       dims,
        }

    # ── Path B: generate + store new case ────────────────────────────────────

    @staticmethod
    def _path_b(feedback: Dict) -> Dict[str, Any]:
        from src.tools.lma_case_store import save_case

        doc_dict = _generate_case(feedback)
        new_id   = save_case(doc_dict)
        doc_dict["case_id"] = new_id

        summary = (
            f"No existing case matched the combination of "
            f"fault_mode={feedback.get('fault_mode','—')}, "
            f"asset_type={feedback.get('asset_type','—')}, "
            f"action={feedback.get('action_taken','—').replace('_',' ')}. "
            f"New institutional knowledge case {new_id} generated and stored."
        )
        logger.info("[lma][path_B] new_case=%s", new_id)
        return {
            "path":             "B",
            "case_id":          new_id,
            "matched_case_id":  None,
            "similarity_score": 0.0,
            "document":         doc_dict,
            "summary":          summary,
            "dimensions":       {d: "COMPLETE" for d in _dim_names_all()},
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dim_names_all() -> List[str]:
    return [
        "signal_signature",
        "diagnosis_and_reasoning",
        "action_and_outcome",
        "root_cause_and_factors",
        "lessons_and_future_reference",
    ]


def _extract_bearing_model(part_number: str) -> str:
    """Pull bearing model from part number (e.g. 'SKF6310-ZZ' → 'SKF6310')."""
    m = re.match(r"([A-Z0-9]+)", part_number.upper())
    return m.group(1) if m else part_number


def _build_query(feedback: Dict) -> str:
    """Construct a rich query string for TF-IDF search from the feedback dict."""
    parts = [
        feedback.get("fault_mode", "").replace("_", " "),
        feedback.get("asset_type", "").replace("_", " "),
        feedback.get("bearing_type", "").replace("_", " "),
        feedback.get("action_taken", "").replace("_", " "),
        f"stage {feedback.get('iso_stage', 0)}",
        feedback.get("urgency", "").replace("_", " "),
    ]
    return " ".join(p for p in parts if p.strip())


def _generate_case(feedback: Dict) -> Dict:
    """
    Dev-mode case generation: build LearnedCaseDocument content from
    execution result data without an LLM call.
    """
    fault  = feedback.get("fault_mode", "unknown_fault").replace("_", " ")
    asset  = feedback.get("asset_type", "asset").replace("_", " ")
    brg    = feedback.get("bearing_type", "bearing")
    stage  = feedback.get("iso_stage", 0)
    action = feedback.get("action_taken", "maintenance action").replace("_", " ")
    asset_id = feedback.get("asset_id", "")
    wo_id    = feedback.get("work_order_id", "")
    urgency  = feedback.get("urgency", "").replace("_", " ")
    rationale = feedback.get("rationale", "")

    content = (
        f"{fault.title()} fault confirmed on {asset} {asset_id} {brg} bearing "
        f"at ISO Stage {stage}. "
        f"Urgency: {urgency}. "
        f"Action taken: {action}. "
        f"Work order: {wo_id}. "
    )
    if rationale:
        content += f"Decision rationale: {rationale} "
    content += (
        f"Execution status: success. "
        f"Root cause: bearing degradation consistent with {fault} fault mode "
        f"in {asset} asset class. "
        f"Lessons learned: monitor vibration trends at 2-week intervals after "
        f"{action} on {asset} assets with {brg} bearings. "
        f"Verify historian baselines within 24 hours of repair completion. "
        f"Schedule next inspection per asset-class PM schedule."
    )

    tags = [
        feedback.get("fault_mode", ""),
        feedback.get("asset_type", ""),
        feedback.get("bearing_type", ""),
        f"stage{stage}",
        feedback.get("action_taken", ""),
        "success",
    ]
    tags = [t for t in tags if t]

    return {
        "case_id":      "",
        "fault_mode":   feedback.get("fault_mode", ""),
        "asset_type":   feedback.get("asset_type", ""),
        "bearing_type": brg,
        "iso_stage":    stage,
        "outcome":      "success",
        "content":      content,
        "tags":         tags,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
