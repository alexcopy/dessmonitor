"""Adaptive Decision Journal v2 against a disposable PostgreSQL database.

Opt-in: JOURNAL_V2_LOCAL_POSTGRES=1, run under ``tests/disposable_postgres.py run --``.
There is no default DSN: ``tests/pg_isolation.py`` proves the target is this run's
disposable server before any DROP/migration. Every test recreates its own tables and
removes them afterwards. Pure unit tests (validation and event derivation) always run.
"""
import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.control import adaptive_journal_v2 as v2
from app.control import online_output_learning as ol
import pg_isolation

ROOT = Path(__file__).resolve().parents[1]
DSN = pg_isolation.configured_dsn()  # no fallback; validated by pg_isolation on every connection
V2_OBJECTS_DROP = '''DROP TABLE IF EXISTS adaptive_worker_heartbeat, adaptive_state_repetitions,
    adaptive_decision_events, adaptive_latest_evaluation, adaptive_learning_state,
    adaptive_journal_control, adaptive_decision_journal, inverter_metrics CASCADE;
    DROP FUNCTION IF EXISTS adaptive_decision_journal_v2_write_guard() CASCADE'''
# Close to real time so API health staleness checks behave as in production.
NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
COLUMNS = ('time', 'working_mode', 'battery_voltage', 'battery_soc', 'battery_current_chg',
           'battery_current_dis', 'pv_power_w', 'output_power_w')


def hold_rows(end):
    """Three hours of reported Line/Invert cycling that evaluates to HOLD / NO_CANDIDATE."""
    rows = []
    for i in range(36):
        line = (i // 3) % 2 == 0
        rows.append((end - timedelta(minutes=175 - i * 5), 'Line Mode' if line else 'Invert Mode',
                     round(26.6 + (0.3 if line else -0.2) + (i % 3) * 0.01, 2), 60.0, 0.0,
                     0.0 if line else 12.0 + i % 3, 400.0 + i, 600.0 + i))
    return rows


def charging_rows(end):
    """Line-mode charging that produces a HYPOTHETICAL_LOWER candidate (historical 27.0 V)."""
    return [(end - timedelta(minutes=30 - i * 5), 'Line Mode', 26.8 + i * .03, 50.0, 5.0 + i, 0.0,
             500.0 + i * 20, 500.0 + i * 10) for i in range(7)]


VALID_STATE = {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': 1, 'pending_response_v': None,
               'active': 'HYPOTHETICAL_LOWER:26.5', 'last_change_at': '2026-11-10T09:00:00+00:00',
               'last_collection_time': '2026-11-10T09:00:00+00:00', 'last_sample_signature': ['x'],
               'confirmation_signatures': [['x']], 'daily_review_date_utc': '2026-11-10',
               'daily_behavior_review': {'status': 'REVIEWED'}}


class ProposalStateValidationTests(unittest.TestCase):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def test_valid_state_round_trips(self):
        self.assertEqual(v2.validate_proposal_state(dict(VALID_STATE), self.now), VALID_STATE)
        self.assertEqual(v2.validate_proposal_state({}, self.now), {})

    def test_corrupt_values_fail_closed(self):
        corrupt = [
            {'stable_count': 'two'}, {'stable_count': True}, {'stable_count': 3},
            {'pending': 'DROP TABLE'}, {'active': ['x']}, {'stable_count': 1, 'pending': None},
            {'active': 'HYPOTHETICAL_LOWER:26.5'},  # cooldown start unknown
            {'last_change_at': '2026-11-10T09:00:00'},  # naive
            {'last_change_at': 'yesterday'}, {'last_change_at': '2026-11-11T00:00:00+00:00'},  # future
            {'pending_response_v': float('nan')}, {'confirmation_signatures': 'x'},
            {'confirmation_signatures': [['x']] * 33}, {'daily_review_date_utc': '10/11/2026'},
            {'unexpected_key': 1}, [], None]
        for state in corrupt:
            with self.subTest(state=state):
                with self.assertRaises(v2.LearnerStateInvalid):
                    v2.validate_proposal_state(state, self.now)


class EventDerivationTests(unittest.TestCase):
    base = {'decision': 'HOLD', 'stability': 'NO_CANDIDATE', 'threshold': None, 'candidate_voltage': None,
            'proposed_voltage': None, 'pending': None, 'stable_count': 0, 'active': None, 'last_change_at': None,
            'cooldown': 'NONE', 'blockers': [], 'battery_v_status': 'VALID', 'response_changed': False,
            'health_status': 'degraded', 'blocking_reason': 'X'}

    def test_identical_outcome_has_no_event_and_first_is_baseline(self):
        self.assertEqual(v2._derive_events(self.base, dict(self.base)), [])
        self.assertEqual([e for e, _ in v2._derive_events({}, self.base)], ['BASELINE_RECORDED'])

    def test_each_material_change_maps_to_one_event(self):
        cases = {
            'DECISION_CHANGED': {'decision': 'INSUFFICIENT_DATA'},
            'HYPOTHESIS_CREATED': {'candidate_voltage': 26.5},
            'CONFIRMATION_CHANGED': {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': 1},
            'EVIDENCE_BLOCKED': {'blockers': ['LATEST_COLLECTION_STALE']},
            'BATTERY_OBSERVATION_CHANGED': {'battery_v_status': 'INVALID_ZERO_VOLTAGE'},
            'BATTERY_RESPONSE_CHANGED': {'response_changed': True},
        }
        for expected, change in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual([e for e, _ in v2._derive_events(self.base, dict(self.base, **change))], [expected])
        self.assertEqual([e for e, _ in v2._derive_events(dict(self.base, blockers=['LATEST_COLLECTION_STALE']),
                                                          self.base)], ['EVIDENCE_RECOVERED'])


@unittest.skipUnless(os.environ.get('JOURNAL_V2_LOCAL_POSTGRES') == '1',
                     'requires disposable local PostgreSQL (JOURNAL_V2_LOCAL_POSTGRES=1)')
class JournalV2PostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.asyncpg = asyncpg
        self.extra_pools = []
        # Isolation is proven on each connection before any destructive statement.
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=4)
        async with self.pool.acquire() as c:
            await c.execute(V2_OBJECTS_DROP)
            await c.execute((ROOT / 'migrations' / '001_adaptive_decision_journal.sql').read_text())
            for _ in range(2):  # migration 002 is repeatable
                await c.execute((ROOT / 'migrations' / '002_adaptive_decision_journal_v2.sql').read_text())
            await c.execute('''CREATE TABLE inverter_metrics (time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT,
                battery_voltage DOUBLE PRECISION, battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
                battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')

    async def asyncTearDown(self):
        for pool in self.extra_pools:
            await pool.close()
        try:
            # Leave no v2 mode/trigger behind for other suites sharing the disposable database.
            async with self.pool.acquire() as c:
                await c.execute(V2_OBJECTS_DROP)
        finally:
            await asyncio.wait_for(self.pool.close(), timeout=5)

    async def new_pool(self):
        pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
        self.extra_pools.append(pool)
        return pool

    async def insert(self, rows):
        async with self.pool.acquire() as c:
            await c.executemany('INSERT INTO inverter_metrics (%s) VALUES (%s)' % (
                ','.join(COLUMNS), ','.join('$%d' % (i + 1) for i in range(len(COLUMNS)))), rows)

    async def value(self, sql, *args):
        async with self.pool.acquire() as c:
            return await c.fetchval(sql, *args)

    async def events(self, event_type=None):
        async with self.pool.acquire() as c:
            rows = await c.fetch('SELECT event_type, payload FROM adaptive_decision_events ORDER BY event_time, event_id')
        return [r['event_type'] for r in rows if event_type in (None, r['event_type'])]

    async def state(self):
        async with self.pool.acquire() as c:
            row = await c.fetchrow('SELECT * FROM adaptive_learning_state WHERE stream = $1', 'battery')
        return dict(row) if row else None

    async def start_v2_from_v1(self, rows, at, **kwargs):
        """Realistic cutover: v1 has been writing; v2 initialises from its newest state."""
        await self.insert(rows)
        self.assertTrue(await ol.run_evaluation(self.pool, at, **kwargs))
        result = await v2.cutover_to_v2(self.pool, at + timedelta(seconds=1))
        self.assertEqual(result['status'], 'CUTOVER_COMPLETE')
        self.assertTrue(result['initialized_from'].startswith('V1:'))

    # 1 ------------------------------------------------------------------------------------
    async def test_1000_identical_hold_evaluations_do_not_flood_the_journal(self):
        await self.start_v2_from_v1(hold_rows(NOW), NOW)
        v1_rows = await self.value('SELECT count(*) FROM adaptive_decision_journal')
        v1_entry_bytes = await self.value('SELECT pg_column_size(entry) FROM adaptive_decision_journal LIMIT 1')
        results = [await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=2, milliseconds=200 * i),
                                              worker_id='w1') for i in range(1000)]
        self.assertTrue(all(results))
        self.assertEqual(await self.value('SELECT count(*) FROM adaptive_decision_journal'), v1_rows)
        self.assertEqual(await self.events(), ['JOURNAL_V2_CUTOVER'])
        self.assertEqual(await self.value('SELECT count(*) FROM adaptive_latest_evaluation'), 1)
        async with self.pool.acquire() as c:
            reps = await c.fetch('SELECT * FROM adaptive_state_repetitions')
            v2_bytes = await c.fetchval('''SELECT (SELECT sum(pg_column_size(entry)) FROM adaptive_latest_evaluation)
                + (SELECT sum(pg_column_size(payload)) FROM adaptive_decision_events)
                + (SELECT sum(pg_column_size(outcome)) FROM adaptive_state_repetitions)
                + (SELECT sum(pg_column_size(proposal_state) + pg_column_size(last_outcome))
                   FROM adaptive_learning_state)''')
        self.assertEqual(len(reps), 1)
        self.assertEqual(reps[0]['evaluation_count'], 1000)
        self.assertEqual(json.loads(reps[0]['outcome'])['decision'], 'HOLD')
        self.assertEqual(json.loads(reps[0]['outcome'])['stability'], 'NO_CANDIDATE')
        # The v1 evaluation had already processed the latest sample: all 1000 are repeats.
        self.assertEqual((reps[0]['new_evidence_count'], reps[0]['repeated_evidence_count']), (0, 1000))
        # Live v2 data stays around one full entry, versus 1000 full entries under v1.
        self.assertLess(v2_bytes, 3 * v1_entry_bytes)

    # 2 ------------------------------------------------------------------------------------
    async def test_genuine_and_cached_evidence_are_counted_separately(self):
        await self.start_v2_from_v1(hold_rows(NOW), NOW)
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=5), worker_id='w1'))
        before = await self.state()
        last = hold_rows(NOW)[-1]
        await self.insert([(NOW + timedelta(seconds=60), last[1], 26.41, 60.0, 0.0, 13.0, 436.0, 636.0)])
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=65), worker_id='w1'))
        after_new = await self.state()
        self.assertNotEqual(after_new['last_evidence_fingerprint'], before['last_evidence_fingerprint'])
        self.assertEqual(after_new['revision'], before['revision'] + 1)
        # Cached repeat: same payload at a new collection time is not new evidence.
        await self.insert([(NOW + timedelta(seconds=120), last[1], 26.41, 60.0, 0.0, 13.0, 436.0, 636.0)])
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=125), worker_id='w1'))
        after_cached = await self.state()
        self.assertEqual(after_cached['last_evidence_fingerprint'], after_new['last_evidence_fingerprint'])
        self.assertEqual(json.loads(after_cached['proposal_state'])['last_collection_time'],
                         json.loads(after_new['proposal_state'])['last_collection_time'])
        async with self.pool.acquire() as c:
            totals = await c.fetchrow('''SELECT sum(evaluation_count) AS n, sum(new_evidence_count) AS new,
                sum(repeated_evidence_count) AS repeated FROM adaptive_state_repetitions''')
        self.assertEqual((totals['n'], totals['new'], totals['repeated']), (3, 1, 2))
        self.assertEqual(await self.events(), ['JOURNAL_V2_CUTOVER'])

    # 3 ------------------------------------------------------------------------------------
    async def test_decision_transition_records_exactly_one_event(self):
        await self.start_v2_from_v1(hold_rows(NOW), NOW)
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=5), worker_id='w1'))
        for i in range(30):  # telemetry stops: latest collection becomes stale
            self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=10, seconds=i),
                                                       worker_id='w1'))
        self.assertEqual(await self.events('DECISION_CHANGED'), ['DECISION_CHANGED'])
        self.assertEqual(await self.events('EVIDENCE_BLOCKED'), ['EVIDENCE_BLOCKED'])
        latest = json.loads(await self.value('SELECT entry FROM adaptive_latest_evaluation'))
        self.assertEqual(latest['decision'], 'INSUFFICIENT_DATA')
        await self.insert([(NOW + timedelta(minutes=11), 'Line Mode', 26.9, 60.0, 0.0, 0.0, 450.0, 650.0)])
        for i in range(5):
            self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=11, seconds=10 + i),
                                                       worker_id='w1'))
        self.assertEqual(await self.events('DECISION_CHANGED'), ['DECISION_CHANGED'] * 2)
        self.assertEqual(await self.events('EVIDENCE_RECOVERED'), ['EVIDENCE_RECOVERED'])

    # 4 ------------------------------------------------------------------------------------
    async def test_concurrent_workers_cannot_duplicate_transitions_or_confirmations(self):
        await self.start_v2_from_v1(charging_rows(NOW), NOW, historical_battery_v=27.0)
        pools = [self.pool, await self.new_pool(), await self.new_pool()]
        await self.insert([(NOW + timedelta(minutes=1), 'Line Mode', 27.04, 50.0, 13.0, 0.0, 700.0, 600.0)])
        at = NOW + timedelta(minutes=1, seconds=5)
        results = await asyncio.gather(*(v2.run_evaluation_v2(p, at, worker_id='w%d' % i,
                                                              historical_battery_v=27.0)
                                         for i, p in enumerate(pools)))
        self.assertEqual(sum(results), 1)
        state = json.loads((await self.state())['proposal_state'])
        self.assertEqual(state['active'], 'HYPOTHETICAL_LOWER:26.5')
        self.assertEqual(state['stable_count'], 2)
        self.assertEqual(await self.events('HYPOTHESIS_CONFIRMED'), ['HYPOTHESIS_CONFIRMED'])
        # Later racing rounds without new telemetry add nothing.
        for round_index in range(5):
            at = NOW + timedelta(minutes=2, seconds=round_index)
            await asyncio.gather(*(v2.run_evaluation_v2(p, at, worker_id='w%d' % i, historical_battery_v=27.0)
                                   for i, p in enumerate(pools)))
        self.assertEqual(await self.events('HYPOTHESIS_CONFIRMED'), ['HYPOTHESIS_CONFIRMED'])
        async with self.pool.acquire() as c:
            duplicate_types = await c.fetchval('''SELECT count(*) FROM (SELECT event_type, state_revision
                FROM adaptive_decision_events WHERE state_revision IS NOT NULL
                GROUP BY event_type, state_revision HAVING count(*) > 1) d''')
            standby = await c.fetchval("SELECT count(*) FROM adaptive_worker_heartbeat")
        self.assertEqual(duplicate_types, 0)
        self.assertEqual(standby, 3)  # losers still record liveness

    # 5 ------------------------------------------------------------------------------------
    async def test_sequential_workers_do_not_duplicate_events(self):
        await self.start_v2_from_v1(hold_rows(NOW), NOW)
        other = await self.new_pool()
        for i in range(40):
            pool, worker = (self.pool, 'a') if i % 2 == 0 else (other, 'b')
            self.assertTrue(await v2.run_evaluation_v2(pool, NOW + timedelta(seconds=2 + i), worker_id=worker))
        self.assertEqual(await self.events(), ['JOURNAL_V2_CUTOVER'])
        self.assertEqual(await self.value('SELECT sum(evaluation_count) FROM adaptive_state_repetitions'), 40)

    # 6 + 7 ------------------------------------------------------------------------------------
    async def test_repeated_failure_then_recovery(self):
        await self.start_v2_from_v1(hold_rows(NOW), NOW)
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=5), worker_id='w1'))
        state_before = await self.state()
        for i in range(5):
            self.assertTrue(await v2.record_worker_failure_v2(self.pool, NOW + timedelta(minutes=1 + i),
                                                              'RuntimeError', worker_id='w1'))
        self.assertEqual(await self.events('WORKER_FAILED'), ['WORKER_FAILED'])
        async with self.pool.acquire() as c:
            beat = await c.fetchrow("SELECT * FROM adaptive_worker_heartbeat WHERE worker_id = 'w1'")
        self.assertEqual((beat['status'], beat['consecutive_failures'], beat['last_error']), ('failing', 5, 'RuntimeError'))
        self.assertEqual(beat['last_heartbeat_at'], NOW + timedelta(minutes=5))
        self.assertEqual(beat['last_success_at'], NOW + timedelta(seconds=5))
        self.assertEqual(await self.state(), state_before)  # failures never touch learner state
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=6), worker_id='w1'))
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=6, seconds=1), worker_id='w1'))
        self.assertEqual(await self.events('WORKER_RECOVERED'), ['WORKER_RECOVERED'])
        async with self.pool.acquire() as c:
            beat = await c.fetchrow("SELECT * FROM adaptive_worker_heartbeat WHERE worker_id = 'w1'")
        self.assertEqual((beat['status'] in ('healthy', 'degraded'), beat['consecutive_failures'], beat['last_error']),
                         (True, 0, None))
        # A second, separate outage is a new transition pair.
        await v2.record_worker_failure_v2(self.pool, NOW + timedelta(minutes=7), 'TimeoutError', worker_id='w1')
        await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=8), worker_id='w1')
        self.assertEqual(len(await self.events('WORKER_FAILED')), 2)
        self.assertEqual(len(await self.events('WORKER_RECOVERED')), 2)

    # 8 ------------------------------------------------------------------------------------
    async def test_restart_preserves_proposal_state_and_cooldown_exactly(self):
        await self.start_v2_from_v1(charging_rows(NOW), NOW, historical_battery_v=27.0)
        await self.insert([(NOW + timedelta(minutes=1), 'Line Mode', 27.04, 50.0, 13.0, 0.0, 700.0, 600.0)])
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=1, seconds=5),
                                                   worker_id='w1', historical_battery_v=27.0))
        confirmed = await self.state()
        proposal = json.loads(confirmed['proposal_state'])
        self.assertEqual(proposal['active'], 'HYPOTHETICAL_LOWER:26.5')
        for i in range(3):
            await v2.record_worker_failure_v2(self.pool, NOW + timedelta(minutes=2 + i), 'RuntimeError', worker_id='w1')
        await self.pool.close()
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=4)  # restart
        self.assertEqual(await self.state(), confirmed)
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=10),
                                                   worker_id='w1', historical_battery_v=27.0))
        resumed = json.loads((await self.state())['proposal_state'])
        self.assertEqual((resumed['active'], resumed['last_change_at']), (proposal['active'], proposal['last_change_at']))
        latest = json.loads(await self.value('SELECT entry FROM adaptive_latest_evaluation'))
        expected = 3600 - int((NOW + timedelta(minutes=10) - datetime.fromisoformat(proposal['last_change_at'])).total_seconds())
        self.assertEqual(latest['cooldown_remaining_seconds'], expected)

    # 9 ------------------------------------------------------------------------------------
    async def test_invalid_legacy_or_durable_state_fails_closed(self):
        corrupt = {'algorithm_version': ol.VERSION, 'evaluated_at': NOW.isoformat(), 'decision': 'HOLD',
                   'proposal_state': {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': 'two'}}
        async with self.pool.acquire() as c:
            await c.execute('INSERT INTO adaptive_decision_journal (evaluation_id, evaluated_at, entry) VALUES ($1,$2,$3)',
                            'corrupt', NOW, json.dumps(corrupt))
        with self.assertRaises(v2.LearnerStateInvalid):
            await v2.cutover_to_v2(self.pool, NOW + timedelta(seconds=1))
        self.assertEqual(await self.value('SELECT mode FROM adaptive_journal_control'), 'v1')
        self.assertIsNone(await self.state())
        # An older valid state is NOT silently used instead of the newer corrupt one.
        async with self.pool.acquire() as c:
            await c.execute('INSERT INTO adaptive_decision_journal (evaluation_id, evaluated_at, entry) VALUES ($1,$2,$3)',
                            'older-valid', NOW - timedelta(hours=1), json.dumps(dict(corrupt, proposal_state={})))
            await c.execute("UPDATE adaptive_decision_journal SET entry = jsonb_set(entry, '{algorithm_version}', '\"future-9\"') WHERE evaluation_id = 'corrupt'")
        with self.assertRaises(v2.LearnerStateInvalid):
            await v2.cutover_to_v2(self.pool, NOW + timedelta(seconds=1))
        async with self.pool.acquire() as c:
            await c.execute('DELETE FROM adaptive_decision_journal')
        with self.assertRaises(v2.LearnerStateMissing):
            await v2.cutover_to_v2(self.pool, NOW + timedelta(seconds=1))
        self.assertEqual(await self.value('SELECT mode FROM adaptive_journal_control'), 'v1')
        # Operator-approved empty start, then corrupt the durable row: evaluation refuses.
        await self.insert(hold_rows(NOW))
        self.assertEqual((await v2.cutover_to_v2(self.pool, NOW, allow_empty_state=True))['initialized_from'],
                         'EMPTY_BY_OPERATOR')
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=5), worker_id='w1'))
        async with self.pool.acquire() as c:
            await c.execute('''UPDATE adaptive_learning_state SET proposal_state =
                '{"pending": "HYPOTHETICAL_LOWER:26.5", "stable_count": 7}'::jsonb''')
        corrupted = await self.state()
        latest_before = await self.value('SELECT evaluated_at FROM adaptive_latest_evaluation')
        with self.assertRaises(v2.LearnerStateInvalid):
            await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=10), worker_id='w1')
        self.assertEqual(await self.state(), corrupted)  # never silently reset
        self.assertEqual(await self.value('SELECT evaluated_at FROM adaptive_latest_evaluation'), latest_before)
        async with self.pool.acquire() as c:
            await c.execute("UPDATE adaptive_learning_state SET algorithm_version = 'online-exploratory-1'")
        with self.assertRaises(v2.LearnerStateInvalid):
            await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=11), worker_id='w1')
        async with self.pool.acquire() as c:
            await c.execute('DELETE FROM adaptive_latest_evaluation; DELETE FROM adaptive_learning_state')
        with self.assertRaises(v2.LearnerStateMissing):
            await v2.run_evaluation_v2(self.pool, NOW + timedelta(seconds=12), worker_id='w1')

    # 10 ------------------------------------------------------------------------------------
    async def test_rollback_before_commit_and_replay_after_commit(self):
        await self.start_v2_from_v1(hold_rows(NOW), NOW)
        at = NOW + timedelta(seconds=5)
        snapshot = await self.state()
        with patch.object(v2, '_heartbeat_success', side_effect=RuntimeError('crash before commit')):
            with self.assertRaises(RuntimeError):
                await v2.run_evaluation_v2(self.pool, at, worker_id='w1')
        self.assertEqual(await self.state(), snapshot)
        self.assertEqual(await self.events(), ['JOURNAL_V2_CUTOVER'])
        self.assertEqual(await self.value('SELECT count(*) FROM adaptive_state_repetitions'), 0)
        self.assertEqual(await self.value('SELECT count(*) FROM adaptive_latest_evaluation'), 0)
        self.assertTrue(await v2.run_evaluation_v2(self.pool, at, worker_id='w1'))
        committed = await self.state()
        for _ in range(3):  # retry of an already committed evaluation is a no-op
            self.assertFalse(await v2.run_evaluation_v2(self.pool, at, worker_id='w1'))
            self.assertFalse(await v2.run_evaluation_v2(self.pool, at - timedelta(seconds=1), worker_id='w2'))
        self.assertEqual(await self.state(), committed)
        self.assertEqual(await self.events(), ['JOURNAL_V2_CUTOVER'])
        async with self.pool.acquire() as c:  # skipped worker still shows liveness
            self.assertEqual(await c.fetchval("SELECT status FROM adaptive_worker_heartbeat WHERE worker_id='w2'"), 'standby')
        self.assertEqual(await self.value('SELECT sum(evaluation_count) FROM adaptive_state_repetitions'), 1)

    # 11 ------------------------------------------------------------------------------------
    async def test_v1_history_export_mixed_writers_retention_and_rollback(self):
        async with self.pool.acquire() as c:  # an entry already past v1 retention
            await c.execute('INSERT INTO adaptive_decision_journal (evaluation_id, evaluated_at, entry) VALUES ($1,$2,$3)',
                            'expired', NOW - timedelta(days=31), json.dumps({'decision': 'HOLD'}))
        await self.start_v2_from_v1(charging_rows(NOW), NOW, historical_battery_v=27.0)
        v1_ids = {r for r in await self._ids()} - {'expired'}
        # Old (not yet upgraded) v1 replica: direct INSERT is refused by the guard trigger.
        async with self.pool.acquire() as c:
            with self.assertRaises(self.asyncpg.exceptions.ObjectNotInPrerequisiteStateError):
                await c.execute('INSERT INTO adaptive_decision_journal (evaluation_id, evaluated_at, entry) VALUES ($1,$2,$3)',
                                'old-replica', NOW, '{}')
        # Upgraded v1 code path stands down while v2 is active.
        self.assertFalse(await ol.run_evaluation(self.pool, NOW + timedelta(minutes=1)))
        self.assertFalse(await ol._record_worker_failure(self.pool, NOW + timedelta(minutes=1), 'RuntimeError'))
        await self.insert([(NOW + timedelta(minutes=1), 'Line Mode', 27.04, 50.0, 13.0, 0.0, 700.0, 600.0)])
        self.assertTrue(await v2.run_evaluation_v2(self.pool, NOW + timedelta(minutes=1, seconds=5),
                                                   worker_id='w1', historical_battery_v=27.0))
        ids = set(await self._ids())
        self.assertEqual(ids, v1_ids)  # v1 history intact; expired row removed by unchanged retention
        await self._assert_v1_api_and_export(v1_ids)
        # Rollback hands the confirmed state back to v1, which resumes with the same cooldown.
        v2_state = json.loads((await self.state())['proposal_state'])
        result = await v2.rollback_to_v1(self.pool, NOW + timedelta(minutes=2))
        self.assertEqual(result['status'], 'ROLLBACK_COMPLETE')
        self.assertTrue(await ol.run_evaluation(self.pool, NOW + timedelta(minutes=3), historical_battery_v=27.0))
        async with self.pool.acquire() as c:
            resumed = json.loads(await c.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(resumed['proposal_state']['active'], v2_state['active'])
        self.assertEqual(resumed['proposal_state']['last_change_at'], v2_state['last_change_at'])
        self.assertGreater(resumed['cooldown_remaining_seconds'], 3000)

    async def _ids(self):
        async with self.pool.acquire() as c:
            return [r['evaluation_id'] for r in await c.fetch('SELECT evaluation_id FROM adaptive_decision_journal')]

    async def _api(self, path='/api/controls/decisions', **kwargs):
        from app import web_routes
        router = web_routes.create_auth_router(None, None)
        endpoint = next(r.endpoint for r in router.routes if r.path == path)
        with patch.object(web_routes, '_check_auth', return_value=(True, 'user')), \
             pg_isolation.app_connections(), \
             patch.dict(os.environ, {'ADAPTIVE_OUTPUT_LEARNING_ENABLED': 'true',
                                     'WEB_AUTH_SESSION_SECRET': 'a' * 64}):
            response = await endpoint(object(), **kwargs)
            if path.endswith('/export'):
                return response.status_code, json.loads(b''.join([c async for c in response.body_iterator]))
        return response.status_code, json.loads(response.body)

    async def _assert_v1_api_and_export(self, v1_ids):
        status, payload = await self._api(days=2, limit=50)
        self.assertEqual(status, 200)
        self.assertEqual({d['evaluation_id'] for d in payload['decisions']} & v1_ids, v1_ids)
        status, exported = await self._api('/api/controls/decisions/export', days=2)
        self.assertEqual(status, 200)
        self.assertEqual({d['evaluation_id'] for d in exported['decisions']}, v1_ids)

    # 12 ------------------------------------------------------------------------------------
    async def test_api_shows_v2_latest_and_health_never_stale_v1(self):
        real_now = datetime.now(timezone.utc)
        await self.start_v2_from_v1(hold_rows(real_now - timedelta(seconds=30)), real_now - timedelta(seconds=20))
        status, payload = await self._api(days=1, limit=50)
        self.assertEqual(status, 200)
        self.assertEqual(payload['journal']['mode'], 'v2')
        self.assertIsNone(payload['latest_decision'])  # the v1 row is history, not the latest v2 evaluation
        self.assertEqual(payload['worker_health']['blocking_reason'], 'NO_WORKER_HEARTBEAT')
        self.assertEqual(len(payload['decisions']), 1)
        self.assertTrue(await v2.run_evaluation_v2(self.pool, real_now - timedelta(seconds=10), worker_id='w1'))
        self.assertTrue(await v2.run_evaluation_v2(self.pool, real_now - timedelta(seconds=5), worker_id='w1'))
        await v2.record_worker_failure_v2(self.pool, real_now - timedelta(seconds=7), 'RuntimeError', worker_id='w2')
        status, payload = await self._api(days=1, limit=50)
        latest, health, journal = payload['latest_decision'], payload['worker_health'], payload['journal']
        self.assertEqual(latest['journal_version'], 'v2')
        # Same outcome 5 s later: snapshot kept, only last_evaluated_at / counter advance.
        self.assertEqual(latest['evaluated_at'], (real_now - timedelta(seconds=10)).isoformat())
        self.assertEqual(latest['snapshot_evaluated_at'], (real_now - timedelta(seconds=10)).isoformat())
        self.assertEqual(latest['last_evaluated_at'], (real_now - timedelta(seconds=5)).isoformat())
        self.assertEqual(latest['evaluations_since_snapshot'], 1)
        self.assertEqual(health['status_source'], 'JOURNAL_V2_HEARTBEAT')
        self.assertEqual(health['last_successful_evaluation_at'], (real_now - timedelta(seconds=5)).isoformat())
        self.assertEqual(health['status'], 'degraded')  # verified learning remains blocked
        self.assertEqual((health['worker_count'], health['failing_workers']), (2, 1))
        self.assertEqual(journal['current_repetition']['evaluation_count'], 2)
        self.assertEqual([e['event_type'] for e in journal['events']].count('JOURNAL_V2_CUTOVER'), 1)
        self.assertIn('WORKER_FAILED', [e['event_type'] for e in journal['events']])
        self.assertEqual(len(payload['decisions']), 1)  # no new v1 rows
        # The newest evaluator failing is reported, not hidden by an older success.
        await v2.record_worker_failure_v2(self.pool, real_now - timedelta(seconds=1), 'TimeoutError', worker_id='w1')
        _, payload = await self._api(days=1, limit=50)
        self.assertEqual(payload['worker_health']['blocking_reason'], 'WORKER_EVALUATION_FAILED')
        self.assertEqual(payload['worker_health']['last_error'], 'TimeoutError')

    async def test_migration_down_refuses_while_v2_active(self):
        await self.start_v2_from_v1(hold_rows(NOW), NOW)
        down = (ROOT / 'migrations' / '002_adaptive_decision_journal_v2_down.sql').read_text()
        async with self.pool.acquire() as c:
            with self.assertRaises(self.asyncpg.exceptions.RaiseError):
                await c.execute(down)
            await c.execute('ROLLBACK')  # the script's own transaction aborted
            self.assertEqual(await c.fetchval("SELECT to_regclass('adaptive_learning_state') IS NOT NULL"), True)
        await v2.rollback_to_v1(self.pool, NOW + timedelta(seconds=5))
        async with self.pool.acquire() as c:
            await c.execute(down)
            self.assertIsNone(await c.fetchval("SELECT to_regclass('adaptive_learning_state')"))
            self.assertGreaterEqual(await c.fetchval('SELECT count(*) FROM adaptive_decision_journal'), 2)
        self.assertTrue(await ol.run_evaluation(self.pool, NOW + timedelta(seconds=10)))  # v1 fully restored


if __name__ == '__main__':
    unittest.main()
