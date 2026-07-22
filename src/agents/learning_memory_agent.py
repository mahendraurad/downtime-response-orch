"""
agents/learning_memory_agent.py  —  Phase 10

Learning & Memory Agent — converts validated execution outcomes into
searchable case documents for closed-loop institutional knowledge.

Entry point:
  LearningMemoryAgent.process(execution, feedback) -> LearnedCaseDocument

Pipeline:
  1. Input validation — typed ExecutionResult + FeedbackEvent required.
  2. Duplicate check — idempotent; rejected if case already learned.
  3. Template narrative — deterministic content from execution + feedback.
  4. Optional LLM narrative enhancement (disabled by default).
  5. Persist to JSON case store + export training row.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.schemas.execution import ExecutionResult
from src.schemas.feedback import FeedbackEvent, LearnedCaseDocument
from src.tools.config_loader import LearningConfig, load_learning_config
from src.tools.learned_case_repository import JSONLearnedCaseRepository


# ************** Added by Prateek Mittal on 20th July 2026 ******************
class LearningMemoryAgent:
    def __init__(self, cfg: LearningConfig = None, repository=None, llm_client=None, now_fn=None):
        self._cfg = cfg or load_learning_config(); self._cfg.validate()
        self._repo = repository or JSONLearnedCaseRepository(self._cfg.repository_path)
        self._llm = llm_client
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._version = hashlib.sha256(json.dumps(asdict(self._cfg), sort_keys=True).encode()).hexdigest()[:16]

    def process(self, execution: ExecutionResult, feedback: FeedbackEvent) -> LearnedCaseDocument:
        reason = self._input_error(execution, feedback)
        if reason:
            return self._empty(execution, reason)
        if self._repo.exists(feedback.case_id):
            result = self._empty(execution, "case outcome has already been learned")
            result.learning_status = "duplicate"
            result.persistence_status = "duplicate_not_stored"
            return result
        content = self._template(execution, feedback)
        content, source = self._optional_llm(content, feedback)
        doc = LearnedCaseDocument(
            case_id=feedback.case_id, content=content, fault_mode=feedback.confirmed_fault_mode,
            asset_type="", bearing_type="", iso_stage=0,
            outcome=f"{feedback.action_taken}: {feedback.root_cause}",
            tags=[feedback.confirmed_fault_mode, feedback.action_taken,
                  "recommendation_followed" if feedback.recommendation_followed else "recommendation_not_followed"],
            created_at=self._now().isoformat(), recommendation_followed=feedback.recommendation_followed,
            post_repair_vib_mm_s=feedback.post_repair_vib_mm_s,
            post_repair_temp_c=feedback.post_repair_temp_c,
            days_to_failure_actual=feedback.days_to_failure_actual,
            narrative_source=source, learning_config_version=self._version,
            source_execution_schema_version=execution.schema_version,
            source_executor_config_version=execution.executor_config_version,
            linked_execution_case_id=execution.case_id,
            status_reason="validated closed outcome converted to searchable memory",
        )
        try:
            doc.persistence_status = "stored"
            doc.index_status = "indexed"
            self._repo.save(doc)
            self._export_training_row(execution, feedback)
        except Exception as exc:
            doc.learning_status = "persistence_failed"
            doc.learning_eligible = False
            doc.persistence_status = "failed"
            doc.index_status = "failed"
            doc.status_reason = f"learning persistence failed: {exc}"
        return doc

    def _input_error(self, execution, feedback):
        if not isinstance(execution, ExecutionResult) or not isinstance(feedback, FeedbackEvent):
            return "typed ExecutionResult and FeedbackEvent are required"
        if execution.status not in self._cfg.allowed_execution_statuses:
            return "execution is not a successfully completed outcome"
        if not execution.execution_eligible or execution.case_id != feedback.case_id:
            return "execution and feedback case identities do not match"
        required = [feedback.asset_id, feedback.bearing_id, feedback.confirmed_fault_mode,
                    feedback.action_taken, feedback.closed_at]
        if any(not isinstance(v, str) or not v.strip() for v in required):
            return "feedback identity, confirmed fault, action and closure time are required"
        if self._cfg.require_root_cause and not feedback.root_cause.strip():
            return "confirmed root cause is required"
        try:
            closed = datetime.fromisoformat(feedback.closed_at.replace("Z", "+00:00"))
            if self._cfg.require_timezone and closed.tzinfo is None:
                return "closure timestamp requires timezone"
            if closed > self._now():
                return "closure timestamp cannot be in the future"
        except ValueError:
            return "closure timestamp must be ISO-8601"
        if feedback.post_repair_vib_mm_s < 0 or feedback.post_repair_temp_c < -273.15:
            return "post-repair measurements are physically invalid"
        if feedback.days_to_failure_actual is not None and feedback.days_to_failure_actual < 0:
            return "actual days to failure cannot be negative"
        return ""

    def _empty(self, execution, reason):
        return LearnedCaseDocument(case_id=getattr(execution, "case_id", ""),
            learning_status="invalid_input", learning_eligible=False, status_reason=reason,
            created_at=self._now().isoformat(), learning_config_version=self._version,
            source_execution_schema_version=getattr(execution, "schema_version", ""),
            source_executor_config_version=getattr(execution, "executor_config_version", ""),
            linked_execution_case_id=getattr(execution, "case_id", ""))

    @staticmethod
    def _template(execution, feedback):
        return (f"Closed maintenance case {feedback.case_id}. Confirmed fault: "
            f"{feedback.confirmed_fault_mode}. Root cause: {feedback.root_cause}. "
            f"Action taken: {feedback.action_taken}. Recommendation followed: "
            f"{feedback.recommendation_followed}. Technician notes: {feedback.technician_notes}. "
            f"Post-repair vibration {feedback.post_repair_vib_mm_s} mm/s and temperature "
            f"{feedback.post_repair_temp_c} C. Execution audit {execution.audit_reference}.")

    def _optional_llm(self, fallback, feedback):
        if not self._cfg.llm_narrative_enabled or self._llm is None:
            return fallback, "template"
        try:
            if hasattr(self._llm, "is_configured") and not self._llm.is_configured():
                return fallback, "template"
            response = self._llm.complete_json(system_prompt=(
                "Rewrite only the learned-case narrative. Preserve all confirmed labels and measurements."),
                user_prompt=fallback, temperature=0.1, max_tokens=500)
            text = response.get("narrative", "") if isinstance(response, dict) else ""
            if isinstance(text, str) and feedback.confirmed_fault_mode in text and feedback.action_taken in text:
                return text[:self._cfg.llm_max_characters], "template+llm_narrative"
        except Exception:
            pass
        return fallback, "template"

    def _export_training_row(self, execution, feedback):
        path = Path(self._cfg.training_export_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {**feedback.model_dump(), "execution_status": execution.status,
               "execution_action": execution.action_taken}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
# ***********************
