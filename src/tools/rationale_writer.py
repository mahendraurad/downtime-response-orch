"""
tools/rationale_writer.py  —  Phase 7

Generates a structured plain-English rationale for a MaintenanceRecommendation.

LLM path  : Azure OpenAI via LangChain (AzureChatOpenAI).
            Credentials loaded from .env — NEVER hardcoded here.
Fallback   : _template_rationale(decision) — always used when use_llm=False
            or when the LLM call fails for any reason.

The decision dict passed to write_rationale should contain:
  recommendation_status, fault_mode, severity, asset_id, bearing_id,
  rul_min_days, recommended_action, timing, source_sop,
  part_status, part_number, lead_time_days, window_chosen,
  alternatives (list of str), confidence (float), inspection_steps, safety_notes.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; credentials can be set via OS environment


def write_rationale(decision: dict, use_llm: bool = True) -> str:
    """
    Generate a rationale string for the given decision dict.

    use_llm=True  — tries Azure LLM first; falls back to template on ANY failure.
    use_llm=False — skips the LLM entirely; safe for testing without API calls.
    """
    if use_llm:
        try:
            return _llm_rationale(decision)
        except Exception:
            pass

    return _template_rationale(decision)


def _llm_rationale(decision: dict) -> str:
    """
    Generate a rationale using AzureChatOpenAI.
    Raises on any problem so write_rationale can catch it and use the template.
    """
    from langchain_openai import AzureChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    api_key     = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint    = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment  = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")

    if not all([api_key, endpoint, deployment, api_version]):
        raise ValueError(
            "One or more Azure OpenAI credentials are missing from .env "
            "(AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, "
            "AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION)."
        )

    llm = AzureChatOpenAI(
        openai_api_key=api_key,
        azure_endpoint=endpoint,
        azure_deployment=deployment,
        openai_api_version=api_version,
        temperature=0.2,
        max_tokens=600,
    )

    system = (
        "You are the rationale writer for an industrial predictive-maintenance system "
        "(Agent 6.6). Write a clear, structured maintenance rationale based ONLY on the "
        "facts provided below. Do NOT invent, change, or contradict any fact "
        "(fault type, severity, action name, part name, numbers, status).\n\n"
        "For status 'ok' write THREE clearly labelled sections:\n"
        "THE PROBLEM: Describe the fault_mode, severity, bearing and asset, and how "
        "urgent the situation is given the remaining useful life.\n"
        "THE RECOMMENDATION: State the recommended action, its timing, the SOP source, "
        "part availability (in-stock or lead time), and the maintenance window if chosen.\n"
        "THE REASONING: Explain why this action was chosen over the alternatives — "
        "reference feasibility, available resources, and the key operational trade-off.\n\n"
        "For FAILURE statuses produce a DIRECTIVE rationale: open with a CAPITALIZED demand "
        "for human action, name the specific problem, say exactly what the human must do. "
        "Begin each failure rationale with [ESCALATED - <REASON>].\n\n"
        "Use plain English suitable for a plant maintenance engineer. "
        "Be concise but direct. Add no information not present in the facts."
    )

    human = (
        "Decision facts:\n"
        "- status: {recommendation_status}\n"
        "- fault_mode: {fault_mode}\n"
        "- severity: {severity}\n"
        "- asset: {asset_id} / bearing: {bearing_id}\n"
        "- remaining_useful_life: {rul_min_days} days\n"
        "- recommended_action: {recommended_action}\n"
        "- timing: {timing}\n"
        "- source_sop: {source_sop}\n"
        "- part_status: {part_status}  part_number: {part_number}  "
        "lead_time_days: {lead_time_days}\n"
        "- window_chosen: {window_chosen}\n"
        "- alternatives_considered: {alternatives}\n"
        "- diagnosis_confidence: {confidence}\n"
        "- inspection_steps: {inspection_steps}\n"
        "- safety_notes: {safety_notes}\n\n"
        "Write the rationale now."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", human),
    ])

    chain = prompt | llm | StrOutputParser()

    facts = {
        "recommendation_status": decision.get("recommendation_status", "ok"),
        "fault_mode":            decision.get("fault_mode", "unknown"),
        "severity":              decision.get("severity", "unknown"),
        "asset_id":              decision.get("asset_id", "unknown"),
        "bearing_id":            decision.get("bearing_id", "unknown"),
        "rul_min_days":          decision.get("rul_min_days", "unknown"),
        "recommended_action":    decision.get("recommended_action", "none"),
        "timing":                decision.get("timing", "none"),
        "source_sop":            decision.get("source_sop") or "N/A",
        "part_status":           decision.get("part_status") or "N/A",
        "part_number":           decision.get("part_number") or "none",
        "lead_time_days":        decision.get("lead_time_days") or "N/A",
        "window_chosen":         decision.get("window_chosen") or "none",
        "alternatives":          ", ".join(decision.get("alternatives", [])) or "none",
        "confidence":            str(decision.get("confidence") or "N/A"),
        "inspection_steps":      decision.get("inspection_steps", "") or "none",
        "safety_notes":          decision.get("safety_notes", "") or "none",
    }

    return chain.invoke(facts)


def _template_rationale(decision: dict) -> str:
    """Route to the correct template based on recommendation_status."""
    status = decision.get("recommendation_status", "ok")
    if status == "blocked_unknown_asset":
        return _rationale_unknown_asset(decision)
    if status == "unreliable_diagnosis":
        return _rationale_unreliable(decision)
    if status == "catalog_miss":
        return _rationale_catalog_miss(decision)
    if status == "blocked_no_part":
        return _rationale_blocked_part(decision)
    if status == "blocked_invalid_input":
        return decision.get("rationale", "")
    return _rationale_ok(decision)


def _rationale_unknown_asset(d: dict) -> str:
    asset = d.get("asset_id", "unknown")
    return (
        f"[ESCALATED - UNKNOWN ASSET] "
        f"HUMAN REVIEW REQUIRED. "
        f"Asset '{asset}' was not found in master data — no reliable recommendation "
        f"is possible without a verified asset record. "
        f"A human must confirm the correct asset ID and update master data before "
        f"any maintenance action is taken."
    )


def _rationale_unreliable(d: dict) -> str:
    confidence = d.get("confidence")
    conf_str   = f"{confidence:.2f}" if isinstance(confidence, float) else "unknown"
    asset      = d.get("asset_id", "unknown")
    bearing    = d.get("bearing_id", "unknown")
    return (
        f"[ESCALATED - UNRELIABLE DIAGNOSIS] "
        f"HUMAN INSPECTION REQUIRED. "
        f"Diagnosis confidence is {conf_str} (threshold: 0.50) for bearing {bearing} "
        f"on asset {asset} — the sensor signal is too degraded or the diagnosis is too "
        f"uncertain to support any automated action. "
        f"A technician must physically inspect the bearing before trusting any "
        f"automated recommendation."
    )


def _rationale_catalog_miss(d: dict) -> str:
    fault    = d.get("fault_mode", "unknown fault")
    severity = d.get("severity", "unknown severity")
    return (
        f"[ESCALATED - NO APPROVED PROCEDURE] "
        f"HUMAN DECISION REQUIRED. "
        f"No approved Standard Operating Procedure exists for '{fault}' at severity "
        f"'{severity}'. This fault/severity combination has not been codified in the "
        f"action catalog. "
        f"A maintenance engineer must review the fault evidence and determine the "
        f"appropriate corrective action before any work order is raised."
    )


def _rationale_blocked_part(d: dict) -> str:
    action   = d.get("recommended_action", "required action")
    part     = d.get("part_number", "required part")
    lead     = d.get("lead_time_days")
    rul      = d.get("rul_min_days")
    fault    = d.get("fault_mode", "fault")
    severity = d.get("severity", "")
    bearing  = d.get("bearing_id", "")

    if lead is not None:
        block = (
            f"Part '{part}' is out of stock with a lead time of {lead} days, "
            f"which exceeds the remaining useful life of ~{rul} days."
        )
        action_required = (
            f"A planner must urgently expedite procurement of '{part}' or "
            f"take the asset offline before the {rul}-day window expires."
        )
    else:
        block = f"Part '{part}' is not found in the inventory catalog."
        action_required = (
            f"A planner must source '{part}' manually before this repair can proceed."
        )

    return (
        f"[ESCALATED - PART UNAVAILABLE] "
        f"HUMAN INTERVENTION REQUIRED. "
        f"Action '{action}' is needed for {fault.replace('_', ' ')} at severity "
        f"'{severity}' on bearing {bearing}. "
        f"{block} "
        f"The asset is at risk until the repair is completed. "
        f"{action_required} "
        f"Apply interim mitigation (load reduction, increased monitoring frequency) "
        f"in the meantime."
    )


def _rationale_ok(d: dict) -> str:
    fault        = d.get("fault_mode", "unknown fault")
    severity     = d.get("severity", "")
    bearing      = d.get("bearing_id", "")
    asset        = d.get("asset_id", "")
    rul          = d.get("rul_min_days", "?")
    action       = d.get("recommended_action", "")
    timing       = d.get("timing", "")
    sop          = d.get("source_sop") or "N/A"
    part_status  = d.get("part_status", "")
    part_number  = d.get("part_number")
    lead_time    = d.get("lead_time_days")
    window       = d.get("window_chosen")
    alternatives = d.get("alternatives", [])

    urgency_phrase = _urgency_phrase(severity, rul)
    problem = (
        f"{fault.replace('_', ' ').title()} detected at severity '{severity}' "
        f"on bearing {bearing} (asset {asset}). "
        f"Remaining useful life estimate: ~{rul} days. {urgency_phrase}"
    )

    rec_lines = [f"Action: {action} ({timing}) per {sop}."]
    if part_number:
        if part_status == "in_stock":
            rec_lines.append(
                f"Required part {part_number} is confirmed in stock — "
                f"the repair can proceed without delay."
            )
        elif part_status == "out_of_stock" and lead_time is not None:
            rec_lines.append(
                f"Required part {part_number} is out of stock "
                f"(lead time: {lead_time} days), but arrives within the "
                f"remaining useful life window."
            )
        else:
            rec_lines.append(f"Required part: {part_number}.")
    else:
        rec_lines.append("No tracked spare part required for this action.")

    if timing == "in_window" and window:
        rec_lines.append(f"Scheduled into maintenance window: {window}.")
    elif timing == "now":
        rec_lines.append("Timing: immediate — no planned window required.")
    elif timing == "monitor":
        rec_lines.append(
            "Timing: continue monitoring — no physical intervention needed at this stage."
        )
    recommendation = " ".join(rec_lines)

    n = len(alternatives) + 1
    reasoning_lines = [
        f"This action was selected as the highest-scoring feasible option "
        f"from {n} candidate{'s' if n != 1 else ''} evaluated."
    ]
    if timing == "in_window":
        reasoning_lines.append(
            "Scheduling in a planned window avoids an unplanned production stop "
            "and allows the line to run until the window opens."
        )
    elif timing == "now":
        reasoning_lines.append(
            "Immediate action was scored highest because the remaining useful life "
            "is short and the required resources are available now."
        )
    elif timing == "monitor":
        reasoning_lines.append(
            "Monitoring was scored highest because the remaining useful life is "
            "sufficient to defer physical intervention without significant risk."
        )
    if alternatives:
        alt_str = ", ".join(f"'{a}'" for a in alternatives[:3])
        if len(alternatives) > 3:
            alt_str += f" and {len(alternatives) - 3} more"
        reasoning_lines.append(f"Alternatives considered: {alt_str}.")
    reasoning = " ".join(reasoning_lines)

    _steps  = d.get("inspection_steps", "")
    _safety = d.get("safety_notes", "")
    rationale = (
        f"THE PROBLEM: {problem}\n\n"
        f"THE RECOMMENDATION: {recommendation}\n\n"
        f"THE REASONING: {reasoning}"
    )
    if _steps:
        rationale += "\n\nINSPECTION STEPS (per SOP): " + _steps
    if _safety:
        rationale += "\n\nSAFETY NOTES: " + _safety
    return rationale


def _urgency_phrase(severity: str, rul) -> str:
    if severity in ("stage_3", "critical", "high"):
        return "Stage 3 / high severity indicates imminent failure — intervention is critical."
    if severity in ("stage_2", "medium"):
        return "Stage 2 / medium severity indicates active degradation — action required soon."
    if severity in ("stage_1", "low"):
        return "Stage 1 / low severity indicates early-stage degradation — plan maintenance."
    return "Bearing is in the monitoring zone — no active fault confirmed."
