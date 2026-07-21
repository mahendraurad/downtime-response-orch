"""
agents/knowledge_agent.py  —  Phase 6

Knowledge Agent — retrieves SOPs, inspection steps, and safety guidance
for each diagnosed fault using cosine-similarity search over the SOP catalog.

Entry point:  KnowledgeAgent.process(diagnosis, trusted) -> KnowledgeGuidance

Pipeline:
  1. Build retrieval query from fault_mode + asset_type + bearing_type + iso_stage.
  2. Call retriever with metadata boost so relevant chunks rank first.
  3. Parse top passages into structured KnowledgeGuidance fields.
  4. Every returned item cites its source document (no unsourced guidance).

Never raises: missing fields degrade gracefully to empty guidance.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List

from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.bearing_signal import TrustedBearingSignal
from src.schemas.knowledge import KnowledgeGuidance
from src.tools.retriever import retrieve

logger = logging.getLogger(__name__)

# Patterns to extract structured fields from SOP text
# Primary: "Step 1: text" format (synthetic SOPs)
_STEP_RE = re.compile(r"Step\s+\d+:\s*(.+?)(?=Step\s+\d+:|$)", re.DOTALL | re.IGNORECASE)
# Fallback: "1 text" numbered-line format (real PDFs); (?![\d\.]) excludes "1." section headers
_NUMBERED_STEP_RE = re.compile(r"(?m)^\s*(\d{1,2})(?![\d\.])\s+(.+)$")
# Match LOTO_NNN permit IDs (e.g. LOTO_001, LOTO_005) — avoids "LOTO permit" and "LOTO per Section"
_LOTO_RE = re.compile(r"(LOTO[_][0-9]{3,})", re.IGNORECASE)
_SAFETY_KEYWORDS = {
    "PPE", "hearing protection", "gloves", "safety glasses", "steel-toed",
    "LOTO", "isolat", "Zone D", "Zone C", "emergency stop", "confined space",
    "hot-work", "permit", "no approach", "hazard",
}


def _extract_steps(text: str) -> List[str]:
    # Try "Step 1: text" format first
    steps = []
    for m in _STEP_RE.finditer(text):
        step = re.sub(r"\s+", " ", m.group(1).strip())
        if step:
            steps.append(step)
    if steps:
        return steps
    # Fallback: "N text" numbered-line format used in real PDFs.
    # Require ≥2 consecutive numbers to avoid false positives from prerequisite lists.
    candidates: list = []
    seen_nums: set = set()
    for m in _NUMBERED_STEP_RE.finditer(text):
        num = int(m.group(1))
        content = re.sub(r"\s+", " ", m.group(2).strip())
        if num not in seen_nums and content and len(content) > 15:
            candidates.append((num, content))
            seen_nums.add(num)
    if len(candidates) >= 2:
        nums = sorted(n for n, _ in candidates)
        has_consecutive = any(nums[i + 1] == nums[i] + 1 for i in range(len(nums) - 1))
        if has_consecutive:
            return [c for _, c in sorted(candidates)]
    return steps


def _extract_safety(text: str) -> List[str]:
    notes = []
    for line in text.splitlines():
        line = line.strip()
        if any(kw.lower() in line.lower() for kw in _SAFETY_KEYWORDS):
            cleaned = re.sub(r"\s+", " ", line).strip(".,;")
            if cleaned and len(cleaned) > 10:
                notes.append(cleaned)
    return list(dict.fromkeys(notes))  # deduplicate preserving order


def _extract_loto(text: str) -> str:
    m = _LOTO_RE.search(text)
    return m.group(1).upper() if m else ""


class KnowledgeAgent:
    """Retrieval-based knowledge agent.  Stateless after construction."""

    def __init__(self, retriever_fn=None):
        # Allow injecting a custom retriever (for testing)
        self._retrieve = retriever_fn or retrieve

    def process(self, diagnosis: FaultDiagnosis,
                trusted: TrustedBearingSignal) -> KnowledgeGuidance:
        """
        Retrieve relevant SOPs for the diagnosed fault and return structured guidance.
        Always returns a KnowledgeGuidance — never raises.
        """
        fault_mode = diagnosis.fault_mode or ""
        asset_type = (trusted.asset_ctx.asset_type
                      if trusted.asset_ctx else "")
        bearing_type = (trusted.bearing_ctx.bearing_model
                        if trusted.bearing_ctx else "")
        iso_stage = diagnosis.iso_stage or 0

        query = (
            f"{fault_mode.replace('_', ' ')} "
            f"{asset_type} bearing "
            f"stage {iso_stage} "
            f"replacement procedure inspection repair steps torque lubricant"
        ).strip()

        try:
            # Fetch 5 to ensure we capture both procedural + safety chunks
            hits = self._retrieve(
                query=query,
                top_k=5,
                fault_mode=fault_mode,
                asset_type=asset_type.lower(),
                iso_stage=iso_stage,
            )
        except Exception as exc:
            logger.warning("KnowledgeAgent: retriever failed (%s) — returning empty guidance.", exc)
            hits = []

        source_documents: List[str] = []
        relevant_sections: List[str] = []
        inspection_steps: List[str] = []
        safety_notes: List[str] = []
        loto_reference: str = ""

        for hit in hits:
            src = hit.get("source", "")
            text = hit.get("text", "")
            if src and src not in source_documents:
                source_documents.append(src)
            if text:
                relevant_sections.append(f"[{src}] {text[:300]}…" if len(text) > 300 else f"[{src}] {text}")
            steps = _extract_steps(text)
            for s in steps:
                if s not in inspection_steps:
                    inspection_steps.append(s)
            for note in _extract_safety(text):
                if note not in safety_notes:
                    safety_notes.append(note)
            if not loto_reference:
                loto_reference = _extract_loto(text)

        logger.info(
            "KnowledgeAgent: fault=%s asset=%s stage=%d → %d SOPs retrieved",
            fault_mode, asset_type, iso_stage, len(source_documents),
        )

        return KnowledgeGuidance(
            case_id=diagnosis.case_id,
            fault_mode=fault_mode,
            asset_type=asset_type,
            bearing_type=bearing_type,
            source_documents=source_documents,
            relevant_sections=relevant_sections,
            inspection_steps=inspection_steps[:8],   # cap to 8 steps
            safety_notes=safety_notes[:6],           # cap to 6 safety notes
            loto_reference=loto_reference,
            processed_at=datetime.now(timezone.utc).isoformat(),
        )
