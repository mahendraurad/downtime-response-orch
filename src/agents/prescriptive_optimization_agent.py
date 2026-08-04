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
    ConfidenceSummary, ConditionSummary, Consequence, Contributor,
    DecisionSupport,
    HistoricalCaseCitation, MaintenanceRecommendation, PrescriptiveAction,
    PartsRULComparison, RecommendedAction, RequiredPart, Verdict,
)
from src.schemas.persona import PersonaContext
from src.tools.persona_formatter import build_persona_context, persona_prompt
from src.tools.config_loader import PrescriptiveConfig, load_prescriptive_config
from src.tools.decision_support_config import (
    decision_support_config_version, load_decision_support_config,
)


# ************** Added by Prateek Mittal on 20th July 2026 ******************
class PrescriptiveOptimizationAgent:
    """Deterministic decision owner with an optional, non-authoritative LLM writer."""

    def __init__(self, cfg: PrescriptiveConfig = None, llm_client=None,
                 now_fn=None, historical_case_fn=None,
                 decision_support_config=None):
        self._cfg = cfg or load_prescriptive_config()
        self._cfg.validate()
        self._llm = llm_client
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._historical_case_fn = historical_case_fn
        self._decision_support = (
            decision_support_config or load_decision_support_config()
        )
        self._decision_support_version = decision_support_config_version(
            self._decision_support
        )
        encoded = json.dumps(asdict(self._cfg), sort_keys=True).encode()
        self._version = hashlib.sha256(encoded).hexdigest()[:16]

    def process(self, risk: RiskAssessment, diagnosis: FaultDiagnosis,
                guidance: KnowledgeGuidance, inventory_lookup: dict,
                context_lookup: dict, persona_context=None,
                trusted_signal=None) -> MaintenanceRecommendation:
        reason = self._input_error(risk, diagnosis, guidance, inventory_lookup, context_lookup)
        if reason:
            return self._empty(risk, reason)

        cfg = self._cfg
        persona = build_persona_context(persona_context or "supervisor")
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
        rationale, source = self._optional_llm_rationale(
            rationale, risk, diagnosis, guidance, persona
        )

        unique_actions = list(dict.fromkeys(actions))
        ranked = []
        prescriptive_actions = []
        for i, action in enumerate(unique_actions):
            selected = action == chosen
            action_rationale = (
                rationale if selected else
                f"{action} remains an alternative but ranks below {chosen} "
                f"for the validated {risk.risk_level} risk and "
                f"{risk.rul_min_days}-{risk.rul_max_days} day RUL window."
            )
            score = self._action_score(risk, i, selected)
            ranked.append({
                "rank": i + 1, "action": action,
                "estimated_duration_hours": cfg.durations_hours[action],
                "selected": selected, "rationale": action_rationale,
                "urgency": self._contract_urgency(cfg.urgency[risk_level]),
                "prescriptive_score": score,
            })
            prescriptive_actions.append(PrescriptiveAction(
                rank=i + 1, action=action, rationale=action_rationale,
                urgency=self._contract_urgency(cfg.urgency[risk_level]),
                prescriptive_score=score,
            ))
        historical_rows = self._historical_cases(
            risk.asset_id, diagnosis.fault_mode
        )
        confidence = self._confidence(
            risk, diagnosis, trusted_signal, len(historical_rows)
        )
        condition = self._condition(risk, diagnosis, trusted_signal)
        verdict = self._verdict(risk, diagnosis, chosen, rationale, persona)
        personnel = cfg.personnel
        citations = [
            HistoricalCaseCitation.model_validate(row)
            for row in historical_rows
        ]
        parts_vs_rul = self._parts_vs_rul(required_parts, risk)
        decision_support = self._build_decision_support(
            chosen, duration, risk, trusted_signal, persona,
            parts_vs_rul, citations,
        )
        consequences = self._consequences(risk, decision_support)
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
            verdict=verdict, condition=condition,
            consequences=consequences,
            prescriptive_actions=prescriptive_actions,
            confidence=confidence, persona_context=persona,
            historical_cases=citations,
            sop_citations=list(guidance.source_documents),
            cost_data_status=decision_support.cost_data_status,
            authority_check=decision_support.authority_check,
            decision_support=decision_support,
        )

    def _build_decision_support(self, action, duration, risk, trusted_signal,
                                persona, parts_vs_rul, citations):
        cfg = self._decision_support
        cost_cfg = cfg["cost_model"]
        override = cost_cfg.get("asset_overrides", {}).get(risk.asset_id, {})
        action_cost = cost_cfg["action_costs"].get(action)
        downtime_rate = float(
            getattr(getattr(trusted_signal, "asset_ctx", None),
                    "downtime_cost_per_hour", 0.0) or 0.0
        )
        if override:
            approved = float(override["cost_if_approved"])
            deferred = float(override["cost_if_deferred"])
            deferred_per_hour = float(override["deferred_cost_per_hour"])
            breakdown = "configured asset-level demo cost override"
            basis = str(override.get("basis", "configured demo cost model"))
            status = "configured_demo"
        elif action_cost and downtime_rate > 0:
            parts = float(action_cost["parts"])
            labour = float(action_cost["labour"])
            downtime = round(float(duration) * downtime_rate, 2)
            approved = round(parts + labour + downtime, 2)
            deferred = round(float(risk.financial_exposure), 2)
            deferred_per_hour = downtime_rate
            breakdown = (
                f"parts {parts:.0f} + labour {labour:.0f} + "
                f"planned downtime {downtime:.0f}"
            )
            basis = "config action costs plus Agent 1 asset downtime rate"
            status = "configured_demo"
        else:
            approved = deferred = deferred_per_hour = None
            breakdown = ""
            basis = "cost inputs unavailable"
            status = "unavailable"

        limit = cfg["authority_usd"].get(persona.id)
        if limit is None or approved is None:
            authority_check = "not_evaluated"
            authority_reason = "persona is not an approver or cost inputs are unavailable"
        elif approved <= float(limit):
            authority_check = "within_authority"
            authority_reason = (
                f"{cfg['currency']} {approved:,.0f} is within the "
                f"{persona.role} limit of {cfg['currency']} {float(limit):,.0f}"
            )
        else:
            authority_check = "requires_escalation"
            hierarchy = cfg["approval_hierarchy"]
            idx = hierarchy.index(persona.id)
            next_role = hierarchy[min(idx + 1, len(hierarchy) - 1)]
            authority_reason = (
                f"{cfg['currency']} {approved:,.0f} exceeds the "
                f"{persona.role} limit; escalate to {next_role}"
            )
        return DecisionSupport(
            cost_if_approved=approved,
            cost_if_deferred=deferred,
            deferred_cost_per_hour=deferred_per_hour,
            cost_breakdown=breakdown,
            cost_basis=basis,
            currency=cfg["currency"],
            cost_data_status=status,
            parts_vs_rul=parts_vs_rul,
            historical_cases=citations,
            authority_check=authority_check,
            authority_reason=authority_reason,
            authority_limit=float(limit) if limit is not None else None,
            decision_support_config_version=self._decision_support_version,
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

    def _optional_llm_rationale(self, fallback, risk, diagnosis, guidance,
                                persona: PersonaContext):
        if not self._cfg.llm_rationale_enabled or self._llm is None:
            return fallback, "rules"
        try:
            if hasattr(self._llm, "is_configured") and not self._llm.is_configured():
                return fallback, "rules"
            response = self._llm.complete_json(
                system_prompt=(
                    "You are the prescriptive reasoning agent in a maintenance "
                    "pipeline. Responses are verdict-first and must explain why "
                    "the chosen action ranks above alternatives, frame the "
                    "consequence of inaction, and surface uncertainty. Rewrite "
                    "only the supplied grounded rationale. Never add sensor, "
                    "financial, historical, or confidence facts. Return exactly "
                    "one JSON object with one string field named rationale.\n\n"
                    + persona_prompt(persona)
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

    @staticmethod
    def _contract_urgency(urgency):
        return {
            "immediate": "now", "urgent": "this_shift",
            "planned": "this_week", "monitor": "this_week",
        }[urgency]

    @staticmethod
    def _action_score(risk, index, selected):
        base = max(0.0, min(1.0, float(risk.confidence)))
        return round(base if selected else max(0.0, base - 0.2 * (index + 1)), 4)

    @staticmethod
    def _condition(risk, diagnosis, trusted):
        raw = getattr(trusted, "raw", None)
        bearing = getattr(trusted, "bearing_ctx", None)
        return ConditionSummary(
            fault_type=diagnosis.fault_mode,
            fault_stage=diagnosis.iso_stage,
            rul_min_days=risk.rul_min_days,
            rul_max_days=risk.rul_max_days,
            failure_probability=risk.failure_probability,
            vibration_mms=getattr(raw, "vib_rms_mm_s", None),
            vibration_threshold=getattr(bearing, "vib_max_valid", None),
            temperature_c=getattr(raw, "temp_c", None),
            temperature_threshold=getattr(bearing, "temp_max_valid", None),
        )

    @staticmethod
    def _consequences(risk, decision_support):
        has_costs = (
            decision_support.cost_if_approved is not None
            and decision_support.cost_if_deferred is not None
        )
        return [
            Consequence(
                type="time",
                description="Validated remaining useful life window",
                value=f"{risk.rul_min_days}-{risk.rul_max_days} days",
            ),
            Consequence(
                type="financial",
                description="Cost if approved versus deferred",
                value=(
                    f"{decision_support.currency} "
                    f"{decision_support.cost_if_approved:,.0f} approved versus "
                    f"{decision_support.currency} "
                    f"{decision_support.cost_if_deferred:,.0f} deferred"
                    if has_costs else None
                ),
                evidence_status="configured_demo" if has_costs else "unavailable",
            ),
            Consequence(
                type="cascade",
                description=(
                    "Business-impact flag is set; affected downstream asset "
                    "identities require validated fleet evidence."
                    if risk.business_impact_flag else
                    "No validated cascade impact is available."
                ),
                value=None, evidence_status="unavailable",
            ),
        ]

    @staticmethod
    def _confidence(risk, diagnosis, trusted, sample_size):
        data_score = float(getattr(trusted, "data_quality_score", 0.0) or 0.0)
        values = {
            "fault_identification": float(diagnosis.confidence),
            "rul_prediction": float(risk.confidence),
            "recommendation": float(risk.confidence),
            "data_completeness": data_score,
        }
        warnings = [
            f"{name} confidence is below 0.80"
            for name, value in values.items() if value < 0.8
        ]
        return ConfidenceSummary(
            **values, training_sample_size=sample_size, warnings=warnings
        )

    @staticmethod
    def _verdict(risk, diagnosis, action, rationale, persona):
        urgency = (
            "requires immediate intervention"
            if risk.risk_level == "critical" else
            f"requires {action.replace('_', ' ')}"
        )
        headline = (
            f"{risk.asset_id} {urgency}; the validated "
            f"{risk.rul_min_days}-{risk.rul_max_days} day RUL window does not "
            "support an unsupported run-to-failure decision."
        )
        reasoning = rationale
        if persona.response_depth == "executive":
            reasoning = (
                f"{diagnosis.fault_mode.replace('_', ' ')} is validated at "
                f"{diagnosis.confidence:.0%} confidence. {rationale}"
            )
        return Verdict(headline=headline, reasoning=reasoning)

    def _historical_cases(self, asset_id, fault_mode):
        if self._historical_case_fn is None:
            return []
        try:
            rows = self._historical_case_fn(asset_id, fault_mode, 3)
        except Exception:
            return []
        return [row for row in rows if isinstance(row, dict)][:3]

    @staticmethod
    def _parts_vs_rul(required_parts, risk):
        if not required_parts:
            return PartsRULComparison(
                rul_min_days=risk.rul_min_days, rul_max_days=risk.rul_max_days,
                status="not_required",
                explanation="selected action does not require a reserved part",
            )
        part = required_parts[0]
        lead = part.lead_time_days
        status = (
            "within_rul_window" if lead <= risk.rul_max_days
            else "outside_rul_window"
        )
        return PartsRULComparison(
            part_number=part.part_number, eta_days=lead,
            rul_min_days=risk.rul_min_days, rul_max_days=risk.rul_max_days,
            status=status,
            explanation=(
                f"part ETA is {lead} days versus validated RUL window "
                f"{risk.rul_min_days}-{risk.rul_max_days} days"
            ),
        )
# ***********************
