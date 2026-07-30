# Human-in-the-Loop (HITL) Changes

## Completion scope

The orchestrator now pauses and durably records five review gates:

| Gate | Allowed decisions | Authorized selected personas |
|---|---|---|
| Agent 1 remediation | `IMPUTE`, `DROP`, `KEEP` | supervisor, engineer, OT |
| Agent 2 monitoring | `SUPPRESS`, `CONFIRM` | supervisor, engineer |
| Agent 3 diagnosis | `CONFIRM`, `MARK_UNDETERMINED` | engineer |
| Agent 4 advisory | `ACCEPT`, `REJECT`, `MODIFY` | supervisor, engineer |
| Agent 5 knowledge | `FLAG_MANUAL`, `ACCEPT_EMPTY` | engineer, maintenance, safety |

Agent 7 execution approval remains a separate high-impact authorization gate.
The selected persona checks are application policy, not user authentication.
Production deployment must derive the persona from a trusted identity token.

## Persistence and concurrency

Pending pipeline state is serialized behind a narrow repository boundary.
SQLite is the local default; Azure PostgreSQL is selected when
`HITL_REPOSITORY=postgres` (or `auto` with `POSTGRES_URL` configured).

Each decision uses an atomic claim with a configurable lease. This ensures:

- exactly one reviewer wins concurrent submissions;
- process restarts do not lose pending work;
- expired sessions cannot be resolved;
- stale processing claims can be recovered after the lease;
- resolved decisions cannot be replayed;
- gate and action mismatches do not consume a session.

Every completed decision records run ID, gate, action, selected persona,
rationale, and timestamp. Stored pipeline payloads are never returned by the
pending/status APIs.

## Configuration

`config/orchestrator_config.json` controls:

```json
{
  "hitl": {
    "session_ttl_seconds": 1800,
    "claim_lease_seconds": 120,
    "repository_backend": "auto",
    "sqlite_path": "data/hitl_sessions.db",
    "permissions": {}
  }
}
```

For PostgreSQL, set `POSTGRES_URL` as a secret and run with
`HITL_REPOSITORY=postgres`. The PostgreSQL DDL is versioned in
`migrations/001_hitl_postgres.sql`. Do not commit credentials.

## Frontend contract

- `GET /api/pipeline/hitl/pending?persona=engineer`
- `GET /api/pipeline/hitl/{run_id}?persona=engineer`
- Existing gate-specific `POST /api/pipeline/hitl/...` resolution routes

Resolution payloads accept an optional `rationale`. The frontend API module
includes pending-list and status helpers.

## Certification

Repository and API tests cover typed payload round trips, restart recovery,
atomic concurrent claims, completion audit, replay protection, retry after a
released claim, expiry, wrong-gate safety, stale-lease recovery, persona
filtering, rationale recording, payload redaction, and API restart recovery.
