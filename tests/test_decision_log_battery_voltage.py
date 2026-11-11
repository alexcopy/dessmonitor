"""Decision Log presentation of the latest battery voltage.

Reviewer counterexample: previous 26.0 V, latest 0.0 V. The calculations already
reject 0.0 V; the Decision Log must not then serve or show it as a measurement.
Expectations are shared with tests/test_decision_log_battery_voltage_ui.cjs via
tests/fixtures/decision_log_battery_voltage_cases.json so API and UI agree.
"""
import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import web_routes
from app.battery_intervals import battery_voltage_presentation
from app.control import online_output_learning as ol
import pg_isolation

T0 = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(minutes=5)
FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'decision_log_battery_voltage_cases.json').read_text())
CASES = FIXTURE['cases']
FIELDS = ('battery_v', 'battery_v_reported_raw', 'battery_v_status', 'battery_v_validity_source')
_FLOATS = {'NaN': float('nan'), 'Infinity': float('inf'), '-Infinity': float('-inf')}


def telemetry(value):
    return _FLOATS.get(value, value) if isinstance(value, str) else value


def row(sec, v, dis=10.0):
    return {'time': T0 + timedelta(seconds=sec), 'working_mode': 'Invert Mode', 'battery_voltage': v,
            'battery_current_chg': 0.0, 'battery_current_dis': dis, 'pv_power_w': 100.0,
            'output_power_w': 500.0, 'battery_soc': 60.0}


def reviewer_rows(latest_voltage=0.0):
    return [row(0, 26.0, dis=10.0), row(60, latest_voltage, dis=11.0)]


def battery_fields(entry):
    return {key: entry[key] for key in FIELDS if key in entry}


def stored_battery_fields(entry):
    return {key: entry[key] for key in FIELDS[:3] if key in entry}


class ReviewerZeroVoltageTests(unittest.TestCase):
    def setUp(self):
        self.entry = ol.evaluate_collected_history(reviewer_rows(0.0), NOW)

    def test_value_is_excluded_from_battery_calculations(self):
        response = self.entry['battery_response']
        self.assertIn('INVALID_BATTERY_VOLTAGE', self.entry['reason_codes'])
        self.assertEqual(response['excluded_observations']['INVALID_ZERO_VOLTAGE'], 1)
        self.assertEqual(response['intervals_integrated'], 0)
        self.assertIsNone(response['median_voltage_slope_v_per_hour'])
        self.assertIsNone(response['discharged_ah_proxy'])
        self.assertEqual(response['throughput_coverage'], 'NONE')

    def test_entry_does_not_present_zero_as_a_measurement(self):
        self.assertIsNone(self.entry['battery_v'])
        self.assertEqual(self.entry['battery_v_reported_raw'], 0.0)
        self.assertEqual(self.entry['battery_v_status'], 'INVALID_ZERO_VOLTAGE')

    def test_journal_json_and_api_view(self):
        stored = json.loads(json.dumps(self.entry, allow_nan=False))
        api = web_routes._decision_export(stored)
        self.assertEqual(battery_fields(api), {'battery_v': None, 'battery_v_reported_raw': 0.0,
            'battery_v_status': 'INVALID_ZERO_VOLTAGE', 'battery_v_validity_source': 'RECORDED_BY_WORKER'})
        json.dumps(api, allow_nan=False)

    def test_zero_contributes_nothing_compared_with_a_missing_reading(self):
        # Same rows with the latest voltage missing instead of 0.0 V: every quantitative
        # result must match (only exclusion labels differ, by design).
        missing = ol.evaluate_collected_history(reviewer_rows(None), NOW)
        for key in ('intervals_integrated', 'integrated_seconds', 'median_voltage_slope_v_per_hour',
                    'latest_voltage_slope_v_per_hour', 'charged_ah_proxy', 'discharged_ah_proxy',
                    'throughput_coverage', 'integration_coverage_ratio', 'capacity_status'):
            with self.subTest(key=key):
                self.assertEqual(self.entry['battery_response'].get(key), missing['battery_response'].get(key))
        for key in ('decision', 'proposed_voltage', 'candidate_voltage', 'stability', 'confidence'):
            with self.subTest(key=key):
                self.assertEqual(self.entry[key], missing[key])
        self.assertEqual(self.entry['charging_context']['intervals_accepted_by_shared_policy'],
                         missing['charging_context']['intervals_accepted_by_shared_policy'])


class SharedContractTests(unittest.TestCase):
    def test_worker_cases_produce_fixture_fields(self):
        for case in (c for c in CASES if c['kind'] == 'worker'):
            with self.subTest(case=case['name']):
                value = telemetry(case['telemetry_battery_voltage'])
                self.assertEqual(battery_voltage_presentation(value), case['stored'])
                entry = ol.evaluate_collected_history(reviewer_rows(value), NOW)
                stored = json.loads(json.dumps(entry, allow_nan=False))
                self.assertEqual(stored_battery_fields(stored), case['stored'])
                self.assertEqual(battery_fields(web_routes._decision_export(stored)), case['api'])
                status = case['stored']['battery_v_status']
                if status.startswith('INVALID_'):
                    # Existing reason codes are unchanged: finite invalid -> INVALID_BATTERY_VOLTAGE,
                    # non-finite was already reported as MISSING_BATTERY_VOLTAGE by the calculation.
                    expected = ('MISSING_BATTERY_VOLTAGE' if status == 'INVALID_NON_FINITE_VOLTAGE'
                                else 'INVALID_BATTERY_VOLTAGE')
                    self.assertIn(expected, entry['reason_codes'])
                    self.assertEqual(entry['battery_response']['intervals_integrated'], 0)
                    self.assertIsNone(entry['battery_response']['median_voltage_slope_v_per_hour'])

    def test_legacy_entries_are_classified_on_read_without_mutation(self):
        for case in (c for c in CASES if c['kind'] == 'legacy'):
            with self.subTest(case=case['name']):
                stored = dict(case['stored'], decision='HOLD', evaluated_at='2026-11-01T00:00:00+00:00',
                              algorithm_version='online-exploratory-5')
                before = copy.deepcopy(stored)
                api = web_routes._decision_export(stored)
                self.assertEqual(battery_fields(api), case['api'])
                self.assertEqual(stored, before)
                json.dumps(api, allow_nan=False)

    def test_inconsistent_valid_status_is_not_trusted(self):
        api = web_routes._decision_export({'battery_v': 0.0, 'battery_v_reported_raw': 0.0,
                                           'battery_v_status': 'VALID'})
        self.assertIsNone(api['battery_v'])
        self.assertEqual(api['battery_v_status'], 'INVALID_ZERO_VOLTAGE')


class DecisionRouteVoltageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        router = web_routes.create_auth_router(None, None)
        self.api = next(r.endpoint for r in router.routes if r.path == '/api/controls/decisions')
        self.export = next(r.endpoint for r in router.routes if r.path == '/api/controls/decisions/export')
        # Journal v2 not installed: these tests cover the Journal v1 read path.
        _v1 = patch.object(web_routes.web_queries, 'journal_v2_status', new=AsyncMock(return_value={'mode': 'v1', 'v2_installed': False}))
        _v1.start(); self.addCleanup(_v1.stop)
        new = json.loads(json.dumps(ol.evaluate_collected_history(reviewer_rows(0.0), NOW), allow_nan=False))
        legacy = {'evaluation_id': 'legacy', 'evaluated_at': '2026-11-10T09:00:00+00:00', 'decision': 'HOLD',
                  'algorithm_version': 'online-exploratory-5', 'battery_v': 0.0, 'hardware_write_attempted': False}
        self.rows = [{'evaluation_id': e['evaluation_id'], 'evaluated_at': datetime.fromisoformat(e['evaluated_at']),
                      'entry': json.dumps(e)} for e in (new, legacy)]
        self.env = {'DATABASE_URL': 'synthetic', 'ADAPTIVE_OUTPUT_LEARNING_ENABLED': 'true',
                    'WEB_AUTH_SESSION_SECRET': 'a' * 64}

    def assert_invalid_zero(self, item, source):
        self.assertEqual(battery_fields(item), {'battery_v': None, 'battery_v_reported_raw': 0.0,
            'battery_v_status': 'INVALID_ZERO_VOLTAGE', 'battery_v_validity_source': source})

    async def test_latest_and_historical_rows(self):
        with patch.object(web_routes, '_check_auth', return_value=(True, 'user')), \
             patch.dict(os.environ, self.env), \
             patch.object(web_routes.web_queries, 'latest_decision', AsyncMock(return_value=self.rows[0])), \
             patch.object(web_routes.web_queries, 'decision_journal', AsyncMock(return_value=self.rows)):
            response = await self.api(object(), days=1)
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assert_invalid_zero(payload['latest_decision'], 'RECORDED_BY_WORKER')
        self.assert_invalid_zero(payload['decisions'][0], 'RECORDED_BY_WORKER')
        self.assert_invalid_zero(payload['decisions'][1], 'DERIVED_ON_READ_FROM_LEGACY_ENTRY')

    async def test_export_uses_the_same_view(self):
        async def stream(*_args, **_kwargs):
            for item in self.rows:
                yield item
        with patch.object(web_routes, '_check_auth', return_value=(True, 'user')), \
             patch.dict(os.environ, self.env), \
             patch.object(web_routes.web_queries, 'stream_decisions', stream):
            response = await self.export(object(), days=1)
            body = b''.join([chunk async for chunk in response.body_iterator])
        decisions = json.loads(body)['decisions']
        self.assert_invalid_zero(decisions[0], 'RECORDED_BY_WORKER')
        self.assert_invalid_zero(decisions[1], 'DERIVED_ON_READ_FROM_LEGACY_ENTRY')


_PG_DSN = pg_isolation.configured_dsn()  # no fallback; proven by tests/pg_isolation.py before use


@unittest.skipUnless(os.environ.get('DECISION_LOG_LOCAL_POSTGRES') == '1',
                     'requires disposable local PostgreSQL (DECISION_LOG_LOCAL_POSTGRES=1)')
class DecisionLogVoltagePostgresTests(unittest.IsolatedAsyncioTestCase):
    """Worker write -> journal -> web query -> API view, on this run's disposable database."""

    async def asyncSetUp(self):
        self.pool = await pg_isolation.create_pool(_PG_DSN, min_size=1, max_size=3)
        async with self.pool.acquire() as c:
            await c.execute('DROP TABLE IF EXISTS adaptive_decision_journal; DROP TABLE IF EXISTS inverter_metrics')
            await c.execute((Path(__file__).parents[1] / 'migrations' / '001_adaptive_decision_journal.sql').read_text())
            await c.execute('''CREATE TABLE inverter_metrics (time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT,
                battery_voltage DOUBLE PRECISION, battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
                battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')
            columns = ('time', 'working_mode', 'battery_voltage', 'battery_soc', 'battery_current_chg',
                       'battery_current_dis', 'pv_power_w', 'output_power_w')
            await c.copy_records_to_table('inverter_metrics', columns=columns,
                                          records=[tuple(r[k] for k in columns) for r in reviewer_rows(0.0)])
            await c.execute('''INSERT INTO adaptive_decision_journal (evaluation_id, evaluated_at, entry)
                VALUES ('legacy-zero', $1, $2::jsonb)''', NOW - timedelta(minutes=1),
                json.dumps({'evaluated_at': (NOW - timedelta(minutes=1)).isoformat(), 'decision': 'HOLD',
                            'algorithm_version': 'online-exploratory-5', 'battery_v': 0.0}))

    async def asyncTearDown(self):
        async with self.pool.acquire() as c:
            await c.execute('DROP TABLE IF EXISTS adaptive_decision_journal; DROP TABLE IF EXISTS inverter_metrics')
        await self.pool.close()

    async def test_worker_journal_and_api(self):
        from app import web_queries
        self.assertTrue(await ol.run_evaluation(self.pool, NOW, historical_battery_v=26.0))
        with pg_isolation.app_connections():  # web_queries opens raw asyncpg connections
            latest = await web_queries.latest_decision(_PG_DSN)
        stored = json.loads(latest['entry'])
        self.assertEqual(stored_battery_fields(stored), {'battery_v': None, 'battery_v_reported_raw': 0.0,
                                                        'battery_v_status': 'INVALID_ZERO_VOLTAGE'})
        self.assertIn('INVALID_BATTERY_VOLTAGE', stored['reason_codes'])
        with pg_isolation.app_connections():  # web_queries opens raw asyncpg connections
            rows = await web_queries.decision_journal(_PG_DSN, NOW - timedelta(days=1), NOW, 10)
        views = [web_routes._decision_export(json.loads(r['entry'])) for r in rows]
        self.assertEqual([v['battery_v_validity_source'] for v in views],
                         ['RECORDED_BY_WORKER', 'DERIVED_ON_READ_FROM_LEGACY_ENTRY'])
        for view in views:
            self.assertIsNone(view['battery_v'])
            self.assertEqual(view['battery_v_reported_raw'], 0.0)
            self.assertEqual(view['battery_v_status'], 'INVALID_ZERO_VOLTAGE')
        async with self.pool.acquire() as c:
            legacy = json.loads(await c.fetchval("SELECT entry FROM adaptive_decision_journal WHERE evaluation_id='legacy-zero'"))
        self.assertEqual(legacy['battery_v'], 0.0)  # stored legacy row is not rewritten
        self.assertNotIn('battery_v_status', legacy)


if __name__ == '__main__':
    unittest.main()
