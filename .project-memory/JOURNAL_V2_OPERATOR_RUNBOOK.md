# Journal v2 Operator Runbook

Production procedure for activating Adaptive Decision Journal v2 (merged in PR #71).
Design and semantics: `app/control/ADAPTIVE_DECISION_JOURNAL_V2_DESIGN.md`.

## Authorization boundary

| Step | Changes state? | Needs explicit operator authorization |
|------|----------------|---------------------------------------|
| 1–2, 4–5 preflight | No (read-only) | No |
| 3a Apply migration 002 | Schema only; journal stays v1 | **Yes** |
| 3b Role grants | Privileges | **Yes** |
| 6 Cutover | Journal writer v1 → v2 | **Yes, separately** |
| 8 Rollback | Journal writer v2 → v1 | **Yes, separately** |
| 8 Down migration | Drops v2 objects | **Yes, separately** |

Nothing in this runbook changes inverter settings, battery thresholds,
`ADAPTIVE_OUTPUT_LEARNING_ENABLED`, or hardware. All proposals stay `NON_ACTIONABLE`.
Never paste or print `DATABASE_URL`; the commands below read it inside the pod.

Shorthand used below:

```sh
NS=dess; DEP=deploy/dessmonitor; C=dessmonitor
kx() { kubectl -n "$NS" exec -i "$DEP" -c "$C" -- "$@"; }
```

## 1. Deployed application version

```sh
kubectl -n $NS get deploy dessmonitor \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="dessmonitor")].image}{"\n"}'
kubectl -n $NS get pods -l app=dessmonitor \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].imageID}{"\n"}{end}'
git fetch origin && git merge-base --is-ancestor 306c5b2 <IMAGE_SHA> && echo "Journal v2 code deployed"
kx test -f scripts/adaptive_journal_v2.py && echo "CLI present"
```

Proceed only if every pod runs one image that contains 306c5b2. Old replicas still
writing v1 rows are rejected by the guard trigger after cutover, so finish the rollout first.

## 2. Database connectivity and identity (read-only)

```sh
kx python - <<'EOF'
import asyncio, os, asyncpg
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'], command_timeout=10)
    try:
        r = await c.fetchrow("""SELECT current_database() db, current_user role,
            inet_server_addr()::text host, inet_server_port() port, pg_is_in_recovery() replica,
            (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb') timescaledb,
            to_regclass('adaptive_decision_journal') IS NOT NULL m001,
            to_regclass('adaptive_journal_control') IS NOT NULL m002""")
        print(dict(r))
    finally:
        await c.close()
asyncio.run(main())
EOF
```

Expect the production database name and host you intend, `replica: False`,
a TimescaleDB version, `m001: True`. Record `role` — this is the runtime role
(web and worker share one `DATABASE_URL` in the current single-Deployment layout).
Stop if any value is unexpected.

## 3. Migration 002 and grants (authorized)

**3a. Migration-role preflight (read-only).** Migration 002 runs
`DROP TRIGGER IF EXISTS` / `CREATE TRIGGER` on `adaptive_decision_journal`, and
PostgreSQL allows that only to the table's owner (or a role that inherits the
owning role's privileges; superusers qualify). It also creates six tables, so the
role needs `CREATE` on the schema. Run as the migration role (DSN in your shell
only, never in logs):

```sh
psql "$MIGRATION_DSN" -X -v ON_ERROR_STOP=1 <<'SQL'
SELECT current_database() AS db, current_user AS migration_role, current_schema() AS schema,
       pg_get_userbyid(c.relowner) AS journal_owner,
       pg_has_role(current_user, c.relowner, 'USAGE') AS acts_as_owner,
       has_schema_privilege(current_schema(), 'CREATE') AS can_create
FROM pg_class c WHERE c.oid = 'adaptive_decision_journal'::regclass;
SELECT pg_has_role(current_user, c.relowner, 'USAGE')
       AND has_schema_privilege(current_schema(), 'CREATE') AS migration_ok
FROM pg_class c WHERE c.oid = 'adaptive_decision_journal'::regclass \gset
\if :migration_ok
\echo 'MIGRATION ROLE PREFLIGHT: PASS'
\else
\echo 'MIGRATION ROLE PREFLIGHT: FAIL'
DO $$ BEGIN RAISE EXCEPTION 'migration role cannot own adaptive_decision_journal or create in schema'; END $$;
\endif
SQL
```

Proceed only on `PASS` (exit 0) with the expected `db`. On `FAIL` (non-zero exit),
or an error that `adaptive_decision_journal` does not exist, **do not apply 002**:
use the role shown as `journal_owner`, or have the DBA grant the migration role
membership in that role (a separately authorized change). Do not change table
ownership as a workaround without review. Re-applying 002 later also requires the
same owner, because `CREATE OR REPLACE FUNCTION` needs ownership of the guard function.

**3a (cont.). Apply (authorized).** Back up first per normal policy; at minimum
`pg_dump -t adaptive_decision_journal`.

```sh
psql "$MIGRATION_DSN" -X -v ON_ERROR_STOP=1 -f migrations/002_adaptive_decision_journal_v2.sql
```

The script runs in its own transaction, refuses without 001, is repeatable, never
touches v1 rows, and creates the control row in mode `v1` — applying it does not
activate v2.

**3b. Grants.** Six tables: `adaptive_journal_control`, `adaptive_learning_state`,
`adaptive_latest_evaluation`, `adaptive_decision_events`, `adaptive_state_repetitions`,
`adaptive_worker_heartbeat`.

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON adaptive_journal_control, adaptive_learning_state,
  adaptive_latest_evaluation, adaptive_decision_events, adaptive_state_repetitions,
  adaptive_worker_heartbeat TO <worker_role>;
GRANT SELECT ON <same six tables> TO <web_role>;   -- only if web uses a separate role
```

Verify (read-only). Each required privilege is checked on its own row: a
comma-separated privilege list in `has_table_privilege` is true if *any* one is
held, so it must not be used here. Required matrix:

| Role | Tables | Privileges |
|------|--------|------------|
| worker | the six v2 tables | SELECT, INSERT, UPDATE, DELETE |
| worker | `adaptive_decision_journal` (v1; rollback handoff row) | SELECT, INSERT, DELETE |
| worker | `inverter_metrics` | SELECT |
| web | the six v2 tables, `adaptive_decision_journal`, `inverter_metrics` (battery observations) | SELECT |
| worker, web | each schema containing any table above | USAGE |

Table grants alone are not enough: without `USAGE` on the containing schema a role
cannot access the tables. If web and worker share one `DATABASE_URL`, pass the same
role for both. Table names resolve through this session's `search_path`; it must
find the same tables the application uses.

```sh
psql "$MIGRATION_DSN" -X -v ON_ERROR_STOP=1 -v worker_role=<worker_role> -v web_role=<web_role> <<'SQL'
CREATE TEMP VIEW required_privileges AS
WITH v2(t) AS (VALUES ('adaptive_journal_control'), ('adaptive_learning_state'),
       ('adaptive_latest_evaluation'), ('adaptive_decision_events'),
       ('adaptive_state_repetitions'), ('adaptive_worker_heartbeat'))
SELECT :'worker_role'::name AS role_name, t AS table_name, p AS privilege
  FROM v2, unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p
UNION ALL SELECT :'worker_role', 'adaptive_decision_journal', p
  FROM unnest(ARRAY['SELECT', 'INSERT', 'DELETE']) p
UNION ALL SELECT :'worker_role', 'inverter_metrics', 'SELECT'
UNION ALL SELECT :'web_role', t, 'SELECT' FROM v2
UNION ALL SELECT :'web_role', 'adaptive_decision_journal', 'SELECT'
UNION ALL SELECT :'web_role', 'inverter_metrics', 'SELECT';
CREATE TEMP VIEW preflight AS
SELECT 'table' AS kind, role_name, table_name AS object, privilege,
       has_table_privilege(role_name, table_name, privilege) AS granted
FROM required_privileges
UNION
SELECT 'schema', r.role_name, n.nspname::text, 'USAGE', has_schema_privilege(r.role_name, n.oid, 'USAGE')
FROM required_privileges r
JOIN pg_class c ON c.oid = r.table_name::regclass
JOIN pg_namespace n ON n.oid = c.relnamespace;
SELECT * FROM preflight ORDER BY granted, kind, role_name, object, privilege;
SELECT bool_and(granted) AS all_granted FROM preflight \gset
\if :all_granted
\echo 'PRIVILEGE PREFLIGHT: PASS'
\else
\echo 'PRIVILEGE PREFLIGHT: FAIL - missing requirements:'
SELECT kind, role_name, object, privilege FROM preflight WHERE NOT granted
ORDER BY kind, role_name, object, privilege;
DO $$ BEGIN RAISE EXCEPTION 'required schema or table privileges are missing'; END $$;
\endif
SQL
```

Proceed only on `PASS` (exit 0). On `FAIL` the missing requirements (kind, role,
schema or table, privilege) are listed and psql exits non-zero; an unknown role or
table is a hard error, also non-zero. Without web access, `/api/controls/decisions`
returns 503 (fails closed).

## 4. Confirm still in v1 mode (read-only)

```sh
kx python scripts/adaptive_journal_v2.py status
```

Expect `"mode": "v1"`, `"v2_installed": true`. Record `changed_at` as incident evidence
for step 6 (it never proves the outcome on its own). The decisions page still shows v1 rows,
and `SELECT max(evaluated_at) FROM adaptive_decision_journal` keeps advancing about
once a minute (adaptive learning is enabled and writing v1).

## 5. Durable-state prerequisites (read-only)

Cutover seeds v2 from the newest v1 entry carrying `proposal_state` and fails closed
if it is missing, corrupt or incompatible.

```sql
SELECT evaluation_id, evaluated_at, entry->>'algorithm_version' AS algorithm_version,
       jsonb_typeof(entry->'proposal_state') AS state_type
FROM adaptive_decision_journal WHERE entry ? 'proposal_state'
ORDER BY evaluated_at DESC, evaluation_id DESC LIMIT 1;
```

```sh
kx python -c "from app.control import online_output_learning as ol; print(sorted(ol.STATE_COMPATIBLE_VERSIONS))"
```

Expect one recent row, `state_type = object`, and its `algorithm_version` in the
printed list. If no row exists, **stop**: `--allow-empty-state` discards learner
history and needs its own review and authorization.

## 6. Cutover (explicit operator authorization required)

Run it in one named pod, and record that pod and the time as evidence in case the
outcome is lost:

```sh
POD=$(kubectl -n $NS get pod -l app=dessmonitor -o jsonpath='{.items[0].metadata.name}'); echo "$POD"; date -u
kubectl -n $NS exec -i "$POD" -c $C -- python scripts/adaptive_journal_v2.py cutover --confirm --note "<who/ticket>"
```

Expect `"status": "CUTOVER_COMPLETE"` with `initialized_from: "V1:<evaluation_id>"`
(matching step 5). `ALREADY_V2` means it was already done. Cutover waits on the
learner advisory lock, then writes durable state and flips the mode in one transaction.

**Outcome UNKNOWN.** If the command raises an exception, times out, loses its
database connection, `kubectl exec` disconnects, or no JSON result is printed, the
outcome is UNKNOWN. UNKNOWN is **not** a failed cutover and **not** a rollback.
The cutover may have committed and lost only its reply, it may still commit later
(for example while waiting on the learner advisory lock, or from a backend still
running after its client disappeared), or it may never commit. This runbook does
not try to tell these apart, and a later v1 reading proves nothing.

When the outcome is UNKNOWN:

1. **Stop.** Do not continue to step 7 unless v2 is confirmed as below.
2. Do **not** retry cutover, use `--allow-empty-state`, roll back, change
   `adaptive_journal_control` by hand, or terminate database sessions or pods.
3. Capture evidence (read-only): the command output, the pod name and start time
   recorded above, and a fresh status from a new process:

```sh
kx python scripts/adaptive_journal_v2.py status
```

If that command fails, a DBA can read the same state directly (read-only):

```sql
SELECT mode, changed_at, change_note FROM adaptive_journal_control WHERE id = 1;
SELECT revision, initialized_from, initialized_at FROM adaptive_learning_state WHERE stream = 'battery';
SELECT event_time, payload->>'initialized_from' AS initialized_from FROM adaptive_decision_events
WHERE event_type = 'JOURNAL_V2_CUTOVER' ORDER BY event_time DESC LIMIT 1;
```

4. Interpret a fresh read only like this:

| Fresh read | Result |
|------------|--------|
| `mode: v2`, `state.initialized_from` as expected from step 5, and a `JOURNAL_V2_CUTOVER` event with the same `initialized_from` | **v2 confirmed active.** Record the incident and continue at step 7 |
| `mode: v1`, even with `changed_at` unchanged | **Still UNKNOWN** |
| `v2` without the matching state row or event, status unreadable, or database unreachable | **Still UNKNOWN** |

5. **Escalate** to the operator and DBA as an incident, with the evidence from
   item 3. Investigating an UNKNOWN outcome, and authorizing any later database
   operation (a new cutover, a rollback, or anything else), is outside this
   runbook. Never resume because time has passed or because a later read shows v1.
   Only a read that confirms v2 as in the table leads back to step 7.

## 7. Post-cutover verification (read-only)

Run `status` now and again after ~3 minutes:

```sh
kx python scripts/adaptive_journal_v2.py status
```

- `mode: v2`; `state.initialized_from` as above; `state.revision` present.
- **Heartbeat:** every running worker appears in `workers` with
  `last_heartbeat_at` within ~2 minutes and advancing, and `consecutive_failures: 0`.
  Status `healthy`, `standby` (lock loser) and `degraded` are all non-failure
  states: `degraded` means the evaluation succeeded but evidence was blocked
  (`blocking_reason` set), and it always has `consecutive_failures: 0`. Only
  `failing` counts worker failures.
- **Events:** `events` contains `JOURNAL_V2_CUTOVER`. Under a steady outcome no new
  events appear between the two runs.
- **Repetition aggregation:** `repetition.evaluation_count` grows (~1/min) between
  the two runs while the event count stays flat; `repeated_evidence_count` grows when
  no new samples arrive.
- `SELECT max(evaluated_at) FROM adaptive_decision_journal` stops advancing.
- `/api/controls/decisions` returns 200 and the page shows the v2 latest snapshot.

## 8. Failure and rollback

These conditions apply only after v2 was confirmed active in step 6 or 7. While a
cutover outcome is UNKNOWN (step 6), do not roll back; escalate instead.

**Rollback conditions** (any one): decisions API 503 after grants were verified;
a worker in status `failing` whose `consecutive_failures` keeps increasing, or a
`WORKER_FAILED` event without a `WORKER_RECOVERED` within ~10 minutes (`degraded`
alone is not a failure: it is a successful evaluation with blocked evidence); no heartbeat
advance for >5 minutes while pods are running; repeated `LearnerStateInvalid` /
`LearnerStateMissing` in worker logs; repetition count not advancing while
heartbeats are; an unexpected flood of events (hundreds per hour under steady
conditions). Transient `adaptive_decision_journal is read-only while Journal v2
is active` (SQLSTATE 55000) means an old replica is still running — finish the
rollout; it is not by itself a rollback reason.

**Rollback (separate explicit operator authorization required):**

```sh
kx python scripts/adaptive_journal_v2.py rollback --confirm --note "<who/ticket/reason>"
```

Expect `ROLLBACK_COMPLETE` with a `handoff_evaluation_id` (or `ALREADY_V1`). Then
`status` shows `mode: v1`; the handoff row (reason code
`JOURNAL_V2_ROLLBACK_STATE_HANDOFF`, same `proposal_state`) is already in
`adaptive_decision_journal`, and regular v1 rows resume within ~2 minutes.
v2 tables are kept for diagnosis.

**Down migration** (optional, separately authorized, only after rollback):
`psql "$MIGRATION_DSN" -v ON_ERROR_STOP=1 -f migrations/002_adaptive_decision_journal_v2_down.sql`.
It refuses while the mode is `v2` and removes only v2 objects.
