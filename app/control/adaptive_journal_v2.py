"""Adaptive Decision Journal v2: durable learner state, significant events,
repetition counters and per-worker heartbeats (migration 002).

The evaluation itself is unchanged (``online_output_learning._evaluate_locked``);
v2 only changes what is persisted:

* ``adaptive_learning_state`` holds proposal/confirmation/cooldown state and the
  last processed evidence, independent of journal retention. It is read under the
  advisory lock with ``FOR UPDATE`` and validated strictly: corrupt or incompatible
  state raises instead of being reset.
* ``adaptive_decision_events`` receives only material transitions, with
  deterministic IDs derived from the locked state revision (never from wall time).
* ``adaptive_state_repetitions`` counts repeated identical outcomes, separating
  new evidence from repeated/cached evidence.
* ``adaptive_latest_evaluation`` keeps one overwritten full evaluation for the UI.
* ``adaptive_worker_heartbeat`` records per-worker health without decision events.

Nothing here writes inverter settings; every table carries a hardware-write guard
or holds no actuation data at all.
"""
import hashlib
import json
import math
import os
import re
import socket
from datetime import date, datetime, timedelta, timezone

from app.control import online_output_learning as ol

STREAM = 'battery'
STATE_VERSION = 1
RETENTION = timedelta(days=30)
# Full latest-evaluation snapshots are rewritten on any outcome change, otherwise at
# most this often; only time-relative descriptive fields (ages, remaining cooldown
# seconds) can be this old, and the snapshot time is shown with them.
SNAPSHOT_REFRESH = timedelta(minutes=5)
PROPOSAL_STATE_KEYS = frozenset((
    'pending', 'stable_count', 'pending_response_v', 'active', 'last_change_at',
    'last_collection_time', 'last_sample_signature', 'confirmation_signatures',
    'daily_review_date_utc', 'daily_behavior_review'))
# Reason codes that describe whether evidence is usable. Per-sample volatile codes
# (e.g. LATEST_OBSERVATION_NOT_COMPARABLE) stay in the latest evaluation and the
# repetition outcome but do not create events.
BLOCKER_CODES = frozenset((
    'NO_RECENT_COLLECTIONS', 'LATEST_COLLECTION_STALE', 'MISSING_BATTERY_VOLTAGE',
    'MISSING_BATTERY_CURRENT_CHG', 'MISSING_BATTERY_CURRENT_DIS', 'MISSING_PV_POWER_W',
    'MISSING_OUTPUT_POWER_W', 'INVALID_BATTERY_VOLTAGE', 'WORKING_MODE_UNKNOWN',
    'MODE_HISTORY_TRUNCATED', 'QUANTITATIVE_HISTORY_TRUNCATED', 'HISTORICAL_BASELINE_UNKNOWN',
    'HISTORICAL_BASELINE_OUT_OF_RANGE', 'COOLDOWN_STATE_UNVERIFIED', 'CATALOGUE_OR_HYSTERESIS_BLOCK'))
V2_TABLES = ('adaptive_journal_control', 'adaptive_learning_state', 'adaptive_latest_evaluation',
             'adaptive_decision_events', 'adaptive_state_repetitions', 'adaptive_worker_heartbeat')
_WORKER_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
_ERROR = re.compile(r'[A-Za-z0-9_]{1,80}')


class JournalV2Error(RuntimeError):
    """Base class; the worker records a failure and never resets state."""


class LearnerStateMissing(JournalV2Error):
    pass


class LearnerStateInvalid(JournalV2Error):
    pass


def worker_identity():
    """Stable per-host identity (ADAPTIVE_WORKER_ID overrides); restarts keep it."""
    raw = os.environ.get('ADAPTIVE_WORKER_ID') or socket.gethostname() or 'worker'
    cleaned = re.sub(r'[^A-Za-z0-9_.:-]', '_', raw)[:128]
    return cleaned or 'worker'


def _safe_worker_id(worker_id):
    worker_id = worker_id if worker_id is not None else worker_identity()
    if not isinstance(worker_id, str) or not _WORKER_ID.fullmatch(worker_id):
        raise ValueError('invalid worker id')
    return worker_id


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def _digest(*parts):
    return hashlib.sha256(_canonical(list(parts)).encode()).hexdigest()


def _aware_iso(value, field, now=None, allow_future=True):
    if not isinstance(value, str):
        raise LearnerStateInvalid('%s must be an ISO timestamp' % field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LearnerStateInvalid('%s is not an ISO timestamp' % field) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LearnerStateInvalid('%s has no timezone' % field)
    if not allow_future and now is not None and parsed > now:
        raise LearnerStateInvalid('%s is in the future' % field)
    return parsed


def validate_proposal_state(state, now):
    """Strictly validate durable proposal state; raise instead of coercing.

    v1 silently normalised malformed confirmation fields. Durable v2 state is the
    only copy, so a malformed value is an operator problem, never a reset.
    """
    if not isinstance(state, dict):
        raise LearnerStateInvalid('proposal_state must be an object')
    unknown = sorted(set(state) - PROPOSAL_STATE_KEYS)
    if unknown:
        raise LearnerStateInvalid('unknown proposal_state keys: %s' % ', '.join(unknown)[:200])
    pattern = ol._PENDING_PATTERN
    pending, active = state.get('pending'), state.get('active')
    if pending is not None and not (isinstance(pending, str) and pattern.fullmatch(pending)):
        raise LearnerStateInvalid('invalid pending candidate')
    if active is not None and not (isinstance(active, str) and pattern.fullmatch(active)):
        raise LearnerStateInvalid('invalid active candidate')
    count = state.get('stable_count', 0)
    if type(count) is not int or not 0 <= count <= 2:
        raise LearnerStateInvalid('invalid stable_count')
    if count and pending is None:
        raise LearnerStateInvalid('stable_count without a pending candidate')
    response = state.get('pending_response_v')
    if response is not None and not (type(response) in (int, float) and math.isfinite(response)):
        raise LearnerStateInvalid('invalid pending_response_v')
    if state.get('last_change_at') is not None:
        _aware_iso(state['last_change_at'], 'last_change_at', now, allow_future=False)
    elif active is not None:
        raise LearnerStateInvalid('active candidate without last_change_at (cooldown unverifiable)')
    if state.get('last_collection_time') is not None:
        _aware_iso(state['last_collection_time'], 'last_collection_time')
    signature = state.get('last_sample_signature')
    if signature is not None and not isinstance(signature, list):
        raise LearnerStateInvalid('invalid last_sample_signature')
    signatures = state.get('confirmation_signatures')
    if signatures is not None and not (isinstance(signatures, list) and len(signatures) <= 32
                                       and all(isinstance(item, list) for item in signatures)):
        raise LearnerStateInvalid('invalid confirmation_signatures')
    review_date = state.get('daily_review_date_utc')
    if review_date is not None:
        try:
            date.fromisoformat(review_date)
        except (TypeError, ValueError) as exc:
            raise LearnerStateInvalid('invalid daily_review_date_utc') from exc
    review = state.get('daily_behavior_review')
    if review is not None and not isinstance(review, dict):
        raise LearnerStateInvalid('invalid daily_behavior_review')
    try:
        _canonical(state)
    except (TypeError, ValueError) as exc:
        raise LearnerStateInvalid('proposal_state is not strict JSON') from exc
    return json.loads(_canonical(state))


def _contrast(value):
    """Only the two fields evaluate_collected_history reads from the previous contrast."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LearnerStateInvalid('invalid previous contrast')
    result = {}
    for key in ('contrast_v', 'response_uncertainty_v'):
        number = value.get(key)
        if number is not None and not (type(number) in (int, float) and math.isfinite(number)):
            raise LearnerStateInvalid('invalid previous contrast %s' % key)
        result[key] = number
    return result


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _outcome(entry):
    """Stable outcome summary: excludes wall time, ages and per-sample confidence."""
    state = entry.get('proposal_state') or {}
    health = entry.get('worker_health') or {}
    contrast = entry.get('exploratory_voltage_contrast')
    cooldown = entry.get('cooldown_remaining_seconds')
    return {
        'decision': entry.get('decision'), 'stability': entry.get('stability'),
        'threshold': entry.get('threshold'), 'candidate_voltage': entry.get('candidate_voltage'),
        'proposed_voltage': entry.get('proposed_voltage'),
        'pending': state.get('pending'), 'stable_count': state.get('stable_count', 0),
        'active': state.get('active'), 'last_change_at': state.get('last_change_at'),
        'cooldown': 'UNVERIFIED' if cooldown is None else 'ACTIVE' if cooldown > 0 else 'NONE',
        'blockers': sorted(set(entry.get('reason_codes') or ()) & BLOCKER_CODES),
        'battery_v_status': entry.get('battery_v_status'),
        'response_changed': bool(isinstance(contrast, dict) and contrast.get('response_changed')),
        'health_status': health.get('status'), 'blocking_reason': health.get('blocking_reason'),
    }


def _derive_events(old, new):
    """Material transitions between two outcome summaries (old == {} -> baseline)."""
    if not old:
        return [('BASELINE_RECORDED', {'to': new})]
    events = []
    if (old.get('decision'), old.get('stability')) != (new['decision'], new['stability']):
        events.append(('DECISION_CHANGED', {
            'from': {'decision': old.get('decision'), 'stability': old.get('stability')},
            'to': {'decision': new['decision'], 'stability': new['stability']}}))
    before, after = old.get('candidate_voltage'), new['candidate_voltage']
    if before is None and after is not None:
        events.append(('HYPOTHESIS_CREATED', {'candidate_voltage': after, 'threshold': new['threshold']}))
    elif before is not None and after is None:
        events.append(('HYPOTHESIS_REJECTED', {'previous_candidate_voltage': before}))
    elif before != after:
        events.append(('HYPOTHESIS_UPDATED', {'from': before, 'to': after}))
    if (old.get('pending'), old.get('stable_count')) != (new['pending'], new['stable_count']):
        events.append(('CONFIRMATION_CHANGED', {
            'from': {'pending': old.get('pending'), 'stable_count': old.get('stable_count')},
            'to': {'pending': new['pending'], 'stable_count': new['stable_count']}}))
    if new['active'] and new['active'] != old.get('active'):
        events.append(('HYPOTHESIS_CONFIRMED', {'active': new['active'], 'last_change_at': new['last_change_at'],
                                                'actionability': 'NON_ACTIONABLE'}))
    old_blockers, new_blockers = set(old.get('blockers') or ()), set(new['blockers'])
    if new_blockers - old_blockers:
        events.append(('EVIDENCE_BLOCKED', {'added': sorted(new_blockers - old_blockers),
                                            'blockers': new['blockers']}))
    if old_blockers - new_blockers:
        events.append(('EVIDENCE_RECOVERED', {'removed': sorted(old_blockers - new_blockers),
                                              'blockers': new['blockers']}))
    if old.get('battery_v_status') != new['battery_v_status']:
        events.append(('BATTERY_OBSERVATION_CHANGED', {'from': old.get('battery_v_status'),
                                                       'to': new['battery_v_status']}))
    if new['response_changed'] and not old.get('response_changed'):
        events.append(('BATTERY_RESPONSE_CHANGED', {'interpretation': 'EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN'}))
    return events


async def _insert_event(conn, event_id, now, event_type, payload, *, revision=None, worker_id=None,
                        evidence_fingerprint=None):
    await conn.execute('''INSERT INTO adaptive_decision_events (event_id, stream, event_time, event_type,
            state_revision, worker_id, evidence_fingerprint, algorithm_version, payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb) ON CONFLICT (event_id) DO NOTHING''',
        event_id, STREAM, now, event_type, revision, worker_id, evidence_fingerprint, ol.VERSION,
        _canonical(payload))


async def _validate_v2_schema_conn(conn):
    for table in V2_TABLES:
        if await conn.fetchval('SELECT to_regclass($1)', table) is None:
            raise JournalV2Error('Journal v2 table %s is missing; apply migration 002' % table)
    if await conn.fetchval('SELECT count(*) FROM adaptive_journal_control WHERE id = 1') != 1:
        raise JournalV2Error('Journal v2 control row is missing')
    trigger = await conn.fetchval('''SELECT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = current_schema()
        AND c.relname = 'adaptive_decision_journal' AND t.tgname = 'adaptive_decision_journal_v2_write_guard'
        AND t.tgenabled <> 'D')''')
    if not trigger:
        raise JournalV2Error('v1 write guard trigger is missing or disabled')


async def validate_v2_schema(pool):
    async with pool.acquire() as conn:
        await _validate_v2_schema_conn(conn)


async def _load_state(conn, now):
    row = await conn.fetchrow('SELECT * FROM adaptive_learning_state WHERE stream = $1 FOR UPDATE', STREAM)
    if row is None:
        raise LearnerStateMissing('no durable learner state; run the explicit cutover')
    if row['state_version'] != STATE_VERSION:
        raise LearnerStateInvalid('unsupported state_version %r' % row['state_version'])
    if row['algorithm_version'] not in ol.STATE_COMPATIBLE_VERSIONS:
        raise LearnerStateInvalid('incompatible algorithm_version %r' % row['algorithm_version'])
    state = validate_proposal_state(_json(row['proposal_state']), now)
    contrast = _contrast(_json(row['previous_contrast']))
    last_outcome = _json(row['last_outcome'])
    if not isinstance(last_outcome, dict):
        raise LearnerStateInvalid('invalid last_outcome')
    return row, state, contrast, last_outcome


async def _evaluate_v2_locked(conn, now, worker_id, **kwargs):
    """True when committed, False for a replayed/older ``now``, None when v2 is not active."""
    if await ol.journal_mode(conn) != 'v2':
        return None
    await _validate_v2_schema_conn(conn)
    row, state, contrast, old_outcome = await _load_state(conn, now)
    if row['last_evaluated_at'] is not None and now <= row['last_evaluated_at']:
        # Replay of an already committed (or older) evaluation time: idempotent no-op.
        return False
    previous = {'algorithm_version': row['algorithm_version'], 'proposal_state': state,
                'exploratory_voltage_contrast': contrast}
    entry = await ol._evaluate_locked(conn, now, previous, **kwargs)
    new_state = validate_proposal_state(entry['proposal_state'], now)
    new_contrast = _contrast({k: v for k, v in (entry.get('exploratory_voltage_contrast') or {}).items()
                              if k in ('contrast_v', 'response_uncertainty_v')}
                             if isinstance(entry.get('exploratory_voltage_contrast'), dict) else None)
    outcome = _outcome(entry)
    outcome_fp = _digest(outcome)
    # New evidence == the confirmation logic accepted a new, unseen sample. Cached or
    # repeated payloads never advance last_collection_time (see evaluate_collected_history).
    new_evidence = (new_state.get('last_collection_time') is not None
                    and new_state.get('last_collection_time') != state.get('last_collection_time'))
    evidence_fp = _digest(new_state.get('last_collection_time'), new_state.get('last_sample_signature'))
    base = row['revision']
    changed = (new_state != state or new_contrast != contrast or outcome_fp != row['last_outcome_fingerprint']
               or evidence_fp != row['last_evidence_fingerprint'] or row['algorithm_version'] != ol.VERSION)
    revision = base + 1 if changed else base
    for event_type, payload in _derive_events(old_outcome, outcome):
        payload = dict(payload, collection_time=entry.get('collection_time'))
        await _insert_event(conn, _digest(STREAM, base, event_type, payload), now, event_type, payload,
                            revision=revision, worker_id=worker_id, evidence_fingerprint=evidence_fp)
    if changed:
        status = await conn.execute('''UPDATE adaptive_learning_state SET revision = $2, algorithm_version = $3,
                proposal_state = $4::jsonb, previous_contrast = $5::jsonb, last_outcome = $6::jsonb,
                last_outcome_fingerprint = $7, last_evidence_fingerprint = $8, last_evaluated_at = $9,
                updated_at = $9
            WHERE stream = $1 AND revision = $10''', STREAM, revision, ol.VERSION, _canonical(new_state),
            None if new_contrast is None else _canonical(new_contrast), _canonical(outcome), outcome_fp,
            evidence_fp, now, base)
    else:
        # Repeated identical evaluation: touch only small columns (no JSON/TOAST rewrite).
        status = await conn.execute('''UPDATE adaptive_learning_state SET last_evaluated_at = $2
            WHERE stream = $1 AND revision = $3''', STREAM, now, base)
    if status != 'UPDATE 1':
        raise JournalV2Error('learner state changed concurrently')
    entry['evaluation_id'] = hashlib.sha256(f"{ol.VERSION}:{now.isoformat()}:{entry['evaluation_id']}".encode()).hexdigest()
    entry['journal_version'] = 'v2'
    entry['state_revision'] = revision
    snapshot = await conn.fetchrow('SELECT evaluated_at FROM adaptive_latest_evaluation WHERE stream = $1 FOR UPDATE',
                                   STREAM)
    if (snapshot is None or outcome_fp != row['last_outcome_fingerprint']
            or now - snapshot['evaluated_at'] >= SNAPSHOT_REFRESH):
        await conn.execute('''INSERT INTO adaptive_latest_evaluation (stream, evaluated_at, last_evaluated_at,
                evaluations_since_snapshot, state_revision, entry)
            VALUES ($1, $2, $2, 0, $3, $4::jsonb) ON CONFLICT (stream) DO UPDATE SET
                evaluated_at = EXCLUDED.evaluated_at, last_evaluated_at = EXCLUDED.last_evaluated_at,
                evaluations_since_snapshot = 0, state_revision = EXCLUDED.state_revision, entry = EXCLUDED.entry''',
            STREAM, now, revision, json.dumps(entry, allow_nan=False))
    else:
        await conn.execute('''UPDATE adaptive_latest_evaluation SET last_evaluated_at = $2,
            evaluations_since_snapshot = evaluations_since_snapshot + 1 WHERE stream = $1''', STREAM, now)
    await conn.execute('''INSERT INTO adaptive_state_repetitions (stream, outcome_fingerprint, outcome, first_seen_at,
            last_seen_at, evaluation_count, new_evidence_count, repeated_evidence_count, last_evidence_fingerprint)
        VALUES ($1, $2, $3::jsonb, $4, $4, 1, $5, $6, $7)
        ON CONFLICT (stream, outcome_fingerprint) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at,
            evaluation_count = adaptive_state_repetitions.evaluation_count + 1,
            new_evidence_count = adaptive_state_repetitions.new_evidence_count + EXCLUDED.new_evidence_count,
            repeated_evidence_count = adaptive_state_repetitions.repeated_evidence_count
                + EXCLUDED.repeated_evidence_count,
            last_evidence_fingerprint = EXCLUDED.last_evidence_fingerprint''',
        STREAM, outcome_fp, _canonical(outcome), now, int(new_evidence), int(not new_evidence), evidence_fp)
    health = entry['worker_health']
    await _heartbeat_success(conn, worker_id, now, health['status'], health.get('blocking_reason'))
    # v1 retention semantics are unchanged; v2 counters follow the same window.
    await conn.execute('DELETE FROM adaptive_decision_journal WHERE evaluated_at < $1', now - RETENTION)
    await conn.execute('''DELETE FROM adaptive_state_repetitions WHERE stream = $1 AND last_seen_at < $2
        AND outcome_fingerprint <> $3''', STREAM, now - RETENTION, outcome_fp)
    return True


async def _heartbeat_success(conn, worker_id, now, status, blocking_reason):
    prior = await conn.fetchrow('''SELECT status, failure_started_at FROM adaptive_worker_heartbeat
        WHERE worker_id = $1 FOR UPDATE''', worker_id)
    if prior is not None and prior['status'] == 'failing':
        started = prior['failure_started_at']
        await _insert_event(conn, _digest(STREAM, 'WORKER_RECOVERED', worker_id,
                                          started.isoformat() if started else None),
                            now, 'WORKER_RECOVERED', {'worker_id': worker_id,
                            'failure_started_at': started.isoformat() if started else None}, worker_id=worker_id)
    await conn.execute('''INSERT INTO adaptive_worker_heartbeat (worker_id, status, last_heartbeat_at,
            last_evaluation_at, last_success_at, last_error, blocking_reason, consecutive_failures, failure_started_at)
        VALUES ($1, $2, $3, $3, $3, NULL, $4, 0, NULL)
        ON CONFLICT (worker_id) DO UPDATE SET status = EXCLUDED.status, last_heartbeat_at = EXCLUDED.last_heartbeat_at,
            last_evaluation_at = EXCLUDED.last_evaluation_at, last_success_at = EXCLUDED.last_success_at,
            last_error = NULL, blocking_reason = EXCLUDED.blocking_reason, consecutive_failures = 0,
            failure_started_at = NULL''',
        worker_id, 'healthy' if status == 'healthy' else 'degraded', now, (blocking_reason or None) and str(blocking_reason)[:120])


async def run_evaluation_v2(pool, now, *, worker_id=None, verified_observations=None, learner=None,
                            historical_battery_v=26.0):
    """Evaluate once under the shared advisory lock and persist v2 state atomically.

    Returns True when committed. Returns False when v2 is not active, when another
    replica holds the lock, or when ``now`` is not newer than the last committed
    evaluation (idempotent replay); the latter two only refresh this worker's heartbeat.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware evaluation time required')
    now = now.astimezone(timezone.utc)
    worker_id = _safe_worker_id(worker_id)
    async with pool.acquire() as conn:
        result = False
        async with conn.transaction():
            if await conn.fetchval('SELECT pg_try_advisory_xact_lock($1)', ol.LOCK_ID):
                result = await _evaluate_v2_locked(conn, now, worker_id, verified_observations=verified_observations,
                                                   learner=learner, historical_battery_v=historical_battery_v)
        if result is None:
            return False
        if result:
            return True
        # Lock busy or replayed/older time (e.g. clock behind another replica): record liveness only.
        await conn.execute('''INSERT INTO adaptive_worker_heartbeat (worker_id, status, last_heartbeat_at)
            VALUES ($1, 'standby', $2) ON CONFLICT (worker_id) DO UPDATE SET
            last_heartbeat_at = GREATEST(adaptive_worker_heartbeat.last_heartbeat_at, EXCLUDED.last_heartbeat_at)''',
            worker_id, now)
        return False


async def record_worker_failure_v2(pool, now, error_type, *, worker_id=None):
    """Update this worker's heartbeat; emit WORKER_FAILED only on the transition.

    Never reads or writes proposal state, so cooldown and pending confirmation
    survive any number of failures.
    """
    now = now.astimezone(timezone.utc)
    worker_id = _safe_worker_id(worker_id)
    safe_error = error_type if isinstance(error_type, str) and _ERROR.fullmatch(error_type) else 'UnknownError'
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await ol.journal_mode(conn) != 'v2':
                return False
            prior = await conn.fetchrow('''SELECT status, failure_started_at FROM adaptive_worker_heartbeat
                WHERE worker_id = $1 FOR UPDATE''', worker_id)
            transition = prior is None or prior['status'] != 'failing'
            started = now if transition or prior['failure_started_at'] is None else prior['failure_started_at']
            if transition:
                await _insert_event(conn, _digest(STREAM, 'WORKER_FAILED', worker_id, started.isoformat()), now,
                                    'WORKER_FAILED', {'worker_id': worker_id, 'error': safe_error},
                                    worker_id=worker_id)
            await conn.execute('''INSERT INTO adaptive_worker_heartbeat (worker_id, status, last_heartbeat_at,
                    last_evaluation_at, last_error, blocking_reason, consecutive_failures, failure_started_at)
                VALUES ($1, 'failing', $2, $2, $3, 'WORKER_EVALUATION_FAILED', 1, $4)
                ON CONFLICT (worker_id) DO UPDATE SET status = 'failing', last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                    last_evaluation_at = EXCLUDED.last_evaluation_at, last_error = EXCLUDED.last_error,
                    blocking_reason = EXCLUDED.blocking_reason,
                    consecutive_failures = adaptive_worker_heartbeat.consecutive_failures + 1,
                    failure_started_at = EXCLUDED.failure_started_at''', worker_id, now, safe_error, started)
            return True


async def cutover_to_v2(pool, now, *, allow_empty_state=False, note=''):
    """Initialise durable state from the newest v1 proposal state and activate v2.

    Waits for any in-flight evaluation (same advisory lock), so v1 and v2 writes
    never interleave. Fails closed on missing, corrupt or incompatible v1 state
    unless the operator explicitly accepts an empty initial state.
    """
    now = now.astimezone(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock($1)', ol.LOCK_ID)
            await _validate_v2_schema_conn(conn)
            if await ol.journal_mode(conn) == 'v2':
                return {'status': 'ALREADY_V2'}
            source = await conn.fetchrow('''SELECT evaluation_id, evaluated_at, entry FROM adaptive_decision_journal
                WHERE entry ? 'proposal_state' ORDER BY evaluated_at DESC, evaluation_id DESC LIMIT 1''')
            if source is None:
                if not allow_empty_state:
                    raise LearnerStateMissing('no v1 entry carries proposal_state; review, then rerun with '
                                              'allow_empty_state to start from an empty state')
                state, contrast, origin, evaluated, baseline = {}, None, 'EMPTY_BY_OPERATOR', None, {}
            else:
                entry = _json(source['entry'])
                if not isinstance(entry, dict) or entry.get('algorithm_version') not in ol.STATE_COMPATIBLE_VERSIONS:
                    raise LearnerStateInvalid('newest v1 proposal state has an incompatible algorithm_version')
                state = validate_proposal_state(entry.get('proposal_state'), now)
                contrast = _contrast(entry.get('exploratory_voltage_contrast'))
                origin, evaluated = 'V1:' + source['evaluation_id'], source['evaluated_at']
                # Compare the first v2 evaluation with the last v1 outcome, so a transition
                # right after cutover is recorded instead of hidden inside a baseline.
                baseline = _outcome(entry) if entry.get('decision') is not None else {}
            existing = await conn.fetchval('SELECT revision FROM adaptive_learning_state WHERE stream = $1 FOR UPDATE',
                                           STREAM)
            revision = (existing or 0) + 1
            await conn.execute('''INSERT INTO adaptive_learning_state (stream, state_version, revision,
                    algorithm_version, proposal_state, previous_contrast, last_outcome, last_outcome_fingerprint,
                    last_evidence_fingerprint, last_evaluated_at, initialized_from, initialized_at, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $10::jsonb, NULL, NULL, $7, $8, $9, $9)
                ON CONFLICT (stream) DO UPDATE SET state_version = EXCLUDED.state_version,
                    revision = EXCLUDED.revision, algorithm_version = EXCLUDED.algorithm_version,
                    proposal_state = EXCLUDED.proposal_state, previous_contrast = EXCLUDED.previous_contrast,
                    last_outcome = EXCLUDED.last_outcome, last_outcome_fingerprint = NULL, last_evidence_fingerprint = NULL,
                    last_evaluated_at = EXCLUDED.last_evaluated_at, initialized_from = EXCLUDED.initialized_from,
                    initialized_at = EXCLUDED.initialized_at, updated_at = EXCLUDED.updated_at''',
                STREAM, STATE_VERSION, revision, ol.VERSION, _canonical(state),
                None if contrast is None else _canonical(contrast), evaluated, origin, now, _canonical(baseline))
            await _insert_event(conn, _digest(STREAM, 'JOURNAL_V2_CUTOVER', revision, origin), now,
                                'JOURNAL_V2_CUTOVER', {'initialized_from': origin, 'state_revision': revision,
                                                       'baseline_outcome': baseline or None}, revision=revision)
            # A snapshot from an earlier v2 period must never be shown as current.
            await conn.execute('DELETE FROM adaptive_latest_evaluation WHERE stream = $1', STREAM)
            await conn.execute('''UPDATE adaptive_journal_control SET mode = 'v2', changed_at = $1,
                change_note = $2 WHERE id = 1''', now, ('cutover to v2; ' + str(note))[:200])
            return {'status': 'CUTOVER_COMPLETE', 'initialized_from': origin, 'revision': revision}


async def rollback_to_v1(pool, now, *, note=''):
    """Return writing to v1, handing the durable state to the v1 journal first.

    The handoff entry carries proposal_state, so the v1 worker resumes with the
    same cooldown and pending confirmation instead of a stale pre-cutover state.
    """
    now = now.astimezone(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock($1)', ol.LOCK_ID)
            await _validate_v2_schema_conn(conn)
            if await ol.journal_mode(conn) != 'v2':
                return {'status': 'ALREADY_V1'}
            row, state, contrast, _ = await _load_state(conn, now)
            await conn.execute('''UPDATE adaptive_journal_control SET mode = 'v1', changed_at = $1,
                change_note = $2 WHERE id = 1''', now, ('rollback to v1; ' + str(note))[:200])
            entry = {
                'evaluation_id': _digest('v2-rollback-handoff', row['revision'], now.isoformat()),
                'evaluated_at': now.isoformat(), 'algorithm_version': ol.VERSION, 'decision': 'BLOCKED',
                'evidence_level': 'EXPLORATORY', 'time_provenance': 'COLLECTION_TIME_ONLY',
                'reason_codes': ['JOURNAL_V2_ROLLBACK_STATE_HANDOFF'],
                'explanation': 'Journal v2 was rolled back; durable learner state handed back to Journal v1.',
                'proposal_state': state, 'exploratory_voltage_contrast': contrast,
                'hardware_write_attempted': False, 'v2_state_revision': row['revision'],
            }
            await conn.execute('''INSERT INTO adaptive_decision_journal (evaluation_id, evaluated_at, entry)
                VALUES ($1, $2, $3::jsonb)''', entry['evaluation_id'], now, json.dumps(entry, allow_nan=False))
            return {'status': 'ROLLBACK_COMPLETE', 'handoff_evaluation_id': entry['evaluation_id'],
                    'state_revision': row['revision']}
