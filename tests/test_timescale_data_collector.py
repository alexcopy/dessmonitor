"""Task 0079: Timescale checkpoint/event writer and adaptive schedules (no real database)."""
import asyncio
import json
import os
import time
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

from app.api import DeviceData
from app.control.adaptive_telemetry import AdaptiveTelemetryRecorder
from app.ml import timescale_data_collector as tdc
from app.ml.timescale_data_collector import PowerMode, TimescaleDataCollector
from shared_state.shared_state import shared_state


def dd(**overrides):
    values = dict(timestamp="t0", working_state="Line Mode", battery_voltage=26.4, battery_capacity=61.0,
                  battery_charging_current=0.0, battery_discharging_current=0.0, pv_total_power=0.0,
                  output_power=400.0, ac_output_load=8.0, ac_input_voltage=230.0, charger_priority="Solar only")
    values.update(overrides)
    return DeviceData(**values)


class FakeConn:
    """In-memory stand-in for one asyncpg connection.

    Returns real PostgreSQL command tags ("INSERT 0 1" / "INSERT 0 0"),
    enforces the time primary keys, and supports nested transactions
    (savepoints) with rollback, plus injected faults:
      pool.fail            -> every statement raises (database down)
      pool.zero_rows       -> every INSERT reports "INSERT 0 0" and stores nothing
      pool.status_override -> every INSERT returns this tag (e.g. None, "UPDATE 1")
      pool.lose_commit_ack -> outermost COMMIT is applied, then the ack is lost
      pool.during_execute  -> callback run inside each INSERT (concurrent arrivals)
    """

    def __init__(self, pool):
        self.pool = pool
        self.depth = 0

    async def execute(self, sql, *args):
        if self.pool.fail:
            raise ConnectionError("database down")
        text = " ".join(sql.split())
        if not text.startswith("INSERT INTO"):
            return "OK"  # DDL during initialize()
        self.pool.statements.append((text, args))
        if self.pool.during_execute is not None:
            self.pool.during_execute()
        if self.pool.status_override is not False:
            return self.pool.status_override
        if self.pool.zero_rows:
            return "INSERT 0 0"
        table = self.pool.rows[text.split()[2]]
        if args[0] in table:
            return "INSERT 0 0"
        table[args[0]] = args
        return "INSERT 0 1"

    async def fetchval(self, sql, *args):
        if self.pool.fail:
            raise ConnectionError("database down")
        if sql.strip() == "SELECT 1":
            return 1
        if "metadata->>'event_id'" in sql:
            row = self.pool.rows["power_mode_events"].get(args[0])
            return json.loads(row[4]).get("event_id") if row and row[4] else None
        return 0  # SELECT COUNT(*)

    @asynccontextmanager
    async def transaction(self):
        saved = {name: dict(rows) for name, rows in self.pool.rows.items()}
        self.depth += 1
        try:
            yield
        except BaseException:
            for name, rows in saved.items():  # in place: pools may share one "server"
                self.pool.rows[name].clear()
                self.pool.rows[name].update(rows)
            raise
        finally:
            self.depth -= 1
        if self.depth == 0 and self.pool.lose_commit_ack:
            self.pool.lose_commit_ack = False
            if self.pool.break_after_lost_ack:
                self.pool.fail = True  # the connection is gone, not just the reply
            raise ConnectionResetError("connection lost before COMMIT acknowledgement")


class FakePool:
    def __init__(self):
        self.statements = []
        self.rows = {"power_mode_events": {}, "inverter_metrics": {}}
        self.fail = False
        self.zero_rows = False
        self.status_override = False
        self.lose_commit_ack = False
        self.break_after_lost_ack = False
        self.during_execute = None
        self.closed = False

    @asynccontextmanager
    async def acquire(self):
        yield FakeConn(self)

    async def close(self):
        self.closed = True

    def tables(self):
        return [sql.split()[2] for sql, _ in self.statements if sql.startswith("INSERT INTO")]

    def stored_event_ids(self):
        return [json.loads(row[4])["event_id"] for row in self.pool_rows("power_mode_events")]

    def pool_rows(self, table):
        return [self.rows[table][key] for key in sorted(self.rows[table])]


class DevMgr:
    def get_devices(self):
        return []


def collector_with(recorder=None):
    collector = TimescaleDataCollector(database_url="synthetic", telemetry_recorder=recorder)
    collector.pool = FakePool()
    collector._is_initialized = True
    collector._collect_weather_data = AsyncMock()
    return collector


class EventWriterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime.now(timezone.utc)
        self.rec = AdaptiveTelemetryRecorder()
        self.collector = collector_with(self.rec)

    async def test_attaching_the_writer_enables_queueing(self):
        self.assertTrue(self.rec.writer_attached)
        self.assertEqual(self.rec.persistence_status, "unavailable")

    async def test_mode_transition_persists_immediately_with_its_own_values(self):
        self.rec.observe(dd(), received_at=self.now - timedelta(seconds=60))
        self.rec.observe(dd(working_state="Invert Mode", output_power=1500.0), received_at=self.now)
        with patch.dict(shared_state, {"output_power": 9999.0, "working_mode": "Line Mode"}):
            written = await self.collector.persist_pending_events()
        self.assertEqual(written, 1)
        self.assertEqual(self.collector.pool.tables(), ["power_mode_events", "inverter_metrics"])
        (pme_sql, pme), (_, metrics) = self.collector.pool.statements
        meta = json.loads(pme[4])
        self.assertEqual((pme[1], pme[2], meta["event_kind"]), ("Line Mode", "Invert Mode", "mode_transition"))
        self.assertEqual(meta["timestamp_quality"], "device_time_unverified")
        self.assertIsNone(meta["measurement_time"])
        self.assertEqual(metrics[0], pme[0])  # same persistence time
        self.assertEqual(metrics[6], 1500.0)  # event's output power, not shared_state
        self.assertEqual(metrics[12], "Invert Mode")
        self.assertEqual(metrics[13], "solar_only")
        self.assertEqual(self.rec.pending(), ())
        self.assertEqual(self.rec.persistence_status, "available")

    async def test_line_invert_line_writes_two_transition_rows_before_checkpoint(self):
        for i, mode in enumerate(("Line Mode", "Invert Mode", "Invert Mode", "Line Mode")):
            self.rec.observe(dd(working_state=mode, timestamp="t%d" % i), received_at=self.now + timedelta(minutes=i))
            await self.collector.persist_pending_events()
        kinds = [json.loads(a[4])["event_kind"] for s, a in self.collector.pool.statements if "power_mode_events" in s]
        self.assertEqual(kinds, ["mode_transition", "mode_transition"])
        times = [a[0] for s, a in self.collector.pool.statements if "power_mode_events" in s]
        self.assertLess(times[0], times[1])

    async def test_database_outage_degrades_retains_events_and_backs_off(self):
        self.rec.observe(dd(), received_at=self.now)
        self.rec.observe(dd(working_state="Invert Mode"), received_at=self.now)
        self.collector.pool.fail = True
        self.assertEqual(await self.collector.persist_pending_events(), 0)
        self.assertEqual(self.rec.persistence_status, "degraded")
        self.assertEqual(len(self.rec.pending()), 1)
        self.collector.pool.fail = False
        self.assertEqual(await self.collector.persist_pending_events(), 0)  # backing off
        self.collector._persist_retry_at = time.monotonic() - 1
        self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertEqual(self.rec.persistence_status, "available")

    async def test_missing_pool_is_unavailable(self):
        self.collector.pool = None
        self.assertEqual(await self.collector.persist_pending_events(), 0)
        self.assertEqual(self.rec.persistence_status, "unavailable")

    async def test_write_batches_are_bounded(self):
        rec = AdaptiveTelemetryRecorder(queue_size=500)
        collector = collector_with(rec)
        for i in range(301):
            rec.observe(dd(working_state="Invert Mode" if i % 2 else "Line Mode"), received_at=self.now)
        self.assertEqual(await collector.persist_pending_events(), tdc.MAX_WRITE_BATCH)
        self.assertEqual(len(rec.pending(1000)), 300 - tdc.MAX_WRITE_BATCH)


class VerifiedPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Blocker 1: a zero-row INSERT never acknowledges an unsaved event."""

    async def asyncSetUp(self):
        self.now = datetime.now(timezone.utc)
        self.rec = AdaptiveTelemetryRecorder()
        self.collector = collector_with(self.rec)
        self.pool = self.collector.pool

    def transition(self, minute=0):
        self.rec.observe(dd(timestamp="a%d" % minute), received_at=self.now + timedelta(minutes=minute))
        self.rec.observe(dd(working_state="Invert Mode", timestamp="b%d" % minute),
                         received_at=self.now + timedelta(minutes=minute, seconds=30))
        return self.rec.pending()[-1]

    def allow_retry(self):
        self.collector._persist_retry_at = time.monotonic() - 1

    async def test_zero_row_insert_is_not_acknowledged(self):
        # Reproduction from the precommit review: both INSERTs report INSERT 0 0.
        event = self.transition()
        self.pool.zero_rows = True
        self.assertEqual(await self.collector.persist_pending_events(), 0)
        self.assertEqual(self.rec.pending(), (event,))
        self.assertEqual(self.rec.events_persisted, 0)
        self.assertEqual(self.rec.persistence_status, "degraded")
        self.assertEqual(self.pool.rows["power_mode_events"], {})
        # Once the database stores rows again, the same event is written once.
        self.pool.zero_rows = False
        self.allow_retry()
        self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertEqual(self.pool.stored_event_ids(), [event.event_id])
        self.assertEqual((self.rec.events_persisted, self.rec.events_reconciled_existing), (1, 0))

    async def test_unrecognised_command_tag_is_not_acknowledged(self):
        self.transition()
        for status in (None, "UPDATE 1", "INSERT 0 2", "INSERT 0 x"):
            with self.subTest(status=status):
                self.pool.status_override = status
                self.allow_retry()
                self.assertEqual(await self.collector.persist_pending_events(), 0)
                self.assertEqual(len(self.rec.pending()), 1)
                self.assertEqual(self.rec.events_persisted, 0)

    async def test_lost_commit_acknowledgement_is_reconciled_without_duplicate(self):
        event = self.transition()
        self.pool.lose_commit_ack = True  # rows committed, client sees an error
        self.assertEqual(await self.collector.persist_pending_events(), 0)
        self.assertEqual(self.rec.pending(), (event,))  # uncertain: not acknowledged
        self.assertEqual(len(self.pool.rows["power_mode_events"]), 1)
        self.allow_retry()
        self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertEqual(self.pool.stored_event_ids(), [event.event_id])  # no duplicate event row
        self.assertEqual(len(self.pool.rows["inverter_metrics"]), 1)  # no duplicate metrics row
        self.assertEqual((self.rec.events_persisted, self.rec.events_reconciled_existing), (1, 1))
        self.assertEqual(self.rec.pending(), ())

    async def test_foreign_row_at_persistence_key_is_rekeyed_not_acknowledged(self):
        event = self.transition()
        t1, t2 = self.now, self.now + timedelta(milliseconds=5)
        foreign = (t1, "Line Mode", "Invert Mode", None, json.dumps({"event_id": "someone-else"}))
        self.pool.rows["power_mode_events"][t1] = foreign
        with patch.object(self.collector, "_next_event_time", side_effect=[t1, t2]):
            self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertIs(self.pool.rows["power_mode_events"][t1], foreign)  # untouched
        self.assertEqual(json.loads(self.pool.rows["power_mode_events"][t2][4])["event_id"], event.event_id)
        self.assertIn(t2, self.pool.rows["inverter_metrics"])
        self.assertEqual(self.rec.events_reconciled_existing, 0)

    async def test_metrics_key_collision_rolls_back_the_event_row_and_rekeys(self):
        event = self.transition()
        t1, t2 = self.now, self.now + timedelta(milliseconds=5)
        self.pool.rows["inverter_metrics"][t1] = ("checkpoint-row",)
        with patch.object(self.collector, "_next_event_time", side_effect=[t1, t2]):
            self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertNotIn(t1, self.pool.rows["power_mode_events"])  # savepoint rolled back
        self.assertEqual(self.pool.stored_event_ids(), [event.event_id])
        self.assertEqual(self.pool.rows["inverter_metrics"][t1], ("checkpoint-row",))
        self.assertIn(t2, self.pool.rows["inverter_metrics"])

    async def test_unresolved_collisions_roll_back_the_whole_batch(self):
        self.transition(0)
        self.transition(5)
        queued = [e.event_id for e in self.rec.pending()]
        self.assertEqual(len(queued), 3)
        foreign = json.dumps({"event_id": "someone-else"})
        clash = [self.now + timedelta(seconds=i) for i in range(10)]
        for key in clash[1:]:
            self.pool.rows["power_mode_events"][key] = (key, "x", "y", None, foreign)
        with patch.object(self.collector, "_next_event_time", side_effect=clash):
            self.assertEqual(await self.collector.persist_pending_events(), 0)
        # The first event's insert at clash[0] was rolled back with the batch.
        self.assertNotIn(clash[0], self.pool.rows["power_mode_events"])
        self.assertEqual([e.event_id for e in self.rec.pending()], queued)
        self.assertEqual(self.rec.events_persisted, 0)

    async def test_retry_preserves_order_and_stable_keys(self):
        self.transition(0)
        self.transition(5)
        queued = [e.event_id for e in self.rec.pending()]
        self.pool.fail = True
        await self.collector.persist_pending_events()
        keys = dict(self.collector._event_keys)
        self.pool.fail = False
        self.allow_retry()
        self.assertEqual(await self.collector.persist_pending_events(), 3)
        self.assertEqual(self.pool.stored_event_ids(), queued)  # receipt order == key order
        self.assertEqual(list(keys), queued[:1])  # failed on the first INSERT
        stored_at = {json.loads(row[4])["event_id"]: row[0] for row in self.pool.pool_rows("power_mode_events")}
        self.assertEqual(stored_at[queued[0]], keys[queued[0]])  # retry reused the stable key
        self.assertEqual(self.collector._event_keys, {})  # bounded: cleared on acknowledgement

    async def test_events_arriving_during_the_write_are_kept(self):
        written = self.transition(0)
        arrivals = []

        def concurrent_dess_response():
            if not arrivals:
                arrivals.append(True)
                self.rec.observe(dd(timestamp="late"), received_at=self.now + timedelta(minutes=9))

        self.pool.during_execute = concurrent_dess_response
        self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertEqual(self.pool.stored_event_ids(), [written.event_id])
        (late,) = self.rec.pending()
        self.assertEqual(late.to_mode, "Line Mode")
        self.assertEqual(self.rec.queue_drops, 0)
        self.pool.during_execute = None
        self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertEqual(self.pool.stored_event_ids(), [written.event_id, late.event_id])

    async def test_overflow_during_write_counts_only_unconfirmed_events_as_dropped(self):
        rec = AdaptiveTelemetryRecorder(queue_size=1)
        collector = collector_with(rec)
        rec.observe(dd(), received_at=self.now)
        rec.observe(dd(working_state="Invert Mode"), received_at=self.now + timedelta(minutes=1))
        collector.pool.during_execute = lambda: rec.observe(
            dd(timestamp="n%d" % len(collector.pool.statements)), received_at=self.now + timedelta(minutes=2))
        self.assertEqual(await collector.persist_pending_events(), 1)  # evicted mid-write but stored
        self.assertEqual((rec.events_persisted, rec.queue_drops), (1, 0))

    async def test_checkpoint_metrics_zero_row_insert_adds_no_marker(self):
        self.rec.observe(dd(), received_at=datetime.now(timezone.utc))
        self.pool.zero_rows = True
        await self.collector.collect_data(DevMgr())
        self.assertEqual(self.rec.persistence_status, "degraded")
        self.assertEqual(self.rec.pending(), ())
        self.assertEqual(self.rec.checkpoints, 0)


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rec = AdaptiveTelemetryRecorder()
        self.collector = collector_with(self.rec)

    async def test_checkpoint_writes_metrics_and_one_marker(self):
        self.rec.observe(dd(), received_at=datetime.now(timezone.utc))
        with patch.dict(shared_state, {"battery_voltage": 26.4, "working_mode": "Line Mode"}):
            stats = await self.collector.collect_data(DevMgr())
        self.assertEqual(stats["status"], "success")
        await self.collector.persist_pending_events()
        self.assertEqual(self.collector.pool.tables(), ["inverter_metrics", "power_mode_events"])
        meta = json.loads(self.collector.pool.statements[1][1][4])
        self.assertEqual(meta["event_kind"], "checkpoint")
        self.assertFalse(meta["verified_observation"])

    async def test_dess_outage_does_not_resave_last_response_as_fresh(self):
        self.rec.observe(dd(), received_at=datetime.now(timezone.utc) - timedelta(minutes=20))
        with patch.dict(shared_state, {"battery_voltage": 26.4}):
            await self.collector.collect_data(DevMgr())
        await self.collector.persist_pending_events()
        self.assertEqual(self.collector.pool.tables(), [])
        self.assertEqual(self.rec.stale_checkpoints_skipped, 1)

    async def test_no_response_yet_writes_nothing(self):
        await self.collector.collect_data(DevMgr())
        self.assertEqual(self.collector.pool.tables(), [])

    async def test_metrics_insert_failure_marks_degraded_without_marker(self):
        self.rec.observe(dd(), received_at=datetime.now(timezone.utc))
        self.collector.pool.fail = True
        await self.collector.collect_data(DevMgr())
        self.assertEqual(self.rec.persistence_status, "degraded")
        self.assertEqual(self.rec.pending(), ())

    async def test_legacy_clock_heuristic_mode_change_is_not_logged_with_recorder(self):
        self.collector._log_mode_change_event = AsyncMock()
        self.collector._previous_mode = PowerMode.GRID
        with patch.object(self.collector, "_detect_power_mode", return_value=PowerMode.INVERTER):
            await self.collector.collect_data(DevMgr())
        self.collector._log_mode_change_event.assert_not_awaited()

    async def test_legacy_path_without_recorder_is_unchanged(self):
        collector = collector_with(None)
        collector._log_mode_change_event = AsyncMock()
        collector._previous_mode = PowerMode.GRID
        with patch.object(collector, "_detect_power_mode", return_value=PowerMode.INVERTER):
            await collector.collect_data(DevMgr())
        collector._log_mode_change_event.assert_awaited_once()
        self.assertEqual(collector.pool.tables(), ["inverter_metrics"])

    async def test_identical_responses_do_not_inflate_rows(self):
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        for minute in range(60):  # one cached response per minute for an hour
            self.rec.observe(dd(), received_at=start + timedelta(minutes=minute))
            if minute % 5 == 0:
                self.rec.add_checkpoint(collected_at=start + timedelta(minutes=minute, seconds=1))
        await self.collector.persist_pending_events()
        self.assertEqual(self.collector.pool.tables(), ["power_mode_events"] * 12)  # checkpoints only
        self.assertEqual(self.rec.repeats, 59)


class IntervalTests(unittest.TestCase):
    def setUp(self):
        self.rec = AdaptiveTelemetryRecorder()
        self.collector = TimescaleDataCollector(database_url="synthetic", telemetry_recorder=self.rec)

    def stable_night(self):
        now = datetime.now(timezone.utc)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Sun times chosen so that "now" is always outside sunrise-1h..sunset+1h.
        sunrise, sunset = now + timedelta(hours=3), now + timedelta(hours=10)
        sun = (now - timedelta(minutes=5), sunrise, sunset)
        self.rec.observe(dd(), received_at=now - timedelta(minutes=20), sun_times=sun)
        self.rec.observe(dd(timestamp="x"), received_at=now - timedelta(seconds=30), sun_times=sun)
        return day

    def interval(self, **state):
        base = {"battery_current_chg": 0.0, "charger_priority": "solar_only", "battery_voltage": 26.4}
        base.update(state)
        with patch.dict(shared_state, base):
            return self.collector.get_current_interval()

    def test_unknown_context_is_conservative_300(self):
        self.collector._current_mode = PowerMode.GRID
        self.assertEqual(self.interval(), 300)

    def test_validated_stable_grid_night_is_900(self):
        self.stable_night()
        self.collector._current_mode = PowerMode.GRID
        self.assertEqual(self.interval(), 900)

    def test_clock_heuristic_cannot_select_900(self):
        self.collector._current_mode = PowerMode.GRID  # sunrise_hour/sunset_hour heuristic says night
        self.rec.observe(dd(), received_at=datetime.now(timezone.utc))
        self.assertEqual(self.interval(), 300)

    def test_charging_emergency_and_switching_keep_precedence(self):
        self.stable_night()
        self.collector._current_mode = PowerMode.GRID
        self.assertEqual(self.interval(battery_current_chg=5.0), 60)
        self.collector._charge_detail_until = 0.0
        self.assertEqual(self.interval(charger_priority="solar_and_mains"), 60)
        self.collector._charge_detail_until = 0.0
        self.assertEqual(self.interval(battery_voltage=24.9), 60)
        self.assertEqual(self.interval(battery_voltage=25.3), 300)
        self.collector._current_mode = PowerMode.SWITCHING
        self.assertEqual(self.interval(), 10)

    def test_without_recorder_legacy_intervals_apply(self):
        collector = TimescaleDataCollector(database_url="synthetic")
        collector._current_mode = PowerMode.INVERTER
        with patch.dict(shared_state, {"battery_current_chg": 0.0, "charger_priority": None}):
            self.assertEqual(collector.get_current_interval(), 300)


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_loop_flushes_events_and_issues_no_dess_request(self):
        rec = AdaptiveTelemetryRecorder()
        collector = collector_with(rec)
        collector.initialize = AsyncMock(return_value=True)
        stop = asyncio.Event()
        collector.collect_data = AsyncMock(return_value={"status": "success"})

        async def flush_then_stop():
            stop.set()
            return 0

        collector.persist_pending_events = AsyncMock(side_effect=flush_then_stop)
        await asyncio.wait_for(tdc.timescale_collection_loop(collector, DevMgr(), stop), timeout=5)
        collector.persist_pending_events.assert_awaited()
        self.assertEqual(rec.persistence_status, "unavailable")  # closed on exit
        self.assertFalse(hasattr(tdc, "DessAPI"))



class _FakeAsyncpg:
    """Stand-in for the asyncpg module: create_pool fails while the database is down."""

    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0
        self.pools = []
        self.down = False
        # One "server": every pool created sees the same committed rows.
        self.rows = {"power_mode_events": {}, "inverter_metrics": {}}

    async def create_pool(self, *args, **kwargs):
        self.calls += 1
        if self.down or self.calls <= self.failures:
            raise ConnectionRefusedError("database unavailable")
        pool = FakePool()
        pool.rows = self.rows
        self.pools.append(pool)
        return pool


class StartupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """Blocker 2: a database outage at startup does not disable the collector."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        patches = [patch.object(tdc, "ASYNCPG_AVAILABLE", True),
                   patch.object(tdc, "INIT_RETRY_INITIAL_SECONDS", 0.01),
                   patch.object(tdc, "INIT_RETRY_MAX_SECONDS", 0.02)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def collector(self, rec):
        collector = TimescaleDataCollector(database_url="postgresql://synthetic", telemetry_recorder=rec)
        collector.collect_data = AsyncMock(return_value={"status": "success"})
        return collector

    async def run_until(self, collector, predicate, stop):
        async def stopper():
            while not predicate():
                await asyncio.sleep(0.005)
            stop.set()

        await asyncio.wait_for(asyncio.gather(
            tdc.timescale_collection_loop(collector, DevMgr(), stop), stopper()), timeout=5)

    async def test_startup_outage_retries_reconnects_and_drains_retained_events(self):
        rec = AdaptiveTelemetryRecorder()
        collector = self.collector(rec)
        fake = _FakeAsyncpg(failures=2)
        # A transition observed while the database is unavailable stays queued.
        rec.observe(dd(), received_at=self.now)
        rec.observe(dd(working_state="Invert Mode"), received_at=self.now + timedelta(minutes=1))
        (event,) = rec.pending()
        statuses = []
        original = collector.initialize

        async def initialize():
            ok = await original()
            statuses.append((ok, rec.persistence_status, len(rec.pending())))
            return ok

        collector.initialize = initialize
        stop = asyncio.Event()
        with patch.object(tdc, "asyncpg", fake, create=True):
            await self.run_until(collector, lambda: rec.events_persisted >= 1, stop)
        self.assertEqual(fake.calls, 3)
        self.assertEqual(statuses[:2], [(False, "unavailable", 1)] * 2)
        self.assertTrue(statuses[2][0])
        (pool,) = fake.pools
        self.assertEqual(pool.stored_event_ids(), [event.event_id])  # exactly once
        self.assertEqual((rec.events_persisted, rec.events_reconciled_existing, rec.queue_drops), (1, 0, 0))
        self.assertEqual(rec.pending(), ())
        collector.collect_data.assert_awaited()  # collection resumed without an app restart

    async def test_half_initialised_pool_is_closed_before_retry(self):
        rec = AdaptiveTelemetryRecorder()
        collector = self.collector(rec)
        fake = _FakeAsyncpg(failures=0)
        broken = []
        original_create = fake.create_pool

        async def create_pool(*args, **kwargs):
            pool = await original_create(*args, **kwargs)
            if not broken:
                pool.fail = True  # table creation fails on the first pool
                broken.append(pool)
            return pool

        fake.create_pool = create_pool
        stop = asyncio.Event()
        with patch.object(tdc, "asyncpg", fake, create=True):
            await self.run_until(collector, lambda: collector._is_initialized, stop)
        self.assertTrue(broken[0].closed)
        self.assertEqual(fake.calls, 2)

    async def test_backoff_is_bounded_exponential_and_not_a_busy_loop(self):
        collector = self.collector(AdaptiveTelemetryRecorder())
        collector.initialize = AsyncMock(return_value=False)
        waits = []
        stop = asyncio.Event()

        async def fake_wait(event, seconds):
            waits.append(seconds)
            return len(waits) >= 9

        with patch.object(tdc, "INIT_RETRY_INITIAL_SECONDS", 5.0), \
                patch.object(tdc, "INIT_RETRY_MAX_SECONDS", 300.0), \
                patch.object(tdc, "_wait_for_stop", side_effect=fake_wait):
            self.assertFalse(await tdc.initialize_with_retry(collector, stop))
        self.assertEqual(waits, [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0, 300.0])
        self.assertEqual(collector.initialize.await_count, 9)

    async def test_shutdown_during_backoff_exits_promptly(self):
        rec = AdaptiveTelemetryRecorder()
        collector = self.collector(rec)
        collector.initialize = AsyncMock(return_value=False)
        stop = asyncio.Event()
        with patch.object(tdc, "INIT_RETRY_INITIAL_SECONDS", 60.0):
            task = asyncio.create_task(tdc.timescale_collection_loop(collector, DevMgr(), stop))
            await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(task, timeout=1)
        self.assertEqual(collector.initialize.await_count, 1)
        collector.collect_data.assert_not_awaited()
        self.assertTrue(rec.writer_attached)  # recoverable: not disabled, just stopped

    async def test_cancellation_during_backoff_stops_cleanly(self):
        collector = self.collector(AdaptiveTelemetryRecorder())
        collector.initialize = AsyncMock(return_value=False)
        collector.close = AsyncMock()
        with patch.object(tdc, "INIT_RETRY_INITIAL_SECONDS", 60.0):
            task = asyncio.create_task(tdc.timescale_collection_loop(collector, DevMgr(), asyncio.Event()))
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)
        collector.close.assert_awaited_once()

    async def test_unrecoverable_configuration_disables_persistence_explicitly(self):
        rec = AdaptiveTelemetryRecorder()
        collector = TimescaleDataCollector(database_url=None, telemetry_recorder=rec)
        with patch.dict("os.environ", {}, clear=True):
            collector.database_url = None
        rec.observe(dd(), received_at=self.now)
        rec.observe(dd(working_state="Invert Mode"), received_at=self.now + timedelta(minutes=1))
        await asyncio.wait_for(tdc.timescale_collection_loop(collector, DevMgr(), asyncio.Event()), timeout=1)
        self.assertEqual((rec.writer_attached, rec.persistence_status, rec.queue_drops), (False, "disabled", 1))
        self.assertEqual(rec.pending(), ())

    async def test_monitor_side_observation_never_waits_for_the_database(self):
        rec = AdaptiveTelemetryRecorder()
        collector = self.collector(rec)
        collector.initialize = AsyncMock(return_value=False)
        stop = asyncio.Event()
        with patch.object(tdc, "INIT_RETRY_INITIAL_SECONDS", 60.0):
            task = asyncio.create_task(tdc.timescale_collection_loop(collector, DevMgr(), stop))
            await asyncio.sleep(0.01)
            started = time.perf_counter()
            for minute in range(50):
                rec.observe(dd(working_state="Invert Mode" if minute % 2 else "Line Mode"),
                            received_at=self.now + timedelta(minutes=minute))
            self.assertLess(time.perf_counter() - started, 0.5)
            stop.set()
            await asyncio.wait_for(task, timeout=1)
        self.assertEqual(len(rec.pending(1000)), 49)



class LoopFaultIsolationTests(unittest.IsolatedAsyncioTestCase):
    """Recoverable collection/database errors never end the event persistence worker."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        patches = [patch.object(tdc, "ASYNCPG_AVAILABLE", True),
                   patch.object(tdc, "INIT_RETRY_INITIAL_SECONDS", 0.01),
                   patch.object(tdc, "INIT_RETRY_MAX_SECONDS", 0.02),
                   patch.object(tdc, "LOOP_RETRY_INITIAL_SECONDS", 0.01),
                   patch.object(tdc, "LOOP_RETRY_MAX_SECONDS", 0.04),
                   patch.object(tdc, "EVENT_FLUSH_INTERVAL_SECONDS", 0.005),
                   patch.object(tdc, "PERSIST_RETRY_SECONDS", 0.01)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.rec = AdaptiveTelemetryRecorder()
        self.fake = _FakeAsyncpg()
        asyncpg_patch = patch.object(tdc, "asyncpg", self.fake, create=True)
        asyncpg_patch.start()
        self.addCleanup(asyncpg_patch.stop)
        self.collector = TimescaleDataCollector(database_url="postgresql://synthetic", telemetry_recorder=self.rec)
        self.collector._collect_weather_data = AsyncMock()
        self.minute = 0

    def transition(self):
        """One observed Line/Invert change (a new queued event)."""
        self.minute += 1
        mode = "Invert Mode" if self.minute % 2 else "Line Mode"
        if self.rec.latest_received_at is None:
            self.rec.observe(dd(timestamp="base"), received_at=self.now)
        self.rec.observe(dd(working_state=mode, timestamp="m%d" % self.minute),
                         received_at=self.now + timedelta(minutes=self.minute))
        return self.rec.pending(1000)[-1]

    def stored_ids(self):
        rows = self.fake.rows["power_mode_events"]
        return [json.loads(rows[k][4])["event_id"] for k in sorted(rows)]

    async def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("condition not reached")
            await asyncio.sleep(0.005)

    def start(self):
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(tdc.timescale_collection_loop(self.collector, DevMgr(), self.stop))

    async def finish(self):
        self.stop.set()
        await asyncio.wait_for(self.task, timeout=2)

    async def test_collect_data_raises_once_then_succeeds(self):
        real_collect = self.collector.collect_data
        calls = []

        async def flaky(dev_mgr):
            calls.append(time.monotonic())
            if len(calls) == 1:
                raise RuntimeError("unexpected collection bug")
            return await real_collect(dev_mgr)

        self.collector.collect_data = flaky
        first = self.transition()
        with self.assertLogs(tdc.logger, "WARNING") as logs:
            self.start()
            await self.wait_for(lambda: len(calls) >= 2)
            await self.wait_for(lambda: self.rec.events_persisted >= 1)
            second = self.transition()  # the worker is still alive and flushing
            await self.wait_for(lambda: self.rec.events_persisted >= 2)
            await self.finish()
        self.assertEqual(self.stored_ids(), [first.event_id, second.event_id])
        self.assertGreaterEqual(calls[1] - calls[0], 0.009)  # backed off, not immediate
        self.assertTrue(any("telemetry collection failed (RuntimeError)" in m for m in logs.output))
        self.assertTrue(self.rec.writer_attached)
        self.assertEqual(self.rec.queue_drops, 0)

    async def test_reviewer_reproduction_collect_always_raising_still_flushes_events(self):
        self.collector.collect_data = AsyncMock(side_effect=RuntimeError("boom"))
        self.start()
        await self.wait_for(lambda: self.collector.collect_data.await_count >= 2)
        event = self.transition()
        await self.wait_for(lambda: self.rec.events_persisted == 1)
        self.assertEqual(self.rec.persistence_status, "available")  # a verified write since
        await self.finish()
        self.assertEqual(self.stored_ids(), [event.event_id])
        self.assertTrue(self.rec.writer_attached)

    async def test_database_fails_mid_operation_recovers_and_persists_exactly_once(self):
        self.start()
        first = self.transition()
        await self.wait_for(lambda: self.rec.events_persisted == 1)
        broken = self.fake.pools[0]
        # Commit applied, reply lost, connection gone; reconnects refused for a while.
        broken.lose_commit_ack = True
        broken.break_after_lost_ack = True
        self.fake.down = True
        during = [self.transition()]
        await self.wait_for(lambda: self.collector.pool is None)  # broken pool discarded
        self.assertTrue(broken.closed)
        self.assertEqual(self.rec.persistence_status, "degraded")
        self.assertEqual(self.rec.events_persisted, 1)  # the uncertain write was NOT acknowledged
        calls_while_down = self.fake.calls
        await self.wait_for(lambda: self.fake.calls >= calls_while_down + 2)  # reconnect retried
        during.append(self.transition())  # more events while recovering
        during.append(self.transition())
        self.assertEqual(self.rec.events_persisted, 1)
        self.fake.down = False
        await self.wait_for(lambda: self.rec.events_persisted == 4)
        await self.finish()
        self.assertEqual(self.stored_ids(), [first.event_id] + [e.event_id for e in during])  # once, in order
        self.assertEqual(self.rec.events_reconciled_existing, 1)  # the lost-ack row was recognised
        self.assertEqual(len(self.fake.rows["inverter_metrics"]), 4)
        self.assertEqual((self.rec.queue_drops, self.rec.pending()), (0, ()))
        self.assertGreaterEqual(len(self.fake.pools), 2)  # pool recreated, no app restart

    async def test_overflow_while_recovering_reports_actual_losses(self):
        rec = AdaptiveTelemetryRecorder(queue_size=2)
        self.rec = rec
        self.collector = TimescaleDataCollector(database_url="postgresql://synthetic", telemetry_recorder=rec)
        self.collector._collect_weather_data = AsyncMock()
        self.fake.down = True
        self.start()
        await self.wait_for(lambda: self.fake.calls >= 2)
        events = [self.transition() for _ in range(4)]
        self.assertEqual(rec.queue_drops, 2)
        self.fake.down = False
        await self.wait_for(lambda: rec.events_persisted == 2)
        await self.finish()
        self.assertEqual(self.stored_ids(), [e.event_id for e in events[2:]])

    async def test_failed_writes_never_acknowledge_while_persistently_failing(self):
        self.start()
        await self.wait_for(lambda: self.fake.pools)
        self.fake.pools[0].zero_rows = True  # every INSERT reports INSERT 0 0
        events = [self.transition(), self.transition()]
        await self.wait_for(lambda: self.collector.persist_failures >= 3)
        self.assertEqual(self.rec.events_persisted, 0)
        self.assertEqual([e.event_id for e in self.rec.pending()], [e.event_id for e in events])
        self.assertEqual(self.rec.persistence_status, "degraded")
        self.fake.pools[0].zero_rows = False
        await self.wait_for(lambda: self.rec.events_persisted == 2)
        await self.finish()
        self.assertEqual(self.stored_ids(), [e.event_id for e in events])

    async def test_persistent_failures_are_not_a_busy_loop_and_logs_are_bounded(self):
        self.collector.collect_data = AsyncMock(side_effect=RuntimeError("always"))
        with patch.object(tdc, "LOOP_FAULT_LOG_INTERVAL_SECONDS", 60.0), \
                self.assertLogs(tdc.logger, "WARNING") as logs:
            self.start()
            await asyncio.sleep(0.5)
            await self.finish()
        # Back-off 0.01 -> 0.04 s cap: at most ~0.5/0.04 + ramp attempts, never thousands.
        self.assertLessEqual(self.collector.collect_data.await_count, 20)
        self.assertGreaterEqual(self.collector.collect_data.await_count, 3)
        collection_logs = [m for m in logs.output if "telemetry collection" in m]
        self.assertEqual(len(collection_logs), 1)  # one per fault streak within the log interval

    async def test_shutdown_during_retry_exits_cleanly(self):
        self.collector.collect_data = AsyncMock(side_effect=RuntimeError("always"))
        with patch.object(tdc, "LOOP_RETRY_INITIAL_SECONDS", 60.0):
            self.start()
            await self.wait_for(lambda: self.collector.collect_data.await_count == 1)
            await asyncio.sleep(0.02)
            started = time.monotonic()
            await self.finish()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.collector.collect_data.await_count, 1)
        self.assertEqual(self.rec.persistence_status, "unavailable")  # stopped, not failed
        self.assertTrue(self.fake.pools[0].closed)

    async def test_cancellation_is_propagated_not_swallowed(self):
        self.collector.collect_data = AsyncMock(side_effect=RuntimeError("always"))
        with patch.object(tdc, "LOOP_RETRY_INITIAL_SECONDS", 60.0):
            self.start()
            await self.wait_for(lambda: self.collector.collect_data.await_count == 1)
            self.task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(self.task, timeout=1)
        self.assertTrue(self.fake.pools[0].closed)

    async def test_unexpected_loop_exit_reports_disabled_not_active(self):
        async def broken_fault_handler(collector):
            raise RuntimeError("defect outside the guarded calls")

        self.collector.collect_data = AsyncMock(side_effect=RuntimeError("x"))
        self.transition()
        with patch.object(tdc, "_after_fault", broken_fault_handler):
            self.start()
            await asyncio.wait_for(self.task, timeout=2)
        self.assertFalse(self.rec.writer_attached)
        self.assertEqual(self.rec.persistence_status, "disabled")
        self.assertEqual(self.rec.queue_drops, 1)  # the retained event is reported lost
        self.assertEqual(self.rec.pending(), ())

    async def test_long_failure_streak_does_not_terminate_worker_or_lose_events(self):
        real_backoff = tdc._FaultBackoff

        class LongStreak(real_backoff):
            """A streak that has already failed ~a million times (about 9.5 years at 300 s)."""

            def __init__(self, name):
                super().__init__(name)
                self.consecutive = 999_999

        self.collector.collect_data = AsyncMock(side_effect=RuntimeError("still failing"))
        first = self.transition()
        with patch.object(tdc, "_FaultBackoff", LongStreak):
            self.start()
            await self.wait_for(lambda: self.collector.collect_data.await_count >= 2)
            second = self.transition()
            await self.wait_for(lambda: self.rec.events_persisted == 2)
            self.assertFalse(self.task.done())
            await self.finish()
        self.assertEqual(self.stored_ids(), [first.event_id, second.event_id])
        self.assertTrue(self.rec.writer_attached)
        self.assertEqual((self.rec.queue_drops, self.rec.pending()), (0, ()))
        self.assertNotEqual(self.rec.persistence_status, "disabled")

    async def test_interval_evaluation_error_does_not_end_the_loop(self):
        self.collector.get_current_interval = Mock(side_effect=ValueError("bad shared_state"))
        self.collector.collect_data = AsyncMock(return_value={"status": "success"})
        self.start()
        event = self.transition()
        await self.wait_for(lambda: self.rec.events_persisted == 1)
        await self.finish()
        self.assertEqual(self.stored_ids(), [event.event_id])


class FaultBackoffTests(unittest.TestCase):
    def test_backoff_is_bounded_and_logs_are_rate_limited(self):
        backoff = tdc._FaultBackoff("x")
        clock, delays = 0.0, []
        with self.assertLogs(tdc.logger, "WARNING") as logs:
            for _ in range(200):  # ~16 hours of persistent failure
                delays.append(backoff.failure(RuntimeError("down"), clock))
                clock = backoff.retry_at
        self.assertEqual(delays[:7], [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0])
        self.assertEqual(max(delays), tdc.LOOP_RETRY_MAX_SECONDS)
        # 1 streak-start log + at most one summary per 300 s of failures.
        self.assertLessEqual(len(logs.output), 1 + clock / tdc.LOOP_FAULT_LOG_INTERVAL_SECONDS + 1)
        self.assertLess(len(logs.output), 200)
        backoff.success()
        self.assertEqual((backoff.consecutive, backoff.retry_at), (0, 0.0))

    def test_delay_for_large_failure_counts_never_overflows(self):
        # Production policy 5 s doubling to a 300 s cap; failure 1025 used to raise
        # OverflowError (5.0 * 2 ** 1024 cannot be converted to float).
        expected = {1: 5.0, 7: 300.0, 1025: 300.0, 1_000_000: 300.0}
        for count, delay in expected.items():
            with self.subTest(failure_count=count):
                backoff = tdc._FaultBackoff("x")
                backoff.consecutive = count - 1
                with self.assertLogs(tdc.logger, "WARNING"):
                    self.assertEqual(backoff.failure(RuntimeError("down"), 1000.0), delay)
                self.assertEqual(backoff.consecutive, count)
                self.assertEqual(backoff.retry_at, 1000.0 + delay)

    def test_streak_of_a_million_failures_keeps_the_policy_sequence(self):
        backoff = tdc._FaultBackoff("x")
        delays = []
        with self.assertLogs(tdc.logger, "WARNING"):
            for _ in range(8):
                delays.append(backoff.failure(None, 0.0))
            backoff.consecutive = 999_999
            delays.append(backoff.failure(None, 0.0))
        self.assertEqual(delays, [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0, 300.0])



# ---------------------------------------------------------------------------
# Real PostgreSQL (opt-in). Run only under the disposable-container harness:
#   TIMESCALE_COLLECTOR_LOCAL_POSTGRES=1 python tests/disposable_postgres.py run -- \
#       python -m unittest test_timescale_data_collector.RealPostgresCollectorTests
# Every connection goes through tests/pg_isolation.py (provisioned DSN only,
# verified run ownership). The harness image is plain PostgreSQL 16 without the
# timescaledb extension: the real _create_tables() DDL runs, but CREATE
# EXTENSION timescaledb is skipped by the shim and create_hypertable() fails
# inside its existing try/except, so the tables are ordinary tables.
# ---------------------------------------------------------------------------
REAL_PG = os.environ.get("TIMESCALE_COLLECTOR_LOCAL_POSTGRES") == "1"
COLLECTOR_TABLES = ("power_mode_events", "inverter_metrics", "device_metrics", "weather_data")


class _ShimConn:
    """Real asyncpg connection; skips the timescaledb extension and can lose a COMMIT reply."""

    def __init__(self, conn, shim):
        self._conn, self._shim, self._depth = conn, shim, 0

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def execute(self, sql, *args):
        if "CREATE EXTENSION IF NOT EXISTS timescaledb" in sql:
            self._shim.skipped_extension = True
            return "CREATE EXTENSION"
        return await self._conn.execute(sql, *args)

    @asynccontextmanager
    async def transaction(self):
        tx = self._conn.transaction()
        await tx.start()
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            try:
                await tx.rollback()
            except Exception:  # noqa: BLE001 - connection may already be gone
                pass
            raise
        self._depth -= 1
        await tx.commit()  # really committed on the server
        if self._depth == 0 and self._shim.lose_next_commit_reply:
            # Lost acknowledgement: the transaction is durable, but the client
            # connection dies and the writer only ever sees an error.
            self._shim.lose_next_commit_reply = False
            self._conn.terminate()
            raise ConnectionResetError("connection lost before COMMIT acknowledgement")


class _ShimPool:
    def __init__(self, pool, shim):
        self._pool, self._shim = pool, shim

    @asynccontextmanager
    async def acquire(self):
        async with self._pool.acquire() as conn:
            yield _ShimConn(conn, self._shim)

    async def close(self):
        await self._pool.close()

    def terminate(self):
        self._pool.terminate()


class _GuardedAsyncpg:
    """asyncpg stand-in whose pools are pg_isolation-verified pools to the disposable DB."""

    def __init__(self):
        self.lose_next_commit_reply = False
        self.skipped_extension = False
        self.pools_created = 0
        self.create_failures = 0

    async def create_pool(self, dsn, min_size=1, max_size=4, command_timeout=None):
        import pg_isolation  # noqa: PLC0415 - only for the opt-in suite
        try:
            pool = await pg_isolation.create_pool(dsn, min_size=1, max_size=4, command_timeout=command_timeout)
        except Exception:
            self.create_failures += 1
            raise
        self.pools_created += 1
        return _ShimPool(pool, self)


@unittest.skipUnless(REAL_PG, "requires disposable local PostgreSQL (TIMESCALE_COLLECTOR_LOCAL_POSTGRES=1)")
class RealPostgresCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import pg_isolation  # noqa: PLC0415
        self.pg = pg_isolation
        self.dsn = pg_isolation.configured_dsn()
        self.control = await pg_isolation.connect(self.dsn)
        await self.drop_tables()
        self.shim = _GuardedAsyncpg()
        patches = [patch.object(tdc, "asyncpg", self.shim, create=True),
                   patch.object(tdc, "ASYNCPG_AVAILABLE", True),
                   patch.object(tdc, "INIT_RETRY_INITIAL_SECONDS", 0.05),
                   patch.object(tdc, "INIT_RETRY_MAX_SECONDS", 0.2),
                   patch.object(tdc, "LOOP_RETRY_INITIAL_SECONDS", 0.05),
                   patch.object(tdc, "LOOP_RETRY_MAX_SECONDS", 0.2),
                   patch.object(tdc, "EVENT_FLUSH_INTERVAL_SECONDS", 0.02),
                   patch.object(tdc, "PERSIST_RETRY_SECONDS", 0.05)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.now = datetime.now(timezone.utc)
        self.rec = AdaptiveTelemetryRecorder()
        self.collector = TimescaleDataCollector(database_url=self.dsn, telemetry_recorder=self.rec)
        self.collector._collect_weather_data = AsyncMock()
        self.minute = 0

    async def asyncTearDown(self):
        await self.set_connections(True)
        if self.collector.pool is not None:
            await self.collector.pool.close()
        await self.drop_tables()
        await self.control.close()

    async def drop_tables(self):
        for table in COLLECTOR_TABLES:
            await self.control.execute("DROP TABLE IF EXISTS %s" % table)

    def transition(self):
        self.minute += 1
        if self.rec.latest_received_at is None:
            self.rec.observe(dd(timestamp="base"), received_at=self.now)
        mode = "Invert Mode" if self.minute % 2 else "Line Mode"
        self.rec.observe(dd(working_state=mode, timestamp="m%d" % self.minute, output_power=100.0 * self.minute),
                         received_at=self.now + timedelta(minutes=self.minute))
        return self.rec.pending(1000)[-1]

    async def stored(self):
        rows = await self.control.fetch(
            "SELECT time, metadata->>'event_id' AS event_id FROM power_mode_events "
            "WHERE metadata ? 'event_id' ORDER BY time")
        return [r["event_id"] for r in rows], [r["time"] for r in rows]

    async def metrics_at(self, times):
        return await self.control.fetchval("SELECT count(*) FROM inverter_metrics WHERE time = ANY($1)", times)

    async def set_connections(self, allowed):
        # New logins for the test role are refused (applies to superusers too);
        # this control session stays connected. The container is disposable.
        role = await self.control.fetchval("SELECT quote_ident(current_user)")
        await self.control.execute("ALTER ROLE %s %s" % (role, "LOGIN" if allowed else "NOLOGIN"))
        if not allowed:
            # Drop every other session on this database (the collector's pool).
            await self.control.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()")

    async def wait_for(self, predicate, timeout=10.0, what="condition"):
        deadline = time.monotonic() + timeout
        while not (await predicate() if asyncio.iscoroutinefunction(predicate) else predicate()):
            if time.monotonic() > deadline:
                self.fail("%s not reached; pool=%s initialized=%s status=%s persisted=%s pending=%s "
                          "persist_failures=%s outcome=%s error=%s create_failures=%s pools=%s" % (
                              what, self.collector.pool is not None, self.collector._is_initialized,
                              self.rec.persistence_status, self.rec.events_persisted, len(self.rec.pending()),
                              self.collector.persist_failures, self.collector.last_persist_outcome,
                              self.collector.last_persist_error, self.shim.create_failures,
                              self.shim.pools_created))
            await asyncio.sleep(0.02)

    async def test_real_initialise_and_verified_writes(self):
        self.assertTrue(await self.collector.initialize())
        self.assertTrue(self.shim.skipped_extension)
        events = [self.transition(), self.transition()]
        self.assertEqual(await self.collector.persist_pending_events(), 2)
        ids, times = await self.stored()
        self.assertEqual(ids, [e.event_id for e in events])
        self.assertEqual(await self.metrics_at(times), 2)
        # Same event again (as after a lost acknowledgement): zero-row INSERT, own row recognised.
        self.collector._event_keys[events[0].event_id] = times[0]
        async with self.collector.pool.acquire() as conn:
            from app.service.battery_charge_controller import charger_mode  # noqa: PLC0415
            async with conn.transaction():
                self.assertEqual(await self.collector._write_event(conn, events[0], charger_mode), "existing")
        self.assertEqual((await self.stored())[0], ids)

    async def test_real_lost_commit_acknowledgement_reconnects_and_persists_exactly_once(self):
        self.assertTrue(await self.collector.initialize())
        events = [self.transition(), self.transition()]
        self.shim.lose_next_commit_reply = True
        self.assertEqual(await self.collector.persist_pending_events(), 0)
        # Committed on the server but not acknowledged by the writer.
        ids, times = await self.stored()
        self.assertEqual(ids, [e.event_id for e in events])
        self.assertEqual(self.rec.events_persisted, 0)
        self.assertEqual([e.event_id for e in self.rec.pending()], ids)
        self.assertTrue(await self.collector.connection_healthy())  # pool replaces the dead connection
        self.collector._persist_retry_at = 0
        self.assertEqual(await self.collector.persist_pending_events(), 2)
        self.assertEqual((await self.stored())[0], ids)  # no duplicates
        self.assertEqual(await self.metrics_at(times), 2)
        self.assertEqual((self.rec.events_persisted, self.rec.events_reconciled_existing), (2, 2))

    async def test_real_outage_mid_operation_loop_recovers_without_restart(self):
        stop = asyncio.Event()
        loop_task = asyncio.create_task(tdc.timescale_collection_loop(self.collector, DevMgr(), stop))
        try:
            first = self.transition()
            await self.wait_for(lambda: self.rec.events_persisted == 1, what="first write")
            # Database refuses connections and the collector's sessions are killed mid-operation.
            await self.set_connections(False)
            during = [self.transition()]
            await self.wait_for(lambda: self.collector.pool is None, what="pool discarded")
            self.assertEqual(self.rec.persistence_status, "degraded")
            failures = self.shim.create_failures
            await self.wait_for(lambda: self.shim.create_failures >= failures + 2, what="refused reconnects")  # real reconnects refused
            during += [self.transition(), self.transition()]
            self.assertEqual(self.rec.events_persisted, 1)
            await self.set_connections(True)
            await self.wait_for(lambda: self.rec.events_persisted == 4, what="recovery drain")
            self.assertFalse(loop_task.done())
        finally:
            stop.set()
            await asyncio.wait_for(loop_task, timeout=10)
        ids, times = await self.stored()
        self.assertEqual(ids, [first.event_id] + [e.event_id for e in during])  # exactly once, in order
        self.assertEqual(await self.metrics_at(times), 4)
        self.assertEqual((self.rec.queue_drops, self.rec.pending()), (0, ()))
        self.assertGreaterEqual(self.shim.pools_created, 2)

    async def test_real_events_arriving_during_a_write_are_kept(self):
        self.assertTrue(await self.collector.initialize())
        first = self.transition()
        # Hold a lock that conflicts with INSERT so the writer blocks mid-write.
        lock = self.control.transaction()
        await lock.start()
        await self.control.execute("LOCK TABLE power_mode_events IN SHARE ROW EXCLUSIVE MODE")
        writer = asyncio.create_task(self.collector.persist_pending_events())
        await asyncio.sleep(0.2)
        self.assertFalse(writer.done())
        late = self.transition()  # arrives while the write is in flight
        await lock.rollback()
        self.assertEqual(await asyncio.wait_for(writer, timeout=10), 1)
        self.assertEqual([e.event_id for e in self.rec.pending()], [late.event_id])
        self.assertEqual(await self.collector.persist_pending_events(), 1)
        self.assertEqual((await self.stored())[0], [first.event_id, late.event_id])


if __name__ == "__main__":
    unittest.main()
