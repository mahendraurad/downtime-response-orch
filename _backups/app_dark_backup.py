"""
app.py — Streamlit frontend for Agent 6.6 (Prescriptive Optimization Agent).
Run from the project root: streamlit run app.py

Display layer only. Agent logic lives in agents/prescriptive_optimization_agent.py.
"""
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st
from fpdf import FPDF
from agents.prescriptive_optimization_agent import recommend_action
from schemas.diagnosis import FaultDiagnosis
from schemas.risk import RiskAssessment
from schemas.knowledge import KnowledgeGuidance
from tools.data_loader import load_operations_context, load_personas, load_assets, load_bearings, load_inventory
from agents.decision_logger import get_history
from scenarios import SCENARIOS, SCENARIO_LABELS, SCENARIO_MAP
from recommendation_chat import answer_about_recommendation
from agents.fault_classifier import classify_fault
from tools.schedule_reader import find_windows

import re as _re_quality

def _is_meaningful_description(text: str) -> bool:
    """True only if the text plausibly describes a fault."""
    if not text or not text.strip():
        return False
    t = text.strip()
    # must be at least 8 characters of real content
    if len(t) < 8:
        return False
    # must contain letters (not just numbers/symbols/emoji)
    letters = _re_quality.sub(r'[^a-zA-Z]', '', t)
    if len(letters) < 5:
        return False
    # must contain at least 2 word-like tokens (≥2 letters each)
    words = [w for w in _re_quality.findall(r'[a-zA-Z]{2,}', t)]
    if len(words) < 2:
        return False
    # reject obvious keyboard-mash: a long token with no vowels
    for w in words:
        if len(w) >= 6 and not _re_quality.search(r'[aeiouAEIOU]', w):
            return False
    return True


NOW = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)

_FAILURE_STATUSES = {
    "blocked_no_part", "blocked_unknown_asset",
    "unreliable_diagnosis", "catalog_miss",
    "blocked_invalid_input",
}

_OLD_SCENARIOS = [
    {
        "label": "A1 — Unknown asset (blocked)",
        "diagnosis": FaultDiagnosis(
            case_id="G-001", asset_id="AST_FAKE", bearing_id="BRG_FAKE",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.90, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-001", asset_id="AST_FAKE", bearing_id="BRG_FAKE",
            failure_probability=0.85, risk_level="high",
            rul_min_days=5, rul_max_days=10, confidence=0.85,
            business_impact_flag=True, estimated_downtime_cost_per_hour=5000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-001"),
    },
    {
        "label": "A2 — Unreliable diagnosis (low confidence)",
        "diagnosis": FaultDiagnosis(
            case_id="G-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_2",
            confidence=0.30, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.40, risk_level="medium",
            rul_min_days=20, rul_max_days=30, confidence=0.30,
            business_impact_flag=False, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-002"),
    },
    {
        "label": "A3 — Catalog miss (inner_race_fault, no SOP)",
        "diagnosis": FaultDiagnosis(
            case_id="G-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_002", fault_mode="inner_race_fault",
            affected_component="bearing_inner_race", severity="stage_3",
            confidence=0.85, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.88, risk_level="high",
            rul_min_days=5, rul_max_days=8, confidence=0.85,
            business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-003"),
    },
    {
        "label": "A4 — Blocked on part (gearbox, SKF22318-E out of stock)",
        "diagnosis": FaultDiagnosis(
            case_id="G-004", asset_id="AST_GBX_001", bearing_id="BRG_011",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.97, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-004", asset_id="AST_GBX_001", bearing_id="BRG_011",
            failure_probability=0.95, risk_level="critical",
            rul_min_days=5, rul_max_days=7, confidence=0.93,
            business_impact_flag=True, estimated_downtime_cost_per_hour=18000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-004"),
    },
    {
        "label": "B1 — Healthy motor (routine preventive service)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-001", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_003", fault_mode="lubrication_issue",
            affected_component="lubrication_system", severity="monitor",
            confidence=0.75, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-001", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.05, risk_level="low",
            rul_min_days=45, rul_max_days=60, confidence=0.90,
            business_impact_flag=False, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-001"),
    },
    {
        "label": "B2 — Outer race fault, motor stage_3",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.96, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.92, risk_level="critical",
            rul_min_days=5, rul_max_days=10, confidence=0.91,
            business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(
            case_id="DEMO-002",
            source_documents=["SOP_001_outer_race_motor_stage3.pdf"],
        ),
    },
    {
        "label": "B3 — Inner race fault, motor stage_3 (catalog miss)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_002", fault_mode="inner_race_fault",
            affected_component="bearing_inner_race", severity="stage_3",
            confidence=0.82, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.88, risk_level="high",
            rul_min_days=5, rul_max_days=8, confidence=0.85,
            business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-003"),
    },
    {
        "label": "B4 — Lubrication issue, pump stage_2",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-004", asset_id="AST_PMP_001", bearing_id="BRG_005",
            fault_code="FT_003", fault_mode="lubrication_issue",
            affected_component="lubrication_system", severity="stage_2",
            confidence=0.88, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-004", asset_id="AST_PMP_001", bearing_id="BRG_005",
            failure_probability=0.60, risk_level="medium",
            rul_min_days=14, rul_max_days=20, confidence=0.85,
            business_impact_flag=False, estimated_downtime_cost_per_hour=10000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(
            case_id="DEMO-004",
            source_documents=["SOP_003_lubrication_pump_stage2.pdf"],
        ),
    },
    {
        "label": "B5 — Signal dropout, conveyor (unreliable diagnosis)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-005", asset_id="AST_CON_001", bearing_id="BRG_009",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="monitor",
            confidence=0.15, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-005", asset_id="AST_CON_001", bearing_id="BRG_009",
            failure_probability=0.20, risk_level="low",
            rul_min_days=30, rul_max_days=60, confidence=0.20,
            business_impact_flag=False, estimated_downtime_cost_per_hour=15000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-005"),
    },
    {
        "label": "B6 — Unknown asset (not in master data)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-006", asset_id="AST_UNKNOWN_001", bearing_id="BRG_UNKNOWN",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.80, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-006", asset_id="AST_UNKNOWN_001", bearing_id="BRG_UNKNOWN",
            failure_probability=0.85, risk_level="high",
            rul_min_days=5, rul_max_days=10, confidence=0.80,
            business_impact_flag=False, estimated_downtime_cost_per_hour=0.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-006"),
    },
    {
        "label": "B7 — Gearbox fault, stage_3 (part out of stock)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-007", asset_id="AST_GBX_001", bearing_id="BRG_011",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.97, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-007", asset_id="AST_GBX_001", bearing_id="BRG_011",
            failure_probability=0.95, risk_level="critical",
            rul_min_days=5, rul_max_days=7, confidence=0.93,
            business_impact_flag=True, estimated_downtime_cost_per_hour=18000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(
            case_id="DEMO-007",
            source_documents=["SOP_005_outer_race_gearbox_stage3.pdf"],
        ),
    },
]

SCENARIO_LABELS = [s["label"] for s in SCENARIOS]
SCENARIO_MAP    = {s["label"]: s for s in SCENARIOS}


def _get_window(window_id: str) -> dict | None:
    if not window_id:
        return None
    for w in load_operations_context():
        if w.get("window_id") == window_id:
            return w
    return None


# ── Page config (must be FIRST Streamlit call) ─────────────────────────────────
st.set_page_config(
    page_title="Agent 6.6 — Maintenance Recommender",
    layout="wide",
    initial_sidebar_state="expanded",
)

import os as _os
_REQUIRED_DATA = [
    "data/asset_master.json", "data/inventory.json", "data/personas.json",
    "data/action_catalog.json", "data/fault_taxonomy.json",
    "data/operations_context.json",
]
_missing = [p for p in _REQUIRED_DATA if not _os.path.exists(p)]
if _missing:
    st.error(
        "⚠ Some data files are missing: " + ", ".join(_missing) +
        ". The app will run but recommendations may be incomplete. "
        "Please restore these files."
    )


# ── Fonts + Theme CSS ──────────────────────────────────────────────────────────
st.html("""
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* ── Global fonts ── */
html, body, [class*="css"] {
    font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* ── Dark backgrounds ── */
html, body { background-color: #080d1a !important; }
.stApp, [data-testid="stAppViewContainer"] {
    background-color: #080d1a !important;
}
.main, .main .block-container {
    background-color: #080d1a !important;
    padding-top: 0.85rem;
    max-width: 1600px;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background-color: #0d1628 !important;
    border-right: 1px solid #1e2d4d !important;
}

/* ── Always-visible, resizable sidebar ──
   Force the sidebar to render in every state (cancel the collapse slide-out) and give
   it a generous default width via MIN-WIDTH only. Using min-width (not a fixed width)
   means it's a floor, not a lock — the drag-to-resize handle on the right edge can
   still pull it WIDER. The collapse arrow is left in place as a normal toggle. */
section[data-testid="stSidebar"] {
    transform: none !important;
    visibility: visible !important;
    margin-left: 0 !important;
    left: 0 !important;
    min-width: 280px !important;
    max-width: 280px !important;
}

section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] div,
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
    color: #e4ecff !important;
}

/* ── Selectbox in sidebar ── */
section[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background-color: #172038 !important;
    border: 1px solid #2a3a5c !important;
    border-radius: 8px !important;
    color: #e4ecff !important;
}

/* ── Primary button ── */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #4f8eff 0%, #2d5ecc 100%) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em !important;
    padding: 0.55rem 1.4rem !important;
    box-shadow: 0 2px 12px rgba(79,142,255,0.25) !important;
    transition: box-shadow 0.2s ease !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 4px 22px rgba(79,142,255,0.45) !important;
}
.stButton > button:not([kind="primary"]) {
    background-color: #172038 !important;
    color: #e4ecff !important;
    border: 1px solid #1e2d4d !important;
    border-radius: 8px !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background-color: #0d1628 !important;
    border-bottom: 1px solid #1e2d4d !important;
    gap: 0 !important;
    padding: 0 !important;
}
.stTabs [data-baseweb="tab"] {
    color: #6d85b8 !important;
    background-color: transparent !important;
    font-weight: 500 !important;
    padding: 0.7rem 1.75rem !important;
    border-bottom: 2px solid transparent !important;
    font-size: 0.9rem !important;
}
.stTabs [aria-selected="true"] {
    color: #4f8eff !important;
    border-bottom-color: #4f8eff !important;
    background-color: transparent !important;
}
.stTabs [data-baseweb="tab-panel"] {
    background-color: #080d1a !important;
    padding: 1.5rem 0 !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: 8px !important;
}

/* ── HR dividers ── */
hr { border-color: #1e2d4d !important; }

/* ── Markdown text color ── */
[data-testid="stMarkdownContainer"] p { color: #b0c4e8; }

/* ── Uniform card heights in detail rows ── */
[data-testid="stHorizontalBlock"] {
    align-items: stretch !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    display: flex !important;
    flex-direction: column !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] > div,
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] > div > div,
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] > div > div > div {
    flex: 1 1 auto !important;
    display: flex !important;
    flex-direction: column !important;
}

/* ── Hide Streamlit chrome ── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* ── Frosted glass popover panel ── */
[data-testid="stPopover"] [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 16px !important;
    background: linear-gradient(160deg, rgba(20,26,48,0.85) 0%, rgba(13,18,38,0.9) 100%) !important;
    backdrop-filter: blur(16px) !important;
    -webkit-backdrop-filter: blur(16px) !important;
    border: 1px solid rgba(129,140,248,0.25) !important;
    box-shadow: 0 8px 40px rgba(79,70,229,0.25) !important;
}

/* ── Chat header bar ── */
.chat-header {
    display: flex; flex-direction: column;
    padding: 2px 2px 9px 2px;
    border-bottom: 1px solid rgba(129,140,248,0.2);
    margin-bottom: 4px;
}
.chat-header-title {
    font-size: 1.08rem; font-weight: 700;
    background: linear-gradient(90deg,#a5b4fc 0%,#818cf8 50%,#6366f1 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
}
.chat-header-sub {
    font-size: 0.72rem; color: #8b9bd4;
    font-family: 'JetBrains Mono', monospace; margin-top: 2px;
}

/* ── Bold/code inside bubbles ── */
[data-testid="stPopover"] div strong { font-weight: 700 !important; color: #c7d2fe !important; }
[data-testid="stPopover"] div code {
    background: rgba(129,140,248,0.18); padding: 1px 6px; border-radius: 5px;
    font-size: 0.82em; font-family: 'JetBrains Mono', monospace; color: #c4b5fd;
}

/* ── Follow-up suggestion chips (glassy violet) ── */
.followup-chips + div [data-testid="stButton"] > button,
.followup-chips ~ div [data-testid="stButton"] > button {
    background: linear-gradient(135deg, rgba(129,140,248,0.14) 0%, rgba(99,102,241,0.14) 100%) !important;
    color: #c7d2fe !important;
    border: 1px solid rgba(129,140,248,0.4) !important;
    border-radius: 20px !important;
    font-size: 0.8rem !important;
    padding: 7px 15px !important;
    text-align: left !important;
    transition: all 0.18s ease !important;
    margin: 3px 0 !important;
}
.followup-chips + div [data-testid="stButton"] > button:hover,
.followup-chips ~ div [data-testid="stButton"] > button:hover {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%) !important;
    color: #ffffff !important;
    border-color: #a5b4fc !important;
    box-shadow: 0 4px 20px rgba(124,92,246,0.5) !important;
    transform: translateY(-1px) !important;
}

/* ── Send button (violet gradient) ── */
[data-testid="stPopover"] button[kind="secondary"] {
    background: linear-gradient(135deg,#6d6df5 0%,#9b6cf8 100%) !important;
    color: #fff !important; border: none !important;
    border-radius: 10px !important; font-weight: 700 !important;
    letter-spacing: 0.02em !important;
    padding: 0.5rem 1.6rem !important;
    box-shadow: 0 4px 18px rgba(124,92,246,0.55),
                0 0 0 1px rgba(165,180,252,0.3) inset !important;
    transition: transform 0.15s ease, box-shadow 0.15s ease, filter 0.1s ease !important;
}
[data-testid="stPopover"] button[kind="secondary"]:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 28px rgba(124,92,246,0.7),
                0 0 0 1px rgba(165,180,252,0.5) inset !important;
    filter: brightness(1.08) !important;
}
[data-testid="stPopover"] button[kind="secondary"]:active {
    transform: translateY(1px) !important;
    filter: brightness(0.8) !important;
    box-shadow: 0 2px 8px rgba(124,92,246,0.35) !important;
}

/* ── Source chip (violet tint) ── */
/* note: source line is inline-styled; this nudges its accent toward violet */
</style>
""")

# ── Chat popover (always-visible, top-right) ──────────────────────────────────
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []
if "chat_input_idx" not in st.session_state:
    st.session_state["chat_input_idx"] = 0
if "uncertainty" not in st.session_state:
    st.session_state["uncertainty"] = None
if "_chat_rerun" not in st.session_state:
    st.session_state["_chat_rerun"] = False
if "last_generated_key" not in st.session_state:
    st.session_state["last_generated_key"] = None
if "classified_fault" not in st.session_state:
    st.session_state["classified_fault"] = None
if "awaiting_confirmation" not in st.session_state:
    st.session_state["awaiting_confirmation"] = False
if "fault_confirmed" not in st.session_state:
    st.session_state["fault_confirmed"] = False
st.session_state.setdefault("unknown_guess", None)
st.session_state.setdefault("unknown_fault_confirmed", False)
st.session_state.setdefault("_mode_persist", "Demo scenario")

def _format_sources(trace: list, rec=None, answer: str = "") -> str:
    def _rec_source(rec):
        # Honest source label built from the recommendation's own evidence.
        if rec is None:
            return "Recommendation"
        # AI-generated recommendations are NOT from the catalog — label honestly
        try:
            if (getattr(rec, "is_llm_suggested", False)
                    or getattr(rec, "recommendation_status", "") == "novel_llm_suggestion"):
                return "AI-generated suggestion (not from approved catalog)"
        except Exception:
            pass
        try:
            ev = getattr(rec, "evidence", {}) or {}
            ra = ev.get("recommended_action", "")
            m = re.search(r'(SOP_\d+)', ra)
            if m:
                return f"Action catalog, per {m.group(1)}"
        except Exception:
            pass
        return "Action catalog"

    # If the model refused/couldn't answer AND used no tool, show no source.
    _refusal_markers = (
        "i'm unable", "i am unable", "i cannot provide", "i can't provide",
        "i don't have", "i do not have", "not included in the recommendation",
        "not available in", "consult maintenance manuals", "out of scope",
        "i'm not able", "i am not able", "no information",
    )
    if not trace:
        _a = (answer or "").lower()
        if any(mk in _a for mk in _refusal_markers):
            return ""   # refusal with no tool → no source chip

    if not trace:
        return _rec_source(rec)
    srcs = []
    for t in trace:
        tool = t.get("tool", "")
        result = str(t.get("result", ""))
        if tool == "search_documents_tool":
            docs = set(re.findall(r'(SOP_\d+|CASE_\d+)', result))
            if docs:
                srcs.append("Documents: " + ", ".join(sorted(docs)))
            else:
                srcs.append("SOP/case documents")
        elif tool == "check_inventory_tool":
            srcs.append("Live inventory data")
        elif tool == "find_windows_tool":
            srcs.append("Maintenance schedule")
        elif tool == "get_asset_info_tool":
            srcs.append("Asset master data")
        elif tool == "get_decision_history_tool":
            srcs.append("Decision history log")
    seen = []
    for s in srcs:
        if s not in seen:
            seen.append(s)
    if not seen:
        return _rec_source(rec)
    return " · ".join(seen)


def _pdf_safe(text) -> str:
    """fpdf2 core fonts are latin-1 only. Map common unicode punctuation to ASCII,
    then replace anything still outside latin-1 so the PDF never crashes."""
    if text is None:
        return ""
    s = str(text)
    for _bad, _good in {
        "—": "-", "–": "-", "'": "'", "'": "'",
        "“": '"', "”": '"', "•": "-", "·": "-", "…": "...",
    }.items():
        s = s.replace(_bad, _good)
    return s.encode("latin-1", "replace").decode("latin-1")


def _simple_pdf(title: str, lines: list) -> bytes:
    """Build a plain-text PDF from a title and a list of line strings."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 8, _pdf_safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    for ln in lines:
        pdf.multi_cell(0, 5, _pdf_safe(ln), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _decision_log_pdf() -> bytes:
    rows = get_history(limit=1000) or []
    lines = [f"Total entries: {len(rows)}", ""]
    for r in rows:
        lines.append(
            f"{r.get('logged_at_utc','')} | {r.get('log_id','')} | "
            f"{r.get('asset_id','')} | {r.get('fault_mode','')} "
            f"{r.get('severity','')} | action={r.get('recommended_action','')} "
            f"| urgency={r.get('urgency','')} | status={r.get('recommendation_status','')} "
            f"| approver={r.get('responsible_approver','')}"
        )
    return _simple_pdf("Decision History Log (full)", lines)


def _inventory_pdf() -> bytes:
    rows = load_inventory() or []
    lines = [f"Total parts: {len(rows)}", ""]
    for r in rows:
        lines.append(
            f"{r.get('part_id','')} | {r.get('part_model','')} "
            f"({r.get('part_type','')}) | on_hand={r.get('qty_on_hand','')} "
            f"available={r.get('qty_available','')} lead={r.get('lead_time_days','')}d "
            f"| compatible={', '.join(r.get('compatible_bearing_ids',[]))}"
        )
    return _simple_pdf("Inventory (full)", lines)


def _schedule_pdf() -> bytes:
    rows = load_operations_context() or []
    lines = [f"Total windows: {len(rows)}", ""]
    for r in rows:
        lines.append(
            f"{r.get('window_id','')} | {r.get('asset_id','')} | "
            f"{r.get('window_type','')} | {r.get('start_utc','')} -> "
            f"{r.get('end_utc','')} ({r.get('duration_hours','')}h) | "
            f"{r.get('notes','')}"
        )
    return _simple_pdf("Maintenance Schedule (full)", lines)


def _assets_pdf() -> bytes:
    rows = load_assets() or []
    lines = [f"Total assets: {len(rows)}", ""]
    for r in rows:
        lines.append(
            f"{r.get('asset_id','')} | {r.get('asset_name','')} | "
            f"{r.get('asset_type','')} | {r.get('oem','')} {r.get('model_number','')} "
            f"| criticality={r.get('criticality','')} "
            f"| downtime_cost/hr={r.get('downtime_cost_per_hour','')} "
            f"| status={r.get('status','')}"
        )
    return _simple_pdf("Asset Master Data (full)", lines)


def _personas_pdf() -> bytes:
    data = load_personas() or {}
    lines = []
    approvers = data.get("approvers", {}) or {}
    lines.append("APPROVERS (sign-off authority by tier)")
    lines.append("")
    for tier, p in approvers.items():
        lines.append(
            f"[{tier}] {p.get('role','')} - {p.get('name','')} "
            f"({p.get('id','')})"
        )
        lines.append(f"    level: {p.get('approval_level','')}")
        lines.append(f"    scope: {p.get('scope','')}")
        lines.append(f"    applies when: {p.get('applies_when','')}")
        lines.append("")
    contributors = data.get("contributors", {}) or {}
    lines.append("CONTRIBUTORS (advisory input)")
    lines.append("")
    for domain, c in contributors.items():
        lines.append(
            f"[{domain}] {c.get('role','')} - {c.get('name','')} "
            f"({c.get('id','')})"
        )
        lines.append(f"    concern: {c.get('concern','')}")
        lines.append("")
    return _simple_pdf("Approval Hierarchy (personas)", lines)


def _plain_rationale(text: str) -> str:
    """Strip markdown markers (### , **) so the rationale reads cleanly in the PDF."""
    if not text:
        return ""
    t = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    return t.replace("**", "")


def _build_export_pdf(rec, diag, risk) -> bytes:
    """Exhaustive, printable PDF report covering every section of the recommendation."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    eff_w = pdf.w - pdf.l_margin - pdf.r_margin

    def heading(txt):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(31, 58, 110)
        pdf.cell(0, 7, _pdf_safe(txt), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(180, 190, 210)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + eff_w, pdf.get_y())
        pdf.ln(1.5)
        pdf.set_text_color(0, 0, 0)

    def kv(label, value):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(42, 6, _pdf_safe(label), new_x="RIGHT", new_y="TOP")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(eff_w - 42, 6, _pdf_safe(value), new_x="LMARGIN", new_y="NEXT")

    def body(txt):
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.4, _pdf_safe(txt), new_x="LMARGIN", new_y="NEXT")

    # --- Header ---
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(0, 9, "Maintenance Recommendation", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6,
             _pdf_safe(f"Case {getattr(rec, 'case_id', '?')}   |   "
                       f"Asset {getattr(rec, 'asset_id', '?')}   |   "
                       f"Bearing {getattr(rec, 'bearing_id', '?')}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _pdf_safe(f"Generated: {getattr(rec, 'generated_at_utc', '')}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    # --- Status banner ---
    pdf.ln(2)
    pdf.set_fill_color(235, 238, 245)
    pdf.set_font("Helvetica", "B", 10)
    pdf.multi_cell(
        0, 7,
        _pdf_safe(f"Status: {getattr(rec, 'recommendation_status', '?')}     "
                  f"Approval: {getattr(rec, 'approval_status', '?')}     "
                  f"Urgency: {getattr(rec, 'urgency', '?')}"),
        border=1, fill=True, new_x="LMARGIN", new_y="NEXT",
    )

    # Loud note when this is a tentative AI suggestion (novel scenario, no SOP).
    if getattr(rec, "is_llm_suggested", False) or \
       getattr(rec, "recommendation_status", "") == "novel_llm_suggestion":
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(150, 90, 0)
        pdf.multi_cell(
            0, 6,
            _pdf_safe("** AI-GENERATED SUGGESTION (NOVEL SCENARIO) ** - tentative, "
                      "lower-confidence; not covered by approved procedures. "
                      "Must be validated by a human before any action."),
            new_x="LMARGIN", new_y="NEXT",
        )
        pdf.set_text_color(0, 0, 0)

    # --- Recommended Action ---
    act = getattr(rec, "recommended_action", None)
    heading("Recommended Action")
    kv("Action:", getattr(act, "name", "-") if act else "-")
    kv("Description:", getattr(act, "description", "-") if act else "-")
    kv("Duration:", f"{getattr(act, 'estimated_duration_hours', 0)} h" if act else "-")
    kv("Source:", (rec.evidence or {}).get("recommended_action", "-"))

    # --- Schedule ---
    heading("Schedule")
    if getattr(rec, "urgency", "") == "monitor":
        _timing = "Monitor only - no scheduled slot required"
    elif getattr(rec, "window_chosen", None):
        _timing = "Scheduled within a maintenance window"
    else:
        _timing = "Immediate - no window fits the remaining useful life"
    kv("Timing:", _timing)
    kv("Window:", getattr(rec, "window_chosen", None) or "None")

    # --- Parts ---
    heading("Required Parts")
    parts = getattr(rec, "required_parts", []) or []
    if parts:
        for p in parts:
            _lead = getattr(p, "lead_time_days", 0)
            _avail = "In stock" if _lead == 0 else f"Order - {_lead} day lead time"
            body(f"- {getattr(p, 'part_number', '?')}  (qty {getattr(p, 'quantity', '?')})  -  {_avail}")
    else:
        body("No parts required for this action.")

    # --- Rationale ---
    heading("Rationale")
    body(_plain_rationale(getattr(rec, "rationale", "") or "No rationale provided."))

    # --- Approval & Routing ---
    heading("Approval & Routing")
    kv("Approver (signs off):",
       f"{getattr(rec, 'responsible_approver', '-')}  ({getattr(rec, 'responsible_approver_id', '-')})")
    contribs = getattr(rec, "contributors", []) or []
    if contribs:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "Contributors (input required):", new_x="LMARGIN", new_y="NEXT")
        for c in contribs:
            _r = c.get("role", ""); _n = c.get("name", ""); _cn = c.get("concern", "")
            _who = f"{_r} - {_n}" if _n else _r
            body(f"- {_who}  :  {_cn}")
    else:
        kv("Contributors:", "No additional input required.")

    # --- Evidence & Sources ---
    heading("Evidence & Sources")
    ev = getattr(rec, "evidence", {}) or {}
    if ev:
        for _claim, _source in ev.items():
            kv(f"{_claim}:", _source)
    else:
        body("No evidence recorded.")

    # --- Alternatives Considered ---
    heading("Alternatives Considered")
    alts = getattr(rec, "ranked_alternatives", []) or []
    if alts:
        for a in alts:
            body(f"- {getattr(a, 'name', '?')}: {getattr(a, 'description', '')} "
                 f"({getattr(a, 'estimated_duration_hours', 0)} h)")
    else:
        body("No alternative actions were applicable for this fault and severity.")

    return bytes(pdf.output())


def _recommendation_pdf() -> bytes:
    import streamlit as _st
    rec = _st.session_state.get("rec")
    _ai = _st.session_state.get("active_inputs") or {}
    diag = _ai.get("diagnosis")
    risk = _ai.get("risk")
    return _build_export_pdf(rec, diag, risk)


def _find_sop_file(prefix: str):
    import glob as _glob
    matches = _glob.glob(str(Path("data/sops_and_cases") / f"{prefix}_*.pdf"))
    if not matches:
        matches = _glob.glob(str(Path("data/sops_and_cases") / f"{prefix}*.pdf"))
    return Path(matches[0]) if matches else None


def _sources_from_trace(trace: list, sources_str: str = "", answer_text: str = "") -> list:
    """Return list of dicts: {label, filename, mime, bytes_fn} for download buttons."""
    offered = []
    seen = set()
    _answer_docs = set(re.findall(r'(SOP_\d+|CASE_\d+)', answer_text or ""))
    for t in (trace or []):
        tool = t.get("tool", "")
        result = str(t.get("result", ""))
        if tool == "get_decision_history_tool" and "log" not in seen:
            seen.add("log")
            offered.append({"label": "Decision History Log",
                             "filename": "decision_history.pdf",
                             "mime": "application/pdf",
                             "bytes_fn": _decision_log_pdf})
        elif tool == "check_inventory_tool" and "inv" not in seen:
            seen.add("inv")
            offered.append({"label": "Inventory",
                             "filename": "inventory.pdf",
                             "mime": "application/pdf",
                             "bytes_fn": _inventory_pdf})
        elif tool == "find_windows_tool" and "sch" not in seen:
            seen.add("sch")
            offered.append({"label": "Maintenance Schedule",
                             "filename": "maintenance_schedule.pdf",
                             "mime": "application/pdf",
                             "bytes_fn": _schedule_pdf})
        elif tool == "get_asset_info_tool" and "ast" not in seen:
            seen.add("ast")
            offered.append({"label": "Asset Master Data",
                             "filename": "asset_master.pdf",
                             "mime": "application/pdf",
                             "bytes_fn": _assets_pdf})
        elif tool == "search_documents_tool":
            for fn in re.findall(r'\[([^\]]+?), page \d+\]', result):
                _pref_match = re.match(r'(SOP_\d+|CASE_\d+)', fn)
                _pref = _pref_match.group(1) if _pref_match else None
                if _pref and _pref in _answer_docs and _pref not in seen:
                    _p = Path("data/sops_and_cases") / fn
                    if _p.exists():
                        seen.add(_pref)
                        offered.append({"label": _pref,
                                         "filename": fn,
                                         "mime": "application/pdf",
                                         "bytes_fn": (lambda p=_p: p.read_bytes())})
    _doc_tool_ran = any(t.get("tool") == "search_documents_tool" for t in (trace or []))
    if _doc_tool_ran:
        for prefix in re.findall(r'(SOP_\d+|CASE_\d+)', sources_str or ""):
            if prefix in seen:
                continue
            if prefix not in _answer_docs:
                continue
            p = _find_sop_file(prefix)
            if p and p.exists():
                seen.add(prefix)
                offered.append({"label": prefix,
                                "filename": p.name,
                                "mime": "application/pdf",
                                "bytes_fn": (lambda pp=p: pp.read_bytes())})
    return offered



def _question_intent(question: str) -> list:
    """Keyword routing — returns a LIST of all matching intents (in priority order).

    HIGH-PRIORITY document override: if the question explicitly names a document
    (SOP, CASE, procedure, LOTO, etc.) return ["document"] ALONE — an SOP question
    is a document question even if it also mentions parts or windows.

    Otherwise ALL matching topic intents are collected so a multi-part question
    ("is the part available AND is there a window?") yields multiple buttons.
    Returns an empty list if nothing matches.
    """
    import re as _re
    q = (question or "").lower()

    # HIGH-PRIORITY: explicit document reference → document only, no dilution.
    if (_re.search(r'\bsop[_ ]?\d*', q)          # "sop", "sop 003", "sop_003"
            or _re.search(r'\bcase[_ ]?\d*', q)   # "case", "case_002"
            or "procedure" in q
            or "repair guide" in q
            or "maintenance guide" in q
            or "loto" in q
            or "lockout" in q):
        return ["document"]

    intents = []
    # 1 — Inventory: part / stock queries
    _inv_substrings = ("in stock", "out of stock", "stock", "spare part",
                       "lead time", "part number", "part model", "skf", "brg_",
                       "quantity available", "inventory")
    _inv_wordmatch = _re.search(r'\bparts?\b', q) is not None  # "part"/"parts" whole-word only
    if any(k in q for k in _inv_substrings) or _inv_wordmatch:
        intents.append("inventory")
    # 2 — Schedule: maintenance window / timing
    if any(k in q for k in ("window", "schedule", "when can", "when is the next",
                             "downtime", "upcoming", "next slot", "shift", "timing")):
        intents.append("schedule")
    # 3 — History: past decisions / decision log
    if any(k in q for k in ("history", "past", "previous", "last time", "how often",
                             "prior", "decision log", "what was done", "happened before",
                             "been done before", "earlier decision")):
        intents.append("history")
    # 4 — Asset: asset master data
    if any(k in q for k in ("asset", "criticality", "bottleneck", "downtime cost",
                             "ast_", "machine type", "equipment criticality")):
        intents.append("asset")
    # 5 — Persona / approval hierarchy
    if any(k in q for k in (
            "approver", "approve", "approves", "approval",
            "sign off", "signs off", "sign-off", "signed off",
            "who signs", "who authorizes", "authorize", "authoriser", "authorizer",
            "who should approve", "who needs to approve", "who can approve",
            "responsible for", "who is responsible", "responsible person",
            "approval hierarchy", "sign-off authority", "who approves",
            "shift supervisor", "ops manager", "maintenance engineer",
            "plant manager", "plant supervisor")):
        intents.append("persona")
    # 6 — Document: procedure-style questions without an explicit SOP/CASE reference
    if any(k in q for k in ("sop", "sop_", "case_", "procedure", "how to", "how do",
                             "steps", "loto", "lockout", "torque", "repair guide",
                             "maintenance guide", "safety procedure", "safety step",
                             "safety", "document")):
        if "document" not in intents:
            intents.append("document")
    # 7 — Recommendation: rec-field questions
    if any(k in q for k in ("urgency", "why was", "why chosen", "why is this",
                             "was chosen", "rationale", "how long will", "duration",
                             "alternative", "recommended action",
                             "this recommendation", "the recommendation",
                             "approval status", "recommendation status",
                             "which part", "parts needed", "parts required",
                             "next step")):
        intents.append("recommendation")
    return intents


def _source_for_intent(intent, answer_text=""):
    if intent == "inventory":
        return {"label": "Inventory", "filename": "inventory.pdf",
                "mime": "application/pdf", "bytes_fn": _inventory_pdf}
    if intent == "schedule":
        return {"label": "Maintenance Schedule",
                "filename": "maintenance_schedule.pdf",
                "mime": "application/pdf", "bytes_fn": _schedule_pdf}
    if intent == "history":
        return {"label": "Decision History Log",
                "filename": "decision_history.pdf",
                "mime": "application/pdf", "bytes_fn": _decision_log_pdf}
    if intent == "asset":
        return {"label": "Asset Master Data", "filename": "asset_master.pdf",
                "mime": "application/pdf", "bytes_fn": _assets_pdf}
    if intent == "persona":
        return {"label": "Approval Hierarchy", "filename": "approval_hierarchy.pdf",
                "mime": "application/pdf", "bytes_fn": _personas_pdf}
    if intent == "document":
        for pref in re.findall(r'(SOP_\d+|CASE_\d+)', answer_text or ""):
            p = _find_sop_file(pref)
            if p and p.exists():
                return {"label": pref, "filename": p.name,
                        "mime": "application/pdf",
                        "bytes_fn": (lambda pp=p: pp.read_bytes())}
    if intent == "recommendation":
        return {"label": "Recommendation Report",
                "filename": "recommendation_report.pdf",
                "mime": "application/pdf",
                "bytes_fn": _recommendation_pdf}
    return None


_sp, _cp = st.columns([4, 1])
with _cp:
    with st.popover("\U0001f4ac Ask Agent 6.6", use_container_width=True):
        st.markdown(
            '<div class="chat-header">'
            '<span class="chat-header-title">💬 Ask Agent 6.6</span>'
            '<span class="chat-header-sub">Prescriptive Optimization Agent</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Ask about assets, parts, maintenance windows, "
            "SOPs, past cases, or the current recommendation."
        )
        _hbox = st.container(height=320)
        with _hbox:
            import re as _re_md
            def _md_lite(text: str) -> str:
                t = text
                t = _re_md.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
                t = _re_md.sub(r'`(.+?)`', r'<code>\1</code>', t)
                t = t.replace('\n', '<br>')
                return t
            _ch = st.session_state.get("chat_history", [])
            for _idx, _m in enumerate(_ch):
                if _m["role"] == "user":
                    _bubble_style = (
                        "background:linear-gradient(135deg,rgba(99,102,241,0.92) 0%,rgba(79,70,229,0.92) 100%);"
                        "color:#ffffff;padding:11px 15px;border-radius:16px 16px 5px 16px;"
                        "margin:7px 0 7px auto;max-width:80%;width:fit-content;"
                        "font-size:0.86rem;line-height:1.5;"
                        "backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);"
                        "border:1px solid rgba(165,180,252,0.4);"
                        "box-shadow:0 4px 20px rgba(79,70,229,0.4);"
                    )
                else:
                    _bubble_style = (
                        "background:linear-gradient(135deg,rgba(99,102,241,0.16) 0%,rgba(124,92,246,0.13) 100%);"
                        "color:#e9edff;padding:11px 15px;border-radius:16px 16px 16px 5px;"
                        "margin:7px auto 7px 0;max-width:82%;width:fit-content;"
                        "font-size:0.86rem;line-height:1.55;"
                        "backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);"
                        "border:1px solid rgba(139,124,246,0.55);"
                        "box-shadow:0 4px 22px rgba(99,102,241,0.18);"
                    )
                st.markdown(
                    f'<div style="{_bubble_style}">{_md_lite(_m["content"])}</div>',
                    unsafe_allow_html=True,
                )
                if _m.get("role") == "assistant":
                    _user_q = ""
                    if _idx > 0 and _ch[_idx - 1].get("role") == "user":
                        _user_q = _ch[_idx - 1].get("content", "")
                    _intents = _question_intent(_user_q)

                    _srcs = []
                    _seen_labels = set()
                    # Deterministic: collect one source per matched intent (deduped).
                    for _int in _intents:
                        _isrc = _source_for_intent(_int, _m.get("content", ""))
                        if _isrc and _isrc["label"] not in _seen_labels:
                            _srcs.append(_isrc)
                            _seen_labels.add(_isrc["label"])
                    # Fall back to trace-derived sources (tools the LLM actually called).
                    if not _srcs:
                        _srcs = _sources_from_trace(_m.get("trace", []),
                                                    _m.get("sources", ""),
                                                    _m.get("content", ""))
                    # Last resort: if a recommendation is loaded, offer the rec report.
                    if not _srcs and st.session_state.get("rec") is not None:
                        _rec_src = _source_for_intent("recommendation", _m.get("content", ""))
                        if _rec_src:
                            _srcs = [_rec_src]
                    if _srcs:
                        _cols = st.columns(min(len(_srcs), 3))
                        for _i, _s in enumerate(_srcs):
                            with _cols[_i % len(_cols)]:
                                try:
                                    _data = _s["bytes_fn"]()
                                    st.download_button(
                                        f"📎 {_s['label']}",
                                        data=_data,
                                        file_name=_s["filename"],
                                        mime=_s["mime"],
                                        key=f"dl_src_{_idx}_{_i}",
                                        use_container_width=True,
                                        help="Download this source",
                                    )
                                except Exception:
                                    st.caption(f"📎 {_s['label']}")
                    elif _m.get("sources"):
                        st.markdown(
                            f'<div style="font-size:0.7rem;color:#8fa3c8;'
                            f'font-family:JetBrains Mono,monospace;margin:-2px 0 10px 4px;'
                            f'padding:3px 8px;background:rgba(79,142,255,0.08);'
                            f'border-left:2px solid #4f8eff;border-radius:0 6px 6px 0;'
                            f'display:inline-block;">'
                            f'📎 {_m["sources"]}</div>',
                            unsafe_allow_html=True,
                        )
                if (_m["role"] == "assistant"
                        and _idx == len(_ch) - 1
                        and _m.get("follow_ups")
                        and st.session_state.get("rec") is not None):
                    st.markdown('<div class="followup-chips"></div>',
                                unsafe_allow_html=True)
                    for _fi, _fq in enumerate(_m["follow_ups"]):
                        if st.button(_fq, key=f"followup_{_fi}_{_idx}",
                                     use_container_width=True):
                            st.session_state["_followup_input"] = _fq
                            st.rerun()
        # clear the chat box on the rerun after a send (must run before the widget)
        if st.session_state.get("_clear_chat_input"):
            st.session_state["chat_input_box"] = ""
            st.session_state["_clear_chat_input"] = False
        _q = st.text_input(
            "Ask a question",
            key="chat_input_box",
            placeholder="Ask a question...",
            label_visibility="collapsed",
        )
        _send_msg = None
        if st.button("Send", key="chat_send_btn"):
            if _q and _q.strip():
                _send_msg = _q.strip()
        if "_followup_input" in st.session_state:
            _send_msg = st.session_state.pop("_followup_input")
        if _send_msg:
            # Trust session_state["rec"] directly — it's set on Generate and cleared to
            # None when the scenario is reset. No need to infer from the widget key
            # (which isn't set yet when this handler runs earlier in the file).
            _rec = st.session_state.get("rec")
            _ai  = st.session_state.get("active_inputs") or {} if _rec is not None else {}
            _diag = _ai.get("diagnosis")
            _risk = _ai.get("risk")
            try:
                _history = list(st.session_state.get("chat_history", []))
                _df = st.session_state.get("defaulted_fields", [])
                with st.spinner("Thinking…"):
                    _res = answer_about_recommendation(
                        _send_msg, _rec, _diag, _risk,
                        _history,
                        defaulted_fields=_df,
                    )
                st.session_state["chat_history"].append(
                    {"role": "user", "content": _send_msg}
                )
                st.session_state["chat_history"].append(
                    {"role": "assistant", "content": _res["answer"],
                     "sources": _format_sources(_res.get("trace", []), _rec, _res.get("answer", "")),
                     "follow_ups": _res.get("follow_ups", []),
                     "trace": _res.get("trace", [])}
                )
                if _res.get("trace"):
                    with st.expander("How it worked"):
                        for _t in _res["trace"]:
                            st.markdown(
                                f"**{_t['tool']}** "
                                f"`{_t['args']}` \u2192 "
                                f"{str(_t['result'])[:200]}"
                            )
                st.session_state["_clear_chat_input"] = True
            except Exception as _e:
                st.error(f"Chat error: {_e}")
            st.session_state["_chat_rerun"] = True
            st.rerun()



# ── HTML helper functions ──────────────────────────────────────────────────────

def _pill(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;border-radius:20px;'
        f'padding:3px 14px;font-family:\'JetBrains Mono\',monospace;'
        f'font-size:0.72rem;font-weight:600;letter-spacing:0.07em;'
        f'white-space:nowrap;display:inline-block;">{text}</span>'
    )


def _urgency_color(u: str) -> str:
    return {"monitor": "#2ecc8a", "planned": "#4f8eff",
            "urgent": "#ffb740", "emergency": "#ff4d6a"}.get(u, "#6d85b8")


def _rec_status_color(s: str) -> str:
    if s == "ok":
        return "#2ecc8a"
    if s in ("blocked_no_part", "blocked_unknown_asset", "blocked_invalid_input"):
        return "#ff4d6a"
    return "#ffb740"


def _approval_color(a: str) -> str:
    return {"pending": "#4f8eff", "escalated": "#ff4d6a",
            "approved": "#2ecc8a", "rejected": "#ff4d6a"}.get(a, "#6d85b8")


_CARD = "background:#172038;border-radius:12px;border:1px solid #1e2d4d;padding:1.8rem 2rem;min-height:10rem;box-sizing:border-box;"
_LBL  = "color:#6d85b8;font-family:'JetBrains Mono',monospace;font-size:0.7rem;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:0.5rem;"


def _detail_card(label: str, value: str, sub: str = "",
                 value_color: str = "#e4ecff", sub_color: str = "#6d85b8",
                 caption: str = "", height: str = "") -> str:
    sub_html = (
        f'<div style="color:{sub_color};font-family:\'JetBrains Mono\',monospace;'
        f'font-size:0.82rem;margin-top:0.35rem;overflow-wrap:break-word;">{sub}</div>'
        if sub else ""
    )
    cap_html = (
        f'<div style="color:#3a5080;font-family:\'JetBrains Mono\',monospace;'
        f'font-size:0.63rem;margin-top:0.4rem;line-height:1.6;overflow-wrap:break-word;">'
        f'&#9432;&nbsp;{caption}</div>'
        if caption else ""
    )
    # Use min-height (not a fixed height) so the card keeps a consistent baseline for
    # short content but GROWS to fit long values/captions instead of overflowing.
    h_style = f"min-height:{height};" if height else ""
    return (
        f'<div style="{_CARD}{h_style}vertical-align:top;">'
        f'<div style="{_LBL}">{label}</div>'
        f'<div style="color:{value_color};font-size:1.2rem;font-weight:700;overflow-wrap:break-word;">{value}</div>'
        f'{sub_html}'
        f'{cap_html}'
        f'</div>'
    )


def _format_rationale(text: str) -> str:
    """
    Strip markdown heading/bold markers and render them as bold styled spans.
    Handles both ### HEADER and **HEADER** formats (the LLM may use either).
    Robust: if neither marker is present, text is returned unchanged.
    """
    _HEAD = (
        'display:block;color:#e4ecff;font-weight:700;'
        'font-size:0.93rem;margin-top:1rem;margin-bottom:0.2rem;'
    )
    # Pass 1 — strip ### / ## / # markdown heading markers (any level, whole line)
    formatted = re.sub(
        r'^#{1,6}\s+(.*?)$',
        lambda m: f'<span style="{_HEAD}">{m.group(1)}</span>',
        text,
        flags=re.MULTILINE,
    )
    # Pass 2 — strip **bold** markers (in case the LLM uses those instead)
    formatted = re.sub(
        r'\*\*(.*?)\*\*',
        lambda m: f'<span style="{_HEAD}">{m.group(1)}</span>',
        formatted,
    )
    # Pass 3 — blank lines → paragraph breaks; single newlines → <br>
    formatted = (
        formatted
        .replace("\n\n", "</p><p style='margin:0 0 0.5rem 0;'>")
        .replace("\n", "<br>")
    )
    return formatted


def _src(text: str) -> str:
    """Return a small muted provenance caption, or empty string if text is blank."""
    if not text:
        return ""
    return (
        f'<div style="color:#3a5080;font-family:\'JetBrains Mono\',monospace;'
        f'font-size:0.62rem;margin-top:0.3rem;line-height:1.5;">'
        f'&#9432;&nbsp;{text}</div>'
    )


def _th(text: str) -> str:
    return (
        f'<th style="padding:0.65rem 1rem;text-align:left;color:#6d85b8;'
        f'font-family:\'JetBrains Mono\',monospace;font-size:0.63rem;'
        f'letter-spacing:0.1em;text-transform:uppercase;font-weight:500;">{text}</th>'
    )


def _td(text: str, mono: bool = False) -> str:
    ff = "font-family:'JetBrains Mono',monospace;" if mono else ""
    return (
        f'<td style="padding:0.7rem 1rem;color:#b0c4e8;font-size:0.85rem;{ff}">{text}</td>'
    )




def _build_export_json(rec, diag, risk) -> str:
    """Full recommendation (every field) + the upstream input context, as pretty JSON."""
    payload = {
        "recommendation": rec.model_dump(mode="json"),  # ALL fields, datetimes -> ISO strings
        "input_context": {
            "fault_mode":           getattr(diag, "fault_mode", None),
            "severity":             getattr(diag, "severity", None),
            "diagnosis_confidence": getattr(diag, "confidence", None),
            "rul_min_days":         getattr(risk, "rul_min_days", None),
            "rul_max_days":         getattr(risk, "rul_max_days", None),
            "risk_level":           getattr(risk, "risk_level", None),
            "business_impact_flag": getattr(risk, "business_impact_flag", None),
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ── Sidebar ────────────────────────────────────────────────────────────────────
st.sidebar.markdown("""
<div style="padding:0.4rem 0 0.2rem 0;">
  <div style="font-size:1.15rem;font-weight:700;color:#e4ecff;letter-spacing:0.02em;">Agent 6.6</div>
  <div style="font-size:0.78rem;color:#6d85b8;margin-top:2px;">Prescriptive Optimization &middot; DRO</div>
</div>
""", unsafe_allow_html=True)

st.sidebar.markdown("---")

# Two ways to feed the agent: a built-in demo, or a brand-new scenario typed by hand.
# Stage 1 builds the form + wiring only; a novel fault gracefully hits the catalog_miss
# (or blocked) path for now — the LLM novel-suggestion logic arrives in Stage 2.
input_mode = st.sidebar.radio(
    "Input mode",
    ["Demo scenario", "Add new scenario"],
    label_visibility="collapsed",
    key="input_mode_radio",
)
# Persist the real mode; on a chat-triggered rerun the radio widget can
# reset, so trust the last persisted value instead of the reset widget.
if st.session_state.get("_chat_rerun"):
    # chat rerun: the radio may have been reset — restore the real mode
    input_mode = st.session_state.get("_mode_persist", input_mode)
else:
    # normal interaction: the radio is authoritative — persist it
    st.session_state["_mode_persist"] = input_mode

# Defaults so downstream code never references an undefined name in either mode.
selected_label       = None
generate_clicked     = False
new_generate_clicked = False
custom_inputs        = None

# Representative values the UPSTREAM agents (6.3 / 6.4 / 6.5) would supply once the
# system is integrated. fault_mode and severity are now user-selectable in the form;
# the remaining keys (rul_days, risk_level, downtime_cost, confidence, component)
# are shown read-only as placeholders for the upstream-agent outputs.
_PREFILL = {
    "fault_mode":    "imbalance",          # representative classification (no catalog SOP)
    "severity":      "stage_2",
    "rul_days":      14,
    "risk_level":    "medium",
    "downtime_cost": 8500.0,
    "confidence":    0.7,
    "component":     "bearing_assembly",
}


def _build_custom_inputs(desc, asset_id, fault_mode, severity,
                         rul_days=14, risk_level="medium",
                         confidence=0.7, downtime_cost=8500.0):
    """Build valid FaultDiagnosis / RiskAssessment / KnowledgeGuidance from the
    primary inputs plus the advanced-panel simulation values. Returns the
    (diagnosis, risk, guidance) triple, or None if the asset id is missing."""
    asset_id = (asset_id or "").strip()
    if not asset_id:
        st.sidebar.warning("Please enter an Asset ID to generate a recommendation.")
        return None

    _bearings = load_bearings()
    _match = next((b for b in _bearings if b.get("asset_id") == asset_id), None)
    if _match:
        bearing_id = _match["bearing_id"]
    else:
        bearing_id = f"{asset_id}_BRG"
        st.sidebar.warning(
            "No bearing found for this asset in master data — results may be incomplete."
        )
    desc = (desc or "").strip()

    _fail_by_level = {"low": 0.25, "medium": 0.55, "high": 0.85, "critical": 0.92}
    rul_min = int(rul_days)
    rul_max = rul_min + 5

    diagnosis = FaultDiagnosis(
        case_id="CUSTOM-001", asset_id=asset_id, bearing_id=bearing_id,
        fault_code="FT_CUSTOM", fault_mode=fault_mode,
        affected_component=_PREFILL["component"], severity=severity,
        confidence=float(confidence),
        evidence=[desc] if desc else [],
        diagnosed_at_utc=NOW,
    )
    risk = RiskAssessment(
        case_id="CUSTOM-001", asset_id=asset_id, bearing_id=bearing_id,
        failure_probability=_fail_by_level.get(risk_level, 0.5),
        risk_level=risk_level,
        rul_min_days=rul_min, rul_max_days=rul_max,
        confidence=float(confidence),
        business_impact_flag=False,
        estimated_downtime_cost_per_hour=float(downtime_cost),
        assessed_at_utc=NOW,
    )
    guidance = KnowledgeGuidance(case_id="CUSTOM-001")
    return diagnosis, risk, guidance


if input_mode == "Demo scenario":
    st.sidebar.markdown(
        '<div style="font-size:0.68rem;color:#6d85b8;letter-spacing:0.1em;'
        'text-transform:uppercase;margin-bottom:0.5rem;">Demo Scenario</div>',
        unsafe_allow_html=True,
    )
    _SCENARIO_PLACEHOLDER = "— Select a scenario —"
    if "active_scenario_label" not in st.session_state:
        st.session_state["active_scenario_label"] = _SCENARIO_PLACEHOLDER

    _options = [_SCENARIO_PLACEHOLDER] + SCENARIO_LABELS
    _cur = st.session_state["active_scenario_label"]
    _sel_index = _options.index(_cur) if _cur in _options else 0

    selected_label = st.sidebar.selectbox(
        label="scenario",
        options=_options,
        label_visibility="collapsed",
        index=_sel_index,
        key="scenario_select",
    )
    st.session_state["active_scenario_label"] = selected_label
    # Reset uncertainty when scenario changes
    if "last_scenario" not in st.session_state:
        st.session_state["last_scenario"] = None
    if selected_label != st.session_state["last_scenario"]:
        st.session_state["uncertainty"] = None
        st.session_state["sop_warning"] = None
        st.session_state["last_scenario"] = selected_label
        # if reset to the placeholder, clear any stale recommendation so the
        # chatbot doesn't reference a phantom rec from a previous generation
        if selected_label == _SCENARIO_PLACEHOLDER:
            st.session_state["rec"] = None
            st.session_state["active_inputs"] = {}
            st.session_state["chat_history"] = []
    generate_clicked = st.sidebar.button(
        "Generate Recommendation",
        type="primary",
        use_container_width=True,
    )


# ── Sidebar context: scenario description + input provenance ───────────────────
# Plain-language description per scenario type. Keyed by category; a helper picks
# the category from the scenario so operational cases (unknown asset / unreliable
# signal) win over the underlying fault type. Missing -> "" (renders nothing).
SCENARIO_DESCRIPTIONS = {
    "healthy":    "The machine is operating normally — this is routine preventive maintenance, not a fault response.",
    "outer":      "Damage on the outer ring of the bearing, often from contamination or heavy load. Causes rising vibration; if ignored it leads to bearing failure and unplanned downtime.",
    "inner":      "Damage on the inner (rotating) ring of the bearing. Produces a distinct vibration signature and progresses toward failure if untreated.",
    "lube":       "The bearing isn't properly lubricated (contamination, degradation, or low oil), increasing friction and wear. Correctable with a lubrication service if caught early, but can escalate to bearing replacement.",
    "gearbox":    "A fault in the gearbox bearing/components — high impact because the gearbox is a critical, costly part of the production line.",
    "unreliable": "The sensor data is degraded or unreliable, so the diagnosis can't be trusted — this needs human inspection rather than automated action.",
    "unknown":    "The asset isn't in the master records, so the agent can't reason about it — needs human verification.",
    "validation": "Upstream agents sent inconsistent data — confidence out of range, asset/bearing mismatch, negative RUL. The validator catches all three errors before the pipeline runs and flags them for the orchestrator.",
}


def _scenario_description(scenario: dict) -> str:
    """Pick a plain-language description for a scenario. Operational/special cases
    (unknown asset, unreliable signal) take priority over the underlying fault type."""
    if not scenario:
        return ""
    label = (scenario.get("label", "") or "").lower()
    fault = (getattr(scenario.get("diagnosis"), "fault_mode", "") or "").lower()
    if "validation blocked" in label:
        return SCENARIO_DESCRIPTIONS["validation"]
    if "unknown asset" in label:
        return SCENARIO_DESCRIPTIONS["unknown"]
    if "signal dropout" in label or "unreliable" in label:
        return SCENARIO_DESCRIPTIONS["unreliable"]
    if "gearbox" in label:
        return SCENARIO_DESCRIPTIONS["gearbox"]
    if "healthy" in label or "preventive" in label:
        return SCENARIO_DESCRIPTIONS["healthy"]
    if fault == "outer_race_fault":
        return SCENARIO_DESCRIPTIONS["outer"]
    if fault == "inner_race_fault":
        return SCENARIO_DESCRIPTIONS["inner"]
    if fault == "lubrication_issue":
        return SCENARIO_DESCRIPTIONS["lube"]
    if "partial" in label or "missing" in label or "incomplete" in label:
        return ("Upstream agents provided incomplete or uncertain data. "
                "The pipeline runs on available information and flags "
                "every assumption or uncertainty for human review.")
    if "invalid" in label:
        return ("Upstream agents sent inconsistent data. The validator "
                "catches the errors before the pipeline runs and flags "
                "them for the orchestrator.")
    return ""


def _prov_row(k: str, v: str) -> str:
    """One label:value row for the provenance panel."""
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'gap:0.5rem;padding:0.16rem 0;">'
        f'<span style="color:#7e93bd;font-size:0.7rem;">{k}</span>'
        f'<span style="color:#dce7ff;font-family:\'JetBrains Mono\',monospace;'
        f'font-size:0.7rem;font-weight:500;text-align:right;">{v}</span></div>'
    )


def _prov_group(agent_label: str, color: str, rows: str) -> str:
    """One upstream-agent group box (coloured header + its rows)."""
    return (
        f'<div style="background:linear-gradient(135deg,#111c34,#0c1424);'
        f'border:1px solid #1e2d4d;border-left:3px solid {color};border-radius:10px;'
        f'padding:0.6rem 0.8rem;margin-bottom:0.55rem;box-shadow:0 1px 3px rgba(0,0,0,0.25);">'
        f'<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.45rem;">'
        f'<span style="width:7px;height:7px;border-radius:50%;background:{color};'
        f'box-shadow:0 0 6px {color};display:inline-block;flex-shrink:0;"></span>'
        f'<span style="color:{color};font-family:\'JetBrains Mono\',monospace;font-size:0.6rem;'
        f'letter-spacing:0.07em;text-transform:uppercase;font-weight:600;">{agent_label}</span>'
        f'</div>{rows}</div>'
    )


if input_mode == "Demo scenario":
    # Selected scenario's input objects (resolved when a real scenario is chosen).
    _sel = SCENARIO_MAP.get(selected_label, {})

    # --- ADDITION 2: plain-language scenario description ---
    if selected_label in SCENARIO_MAP:
        _desc = _scenario_description(_sel)
        if _desc:
            st.sidebar.markdown(
                f'<div style="background:linear-gradient(135deg,#1a1130,#0d1628);'
                f'border:1px solid #2c1e52;border-left:3px solid #A100FF;border-radius:12px;'
                f'padding:0.8rem 0.95rem;margin-top:0.9rem;box-shadow:0 2px 10px rgba(161,0,255,0.13);">'
                f'<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.4rem;">'
                f'<span style="font-size:0.82rem;">&#128161;</span>'
                f'<span style="color:#c79bff;font-family:\'JetBrains Mono\',monospace;font-size:0.6rem;'
                f'letter-spacing:0.1em;text-transform:uppercase;font-weight:600;">What this scenario is</span>'
                f'</div>'
                f'<div style="color:#c4d2ee;font-size:0.78rem;line-height:1.55;">{_desc}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # --- ADDITION 1: input provenance grouped by upstream agent ---
    if selected_label in SCENARIO_MAP:
        _d   = _sel.get("diagnosis")
        _r   = _sel.get("risk")
        _g   = _sel.get("guidance")
        _conf = getattr(_d, "confidence", None)
        _conf_str = f"{_conf:.0%}" if isinstance(_conf, (int, float)) else "—"
        _rmin, _rmax = getattr(_r, "rul_min_days", None), getattr(_r, "rul_max_days", None)
        _rul_str = f"{_rmin}–{_rmax} days" if _rmin is not None and _rmax is not None else "—"
        _cost = getattr(_r, "estimated_downtime_cost_per_hour", None)
        _cost_str = f"${_cost:,.0f}/hr" if isinstance(_cost, (int, float)) else "—"
        _g_docs = getattr(_g, "source_documents", []) or []
        _g_secs = getattr(_g, "relevant_sections", []) or []
        _g_ref  = ", ".join(_g_docs) if _g_docs else (", ".join(_g_secs) if _g_secs else "No specific SOP referenced")

        _grp_63 = _prov_group(
            "Fault Diagnosis · Agent 6.3", "#4f8eff",
            _prov_row("Fault mode", ((getattr(_d, "fault_mode", "") or "—").replace("_", " ").title()))
            + _prov_row("Severity", ((getattr(_d, "severity", "") or "—").replace("_", " ").title()))
            + _prov_row("Bearing", (getattr(_d, "bearing_id", "—") or "—"))
            + _prov_row("Confidence", _conf_str),
        )
        _grp_64 = _prov_group(
            "Risk Assessment · Agent 6.4", "#ffb740",
            _prov_row("RUL", _rul_str)
            + _prov_row("Risk level", ((getattr(_r, "risk_level", "") or "—").title()))
            + _prov_row("Downtime cost", _cost_str),
        )
        _grp_65 = _prov_group(
            "Knowledge Guidance · Agent 6.5", "#2ecc8a",
            _prov_row("SOP / ref", _g_ref),
        )

        st.sidebar.markdown(
            f'<div style="margin-top:0.95rem;font-size:0.72rem;">'
            f'<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.6rem;">'
            f'<span style="font-size:0.78rem;">&#128279;</span>'
            f'<span style="color:#8ea2cc;font-family:\'JetBrains Mono\',monospace;font-size:0.62rem;'
            f'letter-spacing:0.1em;text-transform:uppercase;font-weight:600;">'
            f'Inputs — where this comes from</span>'
            f'</div>'
            f'{_grp_63}{_grp_64}{_grp_65}'
            f'</div>',
            unsafe_allow_html=True,
        )

else:
    # ── Add-new-scenario form (collect inputs -> build schema objects) ──
    st.sidebar.markdown(
        '<div style="font-size:0.68rem;color:#6d85b8;letter-spacing:0.1em;'
        'text-transform:uppercase;margin-bottom:0.3rem;">Add New Scenario</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        '<div style="font-size:0.72rem;color:#6d85b8;line-height:1.5;margin-bottom:0.6rem;">'
        'Describe the fault and select the asset. The agent classifies the fault mode '
        'from your description. Adjust upstream simulation values in Advanced below.</div>',
        unsafe_allow_html=True,
    )
    _f_desc = st.sidebar.text_area(
        "Fault description",
        placeholder="e.g. unusual high-frequency vibration on pump",
    )
    try:
        _asset_records = load_assets() or []
    except Exception:
        _asset_records = []
    if not _asset_records:
        _asset_records = [
            {"asset_id": "AST_MTR_001", "asset_name": "Conveyor Motor A"},
            {"asset_id": "AST_PMP_001", "asset_name": "Cooling Pump A"},
            {"asset_id": "AST_CON_001", "asset_name": "Conveyor Assembly"},
            {"asset_id": "AST_GBX_001", "asset_name": "Gearbox Unit"},
        ]
    _asset_ids   = [a.get("asset_id") for a in _asset_records if a.get("asset_id")]
    _asset_label = {
        a.get("asset_id"): f'{a.get("asset_name", a.get("asset_id"))} ({a.get("asset_id")})'
        for a in _asset_records if a.get("asset_id")
    }
    _f_asset = st.sidebar.selectbox(
        "Asset ID",
        _asset_ids,
        index=0,
        format_func=lambda _aid: _asset_label.get(_aid, _aid),
    )

    with st.sidebar.expander("⚙ Advanced: simulate upstream values", expanded=False):
        _f_severity = st.selectbox(
            "Severity",
            options=["stage_1", "stage_2", "stage_3"],
            index=1,
        )
        _f_rul_days = st.number_input(
            "RUL min days", min_value=1, max_value=365, value=14, step=1,
        )
        _f_risk_level = st.selectbox(
            "Risk level",
            options=["low", "medium", "high", "critical"],
            index=1,
        )
        _f_confidence = st.slider(
            "Confidence", min_value=0.0, max_value=1.0, value=0.7, step=0.05,
        )
        _f_downtime_cost = st.number_input(
            "Downtime cost ($/hr)", min_value=0, value=8500, step=500,
        )
        st.caption(
            "In production these come from upstream agents 6.3/6.4. "
            "Adjust here to simulate different upstream conditions for demo."
        )

    new_generate_clicked = st.sidebar.button(
        "Generate Recommendation",
        type="primary",
        use_container_width=True,
        key="gen_new",
    )
    # PHASE 1: Generate clicked -> classify and ask for confirmation (do NOT generate yet)
    if new_generate_clicked:
        if not (_f_desc and _f_desc.strip()):
            st.sidebar.warning(
                "Please enter a fault description so the agent can classify it."
            )
        elif not _is_meaningful_description(_f_desc):
            st.sidebar.warning(
                "Please describe the fault in a few words — what's happening, "
                "where, and any symptoms (e.g. 'grinding noise from the motor bearing'). "
                "The description entered doesn't contain enough detail to classify."
            )
        else:
            with st.spinner("Analyzing fault and generating recommendation…"):
                _cls_result = classify_fault(_f_desc)
                if _cls_result.get("fault_mode") == "unknown":
                    from agents.fault_classifier import guess_unknown_fault
                    st.session_state["unknown_guess"] = guess_unknown_fault(_f_desc.strip())
                    st.session_state["unknown_fault_confirmed"] = False
                else:
                    st.session_state["unknown_guess"] = None
                    st.session_state["unknown_fault_confirmed"] = False
            st.session_state["classified_fault"] = _cls_result
            st.session_state["awaiting_confirmation"] = True
            st.session_state["fault_confirmed"] = False
            # remember the description used, so a later edit invalidates confirmation
            st.session_state["confirmed_desc"] = _f_desc.strip()

    # PHASE 2: show Yes/No confirmation in the sidebar when awaiting
    custom_inputs = None
    if st.session_state.get("awaiting_confirmation"):
        _pending_cls = st.session_state.get("classified_fault")
        if _pending_cls:
            if _pending_cls.get("fault_mode") == "unknown":
                _guess = st.session_state.get("unknown_guess") or {}
                st.sidebar.warning(
                    "This doesn't match a standard fault. AI's best interpretation:\n\n"
                    f"**{_guess.get('likely_fault','Unrecognized fault')}**\n\n"
                    f"{_guess.get('explanation','')}\n\nIs this correct?"
                )
                _uc1, _uc2 = st.sidebar.columns(2)
                with _uc1:
                    _uyes = st.button("✓ Yes, that's it", key="uconfirm_yes",
                                      use_container_width=True)
                with _uc2:
                    _uno = st.button("✗ No, rephrase", key="uconfirm_no",
                                     use_container_width=True)
                if _uyes:
                    st.session_state["unknown_fault_confirmed"] = True
                    st.session_state["fault_confirmed"] = True
                    st.session_state["awaiting_confirmation"] = False
                if _uno:
                    st.session_state["awaiting_confirmation"] = False
                    st.session_state["fault_confirmed"] = False
                    st.session_state["classified_fault"] = None
                    st.session_state["unknown_guess"] = None
                    st.rerun()
            else:
                _mode_raw = _pending_cls.get("fault_mode", "unknown")
                _mode_pretty = _mode_raw.replace("_", " ")
                _reason = (_pending_cls.get("reason") or "").strip()
                _msg = f"Interpreted as: **{_mode_pretty}**"
                if _reason:
                    _msg += f"\n\n{_reason}"
                _msg += "\n\nProceed?"
                st.sidebar.info(_msg)
                _cc1, _cc2 = st.sidebar.columns(2)
                with _cc1:
                    _yes = st.button("✓ Yes, proceed", key="confirm_yes",
                                     use_container_width=True)
                with _cc2:
                    _no = st.button("✗ No, rephrase", key="confirm_no",
                                    use_container_width=True)
                if _yes:
                    st.session_state["fault_confirmed"] = True
                    st.session_state["awaiting_confirmation"] = False
                if _no:
                    st.session_state["awaiting_confirmation"] = False
                    st.session_state["fault_confirmed"] = False
                    st.session_state["classified_fault"] = None
                    st.rerun()

    # PHASE 3: once confirmed, build the inputs so the run gate can fire
    if (st.session_state.get("fault_confirmed")
            and st.session_state.get("classified_fault")
            and _f_desc and _f_desc.strip()):
        _cls_result = st.session_state["classified_fault"]
        custom_inputs = _build_custom_inputs(
            _f_desc, _f_asset, _cls_result["fault_mode"],
            _f_severity, _f_rul_days, _f_risk_level,
            _f_confidence, _f_downtime_cost,
        )


# ── Browsable catalog of ALL scenarios (collapsed by default; no clutter) ──────
# Reuses SCENARIO_DESCRIPTIONS so the wording stays consistent with the selected-
# scenario card. The "Add new scenario" row is the manual-entry mode, not a fault.
_SCENARIO_CATALOG = [
    ("Healthy / preventive", SCENARIO_DESCRIPTIONS["healthy"]),
    ("Outer race fault",     SCENARIO_DESCRIPTIONS["outer"]),
    ("Inner race fault",     SCENARIO_DESCRIPTIONS["inner"]),
    ("Lubrication issue",    SCENARIO_DESCRIPTIONS["lube"]),
    ("Gearbox fault",        SCENARIO_DESCRIPTIONS["gearbox"]),
    ("Unreliable diagnosis", SCENARIO_DESCRIPTIONS["unreliable"]),
    ("Unknown asset",        SCENARIO_DESCRIPTIONS["unknown"]),
    ("Add new scenario",     "Enter a novel fault manually — handled with an AI-generated, "
                             "human-validated suggestion when no approved procedure exists."),
]

with st.sidebar.expander("\U0001F4CB  View all scenarios", expanded=False):
    _cat_rows = ""
    for _name, _desc in _SCENARIO_CATALOG:
        _cat_rows += (
            f'<div style="padding:0.55rem 0;border-bottom:1px solid #16223c;">'
            f'<div style="color:#4f8eff;font-weight:600;font-size:0.78rem;'
            f'margin-bottom:0.2rem;">{_name}</div>'
            f'<div style="color:#8ea2cc;font-size:0.72rem;line-height:1.45;">{_desc}</div>'
            f'</div>'
        )
    st.markdown(f'<div>{_cat_rows}</div>', unsafe_allow_html=True)


# ── Browsable reference of ALL personas (collapsed by default) ─────────────────
# Read from load_personas() so it stays in sync with the data file (no hardcoding).
# Two groups: approvers (sign-off hierarchy by severity) and contributors (by condition).
_personas = load_personas()

# Short "when" note per approver tier, derived from the key.
_TIER_WHEN = {
    "normal":    "normal recommendations",
    "escalated": "escalated / high-impact cases",
    "executive": "executive tier (reserved)",
}


def _persona_row(role_name: str, note: str, accent: str) -> str:
    """One persona row: role + name in accent, the note/concern muted beneath."""
    return (
        f'<div style="padding:0.45rem 0;border-bottom:1px solid #16223c;">'
        f'<div style="color:{accent};font-weight:600;font-size:0.75rem;'
        f'margin-bottom:0.15rem;">{role_name}</div>'
        f'<div style="color:#8ea2cc;font-size:0.7rem;line-height:1.4;">{note}</div>'
        f'</div>'
    )


def _group_heading(text: str) -> str:
    return (
        f'<div style="color:#6d85b8;font-family:\'JetBrains Mono\',monospace;font-size:0.62rem;'
        f'letter-spacing:0.1em;text-transform:uppercase;margin:0.6rem 0 0.3rem 0;">{text}</div>'
    )

with st.sidebar.expander("\U0001F465  View all personas", expanded=False):
    _approvers    = _personas.get("approvers", {})
    _contributors = _personas.get("contributors", {})

    _html = (
        '<div style="color:#8ea2cc;font-size:0.68rem;line-height:1.45;margin-bottom:0.4rem;">'
        'Approvers sign off (by severity); contributors provide input when their condition '
        'applies.</div>'
    )

    # APPROVERS — ordered tiers; safe .get so a missing tier just doesn't render.
    _html += _group_heading("Approvers · sign-off hierarchy")
    for _tier in ("normal", "escalated", "executive"):
        _appr = _approvers.get(_tier)
        if not _appr:
            continue
        _role = _appr.get("role", "")
        _name = _appr.get("name", "")
        _label = f"{_role} — {_name}" if _name else _role
        _html += _persona_row(_label, _TIER_WHEN.get(_tier, _tier), "#4f8eff")

    # CONTRIBUTORS — iterate the data; show each one's concern.
    _html += _group_heading("Contributors · input by condition")
    for _c in _contributors.values():
        _role = _c.get("role", "")
        _name = _c.get("name", "")
        _label = f"{_role} — {_name}" if _name else _role
        _concern = _c.get("concern", "")
        _html += _persona_row(_label, _concern, "#2ecc8a")

    st.markdown(f'<div>{_html}</div>', unsafe_allow_html=True)


st.sidebar.markdown("---")
st.sidebar.markdown(
    '<div style="font-size:0.68rem;color:#2a3a5c;text-align:center;line-height:1.7;">'
    'Downtime Response Orchestrator<br>Agent 6 of 8</div>',
    unsafe_allow_html=True,
)

# Clear stale result when the active input changes (a different demo scenario, or a
# switch between demo mode and add-new-scenario mode).
_active_key = (selected_label if input_mode == "Demo scenario"
               else "__new_scenario__")

# Only clear rec when scenario genuinely changed AND
# a recommendation exists for the OLD scenario
if (st.session_state.get("last_active_key") is not None and
        st.session_state.get("last_active_key") != _active_key and
        not st.session_state.get("_chat_rerun", False)):
    st.session_state.pop("rec", None)
    st.session_state.pop("approval", None)
    st.session_state["chat_history"] = []

# ALWAYS update last_active_key and reset chat_rerun flag
st.session_state["last_active_key"] = _active_key
st.session_state["_chat_rerun"] = False

# ── DRO pipeline bar ───────────────────────────────────────────────────────────
# A compact one-row strip showing all 8 agents in the Downtime Response Orchestrator,
# so a viewer sees this agent is ONE node in a larger chain. Node 6 (mine) is
# highlighted with a purple accent + glowing neon dot; the other 7 are muted.
_PIPELINE  = ["Data", "Monitoring", "Failure", "Predictive",
              "Knowledge", "Prescriptive", "Executor", "Learning"]
_MINE_IDX  = 5   # 0-based index of MY agent (Prescriptive)

# Build 8 equal segments that sit together inside ONE rounded strip. Thin border-right
# dividers separate them (no gaps) so it reads as a single connected pipeline. Only the
# "Prescriptive" segment is highlighted (accent fill + glow + neon dot); rest are muted.
_segs = ""
for _i, _name in enumerate(_PIPELINE):
    _divider = "border-right:1px solid #1a2840;" if _i < len(_PIPELINE) - 1 else ""
    if _i == _MINE_IDX:
        _segs += (
            f'<div style="flex:1 1 0;display:flex;align-items:center;justify-content:center;'
            f'gap:0.3rem;padding:0.42rem 0.2rem;{_divider}'
            f'background:linear-gradient(135deg,#241640,#1a1238);'
            f'box-shadow:inset 0 0 12px rgba(161,0,255,0.35);">'
            f'<span style="width:6px;height:6px;border-radius:50%;background:#c46bff;'
            f'box-shadow:0 0 7px #A100FF, 0 0 3px #c46bff;display:inline-block;flex-shrink:0;"></span>'
            f'<span style="color:#ecdffb;font-weight:700;font-size:0.64rem;'
            f'font-family:\'Space Grotesk\',sans-serif;">{_name}</span></div>'
        )
    else:
        _segs += (
            f'<div style="flex:1 1 0;display:flex;align-items:center;justify-content:center;'
            f'padding:0.42rem 0.2rem;{_divider}">'
            f'<span style="color:#6d85b8;font-size:0.64rem;">{_name}</span></div>'
        )

st.markdown(
    f'<div style="display:flex;align-items:stretch;width:100%;background:#0d1628;'
    f'border:1px solid #1e2d4d;border-radius:8px;overflow:hidden;margin:0.1rem 0 0.2rem 0;">'
    f'{_segs}</div>'
    f'<div style="color:#5a6f9e;font-size:0.58rem;font-family:\'JetBrains Mono\',monospace;'
    f'letter-spacing:0.08em;margin:0.2rem 0 0.8rem 0;">Prescriptive Optimisation &middot; Agent 6 of 8</div>',
    unsafe_allow_html=True,
)


# ── Page header ───────────────────────────────────────────────────────────────
st.markdown("""
<div style="padding:0.25rem 0 1.5rem 0;border-bottom:1px solid #1e2d4d;margin-bottom:1.75rem;">
  <div style="font-size:0.68rem;font-weight:500;color:#4f8eff;letter-spacing:0.15em;
          text-transform:uppercase;margin-bottom:0.4rem;">
Downtime Response Orchestrator &nbsp;&middot;&nbsp; Agent 6.6
  </div>
  <div style="font-size:1.95rem;font-weight:700;color:#e4ecff;line-height:1.15;margin-bottom:0.35rem;">
Prescriptive Maintenance Recommendation
  </div>
  <div style="font-size:0.87rem;color:#6d85b8;max-width:720px;">
Receives fault diagnosis, risk assessment, and knowledge guidance.
Produces a ranked maintenance recommendation with urgency, parts, and scheduling for Agent&nbsp;6.7 (Executor).
  </div>
</div>
""", unsafe_allow_html=True)


# ── Run agent ──────────────────────────────────────────────────────────────────
# Resolve which inputs to run: a chosen demo scenario, or the manual form inputs.
_pending_inputs = None
if input_mode == "Demo scenario" and generate_clicked:
    if selected_label not in SCENARIO_MAP:
        st.sidebar.warning("Please select a scenario first.")
    else:
        st.session_state["classified_fault"] = None
        _scn = SCENARIO_MAP[selected_label]
        _pending_inputs = (_scn["diagnosis"], _scn["risk"], _scn["guidance"])
elif (input_mode == "Add new scenario"
      and st.session_state.get("fault_confirmed")
      and custom_inputs):
    if st.session_state.get("unknown_fault_confirmed"):
        # confirmed-unknown fault → free LLM recommendation, bypass normal pipeline
        from agents.prescriptive_optimization_agent import make_free_recommendation
        _guess = st.session_state.get("unknown_guess") or {}
        _diag, _risk, _guid = custom_inputs
        st.session_state["_free_rec_request"] = (_diag, _risk, _guid,
            _guess.get("likely_fault", "Unrecognized fault"))
        _pending_inputs = None
    else:
        _pending_inputs = custom_inputs
    st.session_state["fault_confirmed"] = False
    st.session_state["unknown_fault_confirmed"] = False

if st.session_state.get("_free_rec_request"):
    from agents.prescriptive_optimization_agent import make_free_recommendation
    _d, _r, _g, _lf = st.session_state.pop("_free_rec_request")
    with st.spinner("Interpreting the fault and preparing an AI suggestion…"):
        rec = make_free_recommendation(_d, _r, _g, _lf)
    st.session_state["rec"] = rec
    st.session_state["approval"] = rec.approval_status
    st.session_state["chat_history"] = []
    st.session_state["active_inputs"] = {"diagnosis": _d, "risk": _r, "guidance": _g}
    st.session_state["uncertainty"] = None
    st.session_state["defaulted_fields"] = []
    st.session_state["last_generated_key"] = _active_key
    st.rerun()

if _pending_inputs:
    st.session_state["uncertainty"] = None
    st.session_state["sop_warning"] = None
    try:
        with st.spinner("Running prescriptive optimization agent..."):
            from agents.uncertainty_detector import apply_safe_defaults
            _diag, _risk, _defaulted = apply_safe_defaults(
                _pending_inputs[0], _pending_inputs[1])
            _pending_inputs = (_diag, _risk, _pending_inputs[2])
            rec = recommend_action(*_pending_inputs)
        from agents.decision_logger import log_recommendation
        _log_id = log_recommendation(rec, _pending_inputs[0], _pending_inputs[1])
        from agents.uncertainty_detector import detect_uncertainty
        _uncertainty = detect_uncertainty(
            _pending_inputs[0], _pending_inputs[1],
            defaulted_fields=_defaulted)
        st.session_state["uncertainty"] = _uncertainty
        st.session_state["defaulted_fields"] = _defaulted
        st.session_state["rec"]           = rec
        st.session_state["approval"]      = rec.approval_status
        st.session_state["chat_history"]  = []   # reset chat for new recommendation
        st.session_state["last_generated_key"] = _active_key
        # Keep the exact inputs used, so the Evidence tab + exports work for BOTH
        # demo scenarios and manually-entered ones (no SCENARIO_MAP lookup needed).
        st.session_state["active_inputs"] = {
            "diagnosis": _pending_inputs[0],
            "risk":      _pending_inputs[1],
            "guidance":  _pending_inputs[2],
        }
        st.rerun()
    except Exception as e:
        st.error(f"Agent error — {type(e).__name__}: {e}")
        st.stop()


if not st.session_state.get("rec"):
    st.markdown("""
<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;
            padding:3rem 2rem;text-align:center;margin-top:0.5rem;">
  <div style="font-size:2.2rem;color:#4f8eff;margin-bottom:0.8rem;">&#9881;</div>
  <div style="font-size:1rem;font-weight:600;color:#e4ecff;margin-bottom:0.4rem;">
    No recommendation generated yet
  </div>
  <div style="font-size:0.85rem;color:#6d85b8;">
    Select a scenario in the sidebar and click
    <strong style="color:#4f8eff;">Generate Recommendation</strong>.
  </div>
</div>
""", unsafe_allow_html=True)
    st.stop()

rec              = st.session_state["rec"]
current_approval = st.session_state.get("approval", rec.approval_status)
is_failure       = rec.recommendation_status in _FAILURE_STATUSES
# Approver = the single sign-off authority (new persona model). Fall back to the
# legacy responsible_person field if an older recommendation object is loaded.
person           = getattr(rec, "responsible_approver", "") or rec.responsible_person
person_id        = getattr(rec, "responsible_approver_id", "") or (rec.responsible_person_id or "")
rec_color        = _rec_status_color(rec.recommendation_status)
appr_color       = _approval_color(current_approval)
urg_color        = _urgency_color(rec.urgency)
# Novel AI suggestion? (safe access — older recommendations may lack the flag.)
is_llm           = bool(getattr(rec, "is_llm_suggested", False)) or \
                   rec.recommendation_status == "novel_llm_suggestion"
_ai_tag          = _pill("AI SUGGESTION", "#A100FF") if is_llm else ""


# ── Novel AI-suggestion banner (only for LLM-proposed novel scenarios) ─────────
# Deliberately amber/purple warning styling — visually distinct from any normal
# (green/blue) status so a tentative AI suggestion is never mistaken for a
# validated, rule-backed recommendation.
if is_llm:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#2a1e0a,#1a1130);'
        'border:1px solid #ffb74055;border-left:4px solid #ffb740;border-radius:12px;'
        'padding:1.1rem 1.4rem;margin-bottom:1.25rem;box-shadow:0 2px 12px rgba(255,183,64,0.12);">'
        '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.4rem;">'
        '<span style="font-size:1.15rem;">&#9888;</span>'
        '<span style="color:#ffb740;font-weight:700;font-size:1rem;'
        'font-family:\'Space Grotesk\',sans-serif;">AI-Generated Suggestion — Novel Scenario</span>'
        '</div>'
        '<div style="color:#e0cba8;font-size:0.86rem;line-height:1.6;">'
        'This fault is <b>not covered by approved procedures</b>. The recommendation below is a '
        '<b>tentative, lower-confidence</b> suggestion generated by AI and '
        '<b>MUST be validated by a human before any action is taken</b>.</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Validation-blocked card ────────────────────────────────────────────────────
if rec.recommendation_status == "blocked_invalid_input":
    _val_text = rec.recommended_action.description or ""
    # Errors are embedded in action_desc as "  • <text>" lines — extract each bullet.
    _val_errors = [
        line.strip().lstrip("•").strip()
        for line in _val_text.splitlines()
        if line.strip().startswith("•")
    ]
    _BOX_ERR = (
        "background:#0d1628;border-radius:8px;border-left:3px solid #e85d4a;"
        "padding:0.6rem 0.9rem;color:#e4a0a0;font-size:0.85rem;"
        "font-family:'JetBrains Mono',monospace;"
    )
    _BOX_MSG = (
        "background:#0d1628;border-radius:8px;border-left:3px solid #4f8eff;"
        "padding:0.6rem 0.9rem;color:#b0c4e8;font-size:0.85rem;"
    )
    _err_rows = "".join(
        f'<div style="{_BOX_ERR}">{e}</div>'
        for e in _val_errors
    ) or f'<div style="{_BOX_ERR}">{_val_text}</div>'
    st.markdown(
        f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
        f'border-top:3px solid #e85d4a;padding:1.5rem 2rem 1rem 2rem;margin-bottom:1rem;">'
        f'<div style="{_LBL}">&#9888; INPUT VALIDATION FAILED — PIPELINE DID NOT RUN</div>'
        f'<div style="color:#e4ecff;font-size:0.88rem;font-weight:600;margin-bottom:0.6rem;">'
        f'Errors detected in upstream data:</div>'
        f'<div style="display:flex;flex-direction:column;gap:0.55rem;margin-bottom:0.85rem;">'
        f'{_err_rows}'
        f'</div>'
        f'<div style="{_BOX_MSG}">'
        f'These errors must be corrected by the upstream agents before '
        f'Agent 6.6 can generate a recommendation. '
        f'This flag has been returned to the orchestrator.'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.stop()


# ── Status badge row ───────────────────────────────────────────────────────────
st.markdown(
    f'<div style="display:flex;gap:0.7rem;align-items:center;'
    f'margin-bottom:1.5rem;flex-wrap:wrap;">'
    f'<span style="color:#3a5080;font-family:\'JetBrains Mono\',monospace;'
    f'font-size:0.68rem;letter-spacing:0.1em;">STATUS</span>'
    f'{_pill(rec.recommendation_status.replace("_"," ").upper(), rec_color)}'
    f'{_pill(current_approval.upper(), appr_color)}'
    f'<span style="color:#3a5080;font-family:\'JetBrains Mono\',monospace;'
    f'font-size:0.68rem;letter-spacing:0.06em;">CASE&nbsp;{rec.case_id}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

_cls = st.session_state.get("classified_fault")
if _cls and not st.session_state.get("awaiting_confirmation"):
    _msg = (f"🔍 Interpreted your description as "
            f"**{_cls['fault_mode']}**. {_cls['reason']}")
    if _cls.get("alternatives"):
        _msg += (f" (Other possibilities considered: "
                 f"{', '.join(_cls['alternatives'])}.)")
    st.info(_msg)

_displayed_case = st.session_state.get("rec").case_id if st.session_state.get("rec") else None
_current_key = st.session_state.get("last_active_key")
_last_generated_key = st.session_state.get("last_generated_key")

if (_displayed_case and _last_generated_key and
        _current_key != _last_generated_key):
    st.warning(
        f"⚠ The recommendation below is for a DIFFERENT scenario "
        f"({_displayed_case}). You've selected '{selected_label}' but "
        f"haven't generated it yet. Click **Generate Recommendation** "
        f"to update, or your chat questions will be about the OLD "
        f"recommendation."
    )


# ── Partial-data / uncertainty banner ─────────────────────────────────────────
_unc = st.session_state.get("uncertainty")
_rec_exists = st.session_state.get("rec") is not None
# Only show partial banner for scenarios designed to test it,
# or for custom scenarios. Not for standard demo scenarios.
_current_label = st.session_state.get("last_scenario", "")
_is_partial_scenario = (
    "A6" in _current_label or
    "A7" in _current_label or
    st.session_state.get("input_mode") == "Add new scenario"
)
if _unc and _unc.is_partial and _rec_exists and _is_partial_scenario:
    st.markdown(
        '<div style="background:#f59e0b18;border:1px solid #f59e0b44;'
        'border-left:4px solid #f59e0b;border-radius:8px;'
        'padding:0.75rem 1.25rem;margin-bottom:1rem;">'
        '<span style="color:#f59e0b;font-weight:700;font-size:0.85rem;'
        'letter-spacing:0.05em;">&#9888; PARTIAL DATA</span>'
        '<span style="color:#cbd5e1;font-size:0.82rem;margin-left:8px;">'
        'Recommendation based on incomplete or uncertain information — '
        'pipeline ran on available data but confidence is limited.'
        '</span></div>',
        unsafe_allow_html=True,
    )
    with st.expander("View uncertainty details", expanded=False):
        for i, flag in enumerate(_unc.flags, 1):
            st.markdown(
                f'<div style="background:#1e2a3a;border-left:3px solid '
                f'#f59e0b;padding:0.6rem 1rem;margin-bottom:0.5rem;'
                f'border-radius:0 6px 6px 0;font-size:0.83rem;'
                f'color:#cbd5e1;">'
                f'<strong style="color:#f59e0b;">{i}.</strong> {flag}'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.caption(
            "The pipeline recommendation above is the best available "
            "with current data. Resolve the flagged uncertainties for "
            "higher confidence."
        )


# ── Recommendation card ────────────────────────────────────────────────────────
action_display = rec.recommended_action.name.replace("_", " ").title()
dur_h          = rec.recommended_action.estimated_duration_hours
dur_display    = f"{dur_h}h" if dur_h else "—"
desc_text      = rec.recommended_action.description

st.markdown(
    f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
    f'border-left:4px solid {urg_color};padding:1.75rem 2rem;margin-bottom:1.25rem;">'
    f'<div style="{_LBL}">Recommended Action</div>'
    f'<div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:0.65rem;">'
    f'<span style="font-size:1.75rem;font-weight:700;color:#e4ecff;">{action_display}</span>'
    f'{_pill(rec.urgency.upper(), urg_color)}'
    f'{_ai_tag}'
    f'</div>'
    f'<div style="color:#9ab0d0;font-size:0.9rem;line-height:1.6;margin-bottom:0.35rem;">{desc_text}</div>'
    f'{_src(rec.evidence.get("recommended_action", ""))}'
    f'<div style="display:flex;gap:2rem;flex-wrap:wrap;margin-top:0.8rem;">'
    f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:0.78rem;">'
    f'<span style="color:#6d85b8;">ASSET&nbsp;</span>'
    f'<span style="color:#e4ecff;">{rec.asset_id}</span></span>'
    f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:0.78rem;">'
    f'<span style="color:#6d85b8;">BEARING&nbsp;</span>'
    f'<span style="color:#e4ecff;">{rec.bearing_id}</span></span>'
    f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:0.78rem;">'
    f'<span style="color:#6d85b8;">DURATION&nbsp;</span>'
    f'<span style="color:#e4ecff;">{dur_display}</span></span>'
    f'</div>'
    f'{_src(rec.evidence.get("urgency", ""))}'
    f'</div>',
    unsafe_allow_html=True,
)


# ── Key details row (4 cards) ─────────────────────────────────────────────────
if rec.window_chosen:
    win      = _get_window(rec.window_chosen)
    win_val  = rec.window_chosen
    win_sub  = win.get("start_utc", "")[:10] if win else ""
elif rec.urgency == "monitor":
    win_val, win_sub = "Monitor only", "No slot required"
else:
    win_val, win_sub = "Immediate", "No window fits RUL"

_ROW_H = "17rem"   # min-height floor for the 4 detail cards (they grow taller if content needs it)

dc1, dc2, dc3, dc4 = st.columns(4)
with dc1:
    st.markdown(
        _detail_card("Scheduled Window", win_val, win_sub,
                     caption=rec.evidence.get("window", ""), height=_ROW_H),
        unsafe_allow_html=True,
    )
with dc2:
    st.markdown(
        _detail_card("Approver (Signs Off)", person, person_id,
                     caption=rec.evidence.get("responsible_person", ""), height=_ROW_H),
        unsafe_allow_html=True,
    )
with dc3:
    st.markdown(
        _detail_card(
            "Approval Status",
            current_approval.replace("_", " ").title(),
            value_color=appr_color,
            height=_ROW_H,
        ),
        unsafe_allow_html=True,
    )
with dc4:
    st.markdown(
        _detail_card("Est. Duration", dur_display, height=_ROW_H),
        unsafe_allow_html=True,
    )

st.markdown("<div style='margin-bottom:1.25rem;'></div>", unsafe_allow_html=True)


# ── Rationale card ─────────────────────────────────────────────────────────────
rationale_html = _format_rationale(rec.rationale)

st.markdown(
    f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
    f'border-left:4px solid #4f8eff;padding:1.6rem 2rem;margin-bottom:1.5rem;">'
    f'<div style="{_LBL}">Why This Recommendation</div>'
    f'<div style="color:#c0d4ee;font-size:0.9rem;line-height:1.8;">'
    f'<p style="margin:0 0 0.6rem 0;">{rationale_html}</p>'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)


# ── Blocked-part: alternative actions + interim holding strategy ───────────────
if rec.recommendation_status == "blocked_no_part":
    _bp_part    = rec.required_parts[0].part_number if rec.required_parts else "unknown"
    _bp_lead    = rec.required_parts[0].lead_time_days if rec.required_parts else "—"
    _bp_ev_part = rec.evidence.get("part", "")

    _bp_approver = rec.responsible_approver
    _bp_rebook   = (_bp_lead + 1) if isinstance(_bp_lead, int) else "—"
    _BOX_AMB = (
        "background:#0d1628;border-radius:8px;border-left:3px solid #e8a84a;"
        "padding:0.6rem 0.9rem;color:#b0c4e8;font-size:0.85rem;"
    )
    _BOX_RED = (
        "background:#0d1628;border-radius:8px;border-left:3px solid #e85d4a;"
        "padding:0.6rem 0.9rem;color:#b0c4e8;font-size:0.85rem;"
    )

    st.markdown(
        f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
        f'border-top:3px solid #e85d4a;padding:1.5rem 2rem 1rem 2rem;margin-bottom:1rem;">'
        f'<div style="{_LBL}">Alternative Actions — Part Unavailable</div>'
        f'<div style="color:#b0c4e8;font-size:0.87rem;line-height:1.7;margin-bottom:0.8rem;">'
        f'<div>Intended action: <span style="color:#e4ecff;font-weight:600;">'
        f'{rec.recommended_action.name}</span></div>'
        f'<div>Blocked part: <span style="color:#e4ecff;font-family:\'JetBrains Mono\','
        f'monospace;">{_bp_part}</span></div>'
        f'<div>Part lead time: <span style="color:#e4ecff;font-family:\'JetBrains Mono\','
        f'monospace;">{_bp_lead}d</span></div>'
        f'<div style="color:#6d85b8;font-size:0.8rem;margin-top:0.4rem;">{_bp_ev_part}</div>'
        f'</div>'
        f'<div style="display:flex;flex-direction:column;gap:0.55rem;margin-bottom:0.85rem;">'
        f'<div style="{_BOX_AMB}">Procure: <span style="color:#e4ecff;font-family:\'JetBrains Mono\','
        f'monospace;">{_bp_part}</span> — expedited lead time {_bp_lead}d</div>'
        f'<div style="{_BOX_AMB}">Interim: Reduce asset load 15–20% and monitor every 2 hours</div>'
        f'<div style="{_BOX_AMB}">Pre-book: repair window for Day {_bp_rebook}</div>'
        f'<div style="{_BOX_AMB}">Escalate: <span style="color:#e4ecff;">'
        f'{_bp_approver}</span> must approve procurement</div>'
        f'<div style="{_BOX_RED}">Shutdown threshold: if condition worsens before part arrives, '
        f'initiate controlled shutdown</div>'
        f'<div style="{_BOX_RED}">Decision: requires human judgment — consult {_bp_approver}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── No-window / act-now: scheduling options ────────────────────────────────────
if (
    rec.window_chosen is None
    and rec.urgency in ("urgent", "emergency")
    and rec.recommendation_status == "ok"
):
    _rul_ev = rec.evidence.get("risk", "")
    _dur_h  = rec.recommended_action.estimated_duration_hours or 2.0

    try:
        _nw_windows = find_windows(rec.asset_id, _dur_h, within_days=60)
    except Exception:
        _nw_windows = []

    if _nw_windows:
        _nw       = _nw_windows[0]
        _nw_id    = _nw.get("window_id", "—")
        _nw_start = (_nw.get("start_utc", "") or "")[:10]
        _nw_dur   = _nw.get("duration_hours", "—")
        _opt2_text = (
            f'Option 2 — Wait for <span style="color:#e4ecff;font-family:\'JetBrains Mono\','
            f'monospace;">{_nw_id}</span> on '
            f'<span style="color:#e4ecff;font-family:\'JetBrains Mono\',monospace;">'
            f'{_nw_start}</span> ({_nw_dur}h): lower disruption, higher asset risk (outside RUL)'
        )
    else:
        _opt2_text = (
            "Option 2 — No upcoming window found in the next 60 days: "
            "immediate action is the only option."
        )

    st.markdown(
        f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
        f'border-top:3px solid #e8a84a;padding:1.5rem 2rem 1rem 2rem;margin-bottom:1rem;">'
        f'<div style="{_LBL}">Scheduling Options — No Window Within RUL</div>'
        f'<div style="color:#b0c4e8;font-size:0.87rem;line-height:1.7;margin-bottom:0.9rem;">'
        f'No planned window fits within the RUL — '
        f'<span style="color:#6d85b8;font-size:0.82rem;">{_rul_ev}</span>'
        f'</div>'
        f'<div style="display:flex;flex-direction:column;gap:0.55rem;margin-bottom:0.85rem;">'
        f'<div style="background:#0d1628;border-radius:8px;border-left:3px solid #e85d4a;'
        f'padding:0.6rem 0.9rem;color:#b0c4e8;font-size:0.85rem;">'
        f'Option 1 — <b>Act now</b>: higher operational disruption, lower asset risk</div>'
        f'<div style="background:#0d1628;border-radius:8px;border-left:3px solid #e8a84a;'
        f'padding:0.6rem 0.9rem;color:#b0c4e8;font-size:0.85rem;">'
        f'{_opt2_text}</div>'
        f'</div>'
        f'<div style="color:#6d85b8;font-size:0.82rem;font-style:italic;">'
        f'Decision: requires human judgment — consult Plant Manager.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Detail tabs ────────────────────────────────────────────────────────────────
tab_parts, tab_schedule, tab_evidence = st.tabs(
    ["  Parts  ", "  Schedule  ", "  Evidence & Sources  "]
)

with tab_parts:
    if is_llm:
        if rec.required_parts:
            # Amber verify-banner only when there is actually a part below to verify.
            st.markdown(
                f'<div style="padding:12px 16px;border:1px solid #ffb74055;border-radius:10px;'
                f'background:#ffb74011;color:#f5d78a;font-size:0.85rem;margin-bottom:0.75rem;">'
                f'&#9432;&nbsp;This fault is not in the approved SOPs. The part below is pulled '
                f'from inventory for this asset&#8217;s bearing based on the AI&#8217;s suggested '
                f'action &#8212; <b>verify it is the correct part before ordering.</b></div>',
                unsafe_allow_html=True,
            )
            _llm_rows = ""
            for p in rec.required_parts:
                _llm_rows += (
                    f'<tr style="border-bottom:1px solid #1a2840;">'
                    + _td(p.part_number, mono=True)
                    + _td(str(p.quantity))
                    + _td(str(p.lead_time_days))
                    + f'<td style="padding:0.65rem 1rem;">'
                    + _pill("In inventory — verify", "#ffb740")
                    + f'</td></tr>'
                )
            st.markdown(
                f'<div style="background:#172038;border-radius:12px;border:1px solid #1e2d4d;overflow:hidden;">'
                f'<table style="width:100%;border-collapse:collapse;">'
                f'<thead><tr style="background:#0d1628;border-bottom:1px solid #2a3a5c;">'
                + _th("Part Number") + _th("Qty") + _th("Lead (days)") + _th("Status")
                + f'</tr></thead><tbody>{_llm_rows}</tbody></table></div>',
                unsafe_allow_html=True,
            )
        else:
            # No part for this action (e.g. inspect_and_monitor) — no verify-banner needed.
            st.markdown(
                f'<div style="{_CARD}color:#6d85b8;font-size:0.9rem;">'
                f'No specific part is required for the suggested action. This fault is not '
                f'in the approved SOPs; the reviewer will determine any parts if needed.</div>',
                unsafe_allow_html=True,
            )
    elif rec.required_parts:
        rows_html = ""
        for p in rec.required_parts:
            if p.lead_time_days == 0:
                status_html = _pill("In stock", "#2ecc8a")
                lead_display = str(p.lead_time_days)
            else:
                status_html = _pill(f"Order — {p.lead_time_days}d lead time", "#ffb740")
                lead_display = str(p.lead_time_days)
            rows_html += (
                f'<tr style="border-bottom:1px solid #1a2840;">'
                + _td(p.part_number, mono=True)
                + _td(str(p.quantity))
                + _td(lead_display)
                + f'<td style="padding:0.65rem 1rem;">{status_html}</td>'
                + "</tr>"
            )
        st.markdown(
            f'<div style="background:#172038;border-radius:12px;border:1px solid #1e2d4d;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="background:#0d1628;border-bottom:1px solid #2a3a5c;">'
            + _th("Part Number") + _th("Qty") + _th("Lead (days)") + _th("Status")
            + f'</tr></thead><tbody>{rows_html}</tbody></table></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="{_CARD}color:#6d85b8;font-size:0.9rem;">No parts required for this action.</div>',
            unsafe_allow_html=True,
        )
    if not is_llm:
        _part_src = rec.evidence.get("part", "")
        if _part_src:
            st.markdown(
                f'<div style="color:#3a5080;font-family:\'JetBrains Mono\',monospace;'
                f'font-size:0.62rem;margin-top:0.65rem;line-height:1.5;">'
                f'&#9432;&nbsp;{_part_src}</div>',
                unsafe_allow_html=True,
            )

with tab_schedule:
    if rec.window_chosen:
        win = _get_window(rec.window_chosen)
        if win:
            sc1, sc2, sc3 = st.columns(3)
            with sc1:
                st.markdown(_detail_card("Window ID", win.get("window_id", "-")), unsafe_allow_html=True)
            with sc2:
                st.markdown(_detail_card("Start Date", win.get("start_utc", "")[:10]), unsafe_allow_html=True)
            with sc3:
                st.markdown(_detail_card("Duration", f"{win.get('duration_hours')}h" if win.get('duration_hours') else "—"), unsafe_allow_html=True)
            if win.get("notes"):
                st.markdown(
                    f'<div style="background:#0d1628;border-radius:8px;border:1px solid #1e2d4d;'
                    f'padding:0.85rem 1.2rem;margin-top:1rem;color:#6d85b8;font-size:0.85rem;">'
                    f'{win["notes"]}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f'<div style="{_CARD}color:#b0c4e8;font-size:0.9rem;">'
                f'Window <span style="font-family:\'JetBrains Mono\',monospace;color:#e4ecff;">'
                f'{rec.window_chosen}</span> — details not available.</div>',
                unsafe_allow_html=True,
            )
    elif rec.urgency == "monitor":
        st.markdown(
            f'<div style="{_CARD}color:#6d85b8;font-size:0.9rem;">'
            f'Monitoring-only action. No scheduled maintenance slot required.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="background:#1a0f0a;border-radius:12px;border:1px solid #ff4d6a44;'
            f'padding:1.3rem 1.6rem;color:#ffb740;font-size:0.9rem;">'
            f'No maintenance window fits within the remaining useful life horizon. '
            f'Immediate unplanned action is required.</div>',
            unsafe_allow_html=True,
        )

with tab_evidence:
    # Pull the actual inputs used (demo OR manually-entered) so the "Value" column
    # shows the diagnosed values, not just what ended up in the recommendation.
    _s = st.session_state.get("active_inputs", {})
    _d = _s.get("diagnosis")
    _r = _s.get("risk")

    fault_val  = f"{_d.fault_mode} / {_d.severity}" if _d else rec.case_id
    rul_val    = f"{_r.rul_min_days}–{_r.rul_max_days} days" if _r else "—"
    part_val   = rec.required_parts[0].part_number if rec.required_parts else "None"
    window_val = rec.window_chosen or "None"
    ev         = rec.evidence  # the dict populated by the agent

    evidence_rows = [
        ("Fault",                fault_val,                                          ev.get("fault", "—")),
        ("Recommended Action",   rec.recommended_action.name.replace("_", " ").title(), ev.get("recommended_action", "—")),
        ("Remaining Life (RUL)", rul_val,                                            ev.get("risk", "—")),
        ("Part",                 part_val,                                           ev.get("part", "—")),
        ("Window",               window_val,                                         ev.get("window", "—")),
        ("Approver (signs off)", person,                                            ev.get("responsible_person", "—")),
        ("Urgency",              rec.urgency.title(),                                ev.get("urgency", "—")),
    ]

    ev_rows_html = ""
    for _claim, _value, _source in evidence_rows:
        ev_rows_html += (
            f'<tr style="border-bottom:1px solid #1a2840;">'
            f'<td style="padding:0.7rem 1rem;color:#e4ecff;font-weight:600;'
            f'font-size:0.87rem;white-space:nowrap;">{_claim}</td>'
            f'<td style="padding:0.7rem 1rem;color:#b0c4e8;'
            f'font-family:\'JetBrains Mono\',monospace;font-size:0.82rem;">{_value}</td>'
            f'<td style="padding:0.7rem 1rem;color:#3a5080;'
            f'font-family:\'JetBrains Mono\',monospace;font-size:0.75rem;line-height:1.5;">{_source}</td>'
            f'</tr>'
        )

    st.markdown(
        f'<div style="background:#172038;border-radius:12px;border:1px solid #1e2d4d;overflow:hidden;">'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr style="background:#0d1628;border-bottom:1px solid #2a3a5c;">'
        + _th("Claim") + _th("Value") + _th("Source")
        + f'</tr></thead><tbody>{ev_rows_html}</tbody></table></div>',
        unsafe_allow_html=True,
    )

st.markdown("<div style='margin-bottom:1.5rem;'></div>", unsafe_allow_html=True)


# ── Contributors section ("who needs to weigh in") ─────────────────────────────
# Contributors ADVISE (provide input/validation); they do NOT sign off.
# The approver below is the single gate. Empty list => render an intentional note.
_contributors = getattr(rec, "contributors", []) or []
if _contributors:
    cards_html = ""
    for _c in _contributors:
        _role    = _c.get("role", "")
        _name    = _c.get("name", "")
        _concern = _c.get("concern", "")
        _title   = f"{_role} — {_name}" if _name else _role
        cards_html += (
            f'<div style="background:#0d1628;border-radius:10px;border:1px solid #1e2d4d;'
            f'border-left:3px solid #4f8eff;padding:0.9rem 1.1rem;flex:1 1 230px;min-width:230px;">'
            f'<div style="color:#e4ecff;font-weight:600;font-size:0.9rem;margin-bottom:0.25rem;'
            f'overflow-wrap:break-word;">{_title}</div>'
            f'<div style="color:#6d85b8;font-size:0.8rem;line-height:1.5;'
            f'overflow-wrap:break-word;">{_concern}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
        f'padding:1.5rem 2rem;margin-bottom:1rem;">'
        f'<div style="{_LBL}">Input Required From</div>'
        f'<div style="color:#6d85b8;font-size:0.8rem;margin-bottom:1rem;">'
        f'These specialists must provide input or validation. They advise — '
        f'the approver below is the one who signs off.</div>'
        f'<div style="display:flex;gap:0.85rem;flex-wrap:wrap;">{cards_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
        f'padding:1.2rem 2rem;margin-bottom:1rem;">'
        f'<div style="{_LBL}">Input Required From</div>'
        f'<div style="color:#6d85b8;font-size:0.85rem;">'
        f'No additional input required — agent checks passed.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Approval section (the single sign-off gate) ────────────────────────────────
_appr_phrase = {
    "pending":   f"Awaiting approval from {person}",
    "escalated": f"Escalated — awaiting {person} (senior sign-off)",
    "approved":  f"Approved by {person}",
    "rejected":  f"Rejected by {person}",
}.get(current_approval, f"Awaiting approval from {person}")
esc_note    = " — Senior sign-off required" if (is_failure or current_approval == "escalated") else ""
pid_display = person_id

st.markdown(
    f'<div style="background:#172038;border-radius:14px;border:1px solid #1e2d4d;'
    f'border-top:3px solid {appr_color};padding:1.5rem 2rem 1rem 2rem;margin-bottom:1rem;">'
    f'<div style="{_LBL}">Approval \xb7 Sign-Off Authority</div>'
    f'<div style="display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap;margin-bottom:0.5rem;">'
    f'<span style="font-size:1.1rem;font-weight:600;color:#e4ecff;">{person}</span>'
    f'{_pill(current_approval.upper(), appr_color)}'
    f'</div>'
    f'<div style="color:{appr_color};font-size:0.92rem;font-weight:600;margin-bottom:0.35rem;">'
    f'{_appr_phrase}</div>'
    f'<div style="color:#3a5080;font-family:\'JetBrains Mono\',monospace;font-size:0.72rem;">'
    f'{pid_display}{esc_note}</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# Boundary note: Agent 6.6 DECIDES the routing (who approves, who must give input).
# The actual notifying/dispatch is performed downstream by the Executor (Agent 6.7).
st.markdown(
    '<div style="color:#5a6f9e;font-size:0.72rem;font-style:italic;'
    'margin:0.1rem 0 1.25rem 0;">'
    'Routing decided here; notification and dispatch are handled by the Executor (Agent 6.7).</div>',
    unsafe_allow_html=True,
)


# ── Export section (downloadable JSON + PDF of the FULL recommendation) ─────────
# JSON  -> machine-readable, for the Executor agent (every field via model_dump).
# PDF   -> human-readable report (printable, light background, all sections).
# Both use safe access so a missing field never crashes the export.

# Pull the actual inputs used (demo OR manually-entered) so the export can include
# fault/severity/RUL context.
_exp_scn  = st.session_state.get("active_inputs", {})
_exp_diag = _exp_scn.get("diagnosis")
_exp_risk = _exp_scn.get("risk")

st.markdown("<div style='margin-top:0.5rem;'></div>", unsafe_allow_html=True)
st.markdown(
    f'<div style="{_LBL}">Export</div>'
    f'<div style="color:#6d85b8;font-size:0.8rem;margin-bottom:0.8rem;">'
    f'Download the complete recommendation — JSON for the Executor agent, '
    f'PDF as a human-readable report.</div>',
    unsafe_allow_html=True,
)

_case = getattr(rec, "case_id", "case")
exp_col1, exp_col2 = st.columns(2)
with exp_col1:
    st.download_button(
        "Download JSON (for Executor)",
        data=_build_export_json(rec, _exp_diag, _exp_risk),
        file_name=f"recommendation_{_case}.json",
        mime="application/json",
        use_container_width=True,
        key="dl_json",
    )
with exp_col2:
    st.download_button(
        "Download PDF (Report)",
        data=_build_export_pdf(rec, _exp_diag, _exp_risk),
        file_name=f"recommendation_{_case}.pdf",
        mime="application/pdf",
        use_container_width=True,
        key="dl_pdf",
    )
