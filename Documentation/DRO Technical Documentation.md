# Downtime Response Orchestrator (DRO)

## Client Technical Solution Document

**Document version:** 1.0  
**Prepared:** 26 August 2026  
**Solution release:** 0.2

## 1. Purpose and scope

The Downtime Response Orchestrator is a FastAPI and LangGraph application for
bearing predictive maintenance. It accepts telemetry or a governed reference
scenario, validates the evidence, detects abnormal behaviour, identifies a
supported fault, estimates remaining useful life (RUL) and risk, retrieves
cited maintenance guidance, recommends a constrained action, requests human
approval where required, executes approved work through adapters, and learns
from confirmed closure feedback.

This document presents the current solution design and operating parameters. It
covers:

- Agents 1-8 and the bounded Reflexion Agent.
- LangGraph orchestration and natural-language routing.
- Persona-aware responses and persona-level HITL controls.
- FastAPI, chat, pipeline, work-order and websocket contracts.
- Local and cloud-backed persistence.
- Local and Azure AI Search RAG.
- Optional Azure AI Foundry/OpenAI language-model calls.
- LangSmith, Langfuse and local audit controls.
- Current default values and every operationally important configuration.
- Reference data, verification coverage and planned solution evolution.

The user interface is included where it consumes or enforces an application
contract. Authoritative telemetry, diagnosis, RUL, risk and authorization
decisions remain governed by the service layer.

## 2. Design principles

1. **Evidence before inference.** Asset identity alone is not evidence. An
   asset-specific result requires canonical telemetry or an explicitly selected
   registered scenario.
2. **Minimum required agents.** A status question runs Agent 1; a risk question
   runs Agents 1-4; a recommendation runs Agents 1-6. The complete chain is not
   invoked unnecessarily.
3. **Fail closed.** Invalid identity, stale or unusable telemetry, incoherent
   inter-agent handoffs, missing grounded guidance, absent approval, unsafe
   feedback and persistence failures stop downstream work.
4. **Deterministic authority.** LLM output may improve explanation and rationale,
   but cannot replace telemetry, diagnosis rules, RUL calculations, approval or
   confirmed maintenance outcomes.
5. **Human control at material decisions.** Data remediation, borderline
   monitoring, low-confidence diagnosis, missing SOPs, LLM advisories and
   maintenance execution have explicit HITL contracts.
6. **Traceable provenance.** Agent results carry identifiers and versions;
   orchestration produces a pipeline log and optional external traces.
7. **Persona changes presentation, not truth.** The seven personas receive
   different framing and available controls while measurements, decisions and
   citations remain unchanged.

## 3. System architecture

```text
React/Vite or API client
        |
        | POST /api/chat or POST /api/pipeline/run
        v
FastAPI request validation, JWT identity and persona context
        |
        +--> deterministic query planner / clarification manager
        |
        v
LangGraph state machine
  Agent 1 -> Agent 2 -> Agent 3 -> Agent 4 -> Agent 5 -> Agent 6
     |          |          |          |          |          |
     +----------+----------+----------+----------+----------+
                         early-stop / HITL gates
                                                       |
                                                human approval
                                                       v
                                                  Agent 7
                                                       |
                                            confirmed closure feedback
                                                       v
                                                  Agent 8
                                                       |
                                                  Reflexion
                                                       v
Persona-formatted API response + sources + pipeline log + audit/traces
```

Primary solution components:

| Concern | Component |
|---|---|
| Decision intelligence | Eight governed domain agents and bounded response review |
| Orchestration | Conditional workflow and evidence-aware query routing |
| Service interface | Authenticated REST, chat and streaming interfaces |
| Data contracts | Typed telemetry, decision and execution contracts |
| Knowledge | Controlled procedures, approved terminology and learned cases |
| State and audit | Configurable local or PostgreSQL-backed decision state |
| User experience | Persona-aware web application and decision cards |
| Verification | Automated unit, integration, edge and end-to-end certification |

## 4. Agent reference

### 4.1 Agent 1 - Data Foundation

**Responsibility:** Establish whether an incoming telemetry event is trustworthy
and correctly mapped before analytical agents are allowed to use it.

**Input:** A Python dictionary/JSON object containing identity, time, operating
state and bearing signals.

**Main processing:**

1. Copy the caller payload so the input is not mutated.
2. Select a normalization profile from `data_source`.
3. Apply configured aliases and unit conversions.
4. Validate the typed bearing-signal schema.
5. Validate ISO-8601 syntax, timezone, future tolerance and live freshness.
6. Resolve `asset_id -> bearing_id -> channel_id` against master data.
7. Validate completeness, physical bounds, signal quality and cross-field
   consistency.
8. Join asset and bearing context.
9. Calculate the weighted data-quality score.
10. Assign `VALID`, `FLAGGED` or `REJECTED` and an explicit next route.
11. When durable ingestion is requested, enforce idempotency and event order,
    then persist the complete decision for audit.

**Key output:** `TrustedBearingSignal`, including raw normalized data, asset and
bearing context, validation detail, quality score, normalization audit,
provenance versions, persistence status and downstream route.

**Default routes:** `VALID -> monitoring`, `FLAGGED -> data_review`,
`REJECTED -> stop`.

**HITL:** A flagged, remediable event can offer `IMPUTE`, `DROP` or `KEEP`.
Only configured baseline-backed values can be imputed; identity and timestamps
can never be invented.

### 4.2 Agent 2 - Monitoring

**Responsibility:** Detect point anomalies and gradual drift in an eligible,
trusted signal.

**Main processing:**

- Hotelling T-squared uses the configured bearing baselines and available
  non-imputed signals.
- A parallel EWMA detector carries state between readings to identify slow
  degradation.
- Non-running and startup regimes are suppressed rather than diagnosed.
- Alert persistence/cooldown policy prevents unwanted repeated work.
- Decisions can be audited in SQLite.

**Possible outcomes:** `healthy`, `anomaly`, `suppressed`, `ineligible`,
`insufficient_data`, `cooldown`, `duplicate` or `out_of_order`.

**HITL:** A borderline EWMA condition can require `SUPPRESS` or `CONFIRM` before
diagnosis.

### 4.3 Agent 3 - Failure Intelligence

**Responsibility:** Convert a valid anomaly into a governed fault diagnosis.

**Main processing:**

- Match BPFO, BPFI, FTF, BSF or broadband evidence to the versioned fault
  taxonomy.
- Apply threshold, kurtosis, dominance, temperature and priority rules.
- Preserve differential evidence where competing signatures exist.
- Determine ISO stage and business-aware severity.
- Escalate severity for high-criticality or bottleneck assets according to
  configuration.
- Reject tampered identities, impossible values and mismatched provenance.

**Possible outcomes:** `diagnosed`, `undetermined` or `invalid_input`.

**HITL:** Low-confidence classification can require an engineer to `CONFIRM` or
`MARK_UNDETERMINED`.

### 4.4 Agent 4 - Predictive Risk

**Responsibility:** Turn a validated diagnosis into a deterministic RUL, failure
probability, health and business-impact assessment.

**Main processing:**

- Read stage-specific RUL days from `data/fault_taxonomy.json`.
- Blend configured stage probability with Agent 2 anomaly score.
- Reuse Agent 3 severity as risk level.
- Calculate financial exposure from trusted asset context.
- Return a broad monitor band for an undetermined fault.
- Optionally request an LLM advisory only where configured; deterministic
  figures remain authoritative.

**Possible outcomes:** `assessed`, `monitor` or `invalid_input`.

**HITL:** An optional advisory can be `ACCEPT`, `REJECT` or `MODIFY`. Modification
changes only advisory text, not deterministic risk or RUL.

### 4.5 Agent 5 - Knowledge/RAG

**Responsibility:** Retrieve source-grounded inspection, safety and maintenance
guidance applicable to the validated case.

**Runtime backends:**

- `local`: retrieve from the controlled SOP/learned-case corpus under
  `data/sops/`.
- `azure`: require the configured Azure AI Search index.
- `auto`: use Azure when correctly configured; otherwise use local retrieval.

Fault mode, asset type and ISO stage can boost retrieval scores. A source is
required by default. Open-ended conceptual RAG returns inline citation markers
and structured citations. It is prohibited from supplying asset-specific
diagnosis, RUL, risk, cost or action without validated telemetry.

**Possible outcomes:** `grounded`, `no_guidance`, `retrieval_failed` or
`invalid_input`.

**HITL:** When no applicable SOP is found, authorized roles can `FLAG_MANUAL` or
`ACCEPT_EMPTY`. Evidence remains visible in read-only form to other personas.

### 4.6 Agent 6 - Prescriptive Optimization

**Responsibility:** Rank safe, feasible actions from deterministic risk and
grounded knowledge.

**Main processing:**

- Start from risk-level action candidates.
- Check available maintenance windows, parts and contextual constraints.
- Map fault types to replacement or lubrication actions.
- Assign urgency, duration and responsible personnel.
- Build decision-support content: action rationale, confidence, configured illustrative
  costs, parts timing, RUL comparison, historical cases, authority and an A6
  execution trace.
- Optionally use an LLM to rewrite rationale only.

**Possible outcomes:** `ok` or `invalid_input`.

Actions listed in `approval_actions` remain pending. Monitoring-only actions can
be pre-approved by policy.

### 4.7 Agent 7 - Executor

**Responsibility:** Enforce approval and create controlled side effects through
work-order, inventory and notification adapters.

**Main controls:**

- Action must be on the executor allowlist.
- Approval must be present when the recommendation requires it.
- Case identities and recommendation provenance must match.
- Duplicate cases are rejected when durable execution is used.
- Urgency maps to work-order priority.
- Adapter failures are represented as partial/failed steps rather than hidden.

**Possible outcomes:** `success`, `partial`, `blocked`, `failed`,
`invalid_input` or `duplicate`.

The current integration uses controlled CMMS, inventory and notification service adapters.
`/api/executor/run` is the explicit execution boundary; asking the chat to
execute work is not approval.

### 4.8 Agent 8 - Learning and Memory

**Responsibility:** Learn only from an approved execution and validated
technician closure feedback.

**Main controls:**

- Allowed execution states default to `success` and `partial`.
- Feedback timestamps require timezone information.
- Root cause is required.
- Duplicate cases are not learned twice.
- Learned cases are retrievable for history and similar-case questions.
- Validated rows can be exported as JSONL for a future governed training path.

**Possible outcomes:** `learned`, `duplicate`, `invalid_input` or
`persistence_failed`.

An LLM may draft the narrative when enabled and configured, but cannot create a
learned fact that is absent from closure feedback.

### 4.9 Reflexion Agent

**Responsibility:** Validate and refine the user-facing response, not the domain
decision.

Checks include unsupported claims, missing evidence labels, clarity, response
length and alignment with the actual call plan. Refinement is bounded. The
current maximum is three iterations, so no unbounded reflection loop is
possible. LLM-backed reflection is disabled by default; deterministic checks
still run when reflection is enabled.

## 5. Orchestration and routing

### 5.1 Pipeline intents

| User need | Pipeline intent | Minimum call depth |
|---|---|---|
| Current signal/readings | `status` | Agent 1 |
| Anomaly/alert check | `anomaly` | Agents 1-2 |
| Fault/root cause | `diagnosis` | Agents 1-3 |
| RUL/risk/impact | `risk` | Agents 1-4 |
| SOP/guidance | `guidance` | Agents 1-5 |
| Recommended action | `full` | Agents 1-6 |
| Approved execution | `full` + approval | Agents 1-7 |
| Confirmed closure learning | feedback path | Agent 8 after Agent 7 |
| Recent failures/learnings | `history` | Agent 8 |
| Concept definition | `concept` | Approved RAG/glossary; no asset agents |
| Unclassified general query | `general` | Clarification or Reflexion; no fabricated pipeline result |

### 5.2 Early-stop rules

- Agent 1 ineligible or rejected -> stop.
- Status intent -> stop after Agent 1.
- No Agent 2 anomaly, suppressed regime or insufficient data -> stop.
- Anomaly intent -> stop after Agent 2.
- Invalid/ineligible diagnosis -> stop.
- Diagnosis intent -> stop after Agent 3.
- Invalid risk -> stop.
- Risk intent -> stop after Agent 4.
- Ungrounded/no Agent 5 guidance -> stop before recommendation.
- Guidance intent -> stop after Agent 5.
- Invalid recommendation or missing approval -> stop before Agent 7.
- Failed/blocked execution or absent safe feedback -> stop before Agent 8.

### 5.3 Clarification and conversation context

`POST /api/chat` returns `clarification_required=true` when required evidence is
missing. The response includes targeted questions and `missing_fields`. Clients
must resend the returned `conversation_id` on follow-up turns. Context is
bounded by session count, turn count and TTL. Reserved internal keys are
rejected, stale evidence is dropped when the asset changes, and callers can
reset a conversation.

Example:

```json
{
  "message": "What is the RUL of this bearing?",
  "persona": "supervisor"
}
```

Expected behaviour: request the asset and validated signal/scenario. It does not
invent an RUL.

Conceptual contrast:

```json
{
  "message": "What is RUL?",
  "persona": "supervisor"
}
```

Expected behaviour: answer from the approved glossary/RAG without asset data.

### 5.4 Multi-asset behaviour

A multi-asset request is split into independent evidence-backed evaluations.
Every requested asset appears in `multi_asset_results`; one rejected asset does
not suppress the others. A fleet ranking requires an approved `fleet_snapshot`.
Names alone cannot produce a fleet ranking.

## 6. Persona and authorization model

The canonical personas are `supervisor`, `engineer`, `maintenance`, `manager`,
`executive`, `ot` and `safety`. Alias `md` resolves to `executive`.

Persona context is attached to agent calls and response generation. It contains
display name, role, domain, response depth, preferred format, headline focus,
details, actions, tags and suppressed fields. It may change wording and display
depth only.

Application roles currently map to personas as follows:

| Auth role | Selectable personas |
|---|---|
| `admin` | all seven plus alias `md` |
| `plant_ops` | supervisor, engineer, maintenance, OT |
| `leadership` | manager, executive/MD |
| `safety` | safety, supervisor |
| `engineering` | engineer, OT |

The runtime JWT includes subject, role, allowed personas, display name and
expiry. If `DRO_JWT_SECRET` is absent, a random ephemeral secret is used and
tokens do not survive a restart. Deployment policy supplies a stable secret or
managed identity/security service where persistent identity is required.

## 7. HITL contract

### 7.1 Gate permissions and actions

| Gate | Actions | Default authorized personas |
|---|---|---|
| Agent 1 remediation | `IMPUTE`, `DROP`, `KEEP` | Supervisor, Engineer, OT |
| Agent 2 monitoring | `SUPPRESS`, `CONFIRM` | Supervisor, Engineer |
| Agent 3 diagnosis | `CONFIRM`, `MARK_UNDETERMINED` | Engineer |
| Agent 4 advisory | `ACCEPT`, `REJECT`, `MODIFY` | Supervisor, Engineer |
| Agent 5 knowledge | `FLAG_MANUAL`, `ACCEPT_EMPTY` | Engineer, Maintenance, Safety |
| Agent 6/7 execution | approve/reject through execution UI/API | Supervisor, Maintenance, Manager |

The backend returns `access.allowed_personas`, display names, selected persona
and `can_resolve`. React shows the full evidence card to all relevant viewers,
but renders decision controls only for an authorized selected persona. Backend
authorization remains authoritative and returns HTTP 403 for an unauthorized
resolution attempt.

### 7.2 Durability and concurrency

- Session TTL default: 1,800 seconds.
- Claim lease default: 120 seconds.
- SQLite default path: `data/hitl_sessions.db`.
- Backend default: `auto` (PostgreSQL when `POSTGRES_URL` is present, otherwise
  SQLite).
- Claims are atomic and decisions are auditable.
- Invalid actions, wrong gate, expired sessions and replays do not consume an
  unresolved session.

### 7.3 Approval escalation

The decision-support hierarchy defaults to Supervisor -> Manager -> Executive.
Timeouts are urgency-specific: immediate 15 minutes, urgent 1 hour, planned 4
hours and monitor 24 hours. Escalation delivery is configurable for
active-session or durable scheduled operation according to the deployment
operating model.

Escalation hierarchy and execution-gate permissions are independently
configurable policies. Changes to either policy follow the same authority and
change-control review.

### 7.4 Rejection feedback

Recommendation rejection supports five reason codes:

- `diagnosis_wrong`
- `parts_concern`
- `second_opinion`
- `wrong_window`
- `other`

Free text can supplement the code. Two consecutive rejections for the same
asset and fault within 30 days are designed to flag Reliability Engineering.
Validated feedback is routed to Agent 8; it is not treated as proof of a
technical root cause by itself.

## 8. API reference

### 8.1 Principal endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Runtime/backend readiness without secrets |
| `POST` | `/api/auth/login` | Authenticate and obtain JWT |
| `GET` | `/api/auth/me` | Current user and permitted personas |
| `POST` | `/api/chat` | All natural-language questions and follow-ups |
| `POST` | `/api/pipeline/run` | Structured pipeline execution |
| `GET` | `/api/pipeline/scenarios` | Registered demo scenarios |
| `GET` | `/api/assets` | Asset list |
| `GET` | `/api/assets/{asset_id}` | Asset detail |
| `GET` | `/api/assets/{asset_id}/export/pre-repair` | Pre-repair export |
| `GET` | `/api/dashboard/assets` | Dashboard projection |
| `GET` | `/api/pipeline/hitl/pending` | Pending gates permitted for a persona |
| `GET` | `/api/pipeline/hitl/{run_id}` | HITL state and decision audit |
| `POST` | `/api/pipeline/hitl/remediation` | Resolve Agent 1 gate |
| `POST` | `/api/pipeline/hitl/monitoring` | Resolve Agent 2 gate |
| `POST` | `/api/pipeline/hitl/diagnosis` | Resolve Agent 3 gate |
| `POST` | `/api/pipeline/hitl/knowledge` | Resolve Agent 5 gate |
| `POST` | `/api/pipeline/hitl/advisory` | Resolve Agent 4 advisory |
| `POST` | `/api/executor/run` | Execute an approved typed recommendation |
| `POST` | `/api/recommendations/reject` | Capture structured rejection feedback |
| `GET/POST/PATCH` | `/api/workorders` | Current demo work-order operations |
| `GET/POST` | `/api/notifications/...` | Persona inbox and read state |
| `WS` | `/ws/sensors/{asset_id}` | Simulated sensor stream |

Swagger is available at `/docs`; the full machine-readable contract is
`/openapi.json`.

### 8.2 Pipeline request example

```json
{
  "signal": {},
  "scenario": "outer_race_fault",
  "row_index": -1,
  "persona": "engineer",
  "query": "Recommend the maintenance action",
  "thread_id": "meeting-demo-001"
}
```

The response includes `run_id`, `persona_context`, presentation fields,
`pipeline_log`, available structured outputs from Agents 1-8 and all applicable
HITL payloads. `row_index=-1` selects the last scenario row.

### 8.3 Chat request example

```json
{
  "message": "Give the complete action plan for M-121 and P-220",
  "persona": "maintenance",
  "conversation_id": "shift-a",
  "context": {
    "fleet_snapshot": {
      "M-121": {"scenario": "fleet_m_121"},
      "P-220": {"scenario": "fleet_p_220"}
    }
  },
  "conversation_history": []
}
```

The response should label every asset, retain per-asset evidence/call logs and
surface clarifications where an asset lacks approved evidence.

### 8.4 Error model

- Request validation, unknown resource, authorization and defined state errors
  use HTTP 4xx responses.
- Unexpected exceptions use a sanitized `INTERNAL_ERROR` response with an
  `error_id`; raw stack traces and secrets are not returned.
- Optional LLM/tracing failures degrade without changing deterministic results.
- Required Azure RAG or PostgreSQL configuration fails explicitly rather than
  silently pretending the cloud service is active.

## 9. Canonical telemetry contract

Minimum identity/time fields:

```json
{
  "telemetry_id": "TEL-1001",
  "timestamp_utc": "2026-08-26T10:00:00Z",
  "asset_id": "AST_MTR_001",
  "bearing_id": "BRG_001",
  "channel_id": "CH_001"
}
```

Recommended analytical payload:

```json
{
  "telemetry_id": "TEL-1001",
  "timestamp_utc": "2026-08-26T10:00:00Z",
  "asset_id": "AST_MTR_001",
  "bearing_id": "BRG_001",
  "channel_id": "CH_001",
  "rpm": 1780,
  "load_pct": 75,
  "machine_state": "running",
  "startup_shutdown_flag": false,
  "vib_rms_mm_s": 2.2,
  "vib_peak_g": 1.0,
  "kurtosis": 2.8,
  "temp_c": 48,
  "bpfo_energy": 0.8,
  "bpfi_energy": 0.5,
  "bsf_energy": 0.2,
  "ftf_energy": 0.1,
  "motor_current_a": 37,
  "voltage_v": 415,
  "current_deviation": 0.2,
  "signal_quality_score": 1.0,
  "data_source": "historian"
}
```

Configured aliases include `vibration_rms -> vib_rms_mm_s`,
`temperature_c -> temp_c` and `speed_rpm -> rpm`. Configured conversions include
Fahrenheit to Celsius and revolutions/second to RPM. OPC-UA aliases include
`MotorSpeed`, `BearingTemp` and `VibrationRMS`; Event Hub aliases include
`assetId`, `bearingId`, `channelId` and `eventTime`. The canonical field wins
when both forms are supplied.

## 10. Persistence and external adapters

| Concern | Local/default | Enterprise option |
|---|---|---|
| Trusted Agent 1 decisions | SQLite `data/trusted_signals.db` when durable API used | Implement repository adapter/PostgreSQL if required |
| Monitoring decisions | SQLite `data/monitoring_decisions.db` | Replaceable repository boundary |
| EWMA state | JSON `data/ewma_state.json` | External state store adapter required for scale-out |
| HITL sessions/audit | `auto`: SQLite unless PostgreSQL URL exists | Azure PostgreSQL via `HITL_REPOSITORY=postgres` |
| LangGraph checkpoints | Disabled (`none`) | PostgreSQL via `CHECKPOINT_BACKEND=postgres` |
| Execution results | SQLite `data/execution_results.db` | Enterprise CMMS repository/adapters |
| Learned cases | JSON `data/learned_cases.json` | Governed enterprise memory store |
| Training export | JSONL `data/learning_training_rows.jsonl` | Governed ML pipeline |
| Work-order API | In-memory runtime list | CMMS/EAM adapter |
| Inventory/notifications | Controlled service adapters | ERP/MRO and enterprise notification services |

The runtime work-order register starts empty. Work orders appear only when they
are created through the authorized service or approved execution flow; validation
fixtures are never loaded into the live runtime register.

## 11. RAG, LLM and citations

### 11.1 Governed RAG

Default `RAG_BACKEND=local`. Local retrieval uses approved PDFs and glossary
content under `data/sops/`. Azure retrieval uses the configured index and may
authenticate with a query/admin key or RBAC/managed identity.

The current Azure field defaults in `.env.example` are:

| Setting | Default |
|---|---|
| Content field | `chunk` |
| Source/title field | `title` |
| Chunk ID field | `chunk_id` |
| Source URI/parent field | `parent_id` |
| Vector field | `text_vector` |
| Fault/asset/stage metadata fields | disabled with `none` |

The deployed index previously discussed uses a 3,072-dimension vector. Vector
generation/indexing is an ingestion concern; the runtime retriever uses the
configured index schema.

### 11.2 Optional LLM paths

The LLM client prefers `AZURE_AI_*`, with `AZURE_OPENAI_*` as a legacy
alternative. When no complete endpoint/key/deployment is configured, the
system remains operational in deterministic mode.

Features configured `enabled=true` are still conditional on a usable client:

- Agent 4 advisory for undetermined cases.
- Agent 5 open-ended answer synthesis.
- Agent 6 rationale rewriting.
- Agent 8 narrative drafting.

Reflexion LLM use is independently disabled by default. No LLM path may alter
the authoritative rule outputs or perform approval.

## 12. Observability and audit

### LangSmith

Default tracing is off. With `LANGSMITH_TRACING=true`, traces are nested from
Chat API to orchestrator, individual agents and Reflexion. Inputs, outputs,
routes, latency and errors may be sent, so only an approved workspace should be
used for plant data.

### Langfuse

Dual tracing is available with `LANGFUSE_TRACING=true`, public/secret keys and
base URL. Default environment label is `development`.

### Local audit

The orchestrator writes JSONL to `logs/orchestrator_audit.jsonl` by default.
`include_payloads=false` avoids storing complete request/response payloads in
the local audit by default. Agent-specific logging flags control valid,
flagged, rejected, retrieval, diagnosis, recommendation, execution and learning
records.

Tracing failure is non-authoritative and must not change the API decision.

## 13. Configuration reference

All JSON changes require an application restart because configuration is loaded
and cached at startup.

### 13.1 Data Foundation defaults - `config/dfa_config.json`

| Parameter | Default | Meaning |
|---|---:|---|
| `validation.min_signal_quality_score` | `0.50` | Minimum trusted historian quality |
| `validation.max_rpm` | `20000` | Global RPM ceiling |
| `validation.max_load_pct` | `110` | Load ceiling |
| `validation.min_kurtosis` | `1.0` | Physical lower bound |
| `freshness.require_timezone` | `true` | Require timestamp timezone |
| `freshness.max_future_seconds` | `30` | Future clock-skew tolerance |
| `freshness.max_age_seconds` | `300` | Live record age limit |
| `freshness.live_sources` | `live, stream, event_hub` | Sources subject to age limit |
| `quality_score.flagged_threshold` | `0.80` | Minimum overall score before flagging |
| Quality weights | `0.25/0.20/0.20/0.15/0.10/0.10` | Asset, bearing, completeness, range, accuracy, consistency |
| `remediation.default_action` | `FLAG_FOR_REVIEW` | Unattended flagged-record policy |
| `repository.reject_duplicates` | `true` | Enforce telemetry idempotency |
| `repository.flag_out_of_order` | `true` | Review late events |
| Fallback vibration bounds | `0..50 mm/s` | Used if bearing master lacks bounds |
| Fallback temperature bounds | `-10..150 C` | Used if bearing master lacks bounds |

### 13.2 Monitoring defaults - `config/monitoring_config.json`

| Parameter | Default |
|---|---:|
| Method | `hotelling_t2` |
| T-squared anomaly threshold | `0.85` |
| Minimum signals for T-squared | `2` |
| Trigger evidence z-score | `3.0` |
| Exclude imputed values | `true` |
| Running states | `running, fault` |
| Minimum running RPM | `50` |
| Rated RPM tolerance | `15%` |
| Low/high load boundaries | `40% / 85%` |
| EWMA enabled | `true` |
| EWMA alpha / control limit | `0.2 / 3.0` |
| Consecutive anomalies required | `1` |
| Cooldown | `0 seconds` |
| Confidence weights | quality `0.34`, coverage `0.33`, data quality `0.33` |

### 13.3 Diagnosis and risk defaults

| Parameter | Default |
|---|---|
| Agent 3 base severity | Stage 1 low; Stage 2 medium; Stage 3 high |
| Severity escalation | High criticality and bottleneck each enabled |
| Broadband vibration elevation ratio | `1.3` |
| Diagnosis confidence blend | anomaly `0.5`, match strength `0.5` |
| Failure stage base | Stage 0 `0.10`, 1 `0.30`, 2 `0.60`, 3 `0.85` |
| Failure probability blend | stage `0.60`, anomaly `0.40` |
| Undetermined RUL monitor band | `45..999 days` |
| LLM fallback | enabled, undetermined only, temperature `0.2`, max tokens `500`, timeout `20s` |
| Low-confidence LLM trigger | `0` (disabled) |
| Advisory HITL | enabled |

Stage-specific RUL defaults come from the taxonomy:

| Fault | Stage 1 | Stage 2 | Stage 3 |
|---|---:|---:|---:|
| Outer race | 90 d | 30 d | 7 d |
| Inner race | 80 d | 25 d | 5 d |
| Lubrication | 60 d | 20 d | 5 d |
| Imbalance (dormant detector) | 120 d | 60 d | 15 d |
| Misalignment (dormant detector) | 100 d | 40 d | 10 d |
| Cage | 75 d | 20 d | 4 d |
| Rolling element | 90 d | 30 d | 10 d |

### 13.4 Knowledge, recommendation, execution and learning defaults

| Parameter | Default |
|---|---|
| Knowledge top K / minimum score | `5 / 0.45` |
| Retrieval boosts | fault `0.25`, asset `0.15`, ISO stage `0.10` |
| Source required / generic allowed | `true / true` |
| Open-ended top K / minimum | `8 / 0.05` |
| Open-ended max passages / answer chars | `3 / 1600` |
| Agent 6 action by critical risk | `stop_and_replace` first |
| Agent 6 action by high risk | `repair_next_planned_stop` first |
| Agent 6 action by medium risk | `inspect_next_shift` first |
| Agent 6 action by low risk | `continue_monitoring` first |
| Urgency mapping | critical immediate; high urgent; medium planned; low monitor |
| Approval-required actions | repair, derate, stop/replace, lubrication service |
| Agent 6 rationale limit | `800 characters` |
| Executor escalated urgencies | immediate, urgent |
| Executor duplicate rejection | `true` |
| Learning accepted execution states | success, partial |
| Learning requires timezone/root cause | `true / true` |
| Learning retrieval top K/minimum | `5 / 0.15` |
| Learning narrative limit | `2000 characters` |

### 13.5 Chat, reflection, HITL and decision support defaults

| Parameter | Default |
|---|---|
| Chat maximum message | `2000 characters` |
| Default persona | `supervisor` |
| Context sessions / turns | `500 / 12` |
| Context TTL | `1800 seconds` |
| Auto-load registered demo scenario | `true` |
| Reflection enabled / LLM enabled | `true / false` |
| Max response / refinements | `4000 chars / 3` |
| HITL TTL / claim lease | `1800s / 120s` |
| HITL repository | `auto` |
| Audit payloads | `false` |
| Decision-support currency | `USD` |
| Deferred failure multiplier | `7.0` |
| Authority limits | Supervisor `$100k`; Manager `$1m`; Executive `$5m` |
| Escalation enabled | `true` |
| Immediate/urgent/planned/monitor timeout | `15m / 1h / 4h / 24h` |

Cost values are explicitly illustrative configured figures. Generic approved cost equals configured
parts plus labour; deferred cost uses the configured multiplier. `AST_MTR_001`
has an override: approved `$48,000`, deferred `$2,016,000`, and deferred hourly
cost `$7,500`.

### 13.6 Synthetic fleet defaults

`config/synthetic_fleet_config.json` version is `2026.08.2` with 12 history
points per generated asset. It defines 24 generated assets appended to six
original assets and 15 complex certification cases. Regenerate with:

```powershell
python scripts/generate_synthetic_fleet.py
```

## 14. Environment variable reference

| Variable | Default | Required when |
|---|---|---|
| `AZURE_AI_ENDPOINT/KEY/DEPLOYMENT` | empty | Azure AI Foundry LLM calls |
| `AZURE_OPENAI_ENDPOINT/KEY/DEPLOYMENT` | empty | Legacy Azure OpenAI alternative |
| `OPENAI_API_KEY` | empty | Direct public OpenAI integrations only |
| `RAG_BACKEND` | `local` | Set `azure` or `auto` for AI Search |
| `AZURE_SEARCH_AUTH` | `auto` | `key` or `rbac` can be forced |
| `AZURE_SEARCH_*` fields | see Section 11 | Azure retrieval |
| `POSTGRES_URL` | empty | PostgreSQL HITL/checkpoints |
| `HITL_REPOSITORY` | `auto` | `auto`, `sqlite` or `postgres` |
| `CHECKPOINT_BACKEND` | `none` | Set `postgres` for durable resume |
| `POSTGRES_*_SETUP` | `false` | One-time schema setup only |
| `POSTGRES_POOL_MIN/MAX_SIZE` | `1 / 10` | PostgreSQL pools |
| `LANGSMITH_TRACING` | `false` | LangSmith tracing |
| `LANGSMITH_ENDPOINT` | LangSmith cloud URL | Alternate LangSmith host |
| `LANGSMITH_PROJECT` | `DRO` | Trace project |
| `LANGFUSE_TRACING` | `false` | Langfuse tracing |
| `LANGFUSE_BASE_URL` | Langfuse cloud URL | Alternate Langfuse host |
| `LANGFUSE_TRACING_ENVIRONMENT` | `development` | Trace environment label |
| `DRO_JWT_SECRET` | ephemeral | Stable production tokens |
| `DRO_JWT_EXPIRE_HOURS` | `8` | Token lifetime |
| `LOG_LEVEL` | `INFO` | Runtime log verbosity |
| `DATA_DIR` | `./data` | Deployment data root where supported |
| `VITE_API_BASE_URL` | empty/proxy | Separately hosted frontend API origin |

Never commit secrets. Process/container values take precedence over `.env` and
`.env.local`.

## 15. Reference data and examples

Current generated coverage:

- 30 assets.
- 60 bearings and 60 sensor channels.
- 55 usable telemetry scenarios plus a `_comment` metadata property.
- 18 work-order fixture records, though runtime starts with none loaded.
- Motors, pumps, conveyors, gearboxes, fans and one compressor.
- Healthy, startup, dropout, low-quality, thermal, electrical, load and bearing
  fault cases.
- 15 complex cases covering competing signatures, threshold boundaries,
  non-drive-end identity, regime suppression, mapping mismatch and sensor limits.

Example behaviours:

| Scenario | Expected path |
|---|---|
| `healthy` | Agent 1 valid -> Agent 2 healthy -> stop |
| `outer_race_fault` | Agents 1-6 -> approval if action requires it |
| `signal_dropout` | Agent 1 flagged -> remediation HITL/data review |
| `unknown_asset` | Agent 1 rejected -> stop; no diagnosis fabricated |
| `startup_filter` | Agent 2 suppressed -> stop |
| `complex_asset_channel_mismatch` | Agent 1 rejects identity relationship |
| `complex_outer_kurtosis_boundary` | anomaly but diagnosis remains undetermined |

## 16. Running and verification

```powershell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
python -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

Useful checks:

```powershell
python -m pytest -q
python -m pytest tests/test_agent1_to_agent8_integration.py -q
python -m pytest tests/test_orchestrator_chat_reflexion.py -q
python scripts/verify_postgres_langgraph.py
python scripts/run_chat_qc.py --disable-llm
```

The automated verification portfolio comprises **1,020 tests** across agent
logic, edge conditions, adjacent-agent handoffs, end-to-end orchestration,
conversation management, multi-asset processing, human decisions, identity,
persona presentation, governed knowledge, state persistence and service
contracts. Release-specific execution evidence should accompany each controlled
deployment.

The versioned QC pack contains 50 difficult chat questions and can write Excel
results. LangSmith dataset publishing is supported by `scripts/run_chat_qc.py`.

## 17. Future evolution

The next phase will extend governed enterprise connectivity, centralized
identity and durable scheduling while retaining the current evidence, approval
and audit controls. Operational thresholds, economics and knowledge sources
will continue to be calibrated through the established change-control process.

## 18. Change control checklist

Before changing a default:

1. Identify the owning JSON/environment key.
2. Record business owner and rationale.
3. Validate type/range and cross-config consistency.
4. Restart the application.
5. Run agent unit, adjacent integration and end-to-end tests.
6. Re-run relevant QC questions.
7. Confirm persona presentation and HITL permissions.
8. Confirm audit/tracing does not expose secrets or sensitive plant data.
9. Version the change and update both technical and business documentation.
