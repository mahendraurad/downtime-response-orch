"""
agents/prescriptive_optimization_agent.py  —  Phase 7

Prescriptive Optimization Agent.
Receives three validated inputs from upstream agents:
  - FaultDiagnosis    (Phase 4): what kind of fault was found
  - RiskAssessment    (Phase 5): how likely / how soon / how costly
  - KnowledgeGuidance (Phase 6): what the SOPs and past cases say
Produces a MaintenanceRecommendation for the Executor Agent (Phase 9) to act on.

Adapted from the Prescriptive-Optimization-Agent branch of
mahendraurad/downtime-response-orch into DRO's existing package structure.

Key adaptations vs. the source branch:
  - Import paths use src.* prefix (DRO package layout)
  - DRO FaultDiagnosis uses severity="low/medium/high/critical" and iso_stage=0-3;
    catalog matching uses _diagnosis_stage() to derive "stage_1/2/3/monitor"
  - DRO FaultDiagnosis.evidence is Dict[str, Any]; evidence list built from values
  - DRO schema uses RecommendedAction (not Action)
  - Config thresholds are module-level constants (no app_config.py)
  - Wrapped in PrescriptiveOptimizationAgent class with .process() method
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.risk import RiskAssessment
from src.schemas.knowledge import KnowledgeGuidance
from src.schemas.recommendation import MaintenanceRecommendation, RecommendedAction, RequiredPart
from src.tools.data_loader import load_action_catalog, load_assets, load_fault_taxonomy, load_personas
from src.tools.inventory_checker import check_part_for_action
from src.tools.schedule_reader import find_windows
from src.tools.action_ranker import rank_actions
from src.tools.rationale_writer import write_rationale

# ── Tunable thresholds (edit config/prescriptive_config.json to override) ────
MIN_RELIABLE_CONFIDENCE       = 0.5    # below this, diagnosis is too unreliable to act on
CONTRIBUTOR_REVIEW_CONFIDENCE = 0.6    # below this, a reliability engineer reviews
MONITOR_APPROPRIATENESS       = 0.3    # score multiplier for the inspect-and-monitor fallback
DEFAULT_DURATION_HOURS        = 2.0    # assumed action duration when none is specified

# Severity order for catalog matching — maps DRO iso_stage to catalog stage labels
_STAGE_LABEL = {0: "monitor", 1: "stage_1", 2: "stage_2", 3: "stage_3"}
_STAGE_ORDER = {"monitor": 0, "stage_1": 1, "stage_2": 2, "stage_3": 3}

# Statuses that always escalate to the Maintenance Manager
_ESCALATION_STATUSES = {
    "blocked_no_part", "blocked_unknown_asset",
    "unreliable_diagnosis", "catalog_miss",
    "blocked_invalid_input",
}


def _diagnosis_stage(diagnosis: FaultDiagnosis) -> str:
    """
    Convert DRO FaultDiagnosis to catalog-compatible stage label.
    Uses iso_stage (precise int 0-3) rather than the severity string,
    since iso_stage is the canonical stage assigned by the FI agent.
    """
    iso = getattr(diagnosis, "iso_stage", 0) or 0
    return _STAGE_LABEL.get(iso, "monitor")


def _evidence_list(diagnosis: FaultDiagnosis) -> list[str]:
    """
    DRO stores evidence as Dict[str, Any].
    Return a flat list of string values for display/prompts.
    """
    ev = getattr(diagnosis, "evidence", None) or {}
    if isinstance(ev, dict):
        return [str(v) for v in ev.values() if v]
    return [str(x) for x in ev if x]


def _tiebreak_key(candidate: dict) -> tuple:
    is_esc   = 1 if candidate.get("is_escalation") else 0
    duration = candidate.get("est_duration_hours") or 999
    return (is_esc, duration)


# ── Persona resolution ────────────────────────────────────────────────────────

def _resolve_approver(
    urgency: str,
    recommendation_status: str,
    business_impact: bool,
    risk_level: str = "",
) -> tuple[str, str]:
    """
    Pick the single APPROVER from the severity hierarchy in personas.json.
    Returns ("Role — Name", persona_id).
    """
    approvers = load_personas().get("approvers", {})
    escalate = (
        recommendation_status in _ESCALATION_STATUSES
        or urgency == "emergency"
        or bool(business_impact)
        or (urgency == "urgent" and risk_level == "critical")
    )
    tier = "escalated" if escalate else "normal"
    appr = approvers.get(tier, {})
    role = appr.get("role", "Plant Supervisor")
    name = appr.get("name", "James Kowalski")
    pid  = appr.get("id", "PERSONA_SUP")
    return f"{role} — {name}", pid


def _resolve_contributors(
    recommendation_status: str,
    action_name: str,
    diagnosis: FaultDiagnosis,
) -> list[dict]:
    """
    Build the list of CONTRIBUTORS who must weigh in.
    A contributor is added ONLY when its trigger condition is met.
    """
    contributors = load_personas().get("contributors", {})

    action     = (action_name or "").lower()
    fault      = (getattr(diagnosis, "fault_mode", "") or "").lower()
    component  = (getattr(diagnosis, "affected_component", "") or "").lower()
    confidence = getattr(diagnosis, "confidence", 1.0)

    triggers = [
        (
            "diagnosis",
            recommendation_status in ("unreliable_diagnosis", "catalog_miss")
            or confidence < CONTRIBUTOR_REVIEW_CONFIDENCE,
        ),
        (
            "sensor",
            recommendation_status in ("unreliable_diagnosis", "blocked_unknown_asset")
            or "sensor" in fault or "signal" in fault
            or "sensor" in component or "signal" in component
            or "sensor" in action,
        ),
        (
            "parts",
            recommendation_status == "blocked_no_part"
            or "replace" in action or "lubric" in action,
        ),
        (
            "safety",
            "replace" in action or "replacement" in action,
        ),
    ]

    selected: list[dict] = []
    for key, condition in triggers:
        if not condition:
            continue
        person = contributors.get(key)
        if not person:
            continue
        entry = {
            "role":    person.get("role", ""),
            "name":    person.get("name", ""),
            "concern": person.get("concern", ""),
        }
        if entry not in selected:
            selected.append(entry)
    return selected


# ── Guard helpers ─────────────────────────────────────────────────────────────

def _get_asset_type(asset_id: str) -> Optional[str]:
    for asset in load_assets():
        if asset.get("asset_id") == asset_id:
            return asset.get("asset_type")
    return None


def _is_catalog_miss(candidates: list[dict]) -> bool:
    return (
        len(candidates) == 1
        and candidates[0].get("action_name") == "inspect_and_monitor"
        and candidates[0].get("source_sop") is None
    )


def _get_action_type(action_name: str, candidate: Optional[dict] = None) -> Optional[str]:
    if candidate is not None:
        pt = candidate.get("part_type")
        if pt is not None:
            return pt
    name = (action_name or "").lower()
    if "sensor" in name:
        return None
    if "replace" in name or "bearing" in name:
        return "replace_bearing"
    if "lubric" in name:
        return "lubricate"
    return None


def _check_part_blocked(
    action_name: str, diagnosis: FaultDiagnosis, risk: RiskAssessment
) -> Optional[dict]:
    action_type = _get_action_type(action_name)
    if action_type is None:
        return None

    inv    = check_part_for_action(diagnosis.bearing_id, action_type)
    status = inv.get("status")

    if status == "no_matching_part":
        return {"part_model": "unknown", "lead_time_days": None, "reason": "no_matching_part"}

    if status == "out_of_stock":
        lead_time = inv.get("lead_time_days") or 0
        if lead_time > risk.rul_min_days:
            return {
                "part_model":    inv.get("part_model") or "unknown",
                "lead_time_days": lead_time,
                "reason":        "lead_exceeds_rul",
            }
    return None


def _urgency_from_risk_level(risk: RiskAssessment) -> str:
    return {"critical": "emergency", "high": "urgent", "medium": "urgent", "low": "planned"}.get(
        risk.risk_level, "urgent"
    )


# ── Failure-path recommendation builders ─────────────────────────────────────

def _make_failure_rec(
    diagnosis: FaultDiagnosis, risk: RiskAssessment, status: str, urgency: str,
    action_name: str, action_desc: str, guidance: KnowledgeGuidance,
    required_parts: Optional[list] = None,
) -> MaintenanceRecommendation:
    decision = {
        "recommendation_status": status,
        "fault_mode":   diagnosis.fault_mode,
        "severity":     diagnosis.severity,
        "asset_id":     diagnosis.asset_id,
        "bearing_id":   diagnosis.bearing_id,
        "rul_min_days": risk.rul_min_days,
        "confidence":   diagnosis.confidence,
    }
    business_impact = bool(getattr(risk, "business_impact_flag", False))
    approver, approver_id = _resolve_approver(urgency, status, business_impact)
    contributors = _resolve_contributors(status, action_name, diagnosis)

    evidence: dict[str, str] = {
        "fault":              f"Diagnosed by Phase 4 (Failure Intelligence), case {diagnosis.case_id}",
        "risk":               f"Risk assessment by Phase 5 — RUL {risk.rul_min_days} days",
        "recommended_action": "N/A — recommendation blocked before action selection",
        "part":               "N/A — recommendation blocked",
        "window":             "N/A — no window assigned for blocked recommendation",
        "responsible_person": "Approval hierarchy — failure/blocked status escalates to Plant Manager",
        "urgency":            f"Derived from risk level ({risk.risk_level}) — blocked cases use risk-level urgency",
        "guidance": (
            f"Phase 6 (Knowledge) — {', '.join(guidance.source_documents)}"
            + (f"; sections: {', '.join(guidance.relevant_sections)}" if guidance.relevant_sections else "")
            if guidance.source_documents
            else "Phase 6 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }
    if status == "blocked_unknown_asset":
        evidence["fault"] = f"Asset {diagnosis.asset_id} not found in asset master data"
    elif status == "unreliable_diagnosis":
        evidence["fault"] = (
            f"Diagnosis confidence {diagnosis.confidence:.0%} is below the "
            f"{MIN_RELIABLE_CONFIDENCE:.0%} threshold — case {diagnosis.case_id}"
        )
    elif status == "catalog_miss":
        evidence["recommended_action"] = (
            f"No approved SOP in action catalog for "
            f"{diagnosis.fault_mode} / {_diagnosis_stage(diagnosis)}"
        )

    rationale = write_rationale(decision)
    _steps  = "; ".join(guidance.inspection_steps) if guidance.inspection_steps else ""
    _safety = "; ".join(guidance.safety_notes) if guidance.safety_notes else ""
    if _steps:
        rationale += "\n\nINSPECTION STEPS (per SOP): " + _steps
    if _safety:
        rationale += "\n\nSAFETY NOTES: " + _safety

    return MaintenanceRecommendation(
        case_id=diagnosis.case_id,
        asset_id=diagnosis.asset_id,
        bearing_id=diagnosis.bearing_id,
        fault_mode=getattr(diagnosis, "fault_mode", ""),
        asset_type=_get_asset_type(diagnosis.asset_id) or "",
        iso_stage=getattr(diagnosis, "iso_stage", 0) or 0,
        recommended_action=RecommendedAction(
            name=action_name,
            description=action_desc,
            estimated_duration_hours=0,
        ),
        urgency=urgency,
        ranked_alternatives=[],
        required_parts=required_parts or [],
        window_chosen=None,
        rationale=rationale,
        recommendation_status=status,
        approval_status="escalated",
        responsible_person=approver,
        responsible_person_id=approver_id,
        responsible_approver=approver,
        responsible_approver_id=approver_id,
        contributors=contributors,
        evidence=evidence,
        generated_at_utc=datetime.now(tz=timezone.utc).isoformat(),
    )


def _make_blocked_no_part_rec(
    action_name: str, block: dict, diagnosis: FaultDiagnosis,
    risk: RiskAssessment, guidance: KnowledgeGuidance,
) -> MaintenanceRecommendation:
    part_model = block.get("part_model", "unknown")
    lead_time  = block.get("lead_time_days")
    rul_min    = risk.rul_min_days

    decision = {
        "recommendation_status": "blocked_no_part",
        "fault_mode":        diagnosis.fault_mode,
        "severity":          diagnosis.severity,
        "asset_id":          diagnosis.asset_id,
        "bearing_id":        diagnosis.bearing_id,
        "rul_min_days":      rul_min,
        "recommended_action": action_name,
        "part_number":       part_model,
        "lead_time_days":    lead_time,
    }
    rationale = write_rationale(decision)
    _steps  = "; ".join(guidance.inspection_steps) if guidance.inspection_steps else ""
    _safety = "; ".join(guidance.safety_notes) if guidance.safety_notes else ""
    if _steps:
        rationale += "\n\nINSPECTION STEPS (per SOP): " + _steps
    if _safety:
        rationale += "\n\nSAFETY NOTES: " + _safety

    parts = []
    if part_model != "unknown":
        parts = [RequiredPart(part_number=part_model, quantity=1, lead_time_days=lead_time or 0)]

    blocked_urgency = _urgency_from_risk_level(risk)
    business_impact = bool(getattr(risk, "business_impact_flag", False))
    approver, approver_id = _resolve_approver(blocked_urgency, "blocked_no_part", business_impact)
    contributors = _resolve_contributors("blocked_no_part", action_name, diagnosis)

    part_evidence = (
        f"Inventory record — part {part_model} out of stock; "
        f"lead time {lead_time}d exceeds RUL {risk.rul_min_days}d"
        if part_model != "unknown" and lead_time is not None
        else "Inventory record — no matching part found for this bearing / action"
    )

    evidence: dict[str, str] = {
        "fault":              f"Diagnosed by Phase 4, case {diagnosis.case_id}",
        "risk":               f"Risk assessment by Phase 5 — RUL {risk.rul_min_days} days",
        "recommended_action": f"Action catalog — {action_name} is the correct action but blocked on parts",
        "part":               part_evidence,
        "window":             "No window — action blocked on part availability",
        "responsible_person": "Approval hierarchy — blocked_no_part escalates to Plant Manager",
        "urgency":            f"Derived from risk level ({risk.risk_level})",
        "guidance": (
            f"Phase 6 (Knowledge) — {', '.join(guidance.source_documents)}"
            if guidance.source_documents
            else "Phase 6 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }

    return MaintenanceRecommendation(
        case_id=diagnosis.case_id,
        asset_id=diagnosis.asset_id,
        bearing_id=diagnosis.bearing_id,
        fault_mode=getattr(diagnosis, "fault_mode", ""),
        asset_type=_get_asset_type(diagnosis.asset_id) or "",
        iso_stage=getattr(diagnosis, "iso_stage", 0) or 0,
        recommended_action=RecommendedAction(
            name=action_name,
            description=f"{action_name} — BLOCKED: required part unavailable in time",
            estimated_duration_hours=0,
        ),
        urgency=blocked_urgency,
        ranked_alternatives=[],
        required_parts=parts,
        window_chosen=None,
        rationale=rationale,
        recommendation_status="blocked_no_part",
        approval_status="escalated",
        responsible_person=approver,
        responsible_person_id=approver_id,
        responsible_approver=approver,
        responsible_approver_id=approver_id,
        contributors=contributors,
        evidence=evidence,
        generated_at_utc=datetime.now(tz=timezone.utc).isoformat(),
    )


# ── Parts builders ────────────────────────────────────────────────────────────

def _build_required_parts(winner: dict, diagnosis: FaultDiagnosis) -> list:
    action_type = _get_action_type(winner.get("action_name", ""), winner)
    if action_type is None:
        return []
    inv = check_part_for_action(diagnosis.bearing_id, action_type)
    if inv.get("status") in ("no_matching_part", "unknown_action_type"):
        return []
    part_number = inv.get("part_model") or inv.get("part_id")
    if not part_number:
        return []
    return [RequiredPart(
        part_number=part_number,
        quantity=1,
        lead_time_days=inv.get("lead_time_days") or 0,
    )]


def _build_required_parts_for_novel(action_name: str, diagnosis: FaultDiagnosis) -> list:
    try:
        action_type = _get_action_type(action_name)
        if not action_type:
            return []
        inv = check_part_for_action(diagnosis.bearing_id, action_type)
        if not inv or inv.get("status") in ("no_matching_part", "unknown_action_type"):
            return []
        part_number = inv.get("part_model") or inv.get("part_id")
        if not part_number:
            return []
        return [RequiredPart(
            part_number=part_number,
            quantity=1,
            lead_time_days=inv.get("lead_time_days") or 0,
        )]
    except Exception:
        return []


# ── Timing resolution ─────────────────────────────────────────────────────────

_WINDOWS_UNSET = object()


def _resolve_timing(
    winner: dict, diagnosis: FaultDiagnosis, risk: RiskAssessment
) -> tuple[str, Optional[str]]:
    action_name = winner.get("action_name", "")

    if action_name == "inspect_and_monitor":
        return "monitor", None

    windows = winner.get("windows", _WINDOWS_UNSET)
    if windows is _WINDOWS_UNSET:
        duration = winner.get("est_duration_hours") or DEFAULT_DURATION_HOURS
        windows  = find_windows(diagnosis.asset_id, duration, within_days=risk.rul_min_days)

    if windows:
        return "in_window", windows[0].get("window_id")
    return "now", None


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_candidate(
    candidate: dict, diagnosis: FaultDiagnosis, risk: RiskAssessment
) -> dict:
    """Fill urgency_score and feasibility_score on a candidate dict."""
    rul_min = risk.rul_min_days

    # Urgency base: 1 − (rul_remaining / stage_1_lifespan)
    taxonomy = load_fault_taxonomy()
    stage_1_rul = None
    for entry in taxonomy:
        if entry.get("fault_mode") == diagnosis.fault_mode:
            stage_1_rul = entry.get("rul_days_stage_1")
            break

    if stage_1_rul and stage_1_rul > 0:
        urgency_base = 1.0 - (rul_min / stage_1_rul)
    else:
        urgency_base = 1.0

    urgency_base = max(0.0, min(1.0, urgency_base))

    appropriateness = (
        1.0 if (candidate.get("is_escalation") or candidate.get("is_primary"))
        else MONITOR_APPROPRIATENESS
    )
    urgency_score = max(0.0, min(1.0, urgency_base * appropriateness))

    # Feasibility = parts_factor × window_factor
    action_name = candidate.get("action_name", "")
    action_type = _get_action_type(action_name, candidate)

    if action_type is None:
        parts_factor = 1.0
    else:
        inv    = check_part_for_action(diagnosis.bearing_id, action_type)
        status = inv.get("status")
        if status == "in_stock":
            parts_factor = 1.0
        elif status == "out_of_stock":
            lead_time = inv.get("lead_time_days") or 0
            if rul_min > 0 and lead_time <= rul_min:
                parts_factor = max(0.0, 1.0 - (lead_time / rul_min))
            else:
                parts_factor = 0.0
        else:
            parts_factor = 0.0

    if action_name == "inspect_and_monitor" or action_type is None:
        window_factor = 1.0
    else:
        duration = candidate.get("est_duration_hours") or DEFAULT_DURATION_HOURS
        windows  = find_windows(diagnosis.asset_id, duration, within_days=rul_min)
        candidate["windows"] = windows
        window_factor = 1.0 if windows else 0.0

    feasibility_score = max(0.0, min(1.0, parts_factor * window_factor))

    candidate["urgency_score"]     = round(urgency_score, 4)
    candidate["feasibility_score"] = round(feasibility_score, 4)
    return candidate


# ── Candidate generation ──────────────────────────────────────────────────────

def _generate_candidates(diagnosis: FaultDiagnosis) -> list[dict]:
    """
    Look up the action catalog for entries matching this diagnosis.
    Returns list of candidate action dicts.

    Catalog matching uses _diagnosis_stage() to translate DRO's iso_stage
    into the catalog's "stage_1/stage_2/stage_3/monitor" severity labels.
    """
    assets = load_assets()
    asset_type = None
    for asset in assets:
        if asset.get("asset_id") == diagnosis.asset_id:
            asset_type = asset.get("asset_type")
            break

    catalog       = load_action_catalog()
    diag_stage    = _diagnosis_stage(diagnosis)

    matched_entry = None
    for entry in catalog:
        if entry.get("fault_mode") is None and entry.get("severity") is None:
            continue

        fault_ok = (
            entry.get("fault_mode") is None
            or entry.get("fault_mode") == diagnosis.fault_mode
        )

        catalog_sev   = entry.get("severity")
        catalog_fault = entry.get("fault_mode")
        if catalog_sev is None:
            sev_ok = True
        elif catalog_fault is None:
            # General action: exact stage match only
            sev_ok = (catalog_sev == diag_stage)
        else:
            # Fault-specific: applies at stated stage and above
            sev_ok = (
                _STAGE_ORDER.get(catalog_sev, 0) <= _STAGE_ORDER.get(diag_stage, 0)
            )

        asset_ok = (
            entry.get("asset_type") == "all"
            or entry.get("asset_type") == asset_type
        )

        if fault_ok and sev_ok and asset_ok:
            matched_entry = entry
            break

    if matched_entry is None:
        return [{
            "action_name":        "inspect_and_monitor",
            "source_sop":         None,
            "est_duration_hours": None,
            "is_escalation":      False,
            "is_primary":         False,
            "feasibility_score":  None,
        }]

    candidates = [{
        "action_name":        matched_entry.get("action"),
        "source_sop":         matched_entry.get("source_sop"),
        "est_duration_hours": matched_entry.get("est_duration_hours"),
        "is_escalation":      False,
        "is_primary":         True,
        "feasibility_score":  None,
        "part_type":          matched_entry.get("part_type"),
    }]

    escalation = matched_entry.get("escalation")
    if escalation is not None:
        esc_action         = escalation.get("action")
        esc_sop            = escalation.get("escalation_sop")
        catalog_sev_rank   = _STAGE_ORDER.get(matched_entry.get("severity"), 0)
        diagnosis_sev_rank = _STAGE_ORDER.get(diag_stage, 0)

        if esc_action and diagnosis_sev_rank > catalog_sev_rank:
            candidates.append({
                "action_name":        esc_action,
                "source_sop":         esc_sop,
                "est_duration_hours": None,
                "is_escalation":      True,
                "is_primary":         False,
                "feasibility_score":  None,
                "part_type":          escalation.get("part_type"),
            })

    return candidates


# ── Normal-path recommendation assembly ──────────────────────────────────────

def _assemble_recommendation(
    ranked: list, diagnosis: FaultDiagnosis, risk: RiskAssessment,
    guidance: KnowledgeGuidance, timing: str, window_chosen: Optional[str],
) -> MaintenanceRecommendation:
    winner       = ranked[0]
    alternatives = ranked[1:]
    is_esc       = winner.get("is_escalation", False)

    sop_label = winner.get("source_sop") or "N/A"
    recommended_action = RecommendedAction(
        name=winner.get("action_name", "inspect_and_monitor"),
        description=f"{winner.get('action_name')} ({timing}) per {sop_label}",
        estimated_duration_hours=float(winner.get("est_duration_hours") or 0),
    )

    ranked_alternatives = [
        RecommendedAction(
            name=c.get("action_name", "inspect_and_monitor"),
            description=f"{c.get('action_name')} per {c.get('source_sop') or 'N/A'}",
            estimated_duration_hours=float(c.get("est_duration_hours") or 0),
        )
        for c in alternatives
    ]

    required_parts = _build_required_parts(winner, diagnosis)

    diag_stage = _diagnosis_stage(diagnosis)
    if timing == "monitor" or diag_stage == "monitor":
        urgency = "monitor"
    elif timing == "in_window":
        urgency = "planned"
    elif timing == "now" and not is_esc:
        urgency = "urgent"
    else:
        urgency = "emergency"

    action_type_for_rationale = _get_action_type(winner.get("action_name", ""), winner)
    inv_r = check_part_for_action(diagnosis.bearing_id, action_type_for_rationale) \
        if action_type_for_rationale else {}
    decision = {
        "recommendation_status": "ok",
        "fault_mode":        diagnosis.fault_mode,
        "severity":          diagnosis.severity,
        "asset_id":          diagnosis.asset_id,
        "bearing_id":        diagnosis.bearing_id,
        "rul_min_days":      risk.rul_min_days,
        "recommended_action": winner.get("action_name"),
        "timing":            timing,
        "source_sop":        sop_label,
        "part_status":       inv_r.get("status", ""),
        "part_number":       inv_r.get("part_model"),
        "lead_time_days":    inv_r.get("lead_time_days"),
        "window_chosen":     window_chosen,
        "alternatives":      [a.name for a in ranked_alternatives],
        "inspection_steps":  "; ".join(guidance.inspection_steps) if guidance.inspection_steps else "",
        "safety_notes":      "; ".join(guidance.safety_notes) if guidance.safety_notes else "",
    }
    rationale = write_rationale(decision)

    business_impact = bool(getattr(risk, "business_impact_flag", False))
    approver, approver_id = _resolve_approver(urgency, "ok", business_impact, risk.risk_level)
    contributors = _resolve_contributors("ok", winner.get("action_name", ""), diagnosis)

    part_evidence = (
        f"Inventory record — part {required_parts[0].part_number}"
        if required_parts
        else "No part required for this action"
    )
    window_evidence = (
        f"Maintenance schedule — window {window_chosen}"
        if window_chosen
        else "No window (immediate action or monitor-only)"
    )
    _normal_id = load_personas().get("approvers", {}).get("normal", {}).get("id", "PERSONA_SUP")
    person_source = (
        "Approval hierarchy (personas.json) — escalated to Plant Manager"
        if approver_id != _normal_id
        else "Approval hierarchy (personas.json) — routine sign-off by Plant Supervisor"
    )
    evidence: dict[str, str] = {
        "fault":              f"Diagnosed by Phase 4, case {diagnosis.case_id}",
        "risk":               f"Risk assessment by Phase 5 — RUL {risk.rul_min_days}–{risk.rul_max_days} days",
        "recommended_action": f"Action catalog, per {sop_label}",
        "part":               part_evidence,
        "window":             window_evidence,
        "responsible_person": person_source,
        "urgency":            (
            f"Urgency scored from RUL vs fault stage-1 lifespan ({risk.rul_min_days}d); "
            f"final label '{urgency}' resolved from timing ({timing})"
        ),
        "guidance": (
            f"Phase 6 (Knowledge) — {', '.join(guidance.source_documents)}"
            + (f"; sections: {', '.join(guidance.relevant_sections)}" if guidance.relevant_sections else "")
            if guidance.source_documents
            else "Phase 6 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }

    return MaintenanceRecommendation(
        case_id=diagnosis.case_id,
        asset_id=diagnosis.asset_id,
        bearing_id=diagnosis.bearing_id,
        fault_mode=getattr(diagnosis, "fault_mode", ""),
        asset_type=_get_asset_type(diagnosis.asset_id) or "",
        iso_stage=getattr(diagnosis, "iso_stage", 0) or 0,
        recommended_action=recommended_action,
        urgency=urgency,
        ranked_alternatives=ranked_alternatives,
        required_parts=required_parts,
        window_chosen=window_chosen,
        rationale=rationale,
        recommendation_status="ok",
        approval_status="pending",
        responsible_person=approver,
        responsible_person_id=approver_id,
        responsible_approver=approver,
        responsible_approver_id=approver_id,
        contributors=contributors,
        evidence=evidence,
        generated_at_utc=datetime.now(tz=timezone.utc).isoformat(),
    )


# ── Novel-fault LLM path ──────────────────────────────────────────────────────

_NOVEL_ALLOWED_ACTIONS = {
    "inspect_and_monitor", "schedule_inspection", "lubrication_service",
    "replace_bearing", "replace_sensor", "preventive_maintenance", "general_maintenance",
}

_NOVEL_FALLBACK = {
    "action_name": "inspect_and_monitor",
    "brief_reasoning": "AI suggestion unavailable — defaulting to inspection and monitoring. Manual review required.",
    "suggested_part_or_none": None,
    "est_duration_or_none": None,
}


def _propose_novel_action(
    diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance
) -> dict:
    """Ask the LLM to propose a tentative action for a novel fault. Falls back safely."""
    try:
        return _llm_propose_novel_action(diagnosis, risk, guidance)
    except Exception:
        return dict(_NOVEL_FALLBACK)


def _llm_propose_novel_action(
    diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance
) -> dict:
    import os
    import json as _json
    from langchain_openai import AzureChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    api_key     = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint    = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment  = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    if not all([api_key, endpoint, deployment, api_version]):
        raise ValueError("Azure OpenAI credentials missing from .env")

    llm = AzureChatOpenAI(
        openai_api_key=api_key, azure_endpoint=endpoint,
        azure_deployment=deployment, openai_api_version=api_version,
        temperature=0.2, max_tokens=400,
    )

    allowed = ", ".join(sorted(_NOVEL_ALLOWED_ACTIONS))
    system = (
        "You are a cautious industrial-maintenance assistant for the DRO system. "
        "A NOVEL fault has occurred for which NO approved procedure (SOP) exists. "
        "Propose a single best-effort, TENTATIVE maintenance action based ONLY on the facts. "
        f"You may ONLY choose action_name from this exact list: {allowed}. "
        "Match the action to severity and risk — for high/critical risk or very low RUL, "
        "choose a decisive action, NOT passive monitoring. "
        "Respond with ONLY a JSON object: action_name, brief_reasoning (1-2 sentences), "
        "suggested_part_or_none (string or null), est_duration_or_none (hours as number or null). "
        "No markdown, no code fences."
    )
    human = (
        "Novel fault facts:\n"
        "- fault_mode: {fault_mode}\n"
        "- fault_description: {fault_desc}\n"
        "- severity: {severity}\n"
        "- risk_level: {risk_level}\n"
        "- asset: {asset_id} / bearing: {bearing_id}\n"
        "- affected_component: {component}\n"
        "- remaining_useful_life_days: {rul}\n"
        "- diagnosis_confidence: {confidence}\n\n"
        "Return the JSON now."
    )

    prompt = ChatPromptTemplate.from_messages([("system", system), ("human", human)])
    chain  = prompt | llm | StrOutputParser()

    fault_desc = "; ".join(_evidence_list(diagnosis)) or "not provided"
    raw = chain.invoke({
        "fault_mode":  diagnosis.fault_mode,
        "fault_desc":  fault_desc,
        "severity":    diagnosis.severity,
        "risk_level":  risk.risk_level,
        "asset_id":    diagnosis.asset_id,
        "bearing_id":  diagnosis.bearing_id,
        "component":   getattr(diagnosis, "affected_component", "unknown"),
        "rul":         risk.rul_min_days,
        "confidence":  diagnosis.confidence,
    })

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    data = _json.loads(text)

    action_name = str(data.get("action_name", "inspect_and_monitor")).strip()
    if action_name not in _NOVEL_ALLOWED_ACTIONS:
        action_name = "inspect_and_monitor"

    reasoning = str(data.get("brief_reasoning", "") or "").strip() or \
        "Tentative suggestion for a novel fault with limited inputs."
    part = data.get("suggested_part_or_none")
    part = str(part).strip() if part not in (None, "", "null") else None
    dur  = data.get("est_duration_or_none")
    try:
        dur = float(dur) if dur not in (None, "", "null") else None
    except (TypeError, ValueError):
        dur = None

    return {
        "action_name": action_name,
        "brief_reasoning": reasoning,
        "suggested_part_or_none": part,
        "est_duration_or_none": dur,
    }


def _make_novel_suggestion_rec(
    diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance
) -> MaintenanceRecommendation:
    suggestion  = _propose_novel_action(diagnosis, risk, guidance)
    action_name = suggestion.get("action_name", "inspect_and_monitor")
    brief       = suggestion.get("brief_reasoning", "")
    dur         = suggestion.get("est_duration_or_none")

    urgency = _urgency_from_risk_level(risk)

    recommended_action = RecommendedAction(
        name=action_name,
        description=f"AI-suggested (tentative) action for a novel fault: {action_name.replace('_', ' ')}",
        estimated_duration_hours=float(dur) if isinstance(dur, (int, float)) else 0.0,
    )

    required_parts = _build_required_parts_for_novel(action_name, diagnosis)

    rationale = (
        f"[TENTATIVE AI SUGGESTION - NOVEL SCENARIO] HUMAN VALIDATION REQUIRED.\n\n"
        f"Suggested action: {action_name.replace('_', ' ')}.\n"
        f"AI reasoning: {brief}\n\n"
        f"This is a tentative AI-generated suggestion for a novel scenario "
        f"({diagnosis.fault_mode} at severity {diagnosis.severity}) NOT covered by approved "
        f"procedures. It MUST be validated by a human before any action is taken."
    )
    _steps  = "; ".join(guidance.inspection_steps) if guidance.inspection_steps else ""
    _safety = "; ".join(guidance.safety_notes) if guidance.safety_notes else ""
    if _steps:
        rationale += "\n\nINSPECTION STEPS (per SOP): " + _steps
    if _safety:
        rationale += "\n\nSAFETY NOTES: " + _safety

    approver, approver_id = _resolve_approver("emergency", "catalog_miss", True)
    contributors = _resolve_contributors("catalog_miss", action_name, diagnosis)

    evidence = {
        "fault":              f"NOVEL fault — diagnosed by Phase 4, case {diagnosis.case_id}; no catalog SOP exists",
        "risk":               f"Risk by Phase 5 — RUL {risk.rul_min_days} days, level {risk.risk_level}",
        "recommended_action": "AI-proposed (LLM), NOT from the approved action catalog — tentative",
        "part": (
            "Part shown is from inventory for this asset's bearing, selected by the AI's "
            "suggested action. Reviewer MUST verify this is the correct part."
            if required_parts else
            "No part identified for this action; reviewer to determine parts."
        ),
        "window":             "No window assigned — tentative suggestion pending human validation",
        "responsible_person": "Approval hierarchy — novel suggestion escalates to Plant Manager",
        "urgency":            f"Derived from risk level ({risk.risk_level})",
        "guidance": (
            f"Phase 6 (Knowledge) — {', '.join(guidance.source_documents)}"
            if guidance.source_documents
            else "Phase 6 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }

    return MaintenanceRecommendation(
        case_id=diagnosis.case_id,
        asset_id=diagnosis.asset_id,
        bearing_id=diagnosis.bearing_id,
        fault_mode=getattr(diagnosis, "fault_mode", ""),
        asset_type=_get_asset_type(diagnosis.asset_id) or "",
        iso_stage=getattr(diagnosis, "iso_stage", 0) or 0,
        recommended_action=recommended_action,
        urgency=urgency,
        ranked_alternatives=[],
        required_parts=required_parts,
        window_chosen=None,
        rationale=rationale,
        recommendation_status="novel_llm_suggestion",
        approval_status="escalated",
        is_llm_suggested=True,
        responsible_person=approver,
        responsible_person_id=approver_id,
        responsible_approver=approver,
        responsible_approver_id=approver_id,
        contributors=contributors,
        evidence=evidence,
        generated_at_utc=datetime.now(tz=timezone.utc).isoformat(),
    )


# ── Main entry point (free function) ─────────────────────────────────────────

def recommend_action(
    diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance
) -> MaintenanceRecommendation:
    """
    Core logic: four guards then normal scoring pipeline.

    Guards (in order):
      1. Input validation       — blocked_invalid_input
      2. Unknown asset          — blocked_unknown_asset
      3. Unreliable diagnosis   — unreliable_diagnosis
      4. Catalog miss           — novel_llm_suggestion (LLM proposes tentative action)
      5. Part unavailable       — blocked_no_part
    Normal path: score → rank → resolve timing → assemble recommendation.
    """
    from src.tools.poa_input_validator import validate_inputs
    _validation = validate_inputs(diagnosis, risk, guidance)
    if not _validation.valid:
        return _make_failure_rec(
            diagnosis=diagnosis,
            risk=risk,
            status="blocked_invalid_input",
            urgency="urgent",
            action_name="manual_review",
            action_desc=(
                "[INPUT VALIDATION FAILED] The upstream data failed pre-processing checks.\n\n"
                "Errors detected:\n" +
                "\n".join(f"  • {e}" for e in _validation.errors) +
                ("\n\nWarnings:\n" +
                 "\n".join(f"  • {w}" for w in _validation.warnings)
                 if _validation.warnings else "")
            ),
            guidance=guidance,
        )

    # Guard 1: asset must exist in master data
    if _get_asset_type(diagnosis.asset_id) is None:
        return _make_failure_rec(
            diagnosis=diagnosis, risk=risk,
            status="blocked_unknown_asset",
            urgency="urgent",
            action_name="manual_review",
            action_desc="No automated recommendation possible; asset not recognised",
            guidance=guidance,
        )

    # Guard 2: diagnosis confidence must be >= MIN_RELIABLE_CONFIDENCE
    if diagnosis.confidence < MIN_RELIABLE_CONFIDENCE:
        return _make_failure_rec(
            diagnosis=diagnosis, risk=risk,
            status="unreliable_diagnosis",
            urgency="urgent",
            action_name="manual_inspection",
            action_desc="Diagnosis not reliable enough to act on",
            guidance=guidance,
        )

    candidates = _generate_candidates(diagnosis)

    # Guard 3: no catalog SOP → LLM proposes tentative action
    if _is_catalog_miss(candidates):
        return _make_novel_suggestion_rec(diagnosis, risk, guidance)

    # Guard 4: required part unavailable before bearing fails
    base_action_name = next(
        (c["action_name"] for c in candidates if not c.get("is_escalation")), None
    )
    if base_action_name:
        block = _check_part_blocked(base_action_name, diagnosis, risk)
        if block:
            return _make_blocked_no_part_rec(base_action_name, block, diagnosis, risk, guidance)

    # Normal path
    candidates = [_score_candidate(c, diagnosis, risk) for c in candidates]
    candidates = rank_actions(candidates)
    candidates.sort(key=lambda c: (
        -c.get("final_score", 0),
        -c.get("urgency_score", 0) if _diagnosis_stage(diagnosis) == "stage_3"
            and c.get("is_escalation") else 0,
        _tiebreak_key(c),
    ))

    timing, window_chosen = _resolve_timing(candidates[0], diagnosis, risk)
    return _assemble_recommendation(candidates, diagnosis, risk, guidance, timing, window_chosen)


# ── Class wrapper (DRO pipeline interface) ────────────────────────────────────

class PrescriptiveOptimizationAgent:
    """
    Phase 7 — Prescriptive Optimization Agent.

    Wraps recommend_action() in the .process() interface used by the
    LangGraph orchestrator. Stateless: instantiate once and reuse.
    """

    def process(
        self,
        risk: RiskAssessment,
        diagnosis: FaultDiagnosis,
        guidance: KnowledgeGuidance,
        inventory_lookup: dict = None,
        context_lookup: dict = None,
    ) -> MaintenanceRecommendation:
        """
        Run the full recommendation pipeline.

        inventory_lookup and context_lookup are accepted for API compatibility
        but ignored — the agent loads these directly from data files.
        """
        return recommend_action(diagnosis, risk, guidance)
