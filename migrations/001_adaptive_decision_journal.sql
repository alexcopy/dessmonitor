-- Version 001: operator-applied, fail-closed decision journal.
-- Repeatable for the exact compatible schema; incompatible existing tables abort
-- the transaction before the worker can use them.
BEGIN;

CREATE TABLE IF NOT EXISTS adaptive_decision_journal (
    evaluation_id TEXT PRIMARY KEY,
    evaluated_at TIMESTAMPTZ NOT NULL,
    entry JSONB NOT NULL,
    hardware_write_attempted BOOLEAN NOT NULL DEFAULT FALSE
        CHECK (hardware_write_attempted = FALSE)
);

DO $$
DECLARE
    column_info RECORD;
    relation_kind "char";
    has_primary_key BOOLEAN;
    has_hardware_guard BOOLEAN;
BEGIN
    SELECT c.relkind INTO relation_kind
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema() AND c.relname = 'adaptive_decision_journal';
    IF relation_kind IS NULL OR relation_kind NOT IN ('r', 'p') THEN
        RAISE EXCEPTION 'adaptive_decision_journal is not a table in schema %', current_schema();
    END IF;

    FOR column_info IN
        SELECT column_name, data_type, is_nullable, column_default
          FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'adaptive_decision_journal'
    LOOP
        IF column_info.column_name = 'evaluation_id'
           AND (column_info.data_type <> 'text' OR column_info.is_nullable <> 'NO' OR column_info.column_default IS NOT NULL) THEN
            RAISE EXCEPTION 'incompatible adaptive_decision_journal.evaluation_id';
        ELSIF column_info.column_name = 'evaluated_at'
           AND (column_info.data_type <> 'timestamp with time zone' OR column_info.is_nullable <> 'NO' OR column_info.column_default IS NOT NULL) THEN
            RAISE EXCEPTION 'incompatible adaptive_decision_journal.evaluated_at; TIMESTAMPTZ is required';
        ELSIF column_info.column_name = 'entry'
           AND (column_info.data_type <> 'jsonb' OR column_info.is_nullable <> 'NO' OR column_info.column_default IS NOT NULL) THEN
            RAISE EXCEPTION 'incompatible adaptive_decision_journal.entry';
        ELSIF column_info.column_name = 'hardware_write_attempted'
           AND (column_info.data_type <> 'boolean' OR column_info.is_nullable <> 'NO'
                OR column_info.column_default IS NULL
                OR column_info.column_default NOT IN ('false', 'false::boolean')) THEN
            RAISE EXCEPTION 'incompatible adaptive_decision_journal.hardware_write_attempted';
        END IF;
    END LOOP;

    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'adaptive_decision_journal'
           AND column_name IN ('evaluation_id', 'evaluated_at', 'entry', 'hardware_write_attempted')) <> 4 THEN
        RAISE EXCEPTION 'adaptive_decision_journal is missing required columns';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'adaptive_decision_journal'
           AND column_name NOT IN ('evaluation_id', 'evaluated_at', 'entry', 'hardware_write_attempted')
           AND is_nullable = 'NO' AND column_default IS NULL
    ) THEN
        RAISE EXCEPTION 'adaptive_decision_journal has an extra required column without a default';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = current_schema() AND t.relname = 'adaptive_decision_journal'
           AND c.contype = 'p' AND pg_get_constraintdef(c.oid) = 'PRIMARY KEY (evaluation_id)'
    ) INTO has_primary_key;
    IF NOT has_primary_key THEN
        RAISE EXCEPTION 'adaptive_decision_journal requires PRIMARY KEY (evaluation_id)';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = current_schema() AND t.relname = 'adaptive_decision_journal'
           AND c.contype = 'c' AND c.convalidated
           AND regexp_replace(lower(pg_get_constraintdef(c.oid)), '[" ()]', '', 'g')
               IN ('checkhardware_write_attempted=false', 'checkhardware_write_attempted=false::boolean')
    ) INTO has_hardware_guard;
    IF NOT has_hardware_guard THEN
        IF EXISTS (SELECT 1 FROM adaptive_decision_journal WHERE hardware_write_attempted IS DISTINCT FROM FALSE) THEN
            RAISE EXCEPTION 'existing journal rows violate the hardware-write guard';
        END IF;
        ALTER TABLE adaptive_decision_journal
            ADD CONSTRAINT adaptive_decision_journal_hardware_write_guard
            CHECK (hardware_write_attempted = FALSE) NOT VALID;
        ALTER TABLE adaptive_decision_journal
            VALIDATE CONSTRAINT adaptive_decision_journal_hardware_write_guard;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS adaptive_decision_journal_evaluated_at_idx
    ON adaptive_decision_journal (evaluated_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
          JOIN pg_class t ON t.oid = i.indrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
          JOIN pg_class x ON x.oid = i.indexrelid
          JOIN pg_am am ON am.oid = x.relam
         WHERE n.nspname = current_schema() AND t.relname = 'adaptive_decision_journal'
           AND x.relname = 'adaptive_decision_journal_evaluated_at_idx'
           AND i.indisvalid AND i.indisready AND NOT i.indisunique
           AND am.amname = 'btree'
           AND i.indnatts = 1
           AND right(pg_get_indexdef(i.indexrelid), length('(evaluated_at DESC)')) = '(evaluated_at DESC)'
    ) THEN
        RAISE EXCEPTION 'adaptive_decision_journal_evaluated_at_idx has an incompatible definition';
    END IF;
END $$;

COMMIT;
