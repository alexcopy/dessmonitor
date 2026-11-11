-- Reverse 002. Refuses while Journal v2 is active: run
-- `scripts/adaptive_journal_v2.py rollback` first, which hands the durable learner
-- state back to the v1 journal. v1 journal rows are never touched here.
BEGIN;

DO $$
BEGIN
    IF to_regclass('adaptive_journal_control') IS NOT NULL
       AND EXISTS (SELECT 1 FROM adaptive_journal_control WHERE id = 1 AND mode = 'v2') THEN
        RAISE EXCEPTION 'Journal v2 is active; run the rollback command before removing v2 tables';
    END IF;
END $$;

DROP TRIGGER IF EXISTS adaptive_decision_journal_v2_write_guard ON adaptive_decision_journal;
DROP FUNCTION IF EXISTS adaptive_decision_journal_v2_write_guard();
DROP TABLE IF EXISTS adaptive_worker_heartbeat;
DROP TABLE IF EXISTS adaptive_state_repetitions;
DROP TABLE IF EXISTS adaptive_decision_events;
DROP TABLE IF EXISTS adaptive_latest_evaluation;
DROP TABLE IF EXISTS adaptive_learning_state;
DROP TABLE IF EXISTS adaptive_journal_control;

COMMIT;
