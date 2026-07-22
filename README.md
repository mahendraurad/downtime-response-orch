# Downtime Response Orchestrator (DRO)

DRO is a FastAPI and LangGraph backend for bearing predictive maintenance. It validates telemetry, detects anomalies, diagnoses bearing faults, estimates risk and RUL, retrieves cited maintenance guidance, recommends constrained actions, executes approved work through mock adapters, and learns from confirmed outcomes.

The repository is ready for frontend integration in deterministic local mode. Azure credentials are optional.

## Current status

| Capability | Status |
|---|---|
| Agents 1–8 | Implemented |
| LangGraph conditional orchestration | Implemented |
| Persona-aware pipeline and chat APIs | Implemented |
| Human approval and HITL gates | Implemented |
| Reflexion and graceful error envelopes | Implemented |
| Local persistence and mock connectors | Implemented |
| Automated backend tests | `659 passed` |
| React frontend from `feature/8agents_frontend` | Integrated on `dev` |
| Real CMMS/ERP/historian and Azure services | Next phase |

## Agent flow

```text
Raw telemetry
  → Agent 1: Data Foundation
  → Agent 2: Monitoring
  → Agent 3: Failure Intelligence
  → Agent 4: Predictive Risk
  → Agent 5: Knowledge/RAG
  → Agent 6: Prescriptive Optimization
  → approval gate
  → Agent 7: Executor
  → confirmed closure feedback
  → Agent 8: Learning & Memory
  → Reflexion: user-facing response validation
```

The orchestrator stops early when data is ineligible, telemetry is healthy, an upstream handoff is invalid, guidance is ungrounded, approval is missing, execution fails, or closure feedback is unsafe.

## Repository layout

```text
config/       Configurable agent, orchestration, retrieval and safety policies
data/         Synthetic masters, telemetry scenarios and SOP corpus
frontend/     Legacy prototype plus the React/Vite application
Reference/    Change records, historical developer docs and legacy diagnostics
scripts/      Agent and demo runners
src/agents/   Agents 1–8 plus bounded Reflexion Agent
src/api/      FastAPI application and persona formatting
src/orchestrator/ LangGraph state, routing, graph and open-question planner
src/schemas/  Typed Pydantic contracts shared between agents
src/tools/    Validators, repositories, adapters, retrieval and audit utilities
tests/        Unit, edge, API, orchestration and Agent 1→8 integration tests
```

## Setup

Python 3.10 or newer is required. Python 3.14 is currently tested locally.

```powershell
git clone <repository-url>
cd <repository-folder>
.\setup.ps1
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Manual setup:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Do not commit `.env`. Copy `.env.example` only when testing optional Azure-backed LLM/search/storage integrations.

## Start the backend

```powershell
python -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

Useful URLs:

- API documentation: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
- Existing frontend: `http://127.0.0.1:8000/`
- Health check: `GET /api/assets`

## Start the React frontend

Node.js 18 or newer is recommended. Keep the backend running on port 8000,
then open a second PowerShell terminal:

```powershell
cd frontend/react-app
npm ci
npm run dev
```

Open `http://localhost:3000`. The Vite development server proxies `/api` and
`/ws` to `http://localhost:8000`. For a separately hosted API, set
`VITE_API_BASE_URL` to its origin before building or starting Vite.

## Frontend integration

The frontend can generate its API client from `/openapi.json`. The principal endpoints are:

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/chat` | Open-question routing and persona-aware response |
| `POST` | `/api/pipeline/run` | Run telemetry through the required agent depth |
| `GET` | `/api/pipeline/scenarios` | List bundled demo scenarios |
| `GET` | `/api/assets` | Asset cards/list |
| `GET` | `/api/assets/{asset_id}` | Asset detail |
| `POST` | `/api/pipeline/hitl/remediation` | Resolve Agent 1 data-quality gate |
| `POST` | `/api/pipeline/hitl/monitoring` | Resolve Agent 2 borderline event |
| `POST` | `/api/pipeline/hitl/diagnosis` | Resolve Agent 3 low-confidence diagnosis |
| `POST` | `/api/pipeline/hitl/knowledge` | Resolve missing-SOP gate |
| `POST` | `/api/executor/run` | Execute a typed recommendation after approval |
| `GET` | `/api/notifications/counts` | Unread counts for frontend personas |
| `GET` | `/api/notifications/{persona_id}` | Persona notification inbox |
| `POST` | `/api/notifications/{persona_id}/read` | Mark a persona inbox as read |
| `GET/POST/PATCH` | `/api/workorders` | Demo work-order UI operations |
| `WS` | `/ws/sensors/{asset_id}` | Simulated live sensor stream |

### Chat example

```javascript
const response = await fetch("http://127.0.0.1:8000/api/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    message: "What is the RUL risk?",
    persona: "supervisor",
    context: { scenario: "outer_race_fault" }
  })
});
const result = await response.json();
```

Chat returns `run_id`, `intent`, `response`, `details`, `actions`, `call_plan`, actual `pipeline_log`, sources, reflection status and available structured agent outputs. Asking to execute work in chat does not constitute approval.

Recent-failure and lessons-learned questions route directly to Agent 8's
validated closed-case memory. Arbitrary canonical telemetry supplied in
`context.signal` routes by question intent and need not match a demo scenario.

### Pipeline example

```javascript
const response = await fetch("http://127.0.0.1:8000/api/pipeline/run", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    signal: {},
    scenario: "outer_race_fault",
    row_index: -1,
    persona: "engineer",
    query: "recommend maintenance action"
  })
});
```

The response includes persona-ready presentation fields plus `fault_diagnosis`, `risk_assessment`, `knowledge_guidance`, `recommendation`, `execution_result`, `learned_case`, HITL fields, and the ordered pipeline log.

## Expected input values

Agent 1’s canonical telemetry contract requires:

```json
{
  "telemetry_id": "TEL-1001",
  "timestamp_utc": "2026-07-20T10:00:00Z",
  "asset_id": "AST_MTR_001",
  "bearing_id": "BRG_001",
  "channel_id": "CH_001",
  "rpm": 1780,
  "load_pct": 75,
  "machine_state": "running",
  "startup_shutdown_flag": false,
  "vib_rms_mm_s": 2.2,
  "kurtosis": 2.8,
  "temp_c": 48,
  "bpfo_energy": 0.8,
  "bpfi_energy": 0.5,
  "signal_quality_score": 1.0,
  "data_source": "historian"
}
```

Identity and timestamp fields are mandatory. Supported source profiles also normalize configured OPC-UA and Event Hub aliases.

Expected controlled values include:

| Contract | Values |
|---|---|
| Agent 1 validation | `VALID`, `FLAGGED`, `REJECTED` |
| Agent 2 status | `healthy`, `anomaly`, `suppressed`, `ineligible`, `insufficient_data`, `cooldown`, `duplicate`, `out_of_order` |
| Agent 3 diagnosis | `diagnosed`, `undetermined`, `invalid_input` |
| Agent 4 assessment | `assessed`, `monitor`, `invalid_input` |
| Agent 5 guidance | `grounded`, `no_guidance`, `retrieval_failed`, `invalid_input` |
| Agent 6 recommendation | `ok`, `invalid_input` |
| Agent 7 execution | `success`, `partial`, `blocked`, `failed`, `invalid_input`, `duplicate` |
| Agent 8 learning | `learned`, `duplicate`, `invalid_input`, `persistence_failed` |

## Expected outputs and errors

- Healthy telemetry normally stops after Monitoring and creates no maintenance case.
- A supported fault can reach a grounded recommendation.
- Repair/replacement recommendations remain `pending` until approved.
- Execution runs only through the dedicated approval-aware path.
- Learning requires completed execution and matching technician feedback.
- Defined validation/not-found errors return HTTP 4xx responses.
- Unexpected errors return a sanitized `INTERNAL_ERROR` envelope with a correlation `error_id`; raw exception details are not returned.

## Testing

```powershell
# Everything
python -m pytest -q

# Orchestrator and chat/API wiring
python -m pytest tests/test_end_to_end_graph.py tests/test_orchestrator_chat_reflexion.py -q

# Complete Agent 1→8 chain
python -m pytest tests/test_agent1_to_agent8_integration.py -q
```

Current certification:

```text
659 passed
0 failed
```

The test-client stack currently emits one non-functional Starlette/httpx deprecation warning.

## Configuration

All policy files live under `config/`. Important frontend-visible controls include freshness/routing, alert cooldown, retrieval grounding, recommendation approval actions, execution allowlists, learning validation, chat length and reflection limits.

Cloud credentials are never required for deterministic local execution. LLM output is non-authoritative and cannot alter telemetry facts, risk calculations, action approval or confirmed maintenance outcomes.

## Next steps

1. Add PostgreSQL LangGraph checkpointing and durable HITL sessions.
2. Add authentication, persona authorization and approval permissions.
3. Replace local/mocked historian, CMMS, inventory and notification adapters.
4. Move SOP and learned-case retrieval to Azure Blob Storage and Azure AI Search.
5. Add governed multi-turn conversation memory and cited general-knowledge RAG.
6. Add centralized immutable audit storage, tracing, metrics and operational alerts.
7. Validate real LLM endpoints against prompt-injection and grounding evaluations.
8. Run a shadow pilot against at least three historical or live bearing events.

Detailed historical implementation notes are indexed in [`Reference/README.md`](Reference/README.md).
