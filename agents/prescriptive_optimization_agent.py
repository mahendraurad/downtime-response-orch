"""
Agent 6.6 — Prescriptive Optimization Agent.
Receives three validated inputs from upstream agents:
  - FaultDiagnosis   (Agent 6.3): what kind of fault was found
  - RiskAssessment   (Agent 6.4): how likely / how soon / how costly
  - KnowledgeGuidance (Agent 6.5): what the SOPs and past cases say
Produces a MaintenanceRecommendation for Agent 6.7 (Executor) to act on.
"""
from schemas.diagnosis import FaultDiagnosis
from schemas.risk import RiskAssessment
from schemas.knowledge import KnowledgeGuidance
from datetime import datetime, timezone
from schemas.recommendation import MaintenanceRecommendation, Action, RequiredPart
from tools.data_loader import load_action_catalog, load_assets, load_fault_taxonomy, load_personas
from tools.inventory_checker import check_part_for_action
from tools.schedule_reader import find_windows
from tools.action_ranker import rank_actions
from tools.rationale_writer import write_rationale
from app_config import get_threshold

# Severity order used to compare diagnosis severity against a catalog entry's severity.
# Higher number = more severe stage.
_SEVERITY_ORDER = {"monitor": 0, "stage_1": 1, "stage_2": 2, "stage_3": 3}

# --- Tunable thresholds (named for clarity / future config) ---
MIN_RELIABLE_CONFIDENCE       = get_threshold("min_reliable_confidence", 0.5)       # below this, diagnosis is too unreliable to act on
CONTRIBUTOR_REVIEW_CONFIDENCE = get_threshold("contributor_review_confidence", 0.6) # below this, a reliability engineer reviews
MONITOR_APPROPRIATENESS       = get_threshold("monitor_appropriateness", 0.3)       # score multiplier for the inspect-and-monitor fallback
DEFAULT_DURATION_HOURS        = get_threshold("default_duration_hours", 2.0)        # assumed action duration when none is specified

def _tiebreak_key(candidate: dict) -> tuple:
    """
    Secondary sort key when two candidates share the same final_score.
    Lower value = preferred (less disruptive, less costly).

    Priority:
      1. Non-escalation before escalation (lubrication before replacement)
      2. Shorter est_duration_hours (less machine downtime)
    """ 
    is_esc   = 1 if candidate.get("is_escalation") else 0
    duration = candidate.get("est_duration_hours") or 999
    return (is_esc, duration)


# Statuses that always escalate to the Maintenance Manager regardless of action
_ESCALATION_STATUSES = {
    "blocked_no_part", "blocked_unknown_asset",
    "unreliable_diagnosis", "catalog_miss",
    "blocked_invalid_input",
}


# ---------------------------------------------------------------------------
# Persona resolution: ONE approver (severity hierarchy) + N contributors
# ---------------------------------------------------------------------------

def resolve_approver(
    urgency: str,
    recommendation_status: str,
    business_impact: bool,
    risk_level: str = "",
) -> tuple[str, str]:
    """
    Pick the single APPROVER from the severity hierarchy in personas.json["approvers"].

    Tiers: "normal" (Plant Supervisor) -> "escalated" (Plant Manager) -> "executive" (VP Ops).

    Escalate to "escalated" (Plant Manager) when ANY of these is true:
      - recommendation_status is a failure/blocked status, OR
      - urgency is "emergency", OR
      - the asset carries high business impact (bottleneck / costly downtime), OR
      - urgency is "urgent" AND risk_level is "critical" (imminent failure, no window).
    Otherwise use "normal" (Plant Supervisor) for routine 'ok' cases.

    The "executive" tier exists in the data but is reserved for extreme
    portfolio-level impact; it is NOT auto-routed in this stage (kept conservative).

    Returns a ("Role — Name", persona_id) tuple.
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


def resolve_contributors(
    recommendation_status: str,
    action_name: str,
    diagnosis,
) -> list[dict]:
    """
    Build the list of CONTRIBUTORS who must weigh in, from personas.json["contributors"].
    A contributor is added ONLY when its trigger condition is met. A clean, low-risk
    case with none of the conditions returns an empty list [].

    Conditions (evaluated in review order: diagnosis -> sensor -> parts -> safety):
      - diagnosis (Reliability Engineer): status is "unreliable_diagnosis" (review
        the diagnostic evidence) OR "catalog_miss" (no SOP exists — decide the right
        action), OR diagnosis.confidence < 0.6.
      - sensor (OT / Controls): status is "unreliable_diagnosis" (bad signal) OR
        "blocked_unknown_asset" (verify the asset/data — it is not in master data),
        OR the fault / component / action relates to a sensor or signal.
      - parts (Maintenance Planner): status is "blocked_no_part" (confirm parts /
        expedite procurement), OR the winning action requires a part
        (name contains "replace"/"lubric").
      - safety (Safety Officer): the action is a major intervention
        (a replacement action that needs LOTO/permits).

    Failure cases are exactly when a specialist's input IS needed, so each failure
    status maps to at least one contributor above.

    Each returned item is a small dict: {"role", "name", "concern"}.
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
        if entry not in selected:   # avoid duplicates
            selected.append(entry)

    return selected


# ---------------------------------------------------------------------------
# Guard helpers — detect failure cases before committing to a recommendation
# ---------------------------------------------------------------------------

def _get_asset_type(asset_id: str) -> str | None:
    """Return the asset_type for asset_id, or None if the asset is not in master data."""
    for asset in load_assets():
        if asset.get("asset_id") == asset_id:
            return asset.get("asset_type")
    return None


def _is_catalog_miss(candidates: list[dict]) -> bool:
    """
    True when generate_candidates returned the safe fallback — meaning no approved
    catalog action exists for this fault/severity combination.
    The fallback is always a single candidate with action_name="inspect_and_monitor"
    and source_sop=None.
    """
    return (
        len(candidates) == 1
        and candidates[0].get("action_name") == "inspect_and_monitor"
        and candidates[0].get("source_sop") is None
    )


def _check_part_blocked(action_name: str, diagnosis: FaultDiagnosis, risk: RiskAssessment) -> dict | None:
    """
    Check whether the part required for action_name is unavailable in time.
    Returns a block-info dict if blocked, or None if parts are fine (or not needed).
    """
    action_type = _get_action_type(action_name)
    if action_type is None:
        return None  # action needs no tracked part

    inv = check_part_for_action(diagnosis.bearing_id, action_type)
    status = inv.get("status")

    if status == "no_matching_part":
        return {"part_model": "unknown", "lead_time_days": None, "reason": "no_matching_part"}

    if status == "out_of_stock":
        lead_time = inv.get("lead_time_days") or 0
        if lead_time > risk.rul_min_days:
            return {
                "part_model": inv.get("part_model") or "unknown",
                "lead_time_days": lead_time,
                "reason": "lead_exceeds_rul",
            }

    return None  # in stock, or lead time is within the RUL window


def _urgency_from_risk_level(risk: RiskAssessment) -> str:
    """
    Derive urgency from RiskAssessment.risk_level.
    Used when the normal timing-based urgency is misleading (e.g. blocked cases
    where 'monitor' won the scoring only because all actionable options had feasibility=0).
    """
    return {"critical": "emergency", "high": "urgent", "medium": "urgent", "low": "planned"}.get(
        risk.risk_level, "urgent"
    )


def _make_failure_rec(
    diagnosis: FaultDiagnosis, risk: RiskAssessment, status: str, urgency: str,
    action_name: str, action_desc: str,
    guidance: KnowledgeGuidance,
    required_parts: list | None = None,
) -> MaintenanceRecommendation:
    """
    Build a failure MaintenanceRecommendation.
    Always sets approval_required=True and leaves ranked_alternatives empty —
    there are no safe alternatives to suggest when the agent cannot reason confidently.
    """
    decision = {
        "recommendation_status": status,
        "fault_mode":  diagnosis.fault_mode,
        "severity":    diagnosis.severity,
        "asset_id":    diagnosis.asset_id,
        "bearing_id":  diagnosis.bearing_id,
        "rul_min_days": risk.rul_min_days,
        "confidence":  diagnosis.confidence,
    }
    # Failure cases always escalate the approver (Plant Manager) and attach the
    # contributors relevant to the failure (e.g. unreliable_diagnosis -> diagnosis + sensor).
    business_impact = bool(getattr(risk, "business_impact_flag", False))
    approver, approver_id = resolve_approver(urgency, status, business_impact)
    contributors = resolve_contributors(status, action_name, diagnosis)

    # Build evidence: start with the fields that are always known, then override
    # the specific field that caused the failure with an honest problem description.
    evidence: dict[str, str] = {
        "fault":              f"Diagnosed by Agent 6.3 (Fault Diagnosis), case {diagnosis.case_id}",
        "risk":               f"Risk assessment by Agent 6.4 — RUL {risk.rul_min_days} days",
        "recommended_action": "N/A — recommendation blocked before action selection",
        "part":               "N/A — recommendation blocked",
        "window":             "N/A — no window assigned for blocked recommendation",
        "responsible_person": "Approval hierarchy — failure/blocked status escalates to Plant Manager",
        "urgency":            f"Derived from risk level ({risk.risk_level}) — blocked cases use risk-level urgency",
        "guidance": (
            f"Agent 6.5 (Knowledge) — {', '.join(guidance.source_documents)}"
            + (f"; sections: {', '.join(guidance.relevant_sections)}" if guidance.relevant_sections else "")
            if guidance.source_documents
            else "Agent 6.5 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }
    if status == "blocked_unknown_asset":
        evidence["fault"] = f"Asset {diagnosis.asset_id} not found in asset master data"
    elif status == "unreliable_diagnosis":
        evidence["fault"] = (
            f"Diagnosis confidence {diagnosis.confidence:.0%} is below the {MIN_RELIABLE_CONFIDENCE:.0%} threshold — "
            f"case {diagnosis.case_id}"
        )
    elif status == "catalog_miss":
        evidence["recommended_action"] = (
            f"No approved SOP in action catalog for "
            f"{diagnosis.fault_mode} / {diagnosis.severity}"
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
        recommended_action=Action(
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
        generated_at_utc=datetime.now(tz=timezone.utc),
    )


def _make_blocked_no_part_rec(
    action_name: str, block: dict, diagnosis: FaultDiagnosis, risk: RiskAssessment,
    guidance: KnowledgeGuidance,
) -> MaintenanceRecommendation:
    """
    Build a blocked_no_part recommendation.
    Keeps the real intended action so the executor knows WHAT to do,
    and still populates required_parts so procurement can be initiated immediately.
    """
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
        parts = [RequiredPart(
            part_number=part_model,
            quantity=1,
            lead_time_days=lead_time or 0,
        )]

    blocked_urgency = _urgency_from_risk_level(risk)
    business_impact = bool(getattr(risk, "business_impact_flag", False))
    approver, approver_id = resolve_approver(blocked_urgency, "blocked_no_part", business_impact)
    contributors = resolve_contributors("blocked_no_part", action_name, diagnosis)

    if part_model != "unknown" and lead_time is not None:
        part_evidence = (
            f"Inventory record — part {part_model} out of stock; "
            f"lead time {lead_time}d exceeds RUL {risk.rul_min_days}d"
        )
    else:
        part_evidence = "Inventory record — no matching part found for this bearing / action"

    evidence: dict[str, str] = {
        "fault":              f"Diagnosed by Agent 6.3 (Fault Diagnosis), case {diagnosis.case_id}",
        "risk":               f"Risk assessment by Agent 6.4 — RUL {risk.rul_min_days} days",
        "recommended_action": f"Action catalog — {action_name} is the correct action but blocked on parts",
        "part":               part_evidence,
        "window":             "No window — action blocked on part availability",
        "responsible_person": "Approval hierarchy — blocked_no_part escalates to Plant Manager",
        "urgency":            f"Derived from risk level ({risk.risk_level}) — blocked cases use risk-level urgency",
        "guidance": (
            f"Agent 6.5 (Knowledge) — {', '.join(guidance.source_documents)}"
            + (f"; sections: {', '.join(guidance.relevant_sections)}" if guidance.relevant_sections else "")
            if guidance.source_documents
            else "Agent 6.5 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }

    return MaintenanceRecommendation(
        case_id=diagnosis.case_id,
        asset_id=diagnosis.asset_id,
        bearing_id=diagnosis.bearing_id,
        recommended_action=Action(
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
        generated_at_utc=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------

def _build_required_parts(winner: dict, diagnosis: FaultDiagnosis) -> list:
    """
    Return a list of RequiredPart objects for the winning candidate.
    Returns an empty list if the action needs no tracked part.
    """
    action_type = _get_action_type(winner.get("action_name", ""), winner)
    if action_type is None:
        return []

    inv = check_part_for_action(diagnosis.bearing_id, action_type)
    if inv.get("status") in ("no_matching_part", "unknown_action_type"):
        return []

    # part_model is the human-readable model number (e.g. "SKF6208-2RS")
    part_number = inv.get("part_model") or inv.get("part_id")
    if not part_number:
        return []

    return [
        RequiredPart(
            part_number=part_number,
            quantity=1,
            lead_time_days=inv.get("lead_time_days") or 0,
        )
    ]


def _build_required_parts_for_novel(action_name: str, diagnosis) -> list:
    """
    Deterministic inventory lookup for a novel / AI recommendation.
    Uses the same logic as _build_required_parts but never raises —
    returns [] if the action needs no part or no compatible part exists.
    The AI's suggested part name (if any) is intentionally ignored; only
    the real inventory (keyed on the asset's bearing_id + action type) is used.

    NOTE: _get_action_type maps by substring — "replace_sensor" and similar
    names containing "replace" will trigger a bearing lookup. If the AI returns
    such an action for a non-bearing job, the looked-up part will be wrong;
    the verify banner in the UI makes this explicit to the reviewer.
    """
    try:
        action_type = _get_action_type(action_name)
        if not action_type:
            return []   # e.g. inspect_and_monitor → genuinely no part needed
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


# Sentinel meaning "the candidate never had a windows list computed during scoring"
# (distinct from a computed-but-empty list, which legitimately means "no window fits").
_WINDOWS_UNSET = object()


def _resolve_timing(winner: dict, diagnosis: FaultDiagnosis, risk: RiskAssessment) -> tuple[str, str | None]:
    """
    Compute timing and window_chosen for the winning action.
    Timing is an ATTRIBUTE of how we execute the chosen action — not a candidate variant.

    Rules:
    - inspect_and_monitor needs no scheduled slot → "monitor", window=None.
    - All real maintenance actions: look for a fitting window within the RUL horizon.
        * Window found  → "in_window", window = soonest window_id.
        * No window fits → "now" (must act immediately), window=None.

    Efficiency: scoring already ran find_windows() for real maintenance actions and
    stored the result on the candidate, so we REUSE winner["windows"] here instead of
    recomputing. We only call find_windows() as a fallback if no list was stored.
    """
    action_name = winner.get("action_name", "")

    # Pure monitoring action — no physical work, no window needed
    if action_name == "inspect_and_monitor":
        return "monitor", None

    # Reuse the window list scoring already found; recompute only if it wasn't stored
    # (e.g. an action whose feasibility didn't require a window check).
    windows = winner.get("windows", _WINDOWS_UNSET)
    if windows is _WINDOWS_UNSET:
        duration = winner.get("est_duration_hours") or DEFAULT_DURATION_HOURS
        windows  = find_windows(diagnosis.asset_id, duration, within_days=risk.rul_min_days)

    if windows:
        return "in_window", windows[0].get("window_id")
    else:
        return "now", None   # no window available within the RUL — must act immediately


def assemble_recommendation(ranked: list, diagnosis: FaultDiagnosis, risk: RiskAssessment,
                             guidance: KnowledgeGuidance,
                             timing: str, window_chosen: str | None) -> MaintenanceRecommendation:
    """
    Take the ranked candidate list and build a validated MaintenanceRecommendation.
    The first candidate is the winner; the rest become ranked_alternatives.
    """
    winner       = ranked[0]
    alternatives = ranked[1:]
    is_esc       = winner.get("is_escalation", False)

    # --- recommended_action: timing is now passed in, not read from the candidate ---
    sop_label = winner.get("source_sop") or "N/A"
    recommended_action = Action(
        name=winner.get("action_name", "inspect_and_monitor"),
        description=f"{winner.get('action_name')} ({timing}) per {sop_label}",
        estimated_duration_hours=float(winner.get("est_duration_hours") or 0),
    )

    # --- ranked_alternatives: genuinely different actions (no timing variants) ---
    ranked_alternatives = [
        Action(
            name=c.get("action_name", "inspect_and_monitor"),
            description=f"{c.get('action_name')} per {c.get('source_sop') or 'N/A'}",
            estimated_duration_hours=float(c.get("est_duration_hours") or 0),
        )
        for c in alternatives
    ]

    # --- required_parts ---
    required_parts = _build_required_parts(winner, diagnosis)

    # --- urgency: derived from resolved timing + escalation + severity safety cap ---
    # diagnosis.severity == "monitor" caps urgency at "monitor" so a healthy/preventive
    # case never gets labelled "urgent" even if no window was found.
    if timing == "monitor" or diagnosis.severity == "monitor":
        urgency = "monitor"
    elif timing == "in_window":
        urgency = "planned"
    elif timing == "now" and not is_esc:
        urgency = "urgent"
    else:                        # timing "now" AND is_escalation True
        urgency = "emergency"

    # --- rationale ---
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

    # --- approver (severity hierarchy) + contributors (by condition) ---
    business_impact = bool(getattr(risk, "business_impact_flag", False))
    approver, approver_id = resolve_approver(urgency, "ok", business_impact, risk.risk_level)
    contributors = resolve_contributors("ok", winner.get("action_name", ""), diagnosis)

    # --- evidence: one entry per key claim, sourced from the real upstream data ---
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
        "fault":              f"Diagnosed by Agent 6.3 (Fault Diagnosis), case {diagnosis.case_id}",
        "risk":               f"Risk assessment by Agent 6.4 — RUL {risk.rul_min_days}–{risk.rul_max_days} days",
        "recommended_action": f"Action catalog, per {sop_label}",
        "part":               part_evidence,
        "window":             window_evidence,
        "responsible_person": person_source,
        "urgency":            (
            f"Urgency score computed from remaining useful life vs. fault stage-1 "
            f"lifespan (RUL {risk.rul_min_days}d) for ranking; final urgency label "
            f"'{urgency}' resolved from maintenance timing ({timing}) - in_window to "
            f"planned, immediate to urgent (or emergency if escalated), monitor stays "
            f"monitor"
        ),
        "guidance": (
            f"Agent 6.5 (Knowledge) — {', '.join(guidance.source_documents)}"
            + (f"; sections: {', '.join(guidance.relevant_sections)}" if guidance.relevant_sections else "")
            if guidance.source_documents
            else "Agent 6.5 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }

    return MaintenanceRecommendation(
        case_id=diagnosis.case_id,
        asset_id=diagnosis.asset_id,
        bearing_id=diagnosis.bearing_id,
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
        generated_at_utc=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# NOVEL fault path — the LLM proposes a TENTATIVE action when no SOP exists.
# This is a deliberate, guarded exception to "rules decide": the LLM only
# proposes for novel/catalog-miss cases, always flagged lower-confidence and
# requiring human validation. Known faults are untouched (rules + LLM explains).
# ---------------------------------------------------------------------------

# Guardrail: the LLM may ONLY pick from these sensible maintenance categories.
# Anything it returns outside this set is coerced to inspect_and_monitor.
_NOVEL_ALLOWED_ACTIONS = {
    "inspect_and_monitor",
    "schedule_inspection",
    "lubrication_service",
    "replace_bearing",
    "replace_sensor",
    "preventive_maintenance",
    "general_maintenance",
}

# Safe default used whenever the LLM is unavailable or its output can't be trusted.
_NOVEL_FALLBACK = {
    "action_name": "inspect_and_monitor",
    "brief_reasoning": "AI suggestion unavailable — defaulting to inspection and monitoring. Manual review required.",
    "suggested_part_or_none": None,
    "est_duration_or_none": None,
}


def propose_novel_action(diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance) -> dict:
    """
    Ask the LLM to propose ONE tentative maintenance action for a NOVEL fault that
    has no approved procedure. Returns a dict:
        {action_name, brief_reasoning, suggested_part_or_none, est_duration_or_none}

    Wrapped in try/except: ANY failure (missing credentials, network, bad JSON)
    returns the safe fallback so the system never crashes or hangs.
    """
    try:
        return _llm_propose_novel_action(diagnosis, risk, guidance)
    except Exception:
        return dict(_NOVEL_FALLBACK)


def _llm_propose_novel_action(diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance) -> dict:
    """
    The actual LLM call (Azure OpenAI via LangChain), reusing the same .env-based
    credential pattern as rationale_writer. Raises on any problem so the public
    propose_novel_action() can catch it and fall back. Imports are local so a
    missing langchain install can't crash the module.
    """
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
        "You are a cautious industrial-maintenance assistant for Agent 6.6. "
        "A NOVEL fault has occurred for which NO approved procedure (SOP) exists. "
        "Propose a single best-effort, TENTATIVE maintenance action based ONLY on the facts. "
        f"You may ONLY choose action_name from this exact list: {allowed}. "
        "Choose the action_name that matches the SEVERITY and RISK, not the "
        "safest option by default:\n"
        "- monitor or stage_1 with low risk: 'inspect_and_monitor' or 'schedule_inspection'.\n"
        "- stage_2 or medium risk: 'schedule_inspection', or the most relevant "
        "service action (e.g. 'lubrication_service' for a lubrication fault).\n"
        "- stage_3, OR high/critical risk, OR very low remaining useful life: "
        "the asset is near failure — do NOT default to passive monitoring. "
        "Choose a decisive action such as 'schedule_inspection' (urgent) or "
        "'replace_bearing'. Passive 'inspect_and_monitor' is INAPPROPRIATE for a "
        "near-failure asset. Prefer urgent inspection over replacement unless the "
        "facts clearly indicate the component must be replaced.\n"
        "Keep it tentative — a novel fault with no approved SOP requires human "
        "validation — but the action MUST reflect the urgency. "
        "You must NOT invent unusual or unsafe physical procedures. "
        "You must NOT claim certainty — this is a tentative suggestion for a novel scenario. "
        "Keep brief_reasoning to 1-2 plain sentences (the inputs are limited). "
        "Respond with ONLY a JSON object with these keys: "
        "action_name (a string from the list), brief_reasoning (string), "
        "suggested_part_or_none (string or null), est_duration_or_none (number of hours or null). "
        "No markdown, no code fences, no extra text."
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
    chain = prompt | llm | StrOutputParser()

    fault_desc = "; ".join(getattr(diagnosis, "evidence", []) or []) or "not provided"
    raw = chain.invoke({
        "fault_mode":  getattr(diagnosis, "fault_mode", "unknown"),
        "fault_desc":  fault_desc,
        "severity":    getattr(diagnosis, "severity", "unknown"),
        "risk_level":  getattr(risk, "risk_level", "unknown"),
        "asset_id":    getattr(diagnosis, "asset_id", "unknown"),
        "bearing_id":  getattr(diagnosis, "bearing_id", "unknown"),
        "component":   getattr(diagnosis, "affected_component", "unknown"),
        "rul":         getattr(risk, "rul_min_days", "unknown"),
        "confidence":  getattr(diagnosis, "confidence", "unknown"),
    })

    # --- Parse + VALIDATE the LLM output (strip code fences just in case) ---
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    data = _json.loads(text)   # raises if not valid JSON -> caught -> fallback

    # Guardrail: coerce any off-list / missing action to the safe default.
    action_name = str(data.get("action_name", "inspect_and_monitor")).strip()
    if action_name not in _NOVEL_ALLOWED_ACTIONS:
        action_name = "inspect_and_monitor"

    reasoning = str(data.get("brief_reasoning", "") or "").strip() or \
        "Tentative suggestion for a novel fault with limited inputs."

    part = data.get("suggested_part_or_none")
    part = str(part).strip() if part not in (None, "", "null") else None

    dur = data.get("est_duration_or_none")
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


def _make_novel_suggestion_rec(diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance) -> MaintenanceRecommendation:
    """
    Build a TENTATIVE, LLM-proposed recommendation for a novel fault with no SOP.
    Clearly distinct from a rule-backed 'ok': status='novel_llm_suggestion',
    is_llm_suggested=True, escalated to a senior approver, and a rationale that
    demands human validation. NEVER presented with the authority of a normal rec.
    """
    suggestion  = propose_novel_action(diagnosis, risk, guidance)
    action_name = suggestion.get("action_name", "inspect_and_monitor")
    brief       = suggestion.get("brief_reasoning", "")
    dur         = suggestion.get("est_duration_or_none")

    urgency = _urgency_from_risk_level(risk)   # severity/RUL-derived (urgent for med/high)

    recommended_action = Action(
        name=action_name,
        description=(
            f"AI-suggested (tentative) action for a novel fault: "
            f"{action_name.replace('_', ' ')}"
        ),
        estimated_duration_hours=float(dur) if isinstance(dur, (int, float)) else 0.0,
    )

    # Novel rec: look up the REAL inventory part for this asset's bearing,
    # using the AI's suggested action to decide the part type. This is the
    # same deterministic lookup the normal path uses — NOT the AI's guess.
    required_parts = _build_required_parts_for_novel(action_name, diagnosis)

    # Crisp rationale: the LLM's brief reasoning + an explicit tentative/validate warning.
    rationale = (
        f"[TENTATIVE AI SUGGESTION - NOVEL SCENARIO] HUMAN VALIDATION REQUIRED.\n\n"
        f"Suggested action: {action_name.replace('_', ' ')}.\n"
        f"AI reasoning: {brief}\n\n"
        f"This is a tentative AI-generated suggestion for a novel scenario "
        f"({diagnosis.fault_mode} at severity {diagnosis.severity}) NOT covered by approved "
        f"procedures. It is lower-confidence and MUST be validated by a human before any "
        f"action is taken."
    )
    _steps  = "; ".join(guidance.inspection_steps) if guidance.inspection_steps else ""
    _safety = "; ".join(guidance.safety_notes) if guidance.safety_notes else ""
    if _steps:
        rationale += "\n\nINSPECTION STEPS (per SOP): " + _steps
    if _safety:
        rationale += "\n\nSAFETY NOTES: " + _safety

    # Novel = senior validation: force the escalated approver (Plant Manager).
    approver, approver_id = resolve_approver("emergency", "catalog_miss", True)
    # Reliability Engineer is pulled in by the catalog_miss condition to validate the
    # novel diagnosis/action; other contributors are added if the action warrants them.
    contributors = resolve_contributors("catalog_miss", action_name, diagnosis)

    evidence = {
        "fault":              f"NOVEL fault — diagnosed by Agent 6.3, case {diagnosis.case_id}; no catalog SOP exists",
        "risk":               f"Risk assessment by Agent 6.4 — RUL {risk.rul_min_days} days, level {risk.risk_level}",
        "recommended_action": "AI-proposed (LLM), NOT from the approved action catalog — tentative",
        "part":               (
            "Part shown is from inventory for this asset's bearing, selected by the "
            "AI's suggested action. This fault is NOT in the approved SOPs — the "
            "reviewer MUST verify this is the correct part before ordering."
            if required_parts else
            "No part identified for this action; reviewer to determine parts."
        ),
        "window":             "No window assigned — tentative suggestion pending human validation",
        "responsible_person": "Approval hierarchy — novel suggestion escalates to Plant Manager for validation",
        "urgency":            f"Derived from risk level ({risk.risk_level})",
        "guidance": (
            f"Agent 6.5 (Knowledge) — {', '.join(guidance.source_documents)}"
            + (f"; sections: {', '.join(guidance.relevant_sections)}" if guidance.relevant_sections else "")
            if guidance.source_documents
            else "Agent 6.5 (Knowledge) — no SOP guidance retrieved for this case"
        ),
    }

    return MaintenanceRecommendation(
        case_id=diagnosis.case_id,
        asset_id=diagnosis.asset_id,
        bearing_id=diagnosis.bearing_id,
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
        generated_at_utc=datetime.now(tz=timezone.utc),
    )


def _llm_free_recommendation(diagnosis, risk, guidance, likely_fault: str) -> dict:
    """LLM proposes a free-form action for a confirmed-unknown fault. No allow-list."""
    import os
    from langchain_openai import AzureChatOpenAI
    llm = AzureChatOpenAI(
        openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        temperature=0.2, max_tokens=400,
    )
    fault_desc = "; ".join(diagnosis.evidence) if getattr(diagnosis, "evidence", None) else "not provided"
    system = (
        "You are a cautious industrial-maintenance assistant. A fault has been identified "
        "that is OUTSIDE the system's standard catalog and has no approved SOP. "
        "A human has CONFIRMED the likely fault. Propose ONE best-effort maintenance action. "
        "You may propose any reasonable maintenance action (you are NOT limited to a fixed list), "
        "but you must NOT invent unsafe or implausible physical procedures, and you must NOT "
        "claim certainty. This is a tentative recommendation requiring human validation. "
        "Match the action to the severity and risk: for stage_3, or high/critical "
        "risk, or very low remaining useful life, the asset is near failure — "
        "propose a decisive intervention (prefer urgent inspection; component "
        "replacement only if the facts clearly indicate it), NOT passive "
        "monitoring. For lower severity, a conservative action is appropriate. "
        "Keep it tentative and requiring human validation regardless. "
        "Respond with ONLY a JSON object: action_name (short snake_case or plain string), "
        "action_description (one sentence), brief_reasoning (1-2 sentences), "
        "suggested_part_or_none (string or null), est_duration_or_none (hours as number or null). "
        "No markdown, no code fences."
    )
    human = (
        f"Confirmed likely fault: {likely_fault}\n"
        f"Original description: {fault_desc}\n"
        f"Asset: {diagnosis.asset_id} / bearing: {diagnosis.bearing_id}\n"
        f"Severity: {diagnosis.severity}\n"
        f"RUL days: {risk.rul_min_days}\n"
        f"Risk level: {risk.risk_level}\n\n"
        "Return the JSON now."
    )
    import json, re as _re
    resp = llm.invoke([{"role": "system", "content": system},
                       {"role": "user", "content": human}])
    raw = resp.content if hasattr(resp, "content") else str(resp)
    raw = _re.sub(r"^```[a-zA-Z]*|```$", "", raw.strip()).strip()
    data = json.loads(raw)
    return {
        "action_name": str(data.get("action_name", "inspect_and_monitor")).strip(),
        "action_description": str(data.get("action_description", "")).strip(),
        "brief_reasoning": str(data.get("brief_reasoning", "")).strip(),
        "suggested_part_or_none": data.get("suggested_part_or_none"),
        "est_duration_or_none": data.get("est_duration_or_none"),
    }


def make_free_recommendation(diagnosis, risk, guidance, likely_fault: str):
    """Build a fully-labeled, escalated, LLM-generated recommendation for a
    confirmed-unknown fault. Falls back safely to inspect_and_monitor."""
    try:
        s = _llm_free_recommendation(diagnosis, risk, guidance, likely_fault)
    except Exception:
        s = {"action_name": "inspect_and_monitor",
             "action_description": "Inspect and monitor; manual review required.",
             "brief_reasoning": "AI recommendation unavailable — defaulting to inspection.",
             "suggested_part_or_none": None, "est_duration_or_none": None}

    action_name = s.get("action_name") or "inspect_and_monitor"
    dur = s.get("est_duration_or_none")
    urgency = _urgency_from_risk_level(risk)

    recommended_action = Action(
        name=action_name,
        description=(s.get("action_description")
                     or f"AI-suggested action for: {likely_fault}"),
        estimated_duration_hours=float(dur) if isinstance(dur, (int, float)) else 0.0,
    )
    # Novel rec: look up the REAL inventory part for this asset's bearing,
    # using the AI's suggested action to decide the part type. This is the
    # same deterministic lookup the normal path uses — NOT the AI's guess.
    required_parts = _build_required_parts_for_novel(action_name, diagnosis)

    rationale = (
        f"[TENTATIVE AI SUGGESTION - UNRECOGNIZED FAULT] HUMAN VALIDATION REQUIRED.\n\n"
        f"Identified (AI, human-confirmed) likely fault: {likely_fault}.\n"
        f"Suggested action: {action_name}.\n"
        f"AI reasoning: {s.get('brief_reasoning','')}\n\n"
        f"This fault is OUTSIDE the approved catalog and the six standard fault modes. "
        f"Both the fault identification and the action below are AI-generated and "
        f"lower-confidence. They MUST be validated by a human before any action is taken."
    )
    approver, approver_id = resolve_approver("emergency", "catalog_miss", True)
    contributors = resolve_contributors("catalog_miss", action_name, diagnosis)
    evidence = {
        "fault": f"UNRECOGNIZED fault — AI-identified as '{likely_fault}', human-confirmed; outside standard taxonomy",
        "risk": f"Risk by Agent 6.4 — RUL {risk.rul_min_days} days, level {risk.risk_level}",
        "recommended_action": "AI-proposed (LLM, free-form), NOT from the approved catalog — tentative",
        "part": (
            "Part shown is from inventory for this asset's bearing, selected by the "
            "AI's suggested action. This fault is NOT in the approved SOPs — the "
            "reviewer MUST verify this is the correct part before ordering."
            if required_parts else
            "No part identified for this action; reviewer to determine parts."
        ),
        "window": "No window assigned — tentative suggestion pending human validation",
        "responsible_person": "Escalated to Plant Manager for validation (unrecognized fault)",
        "urgency": f"Derived from risk level ({risk.risk_level})",
    }
    return MaintenanceRecommendation(
        case_id=diagnosis.case_id, asset_id=diagnosis.asset_id, bearing_id=diagnosis.bearing_id,
        recommended_action=recommended_action, urgency=urgency,
        ranked_alternatives=[], required_parts=required_parts, window_chosen=None,
        rationale=rationale, recommendation_status="novel_llm_suggestion",
        approval_status="escalated", is_llm_suggested=True,
        responsible_person=approver, responsible_person_id=approver_id,
        responsible_approver=approver, responsible_approver_id=approver_id,
        contributors=contributors, evidence=evidence,
        generated_at_utc=datetime.now(tz=timezone.utc),
    )


def recommend_action(diagnosis: FaultDiagnosis, risk: RiskAssessment, guidance: KnowledgeGuidance) -> MaintenanceRecommendation:
    """
    Main entry point for Agent 6.6.
    Runs four guards in order before the normal pipeline:
      1. Unknown asset     — cannot reason without master data
      2. Unreliable diag   — confidence < 0.5 means the signal is untrustworthy
      3. Catalog miss      — no approved procedure: LLM proposes a tentative action
      4. Blocked on part   — correct action identified but required part unavailable
    Each guard returns a loud, specific failure recommendation instead of a silent "monitor".
    """
    from agents.input_validator import validate_inputs
    _validation = validate_inputs(diagnosis, risk, guidance)
    if not _validation.valid:
        return _make_failure_rec(
            diagnosis=diagnosis,
            risk=risk,
            status="blocked_invalid_input",
            urgency="urgent",
            action_name="manual_review",
            action_desc=(
                "[INPUT VALIDATION FAILED] The upstream data failed "
                "pre-processing checks. The pipeline did not run.\n\n"
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

    # Guard 2: diagnosis confidence must be >= 0.5 (threshold chosen as the minimum
    # reliability for acting on a diagnosis; below this, signal quality is too degraded)
    if diagnosis.confidence < MIN_RELIABLE_CONFIDENCE:
        return _make_failure_rec(
            diagnosis=diagnosis, risk=risk,
            status="unreliable_diagnosis",
            urgency="urgent",
            action_name="manual_inspection",
            action_desc="Diagnosis not reliable enough to act on",
            guidance=guidance,
        )

    # Generate candidates (catalog lookup is inside generate_candidates)
    candidates = generate_candidates(diagnosis)

    # Guard 3: no catalog entry means no approved SOP. Instead of only reporting
    # "no procedure exists", let the LLM PROPOSE a tentative action for this novel
    # fault — clearly flagged, escalated, and requiring human validation.
    if _is_catalog_miss(candidates):
        return _make_novel_suggestion_rec(diagnosis, risk, guidance)

    # Guard 4: check whether the base action's required part is available in time.
    # Run BEFORE scoring so we don't waste computation when the outcome is already blocked.
    # Base action = first non-escalation candidate (always the catalog's primary recommendation).
    base_action_name = next(
        (c["action_name"] for c in candidates if not c.get("is_escalation")),
        None,
    )
    if base_action_name:
        block = _check_part_blocked(base_action_name, diagnosis, risk)
        if block:
            return _make_blocked_no_part_rec(base_action_name, block, diagnosis, risk, guidance)

    # Normal path: score → rank → resolve timing → assemble
    candidates = [score_candidate(c, diagnosis, risk) for c in candidates]
    candidates = rank_actions(candidates)
    candidates.sort(key=lambda c: (
        -c.get("final_score", 0),
        -c.get("urgency_score", 0) if diagnosis.severity == "stage_3"
            and c.get("is_escalation") else 0,
        _tiebreak_key(c)
    ))

    # Timing and window are properties of HOW we execute the winner, not separate candidates
    timing, window_chosen = _resolve_timing(candidates[0], diagnosis, risk)

    return assemble_recommendation(candidates, diagnosis, risk, guidance, timing, window_chosen)


# ---------------------------------------------------------------------------

def _get_action_type(action_name, candidate: dict | None = None) -> str | None:
    """
    Return the part_type for an action. Prefers the data-driven part_type
    carried on the candidate dict (from the catalog); falls back to keyword
    inference for novel/LLM actions that have no catalog entry.
    """
    # Data-driven path: candidate carries part_type from the catalog
    if candidate is not None:
        pt = candidate.get("part_type")
        if pt is not None:
            return pt
        # part_type explicitly absent on a known candidate → fall through to keyword
    # Keyword fallback (novel actions, or callers passing only a name)
    name = (action_name or "").lower()
    if "sensor" in name:
        return None
    if "replace" in name or "bearing" in name:
        return "replace_bearing"
    if "lubric" in name:
        return "lubricate"
    return None


def score_candidate(candidate: dict, diagnosis: FaultDiagnosis, risk: RiskAssessment) -> dict:
    """
    Fill in urgency_score and feasibility_score on a candidate dict and return it.
    Candidates are now distinct ACTIONS (no timing field).

    urgency_score   = urgency_base * appropriateness_multiplier
    feasibility     = parts_factor * window_factor
    final_score     = urgency_score * feasibility  (computed by rank_actions)
    """
    rul_min = risk.rul_min_days

    # -----------------------------------------------------------------------
    # URGENCY / APPROPRIATENESS
    # urgency_base: how far through the fault's detectable lifespan we are.
    #   1 - (rul_remaining / full_detectable_lifespan)
    #   Close to 0  = fault just detected, plenty of life left.
    #   Close to 1  = bearing about to fail.
    # appropriateness: does this action fit the situation?
    #   Primary action at its normal severity  -> 1.0 (this is the right thing to do)
    #   Escalation action (e.g. replace_bearing when stage warrants it) -> 1.0
    #   Catalog-miss fallback (inspect_and_monitor) -> 0.3 (low: no real procedure)
    # -----------------------------------------------------------------------

    taxonomy = load_fault_taxonomy()
    stage_1_rul = None
    for entry in taxonomy:
        if entry.get("fault_mode") == diagnosis.fault_mode:
            stage_1_rul = entry.get("rul_days_stage_1")
            break

    if stage_1_rul and stage_1_rul > 0:
        urgency_base = 1.0 - (rul_min / stage_1_rul)
    else:
        urgency_base = 1.0   # unknown fault — assume high urgency

    urgency_base = max(0.0, min(1.0, urgency_base))

    # Appropriateness multiplier — no timing involved
    if candidate.get("is_escalation") or candidate.get("is_primary"):
        appropriateness = 1.0   # SOP-backed action that fits the situation
    else:
        appropriateness = MONITOR_APPROPRIATENESS   # inspect_and_monitor fallback: valid but low urgency

    urgency_score = max(0.0, min(1.0, urgency_base * appropriateness))

    # -----------------------------------------------------------------------
    # FEASIBILITY = parts_factor * window_factor
    # -----------------------------------------------------------------------

    action_name = candidate.get("action_name", "")
    action_type = _get_action_type(action_name, candidate)

    # --- parts_factor ---
    if action_type is None:
        # Action needs no tracked part (inspect_and_monitor, preventive_maintenance, etc.)
        parts_factor = 1.0
    else:
        inv    = check_part_for_action(diagnosis.bearing_id, action_type)
        status = inv.get("status")

        if status == "in_stock":
            parts_factor = 1.0

        elif status == "out_of_stock":
            lead_time = inv.get("lead_time_days") or 0
            if rul_min > 0 and lead_time <= rul_min:
                # Part arrives before the bearing fails — partial credit
                parts_factor = max(0.0, 1.0 - (lead_time / rul_min))
            else:
                # Part arrives too late
                parts_factor = 0.0

        else:
            # "no_matching_part" or "unknown_action_type"
            parts_factor = 0.0

    # --- window_factor ---
    # inspect_and_monitor needs no scheduled slot — it's always feasible.
    # All real maintenance actions need a window that fits within the RUL horizon.
    if action_name == "inspect_and_monitor" or action_type is None:
        window_factor = 1.0
    else:
        duration = candidate.get("est_duration_hours") or DEFAULT_DURATION_HOURS
        windows  = find_windows(diagnosis.asset_id, duration, within_days=rul_min)
        # Store the actual list so _resolve_timing can REUSE it instead of calling
        # find_windows() a second time with the same args. (window_factor below is
        # unchanged — still just "is there any fitting window?".)
        candidate["windows"] = windows
        window_factor = 1.0 if windows else 0.0

    feasibility_score = max(0.0, min(1.0, parts_factor * window_factor))

    # -----------------------------------------------------------------------
    # Write scores back onto the candidate dict and return it
    # -----------------------------------------------------------------------
    candidate["urgency_score"]     = round(urgency_score,     4)
    candidate["feasibility_score"] = round(feasibility_score, 4)
    return candidate


# ---------------------------------------------------------------------------

def generate_candidates(diagnosis: FaultDiagnosis) -> list[dict]:
    """
    Look up the action catalog for entries matching the diagnosis, and return a
    list of candidate action dicts. Scores are left as None — filled in later.
    """

    # --- Step 1: look up the asset_type for this diagnosis ---
    # The catalog entries are asset-type-specific (motor / pump / gearbox / conveyor).
    # The diagnosis carries asset_id but not asset_type, so we join against the asset list.
    assets = load_assets()
    asset_type = None
    for asset in assets:
        if asset.get("asset_id") == diagnosis.asset_id:
            asset_type = asset.get("asset_type")
            break

    # --- Step 2: load the action catalog ---
    catalog = load_action_catalog()

    # --- Step 3: find the first catalog entry that matches fault_mode + severity + asset_type ---
    # Matching rules:
    #   fault_mode: catalog value must equal diagnosis value, OR catalog value is None (wildcard)
    #   severity:   same logic
    #   asset_type: catalog value must equal the looked-up asset_type, OR catalog value is "all"
    matched_entry = None
    for entry in catalog:
        # Skip entries where BOTH fault_mode and severity are null — those are
        # not triggered by a fault diagnosis (e.g. post_repair_qa, sensor repair).
        # They would otherwise match every diagnosis as a catch-all.
        if entry.get("fault_mode") is None and entry.get("severity") is None:
            continue

        # fault_mode: exact match OR catalog has no filter (None = wildcard)
        fault_ok = (entry.get("fault_mode") is None) or (entry.get("fault_mode") == diagnosis.fault_mode)

        # severity matching depends on whether the catalog entry is fault-specific or general:
        #
        # Fault-specific entry (fault_mode is NOT null, e.g. CAT_003 lubrication_issue):
        #   The SOP applies at its stated stage AND any higher stage — a stage_2 SOP is
        #   even more relevant at stage_3. Use <= comparison.
        #
        # General/wildcard entry (fault_mode IS null, e.g. CAT_002 preventive_maintenance):
        #   These target a specific machine state (e.g. "monitor" = healthy). They should NOT
        #   be stretched to cover more severe stages — preventive maintenance is wrong for a
        #   confirmed stage_3 fault. Require exact severity match.
        #
        # Null catalog severity = wildcard that matches any stage (either case).
        catalog_sev   = entry.get("severity")
        catalog_fault = entry.get("fault_mode")
        if catalog_sev is None:
            sev_ok = True
        elif catalog_fault is None:
            # General action: exact severity match only
            sev_ok = (catalog_sev == diagnosis.severity)
        else:
            # Fault-specific action: applies at stated stage and above
            sev_ok = _SEVERITY_ORDER.get(catalog_sev, 0) <= _SEVERITY_ORDER.get(diagnosis.severity, 0)

        asset_ok  = (entry.get("asset_type") == "all") or (entry.get("asset_type") == asset_type)

        if fault_ok and sev_ok and asset_ok:
            matched_entry = entry
            break

    # --- Step 4: no catalog match → single safe fallback (catalog miss) ---
    if matched_entry is None:
        return [
            {
                "action_name":        "inspect_and_monitor",
                "source_sop":         None,
                "est_duration_hours": None,
                "is_escalation":      False,
                "is_primary":         False,
                "feasibility_score":  None,
            }
        ]

    # --- Step 5: one primary action candidate (no timing — assigned later) ---
    # The primary action is the catalog entry's recommended action for this fault/severity.
    candidates = [
        {
            "action_name":        matched_entry.get("action"),
            "source_sop":         matched_entry.get("source_sop"),
            "est_duration_hours": matched_entry.get("est_duration_hours"),
            "is_escalation":      False,
            "is_primary":         True,
            "feasibility_score":  None,
            "part_type":          matched_entry.get("part_type"),
        }
    ]

    # --- Step 6: escalation candidate — only when severity is STRICTLY above entry's stage ---
    # Same severity-gating rule as before: for a catalog entry that applies at stage_2,
    # the escalation action is only included if the diagnosis is stage_3 or higher.
    # This prevents replace_bearing appearing for a normal stage_2 lubrication case.
    escalation = matched_entry.get("escalation")
    if escalation is not None:
        esc_action         = escalation.get("action")
        esc_sop            = escalation.get("escalation_sop")
        catalog_sev_rank   = _SEVERITY_ORDER.get(matched_entry.get("severity"), 0)
        diagnosis_sev_rank = _SEVERITY_ORDER.get(diagnosis.severity, 0)

        if esc_action and diagnosis_sev_rank > catalog_sev_rank:
            candidates.append(
                {
                    "action_name":        esc_action,
                    "source_sop":         esc_sop,
                    "est_duration_hours": None,
                    "is_escalation":      True,
                    "is_primary":         False,
                    "feasibility_score":  None,
                    "part_type":          escalation.get("part_type"),
                }
            )

    return candidates
