import asyncio
import unittest
from decimal import Decimal
from app.control.easun_output_actuator import Authorization, EASUNOutputActuator, FakeDessTransport, PostgresJournal
from app.control.easun_output_mapping import OutputField

FIELD = OutputField.BATTERY
CAT = {'id': FIELD.value, 'item': [{'key':'26.0','val':'26.0V'}, {'key':'26.5','val':'26.5V'}, {'key':'52','val':'52.0V'}]}
AUTH = Authorization(True, 24, Decimal('24'), Decimal('27'))

class Store:
    def __init__(self): self.busy=False; self.rows={}; self.fail=None
    async def acquire(self):
        if self.fail == 'database': raise RuntimeError()
        if self.busy or self.fail == 'lock': return False
        self.busy=True; return True
    async def available(self):
        if self.fail == 'available': raise RuntimeError()
    async def unresolved(self): return any(s in ('INTENT','UNKNOWN') for s in self.rows.values())
    async def intent(self, ident, *args):
        if self.fail == 'intent': raise RuntimeError()
        self.rows[ident]='INTENT'
    async def finish(self, ident, status): self.rows[ident]=status
    async def release(self): self.busy=False

class ActuatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.store=Store(); self.fake=FakeDessTransport(CAT,'26.0V')
    def actuator(self, auth=AUTH, disabled=lambda:False): return EASUNOutputActuator(self.store,self.fake,auth,disabled=disabled)
    async def call(self, actuator=None, field=FIELD, expected='26.0V', desired='26.5'):
        return await (actuator or self.actuator()).execute(field, expected, desired)
    def writes(self): return [c for c in self.fake.calls if c[0]=='write']
    async def test_disabled(self):
        self.assertEqual((await self.call(self.actuator(Authorization()))).status,'REFUSED'); self.assertEqual(self.writes(),[])
    async def test_success_and_duplicate(self):
        self.assertEqual((await self.call()).status,'VERIFIED'); self.assertEqual(len(self.writes()),1)
        self.assertEqual((await self.call(expected='26.5V')).reason,'DUPLICATE'); self.assertEqual(len(self.writes()),1)
    async def test_unchanged_readback(self):
        self.fake.apply_write=False
        self.assertEqual((await self.call()).status,'MISMATCH'); self.assertEqual(len(self.writes()),1)
    async def test_timeout_and_lost_response_block_restart(self):
        self.fake.write_error=TimeoutError()
        self.assertEqual((await self.call()).status,'UNKNOWN'); self.assertEqual(self.fake.current,'26.5V')
        self.assertEqual((await self.call()).reason,'UNRESOLVED_INTENT'); self.assertEqual(len(self.writes()),1)
    async def test_preconditions(self):
        for kw in ({'expected':'25.5V'}, {'field':'unknown'}, {'desired':'26.2'}, {'desired':'27.0'}):
            self.assertEqual((await self.call(**kw)).status,'REFUSED'); self.assertEqual(self.writes(),[])
        self.assertEqual((await self.call(self.actuator(Authorization(True,24,None,None)))).reason,'LIMITS_UNAVAILABLE')
    async def test_database_lock_and_audit_failures(self):
        for fail in ('database','lock','available','intent'):
            self.store.fail=fail
            self.assertIn((await self.call()).status,('REFUSED','UNKNOWN')); self.assertEqual(self.writes(),[])
        self.store.fail=None
        self.store.rows['prior']='INTENT'
        self.assertEqual((await self.call()).reason,'UNRESOLVED_INTENT')
    async def test_disable_and_external_change_before_write(self):
        states=iter([False,False,True])
        self.assertEqual((await self.call(self.actuator(disabled=lambda:next(states)))).reason,'DISABLED')
        self.assertEqual(self.writes(),[])
        self.store.rows.clear()
        class Changing(FakeDessTransport): pass
        # Keep exact fake transport type: change the reported value on second read.
        original=self.fake.read_setting; count=0
        async def read(field):
            nonlocal count
            count+=1
            if count==2: self.fake.current='25.5V'
            return await original(field)
        self.fake.read_setting=read
        self.assertEqual((await self.call()).reason,'CURRENT_VALUE_CHANGED'); self.assertEqual(self.writes(),[])
    async def test_crash_after_intent_blocks_restart(self):
        class SimulatedCrash(BaseException): pass
        self.fake.write_error=SimulatedCrash()
        with self.assertRaises(SimulatedCrash): await self.call()
        self.assertIn('INTENT',self.store.rows.values())
        self.fake.write_error=None
        self.assertEqual((await self.call()).reason,'UNRESOLVED_INTENT'); self.assertEqual(len(self.writes()),1)
    async def test_crash_after_intent_before_write_never_resends(self):
        class SimulatedCrash(BaseException): pass
        original=self.store.available; count=0
        async def available():
            nonlocal count
            count+=1
            if count==2: raise SimulatedCrash()
            await original()
        self.store.available=available
        with self.assertRaises(SimulatedCrash): await self.call()
        self.assertIn('INTENT',self.store.rows.values()); self.assertEqual(self.writes(),[])
        self.store.available=original
        self.assertEqual((await self.call()).reason,'UNRESOLVED_INTENT'); self.assertEqual(self.writes(),[])
    async def test_lost_readback_is_unknown_without_retry(self):
        original=self.fake.read_setting; count=0
        async def read(field):
            nonlocal count
            count+=1
            if count==3: raise TimeoutError()
            return await original(field)
        self.fake.read_setting=read
        self.assertEqual((await self.call()).status,'UNKNOWN')
        self.assertEqual((await self.call()).reason,'UNRESOLVED_INTENT')
        self.assertEqual(len(self.writes()),1)
    async def test_two_writers_at_most_one(self):
        gate=asyncio.Event(); entered=asyncio.Event()
        original=self.fake.read_catalogue
        async def read(field): entered.set(); await gate.wait(); return await original(field)
        self.fake.read_catalogue=read
        task=asyncio.create_task(self.call()); await entered.wait()
        second=await self.call(); gate.set(); first=await task
        self.assertEqual(second.reason,'LOCK_UNAVAILABLE'); self.assertEqual(first.status,'VERIFIED'); self.assertEqual(len(self.writes()),1)
    async def test_only_fake_transport_allowed(self):
        with self.assertRaises(TypeError): EASUNOutputActuator(self.store,object(),AUTH)

class PostgreSQLTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_waits_for_connection_return(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        class Conn:
            def is_closed(self): return False
        class Pool:
            released = False
            async def release(self, conn):
                entered.set()
                await resume.wait()
                self.released = True
        pool = Pool()
        task = asyncio.create_task(PostgresJournal(pool)._return_connection(Conn()))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        resume.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(pool.released)

    async def test_pinned_connection_and_unlock(self):
        class Conn:
            def __init__(self): self.queries=[]
            def is_closed(self): return False
            async def fetchval(self,query,*args): self.queries.append(query); return True
            async def execute(self,query,*args): self.queries.append(query)
        class Pool:
            def __init__(self): self.conn=Conn(); self.released=False
            async def acquire(self): return self.conn
            async def release(self,c): self.released=True
        pool=Pool(); store=PostgresJournal(pool)
        self.assertTrue(await store.acquire()); await store.available()
        await store.intent('id','field','old','new','key'); await store.finish('id','UNKNOWN'); await store.release()
        self.assertTrue(pool.released)
        self.assertTrue(any('pg_try_advisory_lock' in q for q in pool.conn.queries))
        self.assertTrue(any('pg_advisory_unlock' in q for q in pool.conn.queries))
