# DRO Learning & Memory Agent

**Agent 6.8 of the Downtime Response Orchestrator (DRO)** — a Streamlit application that transforms completed maintenance outcomes into retrievable institutional knowledge for bearing failure diagnostics.

Given a maintenance case (fault mode, asset, sensor signals, prognostics, prescribed action), the agent decides one of three outcomes: **Retrieve** existing knowledge, **Enrich** an existing case with a preview merge, or **Generate** a new case document from scratch.

**Current state:** Prototype complete. Demonstrated to leadership on 2026-07-07. Deployed on internal usbox.ai platform.

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
   - [Input Schema](#41-input-schema)
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

The DRO is an eight-agent pipeline for bearing failure predictive maintenance. Agents 1–7 sense, diagnose, and act on failures. Agent 6.8 (this project) learns from those completed cases so future recurrences benefit from lessons already captured.

| # | Agent | Phase | Purpose |
|---|---|---|---|
| 6.1 | Data Foundation | Sense | Validate sensor and enterprise data |
| 6.2 | Monitoring | Sense | Detect early abnormal patterns |
| 6.3 | Predictive Risk | Understand | Estimate failure risk and remaining useful life |
| 6.4 | Failure Intelligence | Understand | Identify fault mode and root cause |
| 6.5 | Knowledge | Decide | Retrieve SOPs, manuals, past cases |
| 6.6 | Prescriptive Optimisation | Decide | Recommend next action under constraints |
| 6.7 | Executor | Act & Learn | Trigger work orders, execute operational changes |
| **6.8** | **Learning & Memory** | **Act & Learn** | **Capture outcomes as retrievable knowledge (this project)** |

Upstream inputs to Agent 6.8 come from Agents 6.3, 6.4, 6.5, and 6.7. Downstream consumers of Agent 6.8's output are Agent 6.5 (Knowledge) for future case retrieval and the operator UI for lessons review.

---

## 2. Quick Start

### Prerequisites

- Python 3.11 or higher
- Windows PowerShell (project scripts use PowerShell syntax)
- Azure OpenAI resource with `gpt-4o-mini` deployment
- Langfuse account, US cloud region (optional — app runs without it)

### First-time setup

```powershell
cd learning_memory_agent
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Environment variables

Create `.env` at project root:

```
AZURE_OPENAI_KEY=<your key>
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-08-01-preview
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
```

The environment variable is named `AZURE_OPENAI_KEY` (not `AZURE_OPENAI_API_KEY`). Code and `.env` agree.

### Run

```powershell
streamlit run streamlit_app_v3.py
```

Opens at `http://localhost:8501`. Analytics tab should show "6 cases in knowledge base" on first launch.

### Nuclear reset (return to pristine 6-seed baseline)

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process streamlit -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3

Get-ChildItem data\learned_cases\ | Where-Object { $_.Name -notmatch "^case_00[1-6]_.*\.json$" } | Remove-Item -Force
Remove-Item data\faiss_index\* -Recurse -Force
'[]' | Set-Content logs\fault_tracker.json -Encoding UTF8 -NoNewline
'' | Set-Content logs\memory.log -Encoding UTF8 -NoNewline
'' | Set-Content logs\agent_decisions.log -Encoding UTF8 -NoNewline

.\venv\Scripts\python.exe tools\rebuild_index.py
```

---

## 3. How It Works

The agent processes each incoming maintenance case through a LangGraph pipeline of 4 nodes, routing to one of 3 decision paths based on knowledge coverage.

### 3.1 Deterministic Pipeline

Four nodes execute in sequence:

```
feedback_capture → retrieval → coverage_assessment → route_after_coverage_assessment
                                                             │
                        ┌────────────────────────────────────┼────────────────────────────┐
                        ▼                                    ▼                            ▼
                 existing_knowledge_node             partial_match_node          case_generation_node
                     (PATH A)                            (PATH C)                     (PATH B)
                        │                                    │                            │
                        └─── skip storage ───┬─── skip storage (preview only) ───┐        ▼
                                             │                                    memory_storage_node
                                             │                                    (writes to KB)
```

**feedback_capture_node** (`orchestrator/langgraph_flow.py`)
Validates incoming feedback dict schema. Required fields: `case_id`, `fault_mode`, `asset_type`, `bearing_type`. Optional: 30+ additional fields (see Section 4.1).

**retrieval_node**
Calls `services/knowledge_check.py::check_existing_knowledge()`. Embeds query via sentence-transformers, retrieves top-3 candidates from FAISS. Applies fault-mode mismatch penalty of `-0.08` to candidates whose fault_mode differs.

**coverage_assessment_node**
Calls `services/coverage_assessor.py::assess_coverage()`. LLM scores each of 5 knowledge dimensions as COMPLETE / PARTIAL / MISSING (see 3.3). Emits `knowledge_state` = EXISTING / PARTIAL / NEW.

**route_after_coverage_assessment** (router)
| Condition | Route to | Path |
|---|---|---|
| All 5 dimensions COMPLETE AND similarity ≥ 0.70 | `existing_knowledge_node` | A |
| ≥1 dimension PARTIAL/MISSING with identity match | `partial_match_node` | C |
| No candidate above similarity threshold | `case_generation_node` | B |

**case_generation_node** (PATH B only)
Calls `services/case_generator.py::generate_case_from_pipeline()`. LLM writes a 7-section case document via expert-persona prompt.

**memory_storage_node** (PATH B only)
Mints new `case_id` (format `CASE_YYYYMMDD_NNN`), writes case JSON to `data/learned_cases/`, adds vector to FAISS, writes tracker event to `logs/fault_tracker.json`.

### 3.2 LLM Call Sites

The agent makes 12 distinct LLM call sites, all traced by Langfuse:

| # | Site | Location | Purpose |
|---|---|---|---|
| 1 | `coverage_assessor` | `services/coverage_assessor.py::assess_coverage()` | Score 5 dimensions |
| 2 | `case_generator` | `services/case_generator.py::generate_case_from_pipeline()` | Generate PATH_B case |
| 3 | Enrichment preview | `streamlit_app_v3.py::_generate_enrichment_preview()` | PATH_C structured JSON |
| 4 | Add Case extraction | `streamlit_app_v3.py::extract_case_from_scenario()` | Extract structured fields from free text |
| 5 | Chatbot intent classifier | `streamlit_app_v3.py::detect_intent()` | Classify user message into 6 intents |
| 6 | Chatbot: FAULT_QUERY handler | `streamlit_app_v3.py::handle_fault_query()` | Answer fault questions with citations |
| 7 | Chatbot: ANALYTICS_QUERY handler | `streamlit_app_v3.py::handle_analytics_query()` | Answer KB stats questions |
| 8 | Chatbot: CAPTURE_CASE handler | `streamlit_app_v3.py::handle_capture_case()` | Capture case via conversational form |
| 9 | Chatbot: GENERAL handler | `streamlit_app_v3.py::handle_general()` | Greetings, help, out-of-domain refusals |
| 10 | Chatbot: dispatch router | `streamlit_app_v3.py::route_to_handler()` | LLM-assisted intent disambiguation |
| 11 | Chatbot: follow-up chips | `streamlit_app_v3.py::generate_followup_chips()` | Generate 3 next-question suggestions |
| 12 | Fault mode resolution | `streamlit_app_v3.py::resolve_fault_mode()` | Free-text to canonical fault_mode |

All 9 sites in `streamlit_app_v3.py` pass `config={"callbacks": [langfuse_handler]}` at invoke time. Coverage assessor and case generator use `@observe` decorators; case generator additionally uses `langfuse.openai` patched client for automatic prompt/token capture.

### 3.3 Novel / Unknown-Fault Flow (PATH B)

When no candidate exceeds the similarity threshold (0.70), the router selects PATH B.

**Trigger conditions:**
- No FAISS candidate above `similarity_threshold` (`config.json > retrieval > similarity_threshold`)
- OR all candidates have wrong `fault_mode` after the `-0.08` penalty is applied

**Execution flow:**
1. `case_generation_node` receives the feedback dict from the pipeline state
2. `services/case_generator.py::generate_case_from_pipeline()` prepares an expert-persona LLM prompt
3. Prompt requests a structured 7-section case document: identifier, signal signature, diagnosis, action, outcome, root cause, lessons
4. LLM returns JSON that is parsed into the case schema (Section 4.2)
5. `memory_storage_node` mints case_id, writes JSON to disk, updates FAISS
6. UI renders purple PATH_B panel with the new case_id

**Idempotency:** Running the same scenario twice results in PATH_B on first run (case created), PATH_A on second run (case now retrieved). This is by design — the agent LEARNS on first exposure.

### 3.4 Chatbot

Floating popover accessed via top-right toggle. Six intent classes with a two-stage classifier.

**Stage 1 — Pre-LLM heuristic:**
- CASE_id regex (`\b(CASE|AST)[\s_]?\d+\b`) — detects direct case references
- Strong keyword tokens (`_STRONG_INTENT_TOKENS` at ~line 4858) — routes to KB_LIST, KB_LIST_ACTIVITY, CAPTURE_CASE, or ANALYTICS_QUERY
- `is_fault_domain` heuristic — bearing/motor/pump vocabulary triggers FAULT_QUERY

**Stage 2 — LLM classifier:**
Falls back if heuristic doesn't fire. `gpt-4o-mini` classifies into: FAULT_QUERY, ANALYTICS_QUERY, KB_LIST, KB_LIST_ACTIVITY, CAPTURE_CASE, GENERAL.

**Multi-turn memory:**
- Retrieval results are stashed in `st.session_state["kc_last_retrieval"]` after each FAULT_QUERY
- Follow-up detection via `_is_followup_query()` with two paths:
  - Pronoun path: message contains pronoun/anaphor AND no strong keyword
  - Interrogative path: starts with question word (what/which/how/…), ≤ 8 words, no new case_id, no strong keyword
- If either path fires, `handle_fault_query` reuses the stashed retrieval instead of running fresh FAISS
- Two guards force fresh retrieval anyway: new case_id detected, or new fault_mode detected

**Evidence integrity:**
Only candidates above `_EVIDENCE_SIM_THRESHOLD` (0.55, configurable) surface in the evidence card. Prevents unrelated cases from appearing as citations.

**Out-of-domain refusal:**
"General" handler is instructed to politely refuse questions outside bearing maintenance scope. Tested with Piyush's "who won IPL 2026" probe on demo day — refused correctly.

### 3.5 RAG Document Search

Search Case tab uses **hybrid retrieval** — keyword AND-match (with synonym expansion) plus FAISS semantic search, results unioned, deduplicated by case_id, capped at 4 results. This is the only retrieval site in the codebase that is genuinely hybrid in the keyword-plus-semantic sense. The pipeline retrieval (LangGraph `retrieval_node`) uses pure FAISS semantic search with fault-mode mismatch penalty (see Section 4.4); the chatbot uses tiered cascade retrieval — four branches, first-match-returns — via `cascade_retrieve_cases()`.

**Signal 1 — Keyword AND-match:**
`_search_matches()` tokenises the query and matches against `_case_search_corpus()`, which is IDENTITY-ONLY (fault_mode + asset_type + bearing_type + case_id). This is intentional — matching against narrative fields creates false positives ("bearing" in a lessons paragraph would surface unrelated cases).

**Signal 2 — Synonym expansion:**
Free-text terms like "greasing" or "lube" mapped to canonical `lubrication_issue` via `FAULT_SYNONYMS` (~line 1065). Expanded terms passed through Signal 1.

**Signal 3 — FAISS semantic:**
`_faiss_matches()` embeds the query, retrieves top-K neighbors above `_EVIDENCE_SIM_THRESHOLD`. Handles natural-language queries like "fault in the gearbox."

**Result ordering:** Keyword matches first (most precise), then synonym-expanded, then semantic. Preserves precision on exact queries while enabling natural language.

---

## 4. Integration Contracts

This section defines the exact shape of data flowing INTO and OUT OF the agent. Integration team building the wiring from upstream DRO agents should use these schemas as the source of truth.

### 4.1 Input Schema

The agent accepts one master input type: the `feedback` dict. Two entry points build it (Section 4.3) and pass it to the LangGraph pipeline.

**Required fields** (validated by `feedback_capture_node`):

| Field | Type | Provided by (production) | Example |
|---|---|---|---|
| `case_id` | string | Executor Agent (7) OR minted by this agent | `"CASE_20260713_001"` |
| `fault_mode` | string | Failure Intelligence Agent (4) | `"outer_race_fault"` |
| `asset_type` | string | Data Foundation Agent (1) | `"motor"` |
| `bearing_type` | string | Data Foundation Agent (1) | `"SKF6310"` |

**Optional fields** (all consumed downstream if present, gracefully absent otherwise):

**Asset identity (Data Foundation Agent 1):**
| Field | Type | Example |
|---|---|---|
| `asset_id` | string | `"AST_MTR_001"` |

**Signal signature (Monitoring Agent 2):**
| Field | Type | Example |
|---|---|---|
| `vib_rms_mm_s` | float | `7.5` |
| `kurtosis` | float | `7.2` |
| `temp_c` | float | `81.0` |
| `bpfo_energy` | float | `3.8` |
| `bpfi_energy` | float | `2.1` |
| `signal_quality_score` | float | `0.99` |
| `shaft_offset_mm` | float | `0.45` |
| `anomaly_score` | float | `0.81` |

**Fault diagnosis (Failure Intelligence Agent 4):**
| Field | Type | Example |
|---|---|---|
| `fault_stage` | int (0–3) | `3` |
| `diagnosis_confidence` | float | `0.87` |
| `diagnosis_reasoning` | string | `"BPFO energy dominant, kurtosis > 6"` |

**Risk assessment (Predictive Risk Agent 3):**
| Field | Type | Example |
|---|---|---|
| `risk_level` | string | `"HIGH"` |
| `failure_probability` | float | `0.72` |
| `rul_days_min` | int | `5` |
| `rul_days_max` | int | `10` |

**Prescription (Prescriptive Optimisation Agent 6):**
| Field | Type | Example |
|---|---|---|
| `recommended_action` | string | `"bearing_replacement"` |
| `sop_reference` | string | `"SOP-001"` |
| `parts_required` | list[string] OR string | `["SKF6310 bearing", "seal"]` |
| `estimated_duration_hr` | int | `4` |

**Execution outcome (Executor Agent 7):**
| Field | Type | Example |
|---|---|---|
| `work_order_id` | string | `"WO-2026-1234"` |
| `work_order_priority` | string | `"HIGH"` |
| `action_taken` | string | `"Replaced outer race bearing SKF6310"` |
| `post_repair_vib_rms` | float | `1.2` |
| `post_repair_temp_c` | float | `52.0` |
| `post_repair_result` | string | `"successful"` |
| `technician_notes` | string | `"Bearing showed heavy contamination"` |
| `root_cause` | string | `"Seal degradation allowed contamination ingress"` |
| `lessons_learned` | string | `"Replace seal at every bearing change"` |

**QA gates (Predictive Risk Agent 3):**
| Field | Type | Example |
|---|---|---|
| `expected_post_vib_max` | float | `2.5` |
| `expected_post_temp_max` | float | `65.0` |
| `expected_post_kurtosis_max` | float | `3.5` |
| `qa_window` | string | `"48h"` |

**Control flags:**
| Field | Type | Purpose |
|---|---|---|
| `_nl_assessment_only` | bool | Suppress KB write; use for read-only NL assessment |
| `source` | string | `"discovery_demo"` \| `"user_generated"` \| `"upstream_pipeline"` |
| `created_at` | ISO-8601 datetime string | Case creation timestamp |
| `run_id` | string | Correlation ID for Langfuse trace grouping |

**Complete example — production-shaped feedback dict:**

```json
{
  "case_id": "CASE_20260713_001",
  "fault_mode": "outer_race_fault",
  "asset_type": "motor",
  "asset_id": "AST_MTR_042",
  "bearing_type": "SKF6310",
  "fault_stage": 3,
  "vib_rms_mm_s": 7.5,
  "kurtosis": 7.2,
  "temp_c": 81.0,
  "bpfo_energy": 3.8,
  "signal_quality_score": 0.99,
  "anomaly_score": 0.81,
  "diagnosis_confidence": 0.87,
  "diagnosis_reasoning": "BPFO energy dominant at 3.8 g² with kurtosis > 6",
  "risk_level": "HIGH",
  "failure_probability": 0.72,
  "rul_days_min": 5,
  "rul_days_max": 10,
  "recommended_action": "bearing_replacement",
  "sop_reference": "SOP-001",
  "parts_required": ["SKF6310 bearing", "housing seal"],
  "estimated_duration_hr": 4,
  "work_order_id": "WO-2026-1234",
  "work_order_priority": "HIGH",
  "action_taken": "Replaced outer race bearing SKF6310",
  "post_repair_vib_rms": 1.2,
  "post_repair_temp_c": 52.0,
  "post_repair_result": "successful",
  "root_cause": "Seal degradation allowed contamination ingress",
  "lessons_learned": "Replace seal at every bearing change",
  "source": "upstream_pipeline",
  "created_at": "2026-07-13T14:23:00Z",
  "run_id": "run_a1b2c3d4"
}
```

**Machine-readable version:** The complete input contract with all
field types, allowed values, and complete example is also available
as JSON at `docs/learning_memory_agent_input_contract.json` for
integration engineers who prefer schema-driven wiring.

### 4.2 Output Schema

The agent produces two outputs: a **case document** (persisted JSON) and a **tracker event** (log append).

**Case document** — `data/learned_cases/<case_id>_<source>.json`:

```json
{
  "case_id": "CASE_20260713_001",
  "fault_mode": "outer_race_fault",
  "asset_type": "motor",
  "asset_id": "AST_MTR_042",
  "bearing_type": "SKF6310",
  "signal_signature": {
    "vibration_rms": 7.5,
    "kurtosis": 7.2,
    "temperature_c": 81.0,
    "bpfo_energy": 3.8,
    "signal_quality": 0.99
  },
  "diagnosis": "Stage 3 outer race fault confirmed",
  "reasoning": "BPFO energy dominant at 3.8 g² with kurtosis > 6, consistent with outer race spall",
  "action_taken": "Replaced outer race bearing SKF6310",
  "sop_reference": "SOP-001",
  "parts_required": ["SKF6310 bearing", "housing seal"],
  "duration_hours": 4,
  "root_cause": "Seal degradation allowed contamination ingress via housing end cover",
  "contributing_factors": ["Improper storage humidity", "Missed scheduled inspection"],
  "lessons_learned": "Housing end cover seal is a wear item; replace at every bearing change",
  "prevention": "Add seal to standard bearing-change parts kit; inspect housing at 3-month intervals",
  "result": "successful",
  "source": "upstream_pipeline",
  "created_at": "2026-07-13T14:23:00Z",
  "valid_until": "2027-07-13T14:23:00Z"
}
```

**Tracker event** — appended to `logs/fault_tracker.json`:

```json
{
  "case_id": "CASE_20260713_001",
  "timestamp": "2026-07-13T14:23:05Z",
  "path": "B",
  "fault_mode": "outer_race_fault",
  "asset_type": "motor",
  "similarity": null,
  "run_id": "run_a1b2c3d4"
}
```

Fields:
- `path` — `"A"` (retrieve) / `"B"` (generate) / `"C"` (enrich) / `"USER_CAPTURE"` (manual)
- `similarity` — best FAISS similarity score at retrieval (0.0–1.0, null if no candidates)

### 4.3 Entry-Point Functions

Two functions are exposed as the agent's public API:

**`run_pipeline_demo(feedback: dict, run_id: str = None, ...) -> dict`**
- Location: `api/streamlit_api.py::run_pipeline_demo()`
- Signature: takes feedback dict per Section 4.1, returns LangGraph state dict
- Return shape: `knowledge_state, path_taken, processing_log, dimension_assessments, covered_dimensions, missing_dimensions, gap_summary, coverage_reasoning, best_similarity, retrieved_case_id, retrieved_summary, generated_case_content, stored_case_id, final_message, pipeline_duration_ms`
- Use this for **automated pipeline invocation** from upstream orchestrator

**`create_learned_case(case_id, asset_type, bearing_type, fault_mode, root_cause, action_taken, result, lessons_learned, valid_until, created_by='streamlit_user') -> Tuple[bool, str]`**
- Location: `api/streamlit_api.py::create_learned_case()`
- Signature: takes positional args for case fields; returns tuple of (success_boolean, message_string)
- Return shape: `(True, "Case X stored successfully.")` on success, `(False, "error message")` on failure
- Use this for **manual case injection** (e.g., loading historical incidents from Add Case UI or external sources)

**Programmatic invocation example:**

```python
from api.streamlit_api import run_pipeline_demo

feedback = {
    "case_id": "CASE_20260713_001",
    "fault_mode": "outer_race_fault",
    "asset_type": "motor",
    "bearing_type": "SKF6310",
    # ... additional fields per Section 4.1
}

result = run_pipeline_demo(feedback, run_id="upstream_batch_042")
print(f"Path: {result['path_taken']}, State: {result['knowledge_state']}")
```

### 4.4 Hardcoded Assumptions

Assumptions baked into the agent that upstream teams should be aware of:

**Fault mode taxonomy is closed-set:**
Only these `fault_mode` values are recognised by name (others fall to `unknown_fault`):
`outer_race_fault`, `inner_race_fault`, `cage_fault`, `lubrication_issue`, `misalignment`, `imbalance`, `looseness`, `sensor_fault`, `healthy`

Full list: `streamlit_app_v3.py::FAULT_SYNONYMS` (~line 1065).

**Asset type taxonomy is closed-set:**
Only these `asset_type` values are canonical:
`motor`, `pump`, `conveyor`, `gearbox`, `fan`, `compressor`

Full list: `streamlit_app_v3.py::ASSET_SYNONYMS` (~line 1111).

**Bearing type is free-string but expected to follow SKF convention:**
`SKF6208`, `SKF6310`, `SKF22212`, etc. No validation applied.

**Case ID format is enforced on generation:**
`CASE_<YYYYMMDD>_<NNN>` for pipeline-generated cases. `CASE_00N` for the 6 seed cases. `AST_XXX_NNN` reserved but not currently used.

**Similarity threshold applies uniformly across fault modes:**
The `similarity_threshold = 0.70` (in `config.json`) applies to all retrievals. A future refinement (see Section 11) would calibrate per fault mode.

**FAULT_MODE_MISMATCH_PENALTY = -0.08:**
Applied to any candidate whose `fault_mode` differs from the query's `fault_mode`. Ensures identity-mismatched cases rank lower even if their vector is semantically close.

**Coverage assessor is non-deterministic:**
`assess_coverage()` uses an LLM to score 5 dimensions. Same input can score differently across runs at the borderline. Not a bug — a known limitation for this prototype.

**Currency of stored cases is not enforced:**
`valid_until` field exists but is not enforced by retrieval. Cases older than their validity window will still surface. Retention/eviction is on the roadmap.

**Timestamps stored and displayed as UTC:**
All internal timestamps are ISO-8601 UTC end-to-end. No timezone conversion is performed by this agent. Upstream agents should send UTC.

### 4.5 Human Enrichment Handoff to Executor Agent

When PATH_C fires (partial knowledge match), the agent generates an enrichment proposal — a structured document of what NEW information should be added to the matched existing case, based on the incoming feedback. In the current prototype this preview is rendered inline in the Streamlit UI; **in production it is intended to be submitted to Executor Agent (6.7) for human review and approval before being merged into the knowledge base.**

**Implementation status:** The enrichment payload structure is fully implemented. The submission mechanism to Executor Agent is NOT yet implemented — currently marked as design intent. See "Gap for production" at the end of this section.

#### 4.5.1 Enrichment payload structure (implemented)

Produced by `_generate_enrichment_preview()` in `streamlit_app_v3.py`. Contains only the dimensions the coverage assessor scored as PARTIAL or MISSING — the COMPLETE dimensions are omitted because the existing case already has them.

```json
{
  "dims": {
    "signal_signature": {
      "vibration_rms": "6.4 mm/s",
      "kurtosis": "5.8",
      "temp_c": "72.0 °C",
      "bpfo_energy": "2.1",
      "signal_quality": "good"
    },
    "diagnosis_and_reasoning": {
      "primary_diagnosis": "outer race fault, stage 3",
      "confidence": "high",
      "reasoning": "BPFO energy elevated with kurtosis > 5"
    },
    "action_and_outcome": {
      "recommended_action": "bearing replacement",
      "sop_reference": "SOP-001",
      "parts_required": "SKF6310 bearing, housing seal",
      "est_duration": "4 hours"
    },
    "root_cause_and_factors": {
      "primary_cause": "seal degradation",
      "contributing_factors": "contamination ingress, missed inspection"
    },
    "lessons_and_future_reference": {
      "key_lesson": "seal is a wear item; replace at every bearing change",
      "prevention": "add seal to bearing-change parts kit"
    }
  },
  "empty_reason": null
}
```

**Field types:** All dimension values are string-typed (with units embedded where relevant) for human review. If the LLM cannot generate a preview, `dims` is empty and `empty_reason` contains a diagnostic string.

**Which dimensions appear:** Only PARTIAL and MISSING dimensions from the coverage assessment. If all 5 dimensions were COMPLETE, PATH_C would not have fired — the pipeline would have taken PATH_A instead.

#### 4.5.2 LangGraph partial_match state (implemented)

Produced by `partial_knowledge_node` in `orchestrator/langgraph_flow.py`. Held in the LangGraph state dict for the current pipeline run:

```json
{
  "gap_details": {
    "matched_case_id": "CASE_001",
    "covered_dimensions": ["signal_signature", "diagnosis_and_reasoning"],
    "missing_dimensions": ["action_and_outcome", "root_cause_and_factors", "lessons_and_future_reference"],
    "gap_summary": "New evidence adds action, root cause, and lessons",
    "coverage_reasoning": "LLM assessment output — full text",
    "similarity_score": 0.65
  },
  "partial_summary": "Partial knowledge match — proposed enrichment attached",
  "path_taken": "C",
  "final_message": "Partial knowledge match — proposed enrichment attached"
}
```

This state is currently consumed only by the Streamlit UI to render the amber PATH_C panel. It is not forwarded to any external agent.

#### 4.5.3 Proposed Executor handoff envelope (design intent — not yet implemented)

When the "Submit for Approval" flow is built, the envelope sent to Executor Agent should contain three components: the incoming case that triggered PATH_C, the matched existing case being proposed for enrichment, and the enrichment proposal itself. This gives the human reviewer complete context.

```json
{
  "envelope_type": "enrichment_approval_request",
  "envelope_version": "1.0",
  "run_id": "run_a1b2c3d4",
  "created_at": "2026-07-14T09:14:00Z",
  "source_agent": "learning_memory_agent",
  "target_agent": "executor_agent",
  "action_required": "approve_enrichment",

  "incoming_case": {
    "case_id": "CASE_20260714_003",
    "fault_mode": "outer_race_fault",
    "asset_type": "motor",
    "asset_id": "AST_MTR_042",
    "bearing_type": "SKF6310",
    "...all other feedback fields per Section 4.1..."
  },

  "matched_existing_case": {
    "case_id": "CASE_001",
    "similarity_score": 0.65,
    "coverage_state": "PARTIAL",
    "...full case document per Section 4.2..."
  },

  "gap_details": {
    "covered_dimensions": ["signal_signature", "diagnosis_and_reasoning"],
    "missing_dimensions": ["action_and_outcome", "root_cause_and_factors", "lessons_and_future_reference"],
    "gap_summary": "New evidence adds action, root cause, and lessons",
    "coverage_reasoning": "LLM assessment output — full text"
  },

  "enrichment_proposal": {
    "dims": {
      "action_and_outcome": { "...": "..." },
      "root_cause_and_factors": { "...": "..." },
      "lessons_and_future_reference": { "...": "..." }
    },
    "empty_reason": null
  },

  "approval_metadata": {
    "reviewer_role_required": "maintenance_engineer",
    "urgency": "normal",
    "expires_at": "2026-07-21T09:14:00Z"
  }
}
```

**Expected executor response** (also not yet implemented):

```json
{
  "envelope_type": "enrichment_approval_response",
  "envelope_version": "1.0",
  "run_id": "run_a1b2c3d4",
  "response_to_case_id": "CASE_001",
  "reviewed_at": "2026-07-14T11:22:00Z",
  "reviewer_id": "user_042",
  "decision": "approved",
  "reviewer_notes": "Confirmed — merge as-is",
  "field_edits": null
}
```

Decision values: `"approved"` (merge as-is), `"approved_with_edits"` (merge with `field_edits` applied), `"rejected"` (do not merge, discard proposal).

#### 4.5.4 Gap for production

To wire the enrichment-to-Executor handoff, the following needs to be built:

1. **UI:** A "Submit for Approval" button on the PATH_C amber panel (currently rendered by `streamlit_app_v3.py`, section 9 "ENRICHMENT PREVIEW (PATH_C)").
2. **Serialization:** A function that assembles the envelope from `incoming_case` (already in scope as `feedback`), `matched_existing_case` (loaded from `data/learned_cases/` by `case_id`), `gap_details` (from LangGraph state), and `enrichment_proposal` (from `_generate_enrichment_preview()`).
3. **Transport:** HTTP POST to Executor Agent endpoint, or event bus publish, depending on integration team's chosen mechanism. Endpoint URL should be configurable via `config.json` (e.g. `executor.approval_endpoint`).
4. **Response handler:** A poll or callback endpoint that receives the executor's approval response, and applies the write to FAISS + disk if approved. Rejection is logged; no write occurs.
5. **Persistence:** Track pending approvals in a state file (e.g. `data/pending_approvals/<run_id>.json`) so the flow survives app restarts.

**Machine-readable version:** The complete executor handoff envelope
schema is also available as JSON at
`docs/learning_memory_agent_executor_handoff_contract.json`.

**Estimated effort:** 2-3 weeks including Executor Agent's side of the contract and end-to-end testing. See Section 11 for the deferred-work log.

---

## 5. Project Structure

```
learning_memory_agent/
├── streamlit_app_v3.py         # Main UI (7,600 lines, 19 SECTION banners)
├── config.json                 # Runtime tunable values (thresholds, paths, model)
├── README.md                   # This file
├── requirements.txt            # Python dependencies
├── .env                        # Credentials (not committed)
├── .streamlit/
│   └── config.toml             # Light theme, Accenture purple #A100FF
├── orchestrator/
│   ├── langgraph_flow.py       # 4-node LangGraph pipeline definition
│   └── __init__.py
├── services/
│   ├── knowledge_check.py      # FAISS retrieval, similarity threshold
│   ├── coverage_assessor.py    # LLM 5-dimension scoring (@observe)
│   ├── case_generator.py       # PATH_B case generation (langfuse.openai patched)
│   ├── activity_log.py         # fault_tracker + memory.log writers
│   ├── pdf_renderer.py         # Case → PDF rendering
│   └── __init__.py
├── rag/
│   ├── vector_storage.py       # FAISS index abstraction
│   ├── embeddings.py           # Text → vector via sentence-transformers
│   └── __init__.py
├── api/
│   ├── streamlit_api.py        # Public API: run_pipeline_demo, create_learned_case
│   └── __init__.py
├── config/
│   ├── constants.py            # Domain constants (currently orphan — see Section 11)
│   ├── settings.py             # Pydantic settings (unused — legacy)
│   └── __init__.py
├── tools/
│   ├── rebuild_index.py        # Rebuild FAISS from disk .json files
│   ├── fix_faiss_alignment.py  # Repair FAISS/metadata length drift
│   ├── load_data.py            # Legacy seed loader (referenced in docstrings)
│   └── __init__.py
├── data/
│   ├── learned_cases/          # Case JSON documents (6 seeds + generated + user)
│   └── faiss_index/
│       ├── index.faiss         # Binary FAISS vector index
│       └── metadata.json       # Per-case sidecar metadata
├── logs/
│   ├── fault_tracker.json      # Pipeline run events (Analytics source)
│   ├── memory.log              # Memory lifecycle (mostly suppressed)
│   └── agent_decisions.log     # Routing decisions (debug)
└── archive/
    ├── backups/                # Timestamped snapshots
    ├── one_shots/              # Deprecated scripts
    ├── config_backups/         # Old theme configs
    └── fastapi_removed_*/      # Removed dual-interface backend
```

**Section banners in streamlit_app_v3.py** (search "SECTION:" in VS Code):

| # | Section | # | Section |
|---|---|---|---|
| 1 | IMPORTS AND CONFIGURATION | 11 | CHATBOT — INTENT / FOLLOW-UP |
| 2 | RUNTIME CONFIGURATION LOADER | 12 | CHATBOT — RETRIEVAL LAYER |
| 3 | LLM CLIENT AND CONSTANTS | 13 | CHATBOT — INTENT HANDLERS |
| 4 | LANGFUSE INITIALIZATION | 14 | CHATBOT — KB_LIST / ANALYTICS |
| 5 | DOMAIN VOCABULARY | 15 | CHATBOT — DISPATCH / CHIPS |
| 6 | DATA LOAD HELPERS | 16 | KNOWLEDGE COPILOT RENDERER |
| 7 | SEARCH AND MATCHING | 17 | DISCOVERY TAB / PIPELINE |
| 8 | ANALYTICS AND KPI RENDERING | 18 | MAIN APP LAYOUT / ROUTING |
| 9 | ENRICHMENT PREVIEW (PATH_C) | 19 | (reserved) |
| 10 | ADD CASE / SEARCH CASE | | |

---

## 6. Configuration & Tuning

### 6.1 `config.json` — Runtime-tunable values

At project root. Loaded on startup by `_load_runtime_config()`. If the file is missing or malformed, defaults are used.

```json
{
  "retrieval": {
    "similarity_threshold": 0.70,
    "fault_mode_mismatch_penalty": -0.08,
    "top_k": 3
  },
  "add_case": {
    "duplicate_similarity_threshold": 0.55,
    "min_text_length_for_hash": 15
  },
  "chatbot": {
    "evidence_similarity_threshold": 0.55,
    "max_conversation_turns": 10
  },
  "paths": {
    "data_dir": "data/learned_cases",
    "faiss_index_dir": "data/faiss_index",
    "memory_log": "logs/memory.log",
    "decision_log": "logs/agent_decisions.log",
    "fault_tracker": "logs/fault_tracker.json"
  },
  "azure_openai": {
    "api_version": "2024-08-01-preview",
    "default_deployment": "gpt-4o-mini",
    "temperature": 0.3,
    "max_tokens": 1500
  },
  "langfuse": {
    "enabled": true,
    "flush_on_shutdown": true
  },
  "case_generation": {
    "default_estimated_duration_hours": 4,
    "default_priority": "MEDIUM"
  }
}
```

### 6.2 What each value controls

| Key | Effect if lowered | Effect if raised |
|---|---|---|
| `retrieval.similarity_threshold` | More PATH_A retrievals (may retrieve unrelated cases) | Fewer retrievals; more PATH_B generations (KB grows faster) |
| `retrieval.fault_mode_mismatch_penalty` | Fault-mode-mismatched candidates more likely to surface | Stricter fault-mode discipline |
| `retrieval.top_k` | Fewer candidates evaluated per query | More candidates; slower retrieval |
| `add_case.duplicate_similarity_threshold` | More permissive on Add Case duplicates | Stricter block on duplicates |
| `chatbot.evidence_similarity_threshold` | Weaker citations surface in evidence card | Only strong matches surface |
| `chatbot.max_conversation_turns` | Shorter multi-turn memory window | Longer memory; higher token cost |

### 6.3 Environment variables (`.env`)

Values that are secrets or environment-specific. Never in `config.json`.

| Key | Purpose |
|---|---|
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Azure resource endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment name (e.g. `gpt-4o-mini`) |
| `AZURE_OPENAI_API_VERSION` | API version (currently `2024-08-01-preview`) |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key |
| `LANGFUSE_BASE_URL` | Langfuse endpoint (e.g. `https://us.cloud.langfuse.com`) |

Note: env var is `AZURE_OPENAI_KEY` — not the Azure SDK's default `AZURE_OPENAI_API_KEY`. Code and `.env` agree.

### 6.4 Theme config (`.streamlit/config.toml`)

Streamlit theme. Applied at boot.

```toml
[theme]
base = "light"
primaryColor = "#A100FF"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F8F7FB"
textColor = "#1A1A1A"
font = "sans serif"
```

---

## 7. Data Dependencies

### 7.1 Seed cases

Six baseline cases ship with the project in `data/learned_cases/`:

| case_id | fault_mode | asset_type | bearing_type |
|---|---|---|---|
| CASE_001 | outer_race_fault | motor | SKF6310 |
| CASE_002 | lubrication_issue | pump | SKF6208 |
| CASE_003 | inner_race_fault | motor | SKF6310 |
| CASE_004 | cage_fault | conveyor | SKF22212 |
| CASE_005 | healthy | motor | SKF6310 |
| CASE_006 | sensor_fault | conveyor | SKF22212 |

**Provenance:** Synthetic. Fault signatures follow ISO 13373-1 and ISO 17359 conventions. No real plant data was used.

### 7.2 FAISS index

Location: `data/faiss_index/index.faiss` (binary) + `metadata.json` (sidecar).

Vector dimension: 384 (sentence-transformers `all-MiniLM-L6-v2` default).

Index type: `IndexFlatL2` (exact search; scales to ~10,000 cases without issue).

**Rebuild:**
```powershell
.\venv\Scripts\python.exe tools\rebuild_index.py
```

Reads all `.json` in `data/learned_cases/`, embeds each, writes fresh index + sidecar. Idempotent.

### 7.3 Runtime logs

- `logs/fault_tracker.json` — JSON array; **read by Analytics tab**. Empty tracker = "Analytics unavailable" fallback fires.
- `logs/memory.log` — plain text; mostly suppressed by `_INCLUDE_MEMORY_LIFECYCLE = False` flag.
- `logs/agent_decisions.log` — plain text; debug aid, no UI dependency.

---

## 8. Extending the Agent

### 8.1 Adding a new fault mode

1. Add the canonical name (e.g. `belt_wear`) to `FAULT_SYNONYMS` in `streamlit_app_v3.py` (~line 1065) with all synonyms
2. If it applies to a new asset type, add that to `ASSET_SYNONYMS` (~line 1111)
3. If it needs an RUL profile, add to `FAULT_RUL` (~line 1415)
4. If it needs a default SOP/action, add to `FAULT_ACTION` (~line 1445)
5. Add a demo scenario in the `SCENARIOS` dict (~line 1185) for testing
6. Rebuild FAISS: `.\venv\Scripts\python.exe tools\rebuild_index.py` (if new seed cases were added)

### 8.2 Adding a new scenario for demo

Insert into the `SCENARIOS` dict in `streamlit_app_v3.py` (~line 1185) using this shape:

```python
"Belt wear — Fan (SKF6206)": {
    "asset": "AST_FAN_010",
    "bearing": "SKF6206",
    "fault": "belt_wear",
    "stage": 2,
    "risk": "MEDIUM",
    "score": 0.65,
    "matched_case": "",  # empty if novel; else target case_id
    "signals": {
        "vib_rms_mm_s": 4.5,
        "kurtosis": 4.1,
        "temp_c": 62.0,
        "bpfo_energy": 1.8,
        "signal_quality": 0.98
    }
}
```

Then add a matching entry in `SCENARIO_DESCRIPTIONS` (~line 1270) with a short prose description.

### 8.3 Wiring a new upstream agent

New upstream input becomes a new field in the `feedback` dict. Steps:

1. Add the field to Section 4.1 of this README (Optional or Required as appropriate)
2. If the pipeline should consume it, add reference in `orchestrator/langgraph_flow.py::feedback_capture_node` OR downstream service
3. If the case document should persist it, add to `services/case_generator.py` prompt template
4. Test with a synthetic scenario that includes the new field

### 8.4 Adding a new coverage dimension

The 5 dimensions are defined in `services/coverage_assessor.py`. To add a 6th:

1. Extend the LLM prompt in `assess_coverage()` to score the new dimension
2. Update the return schema (`dimensions` dict keys)
3. Update the router in `orchestrator/langgraph_flow.py::route_after_coverage_assessment` if the new dimension affects path selection
4. Update the UI rendering in Section 9 of `streamlit_app_v3.py` (PATH_C enrichment preview)

---

## 9. Safety & Fallback Behavior

### 9.1 Langfuse graceful degradation

If Langfuse credentials are missing or `auth_check()` fails at startup, the `LANGFUSE_AVAILABLE` flag stays `False`. Every callback site follows the pattern:

```python
config={"callbacks": [langfuse_handler]} if langfuse_handler else {}
```

App runs normally without observability that session. No crash, no user-visible impact.

### 9.2 FAISS-disk desync recovery

`services/knowledge_check.py` catches misalignment between FAISS ntotal and metadata length. If detected, an error message points to `tools/fix_faiss_alignment.py` for repair.

Nuclear reset (Section 2) is the last-resort recovery path.

### 9.3 Hard-block on exact-identity duplicates in Add Case

Two cases with the same `fault_mode + asset_type + bearing_type` cannot coexist. Add Case UI shows "Duplicate Case Blocked" with only "View Existing" and "Cancel" options — the "Save as new anyway" button is hidden. Prevents KB pollution.

Variants (e.g. same fault mode, different bearing) show a softer warning with the Save-as-new button visible.

### 9.4 PATH_C is preview-only

The Enrich path shows what would be merged into an existing case but does NOT write to the KB. In production, a human-in-the-loop approval gate would authorise the write. See Section 11.

### 9.5 Out-of-domain refusal

Chatbot's GENERAL handler is instructed to refuse questions outside bearing maintenance scope. Tested at demo with "who won the IPL 2026" — refused politely.

### 9.6 Empty tracker handling

If `logs/fault_tracker.json` is empty (e.g. immediately after nuclear reset), Analytics tab renders "Analytics temporarily unavailable" instead of crashing.

### 9.7 LLM output parsing failures

Case generator and coverage assessor both attempt JSON parsing on LLM output. If parsing fails, they fall back to a safe error response and log the raw output for debug. No unhandled exception surfaces to the UI.

---

## 10. Demo Scaffolding vs Production

This section is explicit about what's synthetic in the prototype vs what production wiring requires.

### 10.1 What is synthetic (demo scaffolding)

**Scenarios in Discovery tab:**
The 8+ scenarios in `SCENARIOS` dict (~line 1185) are hand-crafted. In production these are replaced by feedback dicts arriving from the upstream orchestrator.

**Seed cases:**
The 6 baseline case documents in `data/learned_cases/` are synthetic. Production would seed from historical maintenance records (e.g. CMMS export).

**Fault-scenario inline dicts:**
`FAULT_RUL`, `FAULT_ACTION`, `FAULT_ROOT_CAUSE`, `FAULT_LESSONS` (~lines 1415–1510) are lookup tables used by `build_feedback_from_scenario()` to fill demo defaults. In production, these values arrive from Predictive Risk (3), Prescriptive Optimisation (6), and Executor (7) respectively.

**QA thresholds:**
`POST_REPAIR_QA_THRESHOLDS` scaffolding (~line 1548) uses illustrative values per asset/fault. Production should use plant-specific calibrated thresholds.

### 10.2 What is production-shaped (real code paths)

- LangGraph pipeline nodes and routing
- FAISS retrieval + threshold logic
- LLM coverage assessment and case generation
- Chatbot intent classification and multi-turn memory
- Add Case duplicate detection
- Analytics KPI calculations from FAISS metadata
- Langfuse tracing
- All error handling and safety fallbacks

### 10.3 The switch to production

The transition from demo to production is a **data-plumbing exercise**, not a code refactor:

1. Upstream orchestrator sends `feedback` dicts per Section 4.1 to `run_pipeline_demo()`
2. Discovery tab's synthetic-scenario UI can be hidden or repurposed as a manual test harness
3. Seed cases are replaced by CMMS import (script not included)
4. Threshold values in `config.json` may be tuned to plant-specific calibration
5. Domain vocabulary (`FAULT_SYNONYMS`, `ASSET_SYNONYMS`) extended to plant-specific taxonomy

---

## 11. Known Gaps for Production

Deferred items with recommendation and effort estimate. Each entry
below includes the rationale for deferral, the recommended fix, and
an effort estimate for post-deployment work.

| # | Gap | Impact | Effort |
|---|---|---|---|
| 1 | `config/constants.py` orphan — 2 sources of truth for domain data | Maintenance risk when values need updating | 1 day |
| 2 | PATH_C is preview-only; no live enrichment writes | Requires human approval gate for production merges | 2–3 weeks |
| 3 | Coverage scoring is LLM-based (non-deterministic) | Borderline cases score inconsistently across runs | 1 week + calibration data |
| 4 | Streamlit single-file (7,600 lines) — not truly modular | Harder to navigate for new developers | 6–8 hours refactor + testing |
| 5 | No case supersession semantics | Enriched cases don't version older content | 1–2 weeks |
| 6 | No retention/eviction policy | KB grows unbounded; `valid_until` field unused | 1 week |
| 7 | Similarity threshold uniform across fault modes | Some fault modes may need stricter/looser cutoffs | 3 days + calibration |
| 8 | Chatbot "how often" phrasing routes to analytics (cosmetic misrouting) | User must rephrase to "how frequently" | 1 hour |
| 9 | FAISS is flat local index — no scale-out | Single-machine bottleneck at high case volume | 1 week (migrate to managed store) |
| 10 | No closed-loop feedback from Executor outcomes | Cannot track whether recommended actions actually worked | 3 weeks |
| 11 | 60+ inline alias imports in streamlit_app_v3.py (`import re as _re` etc.) | Code hygiene; not a correctness issue | 4 hours + regression |
| 12 | FAISS seed metadata sparse (patched with disk fallback) | Cosmetic — Add Case duplicate warning showed "—" for seeds | 30 min one-shot script |

---

### 11.1 Detailed Tech Debt Log

The following entries were recorded during pre-deployment hardening
(8–14 July 2026). Each documents a decision made under time
constraints and the recommended follow-up.

---

#### Retrieval technique naming precision

**Discovered:** During manager walkthrough — the function
`hybrid_retrieve_cases` was misleadingly named because it
implements a tiered cascade (mutually exclusive branches, first
match returns), not true hybrid retrieval (parallel keyword +
semantic with score fusion).

**Fix applied:** Function renamed to `cascade_retrieve_cases`.
Docstring updated to explicitly state that branches are NOT
combined or score-fused, and that this is NOT hybrid retrieval
in the industry-standard BM25 + vector sense. README updated to
precisely describe which of the three retrieval sites (pipeline,
chatbot, Search Case tab) uses which technique:

| Retrieval site | Technique |
|---|---|
| LangGraph `retrieval_node` | Pure FAISS semantic + fault-mode mismatch penalty |
| Chatbot (`cascade_retrieve_cases`) | Tiered cascade: exact case ID → AST stub → bearing field scan → FAISS semantic fallback |
| Search Case tab (`render_verify_case`) | **Genuinely hybrid**: keyword AND-match + synonym expansion + FAISS semantic, unioned |

**True hybrid retrieval option:** For future work, adding BM25
or another lexical retriever to the pipeline retrieval site
(`check_existing_knowledge`) would enable genuine keyword +
semantic score fusion. Estimated 1 week including calibration
and threshold tuning against the existing fault-mode mismatch
penalty logic.

---

## 12. Appendix: Full Dependency List

From `requirements.txt`:

**Core framework:**
- `streamlit` — Reactive web UI
- `langchain` — LLM abstraction layer
- `langchain-openai` — Azure OpenAI integration for LangChain
- `langgraph` — Node-based pipeline orchestration
- `langgraph-checkpoint` — LangGraph state persistence

**LLM provider:**
- `openai` — OpenAI SDK (used by `langfuse.openai` patched client)
- `azure-identity` — Azure authentication (via LangChain)

**Vector store:**
- `faiss-cpu` — Facebook AI Similarity Search (local index)
- `sentence-transformers` — Text-to-vector embedding model (`all-MiniLM-L6-v2`)
- `numpy` — Vector math

**Observability:**
- `langfuse` — Prompt/token/latency tracing (v4.11+)

**Data handling:**
- `pandas` — Analytics tab tables
- `plotly` — Donut chart, monthly trend
- `python-dateutil` — Timestamp parsing

**Configuration:**
- `python-dotenv` — `.env` file loader
- `pydantic` — Data models (used by LangChain internally; also in `config/settings.py`)

**PDF rendering:**
- `reportlab` — PDF generation for case documents

**Standard library** (no install):
- `json`, `os`, `logging`, `datetime`, `pathlib`, `re`, `hashlib`, `uuid`, `typing`

Run `pip freeze > pinned_requirements.txt` before deployment to snapshot exact versions.

---

## Contact

**Original author:** Shivani Waghmare — Data & AI Summer Analyst, Accenture
**Manager:** Surekha Ananthapalli
**Domain Lead:** Shiva

For questions after handover, reference Section 11 for known gaps and their recommended fixes.

---

*End of README.*
