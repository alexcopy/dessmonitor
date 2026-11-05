import ast
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import web_queries, web_routes


class ArchitectureTests(unittest.TestCase):
    def test_routes_have_no_sql_or_database_drivers(self):
        tree = ast.parse(Path(web_routes.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                self.assertFalse(any(name.split('.')[0] in ('sqlite3', 'asyncpg') for name in names))
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotRegex(node.value.upper(), r'\b(SELECT|INSERT|UPDATE|DELETE)\s+.+\s+FROM\b')


class QueryCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgres_success_error_timeout_and_cancellation_close(self):
        for failure in (None, RuntimeError('query error'), TimeoutError('timeout'), asyncio.CancelledError()):
            conn = SimpleNamespace(fetch=AsyncMock(return_value=[{'x': 1}]), close=AsyncMock())
            if failure is not None:
                conn.fetch.side_effect = failure
            with patch.dict(sys.modules, {'asyncpg': SimpleNamespace(connect=AsyncMock(return_value=conn))}):
                if failure is None:
                    self.assertEqual(await web_queries.charts_weather('test', '24'), [{'x': 1}])
                else:
                    with self.assertRaises(type(failure)):
                        await web_queries.charts_weather('test', '24')
            conn.close.assert_awaited_once()
            sql, param = conn.fetch.await_args.args
            self.assertIn('$1', sql)
            self.assertEqual(param, '24')

    async def test_actual_task_cancellation_closes_connection(self):
        entered = asyncio.Event()
        async def fetch(*args):
            entered.set()
            await asyncio.Event().wait()
        conn = SimpleNamespace(fetch=fetch, close=AsyncMock())
        with patch.dict(sys.modules, {'asyncpg': SimpleNamespace(connect=AsyncMock(return_value=conn))}):
            task = asyncio.create_task(web_queries.inverter_metrics('test'))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        conn.close.assert_awaited_once()

    async def test_sqlite_closes_cursor_and_connection_on_error(self):
        cursor = Mock()
        cursor.execute.side_effect = sqlite3.OperationalError('bad query')
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch.object(Path, 'exists', return_value=True), patch('sqlite3.connect', return_value=conn):
            with self.assertRaises(sqlite3.OperationalError):
                await web_queries.sqlite_energy_daily(7)
        cursor.close.assert_called_once()
        conn.close.assert_called_once()
        self.assertEqual(cursor.execute.call_args.args[1], (7,))

    async def test_sqlite_real_fixture_and_worker_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'history.sqlite'
            con = sqlite3.connect(db)
            try:
                con.execute('CREATE TABLE ml_points (timestamp TEXT, unix_ts INTEGER, data_json TEXT)')
                con.execute('INSERT INTO ml_points VALUES (datetime("now"), ?, ?)', (1, json.dumps({'unix_ts': 1, 'energy_from_pv_wh': 42})))
                con.commit()
            finally:
                con.close()
            with patch.dict(os.environ, {'ML_SQLITE_PATH': str(db)}):
                rows = await web_queries.sqlite_energy_daily(7)
            self.assertEqual(rows[0][1], 42)

    async def test_cancelled_sqlite_worker_eventually_cleans_up(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        cursor = Mock()
        def execute(*args):
            entered.set()
            release.wait(3)
        cursor.execute.side_effect = execute
        cursor.fetchall.return_value = []
        conn = Mock()
        conn.cursor.return_value = cursor
        conn.close.side_effect = closed.set
        with patch.object(Path, 'exists', return_value=True), patch('sqlite3.connect', return_value=conn):
            task = asyncio.create_task(web_queries.sqlite_energy_daily(1))
            await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            self.assertTrue(await asyncio.to_thread(closed.wait, 3))
        cursor.close.assert_called_once()

    async def test_query_parameters_are_bound(self):
        conn = SimpleNamespace(fetch=AsyncMock(return_value=[]), close=AsyncMock())
        attack = "1'); DROP TABLE inverter_metrics; --"
        with patch.dict(sys.modules, {'asyncpg': SimpleNamespace(connect=AsyncMock(return_value=conn))}):
            await web_queries.charts_inverter('test', attack)
        sql, arg = conn.fetch.await_args.args
        self.assertNotIn(attack, sql)
        self.assertEqual(arg, attack)


class ReadOnlyRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.routes = {r.path: r.endpoint for r in web_routes.create_auth_router(None, None).routes}

    async def test_authentication_and_empty_response_contracts(self):
        paths = {
            '/api/energy/daily': ('sqlite_energy_daily', {'today', 'week'}),
            '/api/energy/daily/balance': ('energy_daily_balance', {'days'}),
            '/api/inverter/metrics': ('inverter_metrics', {'rows'}),
            '/api/energy/devices/today': ('energy_devices_today', {'devices', 'interval_minutes'}),
            '/api/energy/meter/daily': ('energy_meter_daily', {'days'}),
            '/api/charts/inverter': ('charts_inverter', {'data'}),
            '/api/charts/hourly-load': ('charts_hourly_load', {'data'}),
            '/api/charts/weather': ('charts_weather', {'data'}),
        }
        for path, (method, keys) in paths.items():
            with self.subTest(path=path), patch.object(web_routes, '_check_auth', return_value=(False, None)):
                self.assertEqual((await self.routes[path](object())).status_code, 401)
            with self.subTest(path=path), patch.object(web_routes, '_check_auth', return_value=(True, 'user')), \
                 patch.dict(os.environ, {'DATABASE_URL': 'synthetic'}), patch.object(web_queries, method, new=AsyncMock(return_value=[])):
                response = await self.routes[path](object())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(json.loads(response.body)), keys)

    async def test_populated_response_contracts(self):
        stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
        cases = [
            ('/api/energy/daily', 'sqlite_energy_daily', [('2026-01-02', 10, 2, 3, 4, 5)], 'week', {'day', 'pv_wh', 'battery_wh', 'solar_total_wh', 'grid_wh', 'charge_wh', 'load_wh'}),
            ('/api/energy/daily/balance', 'energy_daily_balance', [{'day': stamp.date(), 'pv_kwh': 1, 'grid_kwh': 2, 'battery_load_kwh': 3, 'total_load_kwh': 4, 'self_pct': 5}], 'days', {'day', 'pv_kwh', 'grid_kwh', 'battery_load_kwh', 'total_load_kwh', 'self_pct'}),
            ('/api/inverter/metrics', 'inverter_metrics', [{'time': stamp, 'working_mode': 'Line Mode', 'pv_power_w': 1, 'battery_voltage': 2, 'battery_soc': 3, 'battery_current_dis': 4, 'output_power_w': 5, 'total_load_w': 6, 'ac_output_load_pct': 7}], 'rows', {'time', 'mode', 'pv_w', 'batt_v', 'batt_soc', 'batt_dis', 'output_w', 'load_w', 'load_pct'}),
            ('/api/energy/devices/today', 'energy_devices_today', [{'device_name': 'lamp', 'delta_kwh': 1, 'real_wh': 2, 'on_hours': 3, 'on_count': 4, 'avg_power_w': 5, 'has_energy_meter': True}], 'devices', {'device_name', 'on_hours', 'on_count', 'avg_power_w', 'real_wh', 'has_energy_meter'}),
            ('/api/energy/meter/daily', 'energy_meter_daily', [{'day': stamp.date(), 'device_name': 'lamp', 'consumed_kwh': 1, 'end_kwh': 2, 'samples': 3}], 'days', {'day', 'device_name', 'consumed_kwh', 'end_kwh', 'samples'}),
            ('/api/charts/inverter', 'charts_inverter', [{'time': stamp, 'pv_power_w': 1, 'battery_voltage': 2, 'output_power_w': 3, 'working_mode': 'Line Mode'}], 'data', {'time', 'pv_power_w', 'battery_voltage', 'output_power_w', 'working_mode'}),
            ('/api/charts/hourly-load', 'charts_hourly_load', [{'hour': stamp, 'avg_power_w': 1, 'device_count': 2}], 'data', {'hour', 'avg_power_w', 'device_count'}),
            ('/api/charts/weather', 'charts_weather', [{'time': stamp, 'ambient_temp': 1, 'humidity': 2}], 'data', {'time', 'temp', 'humidity'}),
        ]
        for path, method, rows, key, fields in cases:
            with self.subTest(path=path), patch.object(web_routes, '_check_auth', return_value=(True, 'user')), \
                 patch.dict(os.environ, {'DATABASE_URL': 'synthetic'}), patch.object(web_queries, method, new=AsyncMock(return_value=rows)):
                response = await self.routes[path](object())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(json.loads(response.body)[key][0]), fields)

    async def test_database_failures_are_mapped_to_existing_statuses(self):
        with patch.object(web_routes, '_check_auth', return_value=(True, 'user')), patch.dict(os.environ, {'DATABASE_URL': 'synthetic'}):
            with patch.object(web_queries, 'sqlite_energy_daily', new=AsyncMock(side_effect=FileNotFoundError())):
                self.assertEqual((await self.routes['/api/energy/daily'](object())).status_code, 503)
            with patch.object(web_queries, 'sqlite_energy_daily', new=AsyncMock(side_effect=sqlite3.OperationalError())):
                self.assertEqual((await self.routes['/api/energy/daily'](object())).status_code, 500)
            with patch.object(web_queries, 'inverter_metrics', new=AsyncMock(side_effect=ConnectionError())):
                self.assertEqual((await self.routes['/api/inverter/metrics'](object())).status_code, 500)
            with patch.object(web_queries, 'decision_journal', new=AsyncMock(side_effect=ConnectionError())):
                response = await self.routes['/api/controls/decisions'](object())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.body)['decisions'], [])


@unittest.skipUnless(os.environ.get('WEB_QUERY_LOCAL_POSTGRES') == '1', 'requires disposable localhost PostgreSQL')
class LocalPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_queries_execute_with_synthetic_postgres_tables(self):
        import asyncpg
        dsn = 'postgresql://postgres:synthetic-controls-performance@127.0.0.1:55439/controls_perf_test'
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("""
                CREATE TABLE adaptive_decision_journal (evaluated_at timestamptz, entry text);
                INSERT INTO adaptive_decision_journal VALUES (now(), '{"decision":"INSUFFICIENT_DATA"}');
                CREATE TABLE daily_energy_balance (day date, pv_kwh numeric, grid_kwh numeric, battery_load_kwh numeric, total_load_kwh numeric, self_pct numeric);
                INSERT INTO daily_energy_balance VALUES (current_date, 1, 2, 3, 4, 5);
                CREATE TABLE device_metrics (time timestamptz, device_name text, is_on boolean, power_watts numeric);
                INSERT INTO device_metrics VALUES (now(), 'lamp', true, 50);
                CREATE TABLE device_energy_counters (time timestamptz, device_name text, add_ele_kwh numeric);
                INSERT INTO device_energy_counters VALUES (now(), 'lamp', 1);
                CREATE TABLE device_daily_energy (day timestamptz, device_name text, consumed_kwh numeric, end_kwh numeric, samples integer);
                INSERT INTO device_daily_energy VALUES (now(), 'lamp', 1, 2, 3);
                CREATE TABLE weather_data (time timestamptz, ambient_temp numeric, humidity numeric);
                INSERT INTO weather_data VALUES (now(), 20, 60);
                CREATE FUNCTION time_bucket(interval, timestamptz) RETURNS timestamptz
                    LANGUAGE SQL IMMUTABLE AS 'SELECT date_trunc(''hour'', $2)';
                ALTER TABLE inverter_metrics ADD COLUMN total_load_w numeric;
            """)
        finally:
            await conn.close()
        try:
            calls = [(web_queries.decision_journal, (dsn, 1)), (web_queries.energy_daily_balance, (dsn, 1)),
                     (web_queries.inverter_metrics, (dsn,)), (web_queries.energy_devices_today, (dsn,)),
                     (web_queries.energy_meter_daily, (dsn, '1')), (web_queries.charts_inverter, (dsn, '24')),
                     (web_queries.charts_hourly_load, (dsn, '24')), (web_queries.charts_weather, (dsn, '24'))]
            for query, args in calls:
                with self.subTest(query=query.__name__):
                    self.assertTrue(await query(*args))
        finally:
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute("""
                    DROP TABLE adaptive_decision_journal, daily_energy_balance, device_metrics,
                        device_energy_counters, device_daily_energy, weather_data;
                    DROP FUNCTION time_bucket(interval, timestamptz);
                    ALTER TABLE inverter_metrics DROP COLUMN total_load_w;
                """)
            finally:
                await conn.close()

    async def test_real_history_query_and_cleanup_after_server_error_and_timeout(self):
        import asyncpg
        dsn = 'postgresql://postgres:synthetic-controls-performance@127.0.0.1:55439/controls_perf_test'
        conn = await asyncpg.connect(dsn)
        try:
            as_of = await conn.fetchval("SELECT max(time) + INTERVAL '1 minute' FROM inverter_metrics")
        finally:
            await conn.close()
        from datetime import timedelta
        rows = await web_queries.charge_history(dsn, as_of - timedelta(days=1), as_of)
        self.assertEqual(sum(not row['is_context'] for row in rows), 1440)
        self.assertEqual(sum(row['is_context'] for row in rows), 1)
        self.assertTrue(all(row['verified_since'] == as_of - timedelta(days=7) for row in rows))
        self.assertIn('charger_priority_raw', rows[0])
        self.assertIn('ac_input_voltage', rows[0])
        first = as_of - timedelta(days=7)
        empty = await web_queries.charge_history(dsn, first - timedelta(hours=2), first - timedelta(hours=1))
        self.assertEqual(len(empty), 1)
        self.assertIsNone(empty[0]['time'])
        self.assertEqual(empty[0]['verified_since'], first)
        with self.assertRaises(asyncpg.PostgresError):
            await web_queries._fetch(dsn, 'SELECT missing_column FROM inverter_metrics')
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(web_queries._fetch(dsn, 'SELECT pg_sleep(3)'), timeout=0.05)
        conn = await asyncpg.connect(dsn)
        try:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname = 'controls_perf_test' AND pid <> pg_backend_pid()"), 0)
        finally:
            await conn.close()
