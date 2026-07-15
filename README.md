# Agent 6.6 — Prescriptive Optimization Agent

> **Turns a diagnosed bearing fault into a ranked, auditable maintenance recommendation
> and hands it to the Executor Agent (6.7) — all in under a few seconds.**

The Prescriptive Optimization Agent occupies step 6 of the Downtime Response Orchestrator
(DRO). It receives three structured inputs from upstream agents — a fault diagnosis
(6.3), a risk assessment (6.4), and knowledge guidance (6.5) — and produces a
`MaintenanceRecommendation` that tells the Executor exactly what to do, who must approve
it, which parts are needed, and when to schedule the work.

**Core design principle:** The LLM interprets free-text inputs and generates human-readable
explanations, but a **deterministic, rules-based pipeline** makes every decision that
matters — which action to take, whether to escalate, whether parts are available, which
maintenance window to use. This makes every recommendation fully auditable, which is
non-negotiable for assets where unplanned downtime costs \$5,000–\$18,000 per hour.

---

## Table of Contents

1. [Where It Fits in the DRO](#1-where-it-fits-in-the-dro)
2. [Quick Start](#2-quick-start)
3. [How It Works](#3-how-it-works)
   - [Deterministic Pipeline](#31-deterministic-pipeline)
   - [LLM Call Sites](#32-llm-call-sites)
   - [Novel / Unknown-Fault Flow](#33-novel--unknown-fault-flow)
   - [Chatbot](#34-chatbot)
   - [RAG Document Search](#35-rag-document-search)
4. [Integration Contracts](#4-integration-contracts)
   - [Input Schemas](#41-input-schemas)
   - [Output Schema](#42-output-schema)
   - [Entry-Point Functions](#43-entry-point-functions)
   - [Hardcoded Assumptions](#44-hardcoded-assumptions)
5. [Project Structure](#5-project-structure)
6. [Configuration & Tuning](#6-configuration--tuning)
7. [Data Dependencies](#7-data-dependencies)
8. [Extending the Agent](#8-extending-the-agent)
9. [Safety & Fallback Behavior](#9-safety--fallback-behavior)
10. [Demo Scaffolding vs Production](#10-demo-scaffolding-vs-production)
11. [Known Gaps for Production](#11-known-gaps-for-production)
12. [Appendix: Full Dependency List](#12-appendix-full-dependency-list)

---

## 1. Where It Fits in the DRO

The DRO is an 8-agent pipeline. Agent 6.6 sits exactly in the middle: it receives
structured data from the three diagnostic agents and produces the actionable output
consumed by the Executor.

```
  ┌─────────────────────────────────────────────────────────┐
  │                    DRO Orchestrator                      │
  │         (coordination, procurement, cross-agent)         │
  └───────────────────────┬─────────────────────────────────┘
                          │
          ┌───────────────┼───────────────────────┐
          ▼               ▼                       ▼
  [6.1 Data Foundation]  [6.2 Monitoring]         │
  raw telemetry,         live sensors,            │
  ingestion, quality     anomaly detection        │
          │               │                       │
          └───────────────▼                       │
                  [6.3 Failure Intelligence] ──────┤
                  fault diagnosis,                 │
                  fault_mode, severity,            │
                  confidence                       │
                          │                        │
                  [6.4 Predictive Risk] ───────────┤
                  RUL, failure probability,        │
                  risk_level, downtime cost        │
                          │                        │
                  [6.5 Knowledge] ─────────────────┤
                  SOP steps, safety notes,         │
                  similar past cases               │
                          │
                          ▼
          ┌───────────────────────────────┐
          │   6.6  Prescriptive           │  ◄── THIS AGENT
          │   Optimization Agent          │
          │                               │
          │  • Validates inputs           │
          │  • Runs 4 ordered guards      │
          │  • Scores & ranks candidates  │
          │  • Assigns window + approver  │
          │  • LLM writes rationale       │
          └───────────────┬───────────────┘
                          │
                          ▼
                  [6.7 Executor Agent]
                  work orders, dispatch,
                  carries out the action
                          │
                          ▼
                  [6.8 Learning & Memory]
                  captures outcomes,
                  feeds back into SOPs
```

**Upstream producers → 6.6:**

| Schema | Produced by |
|--------|-------------|
| `FaultDiagnosis` | Agent 6.3 — Failure Intelligence Agent |
| `RiskAssessment` | Agent 6.4 — Predictive Risk Agent |
| `KnowledgeGuidance` | Agent 6.5 — Knowledge Agent |

**6.6 → Downstream consumer:**

| Schema | Consumed by |
|--------|-------------|
| `MaintenanceRecommendation` | Agent 6.7 — Executor Agent |

---

## 2. Quick Start

### 2.1 Python version

```
Python 3.14.5
```

No `.python-version` or `runtime.txt` file is present; the version is determined by
the `.venv` created at setup time.

### 2.2 Install

```bash
# Clone / unzip the project root
cd agent_6_6

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# Install dependencies
# requirements.txt is intentionally minimal (pydantic + pytest only).
# Install the full runtime set:
pip install streamlit==1.58.0 \
    langchain==1.3.9 langchain-openai==1.3.2 langgraph==1.2.5 \
    langgraph-prebuilt==1.1.0 openai==2.41.0 \
    faiss-cpu==1.14.3 sentence-transformers==5.6.0 \
    numpy==2.4.6 pandas==3.0.3 fpdf2==2.8.7 pypdf==6.13.3 \
    python-dotenv==1.2.2 pydantic==2.13.4 pytest==9.0.3
# For the full pinned set, see Appendix §12.
```

### 2.3 Environment variables

Create a `.env` file at the project root (already in `.gitignore` — never commit it):

```dotenv
# Azure OpenAI — used by ALL LLM calls in this project.
# The same deployment handles classification, rationale, novel-action proposals,
# and the chatbot reasoner.

AZURE_OPENAI_API_KEY=<your-azure-openai-api-key>
AZURE_OPENAI_ENDPOINT=https://<your-resource>.services.ai.azure.com
AZURE_OPENAI_DEPLOYMENT=<deployment-name>          # e.g. gpt-4o-mini
AZURE_OPENAI_API_VERSION=<api-version-string>      # e.g. 2024-08-01-preview
```

All four variables are read via `os.getenv()` throughout the codebase
(`agents/fault_classifier.py`, `agents/prescriptive_optimization_agent.py`,
`tools/rationale_writer.py`, `recommendation_chat.py`). Every LLM call fails gracefully to a template/fallback if any
variable is missing — the app will not hard-crash.

### 2.4 Build the RAG index

The chatbot's `search_documents_tool` (SOP / case-document lookup) requires a
pre-built FAISS index. Build it once, and rebuild whenever PDFs in
`data/sops_and_cases/` change:

```bash
# From the project root with the venv active:
python rag/build_index.py
```

**Artifacts produced:**

| File | Contents |
|------|----------|
| `rag/faiss_index.bin` | FAISS flat-IP index (cosine similarity on normalised vectors) |
| `rag/chunks.json` | Chunk metadata: source file, page number, raw text |

**If the index is absent:** `search_documents_tool` catches `FileNotFoundError` and
returns `"Document index not available."` — no crash, and all other pipeline
functionality continues normally.

The first build also downloads the embedding model (`all-MiniLM-L6-v2`, ~90 MB) into
the HuggingFace cache. Subsequent runs are fast.

### 2.5 Run the app

**Windows (recommended):**

```bat
run.bat
```

Contents of `run.bat`:

```bat
@echo off
echo Stopping any old Streamlit processes...
taskkill /F /IM streamlit.exe >nul 2>&1
npx --yes kill-port 8501 >nul 2>&1
echo Activating virtual environment...
call .venv\Scripts\activate
echo Starting app.py on port 8501...
streamlit run app.py --server.port 8501
```

**Direct (any OS, venv active):**

```bash
streamlit run app.py --server.port 8501
```

Default URL: **http://localhost:8501**

---

## 3. How It Works

### 3.1 Deterministic Pipeline

`recommend_action()` in `agents/prescriptive_optimization_agent.py` is the main
entry point. It runs in a strict, ordered sequence — **no LLM decides which action
to take on the normal path**:

```
recommend_action(diagnosis, risk, guidance)
        │
        ├─ 1. validate_inputs()          # field presence, value ranges, cross-object
        │      consistency, master-data cross-checks
        │      → FAIL: status=blocked_invalid_input
        │
        ├─ 2. asset in master data?      # _get_asset_type(diagnosis.asset_id)
        │      → NOT FOUND: status=blocked_unknown_asset
        │
        ├─ 3. diagnosis.confidence ≥ 0.5?
        │      → BELOW THRESHOLD: status=unreliable_diagnosis
        │
        ├─ 4. generate_candidates()      # catalog lookup: fault_mode × severity × asset_type
        │      → each candidate carries part_type from the catalog entry
        │      → NO MATCH (catalog miss): LLM proposes tentative action
        │                                 status=novel_llm_suggestion (escalated)
        │
        ├─ 5. part available in time?    # check_part_for_action() vs rul_min_days
        │      # part type read from candidate's part_type (catalog-driven), not name keywords
        │      → BLOCKED: status=blocked_no_part (escalated)
        │
        └─ 6. NORMAL PATH:
               score_candidate()        # urgency_score × feasibility_score = final_score
               rank_actions()           # sort by final_score (multiply, not add, so
                                        # zero feasibility → zero final_score always)
               _resolve_timing()        # in_window / now / monitor
               assemble_recommendation()
               write_rationale()        # LLM, falls back to template
```

**Scoring formula** (`tools/action_ranker.py`):

```
urgency_score    = urgency_base × appropriateness_multiplier
                   urgency_base = 1 - (rul_min_days / stage_1_rul_days)
                   appropriateness = 1.0 for SOP-backed actions, 0.3 for fallback

feasibility_score = parts_factor × window_factor
                    parts_factor : 1.0 (in stock), 0.0–1.0 (lead time vs RUL), 0.0 (no part)
                    window_factor: 1.0 (window fits), 0.0 (no window in RUL horizon)

final_score = urgency_score × feasibility_score
```

Multiplying (not adding) ensures a zero-feasibility action always scores 0,
regardless of urgency. The agent will never recommend something it cannot execute.

### 3.2 LLM Call Sites

Every LLM call is wrapped in try/except and has a deterministic fallback.
The same Azure deployment handles all six:

| Function | File | `max_tokens` | `temperature` | Purpose | Fallback |
|----------|------|-------------|--------------|---------|----------|
| `guess_unknown_fault()` | `agents/fault_classifier.py:110` | 200 | 0.2 | For "unknown" classifications, name the fault in plain language | Returns `"Unrecognized fault"` |
| `_llm_propose_novel_action()` | `agents/prescriptive_optimization_agent.py` | 400 | 0.2 | Propose a tentative action for a catalog-miss (novel) fault | Returns `inspect_and_monitor` fallback dict |
| `_llm_free_recommendation()` | `agents/prescriptive_optimization_agent.py` | 400 | 0.2 | Free-form action for a confirmed-unknown fault (outside 6 standard modes) | Returns `inspect_and_monitor` fallback dict |
| `_llm_rationale()` | `tools/rationale_writer.py` | 600 | 0.2 | Write the plain-English rationale for any recommendation | Template-based rationale |
| Chat reasoner | `recommendation_chat.py:48` | 600 | 0.2 | Answer user questions; decide which tools to call | Returns transient-error message |

All LLM calls above use `temperature=0.2`. (Free-text fault classification is now
deterministic — see §3.1 — and no longer an LLM call.)

### 3.3 Novel / Unknown-Fault Flow

There are two distinct "novel fault" paths, both producing `is_llm_suggested=True`
and `recommendation_status="novel_llm_suggestion"`:

**Type 1 — Catalog miss** (fault mode is recognised, but no SOP exists for this
fault/severity/asset combination):

```
classify_fault() → fault_mode="inner_race_fault", severity="stage_3"
generate_candidates() → no catalog entry found
↓
_make_novel_suggestion_rec()
  → _llm_propose_novel_action() [LLM, 400 tokens, JSON output]
  → action constrained to _NOVEL_ALLOWED_ACTIONS whitelist
  → status=novel_llm_suggestion, approval_status=escalated
  → labelled [TENTATIVE AI SUGGESTION - NOVEL SCENARIO]
```

**Type 2 — Unrecognised mode** (fault doesn't match any of the 6 standard modes):

```
User enters free text → classify_fault() → fault_mode="unknown"
  ↓
guess_unknown_fault() [LLM, 200 tokens] → "likely_fault": "Gear-mesh tooth wear"
  ↓
User shown the LLM's interpretation and asked to confirm
  ↓
(confirmed) → make_free_recommendation() [LLM, 400 tokens, NO action whitelist]
  → status=novel_llm_suggestion, is_llm_suggested=True
  → labelled [TENTATIVE AI SUGGESTION - UNRECOGNIZED FAULT]
```

Both paths force escalation to Plant Manager (the "escalated" approver tier in
`personas.json`). The chatbot automatically relaxes its source-grounding rule for
novel recommendations and may draw on general engineering knowledge, but must label
any such answer clearly.

### 3.4 Chatbot

The grounded chatbot (`recommendation_chat.py`) allows users to ask follow-up
questions about a produced recommendation. It uses a two-node LangGraph loop:

```
START → [reasoner] ──────────────────────────────────────► END
              │                                              ▲
              │  tool_calls present?                         │
              └──────────► [tools node] ────────────────────┘
                           (executes tool,
                            loops back to reasoner)
```

**Recursion limit:** 8 steps (≈ 4 tool-call cycles), passed at invoke time:

```python
chat_graph.invoke({"messages": ...}, config={"recursion_limit": 8})
```

**Five tools available to the reasoner:**

| Tool | Purpose |
|------|---------|
| `check_inventory_tool` | Live part availability for a bearing/action pair |
| `find_windows_tool` | Upcoming maintenance windows for an asset |
| `get_asset_info_tool` | Asset type, criticality, downtime cost |
| `search_documents_tool` | FAISS semantic search over SOPs and learned cases |
| `get_decision_history_tool` | Past recommendations from the decision log |

**Conversation history:** last `MAX_HISTORY = 10` turns are included in each call.

**Source attribution:** every chat answer carries a source chip derived from the tool
trace — "Action catalog, per SOP_001", "AI-generated suggestion (not from approved
catalog)", etc. Refusal answers (no tool called + refusal language detected) suppress
the chip entirely.

**Follow-up suggestions:** the system prompt mandates a `<follow_ups>` block in every
response; the `_extract_follow_ups()` parser strips it from the displayed answer and
returns up to 3 suggested questions.

**Agent routing:** the system prompt (`_PARTIAL_QUESTIONS_RULE`) teaches the LLM to
name sibling agents by descriptive name when a question is out of scope (e.g.,
"live vibration readings are handled by the Monitoring Agent"). It never deflects
with "I cannot help."

**Safety boundary:** the chatbot can explain and elaborate on the existing
recommendation but will never re-run the pipeline or modify any state.

### 3.5 RAG Document Search

The RAG pipeline is used **only inside `search_documents_tool`** — it is not involved
in classification, scoring, or any pipeline decision.

```
PDF files in data/sops_and_cases/
        │
        ▼ (build time — python rag/build_index.py)
   pypdf extracts text → chunked at 800 chars, 150-char overlap
        │
        ▼
   all-MiniLM-L6-v2 (local, sentence-transformers) embeds each chunk
        │
        ▼
   FAISS IndexFlatIP (cosine similarity via normalised inner product)
   saved to: rag/faiss_index.bin + rag/chunks.json
        │
        ▼ (query time — search_documents_tool)
   query string → embedded by same model (cached module-level)
   FAISS returns top-6 chunks by cosine score
   chunks returned as {source_file, doc_type, page_number, text, score}
```

The embedding model is loaded once at module import time
(`rag/document_store.py:34`) and reused for every query — no repeated loads.
The FAISS index is lazy-loaded on first query and cached in module-level globals
(`_index_cache`, `_chunks_cache`) for the lifetime of the process.

The fault classifier (`classify_fault`) uses **prompt-based reasoning**, not
embeddings. The LLM reads the fault description and reasons about it directly.

---

## 4. Integration Contracts

### 4.1 Input Schemas

All schemas use Pydantic v2 (`pydantic==2.13.4`). Import from `schemas/`.

---

#### `FaultDiagnosis` — from Agent 6.3 (Failure Intelligence Agent)
`schemas/diagnosis.py`

| Field | Type | Notes |
|-------|------|-------|
| `case_id` | `str` | Ties inputs to a single case; cross-checked vs `risk.case_id` |
| `asset_id` | `str` | **Required.** Must exist in `asset_master.json` or pipeline blocks |
| `bearing_id` | `str` | Must belong to `asset_id` in `bearing_master.json`; missing = warning |
| `fault_code` | `str` | **Required.** Free-form code (e.g. `FT_001`) |
| `fault_mode` | `Literal[...]` | **Required.** One of: `outer_race_fault`, `inner_race_fault`, `lubrication_issue`, `imbalance`, `misalignment`, `cage_fault`, `unknown` |
| `affected_component` | `str` | **Required.** (e.g. `bearing_outer_race`) |
| `severity` | `Literal[...]` | **Required.** One of: `monitor`, `stage_1`, `stage_2`, `stage_3` |
| `confidence` | `float` (0.0–1.0) | **< 0.50 blocks pipeline** (unreliable_diagnosis). Missing → safe default 0.60 |
| `evidence` | `list[str]` | Default `[]`. Signals/observations supporting the diagnosis |
| `likely_causes` | `list[str]` | Default `[]` |
| `recommended_checks` | `list[str]` | Default `[]` |
| `diagnosed_at_utc` | `datetime` | UTC timestamp |

**Critical integration notes:**
- `confidence < 0.50` → `recommendation_status="unreliable_diagnosis"`, `approval_status="escalated"`, pipeline does not proceed.
- `fault_mode="unknown"` triggers the novel flow (see §3.3). Only relevant via the demo UI; in programmatic integration, upstream agents should send a recognised mode whenever possible.
- `asset_id` not in `asset_master.json` → `status="blocked_unknown_asset"`, escalated.

---

#### `RiskAssessment` — from Agent 6.4 (Predictive Risk Agent)
`schemas/risk.py`

| Field | Type | Notes |
|-------|------|-------|
| `case_id` | `str` | Cross-checked vs `diagnosis.case_id` (mismatch = warning, not block) |
| `asset_id` | `str` | **Required.** Cross-checked vs `diagnosis.asset_id` (mismatch = error) |
| `bearing_id` | `str` | Cross-checked vs `diagnosis.bearing_id` |
| `failure_probability` | `float` (0.0–1.0) | Missing → safe default 0.50 |
| `risk_level` | `Literal["low","medium","high","critical"]` | Missing → safe default `"medium"` |
| `rul_min_days` | `int` (≥ 0) | Worst-case remaining useful life. Missing → default 14 |
| `rul_max_days` | `int` (≥ `rul_min_days`) | Best-case RUL. Missing → `rul_min + 7` |
| `confidence` | `float` (0.0–1.0) | Risk model confidence |
| `business_impact_flag` | `bool` | True = bottleneck / high-cost asset → forces escalated approver |
| `estimated_downtime_cost_per_hour` | `float` (≥ 0) | Used in rationale text and chatbot context |
| `assessed_at_utc` | `datetime` | UTC timestamp |

Pydantic enforces `rul_max_days >= rul_min_days` with a `@model_validator`.

**Safe defaults** are applied by `apply_safe_defaults()` before the pipeline runs.
Defaulted fields are surfaced to the user via the chatbot's partial-data notice.

---

#### `KnowledgeGuidance` — from Agent 6.5 (Knowledge Agent)
`schemas/knowledge.py`

| Field | Type | Notes |
|-------|------|-------|
| `case_id` | `str` | Ties guidance to the case |
| `source_documents` | `list[str]` | Default `[]` |
| `relevant_sections` | `list[str]` | Default `[]` |
| `inspection_steps` | `list[str]` | Default `[]` |
| `safety_notes` | `list[str]` | Default `[]` |

**Note:** Agent 6.6 consumes `KnowledgeGuidance` on every recommendation path. Its
`source_documents` and `relevant_sections` are cited in the recommendation's
`evidence` (under the `guidance` key), and its `inspection_steps` and
`safety_notes` are appended to the human-readable `rationale`. Guidance does not
change which action is selected or how it is ranked — those decisions remain
driven solely by the diagnosis and risk — it enriches the explanation and audit
trail. Empty guidance is valid and handled gracefully: an empty
`KnowledgeGuidance(case_id=...)` produces a complete recommendation whose evidence
notes "no SOP guidance retrieved for this case."

---

### 4.2 Output Schema

#### `MaintenanceRecommendation` — consumed by Agent 6.7 (Executor Agent)
`schemas/recommendation.py`

**Nested types:**

```python
class Action(BaseModel):
    name:                     str    # e.g. "replace_bearing", "lubrication_service"
    description:              str    # human-readable, includes timing and SOP ref
    estimated_duration_hours: float  # >= 0

class RequiredPart(BaseModel):
    part_number:    str   # model string from inventory (e.g. "SKF6208-2RS")
    quantity:       int   # >= 1
    lead_time_days: int   # 0 = in stock; > 0 = must be ordered
```

**Full field table:**

| Field | Type | Notes |
|-------|------|-------|
| `case_id` | `str` | Echoed from input |
| `asset_id` | `str` | Echoed from input |
| `bearing_id` | `str` | Echoed from input |
| `recommended_action` | `Action` | The winning action |
| `urgency` | `Literal["monitor","planned","urgent","emergency"]` | See urgency rules below |
| `ranked_alternatives` | `list[Action]` | Empty for blocked/novel cases |
| `required_parts` | `list[RequiredPart]` | Empty if action needs no tracked part |
| `window_chosen` | `str \| None` | Window ID from `operations_context.json`; `None` for `"urgent"`, `"emergency"`, or `"monitor"` |
| `rationale` | `str` | LLM-written or template-generated plain-English rationale |
| `evidence` | `dict[str, str]` | Keys: `fault`, `risk`, `recommended_action`, `part`, `window`, `responsible_person`, `urgency`, `guidance` |
| `recommendation_status` | `Literal[...]` | See enum table below |
| `is_llm_suggested` | `bool` | `True` only for `novel_llm_suggestion` |
| `approval_status` | `Literal[...]` | See enum table below |
| `responsible_person` | `str` | Same as `responsible_approver` (kept for backward compat) |
| `responsible_person_id` | `str \| None` | Persona ID |
| `responsible_approver` | `str` | Format: `"Role — Full Name"` (e.g. `"Plant Manager — Sarah Chen"`) |
| `responsible_approver_id` | `str` | Persona ID from `personas.json` |
| `contributors` | `list[dict]` | Each: `{"role": str, "name": str, "concern": str}`. Empty for clean routine cases |
| `generated_at_utc` | `datetime` | UTC timestamp of recommendation creation |

**`recommendation_status` enum:**

| Value | Meaning | `approval_status` | Action for 6.7 |
|-------|---------|-------------------|----------------|
| `"ok"` | Normal, rule-backed recommendation | `"pending"` | Execute after approval |
| `"novel_llm_suggestion"` | AI-proposed; novel fault (catalog miss or unrecognised mode) | `"escalated"` | **Human validation required before any work** |
| `"blocked_no_part"` | Right action, but part unavailable within RUL | `"escalated"` | Expedite procurement; apply interim protocol |
| `"blocked_unknown_asset"` | Asset not in master data | `"escalated"` | Verify asset ID; do not execute |
| `"unreliable_diagnosis"` | `confidence < 0.50` | `"escalated"` | Physical inspection required first |
| `"catalog_miss"` | No SOP for this fault/severity (reserved edge case) | `"escalated"` | Engineering review needed |
| `"blocked_invalid_input"` | Pre-flight validation failed | `"escalated"` | Fix upstream data; do not execute |

**`approval_status` enum:**

| Value | Meaning |
|-------|---------|
| `"pending"` | Valid recommendation awaiting routine human sign-off |
| `"escalated"` | Something is wrong — a human must actively investigate before any action |
| `"approved"` | Set later by the approver (UI button; not written back to any external system) |
| `"rejected"` | Set later by the approver |

**Urgency rules:**

| `urgency` | Condition |
|-----------|-----------|
| `"monitor"` | `timing="monitor"` or `severity="monitor"` |
| `"planned"` | `timing="in_window"` |
| `"urgent"` | `timing="now"` and action is not an escalation |
| `"emergency"` | `timing="now"` and action is an escalation action |

**Invariants Agent 6.7 must respect:**
1. `approval_status="escalated"` → **do not auto-execute**. Escalated cases require active human intervention.
2. `is_llm_suggested=True` → tentative AI proposal for a novel fault. Must be human-validated before physical work begins.
3. `window_chosen` is a `window_id` string when `urgency="planned"`, and `None` otherwise.
4. `recommended_action.name` is `"manual_review"` or `"manual_inspection"` for all blocked statuses — these are placeholder names, not real procedure names.
5. `ranked_alternatives` is always empty for blocked and novel cases.

---

### 4.3 Entry-Point Functions

#### Primary pipeline — `recommend_action()`

```python
# agents/prescriptive_optimization_agent.py

def recommend_action(
    diagnosis: FaultDiagnosis,
    risk: RiskAssessment,
    guidance: KnowledgeGuidance,
) -> MaintenanceRecommendation:
```

**Recommended call pattern** (as used in `app.py`):

```python
from schemas.diagnosis import FaultDiagnosis
from schemas.risk import RiskAssessment
from schemas.knowledge import KnowledgeGuidance
from agents.uncertainty_detector import apply_safe_defaults
from agents.prescriptive_optimization_agent import recommend_action
from agents.decision_logger import log_recommendation

# 1. Apply safe defaults for any missing optional fields
diag, risk, defaulted_fields = apply_safe_defaults(diagnosis, risk)

# 2. Run the pipeline
rec = recommend_action(diag, risk, guidance)

# 3. Log the recommendation
log_id = log_recommendation(rec, diag, risk)

# 4. Check for escalation
if rec.approval_status == "escalated":
    # route to human for review — do not auto-execute
    ...
```

---

#### Novel / confirmed-unknown-fault path — `make_free_recommendation()`

```python
# agents/prescriptive_optimization_agent.py

def make_free_recommendation(
    diagnosis,          # FaultDiagnosis
    risk,               # RiskAssessment
    guidance,           # KnowledgeGuidance
    likely_fault: str,  # human-confirmed fault description (plain language)
) -> MaintenanceRecommendation:
```

Always returns `recommendation_status="novel_llm_suggestion"` and
`is_llm_suggested=True`. The LLM is not constrained to an action whitelist here
(unlike `_llm_propose_novel_action` which is constrained to `_NOVEL_ALLOWED_ACTIONS`).

---

#### Chatbot — `answer_about_recommendation()`

```python
# recommendation_chat.py

def answer_about_recommendation(
    question: str,
    rec=None,               # MaintenanceRecommendation or None
    diagnosis=None,         # FaultDiagnosis (for context)
    risk=None,              # RiskAssessment (for context)
    history: list = None,   # [{"role": "user"|"assistant", "content": str}, ...]
    defaulted_fields=None,  # list[str] of fields that were defaulted upstream
) -> dict:
    # Returns:
    # {
    #     "answer":     str,         # clean answer (follow_ups block stripped)
    #     "trace":      list[dict],  # [{"tool": str, "args": dict, "result": str}, ...]
    #     "follow_ups": list[str],   # up to 3 suggested follow-up questions
    # }
```

When `rec=None`: general assistant mode — answers questions about assets,
inventory, windows, and SOPs without a specific case loaded.

---

### 4.4 Hardcoded Assumptions

**Sibling-agent names in the chatbot routing rule**
(`recommendation_chat.py`, `_PARTIAL_QUESTIONS_RULE`):

The chat system prompt contains the following hardcoded agent names. If the DRO
renames any agent, update the string in `_PARTIAL_QUESTIONS_RULE`:

```
"the Data Foundation Agent"
"the Monitoring Agent"
"the Failure Intelligence Agent"    # = Agent 6.3
"the Predictive Risk Agent"         # = Agent 6.4
"the Knowledge Agent"               # = Agent 6.5
"the Executor Agent"                # = Agent 6.7
"the Learning & Memory Agent"
"the DRO Orchestrator"
```

**Persona names and format** (`data/personas.json`):

Approver names are stored in `personas.json` and flow into
`MaintenanceRecommendation.responsible_approver` as `"Role — Full Name"` strings
(e.g. `"Plant Manager — Sarah Chen"`). If Agent 6.7 dispatches notifications
using this field, it must handle this exact format.

**Decision log path** (`agents/decision_logger.py`):

```python
LOG_PATH = Path("data/decision_log.json")
```

This is relative to the process working directory (the project root when launched
via `run.bat`). In a containerised or multi-directory deployment, this path must
be updated or replaced with an absolute path / environment variable.

**Window anchor date** (`tools/data_loader.py:21`):

```python
_WINDOW_ANCHOR = datetime(2026, 5, 20, tzinfo=timezone.utc)
```

`load_operations_context()` shifts all window timestamps forward from this anchor
to today's date, keeping windows in the near future. This is a demo convenience;
production data should use real future-dated windows and this shifting logic should
be removed.

**Action part types are catalog-driven** (`data/action_catalog.json`):

Each catalog action declares its `part_type` (`"bearing"`, `"lubricant"`, or
`null`). The pipeline reads this field via `_get_action_type()` to look up spare
parts — it no longer infers the part type from action-name keywords. Keyword
inference survives only as a fallback for novel LLM-proposed actions, which have
no catalog entry. To add a new action type, set `part_type` on the catalog entry
(and, if it's a new category, add it to `ACTION_TO_PART_TYPE` in
`tools/inventory_checker.py`) — no other code changes needed.

---

### 4.5 Wiring Points — where input enters and output leaves

This subsection describes the concrete plumbing for integrators. For the complete
field-level specification, sample JSON for every input and the output, and
per-status guidance for the executor, see **`Agent_6_6_Interface_Contract.md`**.

**Single entry point.** The whole agent is invoked through one function:

    from agents.prescriptive_optimization_agent import recommend_action
    recommendation = recommend_action(diagnosis, risk, guidance)

It takes the three input objects and returns exactly one `MaintenanceRecommendation`.
It never raises for known failure cases — unknown assets, unreliable diagnoses,
missing parts, invalid input, and novel faults all return a structured
recommendation with the appropriate `recommendation_status` (see §4.2).

**Where input enters (in `app.py`).** Inputs are assembled into a
`(diagnosis, risk, guidance)` tuple from one of two sources — the pre-built demo
scenarios in `scenarios.py`, or the manual sidebar form — then passed through
`apply_safe_defaults(diagnosis, risk)` (from `agents/uncertainty_detector`, which
fills missing *optional* fields on copies without mutating the originals), and
finally handed to `recommend_action(*inputs)`. To wire a real upstream feed in,
construct the three objects (or deserialize them — see below) and call
`recommend_action` at this point; nothing else in the display layer needs to change.

**Where output goes.** The returned `MaintenanceRecommendation` is logged via
`log_recommendation(...)` (append-only audit trail), stored for display, rendered
in the UI, and is the object handed downstream to Agent 6.7 (Executor).

**Two integration mechanisms.** Because the inputs and output are Pydantic v2
models, integration works either way:

- *In-process:* upstream agents build the `FaultDiagnosis` / `RiskAssessment` /
  `KnowledgeGuidance` objects directly and pass them to `recommend_action`.
- *Across a JSON boundary (network / queue / files):* deserialize inbound JSON with
  `FaultDiagnosis.model_validate(json_dict)` (etc.), and serialize the result with
  `recommendation.model_dump(mode="json")` or `.model_dump_json()`. Validation
  (types, ranges, and the `rul_max >= rul_min` rule) is enforced at
  `model_validate`, so malformed upstream JSON is rejected at the boundary with a
  clear error.

---

## 5. Project Structure

```
agent_6_6/
│
├── app.py                       [DEMO + PRODUCTION] Streamlit UI; orchestrates
│                                the full demo flow and the chatbot surface.
│                                Contains both demo-only scaffolding and calls to
│                                production-ready entry functions.
│
├── recommendation_chat.py       [PRODUCTION] Grounded chatbot API.
│                                Exposes answer_about_recommendation().
│                                LangGraph 2-node graph + system prompts + tools.
│
├── scenarios.py                 [DEMO ONLY] 11 hardcoded demo scenarios.
│                                SCENARIO_MAP: label → {diagnosis, risk, guidance}.
│                                Simulates upstream agents 6.3/6.4/6.5 for the demo.
│
├── run.bat                      Windows launcher script.
├── requirements.txt             Minimal (pydantic + pytest only; see Appendix §12).
├── .env                         Azure OpenAI credentials. NOT committed to git.
├── .gitignore
│
├── agents/
│   ├── prescriptive_optimization_agent.py
│   │                            [PRODUCTION] Core pipeline: recommend_action(),
│   │                            make_free_recommendation(), _llm_propose_novel_action(),
│   │                            candidate generation, scoring, window resolution,
│   │                            approver/contributor resolution.
│   │
│   ├── fault_classifier.py      [PRODUCTION in programmatic use; DEMO ONLY in UI]
│   │                            classify_fault(), guess_unknown_fault().
│   │                            LLM classifies free text; used in Add-new-scenario
│   │                            UI path and can be called programmatically.
│   │
│   ├── input_validator.py       [PRODUCTION] validate_inputs(): field presence,
│   │                            value ranges, cross-object consistency, master-data
│   │                            cross-checks. Returns ValidationResult dataclass.
│   │
│   ├── uncertainty_detector.py  [PRODUCTION] apply_safe_defaults(): fills missing
│   │                            optional fields before the pipeline runs.
│   │                            detect_uncertainty(): flags partial inputs.
│   │
│   └── decision_logger.py       [PRODUCTION] log_recommendation(), get_history().
│                                Append-only JSON log at data/decision_log.json.
│
├── schemas/
│   ├── diagnosis.py             FaultDiagnosis Pydantic model (input from 6.3).
│   ├── risk.py                  RiskAssessment Pydantic model (input from 6.4).
│   ├── knowledge.py             KnowledgeGuidance Pydantic model (input from 6.5).
│   └── recommendation.py        MaintenanceRecommendation + Action + RequiredPart
│                                (output to 6.7).
│
├── tools/
│   ├── data_loader.py           [PRODUCTION] JSON file loaders; @st.cache_data
│   │                            (ttl=300s) on all 7 parameterless loaders.
│   │                            load_scenario() is NOT cached (varying argument).
│   │
│   ├── inventory_checker.py     [PRODUCTION] check_part_for_action(bearing_id,
│   │                            action_type) → availability dict.
│   │                            Maps action_type → part_type via ACTION_TO_PART_TYPE.
│   │
│   ├── schedule_reader.py       [PRODUCTION] find_windows(asset_id, duration_hours,
│   │                            within_days) → list of fitting window dicts.
│   │
│   ├── action_ranker.py         [PRODUCTION] rank_actions(candidates) → sorted list.
│   │                            Computes final_score = urgency × feasibility.
│   │
│   ├── rationale_writer.py      [PRODUCTION] write_rationale(decision, use_llm=True).
│   │                            LLM path (600 tokens) with full template fallback.
│   │
│   └── langgraph_tools.py       [PRODUCTION] @tool wrappers used by the chat graph:
│                                check_inventory_tool, find_windows_tool,
│                                get_asset_info_tool, search_documents_tool,
│                                get_decision_history_tool, render_recommendation.
│
├── rag/
│   ├── config.py                Single source of truth for all RAG settings.
│   ├── build_index.py           CLI: python rag/build_index.py
│   ├── document_store.py        build_index(), search_documents().
│   │                            Embedding model loaded once at module level (line 34).
│   │                            FAISS index lazy-loaded + module-level cached.
│   ├── faiss_index.bin          [BUILD ARTIFACT] Generated by build_index.py.
│   └── chunks.json              [BUILD ARTIFACT] Chunk metadata.
│
├── data/
│   ├── asset_master.json        Asset catalog.
│   ├── bearing_master.json      Bearing catalog.
│   ├── fault_taxonomy.json      Fault mode definitions + RUL stage thresholds.
│   ├── inventory.json           Spare parts + compatibility + lead times.
│   ├── operations_context.json  Maintenance windows (timestamps shifted at runtime).
│   ├── action_catalog.json      SOP-derived action entries (the decision rules).
│   ├── personas.json            Approver hierarchy + contributor roles.
│   ├── telemetry_scenarios.json [DEMO ONLY] Raw telemetry for demo scenarios.
│   ├── decision_log.json        [PRODUCTION] Append-only recommendation history.
│   ├── DRO_SyntheticData_Extended.txt  Source material; not read by code.
│   └── sops_and_cases/          12 PDFs ingested by the RAG pipeline.
│
└── tests/                       pytest test modules.
```

---

## 6. Configuration & Tuning

All values below are read directly from the source code. To change any of them,
edit the file shown and restart the app (and rebuild the RAG index if the embedding
model or chunk settings change).

| Constant | Value | File | Notes |
|----------|-------|------|-------|
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` | `rag/config.py:8` | Change here + in `_embed()` in `document_store.py`; must rebuild index |
| `CHUNK_SIZE` | `800` chars | `rag/config.py:10` | Rebuild index after changing |
| `CHUNK_OVERLAP` | `150` chars | `rag/config.py:11` | Rebuild index after changing |
| `TOP_K` | `6` | `rag/config.py:13` | Number of chunks returned per search |
| `PDF_DIR` | `data/sops_and_cases` | `rag/config.py:15` | Directory scanned by `build_index.py` |
| `INDEX_PATH` | `rag/faiss_index.bin` | `rag/config.py:16` | |
| `CHUNKS_PATH` | `rag/chunks.json` | `rag/config.py:17` | |
| `max_tokens` — `classify_fault` | `150` | `agents/fault_classifier.py:32` | |
| `max_tokens` — `guess_unknown_fault` | `200` | `agents/fault_classifier.py:110` | |
| `max_tokens` — `_llm_propose_novel_action` | `400` | `agents/prescriptive_optimization_agent.py:679` | |
| `max_tokens` — `_llm_free_recommendation` | `400` | `agents/prescriptive_optimization_agent.py:846` | |
| `max_tokens` — `write_rationale` | `600` | `tools/rationale_writer.py` | |
| `max_tokens` — chat reasoner | `600` | `recommendation_chat.py:48` | |
| `temperature` — `classify_fault` | `0.0` | `agents/fault_classifier.py:30` | Maximum determinism |
| `temperature` — all other calls | `0.2` | All other LLM call sites | |
| `recursion_limit` — chat graph | `8` | `recommendation_chat.py` (invoke) | Passed at invoke time; ≈ 4 tool-call cycles |
| `MAX_HISTORY` — chat turns | `10` | `recommendation_chat.py:55` | Prior turns included in each call |
| Confidence block threshold | `0.50` | `agents/prescriptive_optimization_agent.py` | `confidence < 0.50` → unreliable_diagnosis |
| Confidence warn threshold | `0.70` | `agents/uncertainty_detector.py` | Low-confidence flag surfaced in chatbot |
| Wide RUL ratio trigger | `2×` | `agents/uncertainty_detector.py` | `rul_max > 2 × rul_min` → wide-RUL flag |
| Data-loader cache TTL | `300` s | `tools/data_loader.py` | `@st.cache_data(ttl=300)` on 7 loaders |
| Window anchor date | `2026-05-20` | `tools/data_loader.py:21` | Shift anchor for date-adjusted demo windows |

### 6.1 config.json — runtime-tunable thresholds

The tunable thresholds above are read at startup from `config.json` in the project
root, via `app_config.get_threshold(key, default)`. Editing `config.json` and
restarting the app changes these values without touching code:

    {
      "thresholds": {
        "min_reliable_confidence": 0.5,
        "contributor_review_confidence": 0.6,
        "monitor_appropriateness": 0.3,
        "default_duration_hours": 2.0,
        "semantic_classifier_threshold": 0.45
      }
    }

Loading is fail-safe: if `config.json` is missing, unreadable, or a key is absent,
the code falls back to the built-in default (the same values shown), so the app
always runs. Secrets are NOT stored here — Azure and Langfuse credentials remain in
`.env`. Retrieval settings (embedding model, chunk size, paths) remain in
`rag/config.py`.

---

## 7. Data Dependencies

### 7.1 Files in `data/`

The startup health check (`app.py`, top-of-file) warns if any **Required** file is
missing; the app continues but recommendations may be incomplete or blocked.

| File | Contents | Required? |
|------|----------|-----------|
| `asset_master.json` | Asset IDs, types, criticality, bottleneck flag, downtime cost | **Required** |
| `inventory.json` | Spare parts: part_id, model, compatible_bearing_ids, qty, lead_time | **Required** |
| `personas.json` | Approver hierarchy (normal/escalated/executive) and contributor roles | **Required** |
| `action_catalog.json` | SOP-derived rules: fault_mode × severity × asset_type → action + escalation | **Required** |
| `fault_taxonomy.json` | Fault mode definitions with `rul_days_stage_1` for urgency scoring | **Required** |
| `operations_context.json` | Maintenance windows; timestamps auto-shifted to stay in the future | **Required** |
| `bearing_master.json` | Bearing IDs, their asset, type, and specs | Optional (input_validator cross-checks; warnings only if absent) |
| `telemetry_scenarios.json` | Raw synthetic telemetry rows for the 11 demo scenarios | Optional (demo UI only) |
| `decision_log.json` | Append-only recommendation history; created on first write | Optional (chat history tool returns empty if absent) |
| `DRO_SyntheticData_Extended.txt` | Source material for the JSON files | Not read by any code |

### 7.2 SOP and Case PDFs (`data/sops_and_cases/`)

12 PDFs indexed by `python rag/build_index.py`. Required only for
`search_documents_tool`; all other pipeline functionality is unaffected if absent.

| File | Doc type |
|------|----------|
| `SOP_001_outer_race_motor_stage3.pdf` | SOP |
| `SOP_002_preventive_motor_healthy.pdf` | SOP |
| `SOP_003_lubrication_pump_stage2.pdf` | SOP |
| `SOP_004_sensor_replacement_conveyor.pdf` | SOP |
| `SOP_005_outer_race_gearbox_stage3.pdf` | SOP |
| `SOP_006_post_repair_qa_all_assets.pdf` | SOP |
| `CASE_001_learned_case.pdf` | Learned case |
| `CASE_002_learned_case.pdf` | Learned case |
| `CASE_003_learned_case.pdf` | Learned case |
| `CASE_004_learned_case.pdf` | Learned case |
| `CASE_005_learned_case.pdf` | Learned case |
| `CASE_006_learned_case.pdf` | Learned case |

### 7.3 External services

| Service | Purpose | Required? |
|---------|---------|-----------|
| Azure OpenAI | All LLM calls (classification, rationale, novel actions, chatbot) | **Required** for LLM features; each call has a deterministic fallback |
| HuggingFace Hub | First-time download of `all-MiniLM-L6-v2` (~90 MB) | Once only; subsequent runs are fully local |

No database, message bus, authentication service, or other external dependency.

---

## 8. Extending the Agent

### 8.1 Add a new fault mode

1. **`schemas/diagnosis.py`** — add the new mode to the `fault_mode` `Literal[...]`.
2. **`agents/fault_classifier.py`** — add it to `VALID_FAULT_MODES` (the list and
   the classifier prompt).
3. **`agents/input_validator.py`** — add it to `VALID_FAULT_MODES` set.
4. **`data/fault_taxonomy.json`** — add an entry with `fault_mode`, `rul_days_stage_1`,
   and any other stage thresholds used by `score_candidate()`.
5. **`data/action_catalog.json`** — add at least one catalog entry matching the new
   `fault_mode`, `severity`, and `asset_type`, with the `action` and `source_sop`.

### 8.2 Add a new approved procedure (SOP)

1. Drop the new PDF into `data/sops_and_cases/` (prefix with `SOP_` for auto
   `doc_type` labeling in `document_store.py`).
2. Add corresponding entries to `data/action_catalog.json` (with `source_sop`
   pointing to the SOP ID).
3. Rebuild the RAG index: `python rag/build_index.py`.

### 8.3 Add a new chatbot tool

1. Write a function in `tools/langgraph_tools.py` (or a new tools file) decorated
   with `@tool`. Include a clear docstring — the LLM uses it to decide when to call
   the tool.
2. Import it in `recommendation_chat.py` and add it to the `tools` list (~line 51).
   The LangGraph `ToolNode` and `llm_with_tools.bind_tools()` calls will pick it up
   automatically. No graph structure change is needed.
3. If the tool needs a data-loader call, add caching to that loader following the
   `@_cache_data(ttl=300)` pattern in `tools/data_loader.py`.

### 8.4 Add a new demo scenario

1. Add a new dict to the `SCENARIOS` list in `scenarios.py` with a unique `label`,
   and pre-built `FaultDiagnosis`, `RiskAssessment`, and `KnowledgeGuidance`
   instances.
2. The Streamlit UI will pick it up automatically via `SCENARIO_MAP`.

---

## 9. Safety & Fallback Behavior

Agent 6.6 is designed with defense-in-depth so that no single failure — network
outage, missing file, bad LLM output, unexpected input — causes a crash or a
silent wrong recommendation.

| Layer | Mechanism |
|-------|-----------|
| **Input validation** | `validate_inputs()` checks 10+ conditions before the pipeline runs. Hard errors return `blocked_invalid_input`; soft warnings allow the pipeline to continue with defaults. |
| **Safe defaults** | `apply_safe_defaults()` fills `confidence`, `rul_min_days`, `rul_max_days`, `risk_level`, `failure_probability` when missing. All defaults are conservative. |
| **Four ordered guards** | Each guard produces a loud, specific, escalated `MaintenanceRecommendation` rather than a silent fallback. A human always knows exactly what went wrong. |
| **Per-LLM-call fallbacks** | Every LLM call is wrapped in `try/except`. Failures fall back to deterministic templates (`write_rationale`) or safe default dicts (`_llm_propose_novel_action`, `_llm_free_recommendation`, `classify_fault`, `guess_unknown_fault`). |
| **Data-loader fallbacks** | All 8 loaders catch `FileNotFoundError` and `json.JSONDecodeError`, log a warning, and return `[]` or `{}`. The pipeline continues with empty data and guards catch any downstream gaps. |
| **Startup health banner** | `app.py` checks for the 6 required data files at startup and displays a warning banner (not a crash) if any are missing. |
| **RAG graceful degradation** | `search_documents_tool` catches `FileNotFoundError` (index not built) and returns an informative message. All other chatbot functionality continues. |
| **Chat retry + recursion cap** | `answer_about_recommendation()` retries once on transient network errors. The LangGraph graph is capped at `recursion_limit=8` to prevent runaway tool-call loops. |
| **Human in the loop** | Every non-`"ok"` status forces `approval_status="escalated"`. Novel recommendations carry mandatory [TENTATIVE] labelling. The chatbot can discuss but never creates or modifies recommendations. |

---

## 10. Demo Scaffolding vs Production

### Demo-only (safe to remove for production integration)

| Component | File(s) | What it simulates |
|-----------|---------|-------------------|
| Pre-built demo scenarios | `scenarios.py` | Upstream agents 6.3/6.4/6.5 outputs; 11 scenarios A1–A4, B1–B7 |
| "Demo scenario" sidebar mode | `app.py` (SCENARIO_MAP usage) | Real-time agent inputs |
| "Add new scenario" form | `app.py` (PHASE 1/2/3 UI) | Structured agent inputs via free-text + sliders |
| "Simulate upstream values" expander | `app.py` | RUL, confidence, cost inputs from agents 6.4/6.3 |
| `classify_fault()` / `guess_unknown_fault()` in the UI | `agents/fault_classifier.py` calls in `app.py` | Agent 6.3 fault classification output |
| `telemetry_scenarios.json` | `data/` | Real sensor telemetry from Agent 6.1/6.2 |

### Production-ready (integration-ready without modification)

| Component | Notes |
|-----------|-------|
| `recommend_action(diagnosis, risk, guidance)` | Pass real Pydantic objects from 6.3/6.4/6.5 directly |
| `make_free_recommendation(diagnosis, risk, guidance, likely_fault)` | For confirmed-novel faults |
| `answer_about_recommendation(...)` | Stateless per-call; wire to any chat surface |
| All Pydantic schemas in `schemas/` | Stable contracts; no UI dependencies |
| `apply_safe_defaults()` + `validate_inputs()` | Safe to call before the pipeline in any context |
| `log_recommendation()` / `get_history()` | Append-only log; replace `LOG_PATH` for a real DB |
| All `data/` JSON files | Replace demo data in-place; loaders are data-agnostic |
| RAG index | Replace demo PDFs with production SOPs; rebuild index |

---

## 11. Known Gaps for Production

The following limitations exist by design in the demo but would need to be addressed
for a production deployment:

1. **No authentication / RBAC.** The Streamlit app is open to anyone on port 8501.
   Anyone can view recommendations and trigger approvals.

2. **No durable approve/reject write-back.** The approval buttons in the UI update
   `st.session_state` locally but do not write back to any external system, ticket,
   or database. The decision log does not record approval outcomes.

3. **Flat-file decision log with write races.** `data/decision_log.json` is a
   plain JSON file written with `LOG_PATH.write_text(...)`. Under concurrent
   users or multiple Streamlit workers, concurrent writes will corrupt the file.
   Replace with a proper database for multi-user deployments.

4. **Per-process data-loader cache.** `@st.cache_data(ttl=300)` is scoped to a
   single Streamlit process. Multiple workers each maintain their own cache;
   cache invalidation is not coordinated across workers.

5. **Demo window-anchor date shift.** `load_operations_context()` shifts all window
   timestamps forward from `2026-05-20` to today. Production should use real
   future-dated window data and remove the shifting logic.

6. **Single Azure OpenAI deployment for all calls.** All six LLM call sites use
   the same `AZURE_OPENAI_DEPLOYMENT`. In production you may want separate
   deployments for the chatbot (higher quality, lower latency) vs. rationale
   writing (cost-optimised).

7. **No LangSmith / observability.** There is no LangSmith tracing or other
   observability configured. Add `LANGCHAIN_TRACING_V2` and `LANGCHAIN_API_KEY`
   env vars for production monitoring.

---

## 12. Appendix: Full Dependency List

Exact versions installed in the project `.venv` (from `pip freeze`):

```
altair==5.5.0
annotated-types==0.7.0
anyio==4.13.0
attrs==25.3.0
blinker==1.9.0
cachetools==5.5.2
certifi==2025.4.26
charset-normalizer==3.4.2
click==8.2.1
colorama==0.4.6
defusedxml==0.7.1
distro==1.9.0
faiss-cpu==1.14.3
filelock==3.18.0
fonttools==4.58.4
fpdf2==2.8.7
fsspec==2025.5.1
gitdb==4.0.12
GitPython==3.1.44
h11==0.16.0
hf-xet==1.6.4
httpcore==1.0.9
httptools==0.6.4
httpx==0.28.1
huggingface_hub==0.33.1
idna==3.10
iniconfig==2.1.0
itsdangerous==2.2.0
Jinja2==3.1.6
jiter==0.10.0
joblib==1.5.0
jsonpatch==1.33
jsonpointer==3.0.0
jsonschema==4.23.0
jsonschema-specifications==2024.10.1
langchain==0.3.25
langchain-core==0.3.61
langchain-openai==0.3.18
langchain-text-splitters==0.3.8
langgraph==0.4.7
langgraph-checkpoint==2.0.26
langgraph-prebuilt==0.5.2
langgraph-sdk==0.1.72
langsmith==0.3.45
markdown-it-py==3.0.0
MarkupSafe==3.0.2
mdurl==0.1.2
mpmath==1.3.0
narwhals==1.23.0
networkx==3.4.2
numpy==2.2.6
openai==1.84.0
orjson==3.10.18
packaging==24.2
pandas==2.2.3
pillow==11.2.1
pluggy==1.6.0
protobuf==5.29.4
pyarrow==20.0.0
pydantic==2.11.5
pydantic_core==2.33.2
pydeck==0.9.1
Pygments==2.19.1
pypdf==5.5.0
pytest==8.3.5
python-dateutil==2.9.0.post0
python-dotenv==1.1.0
python-multipart==0.0.20
PyYAML==6.0.2
referencing==0.36.2
regex==2024.11.6
requests==2.32.3
requests-toolbelt==1.0.0
rich==14.0.0
rpds-py==0.25.5
safetensors==0.5.3
scikit-learn==1.7.0
scipy==1.15.3
sentence-transformers==4.1.0
setuptools==80.9.0
shellingham==1.5.4
six==1.17.0
smmap==5.0.2
sniffio==1.3.1
starlette==0.47.1
streamlit==1.45.1
sympy==1.14.0
tenacity==9.1.2
threadpoolctl==3.6.0
tiktoken==0.9.0
tokenizers==0.21.1
toml==0.10.2
torch==2.7.1
tqdm==4.67.1
transformers==4.52.4
typer==0.15.3
typing-inspection==0.4.1
typing_extensions==4.14.0
tzdata==2025.2
urllib3==2.4.0
uuid_utils==0.10.0
uvicorn==0.34.3
watchdog==6.0.0
websockets==15.0.1
xxhash==3.5.0
zstandard==0.23.0
```
