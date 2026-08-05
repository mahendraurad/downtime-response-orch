# Running DRO locally

## Install

Python 3.10+ is required. From PowerShell in the repository root:

```powershell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

## Start the backend

```powershell
python -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

- `http://127.0.0.1:8000/docs` for Swagger UI
- `http://127.0.0.1:8000/openapi.json` for frontend client generation
- `http://127.0.0.1:8000/` for the bundled frontend prototype

## Quick API checks

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/pipeline/scenarios
Invoke-RestMethod http://127.0.0.1:8000/api/assets
```

```powershell
$body = @{
  message = "What is the RUL risk?"
  persona = "supervisor"
  context = @{ scenario = "outer_race_fault" }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat `
  -ContentType "application/json" -Body $body
```

## Individual agent demos

```powershell
python scripts\run_agent.py data
python scripts\run_agent.py monitoring
python scripts\run_agent.py failure
python scripts\run_agent.py risk
python scripts\run_agent.py knowledge
python scripts\run_executor_demo.py
```

Agent 6–8 and full-chain behavior are comprehensively exercised through pytest. See `README.md` for the relevant commands and contracts.

Cloud credentials are optional. When no endpoint is configured, deterministic rules remain authoritative and optional LLM advisory paths degrade safely.

## Azure PostgreSQL and LangGraph verification

Keep secrets in the ignored `.env.local` or deployment environment. Required
settings are documented in `.env.example`. For a new database only, enable the
two setup flags once, start the application, and then return both flags to
`false`.

```powershell
python scripts\verify_postgres_langgraph.py
```

The command verifies the PostgreSQL connection, LangGraph checkpoint readback,
approval resume, replay idempotency, atomic HITL claim, and decision readback.
It deletes its verification-only records before exit. `GET /api/health` reports
the selected state-store backends and readiness without returning connection
strings.
