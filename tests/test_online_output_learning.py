import asyncio
import ast
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.control.online_output_learning import (CYCLING_MIN_SHORT_RUNS, LOCK_ID, MAX_ANALYSIS_OBSERVATIONS,
                                                MIN_CANDIDATE_PAIRS, evaluate_collected_history,
                                                parse_historical_record, run_evaluation, validate_journal_schema,
                                                _pair_contrast)
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

    def test_line_sample_with_comparable_partner_lost_to_one_to_one_match_is_not_mislabeled(self):
        line_earlier = row(time=NOW-timedelta(minutes=2), working_mode='Line Mode', battery_voltage=26.0,
                           battery_current_chg=0, output_power_w=800, pv_power_w=200)
        line_later = row(time=NOW-timedelta(minutes=1), working_mode='Line Mode', battery_voltage=26.1,
                         battery_current_chg=0, output_power_w=800, pv_power_w=200)
        invert = row(time=NOW-timedelta(seconds=30), working_mode='Invert Mode', battery_voltage=25.5,
                     battery_current_dis=15, output_power_w=800, pv_power_w=200)

        result = _pair_contrast([line_earlier, line_later], [invert], (75, 75), NOW)
        self.assertEqual(len(result['pairs']), 1)
        self.assertEqual(result['rejected']['NOT_SELECTED_ONE_TO_ONE'], 1)
        self.assertEqual(result['line_without_comparable_partner'], 0)
        self.assertEqual(result['line_not_selected_one_to_one'], 1)

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


from easun_replay_fixture import production_like_history  # noqa: E402

REPLAY_NOW = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)


def _class_counts(result):
    return {name: item['count'] for name, item in result['decision_diagnostics']['evidence_classes'].items()}


class ProductionLikeReplayTests(unittest.TestCase):
    """Synthetic three-hour replay: 60 Line (56 charging), 67 Invert discharging, cycling."""

    def setUp(self):
        self.rows = production_like_history(REPLAY_NOW)
        self.result = evaluate_collected_history(self.rows, REPLAY_NOW)

    def test_unknown_modes_break_transition_and_run_continuity(self):
        cases = [
            (['Line Mode', None, 'Invert Mode'], 0),
            (['Line Mode', 'Unknown', 'Line Mode'], 0),
            (['Line Mode', 'Invert Mode'], 1),
            (['Line Mode', 'Invert Mode', None, 'Line Mode'], 1),
            (['Line Mode', None, 'Invert Mode', None, 'Line Mode', None,
              'Invert Mode', None, 'Line Mode'], 0),
            (['Line Mode', 'Invert Mode', 'Line Mode', 'Invert Mode', 'Line Mode'], 4),
        ]
        for modes, expected in cases:
            with self.subTest(modes=modes):
                rows = [row(time=REPLAY_NOW-timedelta(seconds=30*(len(modes)-i)), working_mode=mode)
                        for i, mode in enumerate(modes)]
                result = evaluate_collected_history(rows, REPLAY_NOW)
                cycling = result['decision_diagnostics']['cycling']
                self.assertEqual(cycling['reported_mode_changes'], expected)
                self.assertEqual(cycling['reported_mode_changes'], result['recent_reported_mode_changes'])
                self.assertEqual(cycling['detected'], expected == 4)
                self.assertEqual(cycling['confidence_interpretation'], 'HEURISTIC_NOT_CALIBRATED_PROBABILITY')

    def test_identical_payloads_across_unknown_are_not_consecutive_duplicates(self):
        modes = ['Line Mode', 'Line Mode', None, 'Line Mode', 'Line Mode']
        rows = [row(time=REPLAY_NOW-timedelta(seconds=30*(len(modes)-i)), working_mode=mode)
                for i, mode in enumerate(modes)]
        result = evaluate_collected_history(rows, REPLAY_NOW)
        cycling = result['decision_diagnostics']['cycling']
        self.assertEqual(cycling['reported_mode_changes'], 0)
        self.assertEqual(cycling['collections_considered'], 2)
        self.assertEqual(cycling['duplicate_payloads_ignored'], 2)
        self.assertEqual(cycling['complete_runs'], 0)

    def test_cycling_evidence_crosses_quantitative_analysis_cap(self):
        start = REPLAY_NOW - timedelta(seconds=800*10)
        modes = ['Invert Mode', 'Line Mode', 'Invert Mode', 'Line Mode', 'Invert Mode']
        rows = [row(time=start+timedelta(seconds=10*i), working_mode='Line Mode',
                    output_power_w=500+i) for i in range(800)]
        for i, mode in enumerate(modes):
            rows[77+i]['working_mode'] = mode
        result = evaluate_collected_history(rows, REPLAY_NOW)
        cycling = result['decision_diagnostics']['cycling']
        self.assertGreater(result['decision_diagnostics']['sample_counts']['rejected_analysis_cap'], 0)
        self.assertEqual(cycling['reported_mode_changes'], 6)
        self.assertEqual(cycling['reported_mode_changes'], result['recent_reported_mode_changes'])
        self.assertTrue(cycling['detected'])

    def test_fixture_matches_the_operator_observed_mix(self):
        line = [r for r in self.rows if r['working_mode'] == 'Line Mode']
        invert = [r for r in self.rows if r['working_mode'] == 'Invert Mode']
        self.assertEqual((len(self.rows), len(line), len(invert)), (127, 60, 67))
        self.assertEqual(sum(r['battery_current_chg'] > 0 for r in line), 56)
        self.assertTrue(all(r['battery_current_dis'] > 0 and r['battery_current_chg'] == 0 for r in invert))

    def test_cycling_is_detected_despite_line_mode_charging(self):
        cycling = self.result['decision_diagnostics']['cycling']
        self.assertTrue(cycling['detected'])
        self.assertGreaterEqual(cycling['reported_mode_changes'], 20)
        self.assertGreaterEqual(cycling['short_runs'], CYCLING_MIN_SHORT_RUNS)
        self.assertGreater(cycling['transition_states'].get('INVERT_TO_LINE:LINE_CHARGING', 0), 0)
        self.assertIn('CYCLING_DETECTED', self.result['reason_codes'])
        # Collection brackets only; never an exact physical switch time.
        self.assertEqual(cycling['time_provenance'], 'COLLECTION_ORDER_ONLY')
        for item in cycling['recent_transitions']:
            self.assertLess(item['bracket_start'], item['bracket_end'])
            self.assertGreater(item['bracket_seconds'], 0)
        self.assertIsNone(self.result['observation_time'])
        self.assertIsNone(self.result['short_cycles'])
        self.assertGreater(self.result['confidence_breakdown']['cycling_detection'], 0.5)

    def test_cycling_is_detected_even_when_voltage_analysis_is_unavailable(self):
        rows = self.rows[:-1] + [{**self.rows[-1], 'battery_current_dis': None}]
        result = evaluate_collected_history(rows, REPLAY_NOW)
        self.assertIsNone(result['exploratory_voltage_contrast'])
        self.assertEqual(result['decision'], 'INSUFFICIENT_DATA')
        self.assertTrue(result['decision_diagnostics']['cycling']['detected'])
        self.assertIn('LATEST_COLLECTION_INCOMPLETE_STALE_OR_CONFLICTING',
                      result['decision_diagnostics']['voltage_comparison']['blocked_by'])

    def test_steady_operation_is_not_reported_as_cycling(self):
        rows = [r for r in self.rows if r['working_mode'] == 'Invert Mode']
        result = evaluate_collected_history(rows, REPLAY_NOW)
        self.assertFalse(result['decision_diagnostics']['cycling']['detected'])
        self.assertNotIn('CYCLING_DETECTED', result['reason_codes'])

    def test_return_to_identical_payload_after_other_mode_still_counts_as_a_transition(self):
        line = row(working_mode='Line Mode', battery_voltage=25.9, battery_current_chg=8,
                   battery_current_dis=0, pv_power_w=300, output_power_w=800)
        invert = row(working_mode='Invert Mode', battery_voltage=25.5, battery_current_chg=0,
                     battery_current_dis=12, pv_power_w=300, output_power_w=800)
        modes = (line, invert, line, invert, line)
        rows = [{**sample, 'time': REPLAY_NOW - timedelta(minutes=4-index)}
                for index, sample in enumerate(modes)]

        result = evaluate_collected_history(rows, REPLAY_NOW)
        cycling = result['decision_diagnostics']['cycling']
        self.assertTrue(cycling['detected'])
        self.assertEqual(cycling['reported_mode_changes'], 4)
        self.assertEqual(cycling['collections_considered'], 5)
        self.assertEqual(cycling['duplicate_payloads_ignored'], 0)
        # Quantitative evidence still excludes repeated signatures globally, while
        # cycling detection preserves the return transition in collection order.
        self.assertEqual(result['decision_diagnostics']['sample_counts']['analysed_observations'], 2)

    def test_charging_observations_remain_in_diagnostic_context(self):
        counts = _class_counts(self.result)
        funnel = self.result['decision_diagnostics']['sample_counts']
        # Only cached duplicates and incomplete rows leave; charging rows stay as their own class.
        self.assertEqual(funnel['rejected_analysis_cap'], 0)
        self.assertGreaterEqual(counts['LINE_CHARGING'], 48)
        self.assertEqual(counts['LINE_NOT_CHARGING'], 4)
        classes = self.result['decision_diagnostics']['evidence_classes']
        self.assertGreaterEqual(classes['LINE_CHARGING']['voltage_range_v'][0], 25.78)
        self.assertLessEqual(classes['INVERT_DISCHARGING']['voltage_range_v'][1], 25.75)
        contrast = self.result['exploratory_voltage_contrast']
        self.assertEqual(contrast['primary_exclusions']['LINE_MODE_CHARGING_CONFOUNDED'], counts['LINE_CHARGING'])
        confounded = contrast['charging_confounded_contrast']
        self.assertEqual(confounded['line_charging_observations'], counts['LINE_CHARGING'])
        self.assertGreater(confounded['matched_pairs'], 10)
        self.assertGreater(confounded['median_difference_v'], 0.2)

    def test_old_fixed_cap_no_longer_discards_earlier_transition_evidence(self):
        contrast = self.result['exploratory_voltage_contrast']
        self.assertGreater(contrast['samples'], 24)
        self.assertEqual(contrast['primary_eligible']['line_not_charging'], 4)
        self.assertGreater(contrast['effective_window_seconds'], 2.5 * 3600)

    def test_analysis_memory_remains_bounded(self):
        start = REPLAY_NOW - timedelta(hours=2, minutes=59)
        rows = [row(time=start + timedelta(seconds=13 * i), working_mode='Invert Mode',
                    battery_voltage=25.4 + (i % 30) / 100, battery_current_dis=10 + i / 100,
                    output_power_w=600 + i % 50) for i in range(800)]
        result = evaluate_collected_history(rows, start + timedelta(seconds=13 * 799))
        funnel = result['decision_diagnostics']['sample_counts']
        self.assertEqual(funnel['analysed_observations'], MAX_ANALYSIS_OBSERVATIONS)
        self.assertEqual(funnel['rejected_analysis_cap'], 800 - MAX_ANALYSIS_OBSERVATIONS)
        self.assertEqual(result['exploratory_voltage_contrast']['samples'], MAX_ANALYSIS_OBSERVATIONS)

    def test_pure_sag_is_never_inferred_from_charging_confounded_observations(self):
        rows = production_like_history(REPLAY_NOW, all_line_charging=True)
        state = None
        for minute in range(4):
            now = REPLAY_NOW + timedelta(minutes=minute)
            extra = [{**rows[-1], 'time': now - timedelta(seconds=20), 'battery_voltage': 25.95 + minute / 100,
                      'battery_current_chg': 20.0 + minute}] if minute else []
            state = evaluate_collected_history(rows + extra, now, state)
            rows = rows + extra
            contrast = state['exploratory_voltage_contrast']
            self.assertIsNone(contrast['contrast_v'])
            self.assertEqual(contrast['comparable_pairs'], 0)
            self.assertGreater(contrast['charging_confounded_contrast']['median_difference_v'], 0.2)
            self.assertFalse(contrast['charging_confounded_contrast']['eligible_for_hypothesis'])
            self.assertNotEqual(state['decision'], 'HYPOTHETICAL_RAISE')
            self.assertIsNone(state['sag_estimate_v'])
            self.assertIsNone(state['rebound_estimate_v'])
            self.assertEqual(state['verified_sag']['status'], 'BLOCKED')
            for code in ('LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE', 'INSUFFICIENT_COMPARABLE_PAIRS',
                         'INSUFFICIENT_HYPOTHESIS_EVIDENCE', 'CYCLING_DETECTED'):
                self.assertIn(code, state['reason_codes'])
            # No comparable pairs: no voltage confidence, not a generic 0.45.
            self.assertIsNone(state['confidence'])
            self.assertIsNone(state['confidence_breakdown']['voltage_contrast'])
            self.assertEqual(state['confidence_breakdown']['basis'], 'NO_VOLTAGE_ESTIMATE')
            self.assertFalse(state['decision_diagnostics']['hypothesis_support']['cycling_used_as_threshold_evidence'])

    def test_every_rejected_sample_and_comparison_has_a_counted_reason(self):
        diagnostics = self.result['decision_diagnostics']
        funnel = diagnostics['sample_counts']
        self.assertEqual(funnel['raw_collections'], 127)
        self.assertEqual(funnel['raw_collections'],
                         funnel['rejected_outside_window_or_invalid_time'] + funnel['in_window_collections'])
        self.assertEqual(funnel['in_window_collections'], funnel['rejected_unknown_mode']
                         + funnel['rejected_missing_or_invalid_numeric'] + funnel['valid_mode_and_numeric'])
        self.assertEqual(funnel['valid_mode_and_numeric'],
                         funnel['rejected_duplicate_cached_payload'] + funnel['unique_observations'])
        self.assertEqual(funnel['unique_observations'], funnel['rejected_analysis_cap'] + funnel['analysed_observations'])
        self.assertEqual(funnel['rejected_duplicate_cached_payload'], 8)
        self.assertEqual(funnel['missing_fields'], {'pv_power_w': 2})
        self.assertEqual(sum(_class_counts(self.result).values()), funnel['analysed_observations'])

        contrast = self.result['exploratory_voltage_contrast']
        rejected = contrast['pair_rejections']
        eligible = contrast['primary_eligible']
        self.assertEqual(eligible['combinations'], eligible['line_not_charging'] * eligible['invert_discharging'])
        self.assertEqual(eligible['combinations'], rejected['TIME_GAP_EXCEEDED'] + rejected['LOAD_NOT_COMPARABLE']
                         + rejected['PV_NOT_COMPARABLE'] + rejected['NOT_SELECTED_ONE_TO_ONE']
                         + contrast['candidate_pairs'])
        self.assertEqual(contrast['candidate_pairs'], contrast['comparable_pairs']
                         + rejected['OUTLIER_REJECTED'] + rejected['INSUFFICIENT_PAIRS'])
        excluded = sum(contrast['primary_exclusions'].get(k, 0) for k in contrast['primary_exclusions']
                       if k not in ('LINE_NOT_CHARGING_WITHOUT_COMPARABLE_PARTNER',
                                    'LINE_NOT_CHARGING_NOT_SELECTED_ONE_TO_ONE'))
        self.assertEqual(excluded + eligible['line_not_charging'] + eligible['invert_discharging'],
                         funnel['analysed_observations'])

    def test_conflicting_and_unknown_current_states_are_separated(self):
        conflicting = {**self.rows[60], 'time': self.rows[60]['time'] + timedelta(seconds=5),
                       'battery_current_chg': 3.0, 'battery_current_dis': 4.0, 'battery_voltage': 25.71}
        invert_idle = {**self.rows[61], 'time': self.rows[61]['time'] + timedelta(seconds=5),
                       'working_mode': 'Invert Mode', 'battery_current_chg': 0.0, 'battery_current_dis': 0.0}
        result = evaluate_collected_history(self.rows + [conflicting, invert_idle], REPLAY_NOW)
        exclusions = result['exploratory_voltage_contrast']['primary_exclusions']
        self.assertEqual(exclusions['CONFLICTING_CURRENT_STATE'], 1)
        self.assertEqual(exclusions['INVERT_MODE_NOT_DISCHARGING'], 1)
        self.assertIn('CONFLICTING_CURRENT', _class_counts(result))

    def test_no_candidate_decision_explains_itself(self):
        diagnostics = self.result['decision_diagnostics']
        self.assertEqual(self.result['decision'], 'HOLD')
        self.assertEqual(self.result['stability'], 'NO_CANDIDATE')
        self.assertIn('INSUFFICIENT_HYPOTHESIS_EVIDENCE', diagnostics['hold_reasons'])
        support = diagnostics['hypothesis_support']
        self.assertFalse(support['supported'])
        self.assertTrue(support['unsupported_because'])
        # The charging rule produced a LOWER candidate that the catalogue rule rejected; it is now visible.
        self.assertEqual(support['blocked_candidate'],
                         {'decision': 'HYPOTHETICAL_LOWER', 'voltage': 25.5, 'reason': 'CATALOGUE_OR_HYSTERESIS_BLOCK'})
        self.assertFalse(support['raise_requirements']['contrast_at_least_0_2_v'])
        # Contrast exists here, so its confidence is a voltage-contrast confidence with that basis.
        self.assertEqual(self.result['confidence_breakdown']['basis'], 'VOLTAGE_CONTRAST')
        self.assertEqual(self.result['confidence'], self.result['confidence_breakdown']['voltage_contrast'])
        self.assertIn('Frequent switching alone does not establish', self.result['explanation'])
        self.assertIn('not verified sag', self.result['explanation'])

    def test_legitimate_comparable_evidence_still_yields_a_bounded_hypothesis(self):
        rows = production_like_history(REPLAY_NOW, non_charging_runs=range(11), rest_voltage=25.95)
        first = evaluate_collected_history(rows, REPLAY_NOW)
        contrast = first['exploratory_voltage_contrast']
        self.assertGreaterEqual(contrast['comparable_pairs'], MIN_CANDIDATE_PAIRS)
        self.assertGreater(contrast['contrast_v'], 0.2)
        self.assertLessEqual(abs(contrast['contrast_v']), 2.0)
        self.assertEqual(first['candidate_voltage'], 26.5)
        self.assertIsNone(first['proposed_voltage'])
        self.assertEqual(first['stability'], 'AWAITING_COMPATIBLE_OBSERVATION')
        self.assertEqual(first['confidence_breakdown']['basis'], 'VOLTAGE_CONTRAST')
        self.assertTrue(first['decision_diagnostics']['hypothesis_support']['supported'])
        self.assertEqual(first['actionability'], 'NON_ACTIONABLE')

    def test_previous_version_state_keeps_cooldown_durable_across_upgrade(self):
        rows = [row(30-i*5, battery_voltage=26.8+i*.03, battery_current_chg=5+i,
                    output_power_w=500+i*10, pv_power_w=500+i*20) for i in range(7)]
        first = evaluate_collected_history(rows, NOW, historical_battery_v=27.0)
        rows.append(row(time=NOW+timedelta(minutes=1), battery_voltage=27.04, battery_current_chg=13,
                        output_power_w=600, pv_power_w=700))
        second = evaluate_collected_history(rows, NOW+timedelta(minutes=1), first, historical_battery_v=27.0)
        self.assertEqual(second['decision'], 'HYPOTHETICAL_LOWER')
        legacy = dict(second, algorithm_version='online-exploratory-4',
                      proposal_state={**second['proposal_state'], 'active': 'different'})
        rows.append(row(time=NOW+timedelta(minutes=2), battery_voltage=27.05, battery_current_chg=14,
                        output_power_w=610, pv_power_w=710))
        blocked = evaluate_collected_history(rows, NOW+timedelta(minutes=2), legacy, historical_battery_v=27.0)
        self.assertEqual(blocked['stability'], 'COOLDOWN')
        self.assertGreater(blocked['cooldown_remaining_seconds'], 3000)
        unknown = dict(legacy, algorithm_version='online-exploratory-3')
        reset = evaluate_collected_history(rows, NOW+timedelta(minutes=2), unknown, historical_battery_v=27.0)
        self.assertNotEqual(reset['stability'], 'COOLDOWN')

    def test_collection_replay_never_reaches_verified_sag_learner(self):
        learner = SagLearner(sag_configuration())
        with patch.object(learner, 'observe') as observe:
            result = evaluate_collected_history(self.rows, REPLAY_NOW, learner=learner)
        observe.assert_not_called()
        self.assertEqual(result['verified_sag']['status'], 'BLOCKED')
        self.assertEqual(result['evidence_level'], 'EXPLORATORY')

    def test_every_replay_outcome_is_non_actionable_without_hardware_writes(self):
        variants = (self.rows, production_like_history(REPLAY_NOW, all_line_charging=True),
                    production_like_history(REPLAY_NOW, non_charging_runs=range(11), rest_voltage=25.95))
        for rows in variants:
            result = evaluate_collected_history(rows, REPLAY_NOW)
            self.assertEqual(result['actionability'], 'NON_ACTIONABLE')
            self.assertFalse(result['hardware_write_attempted'])
            self.assertEqual(result['current_settings']['provenance'], 'unknown')
            json.dumps(result, allow_nan=False)


@unittest.skipUnless(asyncpg is not None and os.environ.get('EASUN_TEST_POSTGRES') == '1', 'requires disposable local test database')
class ReplayJournalPostgresTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_replay_diagnostics_persist_and_upgrade_keeps_legacy_state(self):
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', [
                (r['time'], r['working_mode'], r['battery_voltage'], r['battery_soc'], r['battery_current_chg'],
                 r['battery_current_dis'], r['pv_power_w'], r['output_power_w']) for r in production_like_history(REPLAY_NOW)])
            legacy = {'algorithm_version': 'online-exploratory-4', 'proposal_state': {
                'active': 'HYPOTHETICAL_RAISE:26.5', 'last_change_at': (REPLAY_NOW - timedelta(minutes=10)).isoformat()}}
            await conn.execute('INSERT INTO adaptive_decision_journal (evaluation_id,evaluated_at,entry) VALUES ($1,$2,$3::jsonb)',
                               'legacy', REPLAY_NOW - timedelta(minutes=1), json.dumps(legacy))
        self.assertTrue(await run_evaluation(self.pool, REPLAY_NOW))
        async with self.pool.acquire() as conn:
            entry = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM adaptive_decision_journal WHERE hardware_write_attempted'), 0)
        self.assertEqual(entry['algorithm_version'], 'online-exploratory-5')
        self.assertEqual(entry['decision'], 'HOLD')
        self.assertTrue(entry['decision_diagnostics']['cycling']['detected'])
        self.assertEqual(entry['decision_diagnostics']['sample_counts']['raw_collections'], 127)
        self.assertIn('CYCLING_DETECTED', entry['reason_codes'])
        self.assertEqual(entry['proposal_state']['active'], 'HYPOTHETICAL_RAISE:26.5')
        self.assertGreater(entry['cooldown_remaining_seconds'], 2900)
        self.assertEqual(entry['actionability'], 'NON_ACTIONABLE')
        self.assertFalse(entry['hardware_write_attempted'])
