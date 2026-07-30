"""Agent 6: rank executable maintenance actions under operational constraints."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone

from src.schemas.risk import RiskAssessment
from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.knowledge import KnowledgeGuidance
from src.schemas.recommendation import (
    Contributor, MaintenanceRecommendation, RecommendedAction, RequiredPart,
)
from src.tools.config_loader import PrescriptiveConfig, load_prescriptive_config


# ************** Added by Prateek Mittal on 20th July 2026 ******************
class PrescriptiveOptimizationAgent:
    """Deterministic decision owner with an optional, non-authoritative LLM writer."""

    def __init__(self, cfg: PrescriptiveConfig = None, llm_client=None, now_fn=None):
        self._cfg = cfg or load_prescriptive_config()
        self._cfg.validate()
        self._llm = llm_client
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        encoded = json.dumps(asdict(self._cfg), sort_keys=True).encode()
        self._version = hashlib.sha256(encoded).hexdigest()[:16]

    def process(self, risk: RiskAssessment, diagnosis: FaultDiagnosis,
                guidance: KnowledgeGuidance, inventory_lookup: dict,
                context_lookup: dict) -> MaintenanceRecommendation:
        reason = self._input_error(risk, diagnosis, guidance, inventory_lookup, context_lookup)
        if reason:
            return self._empty(risk, reason)

        cfg = self._cfg
        risk_level = risk.risk_level.lower()
        actions = list(cfg.risk_actions[risk_level])
        if diagnosis.fault_mode == "lubrication_issue" and risk_level != "low":
            actions.insert(0, "lubrication_service")
        # Critical bottlenecks always use the safest roadmap-defined action.
        if risk_level == "critical" and risk.business_impact_flag:
            actions = ["stop_and_replace"] + [a for a in actions if a != "stop_and_replace"]

        part_number = self._part_number(diagnosis, guidance, context_lookup)
        required_parts = []
        if actions[0] in {"stop_and_replace", "repair_next_planned_stop", "lubrication_service"}:
            required_parts = [RequiredPart(
                part_number=part_number, quantity=1,
                lead_time_days=self._inventory_value(inventory_lookup, part_number, "lead_time_days", 0),
            )]
        available = self._inventory_value(inventory_lookup, part_number, "qty_on_hand", 0)
        procurement = bool(required_parts and available < required_parts[0].quantity)

        chosen = actions[0]
        duration = cfg.durations_hours[chosen]
        window = self._choose_window(context_lookup.get("planned_stop_windows", []), duration)
        if chosen == "repair_next_planned_stop" and not window:
            chosen = "derate_and_monitor" if risk_level in {"high", "critical"} else "inspect_next_shift"
            duration = cfg.durations_hours[chosen]
        approval_required = chosen in cfg.approval_actions
        rationale = self._rule_rationale(risk, diagnosis, chosen, procurement, window)
        rationale, source = self._optional_llm_rationale(rationale, risk, diagnosis, guidance)

        ranked = [
            {"rank": i + 1, "action": action,
             "estimated_duration_hours": cfg.durations_hours[action],
             "selected": action == chosen}
            for i, action in enumerate(dict.fromkeys(actions))
        ]
        personnel = cfg.personnel
        return MaintenanceRecommendation(
            case_id=risk.case_id, asset_id=risk.asset_id, bearing_id=risk.bearing_id,
            recommended_action=RecommendedAction(
                name=chosen, description=chosen.replace("_", " "),
                estimated_duration_hours=duration,
            ), urgency=cfg.urgency[risk_level], ranked_alternatives=ranked,
            required_parts=required_parts, window_chosen=window.get("window_id", "") if window else "",
            rationale=rationale,
            evidence={"risk_level": risk_level, "rul_max_days": risk.rul_max_days,
                      "business_impact_flag": risk.business_impact_flag,
                      "part_number": part_number, "qty_on_hand": available,
                      "window_evaluated": bool(context_lookup.get("planned_stop_windows"))},
            recommendation_status="ok", approval_status="pending" if approval_required else "approved",
            approval_required=approval_required, procurement_required=procurement,
            responsible_person=personnel["responsible_person"],
            responsible_person_id=personnel["responsible_person_id"],
            responsible_approver=personnel["responsible_approver"],
            responsible_approver_id=personnel["responsible_approver_id"],
            contributors=[Contributor(role="Reliability Engineer", name="DRO Agent 6",
                                      concern="risk, guidance, inventory and window constraints")],
            generated_at_utc=self._now().isoformat(), status_reason="validated ranked recommendation",
            prescriptive_config_version=self._version,
            source_risk_config_version=risk.risk_config_version,
            source_knowledge_config_version=guidance.knowledge_config_version,
            source_knowledge_index_version=guidance.knowledge_index_version,
            linked_risk_case_id=risk.case_id, rationale_source=source,
            is_llm_suggested=source == "rules+llm_rationale",
        )

    @staticmethod
    def _input_error(risk, diagnosis, guidance, inventory, context) -> str:
        if not isinstance(risk, RiskAssessment) or not isinstance(diagnosis, FaultDiagnosis):
            return "risk and diagnosis must use their typed contracts"
        if not isinstance(guidance, KnowledgeGuidance):
            return "guidance must use KnowledgeGuidance"
        if not isinstance(inventory, dict) or not isinstance(context, dict):
            return "inventory and context lookups must be dictionaries"
        if not risk.risk_eligible or risk.assessment_status not in {"assessed", "monitor"}:
            return "risk is not eligible for recommendation"
        if not diagnosis.diagnostic_eligible or diagnosis.case_id != risk.case_id:
            return "diagnosis and risk handoff is invalid"
        if (risk.asset_id != diagnosis.asset_id or risk.bearing_id != diagnosis.bearing_id):
            return "diagnosis and risk identities do not match"
        if guidance.case_id != risk.case_id or guidance.linked_risk_case_id not in {"", risk.case_id}:
            return "knowledge and risk case identities do not match"
        if guidance.guidance_status != "grounded" or not guidance.guidance_eligible:
            return "source-grounded guidance is required before prescribing work"
        if risk.risk_level not in {"low", "medium", "high", "critical"}:
            return "risk level is unsupported"
        return ""

    def _empty(self, risk, reason):
        return MaintenanceRecommendation(
            case_id=getattr(risk, "case_id", ""), asset_id=getattr(risk, "asset_id", ""),
            bearing_id=getattr(risk, "bearing_id", ""), recommendation_status="invalid_input",
            recommendation_eligible=False, status_reason=reason, approval_status="rejected",
            generated_at_utc=self._now().isoformat(), prescriptive_config_version=self._version,
        )

    def _part_number(self, diagnosis, guidance, context):
        if diagnosis.fault_mode == "lubrication_issue":
            return self._cfg.lubrication_part
        return str(context.get("bearing_part_number") or guidance.bearing_type or "UNKNOWN_BEARING")

    @staticmethod
    def _inventory_value(inventory, part, field, default):
        entry = inventory.get(part, {})
        if isinstance(entry, int):
            return entry if field == "qty_on_hand" else default
        return entry.get(field, default) if isinstance(entry, dict) else default

    @staticmethod
    def _choose_window(windows, duration):
        if not isinstance(windows, list):
            return None
        valid = [w for w in windows if isinstance(w, dict)
                 and isinstance(w.get("duration_hours"), (int, float))
                 and w["duration_hours"] >= duration and w.get("available", True)]
        return valid[0] if valid else None

    @staticmethod
    def _rule_rationale(risk, diagnosis, action, procurement, window):
        text = (f"{diagnosis.fault_mode} at {risk.risk_level} risk with RUL "
                f"{risk.rul_min_days}-{risk.rul_max_days} days ranks {action} first.")
        if risk.business_impact_flag:
            text += " Asset criticality/bottleneck impact increases priority."
        if procurement:
            text += " Required stock is unavailable; procurement is required."
        if window:
            text += f" Window {window.get('window_id', '')} can fit the job."
        return text

    def _optional_llm_rationale(self, fallback, risk, diagnosis, guidance):
        if not self._cfg.llm_rationale_enabled or self._llm is None:
            return fallback, "rules"
        try:
            if hasattr(self._llm, "is_configured") and not self._llm.is_configured():
                return fallback, "rules"
            response = self._llm.complete_json(
                system_prompt=(
                    "Rewrite the supplied maintenance rationale only. Do not change the action or facts. "
                    "Return exactly one JSON object with one string field named rationale; no markdown."
                ),
                user_prompt=json.dumps({"rationale": fallback, "risk": risk.risk_level,
                                        "fault": diagnosis.fault_mode,
                                        "sources": guidance.source_documents}),
                temperature=0.1, max_tokens=300,
            )
            text = response.get("rationale", "") if isinstance(response, dict) else ""
            if isinstance(text, str) and text.strip():
                return text.strip()[:self._cfg.llm_max_characters], "rules+llm_rationale"
        except Exception:
            pass
        return fallback, "rules"
# ***********************
