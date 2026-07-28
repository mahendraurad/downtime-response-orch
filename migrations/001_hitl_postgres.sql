CREATE TABLE IF NOT EXISTS hitl_sessions (
    run_id TEXT PRIMARY KEY,
    gate TEXT NOT NULL,
    persona TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    claim_token TEXT,
    claimed_at TIMESTAMPTZ,
    claimed_by TEXT,
    action TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hitl_pending
    ON hitl_sessions(status, gate, expires_at);

CREATE TABLE IF NOT EXISTS hitl_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    gate TEXT NOT NULL,
    action TEXT NOT NULL,
    persona TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_hitl_decision_run
    ON hitl_decisions(run_id, decided_at);
