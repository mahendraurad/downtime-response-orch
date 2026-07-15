"""
recommendation_chat.py
General grounded assistant for Agent 6.6.

Works in two modes:
  - Recommendation present (rec is not None): answers questions about an
    already-produced MaintenanceRecommendation, using the recommendation
    context plus live-data tools.
  - No recommendation (rec is None): answers general questions about assets,
    inventory/parts, and maintenance windows using the same live-data tools.

Public API
----------
    answer_about_recommendation(question, rec=None, diagnosis=None,
        risk=None, history=None) -> dict
        Returns {"answer": str, "trace": list[dict]}
"""
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition

from tools.langgraph_tools import (
    check_inventory_tool,
    find_windows_tool,
    get_asset_info_tool,
    get_decision_history_tool,
    render_recommendation,
    search_documents_tool,
)

import observability

# ---------------------------------------------------------------------------
# LLM — same credential pattern as rationale_writer.py
# ---------------------------------------------------------------------------
llm = AzureChatOpenAI(
    openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    temperature=0.2,
    max_tokens=600,
)

_followup_llm = AzureChatOpenAI(
    openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    temperature=0.7,
    max_tokens=120,
)

tools = [check_inventory_tool, find_windows_tool, get_asset_info_tool,
         search_documents_tool, get_decision_history_tool]
llm_with_tools = llm.bind_tools(tools)

MAX_HISTORY = 10


# ---------------------------------------------------------------------------
# LangGraph graph — built once at module level, reused on every call
# ---------------------------------------------------------------------------

def _reasoner(state: MessagesState) -> dict:
    return {"messages": [llm_with_tools.invoke(state["messages"])]}


_tool_node = ToolNode(tools)

_builder = StateGraph(MessagesState)
_builder.add_node("reasoner", _reasoner)
_builder.add_node("tools", _tool_node)
_builder.add_edge(START, "reasoner")
_builder.add_conditional_edges("reasoner", tools_condition)
_builder.add_edge("tools", "reasoner")
chat_graph = _builder.compile()


# ---------------------------------------------------------------------------
# Shared follow-up instruction appended to every system prompt
# ---------------------------------------------------------------------------

_FOLLOW_UP_RULE = (
    "\n\nMANDATORY OUTPUT FORMAT — applies to EVERY response without exception, "
    "including one-line answers, refusals, and redirects to other agents. "
    "Before you finish, you MUST append this exact block on its own lines:\n"
    "<follow_ups>\n"
    "Q1: [follow-up question, ≤12 words]\n"
    "Q2: [follow-up question, ≤12 words]\n"
    "Q3: [follow-up question, ≤12 words]\n"
    "</follow_ups>\n"
    "Rules for the questions:\n"
    "- Base them on THIS exchange — what the user just asked and what you just "
    "answered — not generic questions.\n"
    "- If you just redirected to another agent, suggest questions THIS agent can "
    "actually answer (parts, windows, SOPs, the current recommendation).\n"
    "- Never repeat a question already asked or suggested earlier this session.\n"
    "- Vary them each turn so they move the conversation forward.\n"
    "Omitting this block is an error. It is required even if your answer is a "
    "single sentence."
)


# ---------------------------------------------------------------------------
# Partial-question routing rule — appended to every system prompt before
# the MANDATORY OUTPUT FORMAT block
# ---------------------------------------------------------------------------

_PARTIAL_QUESTIONS_RULE = """
YOUR PLACE IN THE SYSTEM:
You are the Prescriptive Optimization Agent in an 8-agent reliability pipeline
(the Downtime Response Orchestrator). You own: prescriptive recommendations —
actions, urgency, parts, windows, approvers, SOPs/cases, live inventory, and
decision history. Other agents own other things:
  - the Data Foundation Agent — raw telemetry ingestion, validation, data quality
  - the Monitoring Agent — live sensor/vibration readings, anomaly detection
  - the Failure Intelligence Agent — fault diagnosis and classification
  - the Predictive Risk Agent — risk scoring, failure probability, remaining useful life (RUL)
  - the Knowledge Agent — SOP authoring, manuals, knowledge-base updates
  - the Executor Agent — work orders, technician dispatch, carrying out the action
  - the Learning & Memory Agent — capturing outcomes and feedback after a job
  - the DRO Orchestrator — coordination, procurement / PO creation, anything cross-agent

PARTIAL QUESTIONS:
Answer every part you can, fully. For any part owned by another agent or the
orchestrator, you MUST name the responsible agent BY ITS NAME (e.g. "the Failure
Intelligence Agent", "the Executor Agent"). NEVER use agent numbers like "6.3"
or "Agent 6.7" — always the descriptive name. NEVER deflect with "I cannot
assist", "I don't have access", "check your monitoring system", or "follow your
organization's process" without immediately naming the agent that CAN handle it.
Bad:  "I cannot raise a PO directly."
Good: "Raising a PO is handled by the DRO Orchestrator (procurement)."
Bad:  "That's Agent 6.3's job."
Good: "That's handled by the Failure Intelligence Agent (fault diagnosis)."
Only route a part you truly cannot answer; never flag your own caveats or
anything the user did not ask for. If you answered everything in scope, end
normally with no routing line.
"""


# ---------------------------------------------------------------------------
# System-prompt builder
# ---------------------------------------------------------------------------

def _build_system(rec, diagnosis, risk, defaulted_fields=None) -> str:
    if rec is not None:
        case_ctx = (
            f"asset_id                        : {diagnosis.asset_id}\n"
            f"bearing_id                      : {diagnosis.bearing_id}\n"
            f"fault_mode                      : {diagnosis.fault_mode}\n"
            f"severity                        : {diagnosis.severity}\n"
            f"diagnosis_confidence            : {diagnosis.confidence}\n"
            f"rul_min_days                    : {risk.rul_min_days}\n"
            f"rul_max_days                    : {risk.rul_max_days}\n"
            f"risk_level                      : {risk.risk_level}\n"
            f"estimated_downtime_cost_per_hour: {risk.estimated_downtime_cost_per_hour}"
        )
        if defaulted_fields:
            case_ctx += "\n--- Partial data notice ---\n"
            case_ctx += "The following fields were missing from upstream and filled with safe defaults:\n"
            for item in defaulted_fields:
                case_ctx += f"  • {item}\n"
            case_ctx += ("The user may ask about missing or defaulted data — "
                         "you can answer this fully from the list above.\n")
        else:
            case_ctx += "\nAll upstream fields were present — no defaults were applied.\n"

        rules = (
            "RULES — follow these exactly:\n"
            "0. MULTI-PART QUESTIONS — CRITICAL: Before answering, identify\n"
            "   EVERY distinct question in the user's message (look for 'and',\n"
            "   'also', multiple '?', or separate asks). You MUST answer ALL of\n"
            "   them, each fully, in order. If different parts need different\n"
            "   tools (e.g. one asks about a window → call find_windows_tool,\n"
            "   another asks about a procedure → call search_documents_tool),\n"
            "   call ALL needed tools and address every part. NEVER answer only\n"
            "   one part and skip the rest. If you find yourself answering just\n"
            "   the most detailed part, STOP and go back to cover the parts you\n"
            "   skipped.\n"
            "1. Answer ONLY using the recommendation above and the tools provided.\n"
            "2. For 'why' questions (why this window, why this approver, why this urgency,\n"
            "   why this action) — use the recommendation's evidence and rationale fields;\n"
            "   explain the deterministic reasoning, do not guess.\n"
            "3. For live-data questions (part stock, maintenance windows, asset criticality\n"
            "   or cost) — you MUST CALL the appropriate tool (check_inventory_tool,\n"
            "   find_windows_tool, or get_asset_info_tool). Never answer these from the\n"
            "   recommendation's static fields or memory alone.\n"
            "4. For questions about procedures, repair steps, safety/LOTO, torque specs, or\n"
            "   past cases — CALL search_documents_tool with a descriptive query and answer\n"
            "   from the returned excerpts. Cite the source document name (and page) when\n"
            "   answering. If the excerpts don't contain the answer, say so rather than guessing.\n"
            "5. ALTERNATIVES: When asked about alternatives or options, check the\n"
            "   recommendation's situation:\n"
            "   - If window_chosen is None AND urgency is 'urgent' or 'emergency': the\n"
            "     alternatives are (1) ACT NOW outside the planned window, or (2) WAIT for\n"
            "     the nearest available window. Call find_windows_tool to get the nearest\n"
            "     window date, and present both options with their tradeoffs. Do NOT say\n"
            "     'no alternatives' — these scheduling options ARE the alternatives.\n"
            "   - If recommendation_status is 'blocked_no_part': the alternative is the\n"
            "     interim holding strategy — call search_documents_tool for the interim\n"
            "     protocol and present it.\n"
            "   - Only say there are no alternatives if NONE of these conditions apply\n"
            "     AND ranked_alternatives is empty.\n"
            "6. NEVER invent or change the recommendation. NEVER make up part numbers, costs,\n"
            "   windows, specs, or data not present in the recommendation or returned by a tool.\n"
            "7. For 'what if / hypothetical' questions that would require re-running the\n"
            "   recommendation with changed inputs, say you can explain the current\n"
            "   recommendation but do not run hypothetical scenarios in this view.\n"
            "8. If the question is unrelated to this recommendation or to maintenance,\n"
            "   politely say it is out of scope.\n"
            "9. Keep answers CONCISE by default — 2 to 4 sentences per question, "
            "direct and to the point. EXCEPTIONS to brevity:\n"
            "   - If the user asks multiple questions in one message, answer EVERY "
            "question — do not skip any. Address each one in order.\n"
            "   - If the user explicitly says 'tell me everything', 'full details', "
            "'elaborate', 'be comprehensive', or 'explain in detail', give a "
            "thorough answer without length restriction.\n"
            "   - If the user asks for step-by-step instructions or a procedure, "
            "list ALL steps — do not truncate.\n"
            "   Do NOT dump long procedures unprompted — only when explicitly asked.\n"
            "10. RECOMMENDATION SUMMARY: If the user asks to 'tell me everything about this "
            "recommendation', 'give me all details', 'summarize the recommendation', or "
            "'what is the full recommendation' — respond with ALL of these fields from the "
            "recommendation in order:\n"
            "   1. Recommended action and why\n"
            "   2. Urgency level\n"
            "   3. Required parts and availability\n"
            "   4. Scheduled window (if any)\n"
            "   5. Responsible approver\n"
            "   6. Key evidence (fault, risk, RUL)\n"
            "   7. Rationale summary\n"
            "   Do not skip any of these. Be concise per field but cover all fields.\n"
            "11. BLOCKED PART: If recommendation_status is 'blocked_no_part', proactively\n"
            "    call search_documents_tool with query 'part shortage interim protocol\n"
            "    monitoring derate' to retrieve the holding strategy from the SOPs. Present\n"
            "    the retrieved steps as the interim recommendation, citing the source document\n"
            "    and page. Also state the part name, lead time, and RUL gap from the\n"
            "    recommendation. Flag: if vibration or kurtosis exceed safe thresholds before\n"
            "    the part arrives, initiate controlled shutdown — retrieve the exact thresholds\n"
            "    from the SOP via search_documents_tool.\n"
            "12. NO WINDOW / ACT NOW: If window_chosen is None AND urgency is 'urgent' or\n"
            "    'emergency' AND recommendation_status is 'ok', this means no maintenance\n"
            "    window fits within the RUL. Proactively explain this when asked about\n"
            "    timing or alternatives:\n"
            "    - Immediately call find_windows_tool with the asset_id and required_hours\n"
            "      from the recommendation — do NOT ask the user first, just retrieve and\n"
            "      show the nearest upcoming window with its date, even if it is outside\n"
            "      the RUL. Then call search_documents_tool for SOP guidance.\n"
            "    - State the gap clearly: nearest window date vs RUL deadline\n"
            "    - Call search_documents_tool with query 'urgent bearing replacement\n"
            "      immediate action no window available' to retrieve any SOP guidance on\n"
            "      acting outside planned windows\n"
            "    - Recommend the human decide between: act now (outside planned window,\n"
            "      higher disruption) vs wait for window (higher asset risk)\n"
            "    - Never make the decision for them — present both options with the\n"
            "      tradeoff clearly stated\n"
            "13. DECISION HISTORY: For ANY question about past decisions, previous\n"
            "    recommendations, history, what was recommended before, decisions made\n"
            "    today / recently / in the last N minutes / this week, or 'show me all\n"
            "    recommendations' — you MUST call get_decision_history_tool. Pass\n"
            "    asset_id and/or fault_mode as filters ONLY if the user named a specific\n"
            "    asset or fault; otherwise call it with no filters to get all recent\n"
            "    decisions. Then give a BRIEF summary (3-5 sentences max): how many\n"
            "    past decisions there are, the common patterns (e.g. mostly lubrication\n"
            "    services or bearing replacements), and the 2-3 MOST RECENT ones with\n"
            "    their dates. Do NOT list every entry verbatim — enumerating all of them\n"
            "    will get cut off. Tell the user the complete history is available to\n"
            "    download via the 'Decision History Log' button shown below your answer.\n"
            "    NEVER say you cannot access past recommendations — the decision log IS\n"
            "    available to you via this tool. Time-based filtering (today, last 10\n"
            "    minutes) is done by reading the timestamps in the returned entries.\n"
            "14. INVENTORY CHECK: For ANY question about parts, stock, part availability,\n"
            "    lead time, quantity on hand, or whether the required part is in stock —\n"
            "    you MUST call check_inventory_tool before answering. Pass bearing_id and\n"
            "    action_type from the recommendation context (bearing_id is in the case\n"
            "    context; action_type is the recommended_action name). Do NOT answer\n"
            "    part/stock questions from the recommendation's static fields alone —\n"
            "    always verify with the live tool so the source is accurate.\n"
            "You can look up live inventory, asset, window data, and SOP/case documents via tools."
        )

        system = (
            "You are Agent 6.6, a prescriptive maintenance assistant. "
            "A recommendation has ALREADY been produced for this case. "
            "Your job is to answer the user's questions about it.\n\n"
            "Here is the full recommendation:\n"
            + render_recommendation(rec)
            + "\n\n"
            "--- Case context ---\n"
            + case_ctx
            + "\n\n"
            + rules
            + _PARTIAL_QUESTIONS_RULE
            + _FOLLOW_UP_RULE
        )
        _is_novel = False
        try:
            _is_novel = (getattr(rec, "is_llm_suggested", False)
                         or getattr(rec, "recommendation_status", "") == "novel_llm_suggestion")
        except Exception:
            _is_novel = False
        if _is_novel:
            system += (
                "\n\nNOVEL / AI-SUGGESTED RECOMMENDATION — SPECIAL RULES:\n"
                "This recommendation is for a fault OUTSIDE the approved catalog, and "
                "was generated by AI. The six standard fault modes and the SOP/case "
                "documents may NOT cover this fault.\n"
                "- Rule 1 is RELAXED for this case: when the recommendation and the "
                "documents do not contain the answer, you MAY answer from your own "
                "general engineering and maintenance knowledge.\n"
                "- When you do so, you MUST clearly label it, e.g. 'Based on general "
                "engineering knowledge (not from an approved procedure): ...'.\n"
                "- Still try search_documents_tool first in case a related document helps; "
                "if it does not cover the fault, fall back to your own knowledge rather "
                "than refusing.\n"
                "- Do NOT claim this is from an approved SOP or the catalog. Be honest "
                "that it is general knowledge for a novel fault requiring human validation.\n"
            )
        return system

    # No recommendation loaded — general assistant mode
    return (
        "You are Agent 6.6, a prescriptive maintenance assistant. "
        "No specific recommendation is loaded right now. "
        "You can answer questions about assets, inventory/parts, maintenance windows, "
        "and maintenance procedures/SOPs/past cases using your tools.\n\n"
        "RULES — follow these exactly:\n"
        "1. For live-data questions (is a part in stock, asset criticality or cost, "
        "available windows) — CALL the appropriate tool; never guess numbers.\n"
        "2. For questions about procedures, repair steps, safety/LOTO, torque specs, "
        "or what happened in past cases — CALL search_documents_tool with a descriptive "
        "query and answer from the returned excerpts. Cite the source document name "
        "(and page) in your answer. If the excerpts don't contain the answer, say so "
        "rather than guessing.\n"
        "3. If asked something unrelated to maintenance or this plant's data, politely "
        "say it is out of scope.\n"
        "4. Never invent part numbers, costs, windows, specs, or asset data.\n"
        "5. Keep answers CONCISE by default — 2 to 4 sentences per question, "
        "direct and to the point. EXCEPTIONS to brevity: "
        "If the user asks multiple questions in one message, answer EVERY question — "
        "do not skip any. Address each one in order. "
        "If the user explicitly says 'tell me everything', 'full details', "
        "'elaborate', 'be comprehensive', or 'explain in detail', give a "
        "thorough answer without length restriction. "
        "If the user asks for step-by-step instructions or a procedure, "
        "list ALL steps — do not truncate. "
        "Do NOT dump long procedures unprompted — only when explicitly asked.\n"
        "6. NO RECOMMENDATION LOADED: There is currently NO recommendation generated. "
        "If the user asks about 'this recommendation', 'the recommendation', its "
        "details, action, urgency, parts, window, approver, or asks you to summarize or "
        "explain the recommendation — do NOT retrieve one from the decision history and "
        "do NOT present any past recommendation as if it were current. Instead, reply "
        "clearly that no recommendation has been generated yet, so you can't give "
        "details on a current one, and invite them to ask about specific parts, "
        "maintenance windows, assets, or procedures — which you can answer with your "
        "tools.\n"
        "7. DECISION HISTORY: For explicit questions about PAST decisions or history "
        "(e.g. 'what recommendations were made last week', 'has this happened before') "
        "— you MUST call get_decision_history_tool. Pass "
        "asset_id and/or fault_mode as filters ONLY if the user named a specific "
        "asset or fault; otherwise call it with no filters to get all recent "
        "decisions. Then give a BRIEF summary (3-5 sentences max): how many "
        "past decisions there are, the common patterns (e.g. mostly lubrication "
        "services or bearing replacements), and the 2-3 MOST RECENT ones with "
        "their dates. Do NOT list every entry verbatim — enumerating all of them "
        "will get cut off. Tell the user the complete history is available to "
        "download via the 'Decision History Log' button shown below your answer. "
        "NEVER say you cannot access past recommendations — the decision log IS "
        "available to you via this tool. Time-based filtering (today, last 10 "
        "minutes) is done by reading the timestamps in the returned entries. "
        "Do NOT use it to answer 'this recommendation' questions (see the NO "
        "RECOMMENDATION LOADED rule)."
        + _PARTIAL_QUESTIONS_RULE
        + _FOLLOW_UP_RULE
    )


# ---------------------------------------------------------------------------
# History converter
# ---------------------------------------------------------------------------

def _history_to_messages(history: list) -> list:
    """Convert [{"role": "user"|"assistant", "content": str}, ...] to LangChain messages."""
    out = []
    for turn in history:
        role = turn.get("role", "")
        content = turn.get("content", "")
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


# ---------------------------------------------------------------------------
# Follow-up question extractor
# ---------------------------------------------------------------------------

def _contextual_fallbacks(mode: str, last_question: str, rec) -> list:
    """Derive contextual fallback follow-ups from the current rec and last question."""
    qs = []
    try:
        if rec is not None:
            if getattr(rec, "required_parts", None):
                qs.append("Is the required part available in time?")
            if getattr(rec, "window_chosen", None):
                qs.append("Why was this maintenance window chosen?")
            status = getattr(rec, "recommendation_status", "")
            if status and status != "ok":
                qs.append("Why was this recommendation escalated?")
            if getattr(rec, "is_llm_suggested", False):
                qs.append("What makes this a novel-fault suggestion?")
            qs.append("What does the SOP say about this action?")
    except Exception:
        pass
    if mode == "general" and not qs:
        qs = [
            "Is a specific part in stock, e.g. SKF-6205?",
            "What SOPs cover common bearing faults?",
            "Which assets are most critical?",
        ]
    seen = set()
    out = []
    for q in qs:
        if q.lower() != (last_question or "").lower() and q not in seen:
            seen.add(q)
            out.append(q)
        if len(out) == 3:
            break
    if not out:
        if mode == "general":
            out = ["Is a specific part in stock?",
                   "What SOPs are available for a fault?",
                   "Tell me about a specific asset."]
        else:
            out = ["Can you explain this recommendation?",
                   "What are the next steps?",
                   "Who needs to approve this?"]
    return out


def _generate_follow_ups(question: str, answer: str, rec, mode: str) -> list:
    """Dedicated short LLM call for contextual, in-scope follow-up suggestions.
    Falls back to _contextual_fallbacks on any failure."""
    _CAPABILITIES = (
        "parts and inventory availability, maintenance windows and scheduling, "
        "SOPs and reference documents, details of the current recommendation "
        "(action, urgency, approver, why it was chosen), decision history / past "
        "cases, and asset information"
    )
    _OUT_OF_SCOPE = (
        "fault diagnosis or root cause, raw sensor/vibration data, risk scores or "
        "remaining-useful-life calculations, or creating/changing a recommendation"
    )
    try:
        if mode == "general":
            sys_prompt = (
                "You suggest 3 short follow-up questions for a maintenance assistant. "
                "NO recommendation is loaded and NO specific part, asset, window, or "
                "action has been chosen yet. "
                f"The assistant CAN answer: {_CAPABILITIES}. "
                f"The assistant CANNOT answer: {_OUT_OF_SCOPE} — never suggest those. "
                "CRITICAL: Do NOT use the words 'this' or 'the' to refer to a specific "
                "part, action, window, asset, or recommendation — none exist yet. Never "
                "write 'this part', 'this action', 'this window', 'the recommendation'. "
                "Instead write self-contained questions a user could ask cold, e.g. "
                "'Is bearing SKF-6205 in stock?', 'What SOPs cover lubrication faults?', "
                "'Which assets are most critical?', 'What windows are open this week?'. "
                "Each 12 words or fewer, varied. Output ONLY the 3 questions, one per "
                "line, no numbering."
            )
            usr_prompt = (
                f"User asked: {question}\n\n"
                f"Assistant answered: {answer[:600]}\n\n"
                "Suggest 3 self-contained general questions. Do NOT reference 'this' or "
                "'the' specific part/action/window/recommendation — none exist yet. One "
                "per line."
            )
        else:
            sys_prompt = (
                "You suggest 3 short follow-up questions a user might ask NEXT in a "
                "maintenance-recommendation assistant. "
                f"The assistant CAN answer: {_CAPABILITIES}. "
                f"The assistant CANNOT answer: {_OUT_OF_SCOPE} — never suggest those. "
                "Base the questions on what the user just asked and how it was "
                "answered. Make them specific, natural next steps, each 12 words or "
                "fewer, and varied. Output ONLY the 3 questions, one per line, no "
                "numbering, no extra text."
            )
            usr_prompt = (
                f"User asked: {question}\n\n"
                f"Assistant answered: {answer[:600]}\n\n"
                "Suggest 3 in-scope follow-up questions, one per line."
            )
        _fu_cfg = {}
        _fu_handler = observability.get_callback_handler()
        if _fu_handler is not None:
            _fu_cfg["callbacks"] = [_fu_handler]
        resp = _followup_llm.invoke([SystemMessage(content=sys_prompt),
                                     HumanMessage(content=usr_prompt)], config=_fu_cfg)
        text = (resp.content or "").strip()
        qs = []
        for line in text.split("\n"):
            q = line.strip().lstrip("0123456789.-) ").strip()
            q = re.sub(r'^[Qq]\d+\s*[:.\-]\s*', '', q).strip()
            if q and len(q) > 4 and q.lower() != (question or "").lower():
                qs.append(q)
        qs = qs[:3]
        # general-mode guard: reject "this part / this action / this window" phrasing
        if mode == "general":
            _banned = ("this part", "this action", "this window", "this recommendation",
                       "the recommendation", "this asset", "this fault", "this service",
                       "this procedure", "this maintenance")
            _clean = [q for q in qs
                      if not any(b in q.lower() for b in _banned)]
            if len(_clean) < 3:
                for q in _contextual_fallbacks("general", question, rec):
                    if q not in _clean and not any(b in q.lower() for b in _banned):
                        _clean.append(q)
                    if len(_clean) >= 3:
                        break
            qs = _clean[:3]
        if len(qs) >= 2:
            return qs
    except Exception as e:
        print(f"[follow_ups] dedicated call failed: {type(e).__name__}: {e}")
    return _contextual_fallbacks(mode, question, rec)


def _extract_follow_ups(text: str, mode: str = "recommendation",
                        last_question: str = "", rec=None) -> tuple:
    """Strip <follow_ups> block from answer text; return (clean_text, [q1, q2, q3])."""
    pattern = r"<follow_ups>(.*?)</follow_ups>"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        print("[follow_ups] WARNING: LLM did not return <follow_ups> block")
        return text.strip(), _contextual_fallbacks(mode, last_question, rec)
    clean = text[:match.start()].strip()
    lines = match.group(1).strip().splitlines()
    questions = []
    for line in lines:
        line = line.strip()
        if re.match(r"Q\d+:", line):
            q = re.sub(r"^Q\d+:\s*", "", line).strip()
            if q:
                questions.append(q)
    if not questions:
        print("[follow_ups] WARNING: <follow_ups> block was empty after parsing")
        return clean, _contextual_fallbacks(mode, last_question, rec)
    return clean, questions[:3]


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def answer_about_recommendation(
    question: str,
    rec=None,
    diagnosis=None,
    risk=None,
    history: list = None,
    defaulted_fields=None,
) -> dict:
    """
    Answer a user question about a MaintenanceRecommendation (or general
    maintenance data when no recommendation is loaded).

    Parameters
    ----------
    question  : the user's current question (plain text)
    rec       : MaintenanceRecommendation or None (if None, runs in general mode)
    diagnosis : FaultDiagnosis used to produce rec (for context in system prompt)
    risk      : RiskAssessment used to produce rec (for context in system prompt)
    history   : list of {"role": "user"|"assistant", "content": str} from
                prior turns in this conversation (None treated as empty list)

    Returns
    -------
    {"answer": str, "trace": list[{"tool", "args", "result"}], "follow_ups": list[str]}
    Never raises — exceptions are caught and returned as an error answer.
    """
    # Code-enforced guard: no rec loaded + a "this recommendation" question →
    # return a fixed redirect instead of letting the LLM pull a phantom rec from
    # history.
    if rec is None:
        _ql = (question or "").lower()
        _rec_refs = ("this recommendation", "the recommendation",
                     "this action", "the recommended action",
                     "this part", "the part for this", "part for this recommendation",
                     "this window", "window for this",
                     "the approver for this", "approver for this recommendation",
                     "details of this", "explain this recommendation",
                     "summarize the recommendation", "summary of this recommendation",
                     "current recommendation", "recommended action for this")
        if any(r in _ql for r in _rec_refs):
            _msg = (
                "No recommendation has been generated yet, so I can't give "
                "details on a current one. Generate a recommendation from the "
                "sidebar first — then I can explain its action, parts, window, "
                "and approver. In the meantime, you can ask me about a specific "
                "part's availability, an asset, maintenance windows, or SOPs."
            )
            return {"answer": _msg, "trace": [], "follow_ups": []}

    if history is None:
        history = []
    trimmed_history = history[-MAX_HISTORY:]

    system_msg = SystemMessage(content=_build_system(rec, diagnosis, risk,
                                                     defaulted_fields=defaulted_fields))
    initial_messages = (
        [system_msg]
        + _history_to_messages(trimmed_history)
        + [HumanMessage(content=question)]
    )

    _result = None
    _lf_handler = observability.get_callback_handler()
    _invoke_cfg = {"recursion_limit": 8}
    if _lf_handler is not None:
        _invoke_cfg["callbacks"] = [_lf_handler]
    for _attempt in range(2):
        try:
            _result = chat_graph.invoke({"messages": initial_messages}, config=_invoke_cfg)
            break
        except Exception as exc:
            _msg = str(exc).lower()
            _transient = any(k in _msg for k in [
                "upstream", "timeout", "rate", "503", "429",
                "connection", "unavailable"])
            if _attempt == 0 and _transient:
                time.sleep(2)
                continue
            if _transient:
                return {"answer": "I'm temporarily unable to reach the "
                        "language model (connection issue). Please try "
                        "your question again in a moment.", "trace": [], "follow_ups": []}
            return {"answer": f"Something went wrong: {exc}", "trace": [], "follow_ups": []}

    if _result is None:
        return {"answer": "Something went wrong: no result returned.", "trace": [], "follow_ups": []}

    raw_answer = _result["messages"][-1].content
    mode = "general" if rec is None else "recommendation"
    answer, _ = _extract_follow_ups(raw_answer, mode=mode, last_question=question, rec=rec)
    if mode == "general" or rec is None:
        follow_ups = []
    else:
        follow_ups = _generate_follow_ups(question, answer, rec, mode)

        # --- filter out already-asked questions (normalized), then backfill to 3 ---
        def _norm_q(s):
            return re.sub(r'[^a-z0-9 ]', '', (s or "").lower()).strip()

        _asked = {_norm_q(m.get("content", "")) for m in (history or [])
                  if m.get("role") == "user"}
        _asked.add(_norm_q(question))

        _filtered = []
        _seen_norm = set()
        for q in follow_ups:
            nq = _norm_q(q)
            if nq and nq not in _asked and nq not in _seen_norm:
                _filtered.append(q)
                _seen_norm.add(nq)

        # backfill from contextual fallbacks if we dropped below 3
        if len(_filtered) < 3:
            for q in _contextual_fallbacks(mode, question, rec):
                nq = _norm_q(q)
                if nq and nq not in _asked and nq not in _seen_norm:
                    _filtered.append(q)
                    _seen_norm.add(nq)
                if len(_filtered) >= 3:
                    break

        follow_ups = _filtered[:3]
        # --- end filter ---

    # Build trace: pair each tool call in AIMessages with its ToolMessage result
    trace = []
    messages = _result["messages"]
    tool_results: dict[str, str] = {
        m.tool_call_id: m.content
        for m in messages
        if isinstance(m, ToolMessage)
    }
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            for call in m.tool_calls:
                trace.append({
                    "tool":   call["name"],
                    "args":   call["args"],
                    "result": tool_results.get(call["id"], ""),
                })

    observability.flush()
    return {"answer": answer, "trace": trace, "follow_ups": follow_ups}
