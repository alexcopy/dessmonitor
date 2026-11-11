"""Negative tests for tests/pg_isolation.py: unsafe targets are refused BEFORE any destructive SQL.

The static, fake-connection and per-suite tests always run and never open a socket
(asyncpg.connect/create_pool are replaced by mocks that fail the test if called).
The live tests need PG_ISOLATION_LIVE=1 under ``tests/disposable_postgres.py run --``.
"""
import asyncio
import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pg_isolation
from pg_isolation import UnsafeTestDatabase

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = '0123456789abcdef'
OTHER_RUN = 'fedcba9876543210'
PORT = 61234
SAFE_DSN = 'postgresql://postgres@127.0.0.1:%d/dm_disposable_%s' % (PORT, RUN_ID)
TOKEN = 'ab' * 32
PASSWORD = 'synthetic-password-0123456789'
STARTED = 'Wed Nov 11 10:00:00 2026'
# The DSNs that previously defaulted in the test suite (incident: easun-test-pg was reset).
INCIDENT_DSNS = (
    'postgresql://postgres:synthetic-easun-test@127.0.0.1:55439/easun_test',
    'postgresql://postgres:synthetic@127.0.0.1:55440/easun_test',
    'postgresql://postgres:synthetic@127.0.0.1:55440/dessmonitor_test',
    'postgresql://postgres:synthetic-controls-performance@127.0.0.1:55439/controls_perf_test',
)
OPT_IN_FLAGS = {flag: '1' for flag in ('EASUN_TEST_POSTGRES', 'WEB_QUERY_LOCAL_POSTGRES', 'DECISION_LOG_LOCAL_POSTGRES',
                                       'BATTERY_GRID_LOCAL_POSTGRES', 'DECISION_PAGINATION_LOCAL_POSTGRES',
                                       'JOURNAL_V2_LOCAL_POSTGRES')}


def env(dsn=SAFE_DSN, run_id=RUN_ID, capability=None):
    values = {}
    if dsn is not None:
        values[pg_isolation.DSN_ENV] = dsn
    if run_id is not None:
        values[pg_isolation.RUN_ID_ENV] = run_id
    if capability is not None:
        values[pg_isolation.CAPABILITY_ENV] = capability
    return values


def capability(**overrides):
    fields = dict(run_id=RUN_ID, dsn=SAFE_DSN, password=PASSWORD, token=TOKEN, provisioner_pid=os.getpid(),
                  provisioner_started=STARTED, container='dm-disposable-pg-' + RUN_ID, port=PORT)
    fields.update(overrides)
    return pg_isolation.Capability(**fields)


def container_info(cap, **overrides):
    info = {'Name': '/' + cap.container,
            'Config': {'Labels': {pg_isolation.RUN_LABEL: cap.run_id, pg_isolation.OWNER_LABEL: cap.digest}},
            'State': {'Running': True},
            'HostConfig': {'PortBindings': {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(cap.port)}]}}}
    for key, value in overrides.items():
        info[key] = value
    return info


class Provisioned:
    """A synthetic provisioned run: private capability file plus faked process tree and Docker.

    The process tree is ``this process -> pid 1`` and the provisioner is this process unless
    ``unrelated=True`` (then the provisioner is a pid outside the tree, as for a process that
    copied another run's environment and capability path).
    """

    def __init__(self, test, cap=None, *, unrelated=False, started=STARTED, docker=None, mode=0o600, dir_mode=0o700,
                 raw=None):
        self.cap = cap or capability(provisioner_pid=424242 if unrelated else os.getpid())
        self.directory = tempfile.mkdtemp(prefix='dm-cap-test-')
        test.addCleanup(shutil.rmtree, self.directory, True)
        os.chmod(self.directory, dir_mode)
        self.path = os.path.join(self.directory, 'capability.json')
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(self.cap.to_json() if raw is None else raw)
        os.chmod(self.path, mode)
        provisioner = 424242 if unrelated else os.getpid()
        tree = {os.getpid(): (1, started if provisioner == os.getpid() else 'Thu Jan  1 00:00:00 2026')}
        if unrelated:
            tree[provisioner] = (1, STARTED)  # the real provisioner is alive, but not our ancestor
        self.process_info = tree.get
        self.docker = (lambda name: container_info(self.cap)) if docker is None else docker
        self.env = env(self.cap.dsn, self.cap.run_id, self.path)
        test.addCleanup(pg_isolation._AUTHORIZED.clear)
        pg_isolation._AUTHORIZED.clear()

    @contextlib.contextmanager
    def active(self, clear=True):
        with patch.dict(os.environ, self.env, clear=clear), \
             patch.object(pg_isolation, '_process_info', self.process_info), \
             patch.object(pg_isolation, '_docker_inspect', self.docker):
            yield self.cap


class NoNetwork:
    """asyncpg entry points and the socket layer are replaced; any call fails the test."""

    def __enter__(self):
        self.connect, self.create_pool = AsyncMock(), AsyncMock()
        self.socket_connect = MagicMock(side_effect=AssertionError('network connection attempted'))
        self.loop_connect = AsyncMock(side_effect=AssertionError('network connection attempted'))
        self._stack = contextlib.ExitStack()
        for target, value in (('asyncpg.connect', self.connect), ('asyncpg.create_pool', self.create_pool),
                              ('socket.socket.connect', self.socket_connect),
                              ('asyncio.base_events.BaseEventLoop.create_connection', self.loop_connect)):
            self._stack.enter_context(patch(target, value))
        return self

    def __exit__(self, *exc):
        self._stack.close()

    def assert_untouched(self, test):
        test.assertEqual((self.connect.await_count, self.create_pool.await_count, self.socket_connect.call_count,
                          self.loop_connect.await_count), (0, 0, 0, 0))


class StaticValidationTests(unittest.TestCase):
    def assert_rejected(self, dsn, run_id=RUN_ID, message=None):
        with self.assertRaises(UnsafeTestDatabase) as raised:
            pg_isolation.disposable_dsn(dsn, env(dsn, run_id))
        if message:
            self.assertIn(message, str(raised.exception))
        self.assertNotIn('synthetic', str(raised.exception))  # the DSN / password is never echoed

    def test_provisioned_dsn_is_accepted(self):
        self.assertEqual(pg_isolation.disposable_dsn(SAFE_DSN, env()), SAFE_DSN)
        ipv6 = 'postgresql://postgres@[::1]:61234/dm_disposable_%s' % RUN_ID
        self.assertEqual(pg_isolation.disposable_dsn(ipv6, env(ipv6)), ipv6)

    def test_missing_dsn_or_run_id_is_rejected(self):
        with self.assertRaisesRegex(UnsafeTestDatabase, 'not set'):
            pg_isolation.disposable_dsn(None, {})
        with self.assertRaisesRegex(UnsafeTestDatabase, 'not set'):
            pg_isolation.disposable_dsn('', env(''))
        for run_id in (None, '', 'abc', RUN_ID.upper(), RUN_ID + '0', "x'; DROP"):
            with self.subTest(run_id=run_id):
                self.assert_rejected(SAFE_DSN, run_id, pg_isolation.RUN_ID_ENV)

    def test_dsn_must_be_the_provisioned_one(self):
        with self.assertRaisesRegex(UnsafeTestDatabase, 'differs'):
            pg_isolation.disposable_dsn(SAFE_DSN.replace('61234', '61235'), env())

    def test_dsn_must_not_carry_credentials(self):
        self.assert_rejected('postgresql://postgres:synthetic@127.0.0.1:61234/dm_disposable_%s' % RUN_ID,
                             message='password')

    def test_incident_and_reserved_targets_are_rejected(self):
        for dsn in INCIDENT_DSNS:
            with self.subTest(dsn=dsn.rsplit('@', 1)[1]):
                self.assert_rejected(dsn, message='reserved')
                self.assert_rejected(dsn, run_id=None)
        for database in ('easun_test', 'dessmonitor_test', 'controls_perf_test', 'postgres', 'template1'):
            with self.subTest(database=database):
                self.assert_rejected('postgresql://postgres@127.0.0.1:61234/%s' % database, message='reserved')
        for port in (5432, 55439, 55440):
            with self.subTest(port=port):
                self.assert_rejected(SAFE_DSN.replace('61234', str(port)), message='reserved')

    def test_test_suffix_is_not_proof_of_isolation(self):
        for database in ('my_test', 'dm_disposable_test', 'dm_disposable_' + OTHER_RUN, 'dm_disposable_%s_test' % RUN_ID):
            with self.subTest(database=database):
                self.assert_rejected('postgresql://postgres@127.0.0.1:61234/%s' % database, message='run')

    def test_non_loopback_ambiguous_or_overridable_endpoints_are_rejected(self):
        tail = '/dm_disposable_%s' % RUN_ID
        for dsn in ('postgresql://postgres@localhost:61234' + tail,
                    'postgresql://postgres@db.internal:61234' + tail,
                    'postgresql://postgres@10.0.0.5:61234' + tail,
                    'postgresql://postgres@127.0.0.2:61234' + tail,
                    'postgresql://postgres@127.0.0.1:61234,127.0.0.1:55439' + tail,
                    'postgresql://postgres@127.0.0.1' + tail,
                    'postgresql://127.0.0.1:61234' + tail,
                    SAFE_DSN + '?host=127.0.0.1&port=55439',
                    SAFE_DSN + '?dbname=easun_test',
                    SAFE_DSN + '?passfile=/tmp/x',
                    'postgresql:///dm_disposable_%s?host=/var/run/postgresql' % RUN_ID,
                    'mysql://postgres@127.0.0.1:61234' + tail,
                    'postgresql://postgres@127.0.0.1:notaport' + tail):
            with self.subTest(dsn=dsn.split('@')[-1]):
                self.assert_rejected(dsn)


class ConnectionOptionTests(unittest.IsolatedAsyncioTestCase):
    """B-01: a validated DSN cannot be redirected or re-authenticated through asyncpg options."""

    FORBIDDEN = {
        'host': '127.0.0.1', 'port': 55439, 'database': 'easun_test',
        'user': 'postgres', 'password': 'synthetic', 'passfile': '/tmp/pgpass',
        'server_settings': {pg_isolation.RUN_ID_SETTING: RUN_ID}, 'connection_class': object,
        'record_class': object, 'ssl': False, 'direct_tls': True, 'target_session_attrs': 'any',
        'krbsrvname': 'x', 'gsslib': 'gssapi', 'loop': None, 'service': 'shared', 'options': '-c x=y',
        'unknown_option': 1,
    }
    POOL_ONLY_FORBIDDEN = {'connect': AsyncMock(), 'setup': AsyncMock(), 'reset': AsyncMock()}
    INVALID_VALUES = ({'timeout': -1}, {'timeout': True}, {'command_timeout': 'x'}, {'statement_cache_size': -1})
    INVALID_POOL_VALUES = ({'min_size': True}, {'max_size': 0}, {'max_queries': 0.5}, {'init': 'not callable'},
                           {'max_inactive_connection_lifetime': -1})

    async def assert_refused(self, call, pattern=None):
        with NoNetwork() as network:
            with self.assertRaises(UnsafeTestDatabase) as raised:
                await call()
            network.assert_untouched(self)
        if pattern:
            self.assertRegex(str(raised.exception), pattern)
        self.assertNotIn('synthetic', str(raised.exception))

    async def test_forbidden_options_never_reach_the_network(self):
        with Provisioned(self).active():
            for name, value in self.FORBIDDEN.items():
                with self.subTest(option=name):
                    await self.assert_refused(lambda: pg_isolation.connect(**{name: value}), repr(name))
                    await self.assert_refused(lambda: pg_isolation.create_pool(**{name: value}), repr(name))
            for name, value in self.POOL_ONLY_FORBIDDEN.items():
                with self.subTest(pool_option=name):
                    await self.assert_refused(lambda: pg_isolation.create_pool(**{name: value}), repr(name))
                    await self.assert_refused(lambda: pg_isolation.connect(**{name: value}), repr(name))
            for options in self.INVALID_VALUES + self.INVALID_POOL_VALUES:
                with self.subTest(invalid=options):
                    await self.assert_refused(lambda: pg_isolation.create_pool(**options), 'invalid value')
            for options in self.INVALID_POOL_VALUES:
                with self.subTest(pool_option_on_connect=options):
                    await self.assert_refused(lambda: pg_isolation.connect(**options), 'allowlist')

    async def test_ambiguous_positional_and_keyword_combinations_are_refused(self):
        with Provisioned(self).active():
            await self.assert_refused(lambda: pg_isolation.connect(SAFE_DSN, dsn=SAFE_DSN), 'ambiguous')
            await self.assert_refused(lambda: pg_isolation.connect(SAFE_DSN, 'extra'), 'positionally')
            await self.assert_refused(lambda: pg_isolation.create_pool(SAFE_DSN, dsn=SAFE_DSN), 'ambiguous')
            await self.assert_refused(lambda: pg_isolation.create_pool(SAFE_DSN, 10), 'positionally')
            await self.assert_refused(lambda: pg_isolation.connect(INCIDENT_DSNS[0]), 'differs')

    async def test_allowed_options_pass_and_the_guard_alone_sets_credentials(self):
        with Provisioned(self).active() as cap, NoNetwork() as network:
            network.connect.return_value = FakeConnection(cap)
            conn = await pg_isolation.connect(SAFE_DSN, timeout=5, command_timeout=10, statement_cache_size=0)
            self.assertIsNotNone(conn)
            args, kwargs = network.connect.await_args
            self.assertEqual(args, (SAFE_DSN,))
            self.assertEqual(kwargs, {'timeout': 5, 'command_timeout': 10, 'statement_cache_size': 0,
                                      'password': PASSWORD, 'server_settings': {'application_name': cap.tag}})
            network.create_pool.return_value = 'pool'
            await pg_isolation.create_pool(dsn=SAFE_DSN, min_size=1, max_size=2, max_queries=10,
                                           max_inactive_connection_lifetime=1.5, timeout=3)
            args, kwargs = network.create_pool.await_args
            self.assertEqual(args, (SAFE_DSN,))
            self.assertEqual(set(kwargs), {'init', 'min_size', 'max_size', 'max_queries',
                                           'max_inactive_connection_lifetime', 'timeout', 'password',
                                           'server_settings'})
            self.assertEqual(network.socket_connect.call_count + network.loop_connect.await_count, 0)


class CapabilityTests(unittest.TestCase):
    """B-02: a matching DSN and run id are not ownership; the provisioner's capability is required."""

    def assert_refused(self, provisioned, pattern, clear=True):
        with provisioned.active(clear=clear), NoNetwork() as network:
            with self.assertRaisesRegex(UnsafeTestDatabase, pattern) as raised:
                pg_isolation.authorize()
            for call in (pg_isolation.connect(), pg_isolation.create_pool()):
                with self.assertRaises(UnsafeTestDatabase):
                    asyncio.run(call)
            network.assert_untouched(self)
        for secret in (TOKEN, PASSWORD, provisioned.path):
            self.assertNotIn(secret, str(raised.exception))

    def test_provisioned_child_is_authorized(self):
        provisioned = Provisioned(self)
        with provisioned.active() as cap:
            self.assertEqual(pg_isolation.authorize().digest, cap.digest)
            self.assertNotIn(TOKEN, repr(pg_isolation.authorize()))

    def test_copied_dsn_and_run_id_without_capability_are_refused(self):
        provisioned = Provisioned(self)
        provisioned.env.pop(pg_isolation.CAPABILITY_ENV)
        self.assert_refused(provisioned, 'CAPABILITY is not set')

    def test_copied_identity_in_an_unrelated_process_is_refused(self):
        # DSN, run id AND the capability path copied, but the process is outside the provisioner's tree.
        self.assert_refused(Provisioned(self, unrelated=True), 'not the provisioner or one of its child')

    def test_provisioner_pid_reuse_is_refused(self):
        self.assert_refused(Provisioned(self, started='Fri Jan  2 00:00:00 2026'), 'pid reused')

    def test_capability_file_must_be_private_and_genuine(self):
        self.assert_refused(Provisioned(self, mode=0o644), 'private')
        self.assert_refused(Provisioned(self, dir_mode=0o755), 'private')
        self.assert_refused(Provisioned(self, raw='not json'), 'malformed')
        self.assert_refused(Provisioned(self, raw='{"version": 2}'), 'version')
        relative = Provisioned(self)
        relative.env[pg_isolation.CAPABILITY_ENV] = 'capability.json'
        self.assert_refused(relative, 'absolute')
        missing = Provisioned(self)
        missing.env[pg_isolation.CAPABILITY_ENV] = missing.path + '.missing'
        self.assert_refused(missing, 'missing')
        linked = Provisioned(self)
        link = os.path.join(linked.directory, 'link.json')
        os.symlink(linked.path, link)
        linked.env[pg_isolation.CAPABILITY_ENV] = link
        self.assert_refused(linked, 'missing or unreadable')

    def test_capability_of_another_run_or_endpoint_is_refused(self):
        other_dsn = 'postgresql://postgres@127.0.0.1:61234/dm_disposable_' + OTHER_RUN
        other = Provisioned(self, capability(run_id=OTHER_RUN, dsn=other_dsn, container='dm-disposable-pg-' + OTHER_RUN))
        other.env = env(SAFE_DSN, RUN_ID, other.path)  # this run's DSN with another run's capability
        self.assert_refused(other, 'different run')
        self.assert_refused(Provisioned(self, capability(token='zz' * 32)), 'token')
        self.assert_refused(Provisioned(self, capability(password='short')), 'credentials')
        self.assert_refused(Provisioned(self, capability(port=61235)), 'container does not match')
        self.assert_refused(Provisioned(self, capability(container='easun-test-pg')), 'container does not match')

    def test_independent_competing_capability_is_refused_by_the_container(self):
        # A competing runner wrote its own capability (own token) for this run's DSN and container.
        competitor = Provisioned(self, capability(token='cd' * 32))
        genuine = capability()
        competitor.docker = lambda name: container_info(genuine)
        self.assert_refused(competitor, 'not created for this capability')

    def test_container_identity_is_required(self):
        cap = capability()
        for name, info, pattern in (
                ('missing', None, 'does not exist'),
                ('other run label', container_info(cap, Config={'Labels': {pg_isolation.RUN_LABEL: OTHER_RUN,
                                                                           pg_isolation.OWNER_LABEL: cap.digest}}),
                 'run label'),
                ('stopped', container_info(cap, State={'Running': False}), 'not running'),
                ('public binding', container_info(cap, HostConfig={'PortBindings': {'5432/tcp': [
                    {'HostIp': '0.0.0.0', 'HostPort': str(PORT)}]}}), 'bound only'),
                ('shared container name', container_info(cap, Name='/easun-test-pg'), 'run label')):
            with self.subTest(name):
                self.assert_refused(Provisioned(self, docker=lambda _name, info=info: info), pattern)


class FakeConnection:
    def __init__(self, cap=None, database='dm_disposable_' + RUN_ID, run_setting=RUN_ID, opts=None,
                 marker=None, owners=None, foreign_sessions=0, databases=None):
        cap = cap or capability()
        opts = '/usr/local/bin/postgres "-c" "%s=%s" "-c" "fsync=off"\n' % (pg_isolation.RUN_ID_SETTING, RUN_ID) \
            if opts is None else opts
        self.row = {'database': database, 'run_setting': run_setting, 'postmaster_opts': opts,
                    'marker': pg_isolation.database_marker(RUN_ID) if marker is None else marker}
        self.owners = [{'run_id': RUN_ID, 'token_sha256': cap.digest, 'provisioner_pid': cap.provisioner_pid,
                        'provisioner_started': cap.provisioner_started}] if owners is None else owners
        self.foreign_sessions = foreign_sessions
        self.databases = databases or ['postgres', 'template0', 'template1', 'dm_disposable_' + RUN_ID]
        self.executed = []
        self.closed = False

    async def fetchrow(self, sql, *args):
        self.assert_read_only(sql)
        return self.row

    async def fetch(self, sql, *args):
        self.assert_read_only(sql)
        if isinstance(self.owners, Exception):
            raise self.owners
        return self.owners

    async def fetchval(self, sql, *args):
        self.assert_read_only(sql)
        return self.foreign_sessions if 'pg_stat_activity' in sql else self.databases

    async def execute(self, sql, *args):  # pragma: no cover - must never be reached
        self.executed.append(sql)

    async def close(self):
        self.closed = True

    @staticmethod
    def assert_read_only(sql):
        assert sql.lstrip().upper().startswith('SELECT'), sql


class ConnectionVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_provisioned_server_database_and_owner_pass(self):
        self.assertTrue(await pg_isolation.verify_connection(FakeConnection(), capability()))

    async def test_wrong_identity_forged_markers_and_competing_runners_are_rejected_without_writes(self):
        cap = capability()
        genuine_owner = FakeConnection().owners[0]
        cases = {
            'connected database': (FakeConnection(database='easun_test'), 'connected database'),
            'run setting missing': (FakeConnection(run_setting=None), 'not started for this run'),
            'other run': (FakeConnection(run_setting=OTHER_RUN), 'not started for this run'),
            'run setting forged by client': (FakeConnection(opts='/usr/local/bin/postgres\n'),
                                             'not started for this run'),
            'other run on the command line': (FakeConnection(opts='postgres "-c" "%s=%s"' % (
                pg_isolation.RUN_ID_SETTING, OTHER_RUN)), 'not started for this run'),
            'marker missing': (FakeConnection(marker=''), 'marker'),
            'forged marker, no ownership record': (FakeConnection(owners=RuntimeError('relation does not exist')),
                                                   'ownership record for this run is missing'),
            'ownership of another capability': (FakeConnection(owners=[dict(genuine_owner, token_sha256='0' * 64)]),
                                                'different capability'),
            'two claims': (FakeConnection(owners=[genuine_owner, dict(genuine_owner, run_id=OTHER_RUN)]), 'ambiguous'),
            'other provisioner': (FakeConnection(owners=[dict(genuine_owner, provisioner_pid=1)]),
                                  'different provisioner'),
            'competing runner session': (FakeConnection(foreign_sessions=1), 'foreign client session'),
            'shared server': (FakeConnection(databases=['postgres', 'template0', 'template1', 'easun_test',
                                                        'dm_disposable_' + RUN_ID]), 'shared server'),
        }
        for name, (conn, pattern) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(UnsafeTestDatabase, pattern):
                    await pg_isolation.verify_connection(conn, cap)
                self.assertEqual(conn.executed, [])

    async def test_connect_closes_an_unverified_connection_and_never_returns_it(self):
        with Provisioned(self).active() as cap:
            conn = FakeConnection(cap, foreign_sessions=2)
            with patch('asyncpg.connect', AsyncMock(return_value=conn)):
                with self.assertRaises(UnsafeTestDatabase):
                    await pg_isolation.connect(SAFE_DSN)
        self.assertTrue(conn.closed)
        self.assertEqual(conn.executed, [])

    async def test_pool_verifies_every_new_connection_before_caller_init(self):
        created = AsyncMock(return_value='pool')
        caller_init = AsyncMock()
        with Provisioned(self).active() as cap, patch('asyncpg.create_pool', created):
            self.assertEqual(await pg_isolation.create_pool(SAFE_DSN, min_size=1, init=caller_init), 'pool')
            init = created.await_args.kwargs['init']
            with self.assertRaises(UnsafeTestDatabase):
                await init(FakeConnection(cap, marker='someone else'))
            caller_init.assert_not_awaited()
            await init(FakeConnection(cap))
            caller_init.assert_awaited_once()

    async def test_unsafe_targets_never_reach_asyncpg(self):
        with NoNetwork() as network:
            for values in ({}, env(INCIDENT_DSNS[0]), env(INCIDENT_DSNS[1], None), env()):
                with self.subTest(values=sorted(values)), patch.dict(os.environ, values, clear=True):
                    with self.assertRaises(UnsafeTestDatabase):
                        await pg_isolation.connect()
                    with self.assertRaises(UnsafeTestDatabase):
                        await pg_isolation.create_pool()
            network.assert_untouched(self)

    async def test_app_connections_route_raw_asyncpg_connect_through_the_guard(self):
        import asyncpg
        with Provisioned(self).active() as cap, NoNetwork() as network:
            network.connect.return_value = FakeConnection(cap)
            with pg_isolation.app_connections() as dsn:
                self.assertEqual(os.environ['DATABASE_URL'], SAFE_DSN)
                await asyncpg.connect(dsn, command_timeout=10)
                with self.assertRaisesRegex(UnsafeTestDatabase, 'forbidden'):
                    await asyncpg.connect(dsn, host='127.0.0.1')
                with self.assertRaisesRegex(UnsafeTestDatabase, 'differs'):
                    await asyncpg.connect(INCIDENT_DSNS[0])
            self.assertEqual(network.connect.await_count, 1)
            self.assertEqual(network.connect.await_args.kwargs['server_settings'], {'application_name': cap.tag})


# (module, attribute holding the module-level DSN or None, class, method run before any SQL)
SUITES = (
    ('test_adaptive_journal_v2', 'DSN', 'JournalV2PostgresTests', 'asyncSetUp'),
    ('test_online_output_learning', 'DSN', 'DiagnosticJournalPostgresTests', 'asyncSetUp'),
    ('test_online_output_learning', 'DSN', 'ReplayJournalPostgresTests', 'asyncSetUp'),
    ('test_online_output_learning', 'DSN', 'HistoryTruncationPostgresTests', 'asyncSetUp'),
    ('test_online_output_learning', 'DSN', 'ModeGapContinuityPostgresTests', 'asyncSetUp'),
    ('test_easun_postgres_integration', 'DSN', 'PostgresIntegrationTests', 'asyncSetUp'),
    ('test_decision_log_battery_voltage', '_PG_DSN', 'DecisionLogVoltagePostgresTests', 'asyncSetUp'),
    ('test_battery_blocker_regressions', '_PG_DSN', 'GridPageSizeIndependencePostgresTests', 'asyncSetUp'),
    ('test_adaptive_decision_pagination', None, 'DecisionPaginationPostgresTests',
     'test_real_cursor_export_over_ten_thousand'),
    ('test_web_query_architecture', None, 'LocalPostgresTests', 'asyncSetUp'),
    ('test_web_query_architecture', None, 'LocalPostgresTests', 'test_real_history_query_and_cleanup_after_server_error_and_timeout'),
)


class SuitesRefuseUnsafeTargetsTests(unittest.TestCase):
    """Each real PostgreSQL suite's first step fails closed for unsafe targets, before connecting."""

    def scenarios(self):
        free_port_wrong_name = 'postgresql://postgres@127.0.0.1:61234/dessmonitor_test'
        return {'no DSN configured': ({}, ''),
                'incident DSN, no run id': (env(INCIDENT_DSNS[0], None), INCIDENT_DSNS[0]),
                'incident DSN with run id': (env(INCIDENT_DSNS[1]), INCIDENT_DSNS[1]),
                'reserved database on a free port': (env(free_port_wrong_name), free_port_wrong_name),
                'hard-coded DSN differs from provisioned': (env(), INCIDENT_DSNS[2]),
                'copied DSN and run id, no capability': (env(), SAFE_DSN)}

    def test_every_postgres_suite_rejects_before_any_connection(self):
        connect, create_pool = AsyncMock(), AsyncMock()
        for module_name, attribute, class_name, method in SUITES:
            module = importlib.import_module(module_name)
            for scenario, (values, module_dsn) in self.scenarios().items():
                if attribute is None and module_dsn not in ('', values.get(pg_isolation.DSN_ENV)):
                    continue  # reads the DSN from the environment only; no module-level DSN to hard-code
                with self.subTest(suite='%s.%s.%s' % (module_name, class_name, method), scenario=scenario), \
                     patch.dict(os.environ, dict(OPT_IN_FLAGS, **values), clear=True), \
                     patch('asyncpg.connect', connect), patch('asyncpg.create_pool', create_pool):
                    with patch.object(module, attribute, module_dsn) if attribute else contextlib.nullcontext():
                        case = getattr(module, class_name)(method)
                        with self.assertRaises(UnsafeTestDatabase):
                            asyncio.run(getattr(case, method)())
        connect.assert_not_awaited()
        create_pool.assert_not_awaited()

    def test_benchmarks_reject_before_any_connection(self):
        import benchmark_adaptive_journal_v2_storage as journal_bench
        import benchmark_controls_history as controls_bench
        connect, create_pool = AsyncMock(), AsyncMock()
        for scenario, (values, module_dsn) in self.scenarios().items():
            with self.subTest(scenario=scenario), patch.dict(os.environ, values, clear=True), \
                 patch('asyncpg.connect', connect), patch('asyncpg.create_pool', create_pool), \
                 patch.object(journal_bench, 'DSN', module_dsn), patch.object(controls_bench, 'DSN', module_dsn):
                for call in (journal_bench.main(), journal_bench.run('v1', 'none'), controls_bench.seed_database()):
                    with self.assertRaises(UnsafeTestDatabase):
                        asyncio.run(call)
        connect.assert_not_awaited()
        create_pool.assert_not_awaited()


class NoHardCodedDsnTests(unittest.TestCase):
    """Source scan: no DSN literal or DSN default can reintroduce a silent fallback."""
    ALLOWED = {'test_pg_isolation.py', 'pg_isolation.py', 'disposable_postgres.py'}

    def sources(self):
        for directory in ('tests', 'scripts'):
            for path in sorted((ROOT / directory).glob('*.py')):
                if path.name not in self.ALLOWED:
                    yield path, path.read_text()

    def test_no_credentialed_dsn_literals_or_defaults(self):
        literal = re.compile(r'postgres(?:ql)?://[^\s\'"]*@')
        # Any read of a DSN-like variable (get/[]/getenv) outside the guard could become a fallback.
        default = re.compile(r'(?:environ\.get\(|environ\[|getenv\()\s*[\'"][A-Z_]*(?:DSN|DATABASE_URL|PGHOST|PGDATABASE)')
        offenders = [str(path.relative_to(ROOT)) for path, text in self.sources()
                     if literal.search(text) or (path.parent.name == 'tests' and default.search(text))]
        self.assertEqual(offenders, [])

    def test_tests_open_connections_only_through_the_guard(self):
        direct = re.compile(r'asyncpg\.(?:connect|create_pool)\(')
        offenders = [str(path.relative_to(ROOT)) for path, text in self.sources()
                     if path.parent.name == 'tests' and direct.search(text)]
        self.assertEqual(offenders, [])


UNRELATED_PROCESS = r"""
import json, os, sys, time
result_path = sys.argv[2]
if os.fork():
    os._exit(0)  # the intermediate parent exits; the grandchild is re-parented outside the provisioner's tree
deadline = time.time() + 10
while os.getppid() != 1 and time.time() < deadline:
    time.sleep(0.05)
sys.path.insert(0, sys.argv[1])
import pg_isolation
try:
    pg_isolation.authorize()
    outcome = {'authorized': True}
except pg_isolation.UnsafeTestDatabase as error:
    outcome = {'authorized': False, 'reason': str(error)}
outcome['ppid'] = os.getppid()
with open(result_path, 'w') as handle:
    json.dump(outcome, handle)
"""


@unittest.skipUnless(os.environ.get('PG_ISOLATION_LIVE') == '1',
                     'requires PG_ISOLATION_LIVE=1 under tests/disposable_postgres.py run')
class LiveDisposableServerTests(unittest.IsolatedAsyncioTestCase):
    """Run against the server provisioned for this run; every change is undone in ``finally``."""

    async def raw(self, application_name, **server_settings):
        import asyncpg
        cap = pg_isolation.load_capability()
        return await asyncpg.connect(cap.dsn, password=cap.password, timeout=10,
                                     server_settings=dict(server_settings, application_name=application_name))

    async def test_provisioned_database_verifies(self):
        conn = await pg_isolation.connect()
        try:
            self.assertTrue(await pg_isolation.verify_connection(conn))
            self.assertEqual(await conn.fetchval("SELECT current_setting('application_name')"),
                             pg_isolation.authorize().tag)
        finally:
            await conn.close()

    async def test_shared_server_and_other_run_identities_are_rejected(self):
        dsn = pg_isolation.configured_dsn()
        admin = await pg_isolation.connect()
        try:
            await admin.execute('CREATE DATABASE easun_test')  # inside this run's own container only
            try:
                with self.assertRaisesRegex(UnsafeTestDatabase, 'shared server'):
                    await pg_isolation.connect()
            finally:
                await admin.execute('DROP DATABASE easun_test')
            await admin.execute('CREATE DATABASE dm_disposable_%s' % OTHER_RUN)
            try:
                other_dsn = dsn.rsplit('/', 1)[0] + '/dm_disposable_' + OTHER_RUN
                with patch.dict(os.environ, env(other_dsn, OTHER_RUN)):
                    # Right name pattern, but the capability belongs to this run, not that one.
                    with self.assertRaisesRegex(UnsafeTestDatabase, 'different run'):
                        await pg_isolation.connect()
                with patch.dict(os.environ, env(dsn, OTHER_RUN)):
                    with self.assertRaisesRegex(UnsafeTestDatabase, 'run id'):
                        await pg_isolation.connect()
            finally:
                await admin.execute('DROP DATABASE dm_disposable_%s' % OTHER_RUN)
        finally:
            await admin.close()
        conn = await pg_isolation.connect()
        await conn.close()

    async def test_copied_identity_in_an_unrelated_process_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            result = os.path.join(directory, 'result.json')
            subprocess.run([sys.executable, '-c', UNRELATED_PROCESS, str(ROOT / 'tests'), result], check=True,
                           timeout=30)  # same environment: DSN, run id and capability path all copied
            for _ in range(200):
                if os.path.exists(result) and os.path.getsize(result):
                    break
                await asyncio.sleep(0.05)
            with open(result) as handle:
                outcome = json.load(handle)
        self.assertFalse(outcome['authorized'], outcome)
        self.assertIn('not the provisioner or one of its child processes', outcome['reason'])

    async def test_independent_competing_session_makes_the_guard_fail_closed(self):
        competitor = await self.raw('competing-runner')
        try:
            with self.assertRaisesRegex(UnsafeTestDatabase, 'foreign client session'):
                await pg_isolation.connect()
        finally:
            await competitor.close()
        for _ in range(50):  # the competitor's backend exits asynchronously
            try:
                conn = await pg_isolation.connect()
                break
            except UnsafeTestDatabase:
                await asyncio.sleep(0.1)
        else:
            self.fail('guard still failing after the competing session closed')
        await conn.close()

    async def test_forged_run_setting_marker_and_ownership_are_rejected(self):
        cap = pg_isolation.authorize()
        other_db = 'dm_disposable_' + OTHER_RUN
        admin = await pg_isolation.connect()
        try:
            # Forge another run's identity on this server: its database, its marker comment, and the
            # run setting supplied by the client at startup. Only postmaster.opts cannot be forged.
            await admin.execute('CREATE DATABASE %s' % other_db)
            await admin.execute("COMMENT ON DATABASE %s IS '%s'" % (other_db, pg_isolation.database_marker(OTHER_RUN)))
            import asyncpg
            other_dsn = cap.dsn.rsplit('/', 1)[0] + '/' + other_db
            forged = await asyncpg.connect(other_dsn, password=cap.password, timeout=10, server_settings={
                pg_isolation.RUN_ID_SETTING: OTHER_RUN, 'application_name': cap.tag})
            try:
                self.assertEqual(await forged.fetchval('SELECT current_setting($1)', pg_isolation.RUN_ID_SETTING),
                                 OTHER_RUN)
                other_cap = pg_isolation.Capability(OTHER_RUN, other_dsn, cap.password, cap.token, cap.provisioner_pid,
                                                    cap.provisioner_started, 'dm-disposable-pg-' + OTHER_RUN, cap.port)
                with self.assertRaisesRegex(UnsafeTestDatabase, 'not started for this run'):
                    await pg_isolation.verify_connection(forged, other_cap)
            finally:
                await forged.close()
        finally:
            await admin.execute('DROP DATABASE IF EXISTS %s' % other_db)
            await admin.close()
        conn = await pg_isolation.connect()
        try:
            for name, statement, pattern in (
                    ('ownership of another capability', "UPDATE %s SET token_sha256 = repeat('0', 64)",
                     'different capability'),
                    ('ownership record removed, marker kept', 'DROP SCHEMA %s CASCADE' % pg_isolation.OWNER_SCHEMA,
                     'ownership record for this run is missing'),
                    ('second claim', "INSERT INTO %s VALUES ('" + OTHER_RUN + "', repeat('1', 64), 1, 'x')",
                     'ambiguous')):
                with self.subTest(name):
                    transaction = conn.transaction()
                    await transaction.start()
                    try:
                        await conn.execute(statement % pg_isolation.OWNER_TABLE if '%s' in statement else statement)
                        with self.assertRaisesRegex(UnsafeTestDatabase, pattern):
                            await pg_isolation.verify_connection(conn, cap)
                    finally:
                        await transaction.rollback()
            self.assertTrue(await pg_isolation.verify_connection(conn, cap))
        finally:
            await conn.close()

    async def test_second_provisioner_cannot_claim_this_database(self):
        import disposable_postgres
        cap = pg_isolation.authorize()
        competitor = pg_isolation.Capability(cap.run_id, cap.dsn, cap.password, 'ef' * 32, os.getpid(),
                                             pg_isolation.process_start_time(os.getpid()), cap.container, cap.port)
        with self.assertRaisesRegex(UnsafeTestDatabase, 'already claimed'):
            await disposable_postgres.claim_database(competitor, time.monotonic() + 10)
        conn = await pg_isolation.connect()  # the genuine claim is intact
        await conn.close()


if __name__ == '__main__':
    unittest.main()
