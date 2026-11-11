-- Version 002: Adaptive Decision Journal v2 (additive, operator-applied).
--
-- Adds durable learner state, significant events, repetition counters, the latest
-- evaluation snapshot, per-worker heartbeats and a journal-mode control row.
-- Nothing here alters, rewrites or deletes adaptive_decision_journal rows; v1
-- retention is unchanged. Applying it does NOT activate v2: the control row is
-- created in mode 'v1' and the explicit cutover (scripts/adaptive_journal_v2.py
-- cutover) switches it. Repeatable; reversible with 002_..._down.sql while in v1.
BEGIN;

DO $$
BEGIN
    IF to_regclass('adaptive_decision_journal') IS NULL THEN
        RAISE EXCEPTION 'apply 001_adaptive_decision_journal.sql first';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS adaptive_journal_control (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL CHECK (mode IN ('v1', 'v2')),
    changed_at TIMESTAMPTZ NOT NULL,
    change_note TEXT NOT NULL DEFAULT '' CHECK (length(change_note) <= 200)
);
INSERT INTO adaptive_journal_control (id, mode, changed_at, change_note)
VALUES (1, 'v1', now(), 'migration 002 applied; v1 remains active')
ON CONFLICT (id) DO NOTHING;

-- One row per learner stream; never subject to journal retention.
CREATE TABLE IF NOT EXISTS adaptive_learning_state (
    stream TEXT PRIMARY KEY CHECK (stream = 'battery'),
    state_version INTEGER NOT NULL CHECK (state_version = 1),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    algorithm_version TEXT NOT NULL,
    proposal_state JSONB NOT NULL CHECK (jsonb_typeof(proposal_state) = 'object'),
    previous_contrast JSONB CHECK (previous_contrast IS NULL OR jsonb_typeof(previous_contrast) = 'object'),
    last_outcome JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(last_outcome) = 'object'),
    last_outcome_fingerprint TEXT,
    last_evidence_fingerprint TEXT,
    last_evaluated_at TIMESTAMPTZ,
    initialized_from TEXT NOT NULL,
    initialized_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- One row, overwritten in place. The full entry is rewritten only when the outcome
-- changes or the snapshot is older than the refresh interval; repeated evaluations
-- update only last_evaluated_at / evaluations_since_snapshot (no TOAST rewrite).
CREATE TABLE IF NOT EXISTS adaptive_latest_evaluation (
    stream TEXT PRIMARY KEY REFERENCES adaptive_learning_state (stream),
    evaluated_at TIMESTAMPTZ NOT NULL,
    last_evaluated_at TIMESTAMPTZ NOT NULL,
    evaluations_since_snapshot BIGINT NOT NULL DEFAULT 0 CHECK (evaluations_since_snapshot >= 0),
    state_revision BIGINT NOT NULL,
    entry JSONB NOT NULL,
    hardware_write_attempted BOOLEAN NOT NULL DEFAULT FALSE CHECK (hardware_write_attempted = FALSE)
);

-- Append-only significant transitions; deterministic event_id makes retries idempotent.
CREATE TABLE IF NOT EXISTS adaptive_decision_events (
    event_id TEXT PRIMARY KEY,
    stream TEXT NOT NULL CHECK (stream = 'battery'),
    event_time TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'JOURNAL_V2_CUTOVER', 'BASELINE_RECORDED', 'DECISION_CHANGED', 'HYPOTHESIS_CREATED', 'HYPOTHESIS_UPDATED',
        'HYPOTHESIS_REJECTED', 'CONFIRMATION_CHANGED', 'HYPOTHESIS_CONFIRMED', 'EVIDENCE_BLOCKED',
        'EVIDENCE_RECOVERED', 'BATTERY_OBSERVATION_CHANGED', 'BATTERY_RESPONSE_CHANGED',
        'WORKER_FAILED', 'WORKER_RECOVERED')),
    state_revision BIGINT,
    worker_id TEXT,
    evidence_fingerprint TEXT,
    algorithm_version TEXT NOT NULL,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    hardware_write_attempted BOOLEAN NOT NULL DEFAULT FALSE CHECK (hardware_write_attempted = FALSE)
);
CREATE INDEX IF NOT EXISTS adaptive_decision_events_time_idx
    ON adaptive_decision_events (event_time DESC, event_id DESC);

-- Compact counters for repeated identical evaluation outcomes.
CREATE TABLE IF NOT EXISTS adaptive_state_repetitions (
    stream TEXT NOT NULL CHECK (stream = 'battery'),
    outcome_fingerprint TEXT NOT NULL,
    outcome JSONB NOT NULL CHECK (jsonb_typeof(outcome) = 'object'),
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    evaluation_count BIGINT NOT NULL CHECK (evaluation_count >= 1),
    new_evidence_count BIGINT NOT NULL CHECK (new_evidence_count >= 0),
    repeated_evidence_count BIGINT NOT NULL CHECK (repeated_evidence_count >= 0),
    last_evidence_fingerprint TEXT,
    PRIMARY KEY (stream, outcome_fingerprint)
);
CREATE INDEX IF NOT EXISTS adaptive_state_repetitions_last_seen_idx
    ON adaptive_state_repetitions (last_seen_at);

-- One row per worker instance; heartbeats never append decision events.
CREATE TABLE IF NOT EXISTS adaptive_worker_heartbeat (
    worker_id TEXT PRIMARY KEY CHECK (worker_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded', 'failing', 'standby')),
    last_heartbeat_at TIMESTAMPTZ NOT NULL,
    last_evaluation_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error TEXT CHECK (last_error IS NULL OR last_error ~ '^[A-Za-z0-9_]{1,80}$'),
    blocking_reason TEXT CHECK (blocking_reason IS NULL OR length(blocking_reason) <= 120),
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    failure_started_at TIMESTAMPTZ
);

-- Mixed-writer protection: once v2 is active, any v1 writer (including old,
-- not-yet-upgraded replicas) fails to INSERT/UPDATE the v1 journal. Retention
-- DELETEs remain allowed, so v1's 30-day retention is unchanged.
CREATE OR REPLACE FUNCTION adaptive_decision_journal_v2_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM adaptive_journal_control WHERE id = 1 AND mode = 'v2') THEN
        RAISE EXCEPTION 'adaptive_decision_journal is read-only while Journal v2 is active'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS adaptive_decision_journal_v2_write_guard ON adaptive_decision_journal;
CREATE TRIGGER adaptive_decision_journal_v2_write_guard
    BEFORE INSERT OR UPDATE ON adaptive_decision_journal
    FOR EACH ROW EXECUTE FUNCTION adaptive_decision_journal_v2_write_guard();

COMMIT;
