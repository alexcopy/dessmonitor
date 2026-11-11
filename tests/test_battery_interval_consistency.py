"""Regression matrix for six verified battery-model audit counterexamples.

No external API/database or hardware access. The PostgreSQL persistence portion
is modeled with a connection double, not claimed as live integration testing.
"""
import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from app.battery_intervals import calculate_battery_intervals
from app.control.online_output_learning import (_battery_response_model, _record_worker_failure, run_evaluation,
                                                _validated_cooldown, evaluate_collected_history, VERSION)
from app.web_battery_analysis import build_battery_grid

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def sample(minutes, *, voltage=25.5, charge=0, discharge=15, **kwargs):
    data = dict(time=NOW + timedelta(minutes=minutes), working_mode='Invert Mode',
                battery_voltage=voltage, battery_current_chg=charge,
                battery_current_dis=discharge, battery_soc=50,
                pv_power_w=100, output_power_w=600)
    data.update(kwargs)
    return data


def both_models(rows):
    worker = _battery_response_model(rows, rows[-1]['time'])
    grid = build_battery_grid(list(reversed(rows)), limit=len(rows)-1,
                              has_more=True, now=rows[-1]['time'])
    return worker, grid['battery_behavior'][0] if grid['battery_behavior'] else None


class BatteryModelAgreementTests(unittest.TestCase):
    def test_missing_voltage_breaks_both_ah_and_wh(self):
        rows = [sample(0), sample(5, voltage=None), sample(10, voltage=25.4)]
        worker, grid = both_models(rows)
        self.assertIsNone(worker['discharged_ah_proxy'])
        self.assertEqual(worker['excluded_interval_seconds']['MISSING_VOLTAGE'], 600)
        self.assertIsNotNone(grid)
        self.assertIsNone(grid['easun_discharge_ah_proxy'])
        self.assertIsNone(grid['easun_discharge_wh_proxy'])
        self.assertIsNone(grid['voltage_slope_v_per_hour'])
        self.assertIn('MISSING_VOLTAGE_INTERRUPTS_CONTINUITY', grid['easun_throughput_exclusions'])

    def test_negative_voltage_is_never_integrated_or_sloped(self):
        for voltage in (-25.9, float('nan'), float('inf'), float('-inf')):
            with self.subTest(voltage=voltage):
                rows = [sample(0), sample(5, voltage=voltage), sample(10, voltage=25.4)]
                worker, grid = both_models(rows)
                self.assertIsNone(worker['discharged_wh_proxy'])
                self.assertIsNone(worker['discharged_ah_proxy'])
                self.assertIsNone(grid['easun_discharge_wh_proxy'])
                self.assertIsNone(grid['voltage_slope_v_per_hour'])
                expected = ('INVALID_NEGATIVE_MEASUREMENT' if voltage == -25.9 else 'MISSING_VOLTAGE')
                self.assertGreater(worker['excluded_observations'][expected], 0)
                display_reason = ('INVALID_NEGATIVE_MEASUREMENT' if expected == 'INVALID_NEGATIVE_MEASUREMENT'
                                  else 'MISSING_VOLTAGE_INTERRUPTS_CONTINUITY')
                self.assertIn(display_reason, grid['easun_throughput_exclusions'])

    def test_short_coverage_is_not_complete(self):
        rows = [sample(0, voltage=25.5), sample(1, voltage=25.4)]
        rows.extend(sample(minute, voltage=25.4) for minute in range(2, 57))
        worker = _battery_response_model(rows, rows[-1]['time'])
        self.assertEqual(worker['integrated_seconds'], 60)
        self.assertEqual(worker['elapsed_collection_seconds'], 3360)
        self.assertEqual(worker['throughput_coverage'], 'PARTIAL')
        self.assertLess(worker['integration_coverage_ratio'], 0.02)
        self.assertIn('CACHED_REPEAT', worker['excluded_interval_seconds'])

    def test_repeat_between_distinct_measurements_is_excluded_in_both(self):
        rows = [sample(0, voltage=25.5), sample(5, voltage=25.5), sample(15, voltage=25.4)]
        worker, grid = both_models(rows)
        self.assertIsNone(worker['discharged_ah_proxy'])
        self.assertEqual(worker['integrated_seconds'], 0)
        self.assertIsNone(grid['easun_discharge_ah_proxy'])
        self.assertIn('CACHED_REPEAT', grid['easun_throughput_exclusions'])

    def test_complete_and_partial_coverage_match_valid_intervals(self):
        complete = [sample(0, voltage=25.5), sample(5, voltage=25.4)]
        worker, grid = both_models(complete)
        self.assertEqual(worker['throughput_coverage'], 'COMPLETE')
        self.assertEqual(grid['easun_throughput_status'], 'COMPLETE')
        self.assertEqual(grid['easun_throughput_coverage_ratio'], 1.0)
        self.assertAlmostEqual(worker['discharged_ah_proxy'], grid['easun_discharge_ah_proxy'])
        missing = [sample(0, voltage=25.5), sample(5, voltage=25.4),
                   sample(10, voltage=None), sample(15, voltage=25.3)]
        worker = _battery_response_model(missing, missing[-1]['time'])
        self.assertEqual(worker['throughput_coverage'], 'PARTIAL')
        self.assertAlmostEqual(worker['discharged_ah_proxy'], 1.25)
        self.assertEqual(worker['integrated_seconds'], 300)

    def test_conflicting_channels_and_invalid_timestamps_do_not_create_throughput(self):
        cases = [
            [sample(0), sample(5, charge=4, discharge=10)],
            [sample(0), sample(5, discharge=-2)],
            [sample(0), sample(0, voltage=25.4)],
            [sample(0), sample(20, voltage=25.4)],
        ]
        for rows in cases:
            with self.subTest(case=rows):
                result = calculate_battery_intervals(rows)
                self.assertEqual(result['covered_seconds'], 0)
                self.assertEqual(result['status'], 'NONE')

    def test_subsecond_interval_never_has_unbounded_slope(self):
        rows = [sample(0), sample(0, voltage=25.4,
                                  time=NOW + timedelta(milliseconds=500))]
        analysis = calculate_battery_intervals(rows)
        self.assertEqual(analysis['covered_seconds'], 0.5)
        self.assertIsNone(analysis['intervals'][0]['voltage_slope_v_per_hour'])

    def test_grid_slope_uses_the_same_supported_interval_policy_as_worker(self):
        rows = [sample(0, voltage=25.5), sample(5, voltage=25.4)]
        worker, grid = both_models(rows)
        self.assertEqual(worker['recent_intervals'][0]['voltage_slope_v_per_hour'],
                         grid['voltage_slope_v_per_hour'])
        missing_current = [sample(0, voltage=25.5),
                           sample(5, voltage=25.4, discharge=None)]
        worker, grid = both_models(missing_current)
        self.assertEqual(worker['intervals_integrated'], 0)
        self.assertIsNone(grid['voltage_slope_v_per_hour'])


class CooldownSafetyTests(unittest.TestCase):
    def test_malformed_naive_future_and_nonstring_cooldown_fail_closed(self):
        for value in ('', 0, '2026-11-07T11:00:00', 'not-a-timestamp',
                      '2026-11-07T13:00:00+00:00', 123):
            with self.subTest(value=value):
                self.assertEqual(_validated_cooldown({'last_change_at': value}, NOW), (None, True))
        self.assertEqual(_validated_cooldown({'last_change_at': '2026-11-07T11:30:00+00:00'}, NOW),
                         (1800, False))
        self.assertEqual(_validated_cooldown({'active': 'HYPOTHETICAL_RAISE:26.5'}, NOW),
                         (None, True))

    def test_malformed_timestamp_never_confirms_stable_candidate(self):
        def charging(i):
            return sample(-30 + 5*i, working_mode='Line Mode',
                          voltage=26.8 + 0.03*i, charge=5+i, discharge=0,
                          pv_power_w=500+20*i, output_power_w=500+10*i)
        rows = [charging(i) for i in range(7)]
        first = evaluate_collected_history(rows, NOW, historical_battery_v=27.0)
        self.assertEqual(first['candidate_voltage'], 26.5)
        for stamp in ('2026-11-07T11:00:00', 'garbage',
                      '2026-11-07T13:00:00+00:00'):
            with self.subTest(stamp=stamp):
                state = {**first['proposal_state'], 'pending': 'HYPOTHETICAL_LOWER:26.5',
                         'stable_count': 2, 'active': 'HYPOTHETICAL_RAISE:26.5',
                         'last_change_at': stamp}
                result = evaluate_collected_history(rows, NOW, {**first, 'proposal_state': state},
                                                    historical_battery_v=27.0)
                self.assertEqual(result['decision'], 'HOLD')
                self.assertEqual(result['stability'], 'COOLDOWN')
                self.assertIsNone(result['cooldown_remaining_seconds'])
                self.assertIn('COOLDOWN_STATE_UNVERIFIED', result['reason_codes'])


class FakeJournalConnection:
    def __init__(self, records):
        self.records = list(records)

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchval(self, sql, *params):
        if 'pg_try_advisory_xact_lock' in sql:
            return True
        if 'to_regclass' in sql:
            return None  # Journal v2 not installed
        if "entry ? 'proposal_state'" in sql:
            allowed = set(params[0]) if params else {VERSION}
            selected = [record for record in self.records
                        if isinstance(record.get('proposal_state'), dict)
                        and record.get('algorithm_version') in allowed]
            return json.dumps(selected[-1]) if selected else None
        if 'FROM adaptive_decision_journal' in sql:
            return json.dumps(self.records[-1]) if self.records else None
        raise AssertionError(sql)

    async def fetch(self, sql, *params, **kwargs):
        if 'FROM inverter_metrics' in sql or 'FROM ranked_samples AS samples' in sql:
            return []
        raise AssertionError(sql)

    async def execute(self, sql, *params):
        if 'INSERT INTO adaptive_decision_journal' in sql:
            self.records.append(json.loads(params[2]))
            return 'INSERT 0 1'
        if 'DELETE FROM adaptive_decision_journal' in sql:
            return 'DELETE 0'
        raise AssertionError(sql)


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class FailurePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_consecutive_failures_keep_active_target_and_cooldown(self):
        state = {'pending': 'HYPOTHETICAL_RAISE:26.5', 'stable_count': 2,
                 'active': 'HYPOTHETICAL_RAISE:26.5',
                 'last_change_at': (NOW - timedelta(minutes=1)).isoformat()}
        old = {'algorithm_version': VERSION, 'evaluated_at': (NOW - timedelta(minutes=1)).isoformat(),
               'decision': 'HYPOTHETICAL_RAISE', 'proposal_state': state,
               'worker_health': {'last_successful_evaluation_at':
                                 (NOW - timedelta(minutes=1)).isoformat()}}
        conn = FakeJournalConnection([old])
        pool = FakePool(conn)
        self.assertTrue(await _record_worker_failure(pool, NOW, 'RuntimeError'))
        self.assertTrue(await _record_worker_failure(pool, NOW + timedelta(minutes=1), 'RuntimeError'))
        for failure in conn.records[-2:]:
            self.assertEqual(failure['proposal_state'], state)
            self.assertEqual(failure['decision'], 'BLOCKED')
            self.assertEqual(failure['worker_health']['last_successful_evaluation_at'],
                             old['evaluated_at'])
        restored = evaluate_collected_history([], NOW + timedelta(minutes=2), conn.records[-1])
        self.assertEqual(restored['proposal_state']['active'], state['active'])
        # The next *successful* evaluation may clear pending confirmation when
        # there is no supported candidate; it must retain active/cooldown.
        self.assertEqual(restored['proposal_state']['stable_count'], 0)
        self.assertGreater(restored['cooldown_remaining_seconds'], 3000)

    async def test_legacy_failure_without_state_recovers_previous_durable_evaluation(self):
        state = {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': 1,
                 'active': 'HYPOTHETICAL_RAISE:26.5',
                 'last_change_at': (NOW - timedelta(minutes=5)).isoformat()}
        old = {'algorithm_version': VERSION, 'evaluated_at':
               (NOW - timedelta(minutes=5)).isoformat(), 'proposal_state': state}
        legacy_failure = {'algorithm_version': VERSION,
                          'evaluated_at': (NOW - timedelta(minutes=1)).isoformat(),
                          'decision': 'BLOCKED', 'worker_health': {'status': 'degraded'}}
        conn = FakeJournalConnection([old, legacy_failure])
        self.assertTrue(await run_evaluation(FakePool(conn), NOW))
        result = conn.records[-1]
        self.assertEqual(result['proposal_state']['active'], state['active'])
        self.assertGreater(result['cooldown_remaining_seconds'], 3000)
        self.assertFalse(result['hardware_write_attempted'])


if __name__ == '__main__':
    unittest.main()
