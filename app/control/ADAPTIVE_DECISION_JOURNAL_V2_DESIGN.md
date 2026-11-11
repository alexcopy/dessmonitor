# Adaptive Decision Journal v2

## Implementation status (minimal v2)

Implemented: durable learner state, significant events, repetition counters, the
latest evaluation snapshot, per-worker heartbeats, an explicit cutover/rollback, and
a database guard against mixed v1/v2 writers. Deferred: anti-flap event rules, the
daily summary table, event retention and a v2 export. The evaluation itself
(`evaluate_collected_history`) is unchanged and shared by v1 and v2.

- Migration: `migrations/002_adaptive_decision_journal_v2.sql` (additive, repeatable);
  reverse with `002_adaptive_decision_journal_v2_down.sql` (refuses while v2 is active).
- Code: `app/control/adaptive_journal_v2.py`; the worker loop reads
  `adaptive_journal_control.mode` every iteration and uses v1 or v2 accordingly.
  `ADAPTIVE_OUTPUT_LEARNING_ENABLED` is unchanged.
- Tables: `adaptive_learning_state` (one row, `FOR UPDATE` under the existing advisory
  lock, strict validation, no retention), `adaptive_decision_events` (deterministic IDs
  from the locked state revision), `adaptive_state_repetitions` (per outcome
  fingerprint: first/last seen, evaluation count, new vs repeated evidence),
  `adaptive_latest_evaluation` (one row; full entry rewritten only on an outcome change
  or every 5 minutes), `adaptive_worker_heartbeat` (per worker; `ADAPTIVE_WORKER_ID` or
  host name), `adaptive_journal_control` (mode `v1`/`v2`).
- Events: `JOURNAL_V2_CUTOVER`, `BASELINE_RECORDED` (only after an empty-state start),
  `DECISION_CHANGED` (decision or stability), `HYPOTHESIS_CREATED/UPDATED/REJECTED`,
  `CONFIRMATION_CHANGED`, `HYPOTHESIS_CONFIRMED`, `EVIDENCE_BLOCKED/RECOVERED` (a fixed
  set of evidence-usability reason codes), `BATTERY_OBSERVATION_CHANGED` (voltage
  validity status), `BATTERY_RESPONSE_CHANGED`, `WORKER_FAILED/RECOVERED` (per worker,
  on transitions only).
- New evidence means the existing confirmation logic accepted a new, unseen sample
  (`proposal_state.last_collection_time` advanced); cached and repeated payloads are
  counted as repeated evidence and never advance confirmation.

### Cutover (operator)

1. Deploy this code while the journal is still v1. New workers keep writing v1.
2. Apply `001` (if needed) and `002` to the target database with `psql -v ON_ERROR_STOP=1`.
   Grant the worker role `SELECT, INSERT, UPDATE, DELETE` on the six v2 tables, and the
   web role `SELECT` on them. The decisions API returns 503 if the web role cannot read
   them.
3. Run `DATABASE_URL=... python scripts/adaptive_journal_v2.py status` and check that
   the mode is `v1`.
4. Run `python scripts/adaptive_journal_v2.py cutover --confirm`. It takes the learner
   advisory lock, waiting for any in-flight evaluation. It validates the newest v1
   entry carrying `proposal_state` and fails closed if that state is corrupt,
   incompatible or missing (`--allow-empty-state` only after review). It then writes
   durable state and sets the mode to `v2` in one transaction.
5. From then on, any v1 INSERT/UPDATE into `adaptive_decision_journal` is rejected by
   the guard trigger, including from old, not-yet-upgraded replicas. Upgraded workers
   switch to v2 on their next iteration. v1 rows stay readable; v1 30-day retention
   continues.

### Rollback (operator)

1. Run `python scripts/adaptive_journal_v2.py rollback --confirm`. Under the same lock
   it sets the mode to `v1` and writes one v1 handoff entry carrying the durable
   `proposal_state`, so v1 resumes with the same pending confirmation and cooldown.
2. Optional, only after rollback: apply `002_adaptive_decision_journal_v2_down.sql`. It
   refuses while the mode is `v2`, and it removes only v2 objects.

### Known limitations

- Events record every learner state transition. Anti-flap rules are deferred: on a
  synthetic day with frequent candidate changes about 380 events were written
  (~0.4 MB).
- Events and the heartbeat table have no retention. Repetition rows follow the
  30-day window.
- The export endpoint still exports Journal v1 rows only.
- Time-relative fields in the latest snapshot (ages, remaining cooldown seconds) can
  be up to 5 minutes old. The snapshot time is shown with them.

---

The original design proposal follows; where it differs from the implementation
above, the implementation status takes precedence.

## Current write-path investigation

- `run.py` starts one `learning_loop(database_url, stop_event)` task per application
  process when `ADAPTIVE_OUTPUT_LEARNING_ENABLED` is enabled. The loop evaluates
  immediately at startup and waits 60 seconds after each completed iteration. A
  restart starts another immediate evaluation. The repository has no other
  non-test call site for `learning_loop` or `run_evaluation`.
- The transaction-scoped PostgreSQL advisory lock prevents two evaluations from
  entering the journal transaction at the same time. It is released at commit and
  does not elect one long-lived replica. Multiple processes can therefore evaluate
  sequentially even though simultaneous attempts are rejected.
- `evaluate_collected_history` first creates a deterministic ID from the latest
  collection timestamp, the telemetry digest, and the decision outcome. In
  `run_evaluation`, that ID is replaced with a hash that also includes
  `now.isoformat()`. Distinct wall-clock instants, including millisecond-separated
  calls, therefore create distinct primary keys for unchanged inputs. The unique
  primary key and `ON CONFLICT DO NOTHING` do not deduplicate those calls.
- The `new_sample` signature check protects proposal confirmation from counting a
  replayed sample. It does not guard journal insertion. Every successful worker
  iteration still inserts a full JSON evaluation. `_record_worker_failure` also
  includes its timestamp in the ID, so repeated failures can each append a row.
- The worker reads proposal state from the latest journal entry. Failure entries do
  not carry `proposal_state`; the 30-day journal delete also means the journal alone
  is not an indefinite durable state store. Neither retention nor API pagination
  suppresses repeated writes.

### Disposable reproduction

The local PostgreSQL regression test inserts one telemetry record and calls
`run_evaluation` twice, 10 ms apart, without adding telemetry. It observes two
different evaluation IDs and two journal rows while decision, reason codes and
proposal state remain identical. This reproduces the code-level mechanism that can
create millisecond-spaced bursts. It does not establish what produced any particular
production screenshot; production was not accessed.

The existing replica-lock test confirms an overlapping transaction is rejected.
That behavior does not prevent sequential writes by replicas or restarted workers.
The number of production replicas, restarts, and exact 2026-11-07 15:28 UTC callers
cannot be determined from this repository and were not inspected.

## Proposed v2 storage

Use additive tables and make event/state/health separate concepts:

1. **`adaptive_learning_state`** — one locked row per learner stream. Store a
   `state_version`, monotonically increasing `revision`, durable proposal and
   cooldown state, active/pending hypothesis, confirmation signatures, last
   processed telemetry fingerprint, evidence/blocker state, and `updated_at`.
   This row is not subject to journal retention.
2. **`adaptive_decision_events`** — append-only, material state transitions only:
   `CYCLING_STARTED`, `CYCLING_ENDED`, `BATTERY_RESPONSE_CHANGED`,
   `HYPOTHESIS_CREATED`, `HYPOTHESIS_UPDATED`, `HYPOTHESIS_REJECTED`,
   `CONFIRMATION_CHANGED`, `EVIDENCE_BLOCKED`, `EVIDENCE_RECOVERED`,
   `WORKER_FAILED`, and `WORKER_RECOVERED`. Include event time, event type,
   state revision, evidence fingerprint, algorithm version, bounded evidence
   provenance, and JSON payload. A deterministic event ID and unique transition
   key make retries idempotent.
3. **`adaptive_state_repetitions`** — compact upsert keyed by a stable state
   fingerprint. Track first/last seen, repetition count, last evaluation time, and
   latest evidence fingerprint. Repeated identical HOLD/NO_CANDIDATE states update
   this row instead of storing another full diagnostic object.
4. **`adaptive_worker_heartbeat`** — one row per worker instance, with instance ID,
   process start, last heartbeat, last evaluation, last success, bounded error code,
   and health status. A heartbeat updates this table independently; it is not a
   decision event and does not append an evaluation JSON row.
5. **`adaptive_daily_summary`** — one upsert per UTC date for evaluation/repetition
   totals, collection coverage, unique payload fingerprints, freshness, observed
   transitions, and blocker durations. It must describe collection-time evidence
   as such.

## Event materiality and anti-flap rules

These are proposed diagnostic event thresholds, not inverter settings or safety
limits. They need review before implementation.

- A repeated evaluation with the same evidence fingerprint updates repetition and
  heartbeat fields only. It cannot increment confirmation or emit another event.
- `CYCLING_STARTED` follows the existing cycling detector becoming true on two
  successive distinct evidence fingerprints. `CYCLING_ENDED` requires the detector
  to remain false for 30 minutes and at least six new payload fingerprints. Unknown
  modes continue to break transition continuity.
- `BATTERY_RESPONSE_CHANGED` requires at least three valid short-gap response
  intervals on each side and a median voltage-slope change of at least 0.25 V/hour
  on two successive distinct fingerprints. This is a qualitative event threshold,
  not a probability or a causal claim.
- `CONFIRMATION_CHANGED` is emitted only when a new, unseen payload signature
  changes the durable confirmation count. Cooldown changes are written to durable
  state in the same transaction.
- `EVIDENCE_BLOCKED`/`EVIDENCE_RECOVERED` represent blocker-set transitions. Recovery
  requires two distinct fresh payload fingerprints with the blocking condition
  absent, preventing one-sample flapping.
- Hypothesis events are disabled unless a separately validated decision method
  supports the hypothesis. Sampled threshold-boundary counts alone never select or
  rank a winner. Unsupported virtual pairs remain comparisons only.
- `WORKER_FAILED` and `WORKER_RECOVERED` are emitted only on health-state
  transitions. Periodic heartbeat updates do not emit events.

## Transaction, concurrency, and recovery

For each evaluation, acquire the existing transaction-scoped advisory lock, then
`SELECT ... FOR UPDATE` the stream's durable state row. Compute from the bounded
telemetry snapshot, compare against the locked state, and in one transaction:

1. update proposal/confirmation/cooldown and evidence state;
2. upsert the repetition summary;
3. insert only material events using deterministic idempotency keys; and
4. record the evaluation result in the compact daily summary.

Concurrent replicas may still do redundant reads, but state transitions serialize.
The second replica sees the first replica's committed state and cannot emit the same
event again. A crash before commit rolls back state, summaries, and events together;
a retry recomputes from stored telemetry. A crash after commit is safe because event
keys and the stored input fingerprint make retry idempotent. Heartbeats use short,
separate upserts keyed by worker instance so a stopped or stuck replica is visible
without depending on the last decision event.

Worker-failure persistence updates the heartbeat and emits `WORKER_FAILED` only on
the transition into failure. It never overwrites or reconstructs proposal state from
the event journal. Restart recovery reads the durable state row, validates its
version, and preserves active proposal/cooldown state.

## Additive migration and rollout requirements

- Create new tables, constraints, and indexes in a reviewed additive migration. Do
  not alter or delete the existing journal rows, and do not change its existing
  30-day retention in this migration.
- Seed `adaptive_learning_state` from the newest valid retained journal entry that
  contains a supported `proposal_state`, not blindly from a worker-failure entry.
  If no valid state exists or versions are incompatible, abort initialization for
  operator review; never silently clear cooldown or confirmations.
- Stop old journal-writing workers before enabling v2 writers. A mixed-version
  deployment would let old replicas continue appending evaluations and could race
  state ownership. Keep existing API/export reads available for historical rows;
  move the operator UI to an event/state/heartbeat API only after v2 coverage is
  complete.
- Keep proposal state outside event retention. Apply any future event-retention
  change only through a separate reviewed decision; daily summaries and durable
  state must not be deleted by the legacy 30-day journal cleanup.
- Do not backfill one event per historical HOLD row. Preserve the legacy journal
  intact and begin event emission from an explicit cutover state snapshot.

## Current bounded telemetry query note

The existing 720-row query applies `LIMIT` to raw rows before Python removes cached
payloads. If more than 720 rows arrive in three hours, cached repeats can consume the
limit and evict older distinct samples. This branch now fetches a second bounded
mode-run-boundary set from the full three-hour time range: PostgreSQL groups
consecutive mode runs, returns each selected run's first/last records, limits to the
latest 720 runs plus the earliest boundary, and reports truncation. The range is
time-bounded and the query has a 10-second command timeout; PostgreSQL still scans
rows inside that three-hour range. This preserves older transition evidence for
cycling diagnostics without feeding sparse boundary
rows into battery-throughput calculations. The original raw cap remains in place for
quantitative analysis; if distinct changed telemetry itself exceeds 720, older
quantitative evidence is still explicitly bounded. A future query can use
`LAG(ROW(...))` to compress consecutive identical payloads before applying the
quantitative cap, but it must retain the latest collection row and surface any
remaining truncation. The daily 2,000-row review has its own explicit truncation
signal.
