CREATE TABLE IF NOT EXISTS trusted_signals (
    telemetry_id TEXT PRIMARY KEY,
    bearing_id TEXT NOT NULL,
    event_epoch DOUBLE PRECISION,
    validation_status TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_trusted_bearing_time
    ON trusted_signals(bearing_id, event_epoch);

CREATE TABLE IF NOT EXISTS monitoring_decisions (
    decision_id BIGSERIAL PRIMARY KEY,
    telemetry_id TEXT NOT NULL,
    bearing_id TEXT NOT NULL,
    status TEXT NOT NULL,
    processed_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_monitoring_telemetry
    ON monitoring_decisions(telemetry_id, decision_id DESC);

CREATE TABLE IF NOT EXISTS executions (
    case_id TEXT PRIMARY KEY,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS learned_cases (
    case_id TEXT PRIMARY KEY,
    asset_id TEXT,
    fault_mode TEXT,
    created_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_learned_cases_asset_created
    ON learned_cases(asset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_learned_cases_fault
    ON learned_cases(fault_mode);

CREATE TABLE IF NOT EXISTS rejection_feedback (
    rejection_id BIGSERIAL PRIMARY KEY,
    case_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    fault_mode TEXT NOT NULL,
    decision TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL,
    UNIQUE(case_id, decision)
);
CREATE INDEX IF NOT EXISTS ix_rejection_feedback_sequence
    ON rejection_feedback(asset_id, fault_mode, decided_at DESC, rejection_id DESC);

CREATE TABLE IF NOT EXISTS chat_learning_observations (
    observation_id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    asset_id TEXT,
    asset_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    learning_kind TEXT NOT NULL,
    intent TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_learning_asset_created
    ON chat_learning_observations(asset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_chat_learning_kind
    ON chat_learning_observations(learning_kind);

CREATE TABLE IF NOT EXISTS work_orders (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    asset_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_work_orders_status
    ON work_orders(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_work_orders_asset
    ON work_orders(asset_id);

CREATE TABLE IF NOT EXISTS curated_signals (
    telemetry_id TEXT PRIMARY KEY,
    asset_id TEXT,
    bearing_id TEXT,
    validation_status TEXT,
    remediation_action TEXT,
    curated_at TIMESTAMPTZ,
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_curated_signals_asset
    ON curated_signals(asset_id, curated_at DESC);

CREATE TABLE IF NOT EXISTS chat_contexts (
    conversation_id TEXT PRIMARY KEY,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_contexts_updated
    ON chat_contexts(updated_at DESC);
