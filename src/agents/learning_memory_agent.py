"""Agent 8: validate closed-loop outcomes and publish searchable learned cases."""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.schemas.execution import ExecutionResult
from src.schemas.feedback import (
    ChatLearningResult, FeedbackEvent, LearnedCaseDocument, RejectionFeedback,
    RejectionLearningResult,
)
from src.tools.config_loader import LearningConfig, load_learning_config
from src.tools.learned_case_repository import (
    JSONChatLearningRepository, JSONLearnedCaseRepository, JSONRejectionRepository,
)


# ************** Added by Prateek Mittal on 20th July 2026 ******************
class LearningMemoryAgent:
    def __init__(self, cfg: LearningConfig = None, repository=None,
                 rejection_repository=None, llm_client=None, now_fn=None):
        self._cfg = cfg or load_learning_config(); self._cfg.validate()
        self._repo = repository or JSONLearnedCaseRepository(self._cfg.repository_path)
        self._rejections = rejection_repository or JSONRejectionRepository(
            self._cfg.rejection_repository_path
        )
        self._chat_observations = JSONChatLearningRepository(
            self._cfg.chat_observation_repository_path
        )
        self._llm = llm_client
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._version = hashlib.sha256(json.dumps(asdict(self._cfg), sort_keys=True).encode()).hexdigest()[:16]

    def process(self, execution: ExecutionResult, feedback: FeedbackEvent,
                persona_context=None) -> LearnedCaseDocument:
        reason = self._input_error(execution, feedback)
        if reason: return self._empty(execution, reason)
        if self._repo.exists(feedback.case_id):
            result = self._empty(execution, "case outcome has already been learned")
            result.learning_status = "duplicate"; result.persistence_status = "duplicate_not_stored"
            return result
        content = self._template(execution, feedback)
        content, source = self._optional_llm(content, feedback)
        doc = LearnedCaseDocument(
            case_id=feedback.case_id, asset_id=feedback.asset_id,
            bearing_id=feedback.bearing_id, content=content,
            fault_mode=feedback.confirmed_fault_mode,
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
            doc.persistence_status = "stored"; doc.index_status = "indexed"
            self._repo.save(doc)
            self._export_training_row(execution, feedback)
        except Exception as exc:
            doc.learning_status = "persistence_failed"; doc.learning_eligible = False
            doc.persistence_status = "failed"; doc.index_status = "failed"
            doc.status_reason = f"learning persistence failed: {exc}"
        return doc

    def recent_cases(self, limit: int = 3, asset_id: str = "") -> list[dict]:
        """Read validated learned history; this does not create new learning."""
        if not hasattr(self._repo, "list_recent"):
            return []
        try:
            return self._repo.list_recent(limit, asset_id=asset_id)
        except TypeError:
            rows = self._repo.list_recent(limit)
            return [row for row in rows if not asset_id or row.get("asset_id") == asset_id]

    def recent_rejections(self, limit: int = 3, asset_id: str = "") -> list[dict]:
        """Read rejection feedback as decision learning, not confirmed outcomes."""
        if not self._cfg.include_rejections_in_history or not hasattr(self._rejections, "list_recent"):
            return []
        try:
            rows = self._rejections.list_recent(
                limit=limit, asset_id=asset_id, decision="rejected"
            )
        except TypeError:
            rows = self._rejections.list_recent(limit)
            rows = [row for row in rows if row.get("decision") == "rejected"
                    and (not asset_id or row.get("asset_id") == asset_id)]
        return [{
            "case_id": str(row.get("case_id", "")),
            "asset_id": str(row.get("asset_id", "")),
            "fault_mode": str(row.get("fault_mode", "")),
            "reason_code": str(row.get("reason_code", "")),
            "free_text": str(row.get("free_text", "")),
            "persona_id": str(row.get("persona_id", "")),
            "recorded_at": str(row.get("decided_at", row.get("rejected_at", ""))),
            "learning_type": "rejection_feedback",
            "valid_closure": True,
            "closure_status": "rejected",
            "physical_outcome_confirmed": False,
            "confirmed_outcome": False,
            "reliability_review_required": bool(
                row.get("reliability_review_required", False)
            ),
        } for row in rows]

    def recent_chat_observations(self, limit: int = 3,
                                 asset_id: str = "") -> list[dict]:
        """Read useful chat observations captured by Agent 8."""
        if not hasattr(self._chat_observations, "list_recent"):
            return []
        return self._chat_observations.list_recent(limit, asset_id=asset_id)

    def matching_cases(self, asset_id: str, fault_mode: str,
                       limit: int = 3) -> list[dict]:
        if not hasattr(self._repo, "search_cases"):
            return []
        return self._repo.search_cases(
            asset_id=asset_id, fault_mode=fault_mode, top_k=limit
        )

    def record_rejection(self, feedback: RejectionFeedback) -> RejectionLearningResult:
        if not isinstance(feedback, RejectionFeedback):
            return RejectionLearningResult(
                case_id="", status="invalid_input",
                learning_recorded=False, persistence_status="not_stored",
            )
        required_fields = (feedback.case_id, feedback.asset_id, feedback.fault_mode,
                           feedback.persona_id)
        if any(not isinstance(value, str) or not value.strip() for value in required_fields):
            return RejectionLearningResult(
                case_id=feedback.case_id, status="invalid_input",
                learning_recorded=False, persistence_status="not_stored",
            )
        now = self._now()
        try:
            rejected_at = datetime.fromisoformat(
                feedback.rejected_at.replace("Z", "+00:00")
            )
        except ValueError:
            return RejectionLearningResult(
                case_id=feedback.case_id, status="invalid_input",
                learning_recorded=False, persistence_status="not_stored",
            )
        if rejected_at.tzinfo is None or rejected_at > now:
            return RejectionLearningResult(
                case_id=feedback.case_id, status="invalid_input",
                learning_recorded=False, persistence_status="not_stored",
            )
        try:
            if (hasattr(self._rejections, "exists_decision")
                    and self._rejections.exists_decision(feedback.case_id, "rejected")):
                return RejectionLearningResult(
                    case_id=feedback.case_id, status="duplicate",
                    learning_recorded=False,
                    valid_closure=True, closure_status="rejected",
                    persistence_status="duplicate_not_stored",
                    review_reason="rejection feedback for this case is already recorded",
                )
            self._rejections.append({
                **feedback.model_dump(), "decision": "rejected",
                "decided_at": rejected_at.isoformat(),
                "learning_type": "rejection_feedback",
                "valid_closure": True,
                "closure_status": "rejected",
            })
            count = self._rejections.consecutive_rejections(
                feedback.asset_id, feedback.fault_mode, now,
                days=self._cfg.rejection_window_days
            )
            required = count >= self._cfg.rejection_review_threshold
            if hasattr(self._rejections, "update_decision"):
                self._rejections.update_decision(feedback.case_id, "rejected", {
                    "consecutive_rejections": count,
                    "reliability_review_required": required,
                })
        except Exception as exc:
            return RejectionLearningResult(
                case_id=feedback.case_id, status="persistence_failed",
                learning_recorded=False, persistence_status="failed",
                review_reason=str(exc),
            )
        return RejectionLearningResult(
            case_id=feedback.case_id,
            valid_closure=True,
            closure_status="rejected",
            reliability_review_required=required,
            consecutive_rejections=count,
            review_reason=(
                f"same asset and fault rejected consecutively within "
                f"{self._cfg.rejection_window_days} days"
                if required else ""
            ),
        )

    def record_approval(self, case_id: str, asset_id: str,
                        fault_mode: str) -> None:
        now = self._now()
        self._rejections.append({
            "case_id": case_id, "asset_id": asset_id,
            "fault_mode": fault_mode, "decision": "approved",
            "decided_at": now.isoformat(),
        })

    def observe_chat(self, event: dict, persona_context=None) -> ChatLearningResult:
        """Observe a chat turn and persist only useful new learning signals."""
        now = self._now()
        if not self._cfg.chat_observation_enabled:
            return ChatLearningResult(
                status="disabled", reason="chat observation is disabled",
                created_at=now.isoformat(),
            )
        if not isinstance(event, dict):
            return ChatLearningResult(
                status="no_new_learning",
                reason="typed chat event is required",
                created_at=now.isoformat(),
            )
        decision = self._chat_learning_decision(event)
        if not decision.get("learning_kind"):
            return ChatLearningResult(
                status="no_new_learning",
                reason=decision.get(
                    "reason", "chat turn contains no durable operational learning"
                ),
                intent=str(event.get("intent", "")),
                created_at=now.isoformat(),
            )
        fingerprint = hashlib.sha256(json.dumps({
            "learning_kind": decision["learning_kind"],
            "asset_ids": decision.get("asset_ids", []),
            "intent": decision.get("intent", ""),
            "evidence": decision.get("evidence", {}),
        }, sort_keys=True).encode()).hexdigest()
        observation_id = f"CHAT-{fingerprint[:12].upper()}"
        if self._chat_observations.exists_fingerprint(fingerprint):
            return ChatLearningResult(
                observation_id=observation_id,
                status="duplicate",
                learning_recorded=False,
                learning_kind=decision["learning_kind"],
                reason="chat observation has already been learned",
                asset_ids=decision.get("asset_ids", []),
                intent=decision.get("intent", ""),
                created_at=now.isoformat(),
                persistence_status="duplicate_not_stored",
                summary=decision.get("summary", ""),
            )
        row = {
            "observation_id": observation_id,
            "fingerprint": fingerprint,
            "learning_type": "chat_observation",
            "learning_kind": decision["learning_kind"],
            "asset_ids": decision.get("asset_ids", []),
            "asset_id": decision.get("asset_ids", [""])[0]
                if decision.get("asset_ids") else "",
            "intent": decision.get("intent", ""),
            "summary": decision.get("summary", ""),
            "reason": decision.get("reason", ""),
            "message": str(event.get("message", ""))[
                :self._cfg.chat_max_message_characters
            ],
            "response": str(event.get("response", ""))[
                :self._cfg.chat_max_response_characters
            ],
            "run_id": str(event.get("run_id", "")),
            "conversation_id": str(event.get("conversation_id", "")),
            "persona_id": getattr(persona_context, "id", "")
                or str(event.get("persona", "")),
            "created_at": now.isoformat(),
            "learning_config_version": self._version,
            "evidence": decision.get("evidence", {}),
        }
        try:
            self._chat_observations.save(row)
        except Exception as exc:
            return ChatLearningResult(
                observation_id=observation_id,
                status="failed",
                learning_recorded=False,
                learning_kind=decision["learning_kind"],
                reason=f"chat observation persistence failed: {exc}",
                asset_ids=decision.get("asset_ids", []),
                intent=decision.get("intent", ""),
                created_at=now.isoformat(),
                persistence_status="failed",
                summary=decision.get("summary", ""),
            )
        return ChatLearningResult(
            observation_id=observation_id,
            status="learned",
            learning_recorded=True,
            learning_kind=decision["learning_kind"],
            reason=decision.get("reason", ""),
            asset_ids=decision.get("asset_ids", []),
            intent=decision.get("intent", ""),
            created_at=now.isoformat(),
            persistence_status="stored",
            summary=decision.get("summary", ""),
        )

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
            if self._cfg.require_timezone and closed.tzinfo is None: return "closure timestamp requires timezone"
            if closed > self._now(): return "closure timestamp cannot be in the future"
        except ValueError: return "closure timestamp must be ISO-8601"
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
    def _chat_learning_decision(event):
        result = event.get("result", {})
        if not isinstance(result, dict):
            result = {}
        intent = str(event.get("intent") or result.get("intent") or "")
        asset_ids = sorted({
            str(value).upper() for value in event.get("asset_ids", [])
            if isinstance(value, str) and value.strip()
        })
        clarification = result.get("clarification", {})
        if not isinstance(clarification, dict):
            clarification = {}
        missing = [
            str(item) for item in clarification.get("missing_fields", [])
            if str(item).strip()
        ]
        asset_id = str(clarification.get("asset_id", "")).upper()
        if asset_id:
            asset_ids = sorted(set(asset_ids + [asset_id]))
        if result.get("clarification_required") and missing:
            if "asset_registration" in missing:
                kind = "asset_registration_gap"
                summary = f"Unregistered asset blocked analysis for {asset_id or 'requested asset'}."
            elif "fleet_aggregation_service" in missing:
                kind = "fleet_evidence_gap"
                summary = "Fleet question required aggregation evidence before ranking."
            else:
                kind = "evidence_gap"
                summary = f"Chat required clarification for missing {', '.join(missing)}."
            return {
                "learning_kind": kind,
                "asset_ids": asset_ids,
                "intent": intent,
                "summary": summary,
                "reason": "Agent 8 captured a reusable clarification/evidence gap.",
                "evidence": {"missing_fields": missing},
            }

        rag = result.get("rag", {})
        if isinstance(rag, dict) and rag.get("status") in {
            "no_match", "retrieval_failed", "blocked",
        }:
            status = str(rag.get("status", ""))
            return {
                "learning_kind": "knowledge_gap",
                "asset_ids": asset_ids,
                "intent": intent,
                "summary": f"RAG could not ground the chat answer: {status}.",
                "reason": "Agent 8 captured a reusable knowledge retrieval gap.",
                "evidence": {
                    "rag_status": status,
                    "retrieval_query": str(rag.get("retrieval_query", "")),
                },
            }

        multi = result.get("multi_asset_results", [])
        if isinstance(multi, list):
            unavailable = [
                str(row.get("asset", "") or row.get("asset_id", "")).upper()
                for row in multi if isinstance(row, dict)
                and row.get("status") == "unavailable"
            ]
            unavailable = [item for item in unavailable if item]
            if unavailable:
                return {
                    "learning_kind": "partial_multi_asset_gap",
                    "asset_ids": sorted(set(asset_ids + unavailable)),
                    "intent": intent,
                    "summary": (
                        "Multi-asset answer had evidence for some assets but "
                        f"not for {', '.join(unavailable)}."
                    ),
                    "reason": "Agent 8 captured a reusable partial evidence gap.",
                    "evidence": {"unavailable_assets": unavailable},
                }

        for field in (
            "hitl_required", "hitl_monitoring", "hitl_diagnosis",
            "hitl_knowledge", "hitl_advisory",
        ):
            gate = result.get(field)
            if isinstance(gate, dict):
                gate_type = gate.get("type") or field.replace("hitl_", "")
                return {
                    "learning_kind": "decision_pending",
                    "asset_ids": asset_ids,
                    "intent": intent,
                    "summary": f"Chat created a pending {gate_type} HITL decision.",
                    "reason": "Agent 8 captured a pending human decision point.",
                    "evidence": {"gate": gate_type},
                }

        return {
            "learning_kind": "",
            "asset_ids": asset_ids,
            "intent": intent,
            "reason": "no new operational learning signal was detected",
        }

    @staticmethod
    def _template(execution, feedback):
        return (f"Closed maintenance case {feedback.case_id} for asset "
            f"{feedback.asset_id}, bearing {feedback.bearing_id}. Confirmed fault: "
            f"{feedback.confirmed_fault_mode}. Root cause: {feedback.root_cause}. "
            f"Action taken: {feedback.action_taken}. Recommendation followed: "
            f"{feedback.recommendation_followed}. Technician notes: {feedback.technician_notes}. "
            f"Post-repair vibration {feedback.post_repair_vib_mm_s} mm/s and temperature "
            f"{feedback.post_repair_temp_c} C. Execution audit {execution.audit_reference}.")

    def _optional_llm(self, fallback, feedback):
        if not self._cfg.llm_narrative_enabled or self._llm is None: return fallback, "template"
        try:
            if hasattr(self._llm, "is_configured") and not self._llm.is_configured(): return fallback, "template"
            response = self._llm.complete_json(system_prompt=(
                "Rewrite only the learned-case narrative. Preserve all confirmed labels and measurements. "
                "Return exactly one JSON object with one string field named narrative; no markdown."),
                user_prompt=fallback, temperature=0.1, max_tokens=500)
            text = response.get("narrative", "") if isinstance(response, dict) else ""
            # Require the immutable confirmed fault and action to remain visible.
            if isinstance(text, str) and feedback.confirmed_fault_mode in text and feedback.action_taken in text:
                return text[:self._cfg.llm_max_characters], "template+llm_narrative"
        except Exception: pass
        return fallback, "template"

    def _export_training_row(self, execution, feedback):
        path = Path(self._cfg.training_export_path); path.parent.mkdir(parents=True, exist_ok=True)
        row = {**feedback.model_dump(), "execution_status": execution.status,
               "execution_action": execution.action_taken}
        with path.open("a", encoding="utf-8") as fh: fh.write(json.dumps(row, sort_keys=True) + "\n")
# ***********************
