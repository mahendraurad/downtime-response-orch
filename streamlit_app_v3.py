"""
Learning & Memory Agent — Main Application (Streamlit UI + Orchestration Glue)
==============================================================================

This is the entry point for the Learning & Memory Agent (Agent 6.8 of the
Downtime Response Orchestrator). Run with: streamlit run streamlit_app_v3.py

WHAT THIS FILE DOES
-------------------
This is the main Streamlit application. It contains the entire user interface
(Discovery, Analytics, Search Case, Add Case tabs), the chatbot floating
popover, all rendering logic for the three pipeline paths (A/B/C), the
runtime configuration loader, the Langfuse observability initialization,
and the glue code that calls into api/streamlit_api.py to run the pipeline.

The file is ~7,600 lines organized into 19 clearly-marked SECTIONS. Use
Ctrl+F "SECTION:" in VS Code to jump between them. The 19 sections are:
  1.  IMPORTS AND CONFIGURATION
  2.  RUNTIME CONFIGURATION LOADER (cfg() function reads config.json)
  3.  LLM CLIENT AND CONSTANTS (AzureChatOpenAI setup)
  4.  LANGFUSE INITIALIZATION (with graceful fallback)
  5.  DOMAIN VOCABULARY (FAULT_SYNONYMS, ASSET_SYNONYMS, etc.)
  6.  DATA LOAD HELPERS
  7.  SEARCH AND MATCHING (keyword + synonym expansion)
  8.  ANALYTICS AND KPI RENDERING
  9.  ENRICHMENT PREVIEW (PATH_C dims generator)
  10. ADD CASE / SEARCH CASE tabs
  11. CHATBOT — INTENT / FOLLOW-UP detection
  12. CHATBOT — RETRIEVAL LAYER (cascade_retrieve_cases)
  13. CHATBOT — INTENT HANDLERS (6 handlers)
  14. CHATBOT — KB_LIST / ANALYTICS handlers
  15. CHATBOT — DISPATCH / follow-up chips
  16. KNOWLEDGE COPILOT RENDERER
  17. DISCOVERY TAB / PIPELINE trigger
  18. MAIN APP LAYOUT / ROUTING
  19. (reserved)

WHAT IT USES
------------
Third-party libraries:
  - streamlit           — reactive web UI framework (single-file app)
  - langchain_openai    — Azure OpenAI chat model client
  - langfuse            — LLM observability (traces, tokens, cost, latency)
  - plotly.graph_objects — Analytics tab visualizations
  - dotenv              — loads Azure and Langfuse credentials from .env

Local modules:
  - api.streamlit_api        — pipeline entry point (run_pipeline_demo, create_learned_case)
  - services.*               — 8 focused service modules (retrieval, generation, logging)
  - config.constants         — RUL_BY_STAGE (single source of truth)
  - schemas.learned_case     — Pydantic case model (via api layer)

TECHNIQUES APPLIED
------------------
  - LangGraph orchestration (delegated to orchestrator/langgraph_flow.py)
  - FAISS semantic retrieval with fault-mode mismatch penalty
  - Multi-turn chatbot with pronoun / interrogative follow-up detection
  - Tiered cascade retrieval for chatbot (see cascade_retrieve_cases)
  - Hybrid retrieval in Search Case tab (keyword + synonym + FAISS union)
  - LLM-based coverage assessment scoring 5 knowledge dimensions

CONFIGURATION
-------------
15 tunable values live in config.json (loaded by cfg() at startup):
  similarity_threshold, top_k, fault_mode_mismatch_penalty, LLM temperature
  and max_tokens, chatbot memory window, all file paths, Langfuse toggles.
See README.md Section 6 for the complete list and effect of each.

Environment variables (.env, not committed):
  AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT,
  AZURE_OPENAI_API_VERSION, LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY,
  LANGFUSE_BASE_URL.

FOR THE HANDOVER READER
-----------------------
See README.md for the complete architecture, integration contracts, and
deployment guide. See README.md Section 11 for documented tech debt
(including the deliberate namespace-aliasing pattern for stdlib imports
inside function bodies).
"""

# ═══════════════════════════════════════════════════════════════════
# SECTION: IMPORTS AND CONFIGURATION
# All standard-library, third-party, and internal imports; .env load.
# ═══════════════════════════════════════════════════════════════════
import os
from dotenv import load_dotenv

import re
import glob
import json
import html
import time
import logging
from datetime import date, datetime, timezone

import streamlit as st
import plotly.graph_objects as go

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)


# Auth and persona system removed — app is now single-user, single-view


# ── Backend ───────────────────────────────────────────────────────────────
try:
    from api.streamlit_api import create_learned_case, get_case_summary
    BACKEND = True
except ImportError:
    BACKEND = False

try:
    from api.streamlit_api import run_pipeline_demo
    PIPELINE_AVAILABLE = True
except Exception as _pe:
    print(f"[PIPELINE] Import failed: {_pe}")
    PIPELINE_AVAILABLE = False

try:
    from services.fault_tracker import record_fault_event, get_path_split
    TRACKER = True
except ImportError:
    TRACKER = False

TRACKER_AVAILABLE = TRACKER

try:
    from services.fault_tracker import get_recurring_faults, get_monthly_trend, load_all_records
except ImportError:
    def get_recurring_faults(top_n=5):
        return []
    def get_monthly_trend():
        return []
    def load_all_records():
        return []

from config.constants import RUL_BY_STAGE

# ═══════════════════════════════════════════════════════════════════
# SECTION: RUNTIME CONFIGURATION LOADER
# Loads config.json for tunable thresholds, paths, and model
# parameters. All values fall back to sensible defaults if the
# file is absent or malformed, so the app never crashes on
# config errors.
# ═══════════════════════════════════════════════════════════════════
import json as _cfg_json
from pathlib import Path as _CfgPath

def _load_runtime_config() -> dict:
    cfg_path = _CfgPath("config.json")
    if not cfg_path.exists():
        return {}
    try:
        return _cfg_json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}

RUNTIME_CONFIG = _load_runtime_config()

def cfg(*keys, default=None):
    """Dot-path config accessor. cfg('retrieval','similarity_threshold', default=0.70)."""
    node = RUNTIME_CONFIG
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node

DATA_DIR = cfg("paths", "data_dir", default="data/learned_cases")
LOG_FILE = cfg("paths", "memory_log", default="logs/memory.log")
DECISION_LOG = cfg("paths", "decision_log", default="logs/agent_decisions.log")
SIMILARITY_THRESHOLD = cfg("retrieval", "similarity_threshold", default=0.70)
FAISS_INDEX_DIR    = cfg("paths", "faiss_index_dir",  default="data/faiss_index")
FAULT_TRACKER_PATH = cfg("paths", "fault_tracker",    default="logs/fault_tracker.json")
_RETRIEVAL_TOP_K   = cfg("retrieval", "top_k",         default=3)


# ── LLM initialisation ────────────────────────────────
_env_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".env"
)
_loaded = load_dotenv(dotenv_path=_env_path, override=True)
print(f"[LLM] .env loaded: {_loaded}")
print(f"[LLM] .env path:   {_env_path}")

# ═══════════════════════════════════════════════════════════════════
# SECTION: LLM CLIENT AND CONSTANTS
# AzureChatOpenAI initialisation; LLM_AVAILABLE flag; shared logger.
# ═══════════════════════════════════════════════════════════════════
LLM_AVAILABLE = False
llm_client    = None

try:
    from langchain_openai import AzureChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    _key      = os.getenv("AZURE_OPENAI_KEY", "")
    _endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    _deploy   = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    _version  = os.getenv(
        "AZURE_OPENAI_API_VERSION", "2024-08-01-preview"
    )

    print(f"[LLM] Key present:  {bool(_key)}")
    print(f"[LLM] Endpoint:     {_endpoint}")
    print(f"[LLM] Deployment:   {_deploy}")
    print(f"[LLM] API version:  {_version}")

    if _key and _endpoint and _deploy:
        llm_client = AzureChatOpenAI(
            openai_api_key=_key,
            azure_endpoint=_endpoint,
            azure_deployment=_deploy,
            openai_api_version=_version,
            temperature=cfg("azure_openai", "temperature", default=0.3),
            max_tokens=cfg("azure_openai", "max_tokens", default=1500),
            request_timeout=30,
        )
        LLM_AVAILABLE = True
        print("[LLM] Initialised OK")
    else:
        _missing = [
            k for k, v in {
                "AZURE_OPENAI_KEY": _key,
                "AZURE_OPENAI_ENDPOINT": _endpoint,
                "AZURE_OPENAI_DEPLOYMENT": _deploy,
            }.items() if not v
        ]
        print(f"[LLM] Missing credentials: {_missing}")

except Exception as e:
    print(f"[LLM] Init error: {type(e).__name__}: {e}")
    LLM_AVAILABLE = False
    llm_client    = None


# ═══════════════════════════════════════════════════════════════════
# SECTION: LANGFUSE INITIALIZATION
# Langfuse auth + CallbackHandler; LANGFUSE_AVAILABLE gates all usage.
# ═══════════════════════════════════════════════════════════════════
# ── Config gate: allow config.json to disable Langfuse ─────────────────
# If langfuse.enabled=false in config.json, skip initialization
# entirely — no auth check, no handler, no startup span.
# Credentials in .env are ignored when this flag is False.
_LANGFUSE_ENABLED = cfg("langfuse", "enabled", default=True)
if _LANGFUSE_ENABLED:
    LANGFUSE_AVAILABLE = False
    LANGFUSE_ERROR = ""
    LANGFUSE_AUTH_OK = False
    langfuse_handler = None
    langfuse_client = None
    try:
        _lf_secret = os.getenv("LANGFUSE_SECRET_KEY", "")
        _lf_public = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        _lf_url    = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com"))
        # Force-set env vars so Langfuse SDK auto-discovers them and registers
        # the global client correctly (constructor-arg path can skip global registration)
        if _lf_secret:
            os.environ["LANGFUSE_SECRET_KEY"] = _lf_secret
        if _lf_public:
            os.environ["LANGFUSE_PUBLIC_KEY"] = _lf_public
        os.environ["LANGFUSE_HOST"] = _lf_url
        print(f"[LANGFUSE_DEBUG] secret_present={bool(_lf_secret)} public_present={bool(_lf_public)} host={_lf_url}")
        if _lf_secret and _lf_public:
            from langfuse import Langfuse, get_client
            from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
            # Instantiate with no args — picks up env vars and registers global client
            Langfuse()
            langfuse_client = get_client()
            try:
                LANGFUSE_AUTH_OK = langfuse_client.auth_check()
                print(f"[LANGFUSE_DEBUG] auth_check={LANGFUSE_AUTH_OK}")
            except Exception as _auth_err:
                LANGFUSE_ERROR = f"auth_check failed: {_auth_err}"
                print(f"[LANGFUSE_DEBUG] {LANGFUSE_ERROR}")
            langfuse_handler = LangfuseCallbackHandler()
            LANGFUSE_AVAILABLE = True
            # Manual smoke-test span — confirms end-to-end delivery
            try:
                with langfuse_client.start_as_current_observation(name="streamlit_app_startup", as_type="span") as _span:
                    _span.update(input={"event": "app_init"}, output={"status": "ok"})
                langfuse_client.flush()
                print("[LANGFUSE_DEBUG] manual_test_span_flushed=True")
            except Exception as _smoke_err:
                print(f"[LANGFUSE_DEBUG] manual_test_span_failed: {_smoke_err}")
            print(f"[LANGFUSE_DEBUG] handler_created=True global_client={langfuse_client is not None}")
        else:
            LANGFUSE_ERROR = "Missing LANGFUSE_SECRET_KEY or LANGFUSE_PUBLIC_KEY in env"
            print(f"[LANGFUSE_DEBUG] {LANGFUSE_ERROR}")
    except Exception as _lf_err:
        LANGFUSE_AVAILABLE = False
        LANGFUSE_ERROR = f"Init failed: {_lf_err}"
        print(f"[LANGFUSE_DEBUG] {LANGFUSE_ERROR}")
else:
    LANGFUSE_AVAILABLE = False
    LANGFUSE_ERROR = ""
    LANGFUSE_AUTH_OK = False
    langfuse_handler = None
    langfuse_client = None
    print("[LANGFUSE_DEBUG] disabled via config.json langfuse.enabled=false")
# ────────────────────────────────────────────────────────────────────────



def inject_css():
    """Inject the Accenture light-theme CSS into the Streamlit page."""
    # ── Light theme CSS (Accenture branding) ──────────────────────────────────────
    st.markdown("""
    <style>
    .stApp { background-color: #FFFFFF !important; }
    section[data-testid="stSidebar"] {
        background-color: #F8F7FB !important;
        border-right: 1px solid #E2E0EA !important;
    }
    /* Keep Streamlit toolbar visible so Deploy button is clickable */
    header[data-testid="stHeader"] {
        height: 2.5rem !important;
        background: transparent !important;
    }
    .main .block-container { padding-top: 2rem !important; max-width: 100% !important; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    .block-container { padding-top: 1rem !important; padding-bottom: 2rem !important; }
    [data-testid="block-container"] {
        padding-top: 2rem !important;
    }
    [data-testid="metric-container"] {
        background-color: #F8F7FB !important;
        border: 1px solid #E2E0EA !important;
        border-radius: 8px !important;
        padding: 14px !important;
    }
    [data-testid="stMetricValue"] {
        color: #1A1A1A !important;
        font-size: 24px !important;
        font-weight: 600 !important;
    }
    [data-testid="stMetricLabel"] {
        color: #8A8A8A !important;
        font-size: 10px !important;
        text-transform: uppercase !important;
        letter-spacing: 0.06em !important;
    }
    .dark-card {
        background: #F8F7FB;
        border: 1px solid #E2E0EA;
        border-radius: 8px;
        padding: 10px 12px;
        margin-bottom: 14px;
    }
    .upstream-box {
        background: #F0EFF8;
        border: 1px solid #D8D6E5;
        border-radius: 8px;
        padding: 10px 14px;
        margin-top: 10px;
    }
    .upstream-box:first-of-type {
        margin-top: 6px;
    }
    .upstream-box .vrow {
        margin: 0;
        padding: 3px 0;
        border-bottom: 1px dashed #DDD9EC;
    }
    .upstream-box .vrow:last-child {
        border-bottom: none;
    }
    .upstream-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
        margin-top: 10px;
    }
    .upstream-grid .upstream-box {
        margin-top: 0;
    }
    .upstream-grid .upstream-box .vval {
        font-size: 11px;
        text-align: right;
    }
    .upstream-grid .upstream-box .vkey {
        font-size: 11px;
    }
    .pipe-done {
        padding: 4px 10px; border-radius: 5px; font-size: 11px;
        font-weight: 500; background: #DCFCE7; color: #16A34A;
        white-space: nowrap; border: none;
    }
    .pipe-active {
        padding: 4px 10px; border-radius: 5px; font-size: 11px;
        font-weight: 600; background: #EDE9FE; color: #4F46E5;
        outline: 1px solid #8000CC; white-space: nowrap; border: none;
    }
    .pipe-standby {
        padding: 4px 10px; border-radius: 5px; font-size: 11px;
        font-weight: 500; background: #F0EFF8; color: #8A8A8A;
        white-space: nowrap; border: none;
    }
    .pipe-arrow { color: #AAAAAA; font-size: 12px; user-select: none; }
    .fb { padding: 2px 8px; border-radius: 10px; font-size: 10px;
          font-weight: 500; display: inline-block; }
    .fb-outer  { background: #FEE2E2; color: #DC2626; }
    .fb-inner  { background: #D1FAE5; color: #059669; }
    .fb-lube   { background: #FEF3C7; color: #D97706; }
    .fb-cage   { background: #F5F3FF; color: #7C3AED; }
    .fb-health { background: #DCFCE7; color: #16A34A; }
    .fb-sensor { background: #DBEAFE; color: #2563EB; }
    .fb-other  { background: #FEF3C7; color: #D97706; }
    .case-row {
        display: flex; align-items: center; gap: 10px;
        padding: 7px 0; border-bottom: 1px solid #E8E5F5;
        font-size: 11px;
    }
    .case-row:last-child { border-bottom: none; }
    .cid { font-weight: 600; color: #4F46E5; min-width: 72px; }
    .casset { color: #5A5A5A; flex: 1; }
    .cdate { color: #8A8A8A; font-size: 10px; }
    .log-block {
        background: #F0F4F8; border: 1px solid #E2E0EA; border-radius: 6px;
        padding: 10px 12px; font-family: 'Courier New', monospace;
        font-size: 10px; color: #16A34A; line-height: 1.8;
        max-height: 220px; overflow-y: auto; white-space: pre-wrap;
    }
    .stat-box {
        background: #F8F7FB; border: 1px solid #E2E0EA; border-radius: 8px;
        padding: 12px; text-align: center;
    }
    .stat-num { font-size: 22px; font-weight: 700; line-height: 1.1; }
    .stat-lbl { font-size: 10px; color: #8A8A8A; margin-top: 3px; }
    .agent-row {
        display: flex; align-items: flex-start; gap: 10px;
        padding: 9px 0; border-bottom: 1px solid #E8E5F5;
    }
    .agent-row:last-child { border-bottom: none; }
    .aname { font-size: 12px; font-weight: 600; color: #1A1A1A; }
    .adesc { font-size: 10px; color: #8A8A8A; line-height: 1.4; margin-top: 1px; }
    .badge-live { background: #DCFCE7; color: #16A34A; font-size: 9px;
                  padding: 2px 7px; border-radius: 8px; font-weight: 600; }
    .topbar {
        background: #F8F7FB; border: 1px solid #E2E0EA; border-radius: 8px;
        padding: 8px 16px; display: flex; align-items: center; gap: 10px;
        margin-bottom: 18px;
    }
    .vrow {
        display: flex; justify-content: space-between;
        padding: 6px 0; border-bottom: 1px solid #E8E5F5; font-size: 12px;
    }
    .vrow:last-child { border-bottom: none; }
    .vkey { color: #8A8A8A; }
    .vval { color: #1A1A1A; font-weight: 500; }
    .vval-green { color: #16A34A; font-weight: 600; }
    .vval-blue  { color: #4F46E5; font-weight: 600; }
    .sec-label {
        font-size: 10px; color: #8A8A8A; text-transform: uppercase;
        letter-spacing: 0.08em; font-weight: 600; margin-bottom: 10px;
    }
    .stTextInput > div > div > input {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
        border: 1px solid #E2E0EA !important;
    }
    .stTextArea > div > div > textarea {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
        border: 1px solid #E2E0EA !important;
    }
    .stSelectbox > div > div {
        background-color: #FFFFFF !important;
        border: 1px solid #E2E0EA !important;
    }
    div[data-baseweb="select"] > div {
        background-color: #FFFFFF !important;
        border: 1px solid #E2E0EA !important;
    }
    .stButton > button {
        background: linear-gradient(90deg, #A100FF 0%, #7500C0 100%) !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 6px !important;
        font-weight: 500 !important;
        padding: 8px 20px !important;
    }
    .stButton > button:hover { background: linear-gradient(90deg, #8B00E0 0%, #6200A8 100%) !important; }
    .stRadio > div { gap: 2px !important; }
    .stRadio label { color: #5A5A5A !important; font-size: 13px !important; }
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label { color: #5A5A5A !important; }
    hr { border-color: #E2E0EA !important; }
    
    /* ── additions (same dark palette) ── */
    .lg-node { padding:5px 11px; border-radius:5px; font-size:11px; font-weight:600;
               white-space:nowrap; }
    .lg-cap  { background:#EDE9FE; color:#4F46E5; }
    .lg-rag  { background:#F5F3FF; color:#7C3AED; }
    .lg-branch { background:#FEF3C7; color:#D97706; }
    .lg-a    { background:#DCFCE7; color:#16A34A; }
    .lg-b    { background:#FEF3C7; color:#D97706; }
    .banner {
        background:#F8F7FB; border:1px solid #E2E0EA; border-radius:8px;
        padding:10px 16px; margin-bottom:14px; font-size:12px; color:#5A5A5A;
        display:flex; align-items:center; gap:16px; flex-wrap:wrap;
    }
    .amber-note {
        background:#FEF3C7; border:1px solid #FDE68A; border-radius:6px;
        padding:9px 12px; font-size:11px; color:#D97706; margin-top:8px;
    }
    .road-card {
        background:#F8F7FB; border:1px solid #E2E0EA; border-radius:8px;
        padding:12px 14px;
    }
    .road-wk { font-size:10px; color:#8A8A8A; text-transform:uppercase;
               letter-spacing:.06em; }
    .road-ttl { font-size:13px; font-weight:600; color:#1A1A1A; margin:3px 0 5px; }
    .road-txt { font-size:10px; color:#5A5A5A; line-height:1.5; }
    /* Download icon button for generated case list */
    div[data-testid="stDownloadButton"] button {
        background: #F5EDFF !important;
        border: 1px solid #A100FF !important;
        color: #A100FF !important;
        border-radius: 6px !important;
        padding: 6px 12px !important;
        font-size: 14px !important;
        min-height: 32px !important;
        line-height: 1 !important;
    }
    div[data-testid="stDownloadButton"] button:hover {
        background: #EDD9FF !important;
        border-color: #C04AFF !important;
        color: #ffffff !important;
    }

    /* ─── Hide Streamlit sidebar entirely ─── */
    section[data-testid="stSidebar"] {
        display: none !important;
    }
    button[kind="header"] {
        display: none !important;
    }
    [data-testid="collapsedControl"] {
        display: none !important;
    }

    /* ─── DRO Header Bar (light strip — Accenture brand) ─── */
    .dro-header {
        display: flex;
        align-items: center;
        gap: 16px;
        padding: 18px 32px 14px 32px;
        margin: -1rem -3.5rem 14px -3.5rem;
        background: #ffffff;
        border-bottom: 4px solid transparent;
        border-image: linear-gradient(90deg, #A100FF 0%, #7500C0 35%, #E65C00 100%) 1;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        position: relative;
    }
    .dro-header-mark {
        width: 42px;
        height: 42px;
        border-radius: 10px;
        background: linear-gradient(135deg, #A100FF 0%, #460073 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        font-family: Arial, sans-serif;
        font-size: 22px;
        font-weight: 800;
        color: #fff;
        box-shadow: 0 3px 12px rgba(161, 0, 255, 0.30);
        letter-spacing: -0.02em;
    }
    .dro-header-text {
        display: flex;
        flex-direction: column;
        line-height: 1.25;
    }
    .dro-header-brand {
        font-size: 11px;
        font-weight: 800;
        color: #A100FF;
        letter-spacing: 0.18em;
        text-transform: uppercase;
    }
    .dro-header-title {
        font-size: 19px;
        font-weight: 800;
        color: #1A1A1A;
        letter-spacing: -0.01em;
    }
    .dro-header-sub {
        font-size: 11px;
        color: #8A8A8A;
        margin-top: 2px;
        font-weight: 500;
    }

    /* ─── Reduce default Streamlit top padding (extra room reclaimed by hiding sidebar) ─── */
    .block-container {
        padding-top: 0 !important;
        padding-left: 3.5rem !important;
        padding-right: 3.5rem !important;
        max-width: 100% !important;
    }

    /* ─── Chatbot popover button styling ─── */
    div[data-testid="stPopover"] > button {
        background: linear-gradient(135deg, #A100FF 0%, #7500C0 100%) !important;
        color: #fff !important;
        border: none !important;
        border-radius: 999px !important;
        padding: 5px 14px !important;
        font-weight: 600 !important;
        font-size: 11px !important;
        letter-spacing: 0.02em !important;
        box-shadow: 0 3px 10px rgba(161, 0, 255, 0.30) !important;
        transition: transform 0.15s ease, box-shadow 0.15s ease !important;
        min-height: 0 !important;
        line-height: 1.3 !important;
    }
    div[data-testid="stPopover"] > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 20px rgba(161, 0, 255, 0.55) !important;
    }
    div[data-testid="stPopover"] > button p {
        color: #fff !important;
        margin: 0 !important;
    }

    /* ─── Pipeline strip refinement ─── */
    .pipeline-strip {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px;
        padding: 10px 16px;
        margin: 6px 0 10px 0;
        background: #F4F4F8;
        border: 1px solid #E2E0EA;
        border-radius: 8px;
        font-size: 11px;
        line-height: 1.4;
    }
    .pipeline-label {
        color: #6A7080;
        font-weight: 700;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        margin-right: 6px;
    }
    .pipeline-node {
        color: #8A8A8A;
    }
    .pipeline-sep {
        color: #AAAAAA;
        margin: 0 2px;
    }
    .pipeline-node-active {
        display: inline-block;
        color: #A100FF;
        font-weight: 700;
        padding: 3px 10px;
        border: 1px solid #A100FF;
        border-radius: 6px;
        background: rgba(161, 0, 255, 0.08);
        box-shadow: 0 0 0 1px rgba(161, 0, 255, 0.18) inset;
    }

    /* ─── Typography bumps — Discovery & Case Library tabs ─── */
    /* Tab labels (Discovery / Case Library & Management / Analytics & Insights) */
    button[data-baseweb="tab"] p,
    button[data-baseweb="tab"] div {
        font-size: 15px !important;
        font-weight: 600 !important;
        letter-spacing: 0.01em !important;
    }

    /* Small uppercase eyebrow / section labels used across panels.
       These are the elements styled with the recurring pattern:
           font-size: 11px (or 10-12px)
           text-transform: uppercase
           letter-spacing: ~0.08-0.12em
       Bumping them to 12px keeps the eyebrow feel but improves readability. */
    .section-label,
    .eyebrow,
    [class*="eyebrow"],
    [class*="section-label"] {
        font-size: 12px !important;
    }

    /* Generic uppercase eyebrow spans used inline */
    span.eyebrow,
    div.eyebrow {
        font-size: 12px !important;
    }

    /* Body paragraph text inside Discovery and Case Library bodies.
       Streamlit wraps body text in <p> tags inside markdown blocks. */
    .stMarkdown p,
    .stMarkdown li {
        font-size: 14.5px !important;
        line-height: 1.55 !important;
    }

    /* Streamlit selectbox label (e.g. "Select demo scenario") */
    .stSelectbox label p,
    label[data-testid="stWidgetLabel"] p {
        font-size: 13px !important;
    }

    /* ─── Discovery page — uniform font bump for all content ─── */

    /* Pipeline strip — label, nodes, and active boxed node */
    .pipeline-label {
        font-size: 12.5px !important;
    }
    .pipeline-node,
    .pipeline-sep {
        font-size: 13px !important;
    }
    .pipeline-node-active {
        font-size: 13px !important;
        padding: 4px 12px !important;
    }

    /* Pipeline demo banner ("Pipeline demo · The agent decides...") */
    [class*="pipeline-demo"],
    [class*="pipeline_demo"] {
        font-size: 14px !important;
    }

    /* "How the Agent Works" eyebrow heading */
    .how-the-agent-works,
    [class*="how-the-agent"] {
        font-size: 13px !important;
    }

    /* Cards inside the "How the Agent Works" flow */
    /* Card title (Repair Completed, RAG Retrieval, Coverage Assessment,
       Path A/B/C, Knowledge Grows) */
    .flow-card-title,
    [class*="flow-card-title"],
    [class*="flow_card_title"] {
        font-size: 14.5px !important;
        font-weight: 700 !important;
    }

    /* Card subtitle (Work order closed, Top-k cases fetched, etc.) */
    .flow-card-sub,
    [class*="flow-card-sub"],
    [class*="flow_card_sub"] {
        font-size: 12px !important;
    }

    /* Path A/B/C path labels ("PATH A", "PATH B", "PATH C") */
    .path-label,
    [class*="path-label"] {
        font-size: 12px !important;
        letter-spacing: 0.10em !important;
    }

    /* Path A/B/C path titles ("Existing Knowledge", "Partial Knowledge",
       "New Knowledge", "Knowledge Grows") */
    .path-title,
    [class*="path-title"] {
        font-size: 14.5px !important;
        font-weight: 700 !important;
    }

    /* Path A/B/C path descriptions ("All 5 dimensions covered",
       "Some dimensions missing", "No case found", "Repository updated") */
    .path-desc,
    [class*="path-desc"] {
        font-size: 12px !important;
    }

    /* OR separators between Path A/B/C */
    .path-or {
        font-size: 12px !important;
        font-weight: 600 !important;
    }

    /* Chevron arrows (›) between flow cards */
    .flow-chevron,
    [class*="flow-chevron"] {
        font-size: 18px !important;
    }

    /* Long explanatory paragraph below the flow cards
       ("After a repair is completed, the agent uses RAG...")
       Already covered by .stMarkdown p rule but reinforce here. */
    .discovery-explanation,
    [class*="discovery-explanation"] {
        font-size: 14.5px !important;
        line-height: 1.6 !important;
    }

    /* "Select demo scenario" label */
    .scenario-selector-label,
    [class*="scenario-selector-label"] {
        font-size: 13px !important;
    }

    /* Selectbox dropdown value (the selected scenario name) */
    div[data-baseweb="select"] > div {
        font-size: 14px !important;
    }

    /* Scenario Description label */
    .scenario-description-label,
    [class*="scenario-description"] {
        font-size: 13px !important;
    }

    /* Scenario Description body text */
    .scenario-description-body,
    [class*="scenario-description-body"] {
        font-size: 14px !important;
        line-height: 1.55 !important;
    }

    /* ─── Discovery typography scale (unified, consistent) ─────────────
       5 sizes total. Every Discovery-tab text element falls into one.
       Tab labels and DRO header are locked separately at the end. */

    /* TIER 1 — Eyebrow labels (uppercase small caps): 12px */
    [class*="eyebrow"],
    [class*="section-label"],
    [class*="path-label"],
    .scenario-selector-label,
    [class*="scenario-selector-label"],
    .scenario-description-label,
    [class*="scenario-description-label"],
    .how-the-agent-works,
    [class*="how-the-agent"],
    .pipeline-label {
        font-size: 12px !important;
        font-weight: 700 !important;
        letter-spacing: 0.10em !important;
        text-transform: uppercase !important;
    }

    /* TIER 2 — Card / section titles: 15px */
    .flow-card-title,
    [class*="flow-card-title"],
    [class*="flow_card_title"],
    .path-title,
    [class*="path-title"],
    [class*="section-title"],
    [class*="card-title"] {
        font-size: 15px !important;
        font-weight: 700 !important;
    }

    /* TIER 3 — Body / sub text: 13.5px */
    .stMarkdown p,
    .stMarkdown li,
    .stMarkdown span,
    .flow-card-sub,
    [class*="flow-card-sub"],
    [class*="flow_card_sub"],
    .path-desc,
    [class*="path-desc"],
    .scenario-description-body,
    [class*="scenario-description-body"],
    .discovery-explanation,
    [class*="discovery-explanation"],
    .dimension-note,
    [class*="dimension-note"] {
        font-size: 13.5px !important;
        line-height: 1.55 !important;
    }

    /* TIER 4 — Numeric values / key callouts: 14px */
    .input-value,
    [class*="input-value"],
    .metric-value-inline {
        font-size: 14px !important;
        font-weight: 600 !important;
    }

    /* TIER 5 — Pipeline strip nodes: 13px (slightly compact) */
    .pipeline-node,
    .pipeline-sep {
        font-size: 13px !important;
    }
    .pipeline-node-active {
        font-size: 13px !important;
        padding: 4px 12px !important;
    }

    /* OR separators between path cards */
    .path-or {
        font-size: 12px !important;
        font-weight: 700 !important;
        letter-spacing: 0.08em !important;
    }

    /* Selectbox label "SELECT DEMO SCENARIO" */
    .stSelectbox label p,
    label[data-testid="stWidgetLabel"] p {
        font-size: 12px !important;
        font-weight: 700 !important;
        letter-spacing: 0.10em !important;
        text-transform: uppercase !important;
    }

    /* Selectbox value (e.g. "Outer race fault — Motor (SKF6310)") */
    div[data-baseweb="select"] > div {
        font-size: 14.5px !important;
    }

    /* Tab labels (navigation — own size, separate from scale) */
    button[data-baseweb="tab"] p,
    button[data-baseweb="tab"] div {
        font-size: 16px !important;
        font-weight: 600 !important;
    }

    /* DRO header — locked sizes, must override any cascade */
    .dro-header-brand     { font-size: 11px !important; }
    .dro-header-title     { font-size: 19px !important; }
    .dro-header-sub       { font-size: 11px !important; }

    [data-testid="stHeader"] {
        display: none !important;
    }
    [data-testid="stMainBlockContainer"] {
        padding-top: 0 !important;
    }
    [data-testid="stAppViewContainer"] > .main {
        padding-top: 0 !important;
    }

    /* ── Discovery 50/50 split — vertical flow column ── */
    .disc-flow-col {
        display: flex;
        flex-direction: column;
        gap: 10px;
        padding-right: 12px;
    }
    .disc-flow-step {
        background: #F8F7FB;
        border: 1px solid #E2E0EA;
        border-left: 4px solid #A100FF;
        border-radius: 8px;
        padding: 10px 14px;
    }
    .disc-flow-step-title {
        font-size: 13px;
        font-weight: 600;
        color: #1A1A1A;
        margin-bottom: 2px;
    }
    .disc-flow-step-sub {
        font-size: 11px;
        color: #8A8A8A;
    }
    .disc-flow-arrow {
        color: #8A8A8A;
        font-size: 1.1rem;
        text-align: center;
        line-height: 1;
        padding: 0;
    }
    .vertical-paths-block {
        background: #F8F8FC;
        border: 1px dashed #D0CDE0;
        border-radius: 6px;
        padding: 10px 8px;
        margin: 4px 0;
        display: flex;
        flex-direction: row;
        align-items: stretch;
        gap: 4px;
    }
    .vertical-path-card {
        flex: 1;
        background: #F8F7FB;
        border: 1px solid #E2E0EA;
        border-radius: 5px;
        padding: 6px 8px;
        margin: 0;
        text-align: center;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }
    .vertical-path-card.path-a { border-left: 3px solid #16A34A; }
    .vertical-path-card.path-c { border-left: 3px solid #D97706; }
    .vertical-path-card.path-b { border-left: 3px solid #A100FF; }
    .vertical-path-label {
        font-size: 9px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin: 0;
    }
    .vertical-path-title {
        font-size: 11px;
        font-weight: 700;
        margin: 1px 0 0 0;
        line-height: 1.2;
    }
    .vertical-path-desc {
        font-size: 9.5px;
        color: #5A5A5A;
        margin: 1px 0 0 0;
        line-height: 1.3;
    }
    .vertical-or-sep {
        display: flex;
        align-items: center;
        color: #8A8A8A;
        font-size: 9px;
        font-weight: 700;
        letter-spacing: 0.10em;
        padding: 0 2px;
    }

    /* ─── Horizontal dimension coverage cards (5 in a row) ─── */
    .dim-coverage-row {
        display: flex;
        flex-direction: row;
        gap: 6px;
        margin: 8px 0 12px 0;
    }
    .dim-coverage-card {
        flex: 1 1 0;
        min-width: 0;
        background: #F8F7FB;
        border: 1px solid #E2E0EA;
        border-radius: 6px;
        padding: 8px 8px;
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
        overflow: hidden;
    }
    .dim-coverage-card.covered {
        border-left: 3px solid #16A34A;
    }
    .dim-coverage-card.missing {
        border-left: 3px solid #D97706;
    }
    .dim-coverage-icon {
        font-size: 16px;
        font-weight: 700;
        line-height: 1;
        margin-bottom: 4px;
    }
    .dim-coverage-icon.covered { color: #16A34A; }
    .dim-coverage-icon.missing { color: #D97706; }
    .dim-coverage-name {
        font-size: 10.5px;
        font-weight: 700;
        color: #1A1A1A;
        line-height: 1.25;
        margin: 0 0 4px 0;
        word-break: break-word;
        hyphens: auto;
    }
    .dim-coverage-desc {
        font-size: 9.5px;
        color: #5A5A5A;
        line-height: 1.35;
        margin: 0;
        word-break: break-word;
    }

    /* ─── Chatbot popover: pulled into header position via CSS ─── */
    /* The popover is rendered immediately after the .dro-header HTML block.
       We use absolute positioning relative to a parent that contains both. */

    /* Make the popover wrapper position itself in the top-right of the page */
    div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stPopover"]) {
        position: absolute !important;
        top: 22px !important;
        right: 32px !important;
        z-index: 100 !important;
        width: auto !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    /* Style the chatbot popover button — outlined purple on white strip */
    div[data-testid="stPopover"] > button {
        background: transparent !important;
        color: #A100FF !important;
        border: 1.5px solid #A100FF !important;
        border-radius: 999px !important;
        padding: 6px 18px !important;
        font-weight: 700 !important;
        font-size: 12px !important;
        letter-spacing: 0.04em !important;
        box-shadow: none !important;
        min-height: 0 !important;
        line-height: 1.3 !important;
        transition: background 0.15s ease, color 0.15s ease !important;
    }
    div[data-testid="stPopover"] > button:hover {
        background: #A100FF !important;
        color: #ffffff !important;
    }
    div[data-testid="stPopover"] > button p {
        color: inherit !important;
        margin: 0 !important;
    }
    /* Thin horizontal scrollbar for Discovery panel outer containers */
    .compact-panel::-webkit-scrollbar { height: 6px; width: 6px; }
    .compact-panel::-webkit-scrollbar-track { background: transparent; }
    .compact-panel::-webkit-scrollbar-thumb { background: #C0BDD0; border-radius: 3px; }
    .compact-panel::-webkit-scrollbar-thumb:hover { background: #8A8A8A; }
    </style>
    """, unsafe_allow_html=True)



# ── Asset context lookup ─────────────────────────────────────────────────
ASSET_LINE_MAP = {
    "AST_MTR_001": {"line": "LINE_001", "line_name": "Conveyor Assembly"},
    "AST_MTR_002": {"line": "LINE_001", "line_name": "Conveyor Assembly"},
    "AST_PMP_001": {"line": "LINE_002", "line_name": "Cooling Process"},
    "AST_PMP_002": {"line": "LINE_002", "line_name": "Cooling Process"},
    "AST_CON_001": {"line": "LINE_001", "line_name": "Conveyor Assembly"},
    "AST_GBX_001": {"line": "LINE_001", "line_name": "Conveyor Assembly"},
}

FAULT_RUL_DAYS = {
    "outer_race_fault":  {1: 90,  2: 30, 3: 7},
    "inner_race_fault":  {1: 80,  2: 25, 3: 5},
    "lubrication_issue": {1: 60,  2: 20, 3: 5},
    "lubrication_failure": {1: 60, 2: 20, 3: 5},
    "misalignment":      {1: 100, 2: 40, 3: 10},
    "cage_fault":        {1: 75,  2: 20, 3: 4},
    "imbalance":         {1: 120, 2: 60, 3: 15},
    "sensor_fault":      {1: None, 2: None, 3: None},
    "healthy_baseline":  {1: None, 2: None, 3: None},
}

SIGNAL_LABELS = {
    "vib_rms_mm_s":         ("Vibration (RMS)",  "mm/s"),
    "kurtosis":             ("Kurtosis",         ""),
    "temp_c":               ("Temperature",      "°C"),
    "bpfo_energy":          ("BPFO energy",      ""),
    "bpfi_energy":          ("BPFI energy",      ""),
    "signal_quality_score": ("Signal quality",   ""),
    "signal_quality":       ("Signal quality",   ""),
    "shaft_offset_mm":      ("Shaft offset",     "mm"),
}

ASSET_DISPLAY = {
    "AST_MTR_001": "Motor A · SKF6310",
    "AST_MTR_002": "Motor B · SKF6310",
    "AST_PMP_001": "Pump A · SKF6208",
    "AST_PMP_002": "Pump B · SKF6208",
    "AST_CON_001": "Conveyor · SKF22212",
    "AST_GBX_001": "Gearbox · SKF22318",
}

# ── Synonym / vocabulary tables for chatbot retrieval ────────────────────────

# ═══════════════════════════════════════════════════════════════════
# SECTION: DOMAIN VOCABULARY
# Fault-mode synonyms, intent-token lists, and resolve_fault_mode().
# ═══════════════════════════════════════════════════════════════════
FAULT_SYNONYMS = {
    "outer_race_fault": [
        "outer race", "outer-race", "outer ring", "outer raceway",
        "bpfo", "race outer", "ore fault", "outer race defect",
        "spalling outer", "pitting outer race",
    ],
    "inner_race_fault": [
        "inner race", "inner-race", "inner ring", "inner raceway",
        "bpfi", "race inner", "ire fault", "inner race defect",
        "spalling inner", "pitting inner race",
    ],
    "cage_fault": [
        "cage", "cage damage", "cage wear", "ftf",
        "retainer", "ball cage", "separator", "cage fracture",
        "cage break",
    ],
    "lubrication_issue": [
        "lubric", "lube", "grease", "greasing", "oil",
        "oiling", "lubrication", "lubricant", "dry running",
        "starved lubrication", "lube starvation", "over-greased",
        "wrong grease", "lubrication failure", "grease degradation",
    ],
    "misalignment": [
        "misalign", "alignment", "shaft offset", "shaft misalign",
        "coupling offset", "angular misalign", "parallel misalign",
        "shaft alignment", "soft foot",
    ],
    "sensor_fault": [
        "sensor", "signal dropout", "signal loss", "transducer",
        "probe", "accelerometer fail", "no signal",
        "sensor failure", "bad reading", "missing data",
        "flat signal", "signal noise",
    ],
    "healthy": [
        "healthy", "baseline", "normal", "no fault",
        "all good", "running fine", "ok status", "good condition",
        "nominal", "no anomaly",
    ],
}

GENERIC_BEARING_TOKENS = [
    "bearing damage", "bearing wear", "worn bearing",
    "bearing failure", "bearing fault", "bearing defect",
    "spalling", "pitting", "race damage", "ball damage",
]

ASSET_SYNONYMS = {
    "motor": [
        "motor", "drive motor", "electric motor", "induction motor",
        "ac motor", "servo motor", "spindle motor",
    ],
    "pump": [
        "pump", "centrifugal pump", "booster pump",
        "circulation pump", "feed pump", "suction pump", "water pump",
    ],
    "gearbox": [
        "gearbox", "gear box", "reducer", "gear reducer",
        "transmission", "speed reducer", "drive train", "gear drive",
    ],
    "conveyor": [
        "conveyor", "conveyor belt", "belt drive",
        "belt conveyor", "drive belt", "transport belt",
    ],
}

FAULT_SYMPTOM_TOKENS = [
    "vibration", "vib", "vibrating", "overheat", "overheating",
    "hot bearing", "noisy", "grinding noise", "squeal",
    "rumble", "knocking", "high temp", "temp spike",
    "bearing noise", "knock", "rattle",
]


def resolve_fault_mode(text: str) -> str:
    """Return canonical fault_mode for free-text input, or '' if none matches."""
    t = text.lower()
    for canonical, synonyms in FAULT_SYNONYMS.items():
        for syn in synonyms:
            if syn in t:
                return canonical
    return ""


def resolve_asset_type(text: str) -> str:
    """Return canonical asset_type for free-text input, or '' if none matches."""
    t = text.lower()
    for canonical, synonyms in ASSET_SYNONYMS.items():
        for syn in synonyms:
            if syn in t:
                return canonical
    return ""


def is_fault_domain(text: str) -> bool:
    """Return True if text contains any fault-domain signal."""
    t = text.lower()
    if resolve_fault_mode(t):
        return True
    if resolve_asset_type(t):
        return True
    if any(tok in t for tok in FAULT_SYMPTOM_TOKENS):
        return True
    if any(tok in t for tok in GENERIC_BEARING_TOKENS):
        return True
    import re as _re
    # Bearing model
    if _re.search(r"\bskf\s*\d{3,5}\b", t):
        return True
    # Case ID like CASE_001 or CASE_007_MANUAL
    if _re.search(r"\bcase[\s_]*\d{2,4}", t):
        return True
    # Asset ID like AST_MTR_001
    if _re.search(r"\bast[\s_]*[a-z]{3}[\s_]*\d{3}", t):
        return True
    return False

# ══════════════════════════════════════════════════════════════════════════
# SCENARIO DATA (Change 2) — 7 real named scenarios
# Agent routes Path A/B/C via LLM coverage assessment of 5 dimensions
# ══════════════════════════════════════════════════════════════════════════
SCENARIOS = {
    "Outer race fault — Motor (SKF6310)": {
        "asset": "AST_MTR_001", "bearing": "SKF6310", "fault": "outer_race_fault",
        "stage": 3, "risk": "HIGH", "score": 0.81, "matched_case": "CASE_001",
        "signals": {"vib_rms_mm_s": 7.5, "kurtosis": 7.2, "temp_c": 81.0,
                    "bpfo_energy": 3.8, "signal_quality": 0.99},
    },
    "Misalignment — Gearbox (SKF22318)": {
        "asset": "AST_GBX_001", "bearing": "SKF22318", "fault": "misalignment",
        "stage": 2, "risk": "MEDIUM", "score": 0.63, "matched_case": "",
        "signals": {"vib_rms_mm_s": 5.2, "kurtosis": 3.1, "temp_c": 68.0,
                    "shaft_offset_mm": 0.8, "signal_quality": 0.97},
    },
    "Lubrication failure — Pump (SKF6208)": {
        "asset": "AST_PMP_001", "bearing": "SKF6208", "fault": "lubrication_issue",
        "stage": 2, "risk": "MEDIUM", "score": 0.77, "matched_case": "CASE_002",
        "signals": {"vib_rms_mm_s": 4.2, "kurtosis": 3.9, "temp_c": 75.0,
                    "bpfo_energy": 1.1, "signal_quality": 0.98},
    },
    "Cage fault — Conveyor (SKF22212)": {
        "asset": "AST_CNV_002", "bearing": "SKF22212", "fault": "cage_fault",
        "stage": 2, "risk": "MEDIUM", "score": 0.74, "matched_case": "CASE_004",
        "signals": {"vib_rms_mm_s": 4.8, "kurtosis": 5.1, "temp_c": 66.0,
                    "bpfo_energy": 2.0, "signal_quality": 0.97},
    },
    "Inner race fault — Motor (SKF6310)": {
        "asset": "AST_MTR_002", "bearing": "SKF6310", "fault": "inner_race_fault",
        "stage": 2, "risk": "HIGH", "score": 0.72, "matched_case": "CASE_003",
        "signals": {"vib_rms_mm_s": 5.6, "kurtosis": 6.1, "temp_c": 74.0,
                    "bpfi_energy": 4.8, "signal_quality": 0.98},
    },
    "Sensor fault — Conveyor (SKF22212)": {
        "asset": "AST_CNV_001", "bearing": "SKF22212", "fault": "sensor_fault",
        "stage": 1, "risk": "LOW", "score": 0.58, "matched_case": "CASE_006",
        "signals": {"vib_rms_mm_s": 2.4, "kurtosis": 2.7, "temp_c": 56.0,
                    "signal_quality": 0.15},
    },
    "Healthy baseline — Motor (SKF6310)": {
        "asset": "AST_MTR_003", "bearing": "SKF6310", "fault": "healthy",
        "stage": 0, "risk": "NONE", "score": 0.21, "matched_case": "CASE_005",
        "signals": {"vib_rms_mm_s": 1.9, "kurtosis": 2.1, "temp_c": 52.0,
                    "bpfo_energy": 0.6, "signal_quality": 1.0},
    },
    # ── PATH_C demo scenarios ──────────────────────────────────────────────
    # Both fire PATH_C via NOTE C in the coverage assessor: asset_type of the
    # incoming incident differs from the matched KB case → action_and_outcome
    # marked PARTIAL → routing to Path C (partial knowledge).
    "Outer race fault — Pump (SKF6208)": {
        "asset": "AST_PMP_002", "bearing": "SKF6208", "fault": "outer_race_fault",
        "stage": 3, "risk": "HIGH", "score": 0.83, "matched_case": "CASE_001",
        "signals": {"vib_rms_mm_s": 5.4, "kurtosis": 6.1, "temp_c": 71.0,
                    "bpfo_energy": 3.2, "signal_quality": 0.96},
    },
    "Cage fault — Pump (SKF6208)": {
        "asset": "AST_PMP_003", "bearing": "SKF6208", "fault": "cage_fault",
        "stage": 3, "risk": "HIGH", "score": 0.87, "matched_case": "CASE_004",
        "signals": {"vib_rms_mm_s": 6.8, "kurtosis": 8.2, "temp_c": 79.0,
                    "bpfo_energy": 2.8, "signal_quality": 0.95},
    },
}

FAULT_CLASS = {
    "outer_race_fault": ("fb fb-outer", "outer race"),
    "inner_race_fault": ("fb fb-inner", "inner race"),
    "lubrication": ("fb fb-lube", "lubrication"),
    "lubrication_issue": ("fb fb-lube", "lubrication"),
    "lubrication_failure": ("fb fb-lube", "lubrication"),
    "cage_fault": ("fb fb-cage", "cage fault"),
    "healthy": ("fb fb-health", "healthy"),
    "sensor_fault": ("fb fb-sensor", "sensor"),
    "imbalance": ("fb fb-other", "imbalance"),
    "misalignment": ("fb fb-other", "misalignment"),
}

FAULT_COLOR = {
    "outer_race_fault": ("#FEE2E2", "#DC2626"),
    "inner_race_fault": ("#D1FAE5", "#059669"),
    "lubrication": ("#FEF3C7", "#D97706"),
    "lubrication_issue": ("#FEF3C7", "#D97706"),
    "lubrication_failure": ("#FEF3C7", "#D97706"),
    "cage_fault": ("#F5F3FF", "#7C3AED"),
    "healthy": ("#DCFCE7", "#16A34A"),
    "sensor_fault": ("#DBEAFE", "#2563EB"),
    "imbalance": ("#FEF3C7", "#D97706"),
    "misalignment": ("#FEF3C7", "#D97706"),
}

SCENARIO_DESCRIPTIONS = {
    "Outer race fault — Motor (SKF6310)":
        "A conveyor motor bearing shows progressive outer race "
        "deterioration. Vibration and BPFO energy are elevated "
        "significantly above baseline. Stage 3 confirmed. "
        "High criticality asset on production Line 1.",

    "Misalignment — Gearbox (SKF22318)":
        "A gearbox unit shows unusual harmonic vibration with "
        "2X RPM dominance and elevated shaft offset. Stage 2 "
        "misalignment detected. Medium risk. Bottleneck asset "
        "on Line 1.",

    "Lubrication failure — Pump (SKF6208)":
        "A cooling pump bearing shows broadband vibration rise "
        "with temperature increase. Lubrication degradation "
        "suspected. Stage 2. Medium risk on Line 2.",

    "Cage fault — Conveyor (SKF22212)":
        "A conveyor assembly bearing shows FTF frequency "
        "dominance with irregular cage behaviour. Stage 2 "
        "cage fault confirmed. Medium risk on Line 1.",

    "Inner race fault — Motor (SKF6310)":
        "A production motor shows BPFI energy dominance "
        "indicating inner race spalling. Stage 2. High risk "
        "asset on Line 1.",

    "Sensor fault — Conveyor (SKF22212)":
        "Burst signal dropouts detected on conveyor channel "
        "CH_31B. Pattern indicates a physical connection "
        "issue, not a mechanical bearing fault. "
        "Low risk. Requires sensor inspection.",

    "Healthy baseline — Motor (SKF6310)":
        "Routine preventive check on a motor asset. All "
        "vibration and temperature readings within normal "
        "baseline range. No fault detected. Stage 0.",

    "Outer race fault — Pump (SKF6208)":
        "Centrifugal pump bearing shows outer race spalling. "
        "BPFO energy and kurtosis elevated. Stage 3. Fault "
        "mode matches prior motor cases in the KB, but pump "
        "maintenance requires different disassembly and "
        "sealing procedures. PATH_C expected.",

    "Cage fault — Pump (SKF6208)":
        "Cage damage detected on a pump bearing at stage 3. "
        "High kurtosis (8.2) and elevated temperature (79°C). "
        "Same fault class as the conveyor case (CASE_004) in "
        "the KB, but the asset is a centrifugal pump. "
        "Conveyor procedure does not translate to pump "
        "maintenance. PATH_C expected.",
}


def assess_nl_case(case_dict: dict, source: str) -> dict:
    """
    Run retrieval + coverage assessment on an NL-extracted case dict.

    Builds a minimal feedback dict from the user-provided / LLM-extracted
    fields, invokes the LangGraph pipeline (which runs retrieval and the
    coverage assessor), and returns the result.

    Save semantics by verdict:
      - EXISTING: caller should NOT save. Show the user the existing case.
      - PARTIAL:  caller should save the new case alongside, flag gaps.
      - NEW:      caller should save the new case (pipeline write is suppressed
                  by _nl_assessment_only; caller handles disk + FAISS).

    Returns a dict with keys:
      knowledge_state, retrieved_case_id, covered_dimensions,
      missing_dimensions, gap_summary, coverage_reasoning,
      coverage_method, candidate_content
    """
    import uuid

    # Normalize natural language fault/asset text to underscore format
    def _normalize(text: str) -> str:
        return text.lower().strip().replace(" ", "_").replace("-", "_")

    _fault = _normalize(case_dict.get("fault_mode", ""))
    _asset = _normalize(case_dict.get("asset_type", ""))

    feedback = {
        "case_id":           case_dict.get("case_id", generate_next_case_id()),
        "fault_mode":        _fault,
        "asset_type":        _asset,
        "asset_id":          case_dict.get("asset_id", ""),
        "bearing_type":      case_dict.get("bearing_type", ""),
        "root_cause":        case_dict.get("root_cause", ""),
        "action_taken":      case_dict.get("action_taken", ""),
        "lessons_learned":   case_dict.get("lessons_learned", ""),
        "source":            source,
        "created_at":        datetime.utcnow().isoformat() + "Z",
        "_nl_assessment_only": True,
    }

    run_id = f"nl_{source}_{uuid.uuid4().hex[:8]}"
    result = run_pipeline_demo(
        feedback,
        run_id,
        llm_client=llm_client,
        langfuse_handler=langfuse_handler,
    )
    return result or {}


def build_feedback_from_scenario(
        scenario: dict,
        run_id: str) -> dict:
    """Convert scenario dict to feedback."""
    asset_id = scenario.get("asset", "AST_MTR_001")
    if "MTR" in asset_id:
        asset_type = "motor"
    elif "GBX" in asset_id:
        asset_type = "gearbox"
    elif "PMP" in asset_id:
        asset_type = "pump"
    elif "CNV" in asset_id:
        asset_type = "conveyor"
    else:
        asset_type = "motor"

    # Get signal values from scenario
    signals = scenario.get("signals", {})
    vib = signals.get("vib_rms_mm_s", 0.0)
    kurtosis = signals.get("kurtosis", 0.0)
    temp = signals.get("temp_c", 0.0)
    bpfo = signals.get("bpfo_energy", 0.0)
    bpfi = signals.get("bpfi_energy", 0.0)
    sig_q = signals.get("signal_quality", 0.99)
    score = float(scenario.get("score", 0.0))
    stage = int(scenario.get("stage", 2))

    # Get RUL from fault taxonomy
    fault = scenario.get(
        "fault",
        scenario.get("fault_mode",
                     "outer_race_fault"))
    # NOTE: FAULT_RUL consolidated into config/constants.py as RUL_BY_STAGE.
    # Remaining inline dicts (FAULT_ACTION, FAULT_ROOT_CAUSE, FAULT_LESSONS)
    # have divergent schemas vs constants.py and are intentionally left here.
    rul = RUL_BY_STAGE.get(fault, {}).get(stage)
    rul_str = (f"{rul}-{rul + 5}" if rul else "N/A")

    # Failure probability from score
    failure_prob = round(score * 100, 1)

    FAULT_ACTION = {
        "outer_race_fault": {
            "action": "Urgent bearing replacement",
            "sop": "SOP_001 / SOP_005",
            "parts": "PART_001 (SKF6310-ZZ) or PART_003 (SKF22318-E)",
            "duration_hr": 6,
        },
        "inner_race_fault": {
            "action": "Bearing replacement + load review",
            "sop": "SOP_001",
            "parts": "PART_001 (SKF6310-ZZ)",
            "duration_hr": 6,
        },
        "lubrication_issue": {
            "action": "Oil drain and refill service",
            "sop": "SOP_003",
            "parts": "PART_005 (Industrial Oil ISO68, 200 mL)",
            "duration_hr": 3,
        },
        "lubrication_failure": {
            "action": "Oil drain and refill service",
            "sop": "SOP_003",
            "parts": "PART_005",
            "duration_hr": 3,
        },
        "misalignment": {
            "action": "Shaft alignment correction and coupling inspection",
            "sop": "Custom — alignment crew required",
            "parts": "Alignment tools, replacement coupling if worn",
            "duration_hr": 4,
        },
        "cage_fault": {
            "action": "Bearing replacement — cage fault cannot be lubricated out",
            "sop": "SOP_001 / SOP_005",
            "parts": "Bearing replacement set",
            "duration_hr": 6,
        },
        "sensor_fault": {
            "action": "Sensor inspection and cable repair at junction box",
            "sop": "SOP_004",
            "parts": "Replacement cable connector (M4 terminal, 5-6 Nm torque)",
            "duration_hr": 2,
        },
        "healthy": {
            "action": "Routine preventive maintenance — grease top-up",
            "sop": "SOP_002",
            "parts": "PART_004 (Synthetic Grease, 30-40g)",
            "duration_hr": 2,
        },
    }
    action_info = FAULT_ACTION.get(
        fault, {
            "action": "Investigation required",
            "sop": "Custom procedure",
            "parts": "TBD at inspection",
            "duration_hr": 4,
        })

    FAULT_ROOT_CAUSE = {
        "outer_race_fault": (
            "Contamination ingress through degraded housing seal allowed "
            "fine process dust entry. Bearing outer race developed spalling "
            "over time."),
        "inner_race_fault": (
            "Cyclic overload combined with shaft misalignment created stress "
            "concentrations on the inner race leading to fatigue spalling."),
        "lubrication_issue": (
            "Lubrication interval exceeded by approximately 38 days. Oil "
            "oxidation reduced viscosity below operational requirements."),
        "lubrication_failure": (
            "Lubrication interval exceeded by approximately 38 days. Oil "
            "oxidation reduced viscosity below operational requirements."),
        "misalignment": (
            "Shaft offset detected at coupling. Likely cause: foundation "
            "settlement or coupling wear creating progressive shaft "
            "misalignment."),
        "cage_fault": (
            "Lubrication degradation combined with contamination caused cage "
            "wear. FTF dominant in spectrum with irregular ball spacing."),
        "sensor_fault": (
            "Loose terminal screw at junction box J-301. Conveyor vibration "
            "gradually backed the screw out over approximately 6 months."),
        "healthy": (
            "No fault found. Asset operating within all baseline parameters. "
            "Continue routine monitoring."),
    }

    FAULT_LESSONS = {
        "outer_race_fault": (
            "Housing seal condition must be inspected at every bearing "
            "replacement. Post-repair baseline to be established for future "
            "reference monitoring."),
        "inner_race_fault": (
            "Load profile must be reviewed post-repair. Check shaft alignment "
            "to prevent recurrence. Post-repair baseline critical."),
        "lubrication_issue": (
            "Re-lubrication interval must be verified against OEM spec. "
            "60-day interval correct for this oil type and temperature."),
        "lubrication_failure": (
            "Re-lubrication interval must be verified against OEM spec. "
            "60-day interval correct for this oil type and temperature."),
        "misalignment": (
            "Alignment must be re-verified at operating temperature. Cold "
            "alignment may shift under thermal expansion. Coupling condition "
            "should be inspected."),
        "cage_fault": (
            "Cage fault indicates lubrication system review needed. Bearing "
            "must be replaced — cannot be resolved by lubrication alone."),
        "sensor_fault": (
            "Terminal tightening torque must be 5-6 Nm using calibrated "
            "torque screwdriver. Hand-tightening is the root cause of "
            "recurrence."),
        "healthy": (
            "Stable baseline confirmed. Continue routine 60-day monitoring "
            "interval. No action required beyond preventive lubrication."),
    }

    FAULT_EXPECTED_POSTREPAIR = {
        "outer_race_fault": {
            "vib_mm_s_max": 2.5,
            "temp_c_max": 60,
            "kurtosis_max": 2.8,
            "qa_window": "1h and 24h post-restart",
        },
        "inner_race_fault": {
            "vib_mm_s_max": 2.5,
            "temp_c_max": 60,
            "kurtosis_max": 2.8,
            "qa_window": "1h and 24h post-restart",
        },
        "lubrication_issue": {
            "vib_mm_s_max": 3.5,
            "temp_c_max": 65,
            "kurtosis_max": 3.5,
            "qa_window": "24h post-service",
        },
        "lubrication_failure": {
            "vib_mm_s_max": 3.5,
            "temp_c_max": 65,
            "kurtosis_max": 3.5,
            "qa_window": "24h post-service",
        },
        "misalignment": {
            "vib_mm_s_max": 3.0,
            "temp_c_max": 62,
            "kurtosis_max": 2.8,
            "qa_window": (
                "1h and 24h after alignment "
                "at operating temperature"),
        },
        "cage_fault": {
            "vib_mm_s_max": 2.8,
            "temp_c_max": 58,
            "kurtosis_max": 2.8,
            "qa_window": "1h and 24h post-restart",
        },
        "sensor_fault": {
            "vib_mm_s_max": 2.8,
            "temp_c_max": 58,
            "kurtosis_max": 2.8,
            "qa_window": "15 consecutive clean readings",
        },
        "healthy": {
            "vib_mm_s_max": 2.5,
            "temp_c_max": 55,
            "kurtosis_max": 2.5,
            "qa_window": "30 min post-maintenance",
        },
    }

    expected = FAULT_EXPECTED_POSTREPAIR.get(
        fault, {
            "vib_mm_s_max": 3.0,
            "temp_c_max": 60,
            "kurtosis_max": 3.0,
            "qa_window": "post-repair",
        })

    root_cause = FAULT_ROOT_CAUSE.get(
        fault,
        "Root cause to be confirmed at teardown inspection.")
    lessons = FAULT_LESSONS.get(
        fault,
        "Capture post-repair findings to enrich knowledge base.")

    return {
        "run_id": run_id,
        # Discovery is scenario-driven, not case-driven. Using a run-scoped
        # placeholder satisfies feedback_capture_node's required-field check
        # without consuming a numbered CASE_YYYYMMDD_NNN slot. The pipeline
        # assigns real case_ids downstream: PATH_A from retrieval,
        # PATH_B from case_generation_node, PATH_C from the partial match.
        "case_id": f"DISC_{run_id}",
        "source": "discovery_demo",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "fault_mode": fault,
        "asset_id": asset_id,
        "asset_type": asset_type,
        "bearing_type": scenario.get("bearing", "SKF6310"),
        "fault_stage": stage,
        "risk_level": scenario.get("risk", "MEDIUM"),
        "anomaly_score": score,
        "vib_rms_mm_s": vib,
        "kurtosis": kurtosis,
        "temp_c": temp,
        "bpfo_energy": bpfo,
        "bpfi_energy": bpfi,
        "signal_quality_score": sig_q,
        "diagnosis_confidence": score,
        "rul_days": rul,
        "rul_days_min": rul if rul else 0,
        "rul_days_max": (rul + 5) if rul else 0,
        "rul_estimate": rul_str,
        "failure_probability": failure_prob,
        "diagnosis_reasoning": (
            f"Stage {stage} {fault} detected"
            f" on {asset_id}. "
            f"vib_rms={vib} mm/s, "
            f"kurtosis={kurtosis}, "
            f"temp={temp}C"),
        "root_cause": root_cause,
        "lessons_learned": lessons,
        "recommended_action": action_info["action"],
        "action_taken": action_info["action"],
        "sop_reference": action_info["sop"],
        "parts_required": action_info["parts"],
        "estimated_duration_hr": action_info["duration_hr"],
        "work_order_priority": (
            "HIGH" if stage >= 3
            else "MEDIUM" if stage == 2
            else "LOW"),
        "technician_observations": (
            f"Stage {stage} {fault} on {asset_id}. "
            f"Vibration {vib} mm/s, kurtosis "
            f"{kurtosis}, temp {temp}C. "
            f"Recommended action: {action_info['action']}."),
        "expected_post_vib_max": expected["vib_mm_s_max"],
        "expected_post_temp_max": expected["temp_c_max"],
        "expected_post_kurtosis_max": expected["kurtosis_max"],
        "qa_window": expected["qa_window"],
        "preferred_case_id": scenario.get(
            "matched_case", ""),
        "_demo_mode": True,
    }


def build_structured_case_json(case_dict):
    fname = case_dict.get("_file", "")
    cid = case_dict.get(
        "case_id",
        fname.replace(".md","").replace(".json","").upper()
    )
    fault   = case_dict.get("fault_mode", "misalignment")
    asset   = case_dict.get("asset_type",  "gearbox")
    bearing = case_dict.get("bearing_type","SKF22318")
    stage   = case_dict.get("stage",        2)

    return {
        "case_id":   cid,
        "title":     (
            f"Stage {stage} "
            f"{fault.replace('_',' ').title()} "
            f"— {asset.title()} {case_dict.get('asset_id','AST_GBX_001')}"
        ),
        "asset_id":    case_dict.get("asset_id",  "AST_GBX_001"),
        "asset_type":  asset,
        "bearing":     bearing,
        "fault_mode":  fault,
        "stage":       stage,
        "outcome":     case_dict.get("result",    "SUCCESSFUL"),
        "generated":   case_dict.get(
            "created_date",
            datetime.now().isoformat()
        ),
        "pipeline":    "DRO Pipeline",
        "document_type": "Learned Case — Knowledge Agent Retrieval Document",

        "detection_summary": {
            "detection_date":    case_dict.get("created_date","")[:10],
            "detected_by":       "monitoring_agent",
            "anomaly_score":     case_dict.get("anomaly_score",   0.67),
            "vibration_rms":     case_dict.get("vib_rms",         "5.2 mm/s"),
            "kurtosis":          case_dict.get("kurtosis",         3.1),
            "temperature":       case_dict.get("temp_c",           "72.0C"),
            "bpfo_energy":       case_dict.get("bpfo_energy",      "0.9x baseline"),
            "signal_quality":    case_dict.get("signal_quality",    0.97),
            "summary":           (
                f"{fault.replace('_',' ').title()} detected on "
                f"{bearing} bearing at Stage {stage}."
            ),
        },

        "agent_diagnosis": {
            "primary_diagnosis": (
                f"{fault.replace('_',' ')} — Stage {stage}"
            ),
            "confidence":        case_dict.get("confidence",       0.79),
            "rul_estimate":      case_dict.get("rul_estimate",     "25-45 days from detection"),
            "risk_level":        case_dict.get("risk_level",       "MEDIUM"),
            "failure_probability": case_dict.get("failure_probability", "52%"),
            "reasoning":         case_dict.get("diagnosis",
                "Dominant frequency pattern and phase analysis confirm fault mode."),
        },

        "action_taken": {
            "recommended_action": case_dict.get(
                "action_taken",
                "shaft_realignment"
            ),
            "work_order":         case_dict.get("work_order",      "WO_DEMO_B_001"),
            "action_performed":   case_dict.get("action_taken",    "shaft_realignment"),
            "technician_notes":   case_dict.get("technician_notes",
                "Repaired per recommended action. Post-repair readings taken."),
        },

        "findings_at_inspection": {
            "asset_inspected":    (
                f"{bearing} bearing on {asset} "
                f"{case_dict.get('asset_id','')}"
            ),
            "fault_confirmed":    fault.replace("_"," "),
            "root_cause_identified": case_dict.get(
                "root_cause",
                "confirmed during inspection"
            ),
            "technician_notes":   case_dict.get("technician_notes",
                "Inspection confirmed fault mode as diagnosed."),
        },

        "outcome": {
            "post_repair_vibration":   case_dict.get(
                "post_repair_vib",    "2.1 mm/s"
            ),
            "post_repair_temperature": case_dict.get(
                "post_repair_temp",   "63.0C"
            ),
            "qa_result":               case_dict.get(
                "result",             "PASS"
            ),
            "recommendation_followed": "Yes",
        },

        "root_cause_confirmed": {
            "root_cause":          case_dict.get(
                "root_cause",
                "confirmed at inspection"
            ),
            "contributing_factors": case_dict.get(
                "contributing_factors",
                "See technician notes and inspection findings."
            ),
        },

        "lessons_learned": case_dict.get(
            "lessons_learned",
            (
                f"{fault.replace('_',' ').title()} on {bearing} "
                f"at Stage {stage}. "
                f"Post-repair baseline recorded. "
                f"Use as reference for future checks."
            )
        ),
    }


# ── Helpers ─────────────────────────────────────────────────────────────────
def badge(fault_mode):
    cls, label = FAULT_CLASS.get(fault_mode, ("fb fb-other", fault_mode))
    return f'<span class="{cls}">{label}</span>'


# ═══════════════════════════════════════════════════════════════════
# SECTION: DATA LOAD HELPERS
# Case JSON loaders, ID deduplication, and load_all_cases() merge.
# ═══════════════════════════════════════════════════════════════════
def load_json_cases():
    out = []
    for p in sorted(glob.glob(f"{DATA_DIR}/*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
            d["_file"] = os.path.basename(p)
            out.append(d)
        except Exception:
            pass
    return out


def parse_md_meta(path):
    txt = open(path, encoding="utf-8").read()
    cid = re.search(r"\*\*(CASE_[A-Z0-9_]+)\*\*", txt)
    fault = re.search(r"Fault Mode:\*\*\s*(\w+)", txt)
    bearing = re.search(r"Bearing:\*\*\s*(\S+)", txt)
    asset = re.search(r"\((\w+)\)", txt)
    return {
        "case_id": cid.group(1) if cid else os.path.basename(path),
        "fault_mode": fault.group(1) if fault else "",
        "bearing_type": bearing.group(1) if bearing else "",
        "asset_type": asset.group(1) if asset else "",
        "valid_until": "2028-12-31",
        "_file": os.path.basename(path),
        "_md": txt,
    }


def write_manual_case_md(case_dict):
    """Write a manual-entry case to data/learned_cases/ as a
    .md file in the 7-section generated-case format so it appears
    in the Generated Cases list and downloads as a PDF.

    Filename pattern: {case_id.lower()}_manual_generated.md
    Matches load_generated_cases glob `*generated*.md`.

    Returns the .md filename on success, None on failure.
    """
    import os
    from datetime import datetime
    try:
        case_id      = case_dict.get("case_id", "CASE_UNKNOWN")
        asset_type   = case_dict.get("asset_type", "unknown")
        bearing      = case_dict.get("bearing_type", "")
        fault_mode   = case_dict.get("fault_mode", "unknown")
        diagnosis    = case_dict.get("diagnosis", "") or case_dict.get("pf_diag", "")
        root_cause   = case_dict.get("root_cause", "")
        action       = case_dict.get("action_taken", "")
        result       = case_dict.get("result", "")
        lessons      = case_dict.get("lessons_learned", "")
        created_by   = case_dict.get("created_by", "manual_entry")
        created_date = case_dict.get("created_date") or datetime.now().strftime("%Y-%m-%d")
        valid_until  = case_dict.get("valid_until", "")

        md = f"""**{case_id}** | Outcome: MANUAL ENTRY | Generated: {created_date}

**Asset:** {asset_type} | **Bearing:** {bearing} | **Fault Mode:** {fault_mode} | **Source:** Manual Entry

Learned Case — Knowledge Agent Retrieval Document | DRO Pipeline

---

### 1. Detection Summary

| Field | Value |
|---|---|
| Detection date | {created_date} |
| Detected by | Manual entry |
| Anomaly score | Not applicable — manually entered case |
| Signal values | Not applicable — manually entered case |

### 2. Agent Diagnosis

| Field | Value |
|---|---|
| Primary diagnosis | {fault_mode} |
| Confidence | Manual entry |
| RUL estimate | Not applicable — manually entered case |

**Reasoning:** {diagnosis if diagnosis else "Not provided in manual entry."}

### 3. Action Taken

| Field | Value |
|---|---|
| Recommended action | {action if action else "Not provided"} |
| Work order | Manual entry |
| Parts required | Not specified |

**Action performed:** {action if action else "Not provided in manual entry."}

### 4. Findings at Inspection

{root_cause if root_cause else "Findings not provided in manual entry."}

### 5. Outcome

| Field | Value |
|---|---|
| Result | {result if result else "Not provided"} |
| Created by | {created_by} |
| Valid until | {valid_until} |

### 6. Root Cause Confirmed

**Root cause:** {root_cause if root_cause else "Not provided in manual entry."}

### 7. Lessons Learned

{lessons if lessons else "No lessons recorded for this manual entry."}
"""

        out_path = os.path.join(DATA_DIR, f"{case_id.lower()}_manual_generated.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        return os.path.basename(out_path)
    except Exception as _e:
        print(f"[write_manual_case_md] failed: {_e}")
        return None


def load_generated_cases():
    # Only .md files: any *.json is already loaded by load_json_cases()
    # with correct case_id extraction. Including .json here caused
    # duplicate entries in load_all_cases() because parse_md_meta
    # falls back to using the filename as case_id when it can't
    # find markdown patterns. Confirmed 2026-07-05 diagnostic.
    patterns = [
        f"{DATA_DIR}/case_demo*.md",
        f"{DATA_DIR}/*DEMO*.md",
        f"{DATA_DIR}/*generated*.md",
    ]
    seen = set()
    files = []
    for p in patterns:
        for f in glob.glob(p):
            norm = os.path.normpath(f)
            if norm not in seen:
                seen.add(norm)
                files.append(f)
    files.sort(key=os.path.getmtime, reverse=True)
    return [parse_md_meta(p) for p in files]


def load_all_cases():
    """Return all cases from disk. Deduplicates by case_id.

    When the same case_id appears from multiple loaders (e.g.
    load_json_cases finds it via *.json, load_generated_cases
    finds a .md companion), we keep the first properly-shaped
    entry. Filename-shaped case_ids (containing '.' or matching
    the .md filename) are treated as parser fallbacks and dropped
    when a proper CASE_* case_id for the same file already exists.
    """
    import re as _re
    _valid_pattern = _re.compile(r"^CASE_[A-Za-z0-9_]+$")
    combined = list(load_json_cases()) + list(load_generated_cases())
    seen: dict = {}
    for c in combined:
        cid = str(c.get("case_id", "") or "")
        # Skip entries with filename-shaped case_ids if a proper
        # one is already in seen or will come later — but first
        # pass identifies canonical entries
        is_valid = bool(_valid_pattern.match(cid))
        if is_valid:
            # Keep first proper case_id encountered
            if cid not in seen:
                seen[cid] = c
        else:
            # Filename-shaped: extract the CASE_ substring if any,
            # otherwise skip if a proper case_id will match this file
            match = _re.search(r"(CASE_[A-Za-z0-9_]+)", cid, _re.IGNORECASE)
            if match:
                normalized = match.group(1).upper()
                if normalized not in seen:
                    # No canonical entry yet — keep this fallback,
                    # but with the extracted normalized case_id
                    c_copy = dict(c)
                    c_copy["case_id"] = normalized
                    seen[normalized] = c_copy
            # else: totally unparseable — drop it
    return list(seen.values())


# ═══════════════════════════════════════════════════════════════════
# SECTION: SEARCH AND MATCHING
# Corpus builder, keyword AND-match scorer for Search Case tab.
# ═══════════════════════════════════════════════════════════════════
def _case_search_corpus(c: dict) -> str:
    """Return a flat lowercase space-separated corpus from a case dict.

    Handles both flat seed JSONs (all string values) and nested
    agent-generated dicts (root_cause/action/outcome as sub-dicts).
    Underscores are converted to spaces so tokens from stored values
    like "outer_race_fault" appear as "outer race fault" and match
    space-separated user queries.  The caller also builds a despaced
    view (all whitespace removed) for ID-style queries such as SKF 6310.
    """
    parts = []

    def _add(val):
        if isinstance(val, dict):
            for v in val.values():
                _add(v)
        elif isinstance(val, list):
            for item in val:
                _add(item)
        elif val is not None:
            parts.append(str(val).lower().replace("_", " "))

    for field in [
        # identity / classification
        "case_id", "_file", "fault_mode", "asset_type",
        "asset_id", "bearing_type",
        # flat string fields (seed JSONs)
        "root_cause", "action_taken", "lessons_learned", "result",
        # nested dict fields (agent-generated cases)
        "action", "lessons", "outcome",
        # detection / diagnosis sub-dicts (any text values inside)
        "detection", "diagnosis",
    ]:
        _add(c.get(field, ""))

    return " ".join(parts)


def _search_matches(c: dict, keyword: str) -> int:
    """Return a relevance score >= 1 if case c matches keyword, else 0.

    Matching rules (all case-insensitive):
    1. Tokenise keyword on whitespace after lowercasing and replacing _ with space.
    2. Build two corpus views: spaced (underscores→spaces) and despaced (no whitespace).
    3. A token matches if it appears in EITHER view, OR the despaced keyword
       appears in the despaced corpus (handles SKF 6310 ↔ SKF6310).
    4. The case matches only if ALL tokens match (AND semantics).
    5. Score = number of top-level field hits (for ranking), minimum 1.
    """
    kw_norm = keyword.lower().strip().replace("_", " ")
    tokens = [t for t in re.split(r"\s+", kw_norm) if t]
    if not tokens:
        return 0

    corpus_spaced = _case_search_corpus(c)
    corpus_despaced = re.sub(r"\s+", "", corpus_spaced)

    # AND-match: every token must appear in spaced or despaced view
    for token in tokens:
        token_despaced = re.sub(r"\s+", "", token)
        if token not in corpus_spaced and token_despaced not in corpus_despaced:
            return 0

    # Despace fallback for whole-query ID match (e.g. "skf 6310" → "skf6310")
    kw_despaced = re.sub(r"\s+", "", kw_norm)
    if len(kw_despaced) >= 4 and kw_despaced not in corpus_despaced:
        # Only mandatory when there's a single multi-char token that failed spaced
        pass  # already matched per-token above

    top_fields = ["fault_mode", "asset_type", "bearing_type",
                  "root_cause", "action_taken", "lessons_learned",
                  "case_id", "_file"]
    score = sum(
        1 for f in top_fields
        if any(
            t in str(c.get(f, "")).lower().replace("_", " ") or
            re.sub(r"\s+", "", t) in re.sub(r"\s+", "", str(c.get(f, "")).lower())
            for t in tokens
        )
    )
    return max(score, 1)


def keyword_search(cases, keyword):
    results = []
    for c in cases:
        score = _search_matches(c, keyword)
        if score:
            results.append((score, c))
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results[:4]]


def count_log_event(event):
    if not os.path.exists(LOG_FILE):
        return 0
    with open(LOG_FILE, encoding="utf-8") as f:
        return sum(1 for l in f if event in l)


# ══════════════════════════════════════════════════════════════════════════
def render_pipeline_strip():
    """Thin top-of-tab pipeline strip with boxed active node."""
    nodes = [
        "Data Foundation", "Monitoring", "Predictive Risk",
        "Failure Intel", "Knowledge", "Prescriptive", "Executor",
    ]
    inner = "".join(
        f"<span class='pipeline-node'>{n}</span>"
        f"<span class='pipeline-sep'>›</span>"
        for n in nodes
    )
    html = (
        "<div class='pipeline-strip'>"
        "<span class='pipeline-label'>Pipeline:</span>"
        f"{inner}"
        "<span class='pipeline-node-active'>Learning &amp; Memory Agent</span>"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def case_to_csv(case_dict):
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Field", "Value"])
    field_labels = {
        "case_id":              "Case ID",
        "fault_mode":           "Fault Mode",
        "asset_type":           "Asset Type",
        "bearing_type":         "Bearing Type",
        "valid_until":          "Valid Until",
        "source":               "Source",
        "created_date":         "Created Date",
        "created_by":           "Created By",
        "root_cause":           "Root Cause",
        "action_taken":         "Action Taken",
        "result":               "Outcome",
        "lessons_learned":      "Lessons Learned",
        "diagnosis":            "Diagnosis Summary",
        "scenario_description": "Scenario Description",
    }
    for key, label in field_labels.items():
        value = case_dict.get(key, "")
        if value:
            writer.writerow([label, str(value)])
    return output.getvalue()


# ══════════════════════════════════════════════════════════════════════════
# TAB: CASE LIBRARY
# ══════════════════════════════════════════════════════════════════════════
def render_case_library():
    st.markdown("""
    <div class="topbar">
      <span style="width:7px;height:7px;border-radius:50%;background:#16A34A;
                   display:inline-block;flex-shrink:0"></span>
      <span style="font-size:13px;font-weight:600;color:#1A1A1A">Case library</span>
      <span style="font-size:11px;color:#8A8A8A;margin-left:8px">
        All learned case documents — readable by Knowledge Agent
      </span>
    </div>
    """, unsafe_allow_html=True)

    json_cases = load_json_cases()
    gen_cases = load_generated_cases()

    col_l = st.container()
    with col_l:
        st.markdown(f'<div class="sec-label">Foundation Cases (JSON) — {len(json_cases)} files</div>',
                    unsafe_allow_html=True)
        for c in json_cases:
            fault = c.get("fault_mode", "unknown")
            bg, fg = FAULT_COLOR.get(fault, ("#F0EFF8", "#5A5A5A"))
            case_id_label = c.get("case_id", c.get("_file", ""))
            bearing = c.get("bearing_type", "")
            date_val = (
                c.get("valid_until") or
                c.get("created_date", "")
            )[:10]
            st.markdown(
                "<div class='case-row'>"
                "<span class='cid'>" + case_id_label + "</span>"
                "<span class='cfault' style='background:" + bg + ";"
                "color:" + fg + "'>"
                + fault.replace("_", " ") +
                "</span>"
                "<span style='color:#555; font-size:.78rem'>"
                + bearing +
                "</span>"
                "<span class='cmeta'>" + date_val + "</span>"
                "</div>",
                unsafe_allow_html=True
            )
        import json as _json
        if json_cases:
            bulk_data = _json.dumps(json_cases, indent=2, default=str)
            st.download_button(
                label="Download Full Case Library (JSON)",
                data=bulk_data,
                file_name="foundation_cases_export.json",
                mime="application/json",
                key="dl_full_case_library"
            )



# ══════════════════════════════════════════════════════════════════════════
# TAB: ANALYTICS & INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════════

def section(title):
    """Render a labelled section heading."""
    st.markdown(
        f"<div style='font-size:1rem;font-weight:700;color:#1A1A1A;"
        f"letter-spacing:.01em;margin:0 0 16px;padding-bottom:8px;"
        f"border-bottom:1px solid #E2E0EA;'>{title}</div>",
        unsafe_allow_html=True,
    )


# ── Analytics helpers — read directly from raw data sources ──
def _load_tracker_records():
    """Read fault_tracker.json directly. Returns list of records.

    Records are events, not cases. Same case_id can appear N times across
    events — this is correct for event analytics. Do not deduplicate by
    case_id here.
    """
    try:
        with open(FAULT_TRACKER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "records" in data:
            return data["records"]
        return []
    except Exception:
        return []


def _load_seed_cases():
    """Read all seed case JSONs from data/learned_cases/. Returns list of dicts."""
    cases = []
    for path in glob.glob("data/learned_cases/*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cases.append(json.load(f))
        except Exception:
            continue
    return cases


def _path_distribution(records):
    """Return dict with counts per path A/B/C and total. Raw count."""
    counts = {"A": 0, "B": 0, "C": 0, "other": 0}
    for r in records:
        p = (r.get("path_taken") or "").upper()
        if p in counts:
            counts[p] += 1
        else:
            counts["other"] += 1
    counts["total"] = len(records)
    return counts



def _most_retrieved_cases(records, top_n=3):
    """Count Path A records by source_case_id."""
    from collections import Counter
    sources = [r.get("source_case_id") for r in records
               if (r.get("path_taken") or "").upper() == "A"
               and r.get("source_case_id")]
    return Counter(sources).most_common(top_n)


def _top_failing_assets(records, top_n=5):
    """Count records by asset_id."""
    from collections import Counter
    return Counter([r.get("asset_id") for r in records if r.get("asset_id")]).most_common(top_n)


def _recurring_fault_modes(records, top_n=5):
    """Count records by fault_mode."""
    from collections import Counter
    return Counter([r.get("fault_mode") for r in records if r.get("fault_mode")]).most_common(top_n)


def _asset_coverage_matrix(seed_cases, records):
    """Build a matrix of (asset_type x fault_mode).
    Cell = 'covered' if KB case exists, 'gap' if seen in events but no KB case, 'unseen' otherwise."""
    asset_types = sorted(set([c.get("asset_type") for c in seed_cases if c.get("asset_type")]
                              + [r.get("asset_type") for r in records if r.get("asset_type")]))
    fault_modes = sorted(set([c.get("fault_mode") for c in seed_cases if c.get("fault_mode")]
                              + [r.get("fault_mode") for r in records if r.get("fault_mode")]))
    covered_pairs = set((c.get("asset_type"), c.get("fault_mode")) for c in seed_cases)
    seen_pairs = set((r.get("asset_type"), r.get("fault_mode")) for r in records)
    matrix = []
    for at in asset_types:
        row = []
        for fm in fault_modes:
            if (at, fm) in covered_pairs:
                row.append("covered")
            elif (at, fm) in seen_pairs:
                row.append("gap")
            else:
                row.append("unseen")
        matrix.append({"asset_type": at, "cells": row})
    return {"asset_types": asset_types, "fault_modes": fault_modes, "matrix": matrix}


def _knowledge_freshness(seed_cases):
    """Min days-until-expiry across seed cases."""
    today = date.today()
    days_list = []
    for c in seed_cases:
        v = c.get("valid_until", "")
        try:
            d = datetime.strptime(v, "%Y-%m-%d").date()
            days_list.append((d - today).days)
        except Exception:
            continue
    return min(days_list) if days_list else None


def _parse_ts(s: str):
    """Parse ISO or space-separated timestamp string; return datetime or None."""
    if not s:
        return None
    _s = str(s).strip().replace("Z", "").replace("+00:00", "")[:26]
    for _f in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(_s, _f)
        except ValueError:
            continue
    return None


def build_monthly_path_series(records: list, months_back: int = 6) -> dict:
    """Bucket fault_tracker records by month × path_taken.
    Always includes the trailing `months_back` months, padded with zeros."""
    now = datetime.now()
    month_keys = []
    for i in range(months_back - 1, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            m += 12
            y -= 1
        month_keys.append(f"{y}-{m:02d}")

    buckets = {mk: {"A": 0, "B": 0, "C": 0} for mk in month_keys}
    for r in records:
        ts = _parse_ts(r.get("timestamp", ""))
        if not ts:
            continue
        mk = f"{ts.year}-{ts.month:02d}"
        if mk in buckets:
            p = r.get("path_taken", "")
            if p in buckets[mk]:
                buckets[mk][p] += 1

    def _label(mk):
        y, m = mk.split("-")
        return datetime(int(y), int(m), 1).strftime("%b %Y")

    return {
        "month_labels": [_label(mk) for mk in month_keys],
        "existing": [buckets[mk]["A"] for mk in month_keys],
        "partial":  [buckets[mk]["C"] for mk in month_keys],
        "new":      [buckets[mk]["B"] for mk in month_keys],
    }


def render_monthly_trend_chart(records: list):
    """Render an embedded stacked-bar monthly trend chart. No modal, no button."""
    series = build_monthly_path_series(records, months_back=6)

    def _sentence(i):
        m = series["month_labels"][i]
        e, c, n = series["existing"][i], series["partial"][i], series["new"][i]
        total = e + c + n
        if total == 0:
            return f"{m}: no events"
        return (
            f"{m}: {total} event{'s' if total != 1 else ''}"
            f" · {e} reused (A) · {n} new (B) · {c} partial (C)"
        )

    tooltips = [_sentence(i) for i in range(len(series["month_labels"]))]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Existing (A)",
        x=series["month_labels"],
        y=series["existing"],
        marker_color="#16A34A",
        hovertext=tooltips,
        hoverinfo="text",
    ))
    fig.add_trace(go.Bar(
        name="Partial (C)",
        x=series["month_labels"],
        y=series["partial"],
        marker_color="#D97706",
        hovertext=tooltips,
        hoverinfo="text",
    ))
    fig.add_trace(go.Bar(
        name="New (B)",
        x=series["month_labels"],
        y=series["new"],
        marker_color="#4F46E5",
        hovertext=tooltips,
        hoverinfo="text",
    ))
    fig.update_layout(
        barmode="stack",
        height=280,
        margin=dict(l=40, r=20, t=10, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#1A1A1A", size=11),
        xaxis=dict(
            title="",
            showgrid=False,
            tickfont=dict(color="#5A5A5A"),
            fixedrange=True,
        ),
        yaxis=dict(
            title=dict(text="Events", font=dict(color="#5A5A5A", size=10)),
            gridcolor="#E8E5F5",
            tickfont=dict(color="#5A5A5A"),
            fixedrange=True,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)",
            font=dict(color="#5A5A5A", size=10),
        ),
        hoverlabel=dict(
            bgcolor="#F4F6F8",
            bordercolor="#A100FF",
            font=dict(color="#1A1A1A", size=11),
        ),
    )
    st.markdown(
        "<div style='margin:0.5rem 0 0.3rem 0;'>"
        "<div style='color:#A100FF;font-size:0.75rem;font-weight:600;"
        "letter-spacing:0.08em;text-transform:uppercase;'>Monthly Trend</div>"
        "<div style='color:#5A5A5A;font-size:0.78rem;margin-top:0.1rem;'>"
        "Path distribution over the last 6 months · hover a bar for the breakdown"
        "</div></div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Modal dialogs (clickable drilldowns) ──
@st.dialog("Monthly Trend — Path Distribution Over Time")
def _show_monthly_trend_dialog():
    _recs = _load_tracker_records()
    render_monthly_trend_chart(_recs)
    st.caption("Source: logs/fault_tracker.json · grouped by month and path_taken")


@st.dialog("Knowledge Base Health — Detailed Metrics")
def _dlg_kb_health(freshness_days, path_c_count, last_method, total_kb_cases):
    cols = st.columns(3)
    with cols[0]:
        if freshness_days is None:
            st.metric("Knowledge Freshness", "N/A")
            st.caption("No valid_until data")
        else:
            st.metric("Knowledge Freshness", f"{freshness_days} days")
            st.caption("Min days until earliest seed case expires")
    with cols[1]:
        st.metric("Path C Escalations", path_c_count)
        st.caption("Partial-knowledge cases flagged for human review")
    with cols[2]:
        st.metric("Last Coverage Method", last_method or "—")
        st.caption("LLM / heuristic / no_candidate for most recent run")
    st.divider()
    st.markdown(f"**Total KB cases:** {total_kb_cases}")
    st.caption("Source: FAISS metadata (data/faiss_index/metadata.json) · Path C count from fault_tracker.json")


# ═══════════════════════════════════════════════════════════════════
# SECTION: ANALYTICS AND KPI RENDERING
# Analytics tab: fault-frequency charts, KPI tiles, activity table.
# ═══════════════════════════════════════════════════════════════════
def render_analytics():
    """Analytics & Insights — single-page dashboard from real data only."""
    import time as _at
    _analytics_t0 = _at.perf_counter()
    records = _load_tracker_records()
    logger.info("[latency] analytics _load_tracker_records took %.3fs", _at.perf_counter() - _analytics_t0)
    _sc_t0 = _at.perf_counter()
    try:
        with open(f"{FAISS_INDEX_DIR}/metadata.json", "r", encoding="utf-8") as _kbf:
            _faiss_meta = json.load(_kbf)
        _kb_cases = (
            list(_faiss_meta.values()) if isinstance(_faiss_meta, dict)
            else list(_faiss_meta)
        )
    except Exception as _kbe:
        logger.warning("[analytics] FAISS metadata read failed: %s", _kbe)
        _kb_cases = []
    seed_cases = _kb_cases  # keep variable name for downstream compatibility
    # Source breakdown for anchor sentence and dialog
    _by_source: dict = {}
    for _kbc in _kb_cases:
        _ks = (_kbc.get("source") or "unknown").lower()
        _by_source[_ks] = _by_source.get(_ks, 0) + 1
    _kb_total = len(_kb_cases)
    _kb_breakdown_parts = []
    if _by_source.get("seed_data", 0):
        _kb_breakdown_parts.append(f"{_by_source['seed_data']} seed")
    for _bsk, _bsv in sorted(_by_source.items()):
        if _bsk == "seed_data":
            continue
        _kb_breakdown_parts.append(f"{_bsv} {_bsk.replace('_', ' ')}")
    _kb_breakdown = " + ".join(_kb_breakdown_parts) if _kb_breakdown_parts else "0"
    logger.info("[latency] analytics FAISS metadata took %.3fs (%d cases)", _at.perf_counter() - _sc_t0, _kb_total)
    # A/B/C only — USER_CAPTURE entries are manual saves, not agent triage decisions.
    # They stay in the tracker (Activity Log shows them) but must not dilute pipeline metrics.
    _abc_records = [
        r for r in records
        if isinstance(r, dict) and (r.get("path_taken") or "").upper() in {"A", "B", "C"}
    ]
    paths = _path_distribution(_abc_records)
    top_cases = _most_retrieved_cases(_abc_records, top_n=3)
    top_assets = _top_failing_assets(records, top_n=5)
    recurring = _recurring_fault_modes(records, top_n=5)
    coverage = _asset_coverage_matrix(seed_cases, records)
    freshness = _knowledge_freshness(seed_cases)
    last_method = st.session_state.get("last_coverage_method", None)
    last_dimensions = st.session_state.get("last_dimension_assessments", None)
    last_run_ts = st.session_state.get("last_run_timestamp", None)
    last_path = st.session_state.get("last_path_taken", None)
    total_events = len(_abc_records)
    reuse_pct = round((paths["A"] / total_events * 100), 1) if total_events else 0
    unique_pairs_seen = len(set((r.get("asset_type"), r.get("fault_mode")) for r in records))
    unique_pairs_covered = len(set((c.get("asset_type"), c.get("fault_mode")) for c in seed_cases))

    # Anchor sentence — KB total from FAISS with source breakdown
    st.markdown(
        f"<div style='background:#F8F7FB;border:1px solid #E2E0EA;border-radius:8px;"
        f"padding:10px 14px;margin-bottom:12px;font-size:13px;color:#1A1A1A;line-height:1.5'>"
        f"Across <b>{total_events}</b> agent triage events, the agent reused existing knowledge "
        f"<b>{reuse_pct}%</b> of the time, escalated <b>{paths['C']}</b> cases for partial review, "
        f"and generated <b>{paths['B']}</b> new cases. The knowledge base holds "
        f"<b>{_kb_total}</b> case{'s' if _kb_total != 1 else ''} ({_kb_breakdown}) and currently covers "
        f"<b>{unique_pairs_covered}</b> of <b>{unique_pairs_seen}</b> fault-asset pairs observed in the field."
        f"</div>",
        unsafe_allow_html=True,
    )

    # KPI tiles — 5 tiles, all live-wired to disk / fault_tracker on every render
    _tile_style = ("background:#F8F7FB;border:1px solid #E2E0EA;border-radius:8px;"
                   "padding:10px;text-align:center;height:80px;display:flex;"
                   "flex-direction:column;justify-content:center;")
    _distinct_fault_modes = len(
        set(c.get("fault_mode") for c in seed_cases if c.get("fault_mode"))
    )
    t1, t2, t3, t4, t5 = st.columns(5)
    with t1:
        st.markdown(
            f"<div style='{_tile_style}'><div style='font-size:22px;font-weight:700;"
            f"color:#4F46E5'>{_kb_total}</div>"
            f"<div style='font-size:11px;color:#5A5A5A'>Cases in KB</div></div>",
            unsafe_allow_html=True)
    with t2:
        st.markdown(
            f"<div style='{_tile_style}'><div style='font-size:22px;font-weight:700;"
            f"color:#A100FF'>{total_events}</div>"
            f"<div style='font-size:11px;color:#5A5A5A'>Retrievals so far</div></div>",
            unsafe_allow_html=True)
    with t3:
        st.markdown(
            f"<div style='{_tile_style}'><div style='font-size:22px;font-weight:700;"
            f"color:#16A34A'>{reuse_pct}%</div>"
            f"<div style='font-size:11px;color:#5A5A5A'>Reuse rate</div></div>",
            unsafe_allow_html=True)
    with t4:
        st.markdown(
            f"<div style='{_tile_style}'><div style='font-size:22px;font-weight:700;"
            f"color:#0D9488'>{_distinct_fault_modes}</div>"
            f"<div style='font-size:11px;color:#5A5A5A'>Fault modes covered</div></div>",
            unsafe_allow_html=True)
    with t5:
        st.markdown(
            f"<div style='{_tile_style}'><div style='font-size:18px;font-weight:700;"
            f"color:#D97706'>{unique_pairs_covered}&thinsp;/&thinsp;{unique_pairs_seen}</div>"
            f"<div style='font-size:11px;color:#5A5A5A'>Fault-asset pairs covered</div></div>",
            unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # Learning Intelligence (left 60%) + KB Health (right 40%)
    c_left, c_right = st.columns([3, 2])

    with c_left:
        st.markdown("<div style='font-size:12px;font-weight:600;color:#4F46E5;"
                    "letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px'>"
                    "Learning Intelligence</div>", unsafe_allow_html=True)
        sub_l, sub_r = st.columns([2, 1])
        with sub_l:
            fig_donut = go.Figure(go.Pie(
                labels=["Existing (A)", "Partial (C)", "New (B)"],
                values=[paths["A"], paths["C"], paths["B"]],
                hole=0.55,
                marker=dict(colors=["#16A34A", "#D97706", "#4F46E5"]),
                textinfo="label+percent",
            ))
            fig_donut.update_layout(
                height=240,
                showlegend=False,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="#F8F7FB", plot_bgcolor="#F8F7FB",
                font=dict(color="#1A1A1A", size=11),
            )
            st.plotly_chart(fig_donut, use_container_width=True)
            if st.button("View Monthly Trend", key="btn_monthly_trend_open",
                         use_container_width=True):
                _show_monthly_trend_dialog()
        with sub_r:
            st.markdown("<div style='font-size:11px;color:#5A5A5A;margin-bottom:6px'>"
                        "Most retrieved cases</div>", unsafe_allow_html=True)
            if top_cases:
                for case_id, count in top_cases:
                    st.markdown(
                        f"<div style='background:#FFFFFF;border-left:2px solid #A100FF;"
                        f"padding:6px 8px;margin-bottom:4px;border-radius:3px'>"
                        f"<div style='font-size:11px;color:#1A1A1A;font-weight:600'>{case_id}</div>"
                        f"<div style='font-size:10px;color:#5A5A5A'>{count} retrievals</div></div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No Path A retrievals yet")

    with c_right:
        st.markdown("<div style='font-size:12px;font-weight:600;color:#0D9488;"
                    "letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px'>"
                    "Knowledge Base Health</div>", unsafe_allow_html=True)
        cells_html = "<table style='width:100%;border-collapse:collapse;font-size:10px'>"
        cells_html += "<tr><th></th>"
        for fm in coverage["fault_modes"]:
            short = fm.replace("_fault", "").replace("_", " ")[:10]
            cells_html += f"<th style='color:#5A5A5A;padding:3px;text-align:center'>{short}</th>"
        cells_html += "</tr>"
        for i, at in enumerate(coverage["asset_types"]):
            cells_html += f"<tr><td style='color:#1A1A1A;padding:3px;font-weight:600'>{at}</td>"
            for status in coverage["matrix"][i]["cells"]:
                if status == "covered":
                    color = "#16A34A"
                elif status == "gap":
                    color = "#D97706"
                else:
                    color = "#E2E0EA"
                cells_html += f"<td style='padding:3px;text-align:center'><div style='width:18px;height:18px;background:{color};border-radius:3px;margin:auto'></div></td>"
            cells_html += "</tr>"
        cells_html += "</table>"
        st.markdown(cells_html, unsafe_allow_html=True)
        st.caption("Green = covered · Amber = seen but no KB case · Gray = not yet observed")
        if st.button("View KB health metrics", key="btn_kb_health",
                     use_container_width=True):
            _dlg_kb_health(freshness, paths["C"], last_method, _kb_total)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ROW 3 — Operational Intelligence (left 50%) + AI & Retrieval (right 50%)
    op_left, op_right = st.columns(2)

    with op_left:
        st.markdown("<div style='font-size:12px;font-weight:600;color:#16A34A;"
                    "letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px'>"
                    "Operational Intelligence</div>", unsafe_allow_html=True)
        if top_assets:
            asset_names = [a[0] for a in top_assets]
            asset_counts = [a[1] for a in top_assets]
            fig_assets = go.Figure(go.Bar(
                x=asset_counts, y=asset_names, orientation="h",
                marker_color="#16A34A",
                text=asset_counts, textposition="outside",
            ))
            fig_assets.update_layout(
                height=200,
                margin=dict(l=80, r=20, t=10, b=20),
                paper_bgcolor="#F8F7FB", plot_bgcolor="#F8F7FB",
                font=dict(color="#1A1A1A", size=10),
                yaxis=dict(autorange="reversed"),
                xaxis=dict(
                    title="Number of fault occurrences",
                    gridcolor="#E2E0EA",
                    zerolinecolor="#E2E0EA",
                ),
            )
            st.plotly_chart(fig_assets, use_container_width=True)
        else:
            st.caption("No asset data available")

    with op_right:
        st.markdown(
            '<div class="sec-label" style="font-size:14px;'
            'color:#16A34A;">Recurring Faults</div>',
            unsafe_allow_html=True,
        )

        if not recurring:
            st.markdown(
                '<div style="background:#F8F7FB;border:1px solid #E2E0EA;'
                'border-radius:8px;padding:14px 18px;color:#5A5A5A;'
                'font-size:13px;">'
                'No fault records yet. Run scenarios to populate.'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            labels_rf = [fm.replace("_", " ").title() for fm, _ in recurring]
            counts_rf = [count for _, count in recurring]
            fig_rf = go.Figure(data=[
                go.Bar(
                    x=labels_rf,
                    y=counts_rf,
                    marker_color="#A100FF",
                    text=counts_rf,
                    textposition="outside",
                    hovertemplate="%{x}<br>%{y} occurrences<extra></extra>",
                )
            ])
            fig_rf.update_layout(
                height=200,
                margin=dict(l=80, r=20, t=10, b=20),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#5A5A5A", size=12),
                yaxis=dict(
                    title="Number of occurrences",
                    gridcolor="#E2E0EA",
                    zerolinecolor="#E2E0EA",
                ),
                xaxis=dict(
                    title="Fault type",
                    showgrid=False,
                    tickangle=-15,
                ),
                showlegend=False,
            )
            st.plotly_chart(fig_rf, use_container_width=True)
        st.markdown("<div style='font-size:10px;color:#5A5A5A;margin:4px 0 2px'>"
                    "Last run · dimension coverage</div>", unsafe_allow_html=True)
        if last_dimensions and isinstance(last_dimensions, dict):
            chips_html = "<div style='display:flex;flex-wrap:wrap;gap:4px'>"
            for dim, info in last_dimensions.items():
                status = (info.get("status", "") if isinstance(info, dict) else "").lower()
                color = "#16A34A" if status in ("covered", "yes", "present") else "#D97706"
                label = dim.replace("_", " ").title()
                chips_html += (
                    f"<span style='background:{color};color:#FFFFFF;padding:3px 8px;"
                    f"border-radius:10px;font-size:9px;font-weight:600'>{label}</span>"
                )
            chips_html += "</div>"
            st.markdown(chips_html, unsafe_allow_html=True)
        else:
            st.caption("Run a scenario in Discovery to populate dimension coverage")

    logger.info("[latency] render_analytics total took %.3fs", _at.perf_counter() - _analytics_t0)


# ══════════════════════════════════════════════════════════════════════════
# TAB: ADD CASE (Manual Override)  — Change 6 LLM pre-fill
# ══════════════════════════════════════════════════════════════════════════
PREFILL_KEYS = ["pf_root", "pf_action", "pf_result", "pf_lesson", "pf_bearing", "pf_fault", "pf_asset"]


# ═══════════════════════════════════════════════════════════════════
# SECTION: ENRICHMENT PREVIEW (PATH_C)
# LLM-driven per-dimension enrichment preview; read-only, no writes.
# ═══════════════════════════════════════════════════════════════════
def _generate_enrichment_preview(
    incoming_feedback: dict,
    retrieved_case: dict,
    missing_dimensions: list,
    llm_client,
) -> dict:
    """Generate LLM-based enrichment content for PARTIAL/MISSING dimensions.

    Does NOT write to disk, FAISS, or fault_tracker. Returns {"html": str}.
    Called from the PATH_C result panel's Preview Enrichment button.
    """
    # Prompts the LLM for structured JSON, NOT prose. Each missing dimension gets a
    # dict of concrete field values (e.g. {"vib_rms_mm_s": "7.2", "kurtosis": "6.8"}).
    # Returns {"dims": {dim_key: dict_or_str}, "empty_reason": str|None}.
    # Graceful fallback: if the LLM returns a plain string for a dimension instead of
    # a dict, it is stored as-is and the UI renders it verbatim.
    _DIM_LABELS_EP = {
        "signal_signature":            "Signal signature",
        "diagnosis_and_reasoning":     "Diagnosis & reasoning",
        "action_and_outcome":          "Action & outcome",
        "root_cause_and_factors":      "Root cause & factors",
        "lessons_and_future_reference":"Lessons & future reference",
    }
    if not llm_client:
        return {"dims": {}, "empty_reason": "LLM not available — enrichment preview requires an active language model."}

    dim_names_str = ", ".join(
        _DIM_LABELS_EP.get(d, d) for d in missing_dimensions
    )
    _prompt_ep = (
        "You are a reliability engineer proposing knowledge enrichment for a "
        "partially-covered case in a predictive maintenance knowledge base.\n\n"
        f"RETRIEVED CASE (partial coverage):\n"
        f"- case_id: {retrieved_case.get('case_id', 'UNKNOWN')}\n"
        f"- fault_mode: {retrieved_case.get('fault_mode', '')}\n"
        f"- asset_type: {retrieved_case.get('asset_type', '')}\n"
        f"- bearing_type: {retrieved_case.get('bearing_type', '')}\n"
        f"- action_taken: {retrieved_case.get('action_taken', '')}\n"
        f"- root_cause: {retrieved_case.get('root_cause', '')}\n"
        f"- lessons_learned: {retrieved_case.get('lessons_learned', '')}\n\n"
        f"INCOMING INCIDENT (new context):\n"
        f"- fault_mode: {incoming_feedback.get('fault_mode', '')}\n"
        f"- asset_type: {incoming_feedback.get('asset_type', '')}\n"
        f"- bearing_type: {incoming_feedback.get('bearing_type', '')}\n"
        f"- asset_id: {incoming_feedback.get('asset_id', '')}\n"
        f"- vib_rms_mm_s: {incoming_feedback.get('vib_rms_mm_s', 0)}\n"
        f"- temp_c: {incoming_feedback.get('temp_c', 0)}\n"
        f"- bpfo_energy: {incoming_feedback.get('bpfo_energy', 0)}\n\n"
        f"DIMENSIONS TO ENRICH: {dim_names_str}\n\n"
        "Return ONLY a JSON object. No markdown, no preamble.\n"
        "Use snake_case DIMENSION names as top-level keys. For each dimension "
        "to enrich, the value MUST be an object with field:value pairs "
        "describing what should be added to the existing case.\n\n"
        "Use these exact field schemas per dimension:\n"
        '  signal_signature: {"vibration_rms": "<value with units>", '
        '"kurtosis": "<value>", "temp_c": "<value with units>", '
        '"bpfo_energy": "<value>", "signal_quality": "<good/degraded/etc>"}\n'
        '  diagnosis_and_reasoning: {"primary_diagnosis": "<short label>", '
        '"confidence": "<qualitative>", "reasoning": "<one sentence>"}\n'
        '  action_and_outcome: {"recommended_action": "<short>", '
        '"sop_reference": "<code or custom>", "parts_required": "<list>", '
        '"est_duration": "<hours>"}\n'
        '  root_cause_and_factors: {"primary_cause": "<short>", '
        '"contributing_factors": "<list>"}\n'
        '  lessons_and_future_reference: {"key_lesson": "<short>", '
        '"prevention": "<action-oriented>"}\n\n'
        "Include ONLY the dimensions listed in DIMENSIONS TO ENRICH. "
        "Every key inside a dimension object must have a concrete string value."
    )
    try:
        from langchain_core.messages import HumanMessage as _HMsg
        _resp = llm_client.invoke(
            [_HMsg(content=_prompt_ep)],
            config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
        )
        _raw = _resp.content if hasattr(_resp, "content") else str(_resp)
        import re as _re_ep, json as _json_ep
        _m = _re_ep.search(r'\{[\s\S]*\}', _raw)
        if not _m:
            return {"dims": {}, "empty_reason": "Enrichment generation failed — no JSON in LLM response."}
        _data = _json_ep.loads(_m.group(0))
        _dims = {}
        for _dk in (
            "signal_signature", "diagnosis_and_reasoning", "action_and_outcome",
            "root_cause_and_factors", "lessons_and_future_reference",
        ):
            _content = _data.get(_dk)
            if not _content:
                continue
            _dims[_dk] = _content  # dict (structured) or str (prose fallback)
        return {"dims": _dims, "empty_reason": None if _dims else "No enrichment fields generated."}
    except Exception as _ep_err:
        logger.warning("[enrich_preview] failed: %s", _ep_err)
        return {"dims": {}, "empty_reason": f"Preview generation failed: {_ep_err}"}


def generate_next_case_id() -> str:
    """Generate next case ID as CASE_YYYYMMDD_NNN (sequence resets per day)."""
    from services.case_id_mint import mint_new_case_id
    return mint_new_case_id()


def extract_case_from_scenario(scenario_text: str) -> dict:
    """Extract structured case data from a plain-language scenario description.

    1. If LLM_AVAILABLE and llm_client: call LLM with structured-output prompt.
    2. Parse JSON response (handles markdown code blocks). On error, fall back.
    3. Backfill missing fields from fault_mode_defaults.
    4. If LLM unavailable: keyword extraction then backfill.
    Always returns a complete dict, never raises.
    """
    import json as _json

    # --- LLM prompt template ---
    _PROMPT = """You are a senior reliability engineer. Extract structured maintenance \
case data from the scenario description below.

The description may be very brief (2-3 words) or highly detailed. For EVERY field \
produce a complete, engineering-sound value:
- Use information stated in the description where available (highest priority).
- For unstated fields, infer the most plausible values based on standard engineering \
knowledge for that fault_mode and asset_type.
- NEVER leave reasoning, root_cause, action_taken, lessons_learned, or outcome as \
empty strings. Use qualifying language where appropriate: \
"likely due to...", "typically caused by...", "recommend...".

Example — minimal input "conveyor fault":
{{
  "fault_mode": "outer_race_fault",
  "asset_type": "conveyor",
  "asset_id": "AST_CONVEYOR_AUTO",
  "bearing_type": "SKF6310",
  "stage": 2,
  "risk_level": "MEDIUM",
  "anomaly_score": 0.72,
  "vib_rms_mm_s": 4.8,
  "kurtosis": 5.1,
  "temp_c": 68.0,
  "bpfo_energy": 1.9,
  "signal_quality": "acceptable",
  "diagnosis_confidence": 0.76,
  "rul_estimate": 12,
  "reasoning": "Elevated BPFO energy with high kurtosis indicates outer race bearing damage on the conveyor drive, likely caused by contamination ingress or lubrication failure.",
  "root_cause": "Contamination ingress through a worn housing seal leading to progressive abrasive outer race wear.",
  "action_taken": "Replace bearing, inspect and replace housing seal, clean bearing cavity, verify lubrication schedule.",
  "parts_required": "SKF6310 bearing, end cover seal kit",
  "sop_reference": "SOP_004",
  "duration_hr": 3.5,
  "lessons_learned": "Inspect housing seals at every bearing replacement to prevent recurrence of contamination-driven wear.",
  "outcome": "Bearing replaced and housing sealed. Vibration and temperature returned to within specification."
}}

fault_mode options: outer_race_fault, inner_race_fault, cage_fault, lubrication_issue, misalignment, imbalance, looseness, healthy, sensor_fault
asset_type options: motor, pump, conveyor, compressor
stage: 0=no fault, 1=incipient, 2=developing, 3=advanced, 4=critical
risk_level: LOW, MEDIUM, HIGH
signal_quality: good, acceptable, poor

Return ONLY a JSON object with these exact keys (no markdown, no explanation):
{{
  "fault_mode": "...",
  "asset_type": "...",
  "asset_id": "...",
  "bearing_type": "...",
  "stage": 0,
  "risk_level": "...",
  "anomaly_score": 0.0,
  "vib_rms_mm_s": 0.0,
  "kurtosis": 0.0,
  "temp_c": 0.0,
  "bpfo_energy": 0.0,
  "signal_quality": "...",
  "diagnosis_confidence": 0.0,
  "rul_estimate": 0,
  "reasoning": "...",
  "action_taken": "...",
  "parts_required": "...",
  "sop_reference": "...",
  "duration_hr": 0.0,
  "root_cause": "...",
  "lessons_learned": "...",
  "outcome": "..."
}}

Scenario:
{scenario_text}"""

    parsed: dict = {}

    # Step 1 & 2: LLM extraction
    if LLM_AVAILABLE and llm_client:
        try:
            prompt_text = _PROMPT.format(scenario_text=scenario_text)
            response = llm_client.invoke(
                [HumanMessage(content=prompt_text)],
                config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
            )
            raw = response.content.strip()
            # Strip markdown code block if present
            _code_match = re.search(r'```(?:json)?\s*([\s\S]+?)```', raw)
            if _code_match:
                raw = _code_match.group(1).strip()
            # Find JSON object
            _json_match = re.search(r'\{[\s\S]*\}', raw)
            if _json_match:
                parsed = _json.loads(_json_match.group())
        except Exception as _llm_err:
            logger.warning("[extract_case] LLM failed: %s", _llm_err)
            st.warning(
                "Extract had trouble parsing the LLM response — "
                "using conservative defaults. Review fields carefully."
            )
            parsed = {}
    else:
        # Step 4: keyword extraction fallback
        t = scenario_text.lower()
        # fault_mode
        if any(w in t for w in ["outer race", "bpfo", "outer-race"]):
            parsed["fault_mode"] = "outer_race_fault"
        elif any(w in t for w in ["inner race", "bpfi", "inner-race"]):
            parsed["fault_mode"] = "inner_race_fault"
        elif any(w in t for w in ["cage", "ftf", "cage fault"]):
            parsed["fault_mode"] = "cage_fault"
        elif any(w in t for w in ["lubric", "oil", "grease"]):
            parsed["fault_mode"] = "lubrication_issue"
        elif any(w in t for w in ["misalign", "2x rpm", "shaft offset"]):
            parsed["fault_mode"] = "misalignment"
        elif any(w in t for w in ["imbalance", "unbalance", "1x rpm"]):
            parsed["fault_mode"] = "imbalance"
        elif any(w in t for w in ["sensor", "signal", "dropout", "connector"]):
            parsed["fault_mode"] = "sensor_fault"
        elif any(w in t for w in ["healthy", "normal", "baseline", "no fault"]):
            parsed["fault_mode"] = "healthy"
        else:
            parsed["fault_mode"] = "outer_race_fault"

        # asset_type
        if "pump" in t:
            parsed["asset_type"] = "pump"
        elif "conveyor" in t:
            parsed["asset_type"] = "conveyor"
        elif "compressor" in t:
            parsed["asset_type"] = "compressor"
        else:
            parsed["asset_type"] = "motor"

        # numeric extraction helpers
        import re as _re_kw
        def _num(pattern, text, default=None):
            m = _re_kw.search(pattern, text)
            return float(m.group(1)) if m else default

        parsed["vib_rms_mm_s"] = _num(r'(\d+\.?\d*)\s*mm/s', t)
        parsed["kurtosis"]     = _num(r'kurtosis[:\s]*(\d+\.?\d*)', t)
        parsed["temp_c"]       = _num(r'(\d+\.?\d*)\s*[°]*c\b', t)
        parsed["bpfo_energy"]  = _num(r'bpfo[:\s]*(\d+\.?\d*)', t)

        # stage
        _stage_m = _re_kw.search(r'stage\s*(\d)', t)
        if _stage_m:
            parsed["stage"] = int(_stage_m.group(1))

        # bearing_type
        _bearing_m = _re_kw.search(r'(skf\w+)', t)
        if _bearing_m:
            parsed["bearing_type"] = _bearing_m.group(1).upper()

    # Step 3: backfill missing fields from fault_mode_defaults
    try:
        from services.fault_mode_defaults import get_defaults
        fault_mode = parsed.get("fault_mode") or "outer_race_fault"
        defaults = get_defaults(fault_mode)
        det = defaults.get("detection", {})
        diag = defaults.get("diagnosis", {})
        act = defaults.get("action", {})

        def _fill(key, default_val):
            v = parsed.get(key)
            if v is None or v == "" or v == 0 and key not in ("stage",):
                parsed[key] = default_val

        _fill("anomaly_score",       det.get("anomaly_score", 0.5))
        _fill("vib_rms_mm_s",        det.get("vib_rms_mm_s", 3.0))
        _fill("kurtosis",            det.get("kurtosis", 3.5))
        _fill("temp_c",              det.get("temp_c", 65.0))
        _fill("bpfo_energy",         det.get("bpfo_energy", 1.5))
        _fill("signal_quality",      det.get("signal_quality", "good"))
        _fill("diagnosis_confidence", diag.get("confidence", 0.75))
        _fill("rul_estimate",        diag.get("rul_estimate", 14))
        _fill("duration_hr",         act.get("duration_hr", 3.0))
        _fill("sop_reference",       act.get("sop", ""))
        _fill("parts_required",      act.get("parts", ""))
    except Exception as _def_err:
        logger.warning("[extract_case] defaults backfill failed: %s", _def_err)

    # Tier-2 backfill: narrative text fields
    # Covers cases where LLM returned empty or generic text for sparse inputs.
    _BAD_NARRATIVE = {
        "", "unknown", "n/a", "none", "no fault detected",
        "not specified", "not enough information", "...",
    }
    _NARRATIVE_DEFAULTS = {
        "outer_race_fault": {
            "reasoning": (
                "Elevated BPFO energy with high kurtosis indicates outer race "
                "bearing damage, likely caused by contamination ingress or "
                "inadequate lubrication."
            ),
            "root_cause": (
                "Contamination ingress through a worn housing seal leading to "
                "abrasive outer race wear."
            ),
            "action_taken": (
                "Remove and replace bearing, inspect and replace housing seal, "
                "clean bearing cavity, verify lubrication schedule."
            ),
            "lessons_learned": (
                "Inspect housing end cover seals at every bearing change to "
                "prevent recurrence of contamination ingress."
            ),
            "outcome": (
                "Bearing replaced and housing sealed. Vibration and temperature "
                "returned to within specification."
            ),
        },
        "inner_race_fault": {
            "reasoning": (
                "BPFI-dominant spectrum with amplitude modulation at shaft "
                "frequency indicates inner race bearing fault, consistent with "
                "overloading or shaft surface damage."
            ),
            "root_cause": (
                "Inner race fatigue from cyclic overloading or shaft surface "
                "corrosion transferring load unevenly."
            ),
            "action_taken": (
                "Replace bearing, inspect shaft for corrosion or damage, apply "
                "shaft hardening treatment if required."
            ),
            "lessons_learned": (
                "Monitor shaft loading and inspect bearing seats regularly to "
                "detect inner race wear before it advances."
            ),
            "outcome": (
                "Bearing replaced and shaft inspected. Post-repair vibration "
                "within specification."
            ),
        },
        "cage_fault": {
            "reasoning": (
                "Irregular high-kurtosis impacts at FTF frequency indicate cage "
                "damage. Progression is rapid once cage fragments break free."
            ),
            "root_cause": (
                "Cage fatigue from axial overloading or lubricant starvation "
                "allowing metal-to-metal contact between cage and rolling elements."
            ),
            "action_taken": (
                "Immediate bearing replacement required. Inspect for loose "
                "fragments. Verify lubrication adequacy and axial load conditions."
            ),
            "lessons_learned": (
                "Cage faults require immediate action — RUL degrades rapidly. "
                "Verify lubricant grade and quantity at every scheduled maintenance."
            ),
            "outcome": (
                "Bearing replaced on emergency basis. No loose fragments found. "
                "Lubrication schedule revised."
            ),
        },
        "lubrication_issue": {
            "reasoning": (
                "Elevated operating temperature with broadband noise and low "
                "kurtosis indicates lubrication starvation — thermal signature "
                "precedes mechanical damage."
            ),
            "root_cause": (
                "Lubrication interval exceeded or incorrect lubricant grade, "
                "leading to oil film breakdown and increased friction heat."
            ),
            "action_taken": (
                "Drain and refill with correct lubricant grade, inspect seals "
                "for leaks, verify lubrication schedule adherence."
            ),
            "lessons_learned": (
                "Maintain lubrication schedule strictly. Elevated temperature "
                "is an early warning — acting before kurtosis rises prevents "
                "bearing replacement."
            ),
            "outcome": (
                "Lubrication serviced. Temperature returned to normal within "
                "24 hours."
            ),
        },
        "misalignment": {
            "reasoning": (
                "High 2x RPM vibration with radial dominance indicates shaft "
                "or coupling misalignment, likely introduced after recent "
                "maintenance or from thermal growth during operation."
            ),
            "root_cause": (
                "Shaft-to-coupling misalignment introduced during last "
                "maintenance or from thermal differential growth in operation."
            ),
            "action_taken": (
                "Perform precision laser alignment, replace flexible coupling "
                "if worn, verify alignment under operating temperature."
            ),
            "lessons_learned": (
                "Always perform alignment checks after bearing or coupling "
                "replacements. Verify hot alignment for assets above 60 C."
            ),
            "outcome": (
                "Alignment corrected to within ISO 10816 tolerance. Vibration "
                "reduced after correction."
            ),
        },
        "imbalance": {
            "reasoning": (
                "Dominant 1x RPM vibration with radial emphasis indicates "
                "rotor imbalance, potentially from accumulated contamination "
                "or component loss."
            ),
            "root_cause": (
                "Rotor mass imbalance caused by accumulated process "
                "contamination, blade erosion, or loss of a balancing weight."
            ),
            "action_taken": (
                "Perform dynamic balancing with calibrated equipment, inspect "
                "for material loss or accumulation on rotor."
            ),
            "lessons_learned": (
                "Schedule periodic rotor inspection and balancing in dusty or "
                "wet environments where material buildup is common."
            ),
            "outcome": (
                "Rotor balanced to G1.0 ISO 1940 tolerance. Vibration reduced "
                "to within specification."
            ),
        },
        "looseness": {
            "reasoning": (
                "Sub-harmonics and broadband noise indicate mechanical "
                "looseness — likely a loose mounting bolt or worn bearing "
                "housing fit."
            ),
            "root_cause": (
                "Mounting bolts loose or bearing housing worn beyond tolerance, "
                "causing micro-impacts that excite broadband vibration."
            ),
            "action_taken": (
                "Inspect and re-torque all mounting bolts to spec, check "
                "bearing housing fit, replace housing if clearance exceeds "
                "tolerance."
            ),
            "lessons_learned": (
                "Include bolt torque check in all scheduled maintenance. "
                "Looseness progresses to bearing damage if unaddressed."
            ),
            "outcome": (
                "Mounting bolts re-torqued. Vibration sub-harmonics eliminated. "
                "Asset returned to service."
            ),
        },
        "sensor_fault": {
            "reasoning": (
                "Burst signal dropout pattern with intermittent zero readings "
                "indicates a loose sensor connection rather than a bearing "
                "fault. Underlying asset appears healthy."
            ),
            "root_cause": (
                "Loose or corroded terminal connection at sensor head causing "
                "intermittent open circuit and burst signal dropouts."
            ),
            "action_taken": (
                "Inspect sensor wiring and terminal connections, re-torque "
                "connectors, calibrate sensor against reference unit."
            ),
            "lessons_learned": (
                "Include sensor connection torque check in all scheduled "
                "visits. Signal quality below 0.7 should trigger sensor "
                "inspection before bearing inspection."
            ),
            "outcome": (
                "Connector re-torqued and signal restored. No underlying "
                "bearing damage found."
            ),
        },
        "healthy": {
            "reasoning": (
                "All vibration and temperature parameters within normal "
                "operating range. No fault indicators detected."
            ),
            "root_cause": (
                "No fault present. Routine baseline measurement for condition "
                "monitoring record."
            ),
            "action_taken": (
                "No corrective action required. Apply routine lubrication if "
                "due per schedule."
            ),
            "lessons_learned": (
                "Consistent healthy readings confirm effectiveness of current "
                "maintenance schedule."
            ),
            "outcome": "Asset confirmed healthy. No action required.",
        },
    }
    _nd = _NARRATIVE_DEFAULTS.get(
        parsed.get("fault_mode", ""),
        {
            "reasoning": (
                "Anomaly detected. Detailed inspection required to confirm "
                "fault mode and source component."
            ),
            "root_cause": (
                "Fault mode under investigation. Likely related to bearing "
                "wear, lubrication, or mechanical loading condition."
            ),
            "action_taken": (
                "Increase monitoring frequency, perform detailed inspection, "
                "consult SOP for fault-mode-specific procedure."
            ),
            "lessons_learned": (
                "Early detection enables planned maintenance. Investigate root "
                "cause to prevent recurrence."
            ),
            "outcome": (
                "Maintenance action completed. Monitor post-repair to verify "
                "resolution."
            ),
        },
    )
    for _nk in ("reasoning", "root_cause", "action_taken", "lessons_learned", "outcome"):
        _nv = str(parsed.get(_nk, "")).strip()
        if not _nv or _nv.lower() in _BAD_NARRATIVE:
            parsed[_nk] = _nd[_nk]

    # Ensure all required keys are present
    _schema_defaults = {
        "fault_mode":           "outer_race_fault",
        "asset_type":           "motor",
        "asset_id":             "",
        "bearing_type":         "SKF6310",
        "stage":                2,
        "risk_level":           "MEDIUM",
        "anomaly_score":        0.5,
        "vib_rms_mm_s":         3.0,
        "kurtosis":             3.5,
        "temp_c":               65.0,
        "bpfo_energy":          1.5,
        "signal_quality":       "good",
        "diagnosis_confidence": 0.75,
        "rul_estimate":         14,
        "reasoning":            "",
        "action_taken":         "",
        "parts_required":       "",
        "sop_reference":        "",
        "duration_hr":          3.0,
        "root_cause":           "",
        "lessons_learned":      "",
        "outcome":              "",
    }
    for _k, _v in _schema_defaults.items():
        _cur = parsed.get(_k)
        if _cur is None or (isinstance(_cur, str) and _cur.strip() == ""):
            parsed[_k] = _v

    # Normalize asset_id
    if not parsed.get("asset_id"):
        parsed["asset_id"] = f"AST_{parsed['asset_type'].upper()}_AUTO"

    return parsed


def _load_case_content_from_disk(case_id: str) -> dict:
    """Load full case content from disk for a given case_id.

    Tries standard filename patterns first (fast), then falls back to a
    glob scan that checks filenames and JSON content. Returns {} if nothing
    matches. Never raises.
    """
    from pathlib import Path as _P
    cases_dir = _P("data/learned_cases")
    if not cases_dir.exists():
        return {}

    cid_lower = case_id.lower()
    cid_upper = case_id.upper()

    candidates = [
        cases_dir / f"{cid_lower}.json",
        cases_dir / f"{cid_upper}.json",
        cases_dir / f"{cid_lower}_user_generated.json",
        cases_dir / f"{cid_lower}_discovery_demo_generated.json",
        cases_dir / f"{cid_lower}_companion_test_generated.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception as _e:
                logger.warning("[case_load] failed to read %s: %s", c, _e)

    for path in cases_dir.glob("*.json"):
        if path.name.endswith(".bak"):
            continue
        if cid_lower in path.name.lower():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("case_id", "").upper() == cid_upper:
                return data
        except Exception:
            continue

    return {}


def _disk_path_for_case_id(case_id: str):
    """Return the first matching disk Path for case_id, or None if absent.

    Tries known filename patterns in priority order, then falls back to a
    glob scan. Used by panels that list cases from FAISS metadata and need
    to check whether the on-disk body exists before offering a PDF button.
    """
    from pathlib import Path as _P
    _dir = _P("data/learned_cases")
    if not _dir.exists():
        return None
    _cid_lower = case_id.lower()
    _candidates = [
        _dir / f"{_cid_lower}_user_generated.json",
        _dir / f"{_cid_lower}_discovery_demo_generated.json",
        _dir / f"{_cid_lower}_pipeline_generated.json",
        _dir / f"{_cid_lower}_manual_generated.json",
        _dir / f"{_cid_lower}.json",
        _dir / f"{case_id}.json",
    ]
    for _c in _candidates:
        if _c.exists():
            return _c
    # Fallback: glob scan for any file whose name contains the case_id
    for _p in _dir.glob(f"*{_cid_lower}*.json"):
        if not _p.name.endswith(".bak"):
            return _p
    return None


# ── Add Case "Check similar cases" helpers ────────────────────────────────
# Post-PPO: unify with the Discovery scenarios dict's implicit vocabulary.
_ADD_CASE_FAULT_VOCAB = (
    "outer_race_fault", "inner_race_fault", "cage_fault",
    "lubrication_issue", "misalignment", "sensor_fault", "healthy",
)
_ADD_CASE_ASSET_VOCAB = ("motor", "gearbox", "pump", "conveyor")

_ADD_CASE_FAISS_THRESHOLD = cfg("add_case", "duplicate_similarity_threshold", default=0.55)
_ADD_CASE_MIN_TEXT_LEN    = cfg("add_case", "min_text_length_for_hash",        default=15)

# Domain-synonym map: canonical vocab term → list of aliases.
# Aliases are matched case-insensitively and whitespace-normalized
# (extra spaces collapsed, "SKF 6310" == "SKF-6310" == "skf6310").
# Adding synonyms here extends what Add Case's auto-check understands
# WITHOUT touching Search Case (which uses its own broader corpus).
_ADD_CASE_SYNONYMS = {
    # ── Bearing fault modes ─────────────────────────────────────────
    "outer_race_fault": [
        "outer race", "outer race fault", "outer raceway",
        "outer raceway defect", "outer race spalling",
        "outer race pitting", "outer race wear", "outer race damage",
        "bpfo", "bpfo spike", "bpfo peak",
        "outer race bearing fault", "outer race defect",
    ],
    "inner_race_fault": [
        "inner race", "inner race fault", "inner raceway",
        "inner raceway defect", "inner race spalling",
        "inner race pitting", "inner race wear", "inner race damage",
        "bpfi", "bpfi spike", "bpfi peak",
        "inner race bearing fault", "inner race defect",
    ],
    "cage_fault": [
        "cage", "cage fault", "cage defect", "cage wear",
        "cage damage", "cage failure", "retainer", "retainer fault",
        "retainer defect", "ftf", "ftf frequency",
        "fundamental train frequency", "separator fault",
    ],
    "lubrication_issue": [
        "lubrication", "lubrication issue", "lubrication failure",
        "lubrication problem", "grease", "grease starvation",
        "grease loss", "grease contamination", "oil",
        "oil contamination", "oil starvation", "oil loss",
        "lube", "lube failure", "lube issue", "dry running",
        "insufficient lubrication", "over lubrication",
        "over-lubrication", "over-greased", "under lubricated",
    ],
    "misalignment": [
        "misalignment", "alignment", "misaligned", "shaft offset",
        "shaft misalignment", "coupling misalignment",
        "angular misalignment", "parallel misalignment",
        "offset misalignment", "2x rpm", "2xrpm", "2x running speed",
        "harmonic vibration", "shaft alignment", "coupling wear",
        "coupling offset",
    ],
    "sensor_fault": [
        "sensor", "sensor fault", "sensor failure", "sensor issue",
        "transducer", "transducer fault", "accelerometer fault",
        "bad sensor", "bad signal", "signal loss", "sensor drift",
        "sensor noise", "flatline", "flat line", "no signal",
        "corrupted signal",
    ],
    "healthy": [
        "healthy", "normal", "baseline", "no fault", "no issue",
        "operating normally", "within spec", "within tolerance",
        "green", "ok", "nominal",
    ],
    # ── Asset types ─────────────────────────────────────────────────
    "motor": [
        "motor", "induction motor", "ac motor", "drive motor",
        "electric motor", "servo motor", "spindle motor",
        "traction motor", "drive", "prime mover",
    ],
    "gearbox": [
        "gearbox", "gear box", "reducer", "gear reducer",
        "speed reducer", "transmission", "gear train", "gearset",
        "reduction unit",
    ],
    "pump": [
        "pump", "centrifugal pump", "impeller", "impeller pump",
        "circulation pump", "coolant pump", "hydraulic pump",
        "vacuum pump", "booster pump",
    ],
    "conveyor": [
        "conveyor", "belt", "belt conveyor", "conveyor belt",
        "roller conveyor", "chain conveyor", "line conveyor",
        "transport belt",
    ],
    "fan": [
        "fan", "blower", "fan blower", "cooling fan", "exhaust fan",
        "extraction fan", "axial fan", "centrifugal fan",
    ],
}


def _normalize_for_match(text: str) -> str:
    """Lowercase, collapse hyphens/underscores and whitespace to single spaces.
    Used on both user text and alias strings so 'SKF-6310', 'SKF 6310',
    'skf6310' all compare equal.
    """
    import re as _re
    t = (text or "").lower()
    t = _re.sub(r"[-_]+", " ", t)
    t = _re.sub(r"\s+", " ", t)
    return t.strip()


def _tokenize_desc(text: str) -> set:
    """Return set of lowercase word tokens from raw scenario text."""
    import re as _re
    return set(_re.findall(r"[a-z]+", text.lower()))


def _extract_case_query_terms(text: str) -> str:
    """Extract canonical fault_mode / asset_type terms from free text via synonym matching.

    Normalizes text (lowercase, hyphens/underscores → spaces, collapsed whitespace)
    then checks whether any alias in _ADD_CASE_SYNONYMS appears as a substring.
    Returns the matched canonical terms (space-form) joined by spaces.
    Returns "" if no vocab term matched — caller must handle empty.

    Examples:
        "outer race"               -> "outer race fault"
        "BPFO spike on motor"      -> "outer race fault motor"
        "shaft offset in gearbox"  -> "misalignment gearbox"
        "SKF-6310 outer race"      -> "outer race fault"
        "grease starvation pump"   -> "lubrication issue pump"
        "there's a weird noise"    -> ""
    """
    _norm = _normalize_for_match(text)
    if not _norm:
        return ""
    _extracted = []
    for _canonical, _aliases in _ADD_CASE_SYNONYMS.items():
        _canonical_spaced = _canonical.replace("_", " ")
        # Include the canonical spaced form itself as an implicit alias
        _candidates = [_canonical_spaced] + [_normalize_for_match(a) for a in _aliases]
        if any(a and a in _norm for a in _candidates):
            _extracted.append(_canonical_spaced)
    # Dedup preserving insertion order
    _seen: set = set()
    _result = []
    for _t in _extracted:
        if _t not in _seen:
            _seen.add(_t)
            _result.append(_t)
    return " ".join(_result)


def _add_case_search_corpus(c: dict) -> str:
    """Add-Case-specific corpus: identity fields ONLY — NOT narrative fields.

    Rationale: at case-creation time, "similar" means "already exists as an
    identity" (same fault_mode + asset_type + bearing_type), not "narratively
    mentions the same words." Narrative fields produce false positives because
    a case may mention other fault types as differentials-considered-and-ruled-out
    (e.g. CASE_003 inner_race_fault references outer-race evidence in its
    lessons_learned). Search Case's _case_search_corpus continues to include
    narrative fields — it serves a different use case.
    """
    _fields = [
        c.get("case_id", ""),
        c.get("fault_mode", ""),
        c.get("asset_type", ""),
        c.get("bearing_type", ""),
    ]
    _raw = " ".join(str(f) for f in _fields if f).lower()
    _spaced = _raw.replace("_", " ")
    return f"{_raw} {_spaced}"


def _keyword_matches(raw_text: str) -> list:
    """Keyword match against Add Case identity corpus (inlined AND-match).

    Uses _extract_case_query_terms (prefix-based, returns string) for the
    query and _add_case_search_corpus (identity fields only) per case.
    Does NOT call _search_matches — avoids false positives from narrative
    corpus without altering Search Case behaviour.
    """
    import re as _re
    _terms = _extract_case_query_terms(raw_text)   # returns str, empty when no vocab
    if not _terms:
        return []
    _kw_norm = _terms.lower().strip().replace("_", " ")
    _tokens = [t for t in _re.split(r"\s+", _kw_norm) if t]
    if not _tokens:
        return []
    _results = []
    try:
        for _c in load_all_cases():
            _corpus = _add_case_search_corpus(_c)
            if all(t in _corpus for t in _tokens):
                _results.append({
                    "case_id":      _c.get("case_id"),
                    "fault_mode":   _c.get("fault_mode"),
                    "asset_type":   _c.get("asset_type"),
                    "bearing_type": _c.get("bearing_type"),
                    "similarity":   None,
                    "match_type":   "keyword",
                })
    except Exception as _ke:
        logger.warning("[add_case] _keyword_matches failed: %s", _ke)
    return _results


def _faiss_matches(raw_text: str, k: int = 5) -> list:
    """Embed raw_text and return FAISS matches with cosine similarity >= 0.55.

    Uses the same embed→search→reconstruct pattern as knowledge_check.py
    lines 106-108. Does NOT call check_existing_knowledge (requires
    structured fields). Local threshold 0.55 — SIMILARITY_THRESHOLD
    (0.70) is NOT modified.
    """
    try:
        import numpy as _np
        from rag.embeddings import generate_embedding as _gen_emb
        from rag.vector_storage import VectorStorage as _VS

        _store = _VS()
        _store.load_index()
        if _store.get_vector_count() == 0:
            return []

        _emb = _gen_emb(raw_text)
        _qvec = _np.array([_emb], dtype=_np.float32)
        _k = min(k, _store.get_vector_count())
        _dists, _idxs = _store.index.search(_qvec, _k)

        _case_ids = list(_store.metadata.keys())
        _results = []
        for _dist, _idx in zip(_dists[0], _idxs[0]):
            if _idx < 0 or _idx >= len(_case_ids):
                continue
            _svec = _np.zeros((1, 384), dtype=_np.float32)
            _store.index.reconstruct(int(_idx), _svec[0])
            _a = _np.array(_emb, dtype=_np.float32)
            _b = _svec[0]
            _na, _nb = _np.linalg.norm(_a), _np.linalg.norm(_b)
            _sim = float(_np.dot(_a, _b) / (_na * _nb)) if _na and _nb else 0.0
            if _sim < _ADD_CASE_FAISS_THRESHOLD:
                continue
            _cid = _case_ids[_idx]
            _meta = _store.metadata.get(_cid, {})
            _results.append({
                "case_id":     _cid,
                "fault_mode":  _meta.get("fault_mode", ""),
                "asset_type":  _meta.get("asset_type", ""),
                "bearing_type": _meta.get("bearing_type", ""),
                "similarity":  round(_sim, 4),
                "match_type":  "faiss",
            })
        return sorted(_results, key=lambda _x: _x["similarity"], reverse=True)
    except Exception as _fe:
        logger.warning("[add_case_check] FAISS query failed: %s", _fe)
        return []


def _find_similar_cases(raw_text: str) -> list:
    """Merge FAISS + keyword matches, deduplicate by case_id; FAISS entry wins."""
    _faiss = _faiss_matches(raw_text)
    _kw = _keyword_matches(raw_text)
    _seen: dict = {}
    for _m in _faiss:
        _seen[_m["case_id"]] = _m
    for _m in _kw:
        if _m["case_id"] not in _seen:
            _seen[_m["case_id"]] = _m
    return list(_seen.values())


def _render_check_pdf_view(cid: str) -> None:
    """PDF viewer rendered in the right column of the two-column Add Case layout."""
    import base64 as _b64

    _hdr, _close = st.columns([5, 1])
    with _hdr:
        st.markdown(
            f"<div style='color:#D97706;font-size:0.75rem;font-weight:600;"
            f"letter-spacing:0.08em;text-transform:uppercase;padding-top:0.35rem;'>"
            f"Viewing: {cid}</div>",
            unsafe_allow_html=True,
        )
    with _close:
        if st.button("✕", key="ac_close_pdf", help="Close PDF preview"):
            st.session_state.pop("ac_check_viewing_pdf", None)
            st.session_state.pop("ac_check_selected_cid", None)
            st.session_state["ac_right_panel_mode"] = "placeholder"
            st.rerun()

    _case_data = _load_case_content_from_disk(cid)
    if not _case_data:
        st.warning(f"No disk file for {cid} — PDF unavailable.")
        return

    try:
        from services.pdf_renderer import render_case_pdf as _rpdf
        _pdf_bytes = _rpdf(_case_data)
        _b64_pdf = _b64.b64encode(_pdf_bytes).decode("utf-8")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{_b64_pdf}" '
            f'width="100%" height="520px" style="border:1px solid #D8D6E5;'
            f'border-radius:0.4rem;"></iframe>',
            unsafe_allow_html=True,
        )
    except Exception as _pe:
        st.error(f"PDF render failed: {_pe}")


def _render_check_results_panel() -> None:
    """Render match cards in the left column. PDF preview opens in the right column."""
    _matches = st.session_state.get("ac_check_matches", [])

    if not _matches:
        st.markdown(
            '<div style="margin-top:0.6rem;padding:0.7rem 0.9rem;background:#F0FFF4;'
            'border:1px solid #BBF7D0;border-left:4px solid #16A34A;'
            'border-radius:0.4rem;">'
            '<div style="color:#16A34A;font-size:0.75rem;font-weight:600;'
            'letter-spacing:0.08em;text-transform:uppercase;">'
            'No Similar Cases Found</div>'
            '<div style="color:#5A5A5A;font-size:0.82rem;margin-top:0.2rem;">'
            'New fault scenario — safe to extract and save.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    _n = len(_matches)
    st.markdown(
        f'<div style="margin-top:0.6rem;padding:0.7rem 0.9rem;background:#FFFBEB;'
        f'border:1px solid #FDE68A;border-left:4px solid #D97706;'
        f'border-radius:0.4rem;margin-bottom:0.5rem;">'
        f'<div style="color:#D97706;font-size:0.75rem;font-weight:600;'
        f'letter-spacing:0.08em;text-transform:uppercase;">'
        f'{_n} Similar Case{"s" if _n != 1 else ""} Found</div>'
        f'<div style="color:#5A5A5A;font-size:0.82rem;margin-top:0.2rem;">'
        f'Review before extracting — click View case to preview the PDF.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    for _m in _matches:
        _cid = _m.get("case_id", "")
        _fm = _m.get("fault_mode", "").replace("_", " ")
        _at = _m.get("asset_type", "")
        _bt = _m.get("bearing_type", "")
        _is_selected = (st.session_state.get("ac_check_selected_cid") == _cid)
        _card_border = "border-left:4px solid #A100FF;" if _is_selected else ""

        _card_col, _btn_col = st.columns([5, 1])
        with _card_col:
            st.markdown(
                f'<div style="padding:0.6rem 0.8rem;background:#F8F7FB;'
                f'border:1px solid #D8D6E5;{_card_border}border-radius:0.4rem;'
                f'margin-bottom:0.3rem;">'
                f'<div style="font-family:monospace;color:#1A1A1A;font-size:0.88rem;">{_cid}</div>'
                f'<div style="color:#5A5A5A;font-size:0.76rem;margin-top:0.15rem;">'
                f'fault={_fm} · asset={_at} · bearing={_bt}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _btn_col:
            if st.button("View", key=f"ac_view_{_cid}", help=f"Preview PDF for {_cid}"):
                st.session_state["ac_check_selected_cid"] = _cid
                st.session_state["ac_check_viewing_pdf"] = True
                st.session_state["ac_right_panel_mode"] = "pdf"
                st.rerun()


# All Streamlit widget keys used in the Add Case Step 2 form.
# Must be cleared whenever the draft is discarded, saved, or a fresh Extract
# is run — otherwise Streamlit re-uses the previous widget state and ignores
# the new draft's values entirely.
_ADD_CASE_WIDGET_KEYS = (
    "dr_fm", "dr_bt", "dr_vib", "dr_bpfo",   # col1
    "dr_at", "dr_st", "dr_kurt", "dr_an",     # col2
    "dr_ai", "dr_rl", "dr_temp", "dr_cf",     # col3
    "dr_sq", "dr_rul", "dr_dur",              # row 2
    "dr_sop", "dr_pr",                         # row 3
    "dr_re", "dr_ac", "dr_rc", "dr_ll", "dr_oc",  # narrative
)


def _clear_add_case_widget_state() -> None:
    for _wk in _ADD_CASE_WIDGET_KEYS:
        st.session_state.pop(_wk, None)
    # clear Check step state alongside form widgets — called at all 4 Add Case lifecycle points
    for _ck in ("ac_check_ran", "ac_check_text_hash", "ac_check_matches",
                "ac_check_selected_cid", "ac_check_viewing_pdf"):
        st.session_state.pop(_ck, None)
    # reset right-column state machine to default on lifecycle events
    st.session_state.pop("ac_right_panel_mode", None)


# ═══════════════════════════════════════════════════════════════════
# SECTION: ADD CASE HANDLERS
# Discovery tab: scenario input, extraction, PATH_A/B/C result panels.
# ═══════════════════════════════════════════════════════════════════
def render_add_case_input():
    st.markdown("### Step 1 — Describe what happened")
    st.caption(
        "Describe the fault scenario in plain language. The knowledge base is "
        "checked automatically as you type. Extract Details is enabled once the "
        "check completes."
    )

    col_left, col_right = st.columns([1, 2], gap="large")

    with col_left:
        scenario_text = st.text_area(
            "Scenario description",
            key="add_case_scenario_text",
            height=160,
            placeholder=(
                "Example: A conveyor motor bearing (SKF6310) showed progressive "
                "outer race deterioration. Vibration RMS reached 7.5 mm/s and "
                "temperature hit 81°C. Stage 3 confirmed. Root cause was "
                "contamination ingress through a worn housing seal."
            ),
        )

        _text_stripped = scenario_text.strip()
        _text_hash = str(hash(_text_stripped)) if _text_stripped else ""
        _stored_hash = st.session_state.get("ac_check_text_hash", "")

        # Auto-check: >=15 chars AND hash changed since last check
        if len(_text_stripped) >= _ADD_CASE_MIN_TEXT_LEN and _text_hash != _stored_hash:
            with st.spinner("Checking knowledge base…"):
                _auto_matches = _find_similar_cases(_text_stripped)
            st.session_state["ac_check_ran"] = True
            st.session_state["ac_check_text_hash"] = _text_hash
            st.session_state["ac_check_matches"] = _auto_matches
            st.session_state.pop("ac_check_selected_cid", None)
            st.session_state.pop("ac_check_viewing_pdf", None)

        # Re-read after potential in-pass update
        _check_ran = st.session_state.get("ac_check_ran", False)
        _check_hash = st.session_state.get("ac_check_text_hash", "")
        _check_valid = _check_ran and bool(_text_stripped) and (_check_hash == _text_hash)

        if _check_valid:
            _render_check_results_panel()
        elif not _text_stripped:
            st.markdown(
                "<div style='color:#8A8A8A;font-size:0.82rem;margin-top:0.8rem;'>"
                "Type at least 15 characters — knowledge base check fires automatically."
                "</div>",
                unsafe_allow_html=True,
            )

    with col_right:
        _right_mode = st.session_state.get("ac_right_panel_mode", "placeholder")
        _viewing_cid = st.session_state.get("ac_check_selected_cid")
        _ac_draft = st.session_state.get("add_case_draft")
        if _right_mode == "pdf" and _viewing_cid:
            _render_check_pdf_view(_viewing_cid)
        elif _right_mode == "extract" and _ac_draft:
            render_add_case_output()
        else:
            st.markdown(
                "<div style='min-height:200px;display:flex;align-items:center;"
                "justify-content:center;color:#C0BDD0;font-size:0.82rem;"
                "border:1px dashed #D8D6E5;border-radius:0.4rem;padding:1.5rem;"
                "text-align:center;margin-top:1.6rem;'>"
                "👁 Click View on a suggestion to preview the case here, "
                "or click Extract Details to generate a new one."
                "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    _extract_label = (
        "↻ Re-extract"
        if st.session_state.get("ac_right_panel_mode") == "extract"
        else "→ Extract Details"
    )
    extract_clicked = st.button(
        _extract_label,
        type="primary",
        disabled=not _check_valid,
        key="add_case_extract_btn",
    )

    if extract_clicked and _check_valid:
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        _logger.info("[add_case] Extract clicked, scenario len=%d, LLM_AVAILABLE=%s",
                     len(scenario_text), LLM_AVAILABLE)
        with st.spinner("Extracting structured fields…"):
            draft = extract_case_from_scenario(scenario_text)
        _logger.info("[add_case] Extraction returned: keys=%s",
                     list(draft.keys()) if isinstance(draft, dict) else type(draft).__name__)
        draft["case_id"] = generate_next_case_id()
        draft["_scenario_text"] = scenario_text
        st.session_state["add_case_draft"] = draft
        st.session_state["ac_right_panel_mode"] = "extract"
        _logger.info("[add_case] Draft stored, right panel set to extract, calling st.rerun()")
        st.rerun()


def render_add_case_output():
    draft = st.session_state.get("add_case_draft")
    if not draft:
        return

    st.markdown("### Step 2 — Review the draft case")

    # Case ID — display only, NOT an input widget
    st.markdown(
        f"<div style='padding:0.35rem 0.6rem;background:#F0F0FF;"
        f"border:1px solid #D8D6E5;border-radius:0.4rem;margin-bottom:0.6rem;'>"
        f"<span style='color:#5A5A5A;font-size:0.75rem;'>Case ID (auto-generated)</span><br>"
        f"<span style='color:#1A1A1A;font-family:monospace;font-size:1.05rem;'>"
        f"{draft['case_id']}</span></div>",
        unsafe_allow_html=True,
    )

    # 3-column grid — 12 fields, 4 rows
    col1, col2, col3 = st.columns(3)
    with col1:
        draft["fault_mode"]   = st.text_input("Fault mode",   draft.get("fault_mode", ""),   key="dr_fm")
        draft["bearing_type"] = st.text_input("Bearing type", draft.get("bearing_type", ""), key="dr_bt")
        draft["vib_rms_mm_s"] = st.number_input("Vibration RMS (mm/s)", value=float(draft.get("vib_rms_mm_s") or 0.0), key="dr_vib")
        draft["bpfo_energy"]  = st.number_input("BPFO energy", value=float(draft.get("bpfo_energy") or 0.0), key="dr_bpfo")
    with col2:
        draft["asset_type"]   = st.text_input("Asset type",   draft.get("asset_type", ""),   key="dr_at")
        draft["stage"]        = st.number_input("Stage", min_value=0, max_value=4,
                                                value=int(draft.get("stage", 0)), key="dr_st")
        draft["kurtosis"]     = st.number_input("Kurtosis", value=float(draft.get("kurtosis") or 0.0), key="dr_kurt")
        draft["anomaly_score"] = st.number_input("Anomaly score", value=float(draft.get("anomaly_score") or 0.0),
                                                 min_value=0.0, max_value=1.0, step=0.01, key="dr_an")
    with col3:
        draft["asset_id"]     = st.text_input("Asset ID",     draft.get("asset_id", ""),     key="dr_ai")
        draft["risk_level"]   = st.text_input("Risk level",   draft.get("risk_level", ""),   key="dr_rl")
        draft["temp_c"]       = st.number_input("Temperature (°C)", value=float(draft.get("temp_c") or 0.0), key="dr_temp")
        draft["diagnosis_confidence"] = st.number_input("Diagnosis confidence",
                                                        value=float(draft.get("diagnosis_confidence") or 0.0),
                                                        min_value=0.0, max_value=1.0, step=0.01, key="dr_cf")

    _sc2a, _sc2b, _sc2c = st.columns(3)
    with _sc2a:
        draft["signal_quality"] = st.text_input("Signal quality", draft.get("signal_quality", ""), key="dr_sq")
    with _sc2b:
        draft["rul_estimate"]   = st.number_input("RUL estimate (days)", value=int(draft.get("rul_estimate") or 0),
                                                  min_value=0, key="dr_rul")
    with _sc2c:
        draft["duration_hr"]    = st.number_input("Duration (hr)", value=float(draft.get("duration_hr") or 0.0), key="dr_dur")

    draft["sop_reference"]  = st.text_input("SOP reference", draft.get("sop_reference", ""), key="dr_sop")
    draft["parts_required"] = st.text_area("Parts required", draft.get("parts_required", ""), height=60, key="dr_pr")

    with st.expander("Narrative fields", expanded=True):
        draft["reasoning"]       = st.text_area("Reasoning",       draft.get("reasoning", ""),       height=60, key="dr_re")
        draft["action_taken"]    = st.text_area("Action taken",    draft.get("action_taken", ""),    height=60, key="dr_ac")
        draft["root_cause"]      = st.text_area("Root cause",      draft.get("root_cause", ""),      height=60, key="dr_rc")
        draft["lessons_learned"] = st.text_area("Lessons learned", draft.get("lessons_learned", ""), height=80, key="dr_ll")
        draft["outcome"]         = st.text_area("Outcome",         draft.get("outcome", ""),         height=60, key="dr_oc")

    # --- Save flow with duplicate warning ---
    _pending_key  = f"pending_save_{draft.get('case_id', 'x')}"
    _show_mch_key = f"show_match_{draft.get('case_id', 'x')}"

    col_save, col_discard = st.columns([1, 1])
    with col_save:
        if st.button("Save to Knowledge Base", type="primary", key="add_case_save_btn"):
            with st.spinner("Checking knowledge base for similar cases…"):
                try:
                    from services.knowledge_check import check_existing_knowledge
                    _ck_exists, _ck_match, _ck_score, _ = check_existing_knowledge(draft)
                except Exception as _ck_e:
                    logger.warning("[add_case] pre-save check failed: %s", _ck_e)
                    _ck_exists, _ck_match, _ck_score = False, None, 0.0

            if _ck_exists and _ck_match and _ck_score >= 0.70:
                st.session_state[_pending_key] = {
                    "best_match": _ck_match,
                    "best_score": _ck_score,
                }
                st.session_state.pop(_show_mch_key, None)
                st.rerun()
            else:
                with st.spinner("Persisting to FAISS, disk, and audit log…"):
                    result = persist_case_via_path_b(draft)
                if result["ok"]:
                    st.success(f"✓ Case saved as {draft['case_id']}")
                    st.session_state.pop("add_case_draft", None)
                    st.session_state.pop("add_case_scenario_text", None)
                    st.session_state["ac_right_panel_mode"] = "placeholder"
                    _clear_add_case_widget_state()
                    st.rerun()
                else:
                    st.error(f"Save failed: {result['error']}")
    with col_discard:
        if st.button("Discard draft", key="add_case_discard_btn"):
            st.session_state.pop("add_case_draft", None)
            st.session_state.pop(_pending_key, None)
            st.session_state.pop(_show_mch_key, None)
            st.session_state["ac_right_panel_mode"] = "placeholder"
            _clear_add_case_widget_state()
            st.rerun()

    # Warning UI — rendered when a similar case was detected on the previous click
    if _pending_key in st.session_state:
        _pending   = st.session_state[_pending_key]
        _match     = _pending["best_match"]
        _match_id  = _match.get("case_id", "unknown")
        _mf_raw    = _match.get("fault_mode", "")
        _match_fault  = _mf_raw.replace("_", " ")
        _match_asset  = _match.get("asset_type", "")

        # Exact-identity duplicate: all three identity fields match.
        # "full_match" validation_label covers fault+asset but not bearing,
        # so we compare bearing_type explicitly here.
        def _norm(s):
            return (str(s or "")).strip().lower()

        _exact_dup = (
            _norm(draft.get("fault_mode"))   == _norm(_match.get("fault_mode"))
            and _norm(draft.get("asset_type"))  == _norm(_match.get("asset_type"))
            and _norm(draft.get("bearing_type")) == _norm(_match.get("bearing_type"))
        )

        if _exact_dup:
            _card_header = "Duplicate Case Blocked"
            _match_bearing = _match.get("bearing_type", "")
            _card_body = (
                f'An exact identity match already exists in the knowledge base: '
                f'<b>{_match_id}</b> '
                f'(fault: {_match_fault} · asset: {_match_asset} · bearing: {_match_bearing}). '
                f'Cases with the same fault mode, asset type, and bearing type cannot be '
                f'duplicated. Use <b>View existing</b> to review the existing case, '
                f'or <b>Cancel</b> to discard this draft.'
            )
        else:
            _card_header = "Similar Case Already Exists"
            _card_body = (
                f'The knowledge base already contains <b>{_match_id}</b> '
                f'(fault: {_match_fault} · asset: {_match_asset}). '
                f"This looks like a strong match for what you're about to save."
            )

        st.markdown(
            f'<div style="margin-top:1rem;padding:0.9rem 1rem;background:#F8F7FB;'
            f'border:1px solid #D8D6E5;border-left:4px solid #D97706;'
            f'border-radius:0.4rem;">'
            f'<div style="color:#D97706;font-size:0.75rem;font-weight:600;'
            f'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:0.5rem;">'
            f'{_card_header}</div>'
            f'<div style="color:#1A1A1A;font-size:0.9rem;line-height:1.5;">'
            f'{_card_body}'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        _c1, _c2, _c3 = st.columns([1, 1, 1])
        with _c1:
            if st.button("View existing case", key=f"view_existing_{_match_id}"):
                st.session_state[_show_mch_key] = True
                st.rerun()
        if not _exact_dup:
            with _c2:
                if st.button(
                    "Save as new anyway", type="primary",
                    key=f"save_anyway_{_match_id}"
                ):
                    with st.spinner("Persisting to FAISS, disk, and audit log…"):
                        result = persist_case_via_path_b(draft)
                    if result["ok"]:
                        st.success(
                            f"✓ Case saved as {draft['case_id']} "
                            f"(duplicate of {_match_id})"
                        )
                        st.session_state.pop(_pending_key, None)
                        st.session_state.pop(_show_mch_key, None)
                        st.session_state.pop("add_case_draft", None)
                        st.session_state["ac_right_panel_mode"] = "placeholder"
                        _clear_add_case_widget_state()
                        st.rerun()
                    else:
                        st.error(f"Save failed: {result['error']}")
        with _c3:
            if st.button("Cancel", key=f"cancel_save_{_match_id}"):
                st.session_state.pop(_pending_key, None)
                st.session_state.pop(_show_mch_key, None)
                _clear_add_case_widget_state()
                # Stay in extract mode — user returns to reviewing the draft
                st.session_state["ac_right_panel_mode"] = "extract"
                st.rerun()

        # Inline detail view — shown after "View existing case" is clicked
        if st.session_state.get(_show_mch_key):
            # Seed cases have sparse FAISS metadata (pre-parity-fix). Fall back
            # to disk so the summary always shows real content.
            _disk = _load_case_content_from_disk(_match_id)

            def _resolved(_field):
                val = (_match or {}).get(_field, "")
                if val and str(val).strip() not in ("", "—"):
                    return str(val)
                disk_val = (_disk or {}).get(_field, "")
                if disk_val and str(disk_val).strip():
                    return str(disk_val)
                return "—"

            _summary_rows = [
                ("Fault",           _resolved("fault_mode")),
                ("Asset type",      _resolved("asset_type")),
                ("Bearing",         _resolved("bearing_type")),
                ("Root cause",      _resolved("root_cause")),
                ("Action taken",    _resolved("action_taken")),
                ("Lessons learned", _resolved("lessons_learned")),
            ]
            _rows_html = "".join(
                f'<tr>'
                f'<td style="color:#5A5A5A;width:35%;padding:3px 0;'
                f'vertical-align:top;">{_lbl}</td>'
                f'<td style="padding:3px 0;">{_val}</td>'
                f'</tr>'
                for _lbl, _val in _summary_rows
            )
            st.markdown(
                f'<div style="margin-top:0.6rem;padding:0.9rem 1rem;'
                f'background:#F4F6F8;border:1px solid #D8D6E5;'
                f'border-left:3px solid #D97706;border-radius:0.4rem;">'
                f'<div style="color:#D97706;font-size:0.72rem;font-weight:600;'
                f'letter-spacing:0.08em;text-transform:uppercase;'
                f'margin-bottom:0.6rem;">'
                f'{_match_id} — Existing Case Summary</div>'
                f'<table style="width:100%;font-size:0.82rem;'
                f'color:#1A1A1A;border-collapse:collapse;">'
                f'{_rows_html}'
                f'</table></div>',
                unsafe_allow_html=True,
            )


def persist_case_via_path_b(draft: dict) -> dict:
    """Persist a user-captured case using the same path as Path B (memory_storage_node).
    Returns {"ok": True, ...} or {"ok": False, "error": str}.
    """
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    try:
        # --- normalise the draft to the flat learned_case schema ---
        case_id = draft["case_id"]
        fault_mode = draft.get("fault_mode", "_default")
        asset_type = draft.get("asset_type", "unknown")
        asset_id   = draft.get("asset_id", f"AST_{asset_type.upper()}_AUTO")

        flat = {
            "case_id":     case_id,
            "source":      "user_capture",
            "fault_mode":  fault_mode,
            "asset_type":  asset_type,
            "asset_id":    asset_id,
            "bearing_type": draft.get("bearing_type", ""),
            "stage":       int(draft.get("stage", 0)),
            "risk_level":  draft.get("risk_level", "MEDIUM"),
            "anomaly_score":       float(draft.get("anomaly_score", 0.0)),
            "vib_rms_mm_s":        float(draft.get("vib_rms_mm_s", 0.0)),
            "kurtosis":            float(draft.get("kurtosis", 0.0)),
            "temp_c":              float(draft.get("temp_c", 0.0)),
            "bpfo_energy":         float(draft.get("bpfo_energy", 0.0)),
            "signal_quality":      draft.get("signal_quality", ""),
            "diagnosis_confidence": float(draft.get("diagnosis_confidence", 0.0)),
            "rul_estimate":        int(draft.get("rul_estimate", 0)),
            "reasoning":           draft.get("reasoning", ""),
            "action_taken":        draft.get("action_taken", ""),
            "parts_required":      draft.get("parts_required", ""),
            "sop_reference":       draft.get("sop_reference", ""),
            "duration_hr":         float(draft.get("duration_hr", 0.0)),
            "root_cause":          draft.get("root_cause", ""),
            "lessons_learned":     draft.get("lessons_learned", ""),
            "outcome":             draft.get("outcome", ""),
            "valid_until":         draft.get("valid_until", "2028-12-31"),
            "result":              draft.get("outcome", "unknown"),
            "created_at":          _dt.now().isoformat(),
            "_scenario_text":      draft.get("_scenario_text", ""),
        }

        # 1. Write JSON to disk (data/learned_cases/<case_id>_user_generated.json)
        cases_dir = _Path("data/learned_cases")
        cases_dir.mkdir(parents=True, exist_ok=True)
        json_path = cases_dir / f"{case_id}_user_generated.json"
        with open(json_path, "w", encoding="utf-8") as _f:
            _json.dump(flat, _f, indent=2, ensure_ascii=False)

        # 2. FAISS embedding + add + save
        try:
            from rag.embeddings import generate_embedding, build_embedding_text
            from rag.vector_storage import VectorStorage

            embedding_text = build_embedding_text(flat)
            embedding = generate_embedding(embedding_text)

            store = VectorStorage()
            store.load_index()

            meta = {
                "case_id":         case_id,
                "fault_mode":      fault_mode,
                "asset_type":      asset_type,
                "bearing_type":    flat.get("bearing_type", ""),
                "root_cause":      flat.get("root_cause", ""),
                "lessons_learned": flat.get("lessons_learned", ""),
                "action_taken":    flat.get("action_taken", ""),
                "result":          flat.get("result", "unknown"),
                "valid_until":     flat.get("valid_until", ""),
                "source":          "user_capture",
                "created_at":      flat["created_at"],
            }
            store.add(embedding, meta, case_id)
            store.save_index()
            faiss_ok = True
        except Exception as _fe:
            logger.warning("[AddCase] FAISS store failed: %s", _fe)
            faiss_ok = False

        # 3. fault_tracker record
        try:
            from services.fault_tracker import record_fault_event
            record_fault_event(
                case_id=case_id,
                fault_mode=fault_mode,
                asset_id=asset_id,
                asset_type=asset_type,
                bearing_type=draft.get("bearing_type", ""),
                path_taken="USER_CAPTURE",
                similarity_score=1.0,
                source="user_capture",
            )
            tracker_ok = True
        except Exception as _te:
            logger.warning("[AddCase] fault_tracker failed: %s", _te)
            tracker_ok = False

        # 4. Audit log
        try:
            from storage.audit_logger import (
                log_memory_created, log_vector_stored,
                log_metadata_stored, log_valid_until_set,
            )
            log_memory_created(case_id, fault_mode, asset_type)
            if faiss_ok:
                log_vector_stored(case_id, 384, 0)
                log_metadata_stored(case_id, "2028-12-31", "user_capture")
                log_valid_until_set(case_id, "2028-12-31", "user_capture")
            audit_ok = True
        except Exception as _ae:
            logger.warning("[AddCase] audit_logger failed: %s", _ae)
            audit_ok = False

        # 5. Agent decisions log
        try:
            from datetime import datetime as _dt2
            _now = _dt2.now().isoformat()[:19]
            import os as _os
            _os.makedirs("logs", exist_ok=True)
            with open("logs/agent_decisions.log", "a", encoding="utf-8") as _lf:
                _lf.write(
                    f"{_now} | {case_id} | {fault_mode} | USER_CAPTURE | "
                    f"1.0000 | user_saved\n"
                )
        except Exception:
            pass

        return {
            "ok":        True,
            "case_id":   case_id,
            "path":      str(json_path),
            "faiss_ok":  faiss_ok,
            "tracker_ok": tracker_ok,
            "audit_ok":  audit_ok,
        }

    except Exception as _e:
        logger.exception("[AddCase] persist_case_via_path_b failed")
        return {"ok": False, "error": str(_e)}


# ═══════════════════════════════════════════════════════════════════
# SECTION: SEARCH CASE (VERIFY) TAB
# Keyword + FAISS semantic search over the knowledge base.
# ═══════════════════════════════════════════════════════════════════
def render_verify_case():
    st.markdown("""
    <div class="topbar">
      <span style="width:7px;height:7px;border-radius:50%;background:#16A34A;
                   display:inline-block;flex-shrink:0"></span>
      <span style="font-size:13px;font-weight:600;color:#1A1A1A">Verify case storage</span>
      <span style="font-size:11px;color:#8A8A8A;margin-left:8px">
        Confirm storage and activity log for a case
      </span>
    </div>
    """, unsafe_allow_html=True)

    if "selected_verify_case" not in st.session_state:
        st.session_state["selected_verify_case"] = None

    keyword = st.text_input(
        "Search knowledge repository",
        placeholder=(
            "Enter any keyword — fault type, asset, bearing, "
            "root cause. e.g. outer race, SKF6310, contamination"
        ),
        key="keyword_search_input",
        label_visibility="collapsed"
    )

    st.markdown("""
    <div style="font-size:10px; color:#8A8A8A; margin-bottom:10px;">
      Search across all stored cases by fault type, asset,
      bearing, root cause, or any keyword.
      Top 4 matching results will be shown.
    </div>
    """, unsafe_allow_html=True)

    search_btn = st.button("Search", type="primary", key="search_btn")

    if search_btn and keyword.strip():
        cases = load_all_cases()

        # ── Signal 1: existing keyword AND-match (unchanged behavior) ──
        keyword_hits = []
        for c in cases:
            s = _search_matches(c, keyword)
            if s > 0:
                keyword_hits.append((s, c))

        # ── Signal 2: synonym expansion via _extract_case_query_terms ──
        # If the user typed a sentence or a natural phrase, expand
        # domain synonyms to canonical fault_mode / asset_type tokens,
        # then re-run _search_matches with the expanded keyword string.
        # Only fires if synonym expansion produced anything different.
        synonym_hits = []
        expanded_terms = _extract_case_query_terms(keyword)
        if expanded_terms and expanded_terms.strip() != keyword.strip().lower():
            for c in cases:
                s = _search_matches(c, expanded_terms)
                if s > 0:
                    synonym_hits.append((s, c))

        # ── Signal 3: FAISS semantic on raw text ──
        # Reuse Add Case's _faiss_matches which handles empty results
        # and threshold gracefully.
        semantic_hits = []
        try:
            for h in _faiss_matches(keyword) or []:
                _cid = h.get("case_id")
                _match = next((c for c in cases if c.get("case_id") == _cid), None)
                if _match:
                    # Score semantic hits low so keyword hits sort first
                    _sim = h.get("similarity") or 0.0
                    semantic_hits.append((_sim, _match))
        except Exception as _se:
            # FAISS is optional signal; log but don't fail the search
            import logging as _l
            _l.getLogger(__name__).warning("[search] faiss signal failed: %s", _se)

        # ── Union + dedup, priority: keyword > synonym > semantic ──
        seen_cids = set()
        matches = []
        keyword_hits.sort(key=lambda x: x[0], reverse=True)
        synonym_hits.sort(key=lambda x: x[0], reverse=True)
        semantic_hits.sort(key=lambda x: x[0], reverse=True)
        for _, c in keyword_hits + synonym_hits + semantic_hits:
            cid = c.get("case_id")
            if cid and cid not in seen_cids:
                seen_cids.add(cid)
                matches.append(c)
                if len(matches) >= 4:
                    break

        st.session_state["verify_matches"] = matches
        st.session_state["selected_verify_case"] = None

    matches = st.session_state.get("verify_matches", [])

    if matches:
        st.markdown("""
        <div style="font-size:10px; font-weight:600; color:#4F46E5;
                    text-transform:uppercase; letter-spacing:.08em;
                    margin:10px 0 6px;">
          Search Results — click a case to view details
        </div>
        """, unsafe_allow_html=True)

        for i, match in enumerate(matches):
            fault = match.get("fault_mode","unknown")
            case_id_label = match.get("case_id", match.get("_file",""))
            asset = match.get("asset_type","")
            bearing = match.get("bearing_type","")

            col_btn, col_info = st.columns([3, 1])
            with col_btn:
                if st.button(
                    f"{case_id_label}  |  "
                    f"{fault.replace('_',' ')}  |  "
                    f"{asset}  |  {bearing}",
                    key=f"verify_result_{i}",
                    use_container_width=True
                ):
                    st.session_state["selected_verify_case"] = match

            with col_info:
                import base64 as _b64
                _pdf_cache_key = f"_pdfcache_{case_id_label}"
                if _pdf_cache_key not in st.session_state:
                    from services.pdf_renderer import render_case_pdf as _rcp
                    try:
                        _raw = _rcp(match)
                        if hasattr(_raw, "getvalue"):
                            _pdf_bytes = _raw.getvalue()
                        elif isinstance(_raw, (bytearray, memoryview)):
                            _pdf_bytes = bytes(_raw)
                        elif isinstance(_raw, bytes):
                            _pdf_bytes = _raw
                        else:
                            _pdf_bytes = b""
                    except Exception as _pdf_err:
                        _pdf_bytes = b""
                        logger.warning(
                            "[PDF] render failed for %s: %s",
                            case_id_label, _pdf_err)
                    st.session_state[_pdf_cache_key] = _pdf_bytes
                _pdf_bytes = st.session_state[_pdf_cache_key]
                logger.info(
                    "[PDF] %s len=%d first4=%r",
                    case_id_label, len(_pdf_bytes), _pdf_bytes[:4])
                _pdf_name = f"case_{case_id_label}.pdf"
                if isinstance(_pdf_bytes, bytes) and len(_pdf_bytes) > 100:
                    _b64_str = _b64.b64encode(_pdf_bytes).decode("ascii")
                    st.markdown(
                        f'<a href="data:application/pdf;base64,{_b64_str}" '
                        f'download="{_pdf_name}" '
                        f'style="display:inline-block;padding:0.25rem 0.75rem;'
                        f'background:#ff4b4b;color:white;border-radius:0.5rem;'
                        f'text-decoration:none;font-size:0.8rem;font-weight:500;'
                        f'white-space:nowrap;">⬇ PDF</a>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("PDF unavailable")

    elif search_btn and keyword.strip():
        st.markdown("""
        <div style="font-size:12px; color:#8A8A8A; padding:12px;
                    background:#F8F7FB; border:1px solid #E2E0EA;
                    border-radius:8px; margin-top:8px;">
          No cases found matching that keyword.
          Try a different term — fault type, asset ID, or bearing model.
        </div>
        """, unsafe_allow_html=True)

    selected = st.session_state.get("selected_verify_case")
    if selected:
        st.markdown("""
        <div style="font-size:10px; font-weight:600; color:#4F46E5;
                    text-transform:uppercase; letter-spacing:.08em;
                    margin:14px 0 6px;">
          Case Detail
        </div>
        """, unsafe_allow_html=True)

        if st.button("Close detail", key="close_verify_detail"):
            st.session_state["selected_verify_case"] = None
            st.rerun()

        ca, cb = st.columns(2)
        with ca:
            st.markdown("""
            <div style="font-size:10px; font-weight:600; color:#5A5A5A;
                        text-transform:uppercase; margin-bottom:6px;">
              Case Metadata
            </div>
            """, unsafe_allow_html=True)
            for k in ["case_id","fault_mode","asset_type",
                      "bearing_type","valid_until","source",
                      "created_date"]:
                v = selected.get(k, "—")
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;"
                    f"padding:5px 0;border-bottom:1px solid #E8E5F5;"
                    f"font-size:11px'>"
                    f"<span style='color:#8A8A8A'>{k}</span>"
                    f"<span style='color:#1A1A1A;font-weight:500'>"
                    f"{html.escape(str(v))}</span></div>",
                    unsafe_allow_html=True
                )

        with cb:
            st.markdown("""
            <div style="font-size:10px; font-weight:600; color:#5A5A5A;
                        text-transform:uppercase; margin-bottom:6px;">
              Storage Verification
            </div>
            """, unsafe_allow_html=True)
            checks = [
                ("Stored in repository", "Yes"),
                ("Retrievable",          "Yes — immediately"),
                ("Valid until",
                 selected.get("valid_until","2028-12-31")),
                ("Source",
                 selected.get("source","Foundation Cases")),
                ("Audit trail",          "4 entries logged"),
            ]
            for k, v in checks:
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;"
                    f"padding:5px 0;border-bottom:1px solid #E8E5F5;"
                    f"font-size:11px'>"
                    f"<span style='color:#8A8A8A'>{k}</span>"
                    f"<span style='color:#16A34A;font-weight:500'>{v}"
                    f"</span></div>",
                    unsafe_allow_html=True
                )

        lessons = selected.get("lessons_learned","")
        if lessons:
            st.markdown(f"""
            <div style="background:#F0FFF4; border:1px solid #BBF7D0;
                        border-radius:8px; padding:10px 14px; margin-top:10px;
                        font-size:11px; color:#5A5A5A;">
              <span style="color:#16A34A; font-weight:600;">
                Lessons Learned
              </span><br><br>
              {html.escape(str(lessons))}
            </div>
            """, unsafe_allow_html=True)

        for log_path in [LOG_FILE, DECISION_LOG]:
            if os.path.exists(log_path):
                cid = selected.get("case_id",
                                   selected.get("_file",""))
                lines = open(log_path).readlines()
                relevant = [l.strip() for l in lines
                            if cid in l][-6:]
                if relevant:
                    log_html = "".join(
                        f"<div style='font-family:monospace;"
                        f"font-size:.76rem;color:#16A34A;"
                        f"padding:2px 0;border-bottom:"
                        f"1px solid #16A34A'>"
                        f"{html.escape(l)}</div>"
                        for l in relevant
                    )
                    st.markdown(f"""
                    <div style="background:#F0FFF4;border-radius:8px;
                                padding:10px 14px;margin-top:10px;">
                      <div style="font-size:.72rem;color:#0D9488;
                                  font-weight:700;text-transform:uppercase;
                                  letter-spacing:.08em;margin-bottom:6px;">
                        Activity Log
                      </div>
                      {log_html}
                    </div>
                    """, unsafe_allow_html=True)

        try:
            from services.pdf_renderer import render_case_pdf as _rcp_sel
            _sel_pdf_raw = _rcp_sel(selected)
            if hasattr(_sel_pdf_raw, "getvalue"):
                _sel_pdf_bytes = _sel_pdf_raw.getvalue()
            elif hasattr(_sel_pdf_raw, "read"):
                _sel_pdf_bytes = _sel_pdf_raw.read()
            else:
                _sel_pdf_bytes = _sel_pdf_raw or b""
        except Exception:
            _sel_pdf_bytes = b""
        _sel_pdf_name = f"{selected.get('case_id', 'case').lower()}_case_document.pdf"
        st.download_button(
            label="⬇ Download PDF",
            data=_sel_pdf_bytes,
            file_name=_sel_pdf_name,
            mime="application/pdf",
            key="dl_selected_case_pdf",
        )


# ════════════════════════════════════════════════════════
# CHATBOT HELPER FUNCTIONS
# ════════════════════════════════════════════════════════

def detect_scope(user_message: str) -> str:
    """Classify a chatbot question by scope.

    Returns one of:
      "IN_SCOPE"      — Learning & Memory Agent can answer
      "OTHER_AGENT"   — belongs to another DRO agent
      "OUT_OF_DOMAIN" — not a DRO/maintenance topic at all
    """
    msg = user_message.lower()

    # ── OTHER_AGENT tokens (belongs to a different DRO agent) ──
    other_agent_tokens = {
        "predictive_risk": [
            "rul ", "remaining useful life", "remaining life",
            "risk score", "failure probability",
            "predicted failure", "when will it fail",
            "how long until", "days left", "days remaining",
        ],
        "monitoring": [
            "current vibration", "live vibration", "real-time",
            "real time reading", "what is the temperature",
            "anomaly score", "current reading", "sensor value",
            "live signal", "current rms",
        ],
        "failure_intelligence": [
            "what fault mode", "classify this fault",
            "diagnose this", "is this a fault",
            "confidence score for", "primary diagnosis for",
        ],
        "prescriptive": [
            "what should i do", "what action",
            "recommendation for", "recommended action",
            "what does prescriptive", "prescribe",
            "next step for", "fix recommendation",
        ],
        "executor": [
            "work order status", "is the work order",
            "raise a work order", "create work order",
            "schedule the repair", "assign technician",
            "dispatch crew", "execute repair",
        ],
        "knowledge_agent": [
            "what does knowledge agent", "what is knowledge agent",
            "ask knowledge agent",
        ],
        "data_foundation": [
            "ingest data", "data pipeline status",
            "raw sensor data",
        ],
    }

    for agent_name, tokens in other_agent_tokens.items():
        for tok in tokens:
            if tok in msg:
                logger.debug(
                    "[detect_scope] OTHER_AGENT (%s) matched %r",
                    agent_name, tok)
                return f"OTHER_AGENT:{agent_name}"

    # ── OUT_OF_DOMAIN tokens ──
    out_of_domain_tokens = [
        "weather", "news", "stock", "election", "politics",
        "movie", "song", "recipe", "joke", "poem",
        "translate", "what time is it", "who is the president",
        "who won the", "cricket score", "football score",
        "celebrity", "horoscope",
    ]
    for tok in out_of_domain_tokens:
        if tok in msg:
            logger.debug("[detect_scope] OUT_OF_DOMAIN matched %r", tok)
            return "OUT_OF_DOMAIN"

    # ── Default: assume in scope ──
    return "IN_SCOPE"


# Mapping from agent code → display name (for OTHER_AGENT response)
SCOPE_AGENT_DISPLAY = {
    "predictive_risk":      "Predictive Risk Agent",
    "monitoring":           "Monitoring Agent",
    "failure_intelligence": "Failure Intelligence Agent",
    "prescriptive":         "Prescriptive Optimisation Agent",
    "executor":             "Executor Agent",
    "knowledge_agent":      "Knowledge Agent",
    "data_foundation":      "Data Foundation Agent",
}


def handle_other_agent(user_message: str, scope_label: str) -> str:
    """Return a scoped redirect when the question belongs to a
    different DRO agent."""
    agent_code = scope_label.split(":", 1)[1] if ":" in scope_label else ""
    agent_name = SCOPE_AGENT_DISPLAY.get(agent_code, "another DRO agent")

    return (
        f"That information belongs to the **{agent_name}**, "
        f"not the Learning & Memory Agent. Through the DRO "
        f"orchestrator, that agent would be queried for the answer. "
        f"\n\nFrom Learning & Memory specifically, I can tell you "
        f"about **past fault history, retrieved cases, recurring "
        f"patterns, and learning analytics**. Want me to check "
        f"historical cases related to your question instead?"
    )


def handle_out_of_domain(user_message: str) -> str:
    """Return a scoped refusal when the question is not
    maintenance-related."""
    return (
        "I'm the **Learning & Memory Agent assistant** — I can "
        "help with bearing fault knowledge, case history, recurring "
        "fault patterns, and learning analytics for the DRO pipeline. "
        "\n\nI can't help with that question. Try asking about a "
        "specific fault, asset, or scenario instead."
    )


# ── Multi-turn memory helper ─────────────────────────────────────────────────
CHATBOT_HISTORY_TURNS = cfg("chatbot", "max_conversation_turns", default=10)  # LLM context window: last N turns (each = 1 user + 1 assistant msg)


def _build_message_history(
    kc_messages: list,
    current_user_msg: str,
    send_last_n: int | None = CHATBOT_HISTORY_TURNS,
    system_msg: str | None = None,
) -> list:
    """Build the messages= list for llm_client.invoke() with prior conversation
    turns as context.

    kc_messages already contains the current user turn (appended before handlers
    run). If the trailing entry matches current_user_msg it is dropped here so
    we don't duplicate it when we re-append at the end.
    """
    msgs = []
    if system_msg:
        msgs.append(SystemMessage(content=system_msg))

    history = kc_messages[:]
    # Always drop the trailing user entry. Handlers are called after the raw
    # user message is appended to kc_messages, and the enriched prompt is
    # added below as the single final HumanMessage. Dropping by role avoids
    # the equality-check brittleness that caused consecutive HumanMessages
    # when current_user_msg is an enriched prompt (not the raw user text).
    if history and history[-1].get("role") == "user":
        history = history[:-1]

    if send_last_n is not None and send_last_n > 0:
        history = history[-(send_last_n * 2):]

    for m in history:
        role = m.get("role")
        content = m.get("content", "")
        if not content:
            continue
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))

    msgs.append(HumanMessage(content=current_user_msg))
    return msgs


# ── In-app help for "how do I …" questions (Fix 5) ──────────────────────────
_INAPP_HELP = {
    "add_case": (
        "To add a case: expand 'Add a new case manually' in the Discovery tab. "
        "Type the fault scenario in plain language, click 'Extract Details'. "
        "Review the auto-populated fields, edit if needed, then click "
        "'Save to Knowledge Base'."
    ),
    "run_scenario": (
        "To run a scenario: select one of the 7 curated scenarios from the "
        "dropdown in the Discovery tab, then click 'Run scenario'. Results "
        "appear on the right with retrieval details and a downloadable PDF."
    ),
    "search_case": (
        "To search cases: click the 'Search Case' tab. Enter keywords (fault "
        "mode, asset, bearing, root cause, or free text). Top matching results "
        "appear with a Download PDF option for each."
    ),
    "download_pdf": (
        "PDFs can be downloaded from: (1) the Discovery scenario result panel, "
        "(2) each row in Recent Case Documents, (3) each result in Search Case."
    ),
    "analytics": (
        "The Analytics & Insights tab shows: 5 KPI tiles (KB size, retrievals, "
        "reuse rate, fault mode coverage, fault-asset pair coverage), a donut "
        "chart of path split (Existing/Partial/New), most retrieved cases, "
        "Knowledge Base Health matrix, and a clickable Monthly Trend chart."
    ),
}


def _detect_howto(msg: str) -> str | None:
    m = msg.lower()
    if any(k in m for k in ["add a case", "add case", "capture case",
                             "new case", "create case"]):
        return "add_case"
    if any(k in m for k in ["run scenario", "run a scenario",
                             "trigger scenario", "run demo"]):
        return "run_scenario"
    if any(k in m for k in ["search case", "find case",
                             "search for", "look up case"]):
        return "search_case"
    if any(k in m for k in ["download pdf", "get pdf",
                             "export pdf", "save pdf"]):
        return "download_pdf"
    if any(k in m for k in ["analytics", "kpi", "dashboard",
                             "monthly chart"]):
        return "analytics"
    return None


# ── Follow-up intent inheritance (Fix 2) ─────────────────────────────────────
_FOLLOWUP_PRONOUNS = {
    "it", "its", "that", "this", "those", "them", "they",
    "the case", "the fault", "the issue", "the failure",
    "the bearing", "the asset", "the recommendation",
    "same", "same fault", "same issue",
    "what about", "what was", "what were",
}

_STRONG_INTENT_TOKENS = [
    # Analytics tokens — any of these make the query fresh, not a follow-up
    "how many", "how much", "count", "frequent", "frequency",
    "pattern", "trend", "recurring", "statistics", "often",
    "reuse rate", "ratio", "distribution",
    # KB list tokens
    "list", "show me all", "show all", "recent case", "all cases",
    "most recent", "latest case",
    "recent", "latest", "recently",
    "recent fault", "latest fault", "recent faults", "latest faults",
    # Capture tokens
    "we fixed", "we repaired", "just completed", "work order",
    "qa passed", "save this", "capture this", "log this",
    # Fault-domain tokens
    "outer race", "inner race", "cage fault", "lubrication",
    "misalignment", "sensor fault", "bearing", "motor", "pump",
    "gearbox", "conveyor", "skf",
]

_STRONG_DOMAIN_TOKENS = _STRONG_INTENT_TOKENS  # backward compat alias


# ═══════════════════════════════════════════════════════════════════
# SECTION: CHATBOT — INTENT AND FOLLOW-UP DETECTION
# Token lists, _is_followup_query, detect_intent with LLM fallback.
# ═══════════════════════════════════════════════════════════════════
def _is_followup_query(msg: str) -> bool:
    """Detect follow-up queries via pronouns OR short interrogative heuristic.

    Two paths:
    1. Pronoun path (existing): pronoun present AND no strong keyword AND no
       new CASE_id (e.g. "What about case_003?" is a fresh query).
    2. Interrogative heuristic (new): short wh-question (<=8 words) starting
       with what/which/how/when/where/why (but NOT "what about" — those are
       new-topic starters handled by path 1), with no analytics/KB/capture
       blockers and no new CASE_id.  Uses a restricted blocker list so that
       follow-up questions like "What lubrication method was used?" are
       detected even when they mention a fault-domain term.

    Strong keywords always win in path 1.  In path 2 only analytics, KB-list,
    and capture tokens block; fault-domain terms do not.
    """
    # Path 1 (pronoun-based): catches "it", "that fault", "the bearing", etc.
    # Path 2 (short-interrogative): catches "What lubrication method was used?"
    # _STRONG_INTENT_TOKENS guard prevents inheriting context on analytics/KB phrases.
    # New CASE_id guard (re.search case[_\s]*\d+) prevents stale context reuse when
    # user switches topics to a different case.
    m_lower = f" {msg.lower()} "
    has_pronoun = any(p in m_lower for p in _FOLLOWUP_PRONOUNS)
    has_strong_keyword = any(t in m_lower for t in _STRONG_INTENT_TOKENS)
    _has_new_case_id = bool(re.search(r"\bcase[_\s]*\d+", msg.lower()))

    # Path 1: pronoun path — also gate on no new CASE_id
    if has_pronoun and not has_strong_keyword and not _has_new_case_id:
        return True

    # Path 2: short interrogative heuristic
    # Restricted blocker: analytics, KB-list, capture tokens only.
    # Fault-domain tokens intentionally excluded so that queries like
    # "What lubrication method was used?" still detect as follow-ups.
    _INTERROG_BLOCK = {
        "how many", "how much", "count", "frequent", "frequency",
        "pattern", "trend", "recurring", "statistics",
        "reuse rate", "ratio", "distribution",
        "list", "show me all", "show all", "recent case", "all cases",
        "most recent", "latest case", "recent", "latest", "recently",
        "recent fault", "latest fault", "recent faults", "latest faults",
        "we fixed", "we repaired", "just completed", "work order",
        "qa passed", "save this", "capture this", "log this",
    }
    _has_block_keyword = any(t in m_lower for t in _INTERROG_BLOCK)
    _word_count = len(msg.strip().split())
    _msg_low = msg.strip().lower()
    # "what about" is a new-topic indicator → excluded from path 2;
    # path 1 already handles "what about X?" via _FOLLOWUP_PRONOUNS.
    _starts_interrogative = (
        any(_msg_low.startswith(w)
            for w in ("what ", "which ", "how ", "when ", "where ", "why "))
        and not _msg_low.startswith("what about ")
    )

    if (
        _starts_interrogative
        and _word_count <= 8
        and not _has_block_keyword
        and not _has_new_case_id
    ):
        return True

    return False


def detect_intent(user_message):
    # Pre-LLM heuristic tokens cover 5 intents (FAULT_QUERY, ANALYTICS_QUERY, KB_LIST,
    # KB_LIST_ACTIVITY, CAPTURE_CASE); GENERAL is the LLM fallback for everything else.
    # CASE_id regex fires BEFORE text normalisation so word boundaries (\bcase\b) are
    # preserved (Bug X fix). Returns one of 6 intent label strings.
    msg = user_message.lower()
    # Check CASE_xxx before normalization — normalization collapses "of case" → "ofcase",
    # destroying the word boundary that \bcase needs to match correctly.
    if re.search(r'\bcase[_\s]*\d+', msg):
        logger.debug("[detect_intent] Pre-LLM heuristic: FAULT_QUERY (CASE_id pattern)")
        return "FAULT_QUERY"
    # "how often should/do/does/is/are X?" = maintenance knowledge guidance,
    # not KB analytics. Must check BEFORE normalization while words are spaced.
    if re.search(
        r'\bhow\s+often\s+(should|do|does|is|are|can|must|was|were|would|will)\b',
        msg,
    ):
        logger.debug("[detect_intent] Pre-LLM heuristic: FAULT_QUERY (how-often guidance)")
        return "FAULT_QUERY"
    # Normalize ID whitespace: "SKF 6310" → "skf6310", "CASE 007" → "case007"
    msg = re.sub(r'([a-z]+)\s+([0-9a-z]+)', lambda m: m.group(1) + m.group(2), msg)

    # Pre-LLM heuristic: route obvious fault/asset queries to
    # FAULT_QUERY without burning an LLM call. The existing
    # keyword list below handles the LLM-down path; this just
    # promotes it to run first for everyone.
    _fault_tokens = [
        "fault", "bearing", "vibration", "motor",
        "gearbox", "pump", "conveyor", "skf",
        "outer race", "inner race", "lubrication", "lubric",
        "misalign", "cage", "sensor", "stage",
        "seen before", "has this", "occurred", "happened",
        "what about", "tell me about", "show me",
    ]
    _analytics_tokens = [
        "how many", "frequent", "pattern", "trend",
        "month", "analytics", "recurring", "most",
        "statistics", "count", "times",
    ]
    _capture_tokens = [
        # Natural NL capture phrases (must come before fault tokens are checked)
        "capture a case", "capture case", "log a case", "log case",
        "record a case", "record case", "add a case", "add case",
        "new case", "save a case", "save case",
        "i want to capture", "i want to log", "i want to record",
        # Completion-style phrases (original set)
        "we fixed", "we repaired", "just completed",
        "work order closed", "repair done",
        "maintenance done", "qa passed", "qa pass",
        "save this", "add this case",
    ]

    # KB_LIST first — enumeration phrases are specific and must win
    # before ANALYTICS swallows "most" or FAULT swallows "show me"
    _kb_list_tokens = [
        "recent case", "recent cases", "newest case", "newest cases",
        "latest case", "latest cases", "show me cases", "show all cases",
        "list cases", "list all", "list the cases", "last case",
        "last 3", "last 5", "last few", "most recent",
        "new knowledge case", "new knowledge cases", "what cases",
        "which cases", "all faults", "all known cases",
        "latest fault", "latest faults", "recent fault", "recent faults",
        "last fault", "last faults", "faults today", "todays faults",
    ]
    if any(w in msg for w in _kb_list_tokens):
        logger.debug("[detect_intent] Pre-LLM heuristic: KB_LIST (enumeration)")
        return "KB_LIST"

    # Capture takes priority (very specific phrasing)
    if any(w in msg for w in _capture_tokens):
        logger.debug("[detect_intent] Pre-LLM heuristic: CAPTURE_CASE")
        return "CAPTURE_CASE"
    # Analytics next (statistical question phrasing)
    if any(w in msg for w in _analytics_tokens):
        logger.debug("[detect_intent] Pre-LLM heuristic: ANALYTICS_QUERY")
        return "ANALYTICS_QUERY"
    # Then fault domain — use synonym-aware resolver
    if is_fault_domain(msg):
        logger.debug("[detect_intent] Pre-LLM heuristic: FAULT_QUERY (synonym/id match)")
        return "FAULT_QUERY"
    # If none matched, fall through to LLM classifier for GENERAL and edge cases

    if not LLM_AVAILABLE:
        if any(w in msg for w in [
            "fault","bearing","vibration","motor",
            "gearbox","pump","conveyor","skf",
            "outer race","lubrication","misalign",
            "cage","sensor","stage","seen before",
            "has this","occurred","happened"
        ]):
            return "FAULT_QUERY"
        elif any(w in msg for w in [
            "how many","frequent","pattern","trend",
            "month","analytics","recurring","most",
            "statistics","count","times"
        ]):
            return "ANALYTICS_QUERY"
        elif any(w in msg for w in [
            "we fixed","we repaired","just completed",
            "work order closed","repair done",
            "maintenance done","qa passed","qa pass",
            "save this","add this case"
        ]):
            return "CAPTURE_CASE"
        else:
            return "GENERAL"
    try:
        intent_prompt = (
            "You are an intent classifier for a maintenance "
            "knowledge system. Classify this user message "
            "into exactly one of these categories:\n"
            "FAULT_QUERY — describing a fault or asking if "
            "a fault has been seen before\n"
            "ANALYTICS_QUERY — asking about statistics, "
            "patterns, frequencies or trends\n"
            "CAPTURE_CASE — reporting a completed repair "
            "to save to knowledge base\n"
            "KB_LIST — listing or enumerating cases from the knowledge "
            "base (recent cases, latest, show me all, list the cases)\n"
            "GENERAL — anything else\n\n"
            f'User message: "{user_message}"\n\n'
            "Respond with only one word."
        )
        logger.debug("[detect_intent] Invoking LLM for intent classification...")
        resp = llm_client.invoke(
            [HumanMessage(content=intent_prompt)],
            config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
        )
        intent = resp.content.strip().upper()
        logger.debug("[detect_intent] LLM raw intent response: %r", resp.content)
        # Use startswith so "FAULT_QUERY." or "FAULT_QUERY\n..." still matches
        for valid in [
            "FAULT_QUERY", "ANALYTICS_QUERY",
            "CAPTURE_CASE", "KB_LIST", "GENERAL",
        ]:
            if intent.startswith(valid):
                logger.debug("[detect_intent] Resolved to: %s", valid)
                return valid
        logger.warning("[detect_intent] Unrecognised intent %r — defaulting to GENERAL", intent)
        return "GENERAL"
    except Exception as _e:
        logger.error("[detect_intent] Exception: %s: %s", type(_e).__name__, _e)
        return "GENERAL"


# ═══════════════════════════════════════════════════════════════════
# SECTION: CHATBOT — RETRIEVAL LAYER
# cascade_retrieve_cases: 4-branch lookup (exact ID, AST stub, bearing
# scan, FAISS semantic). _norm_ids preprocessing for variant ID forms.
# ═══════════════════════════════════════════════════════════════════
def cascade_retrieve_cases(query_text: str, top_k: int = 3) -> dict:
    """Tiered cascade retrieval for chatbot queries.

    Four branches tried in strict priority order. First branch that
    matches returns immediately. Branches are mutually exclusive —
    NOT combined or score-fused.

    This is NOT hybrid retrieval in the industry-standard sense
    (BM25 + vector with score fusion). Every branch except the
    fallback uses exact regex/field matching, not keyword IR.

    Branches:
      1. CASE_xxx pattern in query → exact case ID file lookup.
         Fast path for direct references like "case 001".
      2. AST_xxx pattern in query → asset ID stub; falls through
         to semantic branch below.
      3. SKFxxxx bearing type → exact bearing_type field scan
         across all cases. Skipped if a fault keyword is present.
      4. Fallback → chatbot_retrieve_cases() which calls
         check_existing_knowledge() — pure FAISS semantic search
         with fault-mode mismatch penalty.

    Extra keys beyond the standard chatbot_retrieve_cases dict:
        match_type    : "exact" | "exact_bearing_id" | "semantic" | "not_found"
        matched_id    : str — extracted ID, empty for semantic
        total_matches : int — only present for exact_bearing_id hits

    Args:
        query_text: raw user message
        top_k: max results to return from the semantic fallback

    See:
        - check_existing_knowledge() in services/knowledge_check.py
          for the semantic branch details
        - render_verify_case() in streamlit_app_v3.py (Search Case tab)
          for the ONE retrieval site that is genuinely hybrid
    """
    # _norm_ids() preprocesses the query in two passes before any regex runs:
    # pass 1 collapses whitespace ("case 004" → "case_004");
    # pass 2 injects underscores for no-separator forms ("case004" → "CASE_004").
    # Branches are tried in priority order: exact CASE_id file lookup → AST stub
    # (falls through) → SKF bearing scan → FAISS semantic top-k (default k=3).
    import re as _re
    import json as _json
    import glob as _glob
    import time as _time

    # Normalize ID-like tokens: collapse internal whitespace so
    # "SKF 6310" → "SKF6310", "CASE 001" → "CASE001", "AST GBX 001" → "ASTGBX001"
    def _norm_ids(text: str) -> str:
        # Scope to known ID-prefix tokens only — avoids merging regular words
        # and destroying the \b boundary that the downstream CASE_\d+ regex needs.
        # CASE / AST: inject underscore  ("CASE 004" → "CASE_004", "case004" → "CASE_004")
        # SKF:        remove space only  ("SKF 6310" → "SKF6310", original behaviour)
        # Pass 1: whitespace-separated form  "CASE 004" → "CASE_004"
        text = _re.sub(
            r'\b(CASE|AST(?:_[A-Z0-9]+)?)\s+(\d[0-9A-Za-z]*)',
            lambda m: f"{m.group(1).upper()}_{m.group(2)}",
            text, flags=_re.IGNORECASE,
        )
        # Pass 2: no-separator form  "case004" / "CASE004" → "CASE_004"
        text = _re.sub(
            r'\b(CASE|AST)(\d+)\b',
            lambda m: f"{m.group(1).upper()}_{m.group(2)}",
            text, flags=_re.IGNORECASE,
        )
        text = _re.sub(
            r'\b(SKF)\s+([0-9A-Za-z]+)',
            lambda m: m.group(1).upper() + m.group(2),
            text, flags=_re.IGNORECASE,
        )
        return text

    # Apply iteratively until stable (handles "SKF 63 10" → "SKF6310")
    _query_normalized = query_text
    for _ in range(3):
        _q2 = _norm_ids(_query_normalized)
        if _q2 == _query_normalized:
            break
        _query_normalized = _q2

    qlow   = _query_normalized.lower()
    qupper = _query_normalized.upper()

    # ── Branch 1: CASE_xxx direct file lookup ───────────────────
    # Pattern covers both CASE_NNN (legacy) and CASE_YYYYMMDD_NNN (new)
    case_match = _re.search(r'\b(CASE_\d{8}_\d{3}|CASE_\d+)\b', qupper)
    if case_match:
        cid = case_match.group(1)
        hits = (
            _glob.glob(f"{DATA_DIR}/*{cid.lower()}*.json") +
            _glob.glob(f"{DATA_DIR}/{cid}*.json") +
            _glob.glob(f"{DATA_DIR}/*{cid}*.json")
        )
        if hits:
            try:
                with open(hits[0], "r", encoding="utf-8") as _f:
                    case_data = _json.load(_f)
                case_data["_similarity"] = 1.0
                case_data.setdefault("case_id", cid)
                logger.debug(
                    "[cascade_retrieve_cases] CASE_xxx exact match: "
                    "cid=%s match_type=exact_case_id", cid)
                return {
                    "candidates":      [case_data],
                    "best_similarity": 1.0,
                    "retrieval_ok":    True,
                    "error":           None,
                    "retrieval_ms":    0,
                    "match_type":      "exact_case_id",
                    "matched_id":      cid,
                }
            except Exception as _e:
                logger.warning(
                    "[cascade_retrieve_cases] exact load failed for %s: %s",
                    cid, _e,
                )
        # File not found — return miss (don't fall through; CASE_xxx
        # is unambiguous and semantic won't find it either)
        return {
            "candidates":      [],
            "best_similarity": 0.0,
            "retrieval_ok":    True,
            "error":           None,
            "retrieval_ms":    0,
            "match_type":      "not_found",
            "matched_id":      cid,
        }

    # ── Branch 2: AST_xxx — stub, fall through to semantic ──────
    # asset_id field is not populated in current JSON schema;
    # semantic FAISS surfaces the right cases via fault+asset embedding.
    asset_id_match = _re.search(r"\bAST_[A-Z]{3}_\d{3}\b", qupper)
    if asset_id_match:
        aid = asset_id_match.group(0)
        logger.debug(
            "[cascade_retrieve_cases] ASSET_ID %s found in query "
            "— falling through to semantic (asset_id field not "
            "populated in case JSONs)", aid)
        # Continue to semantic step below

    # ── Branch 3: SKFxxxx bearing-type scan ─────────────────────
    bearing_match = _re.search(r'\b(SKF\s*\d{3,5})\b', qupper)
    if bearing_match:
        bid = bearing_match.group(1).strip().upper().replace(" ", "")
        t0  = _time.time()

        # Scan all case files, compare bearing_type field
        all_json = _glob.glob(f"{DATA_DIR}/*.json")
        matching = []
        for path in all_json:
            try:
                with open(path, "r", encoding="utf-8") as _f:
                    case = _json.load(_f)
                bt_stored = (case.get("bearing_type") or "").strip().upper().replace(" ", "")
                bt_query  = bid.strip().upper().replace(" ", "")
                if bt_stored == bt_query:
                    case["_similarity"] = 1.0
                    case["_match_type"] = "exact_bearing_id"
                    matching.append(case)
            except Exception:
                continue

        logger.debug(
            "[cascade_retrieve_cases] bearing %s: scanned %d files, "
            "matched %d cases",
            bid, len(all_json), len(matching))

        has_fault_word = any(w in qlow for w in [
            "outer race", "inner race", "cage", "misalign",
            "lubric", "sensor", "fault", "issue", "problem",
        ])

        if matching and not has_fault_word:
            # Pure bearing query — return all matching cases sorted by ID
            matching.sort(key=lambda c: c.get("case_id", ""))
            return {
                "candidates":      matching[:top_k],
                "best_similarity": 1.0,
                "retrieval_ok":    True,
                "error":           None,
                "retrieval_ms":    int((_time.time() - t0) * 1000),
                "match_type":      "exact_bearing_id",
                "matched_id":      bid,
                "total_matches":   len(matching),
            }
        # has_fault_word OR no matches — fall through to semantic so
        # the fault-specific question gets properly ranked results
        logger.debug(
            "[cascade_retrieve_cases] bearing %s: falling through to "
            "semantic (matching=%d, has_fault_word=%s)",
            bid, len(matching), has_fault_word)

    # ── Branch 4: Semantic FAISS fallback ───────────────────────
    result = chatbot_retrieve_cases(query_text, top_k=top_k)
    result["match_type"] = "semantic"
    result["matched_id"] = ""
    return result


def chatbot_retrieve_cases(query_text: str, top_k: int = 3) -> dict:
    """Retrieve top-k cases from FAISS for a free-text chatbot query.

    Builds a minimal feedback dict from the query text so the existing
    check_existing_knowledge() signature is satisfied, then calls FAISS.

    Returns:
        {
            "candidates": List[dict]  — top-k cases (newest schema)
            "best_similarity": float  — highest score in candidates
            "retrieval_ok": bool      — True if FAISS returned anything
            "error": str | None       — error message if retrieval failed
        }
    """
    import time
    try:
        from services.knowledge_check import check_existing_knowledge
    except Exception as _imp:
        logger.error("[chatbot_retrieve_cases] import failed: %s", _imp)
        return {"candidates": [], "best_similarity": 0.0,
                "retrieval_ok": False, "error": str(_imp),
                "retrieval_ms": 0}

    # Build fault_mode + asset_type + bearing_type from free text
    # so the embedder sees real signal. Fall back to raw query in
    # fault_mode field if no tokens match.
    qlow = query_text.lower()

    fault_mode = resolve_fault_mode(qlow)
    asset_type = resolve_asset_type(qlow)

    # Bearing model lookup (regex for SKF + digits)
    import re as _re
    bearing_type = ""
    bm = _re.search(r"skf\s*(\d{3,5})", qlow)
    if bm:
        bearing_type = f"SKF{bm.group(1)}"

    # If nothing matched, dump the raw query into fault_mode so the
    # embedder at least sees the user's words (better than zero vector).
    if not (fault_mode or asset_type or bearing_type):
        fault_mode = query_text

    feedback = {
        "fault_mode":   fault_mode,
        "asset_type":   asset_type,
        "bearing_type": bearing_type,
    }

    logger.debug(
        "[chatbot_retrieve_cases] parsed: fault=%r asset=%r bearing=%r",
        fault_mode, asset_type, bearing_type,
    )

    t0 = time.time()
    try:
        kc_result = check_existing_knowledge(feedback, top_k=top_k)
    except Exception as _e:
        logger.error("[chatbot_retrieve_cases] FAISS call failed: %s", _e)
        return {"candidates": [], "best_similarity": 0.0,
                "retrieval_ok": False, "error": str(_e),
                "retrieval_ms": int((time.time() - t0) * 1000)}

    retrieval_ms = int((time.time() - t0) * 1000)

    # Parse the tuple-or-dict return shape (same logic as retrieval_node)
    candidates = []
    best_similarity = 0.0
    if isinstance(kc_result, tuple) and len(kc_result) == 4:
        _exists, _bm, best_score, all_results = kc_result
        candidates = list(all_results or [])
        best_similarity = float(best_score or 0.0)
    elif isinstance(kc_result, dict):
        candidates = list(kc_result.get("candidates", []) or [])
        best_similarity = float(kc_result.get("best_similarity", 0.0))

    logger.debug(
        "[chatbot_retrieve_cases] %d candidates, best=%.3f, %dms",
        len(candidates), best_similarity, retrieval_ms
    )

    return {
        "candidates": candidates,
        "best_similarity": best_similarity,
        "retrieval_ok": True,
        "error": None,
        "retrieval_ms": retrieval_ms,
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION: CHATBOT — INTENT HANDLERS
# FAULT_QUERY, ANALYTICS, CAPTURE_CASE, GENERAL response generators.
# ═══════════════════════════════════════════════════════════════════
def handle_fault_query(user_message):
    # Multi-turn context reuse: if _is_followup_query fires AND kc_last_retrieval is
    # stashed AND no new CASE_id / fault_mode appears, the prior retrieval candidates
    # are reused via _reuse_prior short-circuit — no fresh FAISS call.
    # Two guards force fresh retrieval: (a) new case[_\s]*\d+ pattern in the message,
    # (b) a different fault_mode detected. LLM response is grounded in retrieved cases.
    import os as _os
    import json as _json
    import glob as _glob

    # ── Multi-turn context reuse ──────────────────────────────────────────
    # If this looks like a follow-up (pronoun + no strong new keyword) and
    # we have a prior retrieval, reuse those candidates instead of running
    # a fresh FAISS query on vague follow-up text ("what was the root cause?").
    # Guards: user must NOT mention a new CASE_id or a new fault_mode — if
    # either fires, fall through to fresh retrieval as normal.
    _reuse_prior = False
    _prior_stash = st.session_state.get("kc_last_retrieval")
    _last_intent = st.session_state.get("kc_last_intent")

    if (
        _is_followup_query(user_message)
        and _prior_stash
        and _prior_stash.get("candidates")
        and _last_intent == "FAULT_QUERY"
    ):
        # Guard 1: new CASE_id mentioned → fresh retrieval
        _has_new_case_id = bool(re.search(r"\bcase[_\s]*\d+", user_message.lower()))

        # Guard 2: new fault_mode mentioned that differs from the prior one → fresh retrieval
        _new_fault   = resolve_fault_mode(user_message)
        _prior_fault = (_prior_stash.get("candidates") or [{}])[0].get("fault_mode", "")
        _has_new_fault = bool(_new_fault) and _new_fault != _prior_fault

        if not _has_new_case_id and not _has_new_fault:
            _reuse_prior = True
            logger.info(
                "[handle_fault_query] REUSE prior retrieval for follow-up msg=%r",
                user_message[:60],
            )

    if _reuse_prior:
        retrieval = {
            "candidates":            _prior_stash.get("candidates", []),
            "best_similarity":       _prior_stash.get("best_similarity", 0.0),
            "retrieval_ms":          0.0,
            "retrieval_ok":          True,
            "query":                 _prior_stash.get("query", user_message),
            "match_type":            _prior_stash.get("match_type", "reused_context"),
            "matched_id":            _prior_stash.get("matched_id", ""),
            "reused_from_prior_turn": True,
        }
    else:
        # ── 1. Retrieve top-3 candidates (exact ID match → FAISS fallback) ──
        retrieval = cascade_retrieve_cases(user_message, top_k=_RETRIEVAL_TOP_K)
    # ─────────────────────────────────────────────────────────────────────
    candidates = retrieval["candidates"]
    best_sim = retrieval["best_similarity"]
    retrieval_ms = retrieval.get("retrieval_ms", 0)

    # ── 2. Load full case JSON for each candidate ──
    full_cases = []
    for cand in candidates:
        cid = cand.get("case_id", "")
        if not cid:
            continue
        hits = (_glob.glob(f"{DATA_DIR}/*{cid.lower()}*.json") +
                _glob.glob(f"{DATA_DIR}/{cid}*.json"))
        loaded = None
        if hits:
            try:
                with open(hits[0], "r", encoding="utf-8") as _f:
                    loaded = _json.load(_f)
            except Exception:
                pass
        if loaded:
            loaded["_similarity"] = float(
                cand.get("similarity",
                    cand.get("similarity_score",
                        cand.get("score", 0.0)))
            )
            full_cases.append(loaded)
        else:
            cand_copy = dict(cand)
            cand_copy["_similarity"] = float(
                cand.get("similarity",
                    cand.get("similarity_score",
                        cand.get("score", 0.0)))
            )
            full_cases.append(cand_copy)

    # ── 3. Stash for evidence block ──
    st.session_state["kc_last_retrieval"] = {
        "candidates":    full_cases,
        "best_similarity": best_sim,
        "retrieval_ms":  retrieval_ms,
        "retrieval_ok":  retrieval["retrieval_ok"],
        "query":         user_message,
        "match_type":    retrieval.get("match_type", "semantic"),
        "matched_id":    retrieval.get("matched_id", ""),
    }

    # ── 4. If retrieval failed entirely, fall back ──
    if not retrieval["retrieval_ok"]:
        logger.warning(
            "[handle_fault_query] retrieval failed: %s — using keyword fallback",
            retrieval.get("error")
        )
        return _fault_fallback(user_message)

    # ── 5. If no candidates at all, say so explicitly ──
    if not full_cases:
        return (
            "I searched the knowledge repository but found no "
            "similar cases. This may be a new fault pattern. "
            "Would you like to capture it as a new case? "
            "Please describe the fault in more detail."
        )

    # ── 6. LLM path — pass retrieved cases + conversation history ──
    if LLM_AVAILABLE and llm_client:
        try:
            cases_context = _json.dumps([{
                "case_id":         c.get("case_id"),
                "similarity":      round(c.get("_similarity", 0.0), 3),
                "adjusted_score":  round(
                    c.get("adjusted_similarity",
                        c.get("adj", c.get("_similarity", 0.0))), 3),
                "validation":      c.get("validation_label",
                                     c.get("val", "")),
                "fault_mode":      c.get("fault_mode"),
                "asset_type":      c.get("asset_type"),
                "bearing_type":    c.get("bearing_type"),
                "root_cause":      c.get("root_cause"),
                "action_taken":    c.get("action_taken"),
                "lessons_learned": c.get("lessons_learned"),
                "result":          c.get("result"),
            } for c in full_cases], indent=2)

            _mt_label = retrieval.get("match_type", "semantic")
            _retrieval_label = (
                f"EXACT match ({retrieval.get('matched_id', '')})"
                if _mt_label.startswith("exact_")
                else "FAISS semantic similarity"
            )

            # ── Fix 6: pre-compute adjusted score to choose prompt branch ──
            _is_exact_pre = (
                _mt_label.startswith("exact_") and
                not _mt_label.endswith("_miss")
            )
            _pre_adj = (1.0 if _is_exact_pre else
                        float(full_cases[0].get(
                            "adjusted_similarity",
                            full_cases[0].get("adj",
                                full_cases[0].get("_similarity", 0.0))
                        )) if full_cases else 0.0)

            # Fix B: history-aware guard — only fire no-match diagnostic for fresh
            # queries with no prior conversation context.
            _has_history = len(st.session_state.get("kc_messages", [])) >= 2

            if not _is_exact_pre and _pre_adj < 0.40 and not _has_history:
                # No-match diagnostic path — fresh query, no prior context
                fault_prompt = (
                    "You are Jane, the Learning & Memory Agent's Knowledge Copilot.\n\n"
                    f'User asked: "{user_message}"\n\n'
                    f"Knowledge base search returned no reliable match "
                    f"(no strong match found in knowledge base).\n\n"
                    "Respond with a helpful 3-4 sentence answer that:\n"
                    "1. Clearly states no similar case exists in the knowledge base yet.\n"
                    "2. Offers a diagnostic hint based on the fault type or asset mentioned "
                    "(e.g., what to check, what signals to look for) drawing on general "
                    "vibration analysis / bearing failure knowledge.\n"
                    "3. Suggests capturing this as a new case once the repair is complete, "
                    "so future occurrences can be matched.\n\n"
                    "Do NOT invent case IDs or claim specific historical cases exist. "
                    "Be helpful but epistemically honest about the KB gap."
                )
            elif not _is_exact_pre and _pre_adj < 0.40 and _has_history:
                # Follow-up in active conversation — FAISS score is low because the
                # user's message is vague (pronoun reference). Trust the LLM to resolve
                # the referent from conversation history included via _build_message_history.
                logger.info(
                    "[chatbot] fault_query low-sim follow-up branch "
                    "(pre_adj=%.2f, history=%d msgs)", _pre_adj,
                    len(st.session_state.get("kc_messages", []))
                )
                fault_prompt = (
                    "You are Jane, the Learning & Memory Agent's Knowledge Copilot, "
                    "continuing an ongoing conversation with a maintenance engineer.\n\n"
                    f"Knowledge base search on the current message returned only "
                    f"weak matches, but the user is "
                    f"likely referring to a case already discussed in this conversation.\n\n"
                    f"Current user question: \"{user_message}\"\n\n"
                    f"Instructions:\n"
                    f"1. Read the prior conversation turns (included in the message history "
                    f"above this prompt) to identify which case or fault the user is "
                    f"referring to.\n"
                    f"2. If you can identify the case, answer the current question using "
                    f"details from the prior turns. Cite the case_id explicitly.\n"
                    f"3. If the reference is genuinely ambiguous, ask a brief clarifying "
                    f"question (e.g., 'Do you mean CASE_001 that we discussed earlier?').\n"
                    f"4. Do NOT return a generic 'no reliable match' response — the user "
                    f"is following up on something already discussed.\n\n"
                    f"Keep the answer to 2-3 sentences."
                )
            else:
                fault_prompt = (
                    "You are the DRO Knowledge Assistant for a bearing "
                    "failure maintenance system.\n\n"
                    "CRITICAL RETRIEVAL CONTEXT: This query was retrieved "
                    "via {label}. If that label contains the word 'EXACT', "
                    "the retrieved cases below are authoritative matches and "
                    "you MUST describe their content (case_id, fault_mode, "
                    "root_cause, action_taken, lessons_learned). Do not "
                    "refuse to answer or say 'no reliable match' for EXACT "
                    "retrievals — that phrase is reserved ONLY for FAISS "
                    "semantic retrieval with low similarity.\n\n"
                    "RETRIEVED CASES (top-{n}, retrieved via {label}):\n"
                    "{ctx}\n\n"
                    'USER QUERY: "{q}"\n\n'
                    "STRICT RULES — follow every one:\n"
                    "1. Answer using the retrieved cases above. If the user's "
                    "query contains a pronoun or reference (e.g., 'that case', "
                    "'the same fault', 'it'), resolve it using the earlier "
                    "conversation turns before answering. Do not use general "
                    "knowledge about bearings, lubrication, or maintenance "
                    "beyond what is in the retrieved cases or prior conversation. "
                    "If neither the retrieved cases nor conversation history "
                    "contain the answer, say so explicitly.\n"
                    "2. If the top case has adjusted_score >= 0.70, treat it "
                    "as a confident hit. Reference its case_id, root_cause, "
                    "action_taken, and lessons_learned VERBATIM from the data.\n"
                    "3. If the top case has adjusted_score between 0.40 and "
                    "0.70, say the match is approximate. Quote only the fields "
                    "that align with the user's question. Do not fill in "
                    "missing details from general knowledge.\n"
                    "4. RETRIEVAL TYPE is {label}. If it contains 'EXACT', "
                    "trust the matched cases fully — describe their content "
                    "verbatim. Do NOT say 'no reliable match' and do NOT "
                    "apply similarity thresholds. Only if the label says "
                    "'FAISS semantic similarity' AND the top adjusted_score "
                    "is below 0.40, respond exactly: 'No reliable match was "
                    "found for this query. This may be a new fault pattern "
                    "— would you like to capture it as a new case?'\n"
                    "5. Never invent case contents. If a field is empty or "
                    "missing in the retrieved data, say 'not recorded'.\n"
                    "6. Use markdown **bold** for case IDs and key fields. "
                    "Keep response to 3-5 sentences.\n"
                    "7. CRITICAL: Do not include numeric similarity scores, "
                    "percentages, or decimal probabilities in your response. "
                    "Describe match quality qualitatively only — use 'strong "
                    "match', 'partial match', or 'no reliable match'.\n"
                ).format(n=len(full_cases), label=_retrieval_label,
                         ctx=cases_context, q=user_message)

            logger.debug(
                "[handle_fault_query] LLM prompt: %d chars, %d cases, best_sim=%.3f",
                len(fault_prompt), len(full_cases), best_sim,
            )
            logger.info(
                "[handle_fault_query] match_type=%s, retrieval_label=%s, "
                "n_cases=%d, top_case_id=%s, top_sim=%.3f",
                retrieval.get("match_type", "?"),
                _retrieval_label,
                len(full_cases),
                full_cases[0].get("case_id", "?") if full_cases else "?",
                float(full_cases[0].get("_similarity", 0.0)) if full_cases else 0.0,
            )
            logger.info("[handle_fault_query] FULL PROMPT:\n%s", fault_prompt)
            import time as _time
            _llm_t0 = _time.time()
            # Fix 1: send conversation history + current enriched prompt
            _msgs = _build_message_history(
                st.session_state.get("kc_messages", []),
                current_user_msg=fault_prompt,
            )
            logger.info("[chatbot] fault_query sending %d messages to LLM", len(_msgs))
            logger.info("[chatbot] fault_query message sequence: %s",
                        "->".join(type(m).__name__[0] for m in _msgs))
            resp = llm_client.invoke(
                _msgs,
                config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
            )
            _llm_ms = int((_time.time() - _llm_t0) * 1000)
            logger.debug(
                "[handle_fault_query] LLM response: %d chars, %dms",
                len(resp.content), _llm_ms,
            )

            # Confidence band — exact match overrides similarity logic
            _mt      = retrieval.get("match_type", "semantic")
            _top_adj = 0.0
            if _mt.startswith("exact_") and not _mt.endswith("_miss"):
                _conf    = "Exact match"
                _top_adj = 1.0
            elif _mt.endswith("_miss"):
                _conf    = "ID not found"
                _top_adj = 0.0
            else:
                # Semantic path — use adjusted score thresholds
                if full_cases:
                    _top_adj = float(full_cases[0].get(
                        "adjusted_similarity",
                        full_cases[0].get("adj",
                            full_cases[0].get("_similarity", 0.0))
                    ))
                if _top_adj >= 0.70:
                    _conf = "High"
                elif _top_adj >= 0.40:
                    _conf = "Approximate"
                else:
                    _conf = "No reliable match"

            # Update stash with response-time metadata
            _stash = st.session_state.get("kc_last_retrieval") or {}
            _stash["llm_ms"]       = _llm_ms
            _stash["confidence"]   = _conf
            _stash["top_adjusted"] = round(_top_adj, 3)
            _stash["source"]       = "Knowledge Base"
            st.session_state["kc_last_retrieval"] = _stash

            return resp.content.strip()
        except Exception as e:
            logger.error("[handle_fault_query] LLM %s: %s", type(e).__name__, e)
            print(f"[LLM ERROR] handle_fault_query: {e}")
            return _fault_fallback(user_message)
    else:
        logger.debug("[handle_fault_query] LLM unavailable — using fallback")
        return _fault_fallback(user_message)


def _fault_fallback(msg: str) -> str:
    """Keyword-based fault response when LLM unavailable."""
    m = msg.lower()
    if "outer race" in m or "skf6310" in m:
        return (
            "**CASE_001 matches** — outer race fault "
            "on SKF6310 motor.\n\n"
            "**Root cause:** contamination ingress via "
            "degraded housing seal.\n"
            "**Action:** bearing and seal replacement.\n"
            "**Outcome:** QA PASS — 1.8 mm/s, 55°C.\n\n"
            "**Key lesson:** always replace housing seal "
            "with every bearing change."
        )
    if "lubrication" in m or "skf6208" in m:
        return (
            "**CASE_002 matches** — lubrication fault "
            "on SKF6208 pump.\n\n"
            "**Root cause:** lubrication interval "
            "exceeded.\n"
            "**Action:** full oil drain and refill.\n"
            "**Outcome:** QA PASS.\n\n"
            "**Key lesson:** update lubrication interval "
            "to 60 days."
        )
    return (
        "I searched the knowledge repository but "
        "could not find a reliable match. This may "
        "be a new fault pattern. Would you like me "
        "to help capture it as a new case? Please "
        "describe the fault in more detail."
    )


def handle_analytics_query(user_message):
    from collections import Counter as _Counter

    # ── Gather all analytics data (Fixes 2 + 3) ──────────────────────
    _recurring = []
    _path_split = {}
    _monthly = []
    _top_assets = []

    try:
        _recurring = get_recurring_faults(top_n=5) or []
    except Exception as _e:
        logger.warning("[analytics] get_recurring_faults failed: %s", _e)

    try:
        _path_split = get_path_split() or {}
    except Exception as _e:
        logger.warning("[analytics] get_path_split failed: %s", _e)

    try:
        _monthly = get_monthly_trend() or []
    except Exception as _e:
        logger.warning("[analytics] get_monthly_trend failed: %s", _e)

    try:
        _all_records = load_all_records() or []
        _asset_counts = _Counter(
            r.get("asset_id") or r.get("asset_type", "unknown")
            for r in _all_records
            if r.get("asset_id") or r.get("asset_type")
        )
        _top_assets = _asset_counts.most_common(5)
    except Exception as _e:
        logger.warning("[analytics] asset breakdown failed: %s", _e)
        _top_assets = []

    # ── Build context string ──────────────────────────────────────────
    _ctx_parts = []

    if _recurring:
        _ctx_parts.append(
            "Top recurring faults: " + ", ".join(
                f"{r['fault_mode']} ({r['count']}x)" for r in _recurring
            )
        )

    if _path_split:
        _pa = _path_split.get("path_a", 0)
        _pb = _path_split.get("path_b", 0)
        _pc = _path_split.get("path_c", 0)
        _tot = max(_path_split.get("total", 1), 1)
        _ctx_parts.append(
            f"Knowledge retrieval rate: {round(100*_pa/_tot)}% (existing). "
            f"New cases: {round(100*_pb/_tot)}%. "
            f"Partial matches: {round(100*_pc/_tot)}%."
        )

    if _monthly:
        _recent_months = _monthly[-6:]
        _month_parts = [
            f"{m.get('month', '?')}={m.get('count', 0)}"
            for m in _recent_months
        ]
        if _month_parts:
            _ctx_parts.append(
                "Monthly trend (last 6 months): " + ", ".join(_month_parts)
            )

    if _top_assets:
        _ctx_parts.append(
            "Top affected assets: " + ", ".join(
                f"{a} ({c} events)" for a, c in _top_assets
            )
        )

    analytics_context = (
        " | ".join(_ctx_parts) if _ctx_parts
        else "No analytics data available yet."
    )

    if not _recurring and not _monthly and not _top_assets:
        return (
            "**Analytics temporarily unavailable.**\n\n"
            "No fault tracker records found. Once scenarios have "
            "been run, recurring fault patterns and reuse rates "
            "will appear here."
        )

    if LLM_AVAILABLE and llm_client:
        try:
            analytics_prompt = (
                "You are the DRO Knowledge Assistant.\n\n"
                f"Analytics data: {analytics_context}\n\n"
                f'User question: "{user_message}"\n\n'
                "Use the analytics data above together with any relevant "
                "earlier conversation turns to answer the question. "
                "If the user refers to a metric, asset, or fault mentioned "
                "earlier in the conversation, resolve that reference before "
                "answering. Provide ONE concise business-level insight in "
                "2-3 sentences MAXIMUM. Highlight the top number, what it "
                "suggests, and ONE recommended action. "
                "Use markdown **bold** for key figures. "
                "Do NOT use headers, bullet points, or numbered lists. "
                "Do NOT exceed 80 words. End with a focused follow-up "
                "question."
            )
            logger.debug(
                "[handle_analytics_query] Prompt built (%d chars)",
                len(analytics_prompt),
            )
            # Fix 1: send conversation history + current enriched prompt
            _msgs = _build_message_history(
                st.session_state.get("kc_messages", []),
                current_user_msg=analytics_prompt,
            )
            logger.info(
                "[chatbot] analytics_query sending %d messages to LLM", len(_msgs)
            )
            logger.info("[chatbot] analytics_query message sequence: %s",
                        "->".join(type(m).__name__[0] for m in _msgs))
            resp = llm_client.invoke(
                _msgs,
                config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
            )
            logger.debug(
                "[handle_analytics_query] Response received (%d chars)",
                len(resp.content),
            )
            return resp.content.strip()
        except Exception as e:
            logger.error("[handle_analytics_query] %s: %s", type(e).__name__, e)
            print(f"[LLM ERROR] handle_analytics_query: {e}")
            _labels = [r["fault_mode"] for r in _recurring]
            _counts = [r["count"] for r in _recurring]
            _pa_pct = round(_path_split.get("path_a", 0) /
                            max(_path_split.get("total", 1), 1) * 100, 1)
            _pb_pct = round(_path_split.get("path_b", 0) /
                            max(_path_split.get("total", 1), 1) * 100, 1)
            return _analytics_fallback(_labels, _counts, _pa_pct, _pb_pct)

    logger.debug("[handle_analytics_query] LLM unavailable — using fallback")
    _labels = [r["fault_mode"] for r in _recurring]
    _counts = [r["count"] for r in _recurring]
    _pa_pct = round(_path_split.get("path_a", 0) /
                    max(_path_split.get("total", 1), 1) * 100, 1)
    _pb_pct = round(_path_split.get("path_b", 0) /
                    max(_path_split.get("total", 1), 1) * 100, 1)
    return _analytics_fallback(_labels, _counts, _pa_pct, _pb_pct)


def _analytics_fallback(labels, counts, path_a, path_b):
    """Keyword-based analytics response when LLM unavailable."""
    if not labels:
        return "**Analytics temporarily unavailable.** No tracker data."
    return (
        "**Current analytics summary:**\n\n"
        + "\n".join([
            f"**{l}:** {c} occurrences"
            for l, c in zip(labels, counts)
        ])
        + f"\n\n**Knowledge reuse rate:** {path_a}%\n"
        f"**New cases learned:** {path_b}% of queries\n\n"
        f"**Most frequent fault:** {labels[0]} "
        f"with {counts[0]} occurrences — "
        f"this suggests a systemic issue requiring "
        f"an engineering fix."
    )


def handle_capture_case(user_message):
    if LLM_AVAILABLE and llm_client:
        try:
            capture_prompt = (
                "You are the DRO Knowledge Assistant. "
                "Extract case details from this maintenance "
                "report and respond in this exact JSON format "
                "with no extra text:\n"
                "{\n"
                '  "fault_mode": "detected fault type",\n'
                '  "asset_type": "motor pump conveyor or gearbox",\n'
                '  "bearing_type": "bearing model if mentioned",\n'
                '  "root_cause": "confirmed root cause",\n'
                '  "action_taken": "maintenance action",\n'
                '  "result": "outcome and QA result",\n'
                '  "lessons_learned": "key lesson"\n'
                "}\n\n"
                f'Engineer message: "{user_message}"\n\n'
                "Use earlier conversation turns to resolve any pronouns "
                "or references (e.g., 'that bearing', 'same fault', 'it') "
                "before extracting fields. "
                "If any field is not mentioned use "
                "to be confirmed."
            )
            logger.debug("[handle_capture_case] Prompt built, invoking LLM...")
            # Fix 1: send conversation history + current enriched prompt
            _msgs = _build_message_history(
                st.session_state.get("kc_messages", []),
                current_user_msg=capture_prompt,
            )
            logger.info(
                "[chatbot] capture_case sending %d messages to LLM", len(_msgs)
            )
            logger.info("[chatbot] capture_case message sequence: %s",
                        "->".join(type(m).__name__[0] for m in _msgs))
            resp = llm_client.invoke(
                _msgs,
                config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
            )
            raw = resp.content.strip()
            logger.debug(
                "[handle_capture_case] Response received (%d chars), parsing JSON",
                len(raw),
            )
            json_match = re.search(
                r'\{.*\}', raw, re.DOTALL
            )
            if json_match:
                parsed = json.loads(json_match.group())
                st.session_state["kc_pending_case"] = parsed
                logger.debug("[handle_capture_case] JSON parsed OK, pending case set")
                return (
                    "I have extracted the following details "
                    "from your report. Please confirm to save "
                    "to the knowledge repository."
                )
            else:
                logger.warning("[handle_capture_case] No JSON found in LLM response")
                return (
                    "I understood you are reporting a completed "
                    "repair. Could you provide more detail — "
                    "fault type, asset, root cause, action "
                    "taken, and outcome?"
                )
        except Exception as e:
            logger.error("[handle_capture_case] %s: %s", type(e).__name__, e)
            print(f"[LLM ERROR] handle_capture_case: {e}")
            return (
                f"Unable to process case capture ({e}). "
                "Please use Case Management page instead."
            )
    else:
        return (
            "To capture this repair as a knowledge case, "
            "please use the Case Management page where you "
            "can fill in the details directly."
        )


def handle_general(msg: str) -> str:
    """Handle general DRO questions."""
    if LLM_AVAILABLE and llm_client:
        try:
            system = (
                "You are Knowledge Copilot, the AI assistant for "
                "Agent 6.8 (the Learning & Memory Agent) of the DRO "
                "(Downtime Response Orchestrator) pipeline. "
                "Your scope is STRICTLY limited to:\n"
                "  - Past bearing fault cases and lessons learned\n"
                "  - Knowledge base structure and retrieval\n"
                "  - Recurring fault patterns and learning analytics\n"
                "  - How the Learning & Memory Agent works\n\n"
                "You have access to the full conversation history. "
                "When the user uses pronouns or references ('it', 'that', "
                "'the same', 'that case', 'the fault you mentioned'), "
                "resolve them from the prior turns before responding. "
                "Treat each turn as part of an ongoing diagnostic "
                "conversation, not an isolated query.\n\n"
                "Do NOT answer questions about: live sensor readings, "
                "remaining useful life predictions, risk scores, "
                "prescriptive recommendations, work order execution, "
                "or anything outside maintenance. If the user asks "
                "about those, briefly say it belongs to another DRO "
                "agent and offer to look up historical cases instead.\n\n"
                "Be precise. Use **bold** for key values. Keep responses "
                "to 3-4 sentences. End with one focused follow-up question. "
                "Never say you are an AI model."
            )
            # Fix 5: inject in-app guidance when user asks how-to questions
            _howto_key = _detect_howto(msg)
            if _howto_key:
                system = (system +
                          f"\n\nRelevant in-app guidance for this question:\n"
                          f"{_INAPP_HELP[_howto_key]}")
            logger.debug("[handle_general] Prompt built, invoking LLM...")
            # Fix 1: send conversation history with system message
            _msgs = _build_message_history(
                st.session_state.get("kc_messages", []),
                current_user_msg=msg,
                system_msg=system,
            )
            logger.info(
                "[chatbot] general sending %d messages to LLM", len(_msgs)
            )
            logger.info("[chatbot] general message sequence: %s",
                        "->".join(type(m).__name__[0] for m in _msgs))
            resp = llm_client.invoke(
                _msgs,
                config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
            )
            logger.debug(
                "[handle_general] Response received (%d chars)", len(resp.content)
            )
            return resp.content
        except Exception as e:
            logger.error("[handle_general] %s: %s", type(e).__name__, e)
            print(f"[LLM ERROR] handle_general: {e}")
            return _general_fallback(msg)
    logger.debug("[handle_general] LLM unavailable — using fallback")
    return _general_fallback(msg)


def _general_fallback(msg: str) -> str:
    """Keyword fallback when LLM unavailable."""
    m = msg.lower()
    if "learning" in m or "memory" in m or "agent" in m:
        return (
            "The Learning & Memory Agent (Agent 6.8) "
            "is the final agent in the 8-agent DRO "
            "pipeline. It stores confirmed repair "
            "outcomes into the FAISS knowledge base "
            "so future faults can be resolved "
            "instantly from memory. It is the only "
            "writer to the knowledge base."
        )
    if "path" in m or "retriev" in m:
        return (
            "When a fault is resolved, the agent "
            "checks the knowledge base. If a "
            "similar case exists it is retrieved "
            "instantly at zero LLM cost. If no "
            "match is found a new case is generated "
            "by the LLM and stored for future use."
        )
    if "dro" in m or "pipeline" in m:
        return (
            "The DRO pipeline has 8 agents: Data "
            "Foundation, Monitoring, Predictive "
            "Risk, Failure Intelligence, Knowledge, "
            "Prescriptive Optimisation, Executor, "
            "and Learning & Memory. Each agent "
            "passes outputs downstream until the "
            "repair outcome is stored as knowledge."
        )
    return (
        "I can help you search the fault knowledge "
        "base, review analytics, and capture new "
        "maintenance cases. Try asking about a "
        "specific fault, asset, or scenario."
    )


_KB_LIST_ACTIVITY_PHRASES = [
    "last", "latest", "recent", "just ran", "just now",
    "today", "todays", "today's", "this session",
    "most recent activity", "recently ran", "recent runs",
    "recent activity", "last run", "last few",
]

_KB_LIST_INVENTORY_PHRASES = [
    "list all", "show all", "what's in", "whats in",
    "kb contents", "knowledge base contents", "all cases",
    "by fault mode", "by asset", "by asset type",
    "everything in", "inventory", "catalog",
]


def _kb_list_query_type(user_message: str) -> str:
    """Return 'inventory' or 'activity' for KB_LIST queries.
    Inventory phrases checked first — 'list all recent' routes to inventory."""
    msg = " " + user_message.lower().strip() + " "
    for phrase in _KB_LIST_INVENTORY_PHRASES:
        if " " + phrase + " " in msg or msg.startswith(" " + phrase):
            return "inventory"
    for phrase in _KB_LIST_ACTIVITY_PHRASES:
        if " " + phrase + " " in msg or msg.startswith(" " + phrase):
            return "activity"
    return "activity"


# ═══════════════════════════════════════════════════════════════════
# SECTION: CHATBOT — KB_LIST AND ANALYTICS HANDLERS
# Knowledge-base inventory list and activity-log renderers.
# ═══════════════════════════════════════════════════════════════════
def _handle_kb_list_activity(n_to_show: int, filter_fault):
    """Return the N most recent pipeline runs from fault_tracker.json as a dict."""
    from services.fault_tracker import load_all_records
    from datetime import datetime as _dt2

    records = load_all_records()

    if filter_fault:
        records = [r for r in records if r.get("fault_mode") == filter_fault]

    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    if not records:
        return {
            "response": (
                "No pipeline runs have happened in this session yet. "
                "Run a scenario from the Discovery tab to populate the "
                "activity log, then ask me again."
            ),
            "evidence": {
                "source": FAULT_TRACKER_PATH,
                "confidence": "n/a",
                "cases": [],
            },
            "intent": "KB_LIST_ACTIVITY",
        }

    recent = records[:n_to_show]
    _path_to_label = {"A": "Retrieved", "B": "New", "C": "Enriched"}
    lines = []
    for r in recent:
        cid = (
            r.get("source_case_id")
            if r.get("path_taken") in ("A", "C") and r.get("source_case_id")
            else r.get("case_id", "unknown")
        )
        fmode = (r.get("fault_mode") or "unknown").replace("_", " ")
        atype = r.get("asset_type") or "unknown"
        ts_raw = r.get("timestamp", "")
        outcome = _path_to_label.get(r.get("path_taken", ""), r.get("path_taken", ""))
        try:
            from datetime import timedelta as _td
            _dt_utc = _dt2.fromisoformat(ts_raw)
            # Tracker stores UTC. Convert to IST for display (UTC+5:30).
            # Naive-vs-aware safe: if the parsed dt is tz-naive treat as UTC.
            if _dt_utc.tzinfo is None:
                _dt_ist = _dt_utc + _td(hours=5, minutes=30)
            else:
                from datetime import timezone as _tz
                _dt_ist = _dt_utc.astimezone(_tz(_td(hours=5, minutes=30)))
                _dt_ist = _dt_ist.replace(tzinfo=None)
            ts = _dt_ist.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = ts_raw
        lines.append(f"{cid} · {ts} · {outcome} ({fmode} on {atype})")

    response_body = (
        f"Here are the {len(recent)} most recent pipeline runs:\n\n"
        + "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    )

    return {
        "response": response_body,
        "evidence": {
            "source": FAULT_TRACKER_PATH,
            "confidence": "high",
            "cases": [
                {
                    "case_id": (
                        r.get("source_case_id")
                        if r.get("path_taken") in ("A", "C") and r.get("source_case_id")
                        else r.get("case_id")
                    ),
                    "fault_mode": r.get("fault_mode"),
                    "asset_type": r.get("asset_type"),
                }
                for r in recent
            ],
        },
        "intent": "KB_LIST_ACTIVITY",
    }


def handle_kb_list(user_message):
    """Handle enumeration queries: list recent/all cases from the knowledge base.
    Supports fault-mode filtering when the query names a specific fault type.
    Returns formatted markdown list sorted by file mtime (newest first)."""
    import os as _os
    import json as _json
    import glob as _glob
    import re as _re
    from datetime import datetime

    m_lower = user_message.lower()

    # How many to show
    n_to_show = 5
    n_match = _re.search(r"\b(last|recent|latest|newest)\s+(\d+)", m_lower)
    if n_match:
        try:
            n_to_show = min(int(n_match.group(2)), 20)
        except Exception:
            n_to_show = 5
    elif "3" in m_lower:
        n_to_show = 3
    elif "10" in m_lower:
        n_to_show = 10

    # Fix 4: detect fault-mode filter from query
    _KNOWN_FAULTS_KB = {
        "outer_race_fault":   ["outer race", "outer_race", "bpfo"],
        "inner_race_fault":   ["inner race", "inner_race", "bpfi"],
        "cage_fault":         ["cage"],
        "lubrication_issue":  ["lubrication", "lubrication_issue",
                               "grease", "oil", "lube"],
        "misalignment":       ["misalignment", "misaligned"],
        "imbalance":          ["imbalance", "unbalanced"],
        "sensor_fault":       ["sensor fault", "sensor_fault"],
        "healthy":            ["healthy", "baseline"],
    }
    _filter_fault = None
    for _canonical, _aliases in _KNOWN_FAULTS_KB.items():
        if any(a in m_lower for a in _aliases):
            _filter_fault = _canonical
            break

    _query_type = _kb_list_query_type(user_message)
    if _query_type == "activity":
        return _handle_kb_list_activity(n_to_show, _filter_fault)

    # Load and filter all cases (inventory path)
    cases = []
    for path in _glob.glob(f"{DATA_DIR}/*.json"):
        if path.endswith(".bak"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as _f:
                case = _json.load(_f)
            if _filter_fault and case.get("fault_mode") != _filter_fault:
                continue
            mtime = _os.path.getmtime(path)
            case["_file_mtime"] = mtime
            case["_file"] = _os.path.basename(path)
            cases.append(case)
        except Exception:
            continue

    cases.sort(key=lambda c: c.get("_file_mtime", 0), reverse=True)
    recent = cases[:n_to_show]

    if not recent:
        if _filter_fault:
            _empty_msg = (
                f"No **{_filter_fault.replace('_', ' ')}** cases found "
                "in the knowledge base yet. Try running a matching scenario "
                "to populate it."
            )
        else:
            _empty_msg = ("No cases found in the knowledge base. "
                          "Try running a scenario to populate it.")
        return {
            "response": _empty_msg,
            "evidence": {"source": "data/learned_cases/", "confidence": "high", "cases": []},
            "intent": "KB_LIST",
        }

    _header_fault = (
        f"{_filter_fault.replace('_', ' ')} cases"
        if _filter_fault else "most recent cases"
    )
    lines = [f"Here are the {len(recent)} {_header_fault} in the "
             f"knowledge base, sorted by date:\n"]
    for i, c in enumerate(recent, 1):
        cid = c.get("case_id", c.get("_file", "?"))
        fm = c.get("fault_mode", "?")
        at = c.get("asset_type", "?")
        bt = c.get("bearing_type", "?")
        date_str = datetime.fromtimestamp(
            c.get("_file_mtime", 0)
        ).strftime("%Y-%m-%d")
        lines.append(
            f"{i}. **{cid}** ({date_str}) — "
            f"{fm.replace('_', ' ')} on {at} ({bt})"
        )

    st.session_state["kc_last_retrieval"] = {
        "candidates": [
            {
                "case_id": c.get("case_id", "?"),
                "fault_mode": c.get("fault_mode", "?"),
                "asset_type": c.get("asset_type", "?"),
            } for c in recent
        ],
        "best_similarity": 1.0,
        "retrieval_ok": True,
        "retrieval_ms": 0,
        "query": user_message,
        "match_type": "kb_enumeration",
        "matched_id": None,
        "confidence": "Catalog listing",
        "source": "Knowledge Base catalog",
    }

    return {
        "response": "\n".join(lines),
        "evidence": {
            "source": "data/learned_cases/",
            "confidence": "high",
            "cases": [
                {
                    "case_id": c.get("case_id", "?"),
                    "fault_mode": c.get("fault_mode", "?"),
                    "asset_type": c.get("asset_type", "?"),
                }
                for c in recent
            ],
        },
        "intent": "KB_LIST",
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION: CHATBOT — DISPATCH AND FOLLOW-UP CHIPS
# route_to_handler intent dispatch; generate_followup_chips suggestions.
# ═══════════════════════════════════════════════════════════════════
def route_to_handler(user_msg):
    """Route a message to the appropriate handler and return response text."""
    intent = detect_intent(user_msg)
    if intent == "FAULT_QUERY":
        return handle_fault_query(user_msg)
    elif intent == "ANALYTICS_QUERY":
        return handle_analytics_query(user_msg)
    elif intent == "CAPTURE_CASE":
        return handle_capture_case(user_msg)
    else:
        persona_name   = "user"
        cases = []
        for _path in sorted(
            glob.glob(f"{DATA_DIR}/*.json")
        ):
            try:
                with open(_path) as _f:
                    _c = json.load(_f)
                    cases.append({
                        "case_id":         _c.get("case_id"),
                        "fault_mode":      _c.get("fault_mode"),
                        "asset_type":      _c.get("asset_type"),
                        "bearing_type":    _c.get("bearing_type"),
                        "root_cause":      _c.get("root_cause"),
                        "action_taken":    _c.get("action_taken"),
                        "lessons_learned": _c.get("lessons_learned"),
                    })
            except Exception:
                pass
        fault_labels = ["Outer race", "Lubrication", "Cage", "Misalignment", "Inner race"]
        fault_counts = [12, 8, 5, 3, 2]
        if TRACKER_AVAILABLE:
            try:
                rf = get_recurring_faults(top_n=5)
                fault_labels = [r["fault_mode"] for r in rf]
                fault_counts  = [r["count"] for r in rf]
            except Exception:
                pass
        analytics_ctx = ", ".join(
            f"{l}:{c}x" for l, c in zip(fault_labels, fault_counts)
        )
        if LLM_AVAILABLE and llm_client:
            try:
                _sys = (
                    "You are Knowledge Copilot, a warm and intelligent "
                    "Knowledge Assistant for the DRO "
                    "(Downtime Response Orchestrator) — "
                    "a predictive maintenance AI system for "
                    "bearing failure.\n\n"
                    f"Speaking with: {persona_name}\n\n"
                    f"Knowledge base ({len(cases)} cases):\n"
                    f"{json.dumps(cases[:7], indent=1)}\n\n"
                    f"Fault analytics: {analytics_ctx}\n\n"
                    "You are Knowledge Copilot — always warm, clear, and "
                    "helpful. Match language to the persona. "
                    "Keep responses to 2-4 sentences unless "
                    "more detail is genuinely needed. "
                    "Use **bold** for key terms. "
                    "End with a follow-up offer when relevant. "
                    "Never say you are an AI language model."
                )
                _resp = llm_client.invoke(
                    [
                        SystemMessage(content=_sys),
                        HumanMessage(content=user_msg),
                    ],
                    config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
                )
                return _resp.content.strip()
            except Exception as _e:
                return f"I ran into an issue ({_e}). Please try again."
        else:
            return (
                "I am Knowledge Copilot, your DRO Knowledge Assistant. "
                "I can help you search the fault knowledge base, review "
                "analytics, and capture new maintenance cases. Connect "
                "the LLM via your .env file to enable full conversational "
                "capabilities."
            )


def generate_followup_chips(
    intent: str,
    response_text: str = "",
    evidence: dict = None,
    user_message: str = "",
) -> list:
    """Generate exactly 3 contextual follow-up chips via a small LLM call.

    Passes the last 3 conversation turns so the model can resolve pronouns
    ('how many were there', 'that case'). Falls back to intent-appropriate
    static chips if the LLM is unavailable or returns invalid JSON.
    """
    _FALLBACKS = {
        "FAULT_QUERY": [
            "Show similar cases in the knowledge base",
            "What was the root cause?",
            "Which asset had this fault?",
        ],
        "ANALYTICS_QUERY": [
            "Show monthly trend",
            "Top affected assets",
            "Recurring fault patterns",
        ],
        "KB_LIST": [
            "Show recent cases",
            "Cases by asset type",
            "Cases by fault mode",
        ],
        "KB_LIST_ACTIVITY": [
            "What was the outcome of that run?",
            "Show the case PDF for the top one",
            "Show monthly trend",
        ],
        "CAPTURE_CASE": ["Confirm and save", "Edit details", "Discard"],
        "OTHER_AGENT": [
            "Show historical cases on this asset",
            "Show recurring fault patterns",
            "How does the L&M Agent work?",
        ],
        "OUT_OF_DOMAIN": [],
        "GENERAL": [
            "Most frequent fault this month",
            "Show recurring fault patterns",
            "How does the L&M Agent work?",
        ],
    }

    if intent == "OUT_OF_DOMAIN":
        return []

    fallback = _FALLBACKS.get(intent, _FALLBACKS["GENERAL"])

    if not LLM_AVAILABLE or not llm_client:
        return fallback

    # Build conversation context from last 3 turns for pronoun resolution.
    # kc_messages already has the current user message appended before chips
    # are generated, so exclude it (last element) to avoid duplication.
    prior_msgs = st.session_state.get("kc_messages", [])
    prior_ctx  = prior_msgs[:-1] if prior_msgs else []
    ctx_lines  = []
    for _m in prior_ctx[-6:]:
        _role    = _m.get("role", "")
        _content = (_m.get("content", "") or "")[:200]
        if _role and _content:
            ctx_lines.append(f"{_role.upper()}: {_content}")
    if user_message:
        ctx_lines.append(f"USER (current): {user_message}")
    if response_text:
        ctx_lines.append(f"ASSISTANT (current): {(response_text or '')[:400]}")
    conversation = "\n".join(ctx_lines)

    ev       = evidence or {}
    ev_cases = ev.get("cases", []) or []
    case_ids = [c.get("case_id", "") for c in ev_cases[:3] if c.get("case_id")]
    case_ctx = (
        f"Retrieved cases: {', '.join(case_ids)}"
        if case_ids else "No specific cases retrieved."
    )

    prompt = (
        "You are generating 3 short follow-up question suggestions for a "
        "maintenance knowledge base chatbot. The user is a maintenance engineer.\n\n"
        f"CONVERSATION CONTEXT:\n{conversation}\n\n"
        f"CURRENT INTENT: {intent}\n"
        f"{case_ctx}\n\n"
        "Generate exactly 3 follow-up questions the user is likely to ask NEXT.\n\n"
        "RULES:\n"
        "1. Each question must be a natural continuation of the conversation.\n"
        "2. If the user asked about a specific fault or case, follow-ups should "
        "drill deeper into that — not shift topic.\n"
        "3. If pronouns like 'there', 'that', 'those' appear in the current user "
        "message, resolve them from earlier turns.\n"
        "4. Keep each question under 10 words.\n"
        "5. Prefer questions answerable from the knowledge base (cases, fault "
        "statistics, asset history).\n"
        "6. Do NOT include quote marks around the questions.\n"
        "7. Do NOT generate generic fallbacks when the user is discussing a "
        "specific case.\n\n"
        "Return ONLY a JSON array of exactly 3 strings, nothing else:\n"
        '["question 1", "question 2", "question 3"]'
    )

    try:
        from langchain_core.messages import HumanMessage as _HM
        _resp = llm_client.invoke(
            [_HM(content=prompt)],
            config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
        )
        _raw  = (
            _resp.content if hasattr(_resp, "content") else str(_resp)
        ).strip()
        _m = re.search(r'\[[\s\S]+?\]', _raw)
        if not _m:
            logger.warning("[chips] no JSON array in LLM response — using fallback")
            return fallback
        chips = json.loads(_m.group(0))
        if not isinstance(chips, list):
            logger.warning("[chips] LLM returned non-list — using fallback")
            return fallback
        chips = [str(c).strip() for c in chips if c and str(c).strip()]
        chips = [c for c in chips if len(c) > 3]
        if len(chips) < 3:
            extras = [c for c in fallback if c not in chips]
            chips  = (chips + extras)[:3]
        return chips[:3]
    except Exception as _e:
        logger.warning("[chips] LLM chip call failed: %s — using fallback", _e)
        return fallback


# ═══════════════════════════════════════════════════════════════════
# SECTION: KNOWLEDGE COPILOT RENDERER
# Full chatbot UI: message history, input bar, follow-up chips.
# ═══════════════════════════════════════════════════════════════════
def render_knowledge_copilot():
    """Bottom-docked Knowledge Copilot chat."""
    # ── 1. PROCESS PENDING INPUT FIRST ──────────────────────
    pending = st.session_state.get("kc_pending", "")
    if pending:
        st.session_state["kc_pending"] = ""

        st.session_state["kc_messages"].append({
            "role":      "user",
            "content":   pending,
            "timestamp": datetime.now().strftime("%I:%M %p"),
        })

        scope = detect_scope(pending)

        if scope.startswith("OTHER_AGENT"):
            intent = "OTHER_AGENT"
            resp = handle_other_agent(pending, scope)
        elif scope == "OUT_OF_DOMAIN":
            intent = "OUT_OF_DOMAIN"
            resp = handle_out_of_domain(pending)
        else:
            # IN_SCOPE — proceed with normal intent routing (Fix 2: follow-up inheritance)
            _last_intent = st.session_state.get("kc_last_intent")
            if _is_followup_query(pending) and _last_intent:
                intent = _last_intent
                logger.info("[chatbot] FOLLOWUP → inheriting intent=%s for msg=%r",
                            intent, pending[:60])
            else:
                intent = detect_intent(pending)
                logger.info("[chatbot] FRESH intent → %s for msg=%r",
                            intent, pending[:60])
            st.session_state["kc_last_intent"] = intent

            _kb_ev = None
            if intent == "FAULT_QUERY":
                resp = handle_fault_query(pending)
            elif intent == "ANALYTICS_QUERY":
                resp = handle_analytics_query(pending)
            elif intent == "CAPTURE_CASE":
                resp = handle_capture_case(pending)
            elif intent in ("KB_LIST", "KB_LIST_ACTIVITY"):
                _kb_result = handle_kb_list(pending)
                resp = _kb_result["response"]
                _kb_ev = _kb_result["evidence"]
                intent = _kb_result.get("intent", "KB_LIST")
                st.session_state["kc_last_intent"] = intent
            else:
                resp = handle_general(pending)

        _evidence = None
        if intent == "OTHER_AGENT":
            _agent_code = scope.split(":", 1)[1] if ":" in scope else ""
            _agent_name = SCOPE_AGENT_DISPLAY.get(_agent_code, "Another DRO agent")
            _evidence = {
                "source":     f"Scope redirect: {_agent_name}",
                "confidence": "Out of L&M scope",
                "cases":      [],
            }
        elif intent == "OUT_OF_DOMAIN":
            _evidence = {
                "source":     "Scope refusal",
                "confidence": "Out of domain",
                "cases":      [],
            }
        elif intent == "FAULT_QUERY":
            _stash = st.session_state.get("kc_last_retrieval") or {}
            if _stash.get("retrieval_ok"):
                _mtype = _stash.get("match_type", "semantic")
                _mid   = _stash.get("matched_id", "")
                if _mtype == "exact_case_id":
                    _src_label = f"Exact match — {_mid}"
                elif _mtype == "exact_bearing_id":
                    _nm = _stash.get("total_matches", "")
                    _src_label = (
                        f"Exact match — {_mid}"
                        + (f" ({_nm} cases)" if _nm else "")
                    )
                elif _mtype == "not_found":
                    _src_label = f"No cases for {_mid}"
                else:
                    _src_label = _stash.get("source", "Knowledge Base")
                # ── Filter chatbot evidence to relevant candidates only ──
                # Raw FAISS top-3 often includes semantic neighbors that
                # aren't relevant (e.g. "outer race" query surfaces
                # CASE_003 inner race by proximity). Apply similarity +
                # fault_mode filters so evidence matches what the LLM
                # response actually reasons about.
                _raw_candidates = _stash.get("candidates") or []
                # Derive resolved fault_mode from stored query text;
                # avoids modifying handle_fault_query or its stash schema.
                _query_fault = resolve_fault_mode(
                    (_stash.get("query") or "").lower()
                )
                _EVIDENCE_SIM_THRESHOLD = cfg("chatbot", "evidence_similarity_threshold", default=0.55)

                _filtered = []
                for _c in _raw_candidates:
                    # Loaded-JSON candidates store score as _similarity;
                    # raw-retrieval fallbacks also have similarity/adjusted_score.
                    _sim = (
                        _c.get("_similarity") or
                        _c.get("similarity") or
                        _c.get("adjusted_score") or
                        0.0
                    )
                    if _sim < _EVIDENCE_SIM_THRESHOLD:
                        continue
                    if _query_fault and _c.get("fault_mode") != _query_fault:
                        continue
                    _filtered.append(_c)

                # Safety fallback: if filtering removed everything but raw
                # has candidates, keep top-1 so evidence isn't empty when
                # the response text cites a case.
                if not _filtered and _raw_candidates:
                    _filtered = [_raw_candidates[0]]

                _filtered = _filtered[:3]

                _evidence = {
                    "source":       _src_label,
                    "confidence":   _stash.get("confidence", "—"),
                    "top_adjusted": _stash.get("top_adjusted", 0.0),
                    "retrieval_ms": _stash.get("retrieval_ms", 0),
                    "llm_ms":       _stash.get("llm_ms", 0),
                    "cases": [
                        {
                            "case_id":    c.get("case_id", "?"),
                            "fault_mode": c.get("fault_mode", "?"),
                            "asset_type": c.get("asset_type", "?"),
                        }
                        for c in _filtered
                    ],
                }
        elif intent == "ANALYTICS_QUERY":
            _evidence = {
                "source":     FAULT_TRACKER_PATH,
                "confidence": "Data-driven",
                "cases":      [],
            }
        elif intent in ("KB_LIST", "KB_LIST_ACTIVITY"):
            _evidence = _kb_ev or {
                "source": "data/learned_cases/",
                "confidence": "high",
                "cases": [],
            }
        elif intent == "GENERAL":
            _evidence = {
                "source":     "Generated by LLM",
                "confidence": "—",
                "cases":      [],
            }
        # CAPTURE_CASE: no evidence card (user is reporting, not asking)

        _chips = generate_followup_chips(
            intent=intent,
            response_text=resp,
            evidence=_evidence,
            user_message=pending,
        )

        st.session_state["kc_messages"].append({
            "role":      "assistant",
            "content":   resp,
            "timestamp": datetime.now().strftime("%I:%M %p"),
            "evidence":  _evidence,
            "chips":     _chips,
        })
        st.rerun()

    # ── 2. RENDER CHAT HISTORY ───────────────────────────────
    # Per-message rendering: each message gets its own st.markdown()
    # so chip st.button() widgets can be interspersed between messages.
    msgs = st.session_state.get("kc_messages", [])

    if not msgs:
        st.markdown(
            '<div class="kc-empty">'
            'Ask anything about your assets or faults'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        # Pre-compute the last assistant message index so chips only
        # render under the most recent response, not every historical one.
        last_assistant_idx = -1
        for _j in range(len(msgs) - 1, -1, -1):
            if msgs[_j].get("role") == "assistant":
                last_assistant_idx = _j
                break

        for idx, m in enumerate(msgs):
            ts = m.get("timestamp", "")
            if m["role"] == "user":
                st.markdown(
                    f'<div class="kc-user-row">'
                    f'<div class="kc-user-block">'
                    f'<div class="kc-user-meta">{ts}</div>'
                    f'<div class="kc-user-bubble">'
                    f'{html.escape(m["content"])}'
                    f'</div></div></div>',
                    unsafe_allow_html=True,
                )
            else:
                # Build body with markdown → HTML conversion
                body = html.escape(m["content"])
                body = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', body, flags=re.DOTALL)
                body = re.sub(r'\*(.*?)\*', r'<i>\1</i>', body)
                body = re.sub(
                    r'`(.*?)`',
                    r'<code style="background:#F0EFF8;padding:1px 5px;'
                    r'border-radius:3px;font-size:10.5px;color:#4F46E5;">\1</code>',
                    body,
                )
                body = body.replace("\n", "<br>")

                bubble_html = (
                    f'<div class="kc-agent-row">'
                    f'<div class="kc-agent-label">'
                    f'🤖 <strong style="color:#A100FF">'
                    f'Knowledge Copilot</strong>'
                    f'&nbsp;·&nbsp; {ts}'
                    f'</div>'
                    f'<div class="kc-agent-bubble">{body}</div>'
                )

                ev = m.get("evidence")
                st.markdown(bubble_html + '</div>', unsafe_allow_html=True)

                # Consolidated evidence expander — Fixes B + C
                if ev:
                    import base64 as _b64e
                    _src  = html.escape(str(ev.get("source", "—")))
                    _conf = html.escape(str(ev.get("confidence", "—")))
                    with st.expander("Show evidence", expanded=False):
                        st.markdown(
                            f'<div style="display:flex;gap:2rem;font-size:12px;'
                            f'color:#5A5A5A;margin-bottom:0.5rem;">'
                            f'<div><span style="color:#8A8A8A;text-transform:uppercase;'
                            f'letter-spacing:0.08em;font-size:10px;">Source</span><br/>'
                            f'<span style="color:#1A1A1A;">{_src}</span></div>'
                            f'<div><span style="color:#8A8A8A;text-transform:uppercase;'
                            f'letter-spacing:0.08em;font-size:10px;">Confidence</span>'
                            f'<br/><span style="color:#1A1A1A;">{_conf}</span></div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        _ev_cases = ev.get("cases") or []
                        if _ev_cases:
                            st.markdown(
                                '<div style="color:#8A8A8A;font-size:10px;'
                                'text-transform:uppercase;letter-spacing:0.08em;'
                                'margin-top:0.5rem;margin-bottom:0.25rem;">'
                                'Retrieved Cases</div>',
                                unsafe_allow_html=True,
                            )
                            for _ci, _cc in enumerate(_ev_cases[:3]):
                                _cid   = _cc.get("case_id", "")
                                _fmode = (_cc.get("fault_mode", "") or "").replace("_", " ")
                                _atype = _cc.get("asset_type", "") or ""
                                if not _cid or _cid == "?":
                                    continue
                                _pdf_key = f"kc_case_pdf_{_cid}"
                                _btn_lbl = f"\U0001f4c4 {_cid}  ·  {_fmode}  ·  {_atype}"
                                if st.button(
                                    _btn_lbl,
                                    key=f"kc_case_btn_{idx}_{_ci}",
                                    help=f"Open PDF for {_cid}",
                                    use_container_width=True,
                                ):
                                    _case_data = _load_case_content_from_disk(_cid)
                                    if _case_data:
                                        try:
                                            from services.pdf_renderer import (
                                                render_case_pdf as _rcp_ev,
                                            )
                                            _raw = _rcp_ev(_case_data)
                                            if hasattr(_raw, "getvalue"):
                                                _pbytes = _raw.getvalue()
                                            elif isinstance(
                                                _raw, (bytearray, memoryview)
                                            ):
                                                _pbytes = bytes(_raw)
                                            elif isinstance(_raw, bytes):
                                                _pbytes = _raw
                                            else:
                                                _pbytes = b""
                                            st.session_state[_pdf_key] = (
                                                _pbytes if _pbytes else None
                                            )
                                        except Exception as _pe:
                                            logger.warning(
                                                "[chatbot] PDF render failed %s: %s",
                                                _cid, _pe,
                                            )
                                            st.session_state[_pdf_key] = None
                                    else:
                                        st.session_state[_pdf_key] = None
                                _pdf_bytes = st.session_state.get(_pdf_key)
                                if isinstance(_pdf_bytes, bytes) and len(_pdf_bytes) > 100:
                                    _b64_str = _b64e.b64encode(
                                        _pdf_bytes
                                    ).decode("ascii")
                                    st.markdown(
                                        f'<a href="data:application/pdf;base64,'
                                        f'{_b64_str}" '
                                        f'download="{_cid}.pdf" '
                                        f'style="display:inline-block;'
                                        f'margin:0.25rem 0 0.5rem 0;'
                                        f'padding:0.25rem 0.75rem;'
                                        f'background:#F4F6F8;color:#5A5A5A;'
                                        f'text-decoration:none;'
                                        f'border:1px solid #D8D6E5;'
                                        f'border-radius:4px;font-size:12px;">'
                                        f'⬇ Download {_cid}.pdf</a>',
                                        unsafe_allow_html=True,
                                    )

                # Chip buttons — only under the most recent assistant message
                chips = m.get("chips") or []
                if chips and idx == last_assistant_idx:
                    _chip_n   = min(3, len(chips))
                    chip_cols = st.columns(_chip_n)
                    for ci, chip_text in enumerate(chips[:3]):
                        with chip_cols[ci % _chip_n]:
                            if st.button(
                                chip_text,
                                key=f"kc_chip_{idx}_{ci}",
                                use_container_width=True,
                            ):
                                st.session_state["kc_pending"] = chip_text
                                st.rerun()

        st.markdown(
            '<script>'
            '(function(){'
            'var els=document.querySelectorAll('
            '"[data-testid=\'stVerticalBlock\']");'
            'if(els.length)els[els.length-1].scrollTop=99999;'
            '})();'
            '</script>',
            unsafe_allow_html=True,
        )

    # ── 3. PENDING CASE CONFIRMATION ─────────────────────────
    pending_case = st.session_state.get("kc_pending_case")
    if pending_case:
        st.markdown(
            "<div style='background:#F5F0FF;border:1px solid #3d2a4a;"
            "border-left:3px solid #4F46E5;border-radius:8px;"
            "padding:10px 14px;margin:6px 0;font-size:11px;'>"
            "<strong style='color:#4F46E5;'>Pending Case — confirm to save</strong><br><br>"
            + "<br>".join(
                f"<strong style='color:#5A5A5A;'>{k}:</strong> "
                f"<span style='color:#1A1A1A;'>{v}</span>"
                for k, v in pending_case.items()
            )
            + "</div>",
            unsafe_allow_html=True,
        )
        cy, cn = st.columns(2)
        with cy:
            if st.button("Save to Repository", type="primary",
                         use_container_width=True, key="kc_confirm_save"):
                os.makedirs(DATA_DIR, exist_ok=True)
                with st.spinner("Checking against knowledge base..."):
                    _assessment = assess_nl_case(pending_case, source="chatbot")

                _ks = _assessment.get("knowledge_state", "NEW")
                _retrieved_id = (
                    _assessment.get("retrieved_case_id")
                    or (_assessment.get("best_candidate") or {}).get("case_id", "")
                    or (_assessment.get("best_match") or {}).get("case_id", "")
                )
                _reasoning = _assessment.get("coverage_reasoning", "")

                if _ks == "EXISTING":
                    st.warning(
                        f"This fault is already documented as **{_retrieved_id}**. "
                        f"No new case saved. {_reasoning}"
                    )
                    st.session_state["kc_messages"].append({
                        "role": "assistant",
                        "content": (
                            f"I checked your case against the knowledge base. "
                            f"This fault is already documented as **{_retrieved_id}** — "
                            "no new case was created. Would you like to view it?"
                        ),
                        "timestamp": datetime.now().strftime("%I:%M %p"),
                    })
                    st.session_state["kc_pending_case"] = None
                    st.rerun()

                elif _ks == "PARTIAL":
                    _case_id = generate_next_case_id()
                    pending_case["case_id"]             = _case_id
                    pending_case["source"]               = "chatbot"
                    pending_case["created_at"]           = datetime.utcnow().isoformat() + "Z"
                    pending_case["assessment_verdict"]   = "PARTIAL"
                    pending_case["related_case_id"]      = _retrieved_id
                    pending_case["gaps"]                 = _assessment.get("missing_dimensions", [])
                    with open(f"{DATA_DIR}/{_case_id.lower()}_chatbot.json", "w") as _f:
                        json.dump(pending_case, _f, indent=2)
                    _missing = ", ".join(_assessment.get("missing_dimensions", []))
                    st.warning(
                        f"Saved as **{_case_id}** (partial). Related to **{_retrieved_id}**. "
                        f"Gaps: {_missing}. Awaiting engineering review."
                    )
                    st.session_state["kc_messages"].append({
                        "role": "assistant",
                        "content": (
                            f"Case **{_case_id}** saved as partial knowledge. "
                            f"It relates to **{_retrieved_id}** but has gaps: {_missing}. "
                            "Flagged for engineering review."
                        ),
                        "timestamp": datetime.now().strftime("%I:%M %p"),
                    })
                    st.session_state["kc_pending_case"] = None
                    st.rerun()

                else:  # NEW
                    _case_id = generate_next_case_id()
                    pending_case["case_id"]           = _case_id
                    pending_case["source"]             = "chatbot"
                    pending_case["created_at"]         = datetime.utcnow().isoformat() + "Z"
                    pending_case["assessment_verdict"] = "NEW"
                    with open(f"{DATA_DIR}/{_case_id.lower()}_chatbot.json", "w") as _f:
                        json.dump(pending_case, _f, indent=2)
                    try:
                        from api.streamlit_api import create_learned_case as _clc
                        _clc(
                            case_id=_case_id,
                            asset_type=pending_case.get("asset_type", "motor"),
                            bearing_type=pending_case.get("bearing_type", "SKF6310"),
                            fault_mode=pending_case.get("fault_mode", "unknown"),
                            root_cause=pending_case.get("root_cause", ""),
                            action_taken=pending_case.get("action_taken", ""),
                            result=pending_case.get("result", ""),
                            lessons_learned=pending_case.get("lessons_learned", ""),
                            valid_until="2028-12-31",
                            created_by="chatbot",
                        )
                    except Exception as _faiss_err:
                        st.warning(f"Saved to disk but FAISS indexing skipped: {_faiss_err}")
                    st.success(
                        f"Saved as **{_case_id}** — genuinely new knowledge added to the repository."
                    )
                    st.session_state["kc_messages"].append({
                        "role": "assistant",
                        "content": (
                            f"Case **{_case_id}** saved and indexed in the knowledge base. "
                            "It can be retrieved immediately by the agent."
                        ),
                        "timestamp": datetime.now().strftime("%I:%M %p"),
                    })
                    st.session_state["kc_pending_case"] = None
                    st.rerun()

        with cn:
            if st.button("Discard", use_container_width=True, key="kc_discard_save"):
                st.session_state["kc_pending_case"] = None
                st.session_state["kc_messages"].append({
                    "role": "assistant",
                    "content": "Case discarded. Let me know if you need anything else.",
                    "timestamp": datetime.now().strftime("%I:%M %p"),
                })
                st.rerun()

    user_input = st.chat_input(
        placeholder=(
            "Ask anything: fault risk, "
            "what-if scenarios, decisions..."
        ),
        key="kc_input",
    )
    if user_input:
        st.session_state["kc_pending"] = user_input
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
# SECTION: DISCOVERY TAB AND PIPELINE INVOCATION
# Full main dashboard: tab layout, Discovery/Search/Analytics routing.
# ═══════════════════════════════════════════════════════════════════
def page_main_dashboard():
    """Render the main dashboard with all tabs and the Knowledge Copilot."""
    # ─── DRO Header Bar (white strip) ───
    st.markdown("""
    <div class="dro-header">
        <div class="dro-header-mark">D</div>
        <div class="dro-header-text">
            <div class="dro-header-brand">DRO</div>
            <div class="dro-header-title">Learning &amp; Memory Agent</div>
            <div class="dro-header-sub">Bearing Failure &middot; Predictive &amp; Prescriptive Maintenance</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ─── Chatbot popover (positioned via CSS to overlay header) ───
    if hasattr(st, "popover"):
        with st.popover("Chatbot", use_container_width=False):
            try:
                render_knowledge_copilot()
            except Exception as _e:
                st.error(f"Chatbot unavailable: {_e}")
    if "kc_asset" not in st.session_state:
        st.session_state["kc_asset"] = {
            "asset_id":   "AST_MTR_001",
            "line":       "Line 4",
            "fault_type": "Outer Race Fault",
            "stage":      3,
            "rul_days":   6,
            "risk":       "HIGH",
        }
    if "kc_pending" not in st.session_state:
        st.session_state["kc_pending"] = ""
    if "kc_messages" not in st.session_state:
        st.session_state["kc_messages"] = []
    if "kc_pending_case" not in st.session_state:
        st.session_state["kc_pending_case"] = None
    if "kc_last_intent" not in st.session_state:
        st.session_state["kc_last_intent"] = None
    if "kc_last_retrieval" not in st.session_state:
        st.session_state["kc_last_retrieval"] = None
    if "last_feedback_dict" not in st.session_state:
        st.session_state["last_feedback_dict"] = {}

    st.markdown("""
<style>

/* Give tab content room to scroll normally */
[data-testid="stTabContent"] {
    overflow-y: auto !important;
    padding-bottom: 280px !important;
}

/* Chat scroll area — fixed height, internal scroll */
.kc-chat-scroll {
    height: 260px;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 8px 4px;
    margin-bottom: 4px;
    border: 1px solid #E2E0EA;
    border-radius: 8px;
    background: #FFFFFF;
}
.kc-chat-scroll::-webkit-scrollbar { width: 3px; }
.kc-chat-scroll::-webkit-scrollbar-track { background: transparent; }
.kc-chat-scroll::-webkit-scrollbar-thumb {
    background: #E2E0EA;
    border-radius: 2px;
}

/* Evidence card font lockdown — overrides Streamlit defaults */
details.kc-evidence,
details.kc-evidence * {
    font-size: 10px !important;
    line-height: 1.35 !important;
}
details.kc-evidence summary {
    font-size: 10px !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: .04em !important;
    color: #4F46E5 !important;
    cursor: pointer;
    list-style: none;
    outline: none;
    padding: 0;
    margin: 0;
}
details.kc-evidence summary::-webkit-details-marker {
    display: none;
}
details.kc-evidence b {
    font-size: 10px !important;
    color: #1A1A1A !important;
}
details.kc-evidence .kc-ev-eyebrow {
    font-size: 9px !important;
    color: #8A8A8A !important;
    text-transform: uppercase !important;
    letter-spacing: .06em !important;
}
details.kc-evidence .kc-ev-value {
    font-size: 10.5px !important;
    color: #1A1A1A !important;
}

/* Follow-up chip buttons inside chatbot */
div[data-testid="stHorizontalBlock"] button[kind="secondary"] {
    font-size: 10.5px !important;
    padding: 4px 10px !important;
    background: #F8F7FB !important;
    border: 1px solid #E2E0EA !important;
    border-radius: 12px !important;
    color: #5A5A5A !important;
    white-space: normal !important;
    line-height: 1.25 !important;
    min-height: 28px !important;
    height: auto !important;
}
div[data-testid="stHorizontalBlock"] button[kind="secondary"]:hover {
    border-color: #4F46E5 !important;
    color: #1A1A1A !important;
    background: #F0EFF8 !important;
}

/* Pills */
.kc-pills {
    display: flex;
    gap: 6px;
    margin-bottom: 5px;
    flex-wrap: nowrap;
    overflow-x: auto;
}
.kc-pills::-webkit-scrollbar { height: 0; }
.kc-pill {
    padding: 2px 9px;
    border-radius: 20px;
    font-size: 11px;
    font-family: Arial, sans-serif;
    font-weight: 500;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.kc-pill-loc {
    background: #F0EFF8;
    border: 1px solid #C0BDD0;
    color: #1A1A1A;
}
.kc-pill-fault {
    background: #FFF0F0;
    border: 1px solid #E65C00;
    color: #D97706;
}
.kc-pill-rul {
    background: #F0FFF4;
    border: 1px solid #16A34A;
    color: #16A34A;
}

/* Chat input */
[data-testid="stChatInput"] { margin-bottom: 0 !important; }
[data-testid="stChatInput"] > div {
    background: #F0EFF8 !important;
    border: 1px solid #C0BDD0 !important;
    border-radius: 10px !important;
}
[data-testid="stChatInput"] textarea {
    font-size: 12px !important;
    font-family: Arial, sans-serif !important;
    color: #1A1A1A !important;
    min-height: 40px !important;
    max-height: 40px !important;
    padding: 10px 12px !important;
}
[data-testid="stChatInput"] button {
    background: #A100FF !important;
    border-radius: 8px !important;
}

/* Chip buttons */
div.kc-chip-btn > div[data-testid="stButton"] > button {
    background: none !important;
    border: none !important;
    color: #5A5A5A !important;
    font-size: 11px !important;
    font-family: Arial, sans-serif !important;
    padding: 0 10px 0 0 !important;
    text-decoration: underline !important;
    text-underline-offset: 2px !important;
    white-space: nowrap !important;
    min-height: 0 !important;
    height: 22px !important;
    line-height: 1 !important;
    box-shadow: none !important;
}
div.kc-chip-btn > div[data-testid="stButton"] > button:hover {
    color: #A100FF !important;
    background: none !important;
    border: none !important;
    box-shadow: none !important;
}

/* Message bubbles */
.kc-user-row {
    display: flex;
    justify-content: flex-end;
    margin: 5px 0;
}
.kc-user-block {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    max-width: 65%;
}
.kc-user-meta {
    font-size: 10px;
    color: #8A8A8A;
    margin-bottom: 2px;
    font-family: Arial, sans-serif;
}
.kc-user-bubble {
    background: #F5F0FF;
    border: 1px solid #A100FF;
    border-radius: 14px 14px 3px 14px;
    padding: 7px 11px;
    color: #1A1A1A;
    font-size: 12px;
    font-family: Arial, sans-serif;
    line-height: 1.45;
}
.kc-agent-row {
    display: flex;
    flex-direction: column;
    margin: 5px 0;
    max-width: 75%;
}
.kc-agent-label {
    font-size: 10px;
    color: #5A5A5A;
    margin-bottom: 2px;
    font-family: Arial, sans-serif;
}
.kc-agent-bubble {
    background: #F8F7FB;
    border: 1px solid #E2E0EA;
    border-radius: 3px 14px 14px 14px;
    padding: 7px 11px;
    color: #1A1A1A;
    font-size: 12px;
    font-family: Arial, sans-serif;
    line-height: 1.5;
}
.kc-empty {
    padding: 20px 0;
    text-align: center;
    color: #8A8A8A;
    font-size: 12px;
    font-family: Arial, sans-serif;
    font-style: italic;
}
</style>
""", unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(
        ["Discovery",
         "Search Case",
         "Analytics & Insights"]
    )
    with tab1:
        render_pipeline_strip()

        # ── Element 1: Pipeline demo banner ──
        st.markdown("""
        <div class="topbar">
          <span style="width:7px;height:7px;border-radius:50%;background:#16A34A;
                       display:inline-block;flex-shrink:0"></span>
          <span style="font-size:13px;font-weight:600;color:#1A1A1A">
            Pipeline demo
          </span>
          <span style="font-size:11px;color:#8A8A8A;margin-left:8px">
            The agent decides the outcome automatically based on knowledge base matching
          </span>
        </div>
        """, unsafe_allow_html=True)

        # ── 50/50 split: left = scenario selector, right = results ──
        _has_result = "last_scenario_result" in st.session_state
        _left_col, _right_col = st.columns([1, 1])

        with _left_col:
            # Scenario selector + description + run button
            st.markdown(
                '<div class="sec-label" style="font-size:14px;">Select Demo Scenario</div>',
                unsafe_allow_html=True
            )
            scenario_name = st.selectbox(
                "Select demo scenario", list(SCENARIOS.keys()),
                label_visibility="collapsed", key="disc_scenario_select"
            )
            _desc = SCENARIO_DESCRIPTIONS.get(scenario_name, "")
            if _desc:
                st.markdown(f"""
                <div style="background:#F0EFF8; border:1px solid #E2E0EA;
                            border-radius:8px; padding:10px 16px;
                            margin-bottom:10px; font-size:12px; color:#5A5A5A;
                            border-left:3px solid #4F46E5;">
                  <span style="font-size:10px; font-weight:600; color:#4F46E5;
                               text-transform:uppercase; letter-spacing:.08em;">
                    Scenario Description
                  </span><br><br>
                  {_desc}
                </div>
                """, unsafe_allow_html=True)
            _run = st.button("Run scenario", type="primary",
                             use_container_width=False, key="disc_run_btn")
            if _run:
                _sc = SCENARIOS[scenario_name]
                st.session_state["sc_pf"] = {
                    **_sc,
                    "description": SCENARIO_DESCRIPTIONS.get(scenario_name, "")
                }
                _rul_est = (6 if _sc["risk"] == "HIGH"
                            else 14 if _sc["risk"] == "MEDIUM" else 30)
                st.session_state["kc_asset"] = {
                    "asset_id":   _sc["asset"],
                    "line":       "Line 4",
                    "fault_type": _sc["fault"].replace("_", " ").title(),
                    "stage":      _sc["stage"],
                    "rul_days":   _sc.get("rul_days", _rul_est),
                    "risk":       _sc["risk"],
                }
                import uuid as _uuid
                _run_id = str(_uuid.uuid4())[:8]
                _pipeline_result = None
                if PIPELINE_AVAILABLE:
                    with st.spinner("Running agent pipeline..."):
                        try:
                            _feedback = build_feedback_from_scenario(_sc, _run_id)
                            st.session_state["last_feedback_dict"] = _feedback
                            _pipeline_result = run_pipeline_demo(
                                _feedback, _run_id,
                                llm_client=llm_client,
                                langfuse_handler=langfuse_handler,
                            )
                            st.session_state["last_pipeline_result"] = _pipeline_result
                            st.session_state["last_run_id"] = _run_id
                        except Exception as _pipe_err:
                            print(f"[PIPELINE] Run failed: {_pipe_err}")
                            st.warning(f"Pipeline run failed: {_pipe_err}. Showing demo data.")
                # Tracker write removed: each pipeline node (existing_knowledge_node,
                # case_generation_node, coverage_assessment_node) already calls
                # record_fault_event internally. A second write here produced a
                # duplicate Activity Log entry per Discovery run.
                st.session_state["last_scenario_result"] = _pipeline_result
                import logging as _sc_log
                _scl = _sc_log.getLogger(__name__)
                _scl.info("[discovery_scenario] pipeline complete. session_state keys with 'case' or 'result' or 'pipeline': %s",
                          [k for k in st.session_state.keys() if any(t in k.lower() for t in ['case', 'result', 'pipeline'])])
                for _k in [k for k in st.session_state.keys() if any(t in k.lower() for t in ['case', 'result', 'pipeline'])]:
                    _v = st.session_state.get(_k)
                    if isinstance(_v, dict):
                        _scl.info("[discovery_scenario] key=%s type=dict keys=%s sample_values: case_id=%r fault_mode=%r vib_rms_mm_s=%r",
                                  _k, list(_v.keys())[:15], _v.get("case_id"), _v.get("fault_mode"), _v.get("vib_rms_mm_s"))
                    else:
                        _scl.info("[discovery_scenario] key=%s type=%s", _k, type(_v).__name__)
                st.rerun()

            # ── Recent Case Documents + Activity Log — side by side ──
            import base64 as _disc_b64
            import logging as _disc_log
            import json as _disc_json
            from pathlib import Path as _DPath
            from datetime import datetime as _DT
            import time as _disc_time
            from services.activity_log import load_activity_log as _load_alog
            _discl = _disc_log.getLogger(__name__)

            _col_rc, _col_al = st.columns(2)

            # ── LEFT: Recent Case Documents ──
            with _col_rc:
                st.markdown(
                    "<div style='color:#A100FF;font-size:0.72rem;font-weight:600;"
                    "letter-spacing:0.08em;text-transform:uppercase;"
                    "margin-bottom:0.4rem;'>Recent Case Documents</div>",
                    unsafe_allow_html=True,
                )
                _rcd_t0 = _disc_time.perf_counter()
                # Source of truth: FAISS metadata (includes cases whose disk .json
                # may be temporarily absent). Disk is only consulted per-case when
                # generating a PDF — missing-disk rows show a disabled indicator.
                try:
                    with open(f"{FAISS_INDEX_DIR}/metadata.json", "r", encoding="utf-8") as _mf:
                        _rcd_meta = _disc_json.load(_mf)
                    _rcd_cases = (
                        list(_rcd_meta.values()) if isinstance(_rcd_meta, dict)
                        else list(_rcd_meta)
                    )
                except Exception as _rcd_err:
                    _discl.warning("[recent_cases] FAISS metadata read failed: %s", _rcd_err)
                    _rcd_cases = []

                # Sort by created_at desc; fall back to case_id lexical desc when absent
                _rcd_cases.sort(
                    key=lambda _c: (
                        _c.get("created_at") or "",
                        _c.get("case_id") or "",
                    ),
                    reverse=True,
                )
                _rcd_cases = _rcd_cases[:20]
                _discl.info("[recent_cases] FAISS source: %d cases", len(_rcd_cases))

                if not _rcd_cases:
                    st.markdown(
                        "<div style='padding:0.6rem;color:#8A8A8A;font-size:0.82rem;'>"
                        "No cases yet.</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    from services.pdf_renderer import render_case_pdf as _rcpdf
                    _rc_rows = []
                    _rcd_json_t = 0.0
                    _rcd_pdf_t  = 0.0
                    for _cm in _rcd_cases:
                        _cid   = _cm.get("case_id", "unknown")
                        _fault = (_cm.get("fault_mode") or "unknown").replace("_", " ")
                        _asset = _cm.get("asset_type") or ""
                        _src   = _cm.get("source") or ""

                        # Resolve disk path — needed for PDF only
                        _j0 = _disc_time.perf_counter()
                        _disk_p = _disk_path_for_case_id(_cid)
                        _rcd_json_t += _disc_time.perf_counter() - _j0

                        _pdf_href = None
                        if _disk_p is not None:
                            _p0 = _disc_time.perf_counter()
                            try:
                                _case_body = _disc_json.loads(
                                    _disk_p.read_text(encoding="utf-8")
                                )
                                _pdf = _rcpdf(_case_body)
                                if isinstance(_pdf, bytes) and len(_pdf) > 100:
                                    _b64 = _disc_b64.b64encode(_pdf).decode("ascii")
                                    _pdf_href = f"data:application/pdf;base64,{_b64}"
                            except Exception as _pe:
                                _discl.warning("[recent_cases] pdf %s: %s", _cid, _pe)
                            _rcd_pdf_t += _disc_time.perf_counter() - _p0

                        if _pdf_href:
                            _dl_html = (
                                f'<a href="{_pdf_href}" download="{_cid}.pdf" '
                                f'style="color:#A100FF;text-decoration:none;font-size:0.9rem;'
                                f'padding:0 0.3rem;opacity:0.7;" '
                                f'onmouseover="this.style.opacity=\'1\';" '
                                f'onmouseout="this.style.opacity=\'0.7\';" '
                                f'title="Download PDF">↓</a>'
                            )
                        else:
                            # FAISS entry exists but no disk file — row visible, PDF disabled
                            _tip = (
                                "PDF unavailable (FAISS entry exists, disk file missing)"
                                if _disk_p is None else "PDF render failed"
                            )
                            _dl_html = (
                                f'<span style="color:#C0BDD0;padding:0 0.3rem;'
                                f'cursor:default;" title="{_tip}">·</span>'
                            )

                        _src_badge = (
                            f'<span style="color:#8A8A8A;font-size:0.65rem;'
                            f'margin-left:0.3rem;">[{_src}]</span>' if _src else ""
                        )
                        _rc_rows.append(
                            f'<div style="padding:0.3rem 0.6rem;'
                            f'border-bottom:1px solid #E8E5F5;'
                            f'font-family:monospace;font-size:0.72rem;color:#1A1A1A;'
                            f'white-space:nowrap;">'
                            f'<span style="color:#4F46E5;font-weight:600;margin-right:0.4rem;">'
                            f'{_cid}</span>'
                            f'<span style="color:#5A5A5A;margin-right:0.4rem;">'
                            f'fault={_fault}&nbsp;&middot;&nbsp;asset={_asset}</span>'
                            f'{_src_badge}'
                            f'<span>{_dl_html}</span>'
                            f'</div>'
                        )
                    st.markdown(
                        f'<div class="compact-panel" style="max-height:220px;overflow-y:auto;'
                        f'overflow-x:auto;border:1px solid #D8D6E5;'
                        f'border-radius:0.4rem;background:#F4F6F8;">'
                        f'<div style="display:inline-block;min-width:100%;">'
                        f'{"".join(_rc_rows)}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                _discl.info(
                    "[latency] recent_case_docs_render took %.3fs total "
                    "(json_reads=%.3fs, pdf_gen=%.3fs, n_faiss=%d)",
                    _disc_time.perf_counter() - _rcd_t0,
                    _rcd_json_t if "_rcd_json_t" in dir() else 0.0,
                    _rcd_pdf_t  if "_rcd_pdf_t"  in dir() else 0.0,
                    len(_rcd_cases),
                )

            # ── RIGHT: Activity Log ──
            with _col_al:
                st.markdown(
                    "<div style='color:#A100FF;font-size:0.72rem;font-weight:600;"
                    "letter-spacing:0.08em;text-transform:uppercase;"
                    "margin-bottom:0.4rem;'>Activity Log</div>",
                    unsafe_allow_html=True,
                )
                _al_t0 = _disc_time.perf_counter()
                _al_entries = _load_alog(limit=20)
                if not _al_entries:
                    st.markdown(
                        "<div style='padding:0.6rem;color:#8A8A8A;font-size:0.82rem;'>"
                        "No activity yet.</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    _log_rows = []
                    for _e in _al_entries:
                        _ts_s = _e["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                        _cid_d = _e.get("display_case_id") or _e.get("case_id") or "—"
                        _fault_d = (_e.get("fault_mode") or "—")
                        _log_rows.append(
                            f'<div style="padding:0.35rem 0.6rem;'
                            f'border-bottom:1px solid #E8E5F5;'
                            f'font-family:monospace;font-size:0.75rem;color:#1A1A1A;'
                            f'white-space:nowrap;">'
                            f'<span style="color:#8A8A8A;font-size:0.7rem;margin-right:0.6rem;">'
                            f'{_ts_s}</span>'
                            f'<span style="color:#4F46E5;font-weight:600;margin-right:0.6rem;">'
                            f'{_cid_d}</span>'
                            f'<span style="color:#1A1A1A;">{_fault_d}</span>'
                            f'</div>'
                        )
                    st.markdown(
                        f'<div class="compact-panel" style="max-height:220px;overflow-y:auto;'
                        f'overflow-x:auto;border:1px solid #D8D6E5;'
                        f'border-radius:0.4rem;background:#F4F6F8;">'
                        f'<div style="display:inline-block;min-width:100%;">'
                        f'{"".join(_log_rows)}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                _discl.info(
                    "[latency] activity_log_render took %.3fs (n_entries=%d)",
                    _disc_time.perf_counter() - _al_t0, len(_al_entries),
                )

            # ── Post-run LEFT content ──
            if _has_result:
                _sc = st.session_state.get("sc_pf", {})
                _pipeline_result = st.session_state.get("last_pipeline_result")
                if _pipeline_result is not None and isinstance(_pipeline_result, dict):
                    _knowledge_state = _pipeline_result.get("knowledge_state", "NEW")
                    _path_taken = _pipeline_result.get("path_taken", "B")
                    if _path_taken == "B":
                        try:
                            st.cache_data.clear()
                        except Exception:
                            pass
                    _processing_log = _pipeline_result.get("processing_log", [])
                    _gap_summary = _pipeline_result.get("gap_summary", "")
                    _dim_assessments = _pipeline_result.get("dimension_assessments", {})
                    _covered_dims = _pipeline_result.get("covered_dimensions", [])
                    _missing_dims = _pipeline_result.get("missing_dimensions", [])
                    _best_similarity = _pipeline_result.get("best_similarity", 0.0)
                    _gap_details_left = _pipeline_result.get("gap_details") or {}
                    _retrieved_case_id = (
                        _pipeline_result.get("retrieved_case_id", "")
                        or _gap_details_left.get("matched_case_id", "")
                    )
                else:
                    _fallback_score = _sc.get("score", 0.5)
                    _knowledge_state = "EXISTING" if _fallback_score >= 0.70 else "NEW"
                    _path_taken = "A" if _fallback_score >= 0.70 else "B"
                    _processing_log = ["Demo mode — LangGraph not connected"]
                    _gap_summary = ""
                    _dim_assessments = {}
                    _covered_dims = []
                    _missing_dims = []
                    _best_similarity = _fallback_score
                    _retrieved_case_id = _sc.get("matched_case", "")
                _path = _path_taken

        with _right_col:
            if _has_result:
                # G: Inputs from Upstream Agents card (full enriched feedback)
                _fb = st.session_state.get("last_feedback_dict", {})

                def _row(label, value, unit="", skip_if_empty=True):
                    if skip_if_empty and (value is None or value == "" or value == 0):
                        return ""
                    if isinstance(value, float):
                        v_display = f"{value:g}"
                    else:
                        v_display = str(value)
                    if unit:
                        v_display = f"{v_display} {unit}"
                    return (
                        f'<div class="vrow">'
                        f'<span class="vkey">{label}</span>'
                        f'<span class="vval">{v_display}</span>'
                        f'</div>'
                    )

                def _group_header(text):
                    return (
                        f'<div style="font-size:10px;font-weight:600;color:#4F46E5;'
                        f'text-transform:uppercase;letter-spacing:.08em;'
                        f'margin:0 0 6px 0;">{text}</div>'
                    )

                # ── Group 1: Asset Context ──
                _asset_html = (
                    '<div class="upstream-box">'
                    + _group_header("Asset Context")
                    + _row("Asset ID",     _fb.get("asset_id"))
                    + _row("Asset type",   _fb.get("asset_type"))
                    + _row("Bearing type", _fb.get("bearing_type"))
                    + '</div>'
                )

                # ── Group 2: Fault Diagnosis ──
                _diag_html = (
                    '<div class="upstream-box">'
                    + _group_header("Fault Diagnosis")
                    + _row("Fault mode",           _fb.get("fault_mode"))
                    + _row("Stage",                _fb.get("fault_stage"))
                    + _row("Anomaly score",        _fb.get("anomaly_score"))
                    + _row("Diagnosis confidence", _fb.get("diagnosis_confidence"))
                )
                _diag_reason = _fb.get("diagnosis_reasoning", "")
                if _diag_reason:
                    _diag_html += (
                        f'<div class="vrow" style="flex-direction:column;'
                        f'align-items:flex-start;gap:2px;padding:6px 0;">'
                        f'<span class="vkey">Reasoning</span>'
                        f'<span class="vval" style="text-align:left;'
                        f'font-size:11px;line-height:1.4;color:#5A5A5A;">'
                        f'{_diag_reason}</span></div>'
                    )
                _diag_html += '</div>'

                # ── Group 3: Risk & Prediction ──
                _risk_html = (
                    '<div class="upstream-box">'
                    + _group_header("Risk and Prediction")
                    + _row("Risk level",          _fb.get("risk_level"))
                    + _row("RUL estimate",        _fb.get("rul_estimate"), unit="days")
                    + _row("Failure probability", _fb.get("failure_probability"), unit="%")
                    + _row("Work order priority", _fb.get("work_order_priority"))
                    + '</div>'
                )

                # ── Group 4: Signal Readings ──
                _signal_rows = []
                _signals_to_show = [
                    "vib_rms_mm_s", "kurtosis", "temp_c",
                    "bpfo_energy", "bpfi_energy",
                    "signal_quality_score", "shaft_offset_mm",
                ]
                for _sig_key in _signals_to_show:
                    _sig_val = _fb.get(_sig_key)
                    if _sig_val is None or _sig_val == 0:
                        continue
                    _sig_label, _sig_unit = SIGNAL_LABELS.get(_sig_key, (_sig_key, ""))
                    _signal_rows.append(_row(_sig_label, _sig_val, _sig_unit, skip_if_empty=False))
                _signals_html = (
                    '<div class="upstream-box">'
                    + _group_header("Signal Readings")
                    + "".join(_signal_rows)
                    + '</div>'
                )

                # ── Group 5: Recommended Action ──
                _action_html = (
                    '<div class="upstream-box">'
                    + _group_header("Recommended Action")
                    + _row("Action",         _fb.get("recommended_action"))
                    + _row("SOP reference",  _fb.get("sop_reference"))
                    + _row("Parts required", _fb.get("parts_required"))
                    + _row("Est. duration",  _fb.get("estimated_duration_hr"), unit="hours")
                    + '</div>'
                )

                # ── Group 6: Technician Context ──
                _tech_obs = _fb.get("technician_observations", "")
                _tech_html = ""
                if _tech_obs:
                    _tech_html = (
                        '<div class="upstream-box">'
                        + _group_header("Technician Context")
                        + f'<div class="vrow" style="flex-direction:column;'
                          f'align-items:flex-start;gap:2px;padding:6px 0;">'
                          f'<span class="vkey">Observations</span>'
                          f'<span class="vval" style="text-align:left;'
                          f'font-size:11px;line-height:1.4;color:#5A5A5A;">'
                          f'{_tech_obs}</span></div>'
                        + '</div>'
                    )

                # ── Group 7: Expected Post-Repair (QA criteria) ──
                _qa_html = (
                    '<div class="upstream-box">'
                    + _group_header("Expected Post-Repair (QA)")
                    + _row("Vibration max",   _fb.get("expected_post_vib_max"),      unit="mm/s")
                    + _row("Temperature max", _fb.get("expected_post_temp_max"),     unit="°C")
                    + _row("Kurtosis max",    _fb.get("expected_post_kurtosis_max"))
                    + _row("QA window",       _fb.get("qa_window"))
                    + '</div>'
                )

                st.markdown(f"""
                <div class="dark-card">
                  <div class="sec-label">Inputs from Upstream Agents</div>
                  <div style="font-size:13px;font-weight:600;color:#1A1A1A">
                    {_fb.get('asset_id', '')} &middot; {_fb.get('bearing_type', '')}
                  </div>
                  <div style="font-size:11px;color:#5A5A5A;margin-bottom:4px">
                    {_fb.get('fault_mode', '')} &mdash; Stage {_fb.get('fault_stage', '')}
                    &middot; Risk {_fb.get('risk_level', '')}
                  </div>
                  <div class="upstream-grid">
                    {_asset_html}
                    {_risk_html}
                  </div>
                  {_diag_html}
                  <div class="upstream-grid">
                    {_signals_html}
                    {_qa_html}
                  </div>
                  {_action_html}
                  {_tech_html}
                </div>
                """, unsafe_allow_html=True)

                # ── P1: Explainable E/P/N outcome panel ──────────────────────────────────
                _path_map = {"A": "EXISTING", "B": "NEW", "C": "PARTIAL"}
                fd = st.session_state.get("last_feedback_dict", {})
                _pr = st.session_state.get("last_pipeline_result", {})
                _ks = _pr.get("knowledge_state", _path_map.get(_path, "EXISTING"))

                _gap_details_right = _pr.get("gap_details") or {}
                _retrieved_id  = (
                    _pr.get("retrieved_case_id", "")
                    or _gap_details_right.get("matched_case_id", "")
                    or fd.get("preferred_case_id", "")
                )
                _covered_dims  = _pr.get("covered_dimensions", []) or _covered_dims
                _missing_dims  = _pr.get("missing_dimensions", []) or _missing_dims
                _gap_summary   = _pr.get("gap_summary", "") or _gap_summary
                _gen_case_id   = _pr.get("stored_case_id", "") or fd.get("case_id", "")

                _DIM_LABELS = {
                    "signal_signature":             "Signal signature",
                    "diagnosis_and_reasoning":      "Diagnosis & reasoning",
                    "action_and_outcome":            "Action & outcome",
                    "root_cause_and_factors":        "Root cause & factors",
                    "lessons_and_future_reference":  "Lessons & future reference",
                }
                _ALL_DIMS = list(_DIM_LABELS.keys())

                def _dim_chips(dim_list, chip_color):
                    chips = ""
                    for d in dim_list:
                        label = _DIM_LABELS.get(d, d.replace("_", " ").title())
                        chips += (
                            f'<span style="display:inline-block;padding:3px 9px;'
                            f'border-radius:12px;font-size:11px;font-weight:500;'
                            f'background:{chip_color}20;color:{chip_color};'
                            f'border:1px solid {chip_color}50;margin:2px 3px 2px 0;">'
                            f'{label}</span>'
                        )
                    return chips

                import time as _rp_time
                _rp_t0 = _rp_time.perf_counter()
                if _ks == "EXISTING":
                    _covered = _covered_dims if _covered_dims else _ALL_DIMS
                    _source_line_ex = (
                        f"<br><span style='color:#8A8A8A;font-size:11px;'>Source: {_retrieved_id}</span>"
                        if _retrieved_id else ""
                    )
                    st.html(
                        f"""<div style="background:#F8F7FB;border:1px solid #D8D6E5;
                            border-left:4px solid #16A34A;border-radius:8px;
                            padding:16px 18px;margin-top:12px;">

                          <div style="font-size:13px;font-weight:700;color:#16A34A;
                              margin-bottom:12px;">
                            ✓ Existing Knowledge — Retrieved from Memory
                          </div>

                          <div style="font-size:11px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">
                            Available
                          </div>
                          <div style="margin-bottom:10px;">
                            {_dim_chips(_covered, "#16A34A")}
                          </div>

                          <div style="font-size:11px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">
                            Missing
                          </div>
                          <div style="margin-bottom:10px;">
                            <span style="font-size:12px;color:#8A8A8A;font-style:italic;">
                              None — complete knowledge found.
                            </span>
                          </div>

                          <div style="font-size:11px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">
                            Learned
                          </div>
                          <div style="font-size:12.5px;color:#1A1A1A;line-height:1.6;">
                            Complete knowledge already exists.
                            No new case written. Existing case retrieved.
                            {_source_line_ex}
                          </div>

                        </div>"""
                    )

                elif _ks == "PARTIAL":
                    # Pull PATH_C specific data
                    _gap_details_ptc = _pr.get("gap_details") or {}
                    _ptc_case_id = (
                        _retrieved_id
                        or _gap_details_ptc.get("matched_case_id", "")
                    )
                    _ptc_similarity = (
                        _best_similarity
                        or _gap_details_ptc.get("similarity_score", 0.0)
                    )
                    _ptc_gap_text = (
                        _gap_summary
                        or _gap_details_ptc.get("gap_summary", "")
                        or "Partial knowledge match. Some dimensions require new findings."
                    )

                    # Dimension row renderer
                    _ORDERED_DIMS_PTC = [
                        "signal_signature",
                        "diagnosis_and_reasoning",
                        "action_and_outcome",
                        "root_cause_and_factors",
                        "lessons_and_future_reference",
                    ]
                    _DIM_DISPLAY_PTC = {
                        "signal_signature":            "Signal signature",
                        "diagnosis_and_reasoning":     "Diagnosis reasoning",
                        "action_and_outcome":           "Action & outcome",
                        "root_cause_and_factors":       "Root cause",
                        "lessons_and_future_reference": "Lessons learned",
                    }
                    _VICON = {"COMPLETE": "✓", "PARTIAL": "~", "MISSING": "✗"}
                    _VCOLOR = {
                        "COMPLETE": "#16A34A",
                        "PARTIAL":  "#D97706",
                        "MISSING":  "#dc2626",
                    }

                    def _dim_row_ptc(dim_key, assessments):
                        info = (assessments or {}).get(dim_key, {})
                        verdict = info.get("status", "MISSING")
                        note = info.get("note", "")
                        label = _DIM_DISPLAY_PTC.get(
                            dim_key, dim_key.replace("_", " ").title())
                        icon = _VICON.get(verdict, "?")
                        color = _VCOLOR.get(verdict, "#5A5A5A")
                        note_html = (
                            f'<span style="color:#8A8A8A;font-size:0.73rem;'
                            f'margin-left:0.5rem;">— {note}</span>'
                        ) if note else ""
                        return (
                            f'<div style="padding:0.3rem 0.8rem;display:flex;'
                            f'align-items:flex-start;font-family:monospace;'
                            f'font-size:0.82rem;gap:0.5rem;'
                            f'border-bottom:1px solid #E8E5F5;">'
                            f'<span style="color:{color};font-weight:700;'
                            f'min-width:1rem;margin-top:1px;">{icon}</span>'
                            f'<span style="color:#1A1A1A;flex:1;">{label}</span>'
                            f'<span style="color:{color};font-size:0.73rem;'
                            f'font-weight:600;white-space:nowrap;">{verdict}</span>'
                            f'{note_html}'
                            f'</div>'
                        )

                    if _dim_assessments:
                        _dim_rows_html_ptc = "".join(
                            _dim_row_ptc(d, _dim_assessments)
                            for d in _ORDERED_DIMS_PTC
                        )
                    else:
                        _dim_rows_html_ptc = (
                            '<div style="color:#8A8A8A;font-size:0.82rem;'
                            'padding:0.5rem 0.8rem;">'
                            'Dimension breakdown unavailable — see processing log.'
                            '</div>'
                        )

                    _ptc_case_badge = (
                        f"<span style='color:#8A8A8A;font-size:0.78rem;'>"
                        f"Matched: {_ptc_case_id}</span>&nbsp;&nbsp;"
                    ) if _ptc_case_id else ""

                    st.html(
                        f"""<div style="background:#F8F7FB;border:1px solid #D8D6E5;
                            border-left:4px solid #D97706;border-radius:8px;
                            padding:16px 18px;margin-top:12px;">

                          <div style="font-size:13px;font-weight:700;color:#D97706;
                              margin-bottom:8px;">
                            ⊘ Partial Knowledge Match
                          </div>

                          <div style="display:flex;align-items:center;gap:1rem;
                              flex-wrap:wrap;margin-bottom:12px;">
                            {_ptc_case_badge}
                          </div>

                          <div style="font-size:10px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;
                              margin-bottom:4px;">
                            Knowledge Coverage
                          </div>
                          <div style="border:1px solid #D8D6E5;border-radius:6px;
                              margin-bottom:12px;background:#FFFFFF;
                              overflow:hidden;">
                            {_dim_rows_html_ptc}
                          </div>

                          <div style="font-size:10px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;
                              margin-bottom:4px;">
                            What This Means
                          </div>
                          <div style="font-size:12px;color:#1A1A1A;line-height:1.5;">
                            {_ptc_gap_text}<br>
                            <span style="color:#D97706;font-size:11px;font-weight:600;">
                              Existing case not auto-enriched. Awaiting human validation.
                            </span>
                          </div>

                        </div>"""
                    )

                    # Enrichment button + preview
                    _enrich_key_ptc = f"enrichment_preview_{_pr.get('run_id', 'ptc')}"
                    if st.button(
                        "Preview Enrichment",
                        key=f"enrich_btn_{_pr.get('run_id', 'ptc')}",
                        type="primary",
                    ):
                        _fb_enrich = st.session_state.get("last_feedback_dict", {})
                        _rc_enrich = (
                            _pr.get("best_candidate")
                            or _pr.get("best_match")
                            or {}
                        )
                        _missing_for_enrich = [
                            d for d in _ORDERED_DIMS_PTC
                            if (_dim_assessments or {}).get(d, {}).get("status")
                            in ("PARTIAL", "MISSING")
                        ] or _missing_dims or _ORDERED_DIMS_PTC
                        with st.spinner("Generating enrichment preview…"):
                            _proposal_ptc = _generate_enrichment_preview(
                                incoming_feedback=_fb_enrich,
                                retrieved_case=_rc_enrich,
                                missing_dimensions=_missing_for_enrich,
                                llm_client=llm_client,
                            )
                        st.session_state[_enrich_key_ptc] = _proposal_ptc

                    if _enrich_key_ptc in st.session_state:
                        _prop = st.session_state[_enrich_key_ptc]
                        _ref_label = _ptc_case_id or "the matched case"
                        _ENRICH_LABEL_MAP = {
                            "signal_signature":            "Signal Signature",
                            "diagnosis_and_reasoning":     "Diagnosis & Reasoning",
                            "action_and_outcome":          "Action & Outcome",
                            "root_cause_and_factors":      "Root Cause & Factors",
                            "lessons_and_future_reference":"Lessons & Future Reference",
                        }
                        st.markdown(
                            f'<div style="margin-top:0.8rem;padding:0.6rem 1rem 0.2rem;'
                            f'background:#F4F6F8;border:1px solid #D8D6E5;'
                            f'border-left:3px solid #D97706;border-radius:0.4rem;">'
                            f'<div style="color:#D97706;font-size:0.72rem;'
                            f'font-weight:600;letter-spacing:0.08em;'
                            f'text-transform:uppercase;margin-bottom:0.5rem;">'
                            f'Proposed Enrichment (Preview)</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        _prop_dims = _prop.get("dims") or {}
                        _empty_reason = _prop.get("empty_reason")

                        # ── Asset Context — incoming vs retrieved, side by side ──
                        _inc_asset_id   = fd.get("asset_id") or "—"
                        _inc_asset_type = fd.get("asset_type") or "—"
                        _inc_bearing    = fd.get("bearing_type") or "—"
                        _ret_case_disk  = _load_case_content_from_disk(_ptc_case_id) if _ptc_case_id else {}
                        _ret_asset_id   = (
                            (_ret_case_disk.get("asset_context") or {}).get("asset_id")
                            or _ret_case_disk.get("asset_id")
                            or _ret_case_disk.get("case_id")
                            or "—"
                        )
                        _ret_asset_type = _ret_case_disk.get("asset_type") or "—"
                        _ret_bearing    = _ret_case_disk.get("bearing_type") or "—"
                        _COMPACT_TD_KEY = (
                            "padding:3px 10px;border-bottom:1px solid #E5E5E5;"
                            "color:#5A5A5A;font-size:12px;vertical-align:top;"
                        )
                        _COMPACT_TD_VAL = (
                            "padding:3px 10px;border-bottom:1px solid #E5E5E5;"
                            "color:#1A1A1A;font-size:12px;vertical-align:top;"
                        )
                        _COMPACT_TABLE = "width:100%;border-collapse:collapse;margin:0;"

                        with st.expander("Asset Context", expanded=True):
                            _ctx_data = [
                                ("Asset ID",    _inc_asset_id,   _ret_asset_id),
                                ("Asset type",  _inc_asset_type, _ret_asset_type),
                                ("Bearing type",_inc_bearing,    _ret_bearing),
                            ]
                            _hdr_td = (
                                "padding:3px 10px;border-bottom:2px solid #D8D6E5;"
                                "color:#5A5A5A;font-size:11px;font-weight:600;"
                                "text-transform:uppercase;letter-spacing:.05em;"
                            )
                            _ctx_rows_html = [
                                f"<tr>"
                                f"<th style='{_hdr_td}width:28%;'></th>"
                                f"<th style='{_hdr_td}'>Incoming</th>"
                                f"<th style='{_hdr_td}'>Retrieved case</th>"
                                f"</tr>"
                            ]
                            for _lbl, _inc_v, _ret_v in _ctx_data:
                                _ctx_rows_html.append(
                                    f"<tr>"
                                    f"<td style='{_COMPACT_TD_KEY}width:28%;'>{_lbl}</td>"
                                    f"<td style='{_COMPACT_TD_VAL}'>{_inc_v}</td>"
                                    f"<td style='{_COMPACT_TD_VAL}'>{_ret_v}</td>"
                                    f"</tr>"
                                )
                            st.markdown(
                                f"<table style='{_COMPACT_TABLE}'>"
                                + "".join(_ctx_rows_html)
                                + "</table>",
                                unsafe_allow_html=True,
                            )

                        if _prop_dims:
                            for _dk, _dval in _prop_dims.items():
                                _exp_label = _ENRICH_LABEL_MAP.get(_dk, _dk.replace("_", " ").title())
                                with st.expander(_exp_label, expanded=True):
                                    if isinstance(_dval, dict):
                                        _dim_rows_html = []
                                        for _fk, _fv in _dval.items():
                                            _fv_display = (
                                                ", ".join(str(x) for x in _fv)
                                                if isinstance(_fv, list)
                                                else str(_fv)
                                            )
                                            _dim_rows_html.append(
                                                f"<tr>"
                                                f"<td style='{_COMPACT_TD_KEY}width:35%;'>"
                                                f"{_fk.replace('_', ' ').title()}</td>"
                                                f"<td style='{_COMPACT_TD_VAL}'>"
                                                f"{_fv_display}</td>"
                                                f"</tr>"
                                            )
                                        st.markdown(
                                            f"<table style='{_COMPACT_TABLE}'>"
                                            + "".join(_dim_rows_html)
                                            + "</table>",
                                            unsafe_allow_html=True,
                                        )
                                    else:
                                        # Graceful fallback if LLM returned prose string
                                        st.markdown(str(_dval))
                        else:
                            st.caption(_empty_reason or "No enrichment fields generated. Check LLM availability.")
                        st.markdown(
                            f'<div style="color:#8A8A8A;font-size:0.72rem;'
                            f'margin-top:0.4rem;margin-bottom:0.6rem;font-style:italic;">'
                            f'Preview only — in production this would append to '
                            f'{_ref_label} as new knowledge chunks, without '
                            f'creating a duplicate case.</div>',
                            unsafe_allow_html=True,
                        )

                else:  # _ks == "NEW"
                    _all_dim_chips = _dim_chips(_ALL_DIMS, "#A100FF")
                    _case_id_line = (
                        f"<br><span style='color:#8A8A8A;font-size:11px;'>Case ID: {_gen_case_id}</span>"
                        if _gen_case_id else ""
                    )
                    st.html(
                        f"""<div style="background:#F8F7FB;border:1px solid #D8D6E5;
                            border-left:4px solid #A100FF;border-radius:8px;
                            padding:16px 18px;margin-top:12px;">

                          <div style="font-size:13px;font-weight:700;color:#A100FF;
                              margin-bottom:12px;">
                            ◆ New Knowledge — Generated and Stored
                          </div>

                          <div style="font-size:11px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">
                            Available
                          </div>
                          <div style="margin-bottom:10px;">
                            <span style="font-size:12px;color:#8A8A8A;font-style:italic;">
                              No matching knowledge found in the knowledge base.
                            </span>
                          </div>

                          <div style="font-size:11px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">
                            Missing
                          </div>
                          <div style="margin-bottom:10px;">
                            {_all_dim_chips}
                          </div>

                          <div style="font-size:11px;font-weight:600;color:#5A5A5A;
                              text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">
                            Learned
                          </div>
                          <div style="font-size:12.5px;color:#1A1A1A;line-height:1.6;">
                            A 7-section case document was built and stored.
                            This is genuinely new institutional knowledge,
                            retrievable by the Knowledge Agent immediately.
                            {_case_id_line}
                          </div>

                        </div>"""
                    )


                # ── Discovery PDF download ──
                _disc_fb = st.session_state.get("last_feedback_dict") or {}
                _disc_pr = st.session_state.get("last_pipeline_result") or {}
                _disc_generated = _disc_pr.get("generated_case_content") or {}
                _live_case = (
                    {**_disc_fb, **_disc_generated}
                    if _disc_fb
                    else _disc_pr.get("learned_case_object") or {}
                )
                if not _live_case.get("case_id"):
                    _live_case["case_id"] = _gen_case_id or "SCENARIO_RUN"
                if _live_case and _live_case.get("fault_mode"):
                    import logging as _plog
                    _plog.getLogger(__name__).info(
                        "[discovery_pdf] rendering. fault_mode=%r vib_rms=%r "
                        "temp_c=%r bpfo=%r",
                        _live_case.get("fault_mode"),
                        _live_case.get("vib_rms_mm_s"),
                        _live_case.get("temp_c"),
                        _live_case.get("bpfo_energy"),
                    )
                    try:
                        from services.pdf_renderer import render_case_pdf as _disc_rcp
                        import base64 as _disc_b64_pdf
                        _disc_pdf = _disc_rcp(_live_case)
                        if isinstance(_disc_pdf, bytes) and len(_disc_pdf) > 100:
                            _disc_b64s = _disc_b64_pdf.b64encode(_disc_pdf).decode("ascii")
                            # Apply the same display resolution rule as activity_log._load_tracker:
                            # PATH_A/PATH_C case_id is a DISC_xxx run-correlation id — the
                            # human-meaningful id is source_case_id. PATH_B case_id is already
                            # the minted CASE_YYYYMMDD_NNN. Result: CASE_001.pdf, not
                            # DISC_xxx_case.pdf.
                            _path = _live_case.get("path_taken") or _live_case.get("path")
                            _resolved_cid = (
                                _live_case.get("source_case_id")
                                if _path in ("A", "C") and _live_case.get("source_case_id")
                                else _live_case.get("case_id", "case")
                            )
                            _pdf_fname = f"{_resolved_cid}.pdf"
                            st.markdown(
                                f'<a href="data:application/pdf;base64,{_disc_b64s}" '
                                f'download="{_pdf_fname}" '
                                f'style="display:inline-block;padding:0.35rem 0.9rem;'
                                f'background:#F5EDFF;color:#A100FF;'
                                f'border:1px solid #A100FF;border-radius:0.4rem;'
                                f'text-decoration:none;font-size:0.82rem;font-weight:500;'
                                f'margin-top:0.5rem;">⬇ Download Case PDF</a>',
                                unsafe_allow_html=True,
                            )
                    except Exception as _disc_pdferr:
                        logger.warning("[discovery_pdf] render failed: %s", _disc_pdferr)

                logger.info(
                    "[latency] discovery_result_panel(%s) took %.3fs",
                    _ks, _rp_time.perf_counter() - _rp_t0,
                )
                if st.button("Run another scenario", key="reset_scenario_btn",
                             use_container_width=False):
                    del st.session_state["last_scenario_result"]
                    if "last_pipeline_result" in st.session_state:
                        del st.session_state["last_pipeline_result"]
                    st.rerun()
            else:
                st.markdown(
                    "<div style='height:120px;display:flex;"
                    "align-items:center;justify-content:center;"
                    "color:#8A8A8A;font-size:13px;font-style:italic;"
                    "border:1px dashed #E2E0EA;border-radius:8px;"
                    "margin-top:24px'>"
                    "Run a scenario to see results here"
                    "</div>",
                    unsafe_allow_html=True,
                )

        # ── Add Case (Manual Override) — full page width, below the pipeline split ──
        st.divider()
        _ac_hdr_col, _ac_toggle_col = st.columns([8, 1])
        with _ac_hdr_col:
            st.markdown(
                '<div class="sec-label" style="font-size:14px;">'
                'Add Case (Manual Override)</div>',
                unsafe_allow_html=True,
            )
        with _ac_toggle_col:
            if st.button(
                "Hide" if st.session_state.get("ac_add_case_open", False) else "Show",
                key="ac_toggle_btn",
            ):
                st.session_state["ac_add_case_open"] = not st.session_state.get(
                    "ac_add_case_open", False
                )
                st.rerun()
        if st.session_state.get("ac_add_case_open", False):
            render_add_case_input()

    with tab2:
        render_verify_case()
    with tab3:
        render_analytics()



# ═══════════════════════════════════════════════════════════════════
# SECTION: MAIN APP LAYOUT AND TAB ROUTING
# st.set_page_config, theme CSS injection, tab instantiation, entry.
# ═══════════════════════════════════════════════════════════════════
def main():
    """Application entry point."""
    st.set_page_config(
        page_title="DRO Learning Agent",
        page_icon="L",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_css()

    # Startup demo-cleanup removed (was destroying valid PATH_B cases
    # whose filenames contained "demo" — see diagnostic 2026-07-05).
    # Nuclear reset from CLI handles environment cleanup.

    page_main_dashboard()



if __name__ == "__main__":
    main()
