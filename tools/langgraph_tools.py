"""
langgraph_tools.py
LangGraph-compatible tool wrappers for Agent 6.6.
Each function is decorated with @tool so the LLM can discover and call it.
"""
import json
from datetime import datetime, timezone, timedelta

from langchain_core.tools import tool

from tools.inventory_checker import check_part_for_action
from tools.schedule_reader import find_windows
from tools.data_loader import load_assets, load_inventory
from agents.prescriptive_optimization_agent import recommend_action
from agents.decision_logger import get_history
from scenarios import SCENARIO_MAP

_IST = timezone(timedelta(hours=5, minutes=30))


def _fmt_ist(iso_utc: str) -> str:
    """Render a stored UTC ISO timestamp as a human-readable IST string."""
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M IST")
    except Exception:
        return iso_utc[:16].replace("T", " ")   # safe fallback


@tool
def search_documents_tool(query: str) -> str:
    """Search the maintenance SOPs and past learned-case documents for relevant information.

    Use this for questions about procedures, how to perform a repair, safety/LOTO steps,
    torque specs, part specifications, or what happened in past cases.
    Returns relevant excerpts with their source document and page.

    Input: a natural-language query describing what you are looking for.
    """
    try:
        from rag.document_store import search_documents
        results = search_documents(query)
        if not results:
            return "No relevant documents found for this query."
        parts = []
        for r in results:
            parts.append(
                f"[{r['source_file']}, page {r['page_number']}]\n{r['text']}\n---"
            )
        return "\n".join(parts)
    except FileNotFoundError:
        return "Document index not available."
    except Exception as exc:
        return f"Document search error: {exc}"


@tool
def check_inventory_tool(bearing_id: str, action_type: str) -> str:
    """Check whether the spare part needed for a maintenance action is available in inventory.

    Use when you need to know if a part is in stock and its lead time.

    Inputs:
      bearing_id  - the bearing identifier, e.g. 'BRG_005'
      action_type - the planned action, e.g. 'replace_bearing' or 'lubricate'

    Valid action_type values: 'replace', 'replace_bearing', 'lubricate', 'lubrication_service'.

    Returns a JSON string with keys:
      status             - 'in_stock', 'out_of_stock', 'no_matching_part', or 'unknown_action_type'
      in_stock           - true if the part is available right now
      quantity_available - units free to use
      lead_time_days     - 0 if in stock, supplier lead time if out of stock, null if no match
      part_model         - the matched part model number, or null
      part_id            - the matched part ID, or null
    """
    known_bearings = {
        bid
        for part in load_inventory()
        for bid in part.get("compatible_bearing_ids", [])
    }
    if bearing_id not in known_bearings:
        return json.dumps({
            "status": "bearing_not_found",
            "error": f"Bearing '{bearing_id}' is not found in inventory records.",
        })
    result = check_part_for_action(bearing_id, action_type)
    return json.dumps(result)


@tool
def find_windows_tool(asset_id: str, required_hours: float = 2.0, within_days: float = 30.0) -> str:
    """Find upcoming planned maintenance windows for an asset.

    Use when you need to know WHEN a job can be scheduled.

    Inputs:
      asset_id       - the asset identifier, e.g. 'AST_PMP_001'
      required_hours - minimum window duration needed for the job, e.g. 3.0 (default 2.0)
      within_days    - how many days ahead to search (default 30)

    Returns a plain-English summary of available windows, or a clear message if none found.
    """
    known_assets = {a.get("asset_id") for a in load_assets()}
    if asset_id not in known_assets:
        return f"Asset '{asset_id}' not found in asset records."

    windows = find_windows(asset_id, required_hours, within_days=int(within_days))
    if not windows:
        return (
            f"No suitable maintenance window found for asset {asset_id} "
            f"within the next {int(within_days)} days that fits a {required_hours}-hour job."
        )

    lines = [f"Found {len(windows)} window(s) for asset {asset_id}:"]
    for w in windows:
        lines.append(
            f"  - {w.get('window_id', '?')}: "
            f"{w.get('start_utc', '?')} → {w.get('end_utc', '?')} "
            f"({w.get('duration_hours', '?')}h available)"
            + (f"  Note: {w['notes']}" if w.get("notes") else "")
        )
    return "\n".join(lines)


@tool
def get_asset_info_tool(asset_id: str) -> str:
    """Look up details about an asset: its type, how critical it is, whether it's a bottleneck, and its downtime cost per hour.

    Input: asset_id like 'AST_GBX_001'

    Returns a plain-English summary of the asset's key attributes,
    or a clear 'asset not found' message if the ID is not in master data.
    """
    for asset in load_assets():
        if asset.get("asset_id") == asset_id:
            bottleneck = "yes" if asset.get("is_bottleneck") else "no"
            cost = asset.get("downtime_cost_per_hour")
            cost_str = f"${cost:,}/hr" if cost is not None else "unknown"
            return (
                f"Asset {asset_id} — {asset.get('asset_name', 'unknown name')}\n"
                f"  Type        : {asset.get('asset_type', 'unknown')}\n"
                f"  Criticality : {asset.get('criticality', 'unknown')}\n"
                f"  Bottleneck  : {bottleneck}\n"
                f"  Downtime cost: {cost_str}\n"
                f"  Status      : {asset.get('status', 'unknown')}"
            )
    return f"Asset not found: '{asset_id}' is not present in asset master data."


@tool
def get_decision_history_tool(asset_id: str = "",
                               fault_mode: str = "",
                               limit: int = 15) -> str:
    """Search the decision history log for past recommendations.

    Use when asked about past decisions, previous cases, what was done before,
    or history for an asset.

    Inputs:
      asset_id   - optional asset filter, e.g. 'AST_PMP_001'
      fault_mode - optional fault filter, e.g. 'lubrication_issue'
      limit      - max entries to return (default 15)

    Returns most recent matching entries from the decision log.
    """
    if asset_id:
        assets = load_assets()
        matched = None
        asset_id_lower = asset_id.lower().strip()
        for a in assets:
            if a["asset_id"].lower() == asset_id_lower:
                matched = a["asset_id"]
                break
        if not matched:
            for a in assets:
                if (asset_id_lower in a["asset_name"].lower() or
                        asset_id_lower in a["asset_id"].lower()):
                    matched = a["asset_id"]
                    break
        if matched:
            asset_id = matched

    results = get_history(
        asset_id=asset_id or None,
        fault_mode=fault_mode or None,
        limit=limit,
    )
    if not results:
        return "No matching entries found in the decision log."
    lines = []
    for e in results:
        blocked = " [BLOCKED]" if e.get("is_blocked") else ""
        lines.append(
            f"{_fmt_ist(e['logged_at_utc'])} | {e['asset_id']} | "
            f"{e['fault_mode']} {e['severity']} | "
            f"{e['recommended_action']}{blocked} | "
            f"urgency={e['urgency']} | approval_required_from={e['responsible_approver']} | status={e['approval_status']}"
        )
    return "\n".join(lines)


def render_recommendation(rec) -> str:
    """Render a MaintenanceRecommendation object as a human-readable string.

    Called by recommend_action_tool and by recommendation_chat.py so both
    surfaces produce identical text.
    """
    lines = [
        f"RECOMMENDATION STATUS : {rec.recommendation_status}",
        f"APPROVAL STATUS       : {rec.approval_status}",
        f"IS LLM SUGGESTED      : {rec.is_llm_suggested}",
        "",
        f"Case                  : {rec.case_id}",
        f"Asset                 : {rec.asset_id}",
        f"Bearing               : {rec.bearing_id}",
        "",
        f"RECOMMENDED ACTION    : {rec.recommended_action.name}",
        f"  Description         : {rec.recommended_action.description}",
        f"  Duration            : {rec.recommended_action.estimated_duration_hours}h",
        f"URGENCY               : {rec.urgency}",
        f"WINDOW CHOSEN         : {rec.window_chosen or 'none'}",
        "",
    ]

    if rec.required_parts:
        lines.append("REQUIRED PARTS:")
        for p in rec.required_parts:
            lines.append(f"  {p.part_number}  qty={p.quantity}  lead_time={p.lead_time_days}d")
    else:
        lines.append("REQUIRED PARTS: none")

    lines.append("")

    if rec.ranked_alternatives:
        lines.append("RANKED ALTERNATIVES:")
        for i, a in enumerate(rec.ranked_alternatives, 1):
            lines.append(f"  {i}. {a.name} — {a.description} ({a.estimated_duration_hours}h)")
    else:
        lines.append("RANKED ALTERNATIVES: none")

    lines += [
        "",
        f"RESPONSIBLE PERSON    : {rec.responsible_person}",
        f"RESPONSIBLE APPROVER  : {rec.responsible_approver}",
    ]

    if rec.contributors:
        lines.append("CONTRIBUTORS:")
        for c in rec.contributors:
            lines.append(
                f"  - {c.get('role', '?')} ({c.get('name', '?')}): {c.get('concern', '?')}"
            )

    lines.append("")
    lines.append("EVIDENCE:")
    for k, v in rec.evidence.items():
        lines.append(f"  {k:<22} : {v}")

    lines += [
        "",
        "RATIONALE:",
        f"  {rec.rationale}",
        "",
        f"Generated at          : {rec.generated_at_utc}",
    ]

    return "\n".join(lines)


@tool
def recommend_action_tool(scenario_label: str) -> str:
    """Run the full Agent 6.6 maintenance recommendation pipeline for a named demo scenario.

    Use when you need a complete maintenance recommendation for a known scenario.

    Input: scenario_label — a scenario prefix like 'B4', or the full label like
    'B4 — Lubrication issue, pump stage_2'. Matching is case-insensitive.

    Available prefixes: A1, A2, A3, A4, B1, B2, B3, B4, B5, B6, B7.

    Returns all MaintenanceRecommendation fields: status, approval, action, urgency,
    window, parts, alternatives, approver, contributors, evidence, and rationale.
    """
    key = scenario_label.strip().upper()
    matched = None
    for label in SCENARIO_MAP:
        prefix = label.split("—")[0].strip().upper()   # "B4 — ..." -> "B4"
        if prefix == key or label.upper() == key:
            matched = label
            break

    if matched is None:
        available = ", ".join(lbl.split("—")[0].strip() for lbl in SCENARIO_MAP)
        return (
            f"Scenario '{scenario_label}' not found. "
            f"Available prefixes: {available}"
        )

    scn = SCENARIO_MAP[matched]
    rec = recommend_action(scn["diagnosis"], scn["risk"], scn["guidance"])
    return render_recommendation(rec)
