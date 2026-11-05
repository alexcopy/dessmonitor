import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import web_routes
from app.web_history_analysis import build_observed_output_transitions


AS_OF = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def observation(at, mode, **values):
    return {
        "time": at,
        "working_mode": mode,
        "charger_priority": "solar_only",
        "charger_priority_raw": "0",
        "output_priority_raw": "priority-1",
        "mains_status": "present",
        "battery_voltage": 25.5,
        "battery_soc": 60.0,
        "battery_current_chg": 0.0,
        "battery_current_dis": 12.0 if mode == "Invert Mode" else 0.0,
        "pv_power_w": 450.0,
        "output_power_w": 600.0,
        "output_voltage": 230.0,
        "ac_input_voltage": 231.0,
        "ac_output_load_pct": 30.0,
        "is_context": False,
        **values,
    }


class FakeHistoryConnection:
    def __init__(self, history):
        self.history = sorted(history, key=lambda row: row["time"])
        self.fetch_calls = []
        self.closed = False

    async def fetch(self, query, start, as_of):
        self.fetch_calls.append((query, start, as_of))
        selected = [dict(row, is_context=False) for row in self.history
                    if start <= row["time"] <= as_of]
        previous = [row for row in self.history if row["time"] < start]
        if previous:
            selected.append(dict(previous[-1], is_context=True))
        earliest = self.history[0]['time'] if self.history else None
        if not selected:
            return [{'time': None, 'verified_since': earliest}]
        for item in selected:
            item['verified_since'] = earliest
        return sorted(selected, key=lambda row: row["time"])

    async def close(self):
        self.closed = True


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return AS_OF if tz else AS_OF.replace(tzinfo=None)


class ChargeHistoryApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        router = web_routes.create_auth_router(None, None)
        self.endpoint = next(route.endpoint for route in router.routes
                             if route.path == "/api/charge/history")

    async def call_endpoint(self, history, days=1):
        connection = FakeHistoryConnection(history)
        asyncpg = SimpleNamespace(connect=AsyncMock(return_value=connection))
        with patch.object(web_routes, "_check_auth", return_value=(True, "synthetic")), \
             patch.object(web_routes, "datetime", FixedDateTime), \
             patch.dict("os.environ", {"DATABASE_URL": "synthetic-local"}), \
             patch.dict(sys.modules, {"asyncpg": asyncpg}):
            response = await self.endpoint(object(), days=days)
        if response.status_code != 200:
            self.fail(response.body.decode())
        return response, connection, asyncpg

    async def test_range_query_preserves_branch_base_contract_and_predecessor(self):
        now = AS_OF
        history = [observation(now - timedelta(days=8), "Line Mode"),
                   observation(now - timedelta(days=2), "Line Mode"),
                   observation(now - timedelta(minutes=10), "Invert Mode")]
        response, connection, asyncpg = await self.call_endpoint(history, days=1)
        payload = json.loads(response.body)
        query, start, as_of = connection.fetch_calls[0]
        self.assertIn("WHERE time >= $1 AND time <= $2", query)
        self.assertIn("ORDER BY time DESC", query)
        self.assertIn("LIMIT 1", query)
        self.assertIn("MIN(time)", query)
        self.assertEqual(start, now - timedelta(days=1))
        self.assertEqual(as_of, now)
        self.assertEqual(len(connection.fetch_calls), 1)
        self.assertTrue(connection.closed)
        asyncpg.connect.assert_awaited_once_with("synthetic-local")
        self.assertEqual(len(payload["observations"]), 1)
        self.assertEqual(set(payload), {'verified_since', 'observations', 'transitions', 'summary_24h', 'observed_gap_threshold_seconds'})
        self.assertEqual(payload['verified_since'], (now - timedelta(days=8)).isoformat())
        self.assertEqual(set(payload['observations'][0]), {'time', 'working_mode', 'charger_priority', 'charger_priority_raw', 'output_priority_raw', 'mains_status', 'battery_voltage', 'battery_soc', 'battery_current_chg', 'battery_current_dis', 'pv_power_w', 'output_power_w', 'output_voltage', 'ac_input_voltage', 'ac_output_load_pct'})
        self.assertEqual(payload["transitions"][0]["observed_at"], (now - timedelta(minutes=10)).isoformat())
        self.assertEqual(set(payload['transitions'][0]), {'observed_at', 'from_mode', 'to_mode', 'battery_voltage', 'battery_soc', 'battery_current_chg', 'battery_current_dis', 'pv_power_w', 'output_power_w', 'ac_input_voltage', 'output_voltage', 'charger_priority', 'output_priority_raw', 'mains_status', 'observed_dwell'})
        self.assertEqual(payload['transitions'][0]['ac_input_voltage'], 231.0)
        self.assertEqual(payload['transitions'][0]['mains_status'], 'present')

    async def test_short_transitions_and_long_gaps_remain_observable(self):
        start = AS_OF - timedelta(days=1)
        history = [
            observation(start - timedelta(minutes=1), "Line Mode"),
            observation(start + timedelta(minutes=2), "Invert Mode"),
            observation(start + timedelta(minutes=3), "Line Mode"),
            observation(start + timedelta(minutes=82), "Invert Mode"),
            observation(start + timedelta(minutes=83), "Line Mode"),
        ]
        response, _, _ = await self.call_endpoint(history)
        payload = json.loads(response.body)
        transitions = payload["transitions"]
        self.assertEqual(len(transitions), 4)
        self.assertEqual(transitions[0]["observed_at"], (start + timedelta(minutes=2)).isoformat())
        self.assertEqual(transitions[0]["observed_dwell"], 60.0)
        self.assertEqual(transitions[1]["observed_dwell"], None)
        self.assertEqual(transitions[2]["observed_dwell"], 60.0)
        self.assertEqual(payload["summary_24h"]["output_mode_changes"], 4)

    async def test_24_hour_summary_is_independent_of_requested_range(self):
        history = [
            observation(AS_OF - timedelta(days=8), "Line Mode"),
            observation(AS_OF - timedelta(days=2), "Invert Mode"),
            observation(AS_OF - timedelta(days=1, minutes=2), "Line Mode"),
            observation(AS_OF - timedelta(hours=23, minutes=58), "Invert Mode"),
            observation(AS_OF - timedelta(hours=23, minutes=57), "Line Mode"),
            observation(AS_OF - timedelta(hours=22, minutes=38), "Invert Mode"),
            observation(AS_OF - timedelta(hours=22, minutes=37), "Line Mode"),
        ]
        one_day, _, _ = await self.call_endpoint(history, days=1)
        seven_days, _, _ = await self.call_endpoint(history, days=7)
        one_day_payload, seven_day_payload = json.loads(one_day.body), json.loads(seven_days.body)
        self.assertEqual(one_day_payload["summary_24h"], seven_day_payload["summary_24h"])
        self.assertGreater(len(seven_day_payload["observations"]), len(one_day_payload["observations"]))
        self.assertLess(len(one_day.body), len(seven_days.body))
        self.assertEqual(one_day_payload["summary_24h"]["output_mode_changes"], 4)

    async def test_empty_range_has_empty_arrays_and_zero_summary(self):
        response, _, _ = await self.call_endpoint([])
        payload = json.loads(response.body)
        self.assertEqual(payload["observations"], [])
        self.assertIsNone(payload['verified_since'])
        self.assertEqual(payload["transitions"], [])
        self.assertEqual(payload["summary_24h"]["output_mode_changes"], 0)
        self.assertIsNone(payload["summary_24h"]["median_observed_dwell"])

    async def test_empty_selected_range_still_reports_global_first_collection(self):
        future = AS_OF + timedelta(days=1)
        response, _, _ = await self.call_endpoint([observation(future, 'Line Mode')])
        payload = json.loads(response.body)
        self.assertEqual(payload['observations'], [])
        self.assertEqual(payload['verified_since'], future.isoformat())

    async def test_authentication_and_invalid_range_are_checked_before_database_access(self):
        asyncpg = SimpleNamespace(connect=AsyncMock())
        with patch.object(web_routes, "_check_auth", return_value=(False, None)), \
             patch.dict(sys.modules, {"asyncpg": asyncpg}):
            unauthorized = await self.endpoint(object(), days=1)
        self.assertEqual(unauthorized.status_code, 401)
        with patch.object(web_routes, "_check_auth", return_value=(True, "synthetic")), \
             patch.dict(sys.modules, {"asyncpg": asyncpg}):
            invalid = await self.endpoint(object(), days=0)
        self.assertEqual(invalid.status_code, 422)
        asyncpg.connect.assert_not_awaited()


class DwellContinuityTests(unittest.TestCase):
    def test_unknown_middle_breaks_dwell_without_removing_real_transitions(self):
        for missing in (None, '', 'Unknown', '—', '   ', 123):
            with self.subTest(missing=missing):
                rows = [observation(AS_OF + timedelta(minutes=i), mode)
                        for i, mode in enumerate(('Line Mode', 'Invert Mode', missing, 'Line Mode', 'Invert Mode'))]
                events = build_observed_output_transitions(rows)
                self.assertEqual(len(events), 2)
                self.assertIsNone(events[0]['observed_dwell'])

    def test_unknown_at_start_and_end_does_not_invent_transitions(self):
        rows = [observation(AS_OF + timedelta(minutes=i), mode)
                for i, mode in enumerate((None, 'Line Mode', 'Invert Mode', 'Line Mode', 'Unknown'))]
        events = build_observed_output_transitions(rows)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['observed_dwell'], 60.0)
        self.assertIsNone(events[1]['observed_dwell'])

    def test_midnight_and_long_gap(self):
        midnight = datetime(2026, 11, 5, tzinfo=timezone.utc)
        rows = [observation(midnight + timedelta(minutes=i), mode) for i, mode in
                ((-2, 'Line Mode'), (-1, 'Invert Mode'), (0, 'Line Mode'), (61, 'Invert Mode'))]
        events = build_observed_output_transitions(rows)
        self.assertEqual(events[0]['observed_dwell'], 60.0)
        self.assertIsNone(events[1]['observed_dwell'])
        self.assertEqual(events[1]['observed_at'], midnight)


if __name__ == "__main__":
    unittest.main()
