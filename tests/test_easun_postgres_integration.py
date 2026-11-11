"""Opt-in (EASUN_TEST_POSTGRES=1) disposable PostgreSQL only, proven by tests/pg_isolation.py.

Run under ``tests/disposable_postgres.py run --``; there is no default DSN.
"""
import asyncio
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

try:
    import asyncpg
except ImportError:
    asyncpg = None

from app.control.easun_output_actuator import Authorization, EASUNOutputActuator, FakeDessTransport, PostgresJournal
from app.control.easun_output_mapping import OutputField
import pg_isolation

DSN = pg_isolation.configured_dsn()
CAT = {'id': OutputField.BATTERY.value, 'item': [{'key':'26.0','val':'26.0V'}, {'key':'26.5','val':'26.5V'}]}
AUTH = Authorization(True, 24, Decimal('24'), Decimal('27'))

@unittest.skipUnless(asyncpg is not None and os.environ.get('EASUN_TEST_POSTGRES') == '1', 'requires disposable local test database')
class PostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Every connection is proven to be this run's disposable database before use.
        self.pool = await pg_isolation.create_pool(DSN, min_size=2, max_size=5)
        async with self.pool.acquire() as c:
            await c.execute(PostgresJournal.SCHEMA)
            await c.execute(PostgresJournal.SCHEMA)
            await c.execute('TRUNCATE easun_output_reconciliation_events, easun_output_command_journal')
    async def asyncTearDown(self):
        try:
            await asyncio.wait_for(self.pool.close(), timeout=3)
        except asyncio.TimeoutError:
            self.fail('pool.close() timed out; a connection checkout was leaked')
    def actuator(self, fake, store=None):
        return EASUNOutputActuator(store or PostgresJournal(self.pool), fake, AUTH, disabled=lambda:False)
    async def test_success_persists_intent_before_write_and_outcome(self):
        fake = FakeDessTransport(CAT,'26.0V'); original = fake.write_setting
        async def write(field,key):
            async with self.pool.acquire() as observer:
                self.assertEqual(await observer.fetchval('SELECT status FROM easun_output_command_journal'), 'INTENT')
            await original(field,key)
        fake.write_setting=write
        result=await self.actuator(fake).execute(OutputField.BATTERY,'26.0V','26.5')
        self.assertEqual(result.status,'VERIFIED')
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT status FROM easun_output_command_journal WHERE id=$1',result.command_id),'VERIFIED')
    async def test_cross_connection_lock_and_connection_termination(self):
        a,b=PostgresJournal(self.pool),PostgresJournal(self.pool)
        self.assertTrue(await a.acquire()); self.assertFalse(await b.acquire())
        pid=await a.conn.fetchval('SELECT pg_backend_pid()')
        async with self.pool.acquire() as c: self.assertTrue(await c.fetchval('SELECT pg_terminate_backend($1)',pid))
        # The server has released the session lock; acquisition must not imply a previous write failed.
        for _ in range(40):
            if await b.acquire(): break
            await asyncio.sleep(.05)
        else: self.fail('lock was not released')
        await b.release()
        try: await a.release()
        except Exception: pass
    async def test_restart_observation_and_authorized_resolution(self):
        fake=FakeDessTransport(CAT,'26.0V'); fake.write_error=TimeoutError()
        result=await self.actuator(fake).execute(OutputField.BATTERY,'26.0V','26.5')
        self.assertEqual(result.status,'UNKNOWN'); self.assertEqual(len([x for x in fake.calls if x[0]=='write']),1)
        restarted=self.actuator(fake)
        self.assertEqual((await restarted.execute(OutputField.BATTERY,'26.0V','26.5')).reason,'UNRESOLVED_INTENT')
        observed=await restarted.reconcile(result.command_id)
        self.assertEqual(observed.status,'OBSERVED_TARGET')
        self.assertEqual((await restarted.execute(OutputField.BATTERY,'26.0V','26.5')).reason,'UNRESOLVED_INTENT')
        self.assertEqual((await restarted.resolve(result.command_id,observed.command_id,reason='Reviewed')).status,'REFUSED')
        self.assertEqual((await restarted.resolve(result.command_id,observed.command_id,operator_authorized=True,reason='Reviewed independently')).status,'MANUAL_INTERVENTION_REQUIRED')
        async with self.pool.acquire() as c:
            kinds=await c.fetch('SELECT kind FROM easun_output_reconciliation_events ORDER BY created_at')
            self.assertEqual([x['kind'] for x in kinds],['OBSERVED_TARGET'])
        self.assertEqual(len([x for x in fake.calls if x[0]=='write']),1)
    async def test_connection_loss_after_fake_write_preserves_intent(self):
        fake=FakeDessTransport(CAT,'26.0V'); store=PostgresJournal(self.pool); original=fake.write_setting
        async def write(field,key):
            await original(field,key)
            pid=await store.conn.fetchval('SELECT pg_backend_pid()')
            async with self.pool.acquire() as c: await c.fetchval('SELECT pg_terminate_backend($1)',pid)
        fake.write_setting=write
        result=await self.actuator(fake,store).execute(OutputField.BATTERY,'26.0V','26.5')
        self.assertEqual(result.status,'UNKNOWN')
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT status FROM easun_output_command_journal WHERE id=$1', result.command_id), 'INTENT')
        self.assertEqual((await self.actuator(fake).execute(OutputField.BATTERY,'26.0V','26.5')).reason,'UNRESOLVED_INTENT')

    async def test_process_crash_after_committed_intent(self):
        script = '''
import asyncio, os, sys
sys.path.insert(0, sys.argv[1])
import pg_isolation
from app.control.easun_output_actuator import PostgresJournal
async def main():
    # Same verified DSN as the parent; no fallback inside the subprocess either.
    pool = await pg_isolation.create_pool(sys.argv[2])
    journal = PostgresJournal(pool)
    assert await journal.acquire()
    await journal.intent("crashed-process", "bat_sp_battery_mode_voltage", "26.0V", "26.5", "26.5")
    os._exit(0)
asyncio.run(main())
'''
        waiter = await pg_isolation.connect(DSN)
        try:
            spawned_at = await waiter.fetchval('SELECT clock_timestamp()')
            proc = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(Path(__file__).resolve().parent), DSN)
            self.assertEqual(await proc.wait(), 0)
            # The server ends a crashed client's sessions (and its advisory lock) asynchronously; wait for that.
            for _ in range(100):
                if not await waiter.fetchval('''SELECT count(*) FROM pg_stat_activity
                        WHERE backend_type = 'client backend' AND backend_start > $1 AND pid <> pg_backend_pid()''',
                        spawned_at):
                    break
                await asyncio.sleep(0.05)
            else:
                self.fail('crashed process sessions did not end')
        finally:
            await waiter.close()
        fake = FakeDessTransport(CAT, '26.0V')
        result = await self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '26.5')
        self.assertEqual(result.reason, 'UNRESOLVED_INTENT')
        self.assertFalse(any(call[0] == 'write' for call in fake.calls))
        self.assertEqual((await self.actuator(fake).reconcile('crashed-process')).status, 'OBSERVED_PREVIOUS')

    async def test_competing_actuators_only_one_can_write(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        fake = FakeDessTransport(CAT, '26.0V')
        original = fake.read_catalogue
        async def held_read(field):
            entered.set()
            await resume.wait()
            return await original(field)
        fake.read_catalogue = held_read
        first = asyncio.create_task(self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '26.5'))
        await entered.wait()
        second = await self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '26.5')
        self.assertEqual(second.reason, 'LOCK_UNAVAILABLE')
        resume.set()
        self.assertEqual((await first).status, 'VERIFIED')
        self.assertEqual(len([c for c in fake.calls if c[0] == 'write']), 1)

    async def test_lock_lost_before_write_preserves_intent(self):
        fake, store = FakeDessTransport(CAT, '26.0V'), PostgresJournal(self.pool)
        original = fake.read_setting
        reads = 0
        async def read(field):
            nonlocal reads
            reads += 1
            if reads == 2:
                await store.conn.fetchval('SELECT pg_advisory_unlock($1)', store.LOCK_ID)
            return await original(field)
        fake.read_setting = read
        result = await self.actuator(fake, store).execute(OutputField.BATTERY, '26.0V', '26.5')
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(any(c[0] == 'write' for c in fake.calls))
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT status FROM easun_output_command_journal WHERE id=$1', result.command_id), 'INTENT')
        self.assertEqual((await self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '26.5')).reason, 'UNRESOLVED_INTENT')

    async def test_connection_terminated_before_write(self):
        fake, store = FakeDessTransport(CAT, '26.0V'), PostgresJournal(self.pool)
        original, reads = fake.read_setting, 0
        async def read(field):
            nonlocal reads
            reads += 1
            if reads == 2:
                pid = await store.conn.fetchval('SELECT pg_backend_pid()')
                async with self.pool.acquire() as c:
                    await c.fetchval('SELECT pg_terminate_backend($1)', pid)
            return await original(field)
        fake.read_setting = read
        result = await self.actuator(fake, store).execute(OutputField.BATTERY, '26.0V', '26.5')
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertFalse(any(c[0] == 'write' for c in fake.calls))
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT status FROM easun_output_command_journal WHERE id=$1', result.command_id), 'INTENT')

    async def test_reconciliation_observations_and_append_only_audit(self):
        fake = FakeDessTransport(CAT, '25.5V')
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO easun_output_command_journal (id,field,old_value,new_value,command_key,status) VALUES ('uncertain',$1,'26.0V','26.5','26.5','INTENT')", OutputField.BATTERY.value)
        actuator = self.actuator(fake)
        other = await actuator.reconcile('uncertain')
        self.assertEqual(other.status, 'OBSERVED_OTHER')
        fake.read_error = TimeoutError()
        unavailable = await actuator.reconcile('uncertain')
        self.assertEqual(unavailable.status, 'READBACK_UNAVAILABLE')
        self.assertEqual((await actuator.resolve('uncertain', unavailable.command_id, operator_authorized=True, reason='reviewed')).status, 'MANUAL_INTERVENTION_REQUIRED')
        async with self.pool.acquire() as c:
            with self.assertRaises(asyncpg.PostgresError):
                await c.execute('DELETE FROM easun_output_reconciliation_events WHERE id=$1', other.command_id)
            self.assertIsNotNone(await c.fetchval('SELECT created_at FROM easun_output_reconciliation_events WHERE id=$1', other.command_id))
        self.assertFalse(any(c[0] == 'write' for c in fake.calls))

    async def test_mismatch_blocks_until_reconciled(self):
        fake = FakeDessTransport(CAT, '26.0V')
        fake.apply_write = False
        actuator = self.actuator(fake)
        result = await actuator.execute(OutputField.BATTERY, '26.0V', '26.5')
        self.assertEqual(result.status, 'MISMATCH')
        self.assertEqual((await actuator.execute(OutputField.BATTERY, '26.0V', '26.5')).reason, 'UNRESOLVED_INTENT')
        observed = await actuator.reconcile(result.command_id)
        self.assertEqual(observed.status, 'OBSERVED_PREVIOUS')
        self.assertEqual((await actuator.resolve(result.command_id, observed.command_id, operator_authorized=True, reason='reviewed')).status, 'MANUAL_INTERVENTION_REQUIRED')
        async with self.pool.acquire() as c:
            self.assertEqual(await c.fetchval('SELECT status FROM easun_output_command_journal WHERE id=$1', result.command_id), 'MISMATCH')

    async def test_inflight_lock_loss_cannot_be_resolved_into_conflicting_write(self):
        catalogue = {'id': OutputField.BATTERY.value, 'item': [
            {'key': value, 'val': value + 'V'} for value in ('25.5', '26.0', '26.5')]}
        fake = FakeDessTransport(catalogue, '26.0V')
        store = PostgresJournal(self.pool)
        entered, resume = asyncio.Event(), asyncio.Event()
        original = fake.write_setting
        async def delayed(field, key):
            entered.set()
            await resume.wait()
            await original(field, key)
        fake.write_setting = delayed
        first = asyncio.create_task(self.actuator(fake, store).execute(OutputField.BATTERY, '26.0V', '26.5'))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            pid = await store.conn.fetchval('SELECT pg_backend_pid()')
            async with self.pool.acquire() as c:
                command = await c.fetchval("SELECT id FROM easun_output_command_journal WHERE status='INTENT'")
                await c.fetchval('SELECT pg_terminate_backend($1)', pid)
            second = self.actuator(fake)
            observed = await second.reconcile(command)
            self.assertEqual(observed.status, 'OBSERVED_PREVIOUS')
            resolution = await second.resolve(command, observed.command_id, operator_authorized=True, reason='reviewed')
            self.assertEqual(resolution.status, 'MANUAL_INTERVENTION_REQUIRED')
            self.assertEqual((await second.execute(OutputField.BATTERY, '26.0V', '25.5')).reason, 'UNRESOLVED_INTENT')
            self.assertEqual((await self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '25.5')).reason, 'UNRESOLVED_INTENT')
            self.assertFalse(any(call[0] == 'write' for call in fake.calls))
        finally:
            resume.set()
            result = await asyncio.wait_for(first, 3)
        self.assertEqual(result.status, 'UNKNOWN')
        self.assertEqual([call[2] for call in fake.calls if call[0] == 'write'], ['26.5'])
        self.assertEqual((await self.actuator(fake).execute(OutputField.BATTERY, '26.5V', '25.5')).reason, 'UNRESOLVED_INTENT')

    async def test_repeated_termination_releases_pinned_connections(self):
        for after_write in (False, True):
            for _ in range(12):
                async with self.pool.acquire() as c:
                    await c.execute('TRUNCATE easun_output_reconciliation_events, easun_output_command_journal')
                fake, store = FakeDessTransport(CAT, '26.0V'), PostgresJournal(self.pool)
                read, write, count = fake.read_setting, fake.write_setting, 0
                async def terminate():
                    pid = await store.conn.fetchval('SELECT pg_backend_pid()')
                    async with self.pool.acquire() as c:
                        await c.fetchval('SELECT pg_terminate_backend($1)', pid)
                async def before(field):
                    nonlocal count
                    count += 1
                    if count == 2 and not after_write:
                        await terminate()
                    return await read(field)
                async def after(field, key):
                    await write(field, key)
                    if after_write:
                        await terminate()
                fake.read_setting, fake.write_setting = before, after
                result = await self.actuator(fake, store).execute(OutputField.BATTERY, '26.0V', '26.5')
                self.assertEqual(result.status, 'UNKNOWN')
                self.assertEqual(len([c for c in fake.calls if c[0] == 'write']), int(after_write))
                self.assertEqual(sum(h._in_use is not None for h in self.pool._holders), 0)

    async def test_cancellation_returns_connection_and_preserves_intent(self):
        fake, entered = FakeDessTransport(CAT, '26.0V'), asyncio.Event()
        async def held(field, key):
            entered.set()
            await asyncio.Future()
        fake.write_setting = held
        task = asyncio.create_task(self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '26.5'))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sum(h._in_use is not None for h in self.pool._holders), 0)
        self.assertEqual((await self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '26.5')).reason, 'UNRESOLVED_INTENT')

    async def test_legacy_resolution_and_unknown_status_fail_closed(self):
        fake = FakeDessTransport(CAT, '26.0V')
        for status in ('RESOLVED', 'UNKNOWN', 'UNRECOGNIZED'):
            async with self.pool.acquire() as c:
                await c.execute('TRUNCATE easun_output_reconciliation_events, easun_output_command_journal')
                await c.execute("INSERT INTO easun_output_command_journal (id,field,old_value,new_value,command_key,status) VALUES ('legacy',$1,'26.0V','26.5','26.5',$2)", OutputField.BATTERY.value, status)
            self.assertEqual((await self.actuator(fake).execute(OutputField.BATTERY, '26.0V', '26.5')).reason, 'UNRESOLVED_INTENT')
        self.assertFalse(any(c[0] == 'write' for c in fake.calls))

    async def test_nullable_legacy_schema_null_status_blocks_and_reconciles(self):
        fake = FakeDessTransport(CAT, '26.0V')
        try:
            async with self.pool.acquire() as c:
                await c.execute('ALTER TABLE easun_output_command_journal ALTER COLUMN status DROP NOT NULL')
                # CREATE IF NOT EXISTS leaves an existing nullable schema intact.
                await c.execute(PostgresJournal.SCHEMA)
                await c.execute("INSERT INTO easun_output_command_journal (id,field,old_value,new_value,command_key,status) VALUES ('legacy-null',$1,'26.0V','26.5','26.5',NULL)", OutputField.BATTERY.value)
            actuator = self.actuator(fake)
            self.assertEqual((await actuator.execute(OutputField.BATTERY, '26.0V', '26.5')).reason, 'UNRESOLVED_INTENT')
            self.assertEqual((await actuator.reconcile('legacy-null')).status, 'OBSERVED_PREVIOUS')
            self.assertFalse(any(call[0] == 'write' for call in fake.calls))
        finally:
            async with self.pool.acquire() as c:
                await c.execute('TRUNCATE easun_output_reconciliation_events, easun_output_command_journal')
                await c.execute('ALTER TABLE easun_output_command_journal ALTER COLUMN status SET NOT NULL')

    async def test_normal_schema_rejects_null_status(self):
        async with self.pool.acquire() as c:
            with self.assertRaises(asyncpg.NotNullViolationError):
                await c.execute("INSERT INTO easun_output_command_journal (id,field,old_value,new_value,command_key,status) VALUES ('null',$1,'26.0V','26.5','26.5',NULL)", OutputField.BATTERY.value)
        self.assertEqual((await self.actuator(FakeDessTransport(CAT, '26.0V')).execute(OutputField.BATTERY, '26.0V', '26.5')).status, 'VERIFIED')
