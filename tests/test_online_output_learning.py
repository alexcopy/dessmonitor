import asyncio
import ast
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.control.online_output_learning import LOCK_ID, evaluate_collected_history, parse_historical_record, run_evaluation, validate_journal_schema
from app.control.sag_learning import SagLearner, SagSample
from test_sag_learning import configuration as sag_configuration

try:
    import asyncpg
except ImportError:
    asyncpg = None

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
DSN = 'postgresql://postgres:synthetic-easun-test@127.0.0.1:55439/easun_test'


def row(minutes=0, **values):
    return {'time': NOW - timedelta(minutes=minutes), 'working_mode': 'Line Mode',
            'battery_voltage': 25.9, 'battery_soc': 50, 'battery_current_chg': 0.0,
            'battery_current_dis': 0.0, 'pv_power_w': 200.0, 'output_power_w': 800.0, **values}


def matched_rows(responses=(0.5, 0.5, 0.5, 0.5), loads=(500, 700, 900, 1100),
                 pv=(200, 400, 600, 800)):
    """Synthetic collection history with explicitly matched load/PV pairs."""
    rows = []
    for index, (response, load, solar) in enumerate(zip(responses, loads, pv)):
        line_at = NOW - timedelta(minutes=(5 + (len(responses) - 1 - index) * 15))
        rows.append(row(time=line_at, working_mode='Line Mode', battery_voltage=26.0,
                        battery_current_dis=0, battery_current_chg=0, pv_power_w=solar,
                        output_power_w=load))
        rows.append(row(time=line_at + timedelta(minutes=2), working_mode='Invert Mode',
                        battery_voltage=26.0 - response, battery_current_dis=15 + index,
                        battery_current_chg=0, pv_power_w=solar + 25, output_power_w=load + 30))
    return rows


def verified_sag_observations():
    observations = []
    start = NOW - timedelta(hours=2)
    for index, sag in enumerate((0.30, 0.32, 0.31, 0.30)):
        at = start + timedelta(minutes=index * 5)
        for offset, voltage in ((0, 26.0), (3, 26.0 - sag)):
            observed = at + timedelta(minutes=offset)
            sample = SagSample(observed, observed + timedelta(seconds=5), f'synthetic-trial-{index}',
                               'Invert Mode', voltage, 1000.0, 15.0, 0.0, True, 20.0, 70.0)
            observations.append({'sample': sample, 'time_basis': 'SYNTHETIC_VERIFIED_DEVICE_TIME_FIXTURE',
                                 'source_provenance': 'synthetic-easun-fixture-v1'})
    return observations


class DiagnosticEvaluationTests(unittest.TestCase):
    def test_learning_worker_has_no_actuator_or_dess_write_dependencies(self):
        module_path = Path('app/control/online_output_learning.py')
        tree = ast.parse(module_path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or '')
        forbidden = ('app.tuya', 'app.control.easun_output_actuator', 'requests', 'httpx', 'socket', 'subprocess')
        self.assertFalse([name for name in imports if any(name == item or name.startswith(item + '.') for item in forbidden)])
        self.assertNotRegex(module_path.read_text(), r'(?i)(?:write_setting|set_output|set_threshold|dess_request|inverter_client)')

        run_tree = ast.parse(Path('run.py').read_text())
        calls = [node for node in ast.walk(run_tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'learning_loop']
        self.assertTrue(calls)
        self.assertTrue(all([arg.id for arg in call.args if isinstance(arg, ast.Name)] ==
                            ['database_url', 'stop_event'] for call in calls))

    def test_verified_synthetic_samples_execute_sag_learner(self):
        learner = SagLearner(sag_configuration())
        result = evaluate_collected_history([], NOW, learner=learner,
                                            verified_observations=verified_sag_observations())
        evidence = result['verified_sag']
        self.assertEqual(evidence['status'], 'VERIFIED')
        self.assertEqual(evidence['evidence_level'], 'VERIFIED')
        self.assertEqual(evidence['observations'].count('OBSERVATION_ACCEPTED'), 4)
        self.assertEqual(evidence['estimate']['observation_count'], 4)
        self.assertTrue(evidence['estimate']['usable'])
        self.assertEqual(evidence['provenance'], [{'time_basis': 'SYNTHETIC_VERIFIED_DEVICE_TIME_FIXTURE',
                                                   'source': 'synthetic-easun-fixture-v1'}])
        # The main decision remains collection-time exploratory even when a separate
        # verified SagLearner result is journaled.
        self.assertEqual(result['evidence_level'], 'EXPLORATORY')

    def test_collection_time_only_never_enters_sag_learner(self):
        learner = SagLearner(sag_configuration())
        sample = verified_sag_observations()[0]['sample']
        with patch.object(learner, 'observe') as observe:
            result = evaluate_collected_history([], NOW, learner=learner, verified_observations=[{
                'sample': sample, 'time_basis': 'COLLECTION_TIME_ONLY', 'source_provenance': 'inverter_metrics.time'}])
        observe.assert_not_called()
        self.assertEqual(result['verified_sag']['status'], 'BLOCKED')
        self.assertIn('DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED', result['verified_sag']['reason_codes'])

    def test_comparable_load_samples_produce_bounded_recency_weighted_estimate(self):
        result = evaluate_collected_history(matched_rows(responses=(0.35, 0.42, 0.50, 0.62)), NOW)
        contrast = result['exploratory_voltage_contrast']
        self.assertEqual(contrast['comparable_pairs'], 4)
        self.assertGreaterEqual(contrast['contrast_v'], contrast['response_bounds_v'][0])
        self.assertLessEqual(contrast['contrast_v'], contrast['response_bounds_v'][1])
        self.assertGreater(contrast['weighted_load_w'], 800)
        self.assertGreater(contrast['weighted_pv_w'], 500)
        self.assertGreater(contrast['contrast_v'], 0.45)

    def test_sudden_load_change_rejects_incomparable_response(self):
        rows = []
        for index in range(3):
            at = NOW - timedelta(minutes=35 - index * 15)
            rows.append(row(time=at, working_mode='Line Mode', battery_voltage=26.0,
                            output_power_w=400 + index * 50, pv_power_w=300))
            rows.append(row(time=at + timedelta(minutes=2), working_mode='Invert Mode', battery_voltage=25.4,
                            battery_current_dis=18, output_power_w=1200 + index * 50, pv_power_w=300))
        result = evaluate_collected_history(rows, NOW)
        contrast = result['exploratory_voltage_contrast']
        self.assertIsNone(contrast['contrast_v'])
        self.assertEqual(contrast['comparable_pairs'], 0)
        self.assertIsNone(result['proposed_voltage'])

    def test_noisy_pairs_keep_a_bounded_robust_estimate(self):
        result = evaluate_collected_history(matched_rows((0.48, 1.6, 0.50, 0.52, 0.51),
                                                          loads=(400, 600, 800, 1000, 1200),
                                                          pv=(100, 300, 500, 700, 900)), NOW)
        contrast = result['exploratory_voltage_contrast']
        self.assertEqual(contrast['candidate_pairs'], 5)
        self.assertEqual(contrast['comparable_pairs'], 4)
        self.assertLess(abs(contrast['contrast_v'] - 0.5), 0.04)
        self.assertLessEqual(abs(contrast['contrast_v']), 2.0)
        self.assertEqual(result['stability'], 'AWAITING_CONFIRMATION')
        self.assertIsNone(result['proposed_voltage'])

    def test_empirical_response_change_reduces_confidence_without_guessing_cause(self):
        initial = evaluate_collected_history(matched_rows((0.5, 0.5, 0.5, 0.5)), NOW)
        changed = evaluate_collected_history(matched_rows((0.9, 0.9, 0.9, 0.9)), NOW + timedelta(minutes=1), initial)
        self.assertIn('EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN', changed['reason_codes'])
        self.assertTrue(changed['exploratory_voltage_contrast']['response_changed'])
        self.assertLess(changed['confidence'], initial['confidence'])
        self.assertEqual(changed['decision'], 'HOLD')
        self.assertIsNone(changed['proposed_voltage'])
        self.assertIn('cause is unknown', changed['explanation'])

    def test_outlier_cannot_confirm_or_change_an_existing_target(self):
        rows = matched_rows()
        first = evaluate_collected_history(rows, NOW)
        self.assertEqual(first['stability'], 'AWAITING_CONFIRMATION')
        compatible = row(time=NOW + timedelta(minutes=1), working_mode='Line Mode', battery_voltage=26.0,
                         battery_current_dis=0, pv_power_w=825, output_power_w=1130)
        confirmed = evaluate_collected_history(rows + [compatible], NOW + timedelta(minutes=1), first)
        self.assertEqual(confirmed['decision'], 'HYPOTHETICAL_RAISE')
        old_target = confirmed['proposal_state']['active']
        outlier = row(time=NOW + timedelta(minutes=2), working_mode='Invert Mode', battery_voltage=24.4,
                      battery_current_dis=20, pv_power_w=825, output_power_w=1130)
        held = evaluate_collected_history(rows + [compatible, outlier], NOW + timedelta(minutes=2), confirmed)
        self.assertEqual(held['decision'], 'HOLD')
        self.assertIsNone(held['proposed_voltage'])
        self.assertEqual(held['proposal_state']['active'], old_target)
        self.assertEqual(held['proposal_state']['stable_count'], 0)

    def test_documented_record_timestamp_and_dst(self):
        receipt = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        parsed = parse_historical_record(['synthetic-id', '2026-06-01 12:30:00'], 'Europe/London', receipt, set())
        self.assertEqual(parsed['device_observed_at'], datetime(2026, 6, 1, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(parsed['received_at'], receipt)
        self.assertFalse(parsed['field_mapping_verified'])
        with self.assertRaises(ValueError):
            parse_historical_record(['synthetic-id', '2026-06-01 12:30:00'], 'Europe/London', receipt, {'synthetic-id'})
        for stamp in ('2026-10-25 01:30:00', '2026-03-29 01:30:00'):
            with self.assertRaises(ValueError):
                parse_historical_record(['another', stamp], 'Europe/London', datetime(2027, 1, 1, tzinfo=timezone.utc), set())

    def test_exploratory_hypothesis_is_historical_and_never_actionable(self):
        rows = [row(50-i*10, working_mode='Line Mode' if i % 2 == 0 else 'Invert Mode',
                    battery_voltage=(25.9+i*.01 if i % 2 == 0 else 25.4+i*.01),
                    battery_current_dis=(0 if i % 2 == 0 else 15+i), output_power_w=500+i*20)
                for i in range(6)]
        pending = evaluate_collected_history(rows, NOW)
        self.assertEqual(pending['decision'], 'HOLD')
        cached = rows[-1].copy()
        cached['time'] = NOW + timedelta(seconds=30)
        repeated = evaluate_collected_history(rows + [cached], NOW + timedelta(seconds=30), pending)
        self.assertEqual(repeated['stability'], 'AWAITING_CONFIRMATION')
        rows.append(row(time=NOW + timedelta(minutes=1), working_mode='Line Mode', battery_voltage=26.0,
                        battery_current_dis=0, output_power_w=600))
        result = evaluate_collected_history(rows, NOW + timedelta(minutes=1), pending)
        self.assertEqual(result['decision'], 'HYPOTHETICAL_RAISE')
        self.assertEqual(result['proposed_voltage'], 26.5)
        self.assertEqual(result['time_provenance'], 'COLLECTION_TIME_ONLY')
        self.assertIn('HISTORICAL_BASELINE_ONLY', result['reason_codes'])
        self.assertIn('BMS_LIMITS_UNKNOWN', result['safety_blockers'])
        self.assertEqual(result['actionability'], 'NON_ACTIONABLE')
        self.assertFalse(result['hardware_write_attempted'])
        self.assertIsNone(result['observation_time'])

    def test_charging_lower_requires_confirmation_and_respects_cooldown(self):
        rows = [row(30-i*5, battery_voltage=26.8+i*.03, battery_current_chg=5+i,
                    output_power_w=500+i*10, pv_power_w=500+i*20) for i in range(7)]
        first = evaluate_collected_history(rows, NOW, historical_battery_v=27.0)
        self.assertEqual(first['decision'], 'HOLD')
        self.assertEqual(first['stability'], 'AWAITING_CONFIRMATION')
        rows.append(row(time=NOW+timedelta(minutes=1), battery_voltage=27.04, battery_current_chg=13, output_power_w=600, pv_power_w=700))
        second = evaluate_collected_history(rows, NOW+timedelta(minutes=1), first, historical_battery_v=27.0)
        self.assertEqual(second['decision'], 'HYPOTHETICAL_LOWER')
        self.assertEqual(second['proposed_voltage'], 26.5)
        self.assertIn('BMS_LIMITS_UNKNOWN', second['safety_blockers'])
        state = dict(second, proposal_state={**second['proposal_state'], 'active': 'different', 'last_change_at': NOW.isoformat()})
        rows.append(row(time=NOW+timedelta(minutes=2), battery_voltage=27.05, battery_current_chg=14, output_power_w=610, pv_power_w=710))
        blocked = evaluate_collected_history(rows, NOW+timedelta(minutes=2), state, historical_battery_v=27.0)
        self.assertEqual(blocked['decision'], 'HOLD')
        self.assertEqual(blocked['stability'], 'COOLDOWN')

    def test_replayed_identical_cloud_values_cannot_support_hypothesis(self):
        rows = [row(50-i*10, working_mode='Line Mode' if i % 2 == 0 else 'Invert Mode',
                    battery_voltage=26 if i % 2 == 0 else 25.4,
                    battery_current_dis=0 if i % 2 == 0 else 15, pv_power_w=200, output_power_w=800)
                for i in range(6)]
        self.assertEqual(evaluate_collected_history(rows, NOW)['decision'], 'INSUFFICIENT_DATA')

    def test_collection_time_is_not_observation_or_receipt_time(self):
        result = evaluate_collected_history([row(10), row(0)], NOW)
        self.assertEqual(result['collection_count'], 2)
        self.assertIsNone(result['observation_time'])
        self.assertIsNone(result['receipt_time'])
        self.assertIn('DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED', result['reason_codes'])
        self.assertIn('CURRENT_THRESHOLD_SETTINGS_UNKNOWN', result['reason_codes'])
        self.assertFalse(result['hardware_write_attempted'])
        self.assertIsNone(result['proposed_voltage'])

    def test_unknown_historical_baseline_is_explained_and_never_actionable(self):
        result = evaluate_collected_history(matched_rows(), NOW, historical_battery_v=None)
        self.assertIn('HISTORICAL_BASELINE_UNKNOWN', result['reason_codes'])
        self.assertEqual(result['actionability'], 'NON_ACTIONABLE')
        self.assertIsNone(result['proposed_voltage'])

    def test_recent_changes_are_explicitly_untrusted_and_missing_data_is_visible(self):
        result = evaluate_collected_history([row(200), row(30), row(20, working_mode='Invert Mode'),
                                             row(10, battery_current_dis=None, pv_power_w=None)], NOW)
        self.assertEqual(result['collection_count'], 3)
        self.assertEqual(result['recent_reported_mode_changes'], 2)
        self.assertIn('MISSING_PV_POWER_W', result['reason_codes'])
        self.assertIn('MISSING_BATTERY_CURRENT_DIS', result['reason_codes'])
        self.assertIsNone(result['short_cycles'])
        self.assertIsNone(result['sag_estimate_v'])

    def test_idempotency_and_stale_state_change(self):
        first = evaluate_collected_history([row()], NOW)
        self.assertEqual(first['evaluation_id'], evaluate_collected_history([row()], NOW)['evaluation_id'])
        stale = evaluate_collected_history([row()], NOW + timedelta(minutes=6))
        self.assertIn('LATEST_COLLECTION_STALE', stale['reason_codes'])
        self.assertNotEqual(first['evaluation_id'], stale['evaluation_id'])

    def test_empty_future_and_naive_rows_do_not_become_evidence(self):
        future = row(-1)
        naive = row(time=NOW.replace(tzinfo=None))
        result = evaluate_collected_history([future, naive], NOW)
        self.assertIn('NO_RECENT_COLLECTIONS', result['reason_codes'])
        self.assertEqual(result['decision'], 'INSUFFICIENT_DATA')


@unittest.skipUnless(asyncpg is not None and os.environ.get('EASUN_TEST_POSTGRES') == '1', 'requires disposable local test database')
class DiagnosticJournalPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT current_database()'), 'easun_test')
            await conn.execute(Path('migrations/001_adaptive_decision_journal.sql').read_text())
            await conn.execute('''CREATE TABLE IF NOT EXISTS inverter_metrics (
                time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT, battery_voltage DOUBLE PRECISION,
                battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
                battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')
            await conn.execute('TRUNCATE inverter_metrics, adaptive_decision_journal')

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_persistence_replay_and_restart(self):
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO inverter_metrics (time,working_mode,battery_voltage) VALUES ($1,'Line Mode',25.9)", NOW)
        self.assertTrue(await run_evaluation(self.pool, NOW))
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM adaptive_decision_journal'), 1)
            entry = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal'))
            self.assertEqual(entry['decision'], 'INSUFFICIENT_DATA')
            self.assertFalse(entry['hardware_write_attempted'])
            self.assertEqual(entry['worker_health']['status'], 'degraded')
            self.assertEqual(entry['worker_health']['last_evaluation_at'], NOW.isoformat())
            self.assertEqual(entry['worker_health']['blocking_reason'], 'VERIFIED_OBSERVATION_SOURCE_UNAVAILABLE')
        # A fresh pool sees the same journal and replay remains idempotent.
        other = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            await run_evaluation(other, NOW)
            async with other.acquire() as conn:
                self.assertEqual(await conn.fetchval('SELECT count(*) FROM adaptive_decision_journal'), 1)
        finally:
            await other.close()

    async def test_replica_lock_and_retention(self):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchval('SELECT pg_advisory_xact_lock($1)', LOCK_ID)
                self.assertFalse(await run_evaluation(self.pool, NOW))
            await conn.execute("INSERT INTO adaptive_decision_journal (evaluation_id,evaluated_at,entry) VALUES ('old',$1,'{}'::jsonb)", NOW - timedelta(days=31))
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM adaptive_decision_journal WHERE evaluation_id='old'"), 0)

    async def test_existing_schema_validation(self):
        await validate_journal_schema(self.pool)
        async with self.pool.acquire() as conn:
            await conn.execute('ALTER TABLE adaptive_decision_journal ALTER COLUMN entry DROP NOT NULL')
        try:
            with self.assertRaises(RuntimeError):
                await validate_journal_schema(self.pool)
        finally:
            async with self.pool.acquire() as conn:
                await conn.execute('ALTER TABLE adaptive_decision_journal ALTER COLUMN entry SET NOT NULL')

    async def test_migration_repeats_on_compatible_schema_and_rejects_incompatible_schema(self):
        migration = Path('migrations/001_adaptive_decision_journal.sql').read_text()
        async with self.pool.acquire() as conn:
            await conn.execute('DROP TABLE adaptive_decision_journal')
            await conn.execute('''CREATE TABLE adaptive_decision_journal (
                evaluation_id TEXT PRIMARY KEY, evaluated_at TIMESTAMPTZ NOT NULL,
                entry JSONB NOT NULL, hardware_write_attempted BOOLEAN NOT NULL DEFAULT FALSE)''')
            await conn.execute(migration)
            await conn.execute(migration)
            await validate_journal_schema(self.pool)
            await conn.execute('DROP TABLE adaptive_decision_journal')
            await conn.execute('''CREATE TABLE adaptive_decision_journal (
                evaluation_id TEXT PRIMARY KEY, evaluated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                entry JSONB NOT NULL, hardware_write_attempted BOOLEAN NOT NULL DEFAULT FALSE)''')
            with self.assertRaises(asyncpg.PostgresError):
                await conn.execute(migration)
            await conn.execute('ROLLBACK')
            self.assertEqual(await conn.fetchval("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='adaptive_decision_journal' AND column_name='evaluated_at'"), 'timestamp without time zone')
            await conn.execute('DROP TABLE adaptive_decision_journal')
            await conn.execute(migration)
            await validate_journal_schema(self.pool)

    async def test_verified_sag_results_and_provenance_are_persisted(self):
        learner = SagLearner(sag_configuration())
        self.assertTrue(await run_evaluation(self.pool, NOW, learner=learner,
                                             verified_observations=verified_sag_observations()))
        async with self.pool.acquire() as conn:
            entry = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal'))
        self.assertEqual(entry['verified_sag']['status'], 'VERIFIED')
        self.assertTrue(entry['verified_sag']['estimate']['usable'])
        self.assertEqual(entry['verified_sag']['estimate']['observation_count'], 4)
        self.assertEqual(entry['verified_sag']['provenance'][0]['source'], 'synthetic-easun-fixture-v1')
        self.assertEqual(entry['evidence_level'], 'EXPLORATORY')
        self.assertEqual(entry['worker_health']['last_successful_evaluation_at'], NOW.isoformat())

    async def test_concurrent_replicas_cannot_count_one_observation_twice(self):
        rows = matched_rows()
        rows[-1].update(output_power_w=3000, pv_power_w=2000, battery_voltage=24.0)
        async with self.pool.acquire() as conn:
            for item in rows:
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', item['time'], item['working_mode'], item['battery_voltage'],
                    item['battery_soc'], item['battery_current_chg'], item['battery_current_dis'], item['pv_power_w'], item['output_power_w'])
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            initial = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(initial['proposal_state']['stable_count'], 0)

        other = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,'Line Mode',26,50,0,0,900,1300)''', NOW+timedelta(minutes=1))
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,'Invert Mode',25.5,50,0,19,925,1330)''', NOW+timedelta(minutes=2))
            await asyncio.gather(run_evaluation(self.pool, NOW+timedelta(minutes=2)),
                                 run_evaluation(other, NOW+timedelta(minutes=2)))
            async with self.pool.acquire() as conn:
                entries = [json.loads(item['entry']) for item in await conn.fetch('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at')]
            latest = entries[-1]
            self.assertEqual(latest['decision'], 'HOLD')
            self.assertEqual(latest['proposal_state']['stable_count'], 1)
            self.assertEqual(len(entries), 2)
        finally:
            await other.close()

    async def test_cooldown_survives_worker_restart(self):
        async with self.pool.acquire() as conn:
            for index in range(7):
                at = NOW - timedelta(minutes=30-index*5)
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,'Line Mode',$2,50,$3,0,$4,$5)''', at, 26.8+index*.03, 5+index, 500+index*20, 500+index*10)
        await run_evaluation(self.pool, NOW, historical_battery_v=27.0)
        async with self.pool.acquire() as conn:
            await conn.execute('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,'Line Mode',27.04,50,13,0,700,600)''', NOW+timedelta(minutes=1))
        await run_evaluation(self.pool, NOW+timedelta(minutes=1), historical_battery_v=27.0)
        async with self.pool.acquire() as conn:
            confirmed = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(confirmed['decision'], 'HYPOTHETICAL_LOWER')
        self.assertEqual(confirmed['proposal_state']['active'], 'HYPOTHETICAL_LOWER:26.5')

        await self.pool.close()
        self.pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
        later = NOW + timedelta(minutes=40)
        raise_rows = matched_rows()
        async with self.pool.acquire() as conn:
            for item in raise_rows:
                at = item['time'] + timedelta(minutes=42)
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,$2,$3,50,$4,$5,$6,$7)''', at, item['working_mode'], item['battery_voltage'],
                    item['battery_current_chg'], item['battery_current_dis'], item['pv_power_w'], item['output_power_w'])
        await run_evaluation(self.pool, NOW+timedelta(minutes=39), historical_battery_v=27.0)
        async with self.pool.acquire() as conn:
            await conn.execute('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,'Line Mode',26,50,0,0,825,1130)''', later)
        other = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            await asyncio.gather(run_evaluation(self.pool, later, historical_battery_v=27.0),
                                 run_evaluation(other, later, historical_battery_v=27.0))
        finally:
            await other.close()
        async with self.pool.acquire() as conn:
            blocked = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(blocked['decision'], 'HOLD')
        self.assertEqual(blocked['stability'], 'COOLDOWN')
        self.assertIn('DRY_RUN_COOLDOWN', blocked['reason_codes'])

    async def test_confirmation_survives_new_pool(self):
        async with self.pool.acquire() as conn:
            for i in range(6):
                await conn.execute('''INSERT INTO inverter_metrics (time,working_mode,battery_voltage,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,$2,$3,0,$4,200,$5)''', NOW-timedelta(minutes=50-i*10),
                    'Line Mode' if i % 2 == 0 else 'Invert Mode',
                    (25.9+i*.01 if i % 2 == 0 else 25.4+i*.01), 0 if i % 2 == 0 else 15+i, 500+i*20)
        await run_evaluation(self.pool, NOW)
        other = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            async with other.acquire() as conn:
                await conn.execute("INSERT INTO inverter_metrics (time,working_mode,battery_voltage,battery_current_chg,battery_current_dis,pv_power_w,output_power_w) VALUES ($1,'Line Mode',26,0,0,200,600)", NOW+timedelta(minutes=1))
            await run_evaluation(other, NOW+timedelta(minutes=1))
            async with other.acquire() as conn:
                entries = [json.loads(r['entry']) for r in await conn.fetch('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at')]
            self.assertEqual([r['decision'] for r in entries], ['HOLD', 'HYPOTHETICAL_RAISE'])
        finally:
            await other.close()
