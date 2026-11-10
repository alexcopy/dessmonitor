import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.web_battery_analysis import build_battery_grid
from app import web_routes
from app import web_queries


NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def metric(minutes_ago, **changes):
    return {'time': NOW - timedelta(minutes=minutes_ago), 'working_mode': 'Line Mode',
            'battery_voltage': 25.5, 'battery_soc': 50,
            'battery_current_chg': 0.0, 'battery_current_dis': 0.0,
            'pv_power_w': 200.0, 'output_power_w': 600.0, **changes}


class BatteryGridAnalysisTests(unittest.TestCase):
    def test_grid_bounds_rows_marks_cached_payload_and_only_derives_new_battery_fields(self):
        latest = metric(0, battery_voltage=25.7, pv_power_w=900, output_power_w=300)
        older = metric(5, battery_voltage=25.5, pv_power_w=700, output_power_w=500)
        context = metric(10, battery_voltage=25.5, pv_power_w=700, output_power_w=500)
        page = build_battery_grid([latest, older, context], limit=2, has_more=True, now=NOW)
        self.assertEqual(len(page['observations']), 2)
        self.assertEqual(len(page['battery_behavior']), 1)
        self.assertEqual(page['observations'][0]['voltage_change_since_previous_valid_v'], 0.2)
        self.assertIn('EXTERNAL_CHARGING_POSSIBLE', page['observations'][0]['external_charging_status'])
        self.assertIn('DEVICE_MEASUREMENT_TIME_UNVERIFIED', page['observations'][0]['data_provenance'])
        self.assertIn('REPEATED_CACHED_PAYLOAD', page['observations'][1]['data_quality'])
        behavior = page['battery_behavior'][0]
        # The older endpoint is cached relative to the context row, so a
        # 5-minute voltage slope cannot be treated as independent evidence.
        self.assertIsNone(behavior['voltage_slope_v_per_hour'])
        self.assertIn('CACHED_REPEAT', behavior['easun_throughput_exclusions'])
        self.assertEqual(behavior['capacity_or_response_proxy'].split(';')[0], 'CAPACITY_NOT_IDENTIFIABLE')
        self.assertIn('TOTAL BATTERY CURRENT UNOBSERVED', behavior['capacity_or_response_proxy'])
        self.assertEqual(page['external_charging_status'], 'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT')

    def test_missing_current_is_not_rendered_as_zero_and_long_interval_is_not_integrated(self):
        latest = metric(0, battery_voltage=25.7, battery_current_chg=None,
                        battery_current_dis=None)
        older = metric(20, battery_voltage=25.5, battery_current_chg=None,
                       battery_current_dis=None)
        page = build_battery_grid([latest, older], limit=1, has_more=True, now=NOW)
        observation = page['observations'][0]
        self.assertIsNone(observation['easun_charge_current_a_reported'])
        self.assertIn('EASUN_CURRENT_UNKNOWN', observation['external_charging_status'])
        behavior = page['battery_behavior'][0]
        self.assertIsNone(behavior['voltage_slope_v_per_hour'])
        self.assertEqual(behavior['slope_status'], 'CONTINUITY_NOT_VERIFIED')
        self.assertIsNone(behavior['easun_charge_ah_proxy'])
        self.assertIsNone(behavior['easun_discharge_ah_proxy'])

    def test_long_collection_gap_does_not_produce_a_voltage_slope(self):
        latest = metric(0, battery_voltage=25.7)
        older = metric(20, battery_voltage=25.5)
        page = build_battery_grid([latest, older], limit=1, has_more=True, now=NOW)
        behavior = page['battery_behavior'][0]
        self.assertIsNone(behavior['voltage_slope_v_per_hour'])
        self.assertEqual(behavior['slope_status'], 'CONTINUITY_NOT_VERIFIED')
        self.assertIn('COLLECTION_GAP_OVER_15M', behavior['easun_throughput_exclusions'])


class BatteryGridIntervalTests(unittest.TestCase):
    """Fractional, duplicate and reversed collection intervals (no truncation to 0 s)."""

    def page(self, older_offset):
        latest = metric(0, battery_voltage=25.7)
        older = dict(metric(0, battery_voltage=25.5), time=NOW - older_offset)
        return build_battery_grid([latest, older], limit=1, has_more=True, now=NOW)

    def test_subsecond_and_short_intervals_never_divide_by_zero_or_claim_a_slope(self):
        for millis in (100, 500, 1000):
            with self.subTest(millis=millis):
                page = self.page(timedelta(milliseconds=millis))
                observation = page['observations'][0]
                self.assertEqual(observation['interval_seconds'], millis / 1000)
                self.assertIn('INTERVAL_TOO_SHORT_FOR_SLOPE', observation['interval_status'])
                behavior = page['battery_behavior'][0]
                self.assertIsNone(behavior['voltage_slope_v_per_hour'])
                self.assertEqual(behavior['slope_status'], 'INTERVAL_TOO_SHORT_FOR_SLOPE')
                self.assertIn('NOT_INDEPENDENT_VOLTAGE_EVIDENCE', behavior['estimated_voltage_response'])
                # A near-simultaneous rise is not counted as independent rise evidence.
                self.assertNotIn('VOLTAGE_RISE_OBSERVED', page['external_charging_labels'])

    def test_identical_timestamps_produce_no_interval(self):
        page = self.page(timedelta(0))
        self.assertIsNone(page['observations'][0]['interval_seconds'])
        self.assertIn('DUPLICATE_COLLECTION_TIME', page['observations'][0]['interval_status'])
        self.assertEqual(page['battery_behavior'], [])

    def test_reversed_timestamps_produce_no_interval(self):
        page = self.page(-timedelta(minutes=5))
        self.assertIsNone(page['observations'][0]['interval_seconds'])
        self.assertIn('OUT_OF_ORDER_COLLECTION_TIME', page['observations'][0]['interval_status'])
        self.assertEqual(page['battery_behavior'], [])

    def test_five_minute_interval_computes_slope(self):
        page = self.page(timedelta(minutes=5))
        self.assertEqual(page['observations'][0]['interval_seconds'], 300)
        self.assertEqual(page['observations'][0]['interval_status'], 'INTERVAL_AVAILABLE')
        behavior = page['battery_behavior'][0]
        self.assertEqual(behavior['voltage_slope_v_per_hour'], 2.4)
        self.assertEqual(behavior['slope_status'], 'COMPUTED')
        self.assertIn('VOLTAGE_RISE_OBSERVED', page['external_charging_labels'])


def _distinct(rows):
    """Give each valid-voltage row a distinct voltage so none is a cached battery repeat."""
    return [dict(item, battery_voltage=round(25.0 + index * 0.1, 2)) if item['battery_voltage'] is not None
            else item for index, item in enumerate(rows)]


class BatteryGridIntegrationContinuityTests(unittest.TestCase):
    """EASUN Ah/Wh proxies only over fully observed consecutive collections."""

    def latest_behavior(self, rows):
        return build_battery_grid(_distinct(rows), limit=len(rows) - 1, has_more=True, now=NOW)['battery_behavior'][0]

    def test_complete_coverage_integrates(self):
        behavior = self.latest_behavior([metric(0, battery_current_dis=15.0), metric(5, battery_current_dis=15.0)])
        self.assertEqual(behavior['easun_discharge_ah_proxy'], 1.25)
        self.assertEqual(behavior['easun_throughput_status'], 'COMPLETE')
        self.assertEqual(behavior['easun_throughput_covered_seconds'], 300)
        self.assertEqual(behavior['easun_throughput_coverage_ratio'], 1.0)
        self.assertIsNone(behavior['total_battery_throughput_ah'])

    def test_missing_middle_current_interrupts_instead_of_bridging(self):
        # Reproduces the audit: 15 A at both ends, unknown at the 5-minute midpoint.
        rows = [metric(0, battery_current_dis=15.0),
                metric(5, battery_voltage=None, battery_current_dis=None),
                metric(10, battery_current_dis=15.0)]
        behavior = self.latest_behavior(rows)
        self.assertEqual(behavior['interval_seconds'], 600)
        self.assertIsNone(behavior['easun_discharge_ah_proxy'])
        self.assertIsNone(behavior['easun_discharge_wh_proxy'])
        self.assertEqual(behavior['easun_throughput_status'], 'NOT_INTEGRATED')
        self.assertEqual(behavior['easun_throughput_elapsed_seconds'], 600)
        self.assertEqual(behavior['easun_throughput_covered_seconds'], 0)
        self.assertEqual(behavior['easun_throughput_coverage_ratio'], 0.0)
        self.assertIn('MISSING_CURRENT_INTERRUPTS_CONTINUITY', behavior['easun_throughput_exclusions'])

    def test_zero_current_is_observed_zero_not_missing(self):
        behavior = self.latest_behavior([metric(0), metric(5)])
        self.assertEqual(behavior['easun_charge_ah_proxy'], 0.0)
        self.assertEqual(behavior['easun_discharge_ah_proxy'], 0.0)
        self.assertEqual(behavior['easun_throughput_status'], 'COMPLETE')

    def test_conflicting_channels_are_excluded(self):
        behavior = self.latest_behavior([metric(0, battery_current_dis=10.0),
                                         metric(5, battery_current_chg=5.0, battery_current_dis=5.0)])
        self.assertIsNone(behavior['easun_charge_ah_proxy'])
        self.assertIn('CONFLICTING_CURRENT_CHANNELS_EXCLUDED', behavior['easun_throughput_exclusions'])

    def test_long_gap_is_not_integrated(self):
        behavior = self.latest_behavior([metric(0, battery_current_dis=10.0), metric(20, battery_current_dis=10.0)])
        self.assertIsNone(behavior['easun_discharge_ah_proxy'])
        self.assertIn('COLLECTION_GAP_OVER_15M', behavior['easun_throughput_exclusions'])
        self.assertIn('COLLECTION_GAP_OVER_15M', behavior['limitation'])

    def test_duplicate_timestamp_with_differing_values_is_ambiguous(self):
        rows = [metric(0, battery_current_dis=10.0),
                metric(5, battery_voltage=None, battery_current_dis=12.0),
                metric(5, battery_current_dis=15.0)]
        behavior = self.latest_behavior(rows)
        self.assertIsNone(behavior['easun_discharge_ah_proxy'])
        self.assertIn('AMBIGUOUS_DUPLICATE_TIMESTAMP', behavior['easun_throughput_exclusions'])

    def test_partial_coverage_reports_covered_seconds_without_a_total(self):
        rows = [metric(0, battery_voltage=None, battery_current_dis=10.0),
                metric(5, battery_voltage=None, battery_current_dis=None),
                metric(10, battery_current_dis=10.0)]
        behavior = self.latest_behavior([metric(-5, battery_current_dis=10.0)] + rows)
        self.assertEqual(behavior['easun_throughput_elapsed_seconds'], 900)
        # Missing voltage is now a barrier for Ah as well as Wh. The previous
        # 300 seconds were incorrectly integrated across that invalid row.
        self.assertEqual(behavior['easun_throughput_covered_seconds'], 0)
        self.assertEqual(behavior['easun_throughput_status'], 'NOT_INTEGRATED')
        self.assertIsNone(behavior['easun_discharge_ah_proxy'])


class BatteryObservationRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        router = web_routes.create_auth_router(None, None)
        self.api = next(route.endpoint for route in router.routes
                        if route.path == '/api/controls/battery-observations')

    async def test_auth_range_and_limit_are_checked_before_query(self):
        with patch.object(web_routes, '_check_auth', return_value=(False, None)), \
             patch('app.web_queries.battery_observations', new=AsyncMock()) as query:
            self.assertEqual((await self.api(object())).status_code, 401)
            query.assert_not_awaited()
        with patch.object(web_routes, '_check_auth', return_value=(True, 'operator')), \
             patch.dict('os.environ', {'WEB_AUTH_SESSION_SECRET': 's' * 64}, clear=True), \
             patch('app.web_queries.battery_observations', new=AsyncMock()) as query:
            self.assertEqual((await self.api(object(), days=3)).status_code, 422)
            self.assertEqual((await self.api(object(), limit=201)).status_code, 422)
            query.assert_not_awaited()

    async def test_signed_cursor_keeps_fixed_range_and_uses_bound_time_keyset(self):
        # The route bounds the range with the real clock and the signed cursor must lie
        # inside it, so these rows are anchored to the real clock, not the fixed NOW.
        real_now = datetime.now(timezone.utc)
        newest = dict(metric(1), time=real_now - timedelta(minutes=1))
        older = dict(metric(2), time=real_now - timedelta(minutes=2))
        with patch.object(web_routes, '_check_auth', return_value=(True, 'operator')), \
             patch.dict('os.environ', {'DATABASE_URL': 'synthetic', 'WEB_AUTH_SESSION_SECRET': 's' * 64}, clear=True), \
             patch('app.web_queries.battery_observations', new=AsyncMock(side_effect=[[newest, older], [older]])) as query:
            first = await self.api(object(), days=1, limit=1)
            self.assertEqual(first.status_code, 200)
            first_data = json.loads(first.body)
            self.assertTrue(first_data['has_more'])
            token = first_data['next_cursor']
            self.assertTrue(token)
            second = await self.api(object(), days=1, limit=1, cursor=token)
            self.assertEqual(second.status_code, 200)
            second_data = json.loads(second.body)
        self.assertFalse(second_data['has_more'])
        self.assertIsNone(second_data['next_cursor'])
        self.assertEqual(query.await_count, 2)
        first_args = query.await_args_list[0].args
        second_args = query.await_args_list[1].args
        self.assertEqual(first_args[0], 'synthetic')
        self.assertEqual(first_args[3], 2)
        self.assertIsNone(first_args[4])
        self.assertEqual(second_args[1:3], first_args[1:3])
        self.assertEqual(second_args[3], 2)
        self.assertEqual(second_args[4], newest['time'])

    async def test_invalid_tampered_or_range_mismatched_cursor_returns_422(self):
        with patch.object(web_routes, '_check_auth', return_value=(True, 'operator')), \
             patch.dict('os.environ', {'DATABASE_URL': 'synthetic', 'WEB_AUTH_SESSION_SECRET': 's' * 64}, clear=True), \
             patch('app.web_queries.battery_observations', new=AsyncMock(return_value=[])) as query:
            first = await self.api(object(), days=1, limit=1)
            # Forge a structurally valid signed cursor whose days do not match.
            now = datetime.now(timezone.utc)
            payload = {'v': 1, 'exp': int(now.timestamp()) + 300, 'days': 2,
                       'lower': (now - timedelta(days=2)).isoformat(), 'upper': now.isoformat(),
                       'after_time': now.isoformat()}
            token = web_routes._encode_battery_observation_cursor(payload, 's' * 64)
            mismatched = await self.api(object(), days=1, cursor=token)
            tampered = await self.api(object(), days=1, cursor=token[:-1] + ('A' if token[-1] != 'A' else 'B'))
            oversized = await self.api(object(), days=1, cursor='x' * 2049)
            expired_payload = {'v': 1, 'exp': int(now.timestamp()) - 1, 'days': 1,
                               'lower': (now - timedelta(days=1)).isoformat(), 'upper': now.isoformat(),
                               'after_time': now.isoformat()}
            expired_token = web_routes._encode_battery_observation_cursor(expired_payload, 's' * 64)
            expired = await self.api(object(), days=1, cursor=expired_token)
            boolean_version_payload = dict(expired_payload, v=True, exp=int(now.timestamp()) + 300)
            boolean_version_payload['days'] = 1
            boolean_version_payload['lower'] = (now - timedelta(days=1)).isoformat()
            boolean_version_payload['upper'] = now.isoformat()
            boolean_version_payload['after_time'] = now.isoformat()
            boolean_version = await self.api(object(), days=1, cursor=web_routes._encode_battery_observation_cursor(
                boolean_version_payload, 's' * 64))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(mismatched.status_code, 422)
        self.assertEqual(tampered.status_code, 422)
        self.assertEqual(oversized.status_code, 422)
        self.assertEqual(expired.status_code, 422)
        self.assertEqual(boolean_version.status_code, 422)
        self.assertEqual(query.await_count, 1)

    async def test_empty_observation_range_is_a_successful_empty_page(self):
        with patch.object(web_routes, '_check_auth', return_value=(True, 'operator')), \
             patch.dict('os.environ', {'DATABASE_URL': 'synthetic', 'WEB_AUTH_SESSION_SECRET': 's' * 64}, clear=True), \
             patch('app.web_queries.battery_observations', new=AsyncMock(return_value=[])):
            response = await self.api(object())
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertEqual(body['observations'], [])
        self.assertFalse(body['has_more'])
        self.assertIsNone(body['next_cursor'])

    async def test_database_failure_is_503(self):
        with patch.object(web_routes, '_check_auth', return_value=(True, 'operator')), \
             patch.dict('os.environ', {'DATABASE_URL': 'synthetic', 'WEB_AUTH_SESSION_SECRET': 's' * 64}, clear=True), \
             patch('app.web_queries.battery_observations', new=AsyncMock(side_effect=ConnectionError())):
            response = await self.api(object())
        self.assertEqual(response.status_code, 503)

    async def test_query_binds_bounds_cursor_and_limit(self):
        lower, upper, after = NOW-timedelta(days=1), NOW, NOW-timedelta(minutes=5)
        with patch.object(web_queries, '_fetch', new=AsyncMock(return_value=[])) as fetch:
            await web_queries.battery_observations('synthetic', lower, upper, 51, after)
        database_url, sql, *bound = fetch.await_args.args
        self.assertEqual(database_url, 'synthetic')
        self.assertIn('time >= $1 AND time <= $2', sql)
        self.assertIn('time < $3', sql)
        self.assertIn('LIMIT $4', sql)
        self.assertIn('LIMIT $5', sql)
        self.assertIn('time < (SELECT min(time) FROM page)', sql)
        self.assertEqual(bound, [lower, upper, after, 51, 0])
        self.assertEqual(fetch.await_args.kwargs['command_timeout'], 10)
