"""Regression tests for the three independent-review blockers.

1. 0.0 V (and other invalid voltage) is never usable battery evidence.
2. Battery-grid derived values are independent of page size / cursor position.
3. Multi-source charging context reuses the shared interval policy only.

Plus cross-module agreement between worker, grid, fast context and
multi-source diagnostics for the same source rows.
"""
import json
import math
import os
import random
from statistics import median
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import web_routes
from app.battery_intervals import calculate_battery_intervals, valid_battery_voltage
from app.control import online_output_learning as ol
from app.web_battery_analysis import GRID_CONTEXT_ROWS, build_battery_grid

T0 = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=1)


def row(sec, v=26.0, chg=0.0, dis=10.0, mode='Invert Mode', pv=100.0, load=500.0, soc=60.0, base=T0):
    return {'time': base + timedelta(seconds=sec), 'working_mode': mode, 'battery_voltage': v,
            'battery_current_chg': chg, 'battery_current_dis': dis, 'pv_power_w': pv,
            'output_power_w': load, 'battery_soc': soc}


def grid_rows(chronological, **kwargs):
    newest_first = list(reversed(chronological))
    return build_battery_grid(newest_first, limit=len(newest_first), has_more=False, now=NOW, **kwargs)


INVALID_VOLTAGES = (0.0, -0.0, -1.0, -25.9, float('nan'), float('inf'), float('-inf'))


class ZeroVoltageValidationTests(unittest.TestCase):
    def test_shared_validator(self):
        for bad in INVALID_VOLTAGES + (None, '26.0', True, False, 0):
            with self.subTest(bad=bad):
                self.assertIsNone(valid_battery_voltage(bad))
        self.assertEqual(valid_battery_voltage(26.1), 26.1)
        self.assertEqual(valid_battery_voltage(1e-3), 1e-3)

    def test_zero_between_valid_measurements_is_a_barrier_everywhere(self):
        # The reviewer counterexample: 52.0 / 0.0 / 51.8 V at 120 s.
        rows = [row(0, 52.0, dis=10), row(120, 0.0, dis=11), row(240, 51.8, dis=12)]
        shared = calculate_battery_intervals(rows)
        self.assertEqual(shared['successful'], [])
        self.assertEqual(shared['status'], 'NONE')
        self.assertEqual(shared['rejected']['INVALID_ZERO_VOLTAGE'], 2)
        self.assertEqual(shared['excluded_observations']['INVALID_ZERO_VOLTAGE'], 1)
        self.assertEqual(shared['excluded_seconds']['INVALID_ZERO_VOLTAGE'], 240)
        self.assertEqual(shared['covered_seconds'], 0)

        worker = ol._battery_response_model(rows, NOW)
        self.assertEqual(worker['throughput_coverage'], 'NONE')
        self.assertIsNone(worker['median_voltage_slope_v_per_hour'])
        self.assertIsNone(worker['discharged_ah_proxy'])
        self.assertIsNone(worker['discharged_wh_proxy'])
        self.assertEqual(worker['excluded_observations']['INVALID_ZERO_VOLTAGE'], 1)

        grid = grid_rows(rows)
        for behavior in grid['battery_behavior']:
            self.assertIsNone(behavior['voltage_slope_v_per_hour'])
            self.assertIsNone(behavior['easun_discharge_ah_proxy'])
            self.assertIsNone(behavior['easun_discharge_wh_proxy'])
            self.assertNotEqual(behavior['easun_throughput_status'], 'COMPLETE')
            self.assertIn('INVALID_ZERO_VOLTAGE_INTERRUPTS_CONTINUITY', behavior['easun_throughput_exclusions'])
        self.assertNotIn('VOLTAGE_RISE_OBSERVED', grid['external_charging_labels'])

        context = ol._multi_source_charging_context(rows, NOW)
        self.assertEqual(context['intervals_with_voltage_response'], 0)
        self.assertEqual(context['median_voltage_slope_v_per_hour_by_easun_context'], {})
        self.assertEqual(context['intervals_rejected_by_shared_policy'], {'INVALID_ZERO_VOLTAGE': 2})

        fast = ol._fast_context(rows, T0 + timedelta(seconds=300))
        self.assertIsNone(fast['battery_voltage_slope_v_per_hour'])

    def test_every_invalid_voltage_kind_between_valid_measurements(self):
        for bad in INVALID_VOLTAGES:
            with self.subTest(bad=bad):
                rows = [row(0, 26.0), row(120, bad, dis=11), row(240, 25.8, dis=12)]
                self.assertEqual(calculate_battery_intervals(rows)['successful'], [])
                worker = ol._battery_response_model(rows, NOW)
                self.assertIsNone(worker['median_voltage_slope_v_per_hour'])
                self.assertIsNone(worker['discharged_ah_proxy'])
                for behavior in grid_rows(rows)['battery_behavior']:
                    self.assertIsNone(behavior['voltage_slope_v_per_hour'])
                    self.assertIsNone(behavior['easun_discharge_ah_proxy'])
                self.assertEqual(ol._multi_source_charging_context(rows, NOW)['intervals_with_voltage_response'], 0)
                self.assertEqual(ol._sampled_voltage_crossings(rows, 25.9)[0], 0)

    def test_zero_at_first_and_last_observation(self):
        for rows in ([row(0, 0.0), row(120, 26.0, dis=11), row(240, 25.9, dis=12)],
                     [row(0, 26.0), row(120, 25.9, dis=11), row(240, 0.0, dis=12)]):
            with self.subTest(first=rows[0]['battery_voltage']):
                shared = calculate_battery_intervals(rows)
                self.assertEqual(len(shared['successful']), 1)
                self.assertEqual(shared['status'], 'PARTIAL')
                self.assertFalse(shared['complete'])
                for interval in shared['successful']:
                    self.assertGreater(interval['older']['battery_voltage'], 0)
                    self.assertGreater(interval['newer']['battery_voltage'], 0)
                worker = ol._battery_response_model(rows, NOW)
                self.assertEqual(worker['throughput_coverage'], 'PARTIAL')
                self.assertEqual(worker['intervals_integrated'], 1)
                self.assertTrue(math.isclose(worker['median_voltage_slope_v_per_hour'], -3.0, abs_tol=1e-6))
                grid = grid_rows(rows)
                slopes = [b['voltage_slope_v_per_hour'] for b in grid['battery_behavior'] if b['voltage_slope_v_per_hour'] is not None]
                self.assertEqual(slopes, [-3.0])

    def test_recovery_after_invalid_measurement(self):
        rows = [row(0, 26.0), row(60, 0.0, dis=11), row(120, 25.95, dis=12), row(180, 25.9, dis=13)]
        shared = calculate_battery_intervals(rows)
        self.assertEqual([(int((i['older']['time'] - T0).total_seconds()), i['reason']) for i in shared['intervals']],
                         [(0, 'INVALID_ZERO_VOLTAGE'), (60, 'INVALID_ZERO_VOLTAGE'), (120, None)])
        self.assertEqual(shared['covered_seconds'], 60)
        grid = grid_rows(rows)
        newest = grid['battery_behavior'][0]
        self.assertEqual(newest['easun_throughput_status'], 'COMPLETE')
        self.assertEqual(newest['voltage_slope_v_per_hour'], -3.0)
        bridged = grid['battery_behavior'][1]
        self.assertEqual(bridged['interval_seconds'], 120)
        self.assertIsNone(bridged['voltage_slope_v_per_hour'])
        self.assertIsNone(bridged['easun_discharge_ah_proxy'])
        self.assertEqual(ol._multi_source_charging_context(rows, NOW)['intervals_with_voltage_response'], 1)

    def test_no_false_virtual_boundary_crossings_through_zero(self):
        # 26.0 -> 0.0 -> 26.0 used to give two crossings of 25.5 V.
        rows = [row(0, 26.0), row(300, 0.0), row(600, 26.0, dis=11)]
        self.assertEqual(ol._sampled_voltage_crossings(rows, 25.5)[0], 0)
        self.assertNotIn(0.0, [v for _, v in ol._sampled_voltage_crossings(rows, 25.5)[1]])
        # A genuine crossing either side of the barrier is still not bridged.
        rows = [row(0, 25.4), row(300, 0.0), row(600, 25.6, dis=11)]
        self.assertEqual(ol._sampled_voltage_crossings(rows, 25.5)[0], 0)
        rows = [row(0, 25.4), row(300, 25.6, dis=11)]
        self.assertEqual(ol._sampled_voltage_crossings(rows, 25.5)[0], 1)

    def test_virtual_experiment_ignores_zero_voltage_for_margins(self):
        rows = [row(0, 26.2), row(300, 0.0, dis=11), row(600, 26.1, dis=12)]
        replay = ol._virtual_threshold_experiment(rows, NOW, 26.0)
        for candidate in replay['candidates']:
            self.assertEqual(candidate['observed_voltage_at_positive_reported_load_range_v'], [26.1, 26.2])
            self.assertGreater(candidate['sampled_min_voltage_minus_utility_threshold_v'], -1)
            self.assertEqual(candidate['sampled_boundary_crossing_count'], 0)
            self.assertEqual(candidate['actionability'], 'NON_ACTIONABLE')
        self.assertEqual(replay['comparison_status'], 'ALL_CANDIDATES_EXPLORATORY_NO_WINNER')
        self.assertIsNone(replay['recommendation'])

    def test_zero_voltage_rows_are_excluded_from_quantitative_and_latest_evidence(self):
        analysed, funnel = ol._prepare_observations([
            row(0, 26.0, mode='Line Mode', dis=0), row(60, 0.0, mode='Line Mode', dis=0)])
        self.assertEqual(len(analysed), 1)
        self.assertEqual(funnel['missing_fields'], {'battery_voltage': 1})
        rows = [row(-600 + i * 60, 26.0 + i * 0.01, mode='Line Mode' if i % 2 else 'Invert Mode',
                    dis=0 if i % 2 else 10, base=NOW) for i in range(9)] + [row(0, 0.0, base=NOW)]
        result = ol.evaluate_collected_history(rows, NOW)
        self.assertIn('INVALID_BATTERY_VOLTAGE', result['reason_codes'])
        self.assertIsNone(result['exploratory_voltage_contrast'])
        self.assertFalse(result['hardware_write_attempted'])

    def test_grid_preserves_raw_zero_for_diagnostics_only(self):
        rows = [row(0, 26.0), row(120, 0.0, dis=11)]
        observation = grid_rows(rows)['observations'][0]
        self.assertIsNone(observation['battery_voltage_v'])
        self.assertEqual(observation['battery_voltage_v_reported_raw'], 0.0)
        self.assertEqual(observation['invalid_fields'], ['battery_voltage'])
        self.assertNotIn('battery_voltage', observation['missing_fields'])
        self.assertIn('INVALID_BATTERY_VOLTAGE_EXCLUDED_FROM_INFERENCE', observation['data_quality'])
        self.assertIsNone(observation['voltage_change_since_previous_valid_v'])
        json.dumps(grid_rows([row(0, 26.0), row(120, float('nan'))]), default=str, allow_nan=False)


class _FakeInverterMetrics:
    """In-memory implementation of web_queries.battery_observations' SQL contract."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: r['time'], reverse=True)
        self.calls = []

    async def __call__(self, database_url, lower, upper, limit, after_time=None, context_rows=0):
        self.calls.append((lower, upper, limit, after_time, context_rows))
        page = [dict(r) for r in self.rows if lower <= r['time'] <= upper
                and (after_time is None or r['time'] < after_time)][:limit]
        context = []
        if page:
            oldest = min(r['time'] for r in page)
            context = [dict(r) for r in self.rows if r['time'] < oldest][:context_rows]
        return ([{**r, 'is_context': False} for r in page] +
                [{**r, 'is_context': True} for r in context])


_COMPARED = ('interval_seconds', 'voltage_slope_v_per_hour', 'slope_status', 'voltage_delta_v',
             'easun_charge_ah_proxy', 'easun_discharge_ah_proxy', 'easun_charge_wh_proxy',
             'easun_discharge_wh_proxy', 'easun_throughput_status', 'easun_throughput_covered_seconds',
             'easun_throughput_elapsed_seconds', 'easun_throughput_coverage_ratio',
             'easun_throughput_exclusions', 'estimated_voltage_response', 'charging_interpretation',
             'limitation', 'capacity_or_response_proxy', 'data_kind')


class GridPageSizeIndependenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        router = web_routes.create_auth_router(None, None)
        self.api = next(route.endpoint for route in router.routes
                        if route.path == '/api/controls/battery-observations')
        # Anchor to the real clock (the route bounds the range with it), but far
        # enough back that freshness labels are stable during the test.
        self.base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)

    async def traverse(self, store, limit):
        observations, behaviors, cursor, pages = [], [], None, 0
        with patch.object(web_routes, '_check_auth', return_value=(True, 'operator')), \
             patch.dict('os.environ', {'DATABASE_URL': 'synthetic', 'WEB_AUTH_SESSION_SECRET': 's' * 64}, clear=True), \
             patch('app.web_queries.battery_observations', new=store):
            while True:
                response = await self.api(object(), days=1, limit=limit, cursor=cursor)
                self.assertEqual(response.status_code, 200)
                data = json.loads(response.body)
                self.assertLessEqual(len(data['observations']), limit)
                observations += data['observations']
                behaviors += data['battery_behavior']
                pages += 1
                if not data['has_more']:
                    self.assertIsNone(data['next_cursor'])
                    break
                cursor = data['next_cursor']
        return observations, behaviors, pages

    def dataset(self):
        b = self.base

        def row(sec, *args, **kwargs):
            return globals()['row'](sec, *args, base=b, **kwargs)
        chrono = [
            # Rows below the 1-day range: context only, never rendered.
            row(-86400 - 120, 26.4),
            row(-86400 - 60, 26.4),
            # Reviewer counterexample: A@0, A@60 (cached), B@120, C@180.
            row(0, 26.0, dis=10), row(60, 26.0, dis=10), row(120, 25.9, dis=11), row(180, 25.8, dis=12),
            # Long cached run that will straddle page boundaries.
            *[row(240 + 60 * i, 25.7, dis=13) for i in range(7)],
            row(660, 25.65, dis=14), row(720, 25.6, dis=15),
            # Missing current, 0 V, NaN, negative voltage, a run of invalid voltages.
            row(780, 25.55, dis=None), row(840, 25.5, dis=15), row(900, 0.0, dis=15), row(960, 25.45, dis=16),
            row(1020, float('nan'), dis=16), row(1080, -1.0, dis=16), row(1140, 25.4, dis=17),
            *[row(1200 + 60 * i, 0.0, dis=17) for i in range(GRID_CONTEXT_ROWS + 2)],
            row(1200 + 60 * (GRID_CONTEXT_ROWS + 2), 25.35, dis=18),
            # Long gap, a 10 s pair, a 5-minute pair, a rise under discharge.
            row(4000, 25.3, dis=18), row(4010, 25.31, dis=18.5), row(4310, 25.25, dis=19),
            row(4610, 25.4, dis=5, load=200), row(4670, 25.42, chg=4, dis=0, load=200),
        ]
        chrono[0]['time'] = b - timedelta(days=1, minutes=2)
        chrono[1]['time'] = b - timedelta(days=1, minutes=1)
        return chrono

    async def test_same_derived_values_for_every_page_size_and_cursor_page(self):
        chrono = self.dataset()
        in_range_times = {r['time'] for r in chrono if r['time'] >= self.base - timedelta(hours=1)}
        results = {}
        for limit in (1, 2, 3, 20, 200):
            store = _FakeInverterMetrics(chrono)
            observations, behaviors, pages = await self.traverse(store, limit)
            times = [item['collection_time'] for item in observations]
            self.assertEqual(len(times), len(set(times)), 'no row rendered twice')
            self.assertEqual(times, sorted(times, reverse=True), 'keyset order preserved')
            self.assertEqual({datetime.fromisoformat(t) for t in times}, in_range_times,
                             'context-only rows below the range are never rendered')
            behavior_times = [item['interval_end_collection_time'] for item in behaviors]
            self.assertEqual(len(behavior_times), len(set(behavior_times)), 'no interval counted twice')
            self.assertTrue(all(call[4] == GRID_CONTEXT_ROWS for call in store.calls))
            results[limit] = (
                {item['collection_time']: {k: v for k, v in item.items()} for item in observations},
                {item['interval_end_collection_time']: {k: item[k] for k in _COMPARED} for item in behaviors},
                pages)
        reference_obs, reference_beh, _ = results[200]
        for limit, (obs, beh, pages) in results.items():
            with self.subTest(limit=limit, pages=pages):
                self.assertEqual(obs, reference_obs)
                self.assertEqual(beh, reference_beh)

        # The reviewer's row B@120 is a CACHED_REPEAT interval at every page size.
        b_time = (self.base + timedelta(seconds=120)).isoformat()
        self.assertEqual(reference_beh[b_time]['easun_throughput_status'], 'NOT_INTEGRATED')
        self.assertIn('CACHED_REPEAT', reference_beh[b_time]['easun_throughput_exclusions'])
        self.assertIsNone(reference_beh[b_time]['easun_discharge_ah_proxy'])
        self.assertIsNone(reference_beh[b_time]['voltage_slope_v_per_hour'])
        # Beyond the bounded context the grid reports unavailable evidence, not continuity.
        after_run = (self.base + timedelta(seconds=1200 + 60 * (GRID_CONTEXT_ROWS + 2))).isoformat()
        self.assertNotIn(after_run, reference_beh)
        self.assertIn('NO_VALID_VOLTAGE_WITHIN_BOUNDED_CONTEXT', reference_obs[after_run]['interval_status'])

    async def test_grid_matches_shared_policy_for_every_rendered_interval(self):
        chrono = self.dataset()
        shared = calculate_battery_intervals(chrono)
        accepted = {i['newer']['time'].isoformat(): i for i in shared['successful']
                    if i['voltage_slope_v_per_hour'] is not None}
        _, behaviors, _ = await self.traverse(_FakeInverterMetrics(chrono), 2)
        computed = {b['interval_end_collection_time']: b for b in behaviors if b['slope_status'] == 'COMPUTED'}
        self.assertEqual(set(computed), set(accepted))
        for stamp, behavior in computed.items():
            interval = accepted[stamp]
            self.assertAlmostEqual(behavior['voltage_slope_v_per_hour'], interval['voltage_slope_v_per_hour'], places=3)
            self.assertAlmostEqual(behavior['easun_discharge_ah_proxy'], interval['discharge_ah'], places=4)

    def test_insufficient_supplied_context_is_reported_unavailable(self):
        rows = list(reversed([row(0, 26.0), row(60, 26.0), row(120, 25.9, dis=11), row(180, 25.8, dis=12)]))
        # Caller supplies only one context row and states more history exists.
        grid = build_battery_grid(rows[:2], context=rows[2:3], limit=2, has_more=True, now=NOW,
                                  older_context_complete=False)
        newest, second = grid['observations']
        self.assertEqual(newest['interval_status'], 'INTERVAL_AVAILABLE')
        self.assertIn('PREVIOUS_CONTEXT_UNAVAILABLE', second['interval_status'])
        self.assertEqual(len(grid['battery_behavior']), 1)
        # With the predecessor supplied, the row is correctly a cached repeat.
        full = build_battery_grid(rows[:2], context=rows[2:], limit=2, has_more=True, now=NOW,
                                  older_context_complete=True)
        self.assertIn('CACHED_REPEAT', full['battery_behavior'][1]['easun_throughput_exclusions'])

    def test_short_final_page_never_renders_context_rows(self):
        rows = list(reversed([row(0, 26.0), row(60, 25.9, dis=11), row(120, 25.8, dis=12)]))
        grid = build_battery_grid(rows[:1], context=rows[1:], limit=50, has_more=False, now=NOW)
        self.assertEqual(len(grid['observations']), 1)
        self.assertEqual(len(grid['battery_behavior']), 1)


_PG_DSN = os.environ.get('BATTERY_GRID_TEST_DSN',
                         'postgresql://postgres:synthetic@127.0.0.1:55440/dessmonitor_test')


@unittest.skipUnless(os.environ.get('BATTERY_GRID_LOCAL_POSTGRES') == '1',
                     'requires disposable local PostgreSQL (BATTERY_GRID_LOCAL_POSTGRES=1)')
class GridPageSizeIndependencePostgresTests(GridPageSizeIndependenceTests):
    """Same traversal through the real SQL (page + bounded context CTE)."""

    async def asyncSetUp(self):
        import asyncpg
        self.conn = await asyncpg.connect(_PG_DSN)
        database = await self.conn.fetchval('SELECT current_database()')
        if not database.endswith('_test'):
            raise RuntimeError('refusing to use a non-test database')
        await self.conn.execute('DROP TABLE IF EXISTS inverter_metrics')
        await self.conn.execute('''CREATE TABLE inverter_metrics (time TIMESTAMPTZ PRIMARY KEY,
            working_mode TEXT, battery_voltage DOUBLE PRECISION, battery_soc DOUBLE PRECISION,
            battery_current_chg DOUBLE PRECISION, battery_current_dis DOUBLE PRECISION,
            pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')
        columns = ('time', 'working_mode', 'battery_voltage', 'battery_soc', 'battery_current_chg',
                   'battery_current_dis', 'pv_power_w', 'output_power_w')
        await self.conn.copy_records_to_table('inverter_metrics', columns=columns,
            records=[tuple(r[c] for c in columns) for r in self.dataset()])
        await self.conn.execute('ANALYZE inverter_metrics')

    async def asyncTearDown(self):
        await self.conn.execute('DROP TABLE IF EXISTS inverter_metrics')
        await self.conn.close()

    async def traverse(self, store, limit):
        from app import web_queries
        calls = []
        real = web_queries.battery_observations

        async def recording(*args, **kwargs):
            rows = await real(_PG_DSN, *args[1:], **kwargs)
            calls.append(args[1:] + (kwargs.get('context_rows', 0),))
            return [dict(r) for r in rows]
        store.calls = calls
        return await super().traverse(recording, limit)

    async def test_context_query_is_bounded_and_index_ordered(self):
        plan = '\n'.join(r[0] for r in await self.conn.fetch(
            '''EXPLAIN WITH page AS (SELECT time FROM inverter_metrics WHERE time >= $1 AND time <= $2
                   ORDER BY time DESC LIMIT 3), context AS (SELECT time FROM inverter_metrics
                   WHERE time < (SELECT min(time) FROM page) ORDER BY time DESC LIMIT 8)
               SELECT * FROM page UNION ALL SELECT * FROM context''',
            self.base - timedelta(days=1), self.base + timedelta(hours=2)))
        self.assertGreaterEqual(plan.count('Limit'), 2)


class MultiSourceIntervalConsistencyTests(unittest.TestCase):
    def assert_agreement(self, rows):
        """Worker, multi-source context and grid agree with the shared policy."""
        now = max(r['time'] for r in rows) + timedelta(minutes=1)
        self.assertLessEqual(now - min(r['time'] for r in rows), ol.WINDOW)
        shared = calculate_battery_intervals(sorted(rows, key=lambda r: r['time']))
        slopes = [round(i['voltage_slope_v_per_hour'], 4) for i in shared['successful']
                  if i['voltage_slope_v_per_hour'] is not None]
        worker = ol._battery_response_model(rows, now)
        worker_slopes = [s['voltage_slope_v_per_hour'] for s in worker['recent_intervals']
                         if s['voltage_slope_v_per_hour'] is not None]
        self.assertEqual(worker_slopes, slopes[-5:])
        context = ol._multi_source_charging_context(rows, now)
        self.assertEqual(context['intervals_with_voltage_response'], len(slopes))
        self.assertEqual(context['intervals_accepted_by_shared_policy'], len(shared['successful']))
        self.assertEqual(sum(context['intervals_by_easun_reported_current_context'].values()), len(slopes))
        if slopes:
            self.assertAlmostEqual(worker['median_voltage_slope_v_per_hour'], median(slopes), places=3)
        else:
            self.assertIsNone(worker['median_voltage_slope_v_per_hour'])
            self.assertEqual(context['median_voltage_slope_v_per_hour_by_easun_context'], {})
            self.assertNotIn('VOLTAGE_RISE_OBSERVED', context['evidence_labels'])
            self.assertEqual(context['voltage_trend_evidence'], 'NONE; NO_INTERVAL_ACCEPTED_FOR_SLOPE_BY_SHARED_POLICY')
        rises = sum(1 for i in shared['successful'] if i['voltage_slope_v_per_hour'] is not None
                    and i['newer']['battery_voltage'] > i['older']['battery_voltage'])
        self.assertEqual(context['voltage_rise_observed_intervals'], rises)
        self.assertEqual('VOLTAGE_RISE_OBSERVED' in context['evidence_labels'], rises > 0)
        grid = grid_rows(sorted(rows, key=lambda r: r['time']))
        grid_slopes = sorted(b['voltage_slope_v_per_hour'] for b in grid['battery_behavior']
                             if b['voltage_slope_v_per_hour'] is not None)
        self.assertEqual(grid_slopes, sorted(slopes))
        self.assertEqual('VOLTAGE_RISE_OBSERVED' in grid['external_charging_labels'], rises > 0)
        for label in ('EXTERNAL_CHARGING_POSSIBLE', 'TOTAL_BATTERY_CURRENT_UNOBSERVED', 'CAPACITY_NOT_IDENTIFIABLE'):
            self.assertIn(label, context['evidence_labels'])
        self.assertFalse(context['external_charging_assumed_zero'])
        self.assertFalse(context['total_battery_current_observed'])
        self.assertNotIn('CHARGING_RESPONSE_OBSERVED', context['evidence_labels'])
        return shared, context

    def test_two_valid_observations_ten_seconds_apart_give_no_trend(self):
        rows = [row(0, 26.0, dis=10), row(10, 26.1, dis=10.5)]
        shared, context = self.assert_agreement(rows)
        self.assertEqual(len(shared['successful']), 1)
        self.assertEqual(context['accepted_intervals_too_short_for_slope'], 1)
        self.assertEqual(context['intervals_with_voltage_response'], 0)

    def test_reviewer_ten_second_series_claims_no_rise(self):
        rows = [row(0, 52.0, dis=10), row(10, 52.1, dis=10.5), row(20, 52.2, dis=11), row(30, 52.3, dis=11.5)]
        _, context = self.assert_agreement(rows)
        self.assertNotIn('VOLTAGE_RISE_OBSERVED', context['evidence_labels'])

    def test_cached_repeats_then_changed_payload_are_not_bridged(self):
        rows = [row(0, 26.0, dis=0), row(60, 26.0, dis=0), row(120, 26.0, dis=0), row(180, 26.5, dis=0)]
        shared, context = self.assert_agreement(rows)
        self.assertEqual(shared['successful'], [])
        self.assertEqual(context['intervals_rejected_by_shared_policy'], {'CACHED_REPEAT': 3})
        rows.append(row(240, 26.6, dis=0))
        shared, context = self.assert_agreement(rows)
        self.assertEqual(len(shared['successful']), 1)
        self.assertEqual(context['intervals_with_voltage_response'], 1)

    def test_valid_five_minute_intervals(self):
        rows = [row(300 * i, 26.0 - 0.02 * i, dis=10 + i) for i in range(6)]
        _, context = self.assert_agreement(rows)
        self.assertEqual(context['intervals_with_voltage_response'], 5)
        self.assertEqual(context['median_voltage_slope_v_per_hour_by_easun_context'],
                         {'EASUN_DISCHARGE_REPORTED': -0.24})

    def test_invalid_voltage_between_valid_observations(self):
        for bad in (0.0, -1.0, None, float('nan')):
            with self.subTest(bad=bad):
                _, context = self.assert_agreement([row(0, 26.0), row(300, bad), row(600, 26.4, dis=11)])
                self.assertEqual(context['intervals_with_voltage_response'], 0)

    def test_missing_current_is_not_bridged(self):
        _, context = self.assert_agreement([row(0, 26.0, chg=None), row(600, 26.6, chg=None)])
        self.assertEqual(context['intervals_by_easun_reported_current_context'], {})

    def test_long_telemetry_gap(self):
        _, context = self.assert_agreement([row(0, 26.0), row(1000, 26.5, dis=11)])
        self.assertEqual(context['intervals_with_voltage_response'], 0)
        self.assertEqual(context['gaps_or_nonpositive_intervals'], 1)

    def test_voltage_rise_while_easun_reports_discharge(self):
        _, context = self.assert_agreement([row(0, 25.4, dis=10, load=800), row(300, 25.6, dis=10.5, load=800)])
        self.assertEqual(context['intervals_by_easun_reported_current_context'], {'EASUN_DISCHARGE_REPORTED': 1})
        self.assertIn('VOLTAGE_RISE_OBSERVED', context['evidence_labels'])
        self.assertEqual(context['voltage_rises_without_easun_charge_reported'], 1)
        self.assertEqual(context['charging_relationship'], 'CHARGING_RELATIONSHIP_UNVERIFIED')

    def test_external_charging_possible_but_unmeasured(self):
        _, context = self.assert_agreement([row(0, 25.4, dis=0, soc=50), row(300, 25.7, dis=0, soc=52)])
        self.assertEqual(context['intervals_by_easun_reported_current_context'], {'NO_EASUN_CURRENT_REPORTED': 1})
        self.assertEqual(context['voltage_rises_without_easun_charge_reported'], 1)
        self.assertEqual(context['external_charging_status'], 'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT')
        self.assertIn('SOC_CHANGE_NOT_VALIDATED_BY_EASUN_THROUGHPUT', context['evidence_labels'])

    def test_conflicting_channels_are_counted_not_trended(self):
        _, context = self.assert_agreement([row(0, 25.4, chg=4, dis=20), row(300, 25.6, chg=5, dis=22)])
        self.assertEqual(context['simultaneous_nonzero_easun_charge_and_discharge_channel_intervals'], 1)
        self.assertEqual(context['intervals_with_voltage_response'], 0)

    def test_randomized_sequences_agree_across_modules(self):
        rng = random.Random(20261110)
        choices_v = (25.8, 25.9, 26.0, 26.1, 0.0, None, -1.0, float('nan'))
        for case in range(300):
            t, rows = 0, []
            for _ in range(rng.randint(2, 14)):
                t += rng.choice((10, 30, 60, 60, 120, 300, 950))
                if rows and rng.random() < 0.25:
                    rows.append(dict(rows[-1], time=T0 + timedelta(seconds=t)))
                    continue
                v = rng.choice(choices_v)
                chg, dis = rng.choice(((0.0, 10.0), (0.0, 12.0), (3.0, 0.0), (0.0, 0.0), (None, 5.0), (2.0, 4.0)))
                rows.append(row(t, v, chg=chg, dis=dis, load=rng.choice((300.0, 500.0, 800.0))))
            with self.subTest(case=case):
                self.assert_agreement(rows)


if __name__ == '__main__':
    unittest.main()
