"""Regression tests: which SQL the Journal v2 storage benchmark runs in each measurement mode.

No database: the guard's connect/pool, the reset, and the evaluation functions are replaced,
and a recording connection captures every statement in order.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import benchmark_adaptive_journal_v2_storage as bench

MINUTES = 120  # two periodic-VACUUM intervals


class RecordingConnection:
    def __init__(self, vacuum_count=0, autovacuum_count=0):
        self.log = []
        self.vacuum_count, self.autovacuum_count = vacuum_count, autovacuum_count

    async def execute(self, sql, *args):
        self.log.append(('execute', ' '.join(sql.split())))

    async def fetchval(self, sql, *args):
        self.log.append(('fetchval', ' '.join(sql.split())))
        return 1

    async def fetch(self, sql, *args):
        self.log.append(('fetch', ' '.join(sql.split())))
        if 'pg_stat_user_tables' in sql:
            return [{'vacuum_count': self.vacuum_count, 'autovacuum_count': self.autovacuum_count}]
        return []

    async def close(self):
        pass

    def executed(self):
        return [sql for kind, sql in self.log if kind == 'execute']


class Pool:
    async def close(self):
        pass


class BenchmarkModeTests(unittest.TestCase):
    def run_mode(self, mode, vacuum_mode, final_vacuum=False, conn=None):
        conn = conn or RecordingConnection(vacuum_count=0 if vacuum_mode == 'none' else 2)
        with patch.object(bench, 'MINUTES', MINUTES), \
             patch.object(bench.pg_isolation, 'connect', AsyncMock(return_value=conn)), \
             patch.object(bench.pg_isolation, 'create_pool', AsyncMock(return_value=Pool())), \
             patch.object(bench, 'reset', AsyncMock()), \
             patch.object(bench.ol, 'run_evaluation', AsyncMock(return_value=True)), \
             patch.object(bench.v2, 'run_evaluation_v2', AsyncMock(return_value=True)), \
             patch.object(bench.v2, 'cutover_to_v2', AsyncMock()):
            return asyncio.run(bench.run(mode, vacuum_mode, final_vacuum)), conn

    @staticmethod
    def vacuums(conn):
        return [sql for sql in conn.executed() if sql.upper().startswith('VACUUM')]

    def test_measurement_never_vacuums_or_analyzes(self):
        conn = RecordingConnection()
        asyncio.run(bench.sizes(conn, ['adaptive_decision_journal', 'adaptive_learning_state']))
        self.assertEqual(conn.executed(), [])
        self.assertFalse(any('VACUUM' in sql.upper() or 'ANALYZE' in sql.upper() for _, sql in conn.log))

    def test_no_vacuum_mode_runs_no_vacuum_and_disables_autovacuum_first(self):
        for mode in ('v1', 'v2'):
            with self.subTest(mode=mode):
                report, conn = self.run_mode(mode, 'none')
                self.assertEqual(self.vacuums(conn), [])
                self.assertFalse(any('ANALYZE' in sql.upper() for sql in conn.executed()))
                tables = bench.journal_tables(mode)
                self.assertEqual(conn.executed(), [
                    'ALTER TABLE %s SET (autovacuum_enabled = false, toast.autovacuum_enabled = false)' % t
                    for t in tables])
                self.assertEqual(report['vacuum_mode'], 'none')
                self.assertEqual(report['periodic_manual_vacuums'], 0)
                self.assertNotIn('after_final_vacuum', report)

    def test_periodic_mode_vacuums_every_journal_table_on_schedule_only(self):
        for mode in ('v1', 'v2'):
            with self.subTest(mode=mode):
                report, conn = self.run_mode(mode, 'periodic')
                expected = 'VACUUM ' + ', '.join(bench.journal_tables(mode))
                self.assertEqual(self.vacuums(conn), [expected] * (MINUTES // bench.PERIODIC_VACUUM_EVERY))
                self.assertEqual(report['periodic_manual_vacuums'], MINUTES // bench.PERIODIC_VACUUM_EVERY)
                last_vacuum = max(i for i, (kind, sql) in enumerate(conn.log) if sql.startswith('VACUUM'))
                first_measure = min(i for i, (kind, sql) in enumerate(conn.log) if 'pg_total_relation_size' in sql)
                self.assertLess(last_vacuum, first_measure)

    def test_final_vacuum_is_measured_and_labelled_separately(self):
        for vacuum_mode in bench.VACUUM_MODES:
            with self.subTest(vacuum_mode=vacuum_mode):
                report, conn = self.run_mode('v2', vacuum_mode, final_vacuum=True)
                log = conn.log
                final = max(i for i, (kind, sql) in enumerate(log) if sql.startswith('VACUUM'))
                measured_before = [i for i, (kind, sql) in enumerate(log) if 'pg_total_relation_size' in sql]
                tables = len(bench.journal_tables('v2'))
                self.assertEqual(len(measured_before), 2 * tables)  # once before, once after the final VACUUM
                self.assertTrue(all(i < final for i in measured_before[:tables]))
                self.assertTrue(all(i > final for i in measured_before[tables:]))
                self.assertIn('after_final_vacuum', report)
                self.assertIn('measured', report)
                periodic = MINUTES // bench.PERIODIC_VACUUM_EVERY if vacuum_mode == 'periodic' else 0
                self.assertEqual(len(self.vacuums(conn)), periodic + 1)

    def test_any_vacuum_activity_invalidates_a_no_vacuum_result(self):
        with self.assertRaisesRegex(RuntimeError, 'autovacuum ran'):
            self.run_mode('v2', 'none', conn=RecordingConnection(autovacuum_count=1))
        with self.assertRaisesRegex(RuntimeError, 'autovacuum ran'):
            self.run_mode('v1', 'periodic', conn=RecordingConnection(vacuum_count=2, autovacuum_count=1))
        with self.assertRaisesRegex(RuntimeError, 'no-VACUUM'):
            self.run_mode('v1', 'none', conn=RecordingConnection(vacuum_count=1))

    def test_unknown_modes_are_refused(self):
        with self.assertRaises(ValueError):
            asyncio.run(bench.run('v2', 'sometimes'))


if __name__ == '__main__':
    unittest.main()
