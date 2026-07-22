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
import math
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.bearing_signal import TrustedBearingSignal
from src.schemas.knowledge import (
    GroundedGuidanceItem,
    KnowledgeGuidance,
    SourceDocument,
)
from src.schemas.risk import RiskAssessment
from src.tools.retriever import retrieve, knowledge_index_version
from src.tools.config_loader import KnowledgeConfig, load_knowledge_config
from src.tools.failure_intelligence_utilities import stable_version

logger = logging.getLogger(__name__)

# Patterns to extract structured fields from SOP text
# Primary: "Step 1: text" format (synthetic SOPs)
_STEP_RE = re.compile(r"Step\s+\d+:\s*(.+?)(?=Step\s+\d+:|$)", re.DOTALL | re.IGNORECASE)
# Fallback: "1 text" numbered-line format (real PDFs); (?![\d\.]) excludes "1." section headers
_NUMBERED_STEP_RE = re.compile(r"(?m)^\s*(\d{1,2})(?![\d\.])\s+(.+)$")
# Match LOTO_NNN permit IDs (e.g. LOTO_001, LOTO_005) — avoids "LOTO permit" and "LOTO per Section"
_LOTO_RE = re.compile(
    r"(LOTO[_-][0-9]{3,}|(?:EL|IL)-[A-Z0-9-]+)", re.IGNORECASE
)
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

    def __init__(self, retriever_fn=None, cfg: KnowledgeConfig = None,
                 index_version: str = ""):
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        # Retrieval policy is validated/configurable. The corpus identifier makes
        # every guidance result traceable to the exact indexed knowledge content.
        self._retrieve = retriever_fn or retrieve
        self._cfg = cfg or load_knowledge_config()
        self._cfg.validate()
        self._config_version = stable_version(self._cfg)
        self._index_version = (
            index_version or
            (knowledge_index_version() if retriever_fn is None else "injected-retriever")
        )
        # ***********************

    def process(self, diagnosis: FaultDiagnosis,
                trusted: TrustedBearingSignal,
                risk: Optional[RiskAssessment] = None) -> KnowledgeGuidance:
        """
        Retrieve relevant SOPs for the diagnosed fault and return structured guidance.
        Always returns a KnowledgeGuidance — never raises.
        """
        # ************** Added by Prateek Mittal on 20th July 2026 ******************
        invalid_reason = self._input_error(diagnosis, trusted, risk)
        if invalid_reason:
            return self._empty(
                diagnosis, risk, "invalid_input", invalid_reason, eligible=False
            )

        fault_mode = diagnosis.fault_mode or ""
        # A valid undetermined diagnosis has no applicable fault-mode SOP. Do not
        # return generic repair instructions that could be mistaken for a match.
        if fault_mode == "undetermined" or diagnosis.iso_stage == 0:
            return self._empty(
                diagnosis, risk, "no_guidance",
                "no classified fault mode is available for SOP retrieval",
                eligible=False,
            )
        # ***********************
        asset_type = (trusted.asset_ctx.asset_type
                      if trusted.asset_ctx else "")
        bearing_type = (trusted.bearing_ctx.bearing_model
                        if trusted.bearing_ctx else "")
        iso_stage = diagnosis.iso_stage or 0

        risk_level = risk.risk_level if risk is not None else diagnosis.severity
        query = (
            f"{fault_mode.replace('_', ' ')} "
            f"{asset_type} bearing "
            f"stage {iso_stage} "
            f"severity {diagnosis.severity} risk {risk_level} "
            f"replacement procedure inspection repair steps torque lubricant"
        ).strip()

        try:
            hits = self._retrieve(
                query=query,
                top_k=self._cfg.top_k,
                fault_mode=fault_mode,
                asset_type=asset_type.lower(),
                iso_stage=iso_stage,
                minimum_score=self._cfg.minimum_score,
                fault_mode_boost=self._cfg.fault_mode_boost,
                asset_type_boost=self._cfg.asset_type_boost,
                iso_stage_boost=self._cfg.iso_stage_boost,
            )
        except Exception as exc:
            logger.warning("KnowledgeAgent: retriever failed (%s).", exc)
            return self._empty(
                diagnosis, risk, "retrieval_failed", f"retriever failed: {exc}",
                eligible=False, query=query,
            )
        if not isinstance(hits, (list, tuple)):
            return self._empty(
                diagnosis, risk, "no_guidance",
                "retriever returned no valid hit collection",
                eligible=False, query=query,
            )

        source_documents: List[str] = []
        relevant_sections: List[str] = []
        inspection_steps: List[str] = []
        safety_notes: List[str] = []
        loto_reference: str = ""
        grounded_items: List[GroundedGuidanceItem] = []
        source_details: List[SourceDocument] = []
        seen_sources = set()
        seen_items = set()
        accepted_hits = 0

        for hit in hits:
            if not isinstance(hit, dict):
                continue
            src = hit.get("source", "")
            text = hit.get("text", "")
            if not isinstance(src, str) or not isinstance(text, str):
                continue
            src = src.strip()
            text = text.strip()
            if self._cfg.require_source and not src:
                continue
            if not src or not isinstance(text, str) or not text.strip():
                continue
            hit_fault = hit.get("fault_mode", "")
            if hit_fault and hit_fault != fault_mode:
                continue
            if not hit_fault and not self._cfg.allow_generic_documents:
                continue
            try:
                score = float(hit.get("score", 0.0))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(score) or score < self._cfg.minimum_score:
                continue
            try:
                hit_stage = int(hit.get("iso_stage", 0) or 0)
            except (TypeError, ValueError):
                hit_stage = 0
            accepted_hits += 1
            if src and src not in source_documents:
                source_documents.append(src)
            if src not in seen_sources:
                source_details.append(SourceDocument(
                    title=src, retrieval_score=score,
                    fault_mode=hit_fault,
                    asset_type=hit.get("asset_type", ""),
                    iso_stage=hit_stage,
                ))
                seen_sources.add(src)
            if text:
                preview = self._cfg.section_preview_characters
                relevant_sections.append(
                    f"[{src}] {text[:preview]}…" if len(text) > preview else f"[{src}] {text}"
                )
            steps = _extract_steps(text)
            for s in steps:
                if s not in inspection_steps:
                    inspection_steps.append(s)
                key = ("inspection_step", s)
                if key not in seen_items:
                    grounded_items.append(GroundedGuidanceItem(
                        text=s, source_document=src,
                        item_type="inspection_step", retrieval_score=score,
                    ))
                    seen_items.add(key)
            for note in _extract_safety(text):
                if note not in safety_notes:
                    safety_notes.append(note)
                key = ("safety_note", note)
                if key not in seen_items:
                    grounded_items.append(GroundedGuidanceItem(
                        text=note, source_document=src,
                        item_type="safety_note", retrieval_score=score,
                    ))
                    seen_items.add(key)
            if not loto_reference:
                loto_reference = _extract_loto(text)

        if not source_documents:
            return self._empty(
                diagnosis, risk, "no_guidance",
                "no source-backed SOP met the retrieval and relevance policy",
                eligible=False, query=query,
            )

        if self._cfg.log_retrievals:
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
            relevant_sections=relevant_sections[:self._cfg.max_relevant_sections],
            inspection_steps=inspection_steps[:self._cfg.max_inspection_steps],
            safety_notes=safety_notes[:self._cfg.max_safety_notes],
            loto_reference=loto_reference,
            processed_at=datetime.now(timezone.utc).isoformat(),
            guidance_status="grounded",
            guidance_eligible=True,
            status_reason="source-backed SOP guidance retrieved",
            retrieval_query=query,
            retrieval_hit_count=accepted_hits,
            grounded_items=[
                item for item in grounded_items
                if ((item.item_type == "inspection_step" and item.text in inspection_steps[:self._cfg.max_inspection_steps])
                    or (item.item_type == "safety_note" and item.text in safety_notes[:self._cfg.max_safety_notes]))
            ],
            source_details=source_details,
            **self._provenance(diagnosis, risk),
        )

    # ************** Added by Prateek Mittal on 20th July 2026 ******************
    @staticmethod
    def _input_error(diagnosis, trusted, risk) -> str:
        if not isinstance(diagnosis, FaultDiagnosis):
            return "diagnosis must be a FaultDiagnosis"
        if not isinstance(trusted, TrustedBearingSignal):
            return "trusted must be a TrustedBearingSignal"
        if getattr(diagnosis, "diagnosis_status", "diagnosed") == "invalid_input":
            return "Agent 3 diagnosis is invalid_input"
        if not getattr(diagnosis, "diagnostic_eligible", True):
            return "Agent 3 diagnosis is not guidance eligible"
        if not trusted.downstream_eligible:
            return "Agent 1 signal is not downstream eligible"
        if (diagnosis.asset_id != trusted.raw.asset_id
                or diagnosis.bearing_id != trusted.raw.bearing_id):
            return "diagnosis and trusted signal identities do not match"
        if risk is not None:
            if not isinstance(risk, RiskAssessment):
                return "risk must be a RiskAssessment when supplied"
            if not risk.risk_eligible or risk.assessment_status == "invalid_input":
                return "Agent 4 risk assessment is not guidance eligible"
            if risk.case_id != diagnosis.case_id:
                return "risk and diagnosis case identities do not match"
            if (risk.asset_id != diagnosis.asset_id
                    or risk.bearing_id != diagnosis.bearing_id):
                return "risk and diagnosis asset identities do not match"
        return ""

    def _provenance(self, diagnosis, risk) -> Dict[str, str]:
        return {
            "knowledge_config_version": self._config_version,
            "knowledge_index_version": self._index_version,
            "source_diagnosis_schema_version": getattr(diagnosis, "schema_version", ""),
            "source_fi_config_version": getattr(diagnosis, "fi_config_version", ""),
            "source_taxonomy_version": getattr(diagnosis, "taxonomy_version", ""),
            "source_risk_schema_version": getattr(risk, "schema_version", ""),
            "source_risk_config_version": getattr(risk, "risk_config_version", ""),
            "source_monitoring_config_version": getattr(diagnosis, "source_monitoring_config_version", ""),
            "source_data_config_version": getattr(diagnosis, "source_config_version", ""),
            "source_master_data_version": getattr(diagnosis, "source_master_data_version", ""),
            "linked_risk_case_id": getattr(risk, "case_id", ""),
        }

    def _empty(self, diagnosis, risk, status: str, reason: str,
               eligible: bool, query: str = "") -> KnowledgeGuidance:
        return KnowledgeGuidance(
            case_id=getattr(diagnosis, "case_id", ""),
            fault_mode=getattr(diagnosis, "fault_mode", ""),
            guidance_status=status,
            guidance_eligible=eligible,
            status_reason=reason,
            retrieval_query=query,
            processed_at=datetime.now(timezone.utc).isoformat(),
            **self._provenance(diagnosis, risk),
        )
    # ***********************