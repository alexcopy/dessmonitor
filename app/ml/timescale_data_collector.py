#!/usr/bin/env python3
"""
TimescaleDB Data Collector для солнечной системы
Адаптивный сбор данных: интенсивный при работе от инвертора, редкий от сети
"""

import asyncio
import logging
import os
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Dict, Any, Optional, Literal
from enum import Enum
import json

from app.control.adaptive_telemetry import AdaptiveTelemetryRecorder, MAX_WRITE_BATCH

# Back-off after a failed telemetry-event write; events stay in the bounded queue.
PERSIST_RETRY_SECONDS = 30
# A persistence key held by a different row is re-keyed at most this often per
# event and attempt; anything still unverified rolls the batch back for retry.
EVENT_KEY_COLLISION_ATTEMPTS = 3
# Database initialisation retry: bounded exponential back-off while the writer
# stays attached. Retries continue until shutdown; no busy loop.
INIT_RETRY_INITIAL_SECONDS = 5.0
INIT_RETRY_MAX_SECONDS = 300.0
# Collection-loop fault isolation: a failing collection, flush or reconnect
# backs off 5 s -> ... -> 300 s (cap). A fault streak logs its first failure
# with a traceback, then at most one summary per LOOP_FAULT_LOG_INTERVAL_SECONDS.
LOOP_RETRY_INITIAL_SECONDS = 5.0
LOOP_RETRY_MAX_SECONDS = 300.0
LOOP_FAULT_LOG_INTERVAL_SECONDS = 300.0
LOOP_RETRY_EXPONENT_CAP = 30
CONNECTION_CHECK_TIMEOUT_SECONDS = 10.0
# With the recorder the loop wakes this often to flush observed changes; it
# performs no DESS request (DESS polling stays in InverterMonitor, 60 s).
EVENT_FLUSH_INTERVAL_SECONDS = 1.0

_PME_INSERT = """INSERT INTO power_mode_events
                 (time, from_mode, to_mode, battery_soc, metadata)
                 VALUES ($1, $2, $3, $4, $5)
                 ON CONFLICT (time) DO NOTHING"""
_PME_EVENT_ID_AT = "SELECT metadata->>'event_id' FROM power_mode_events WHERE time = $1"
_EVENT_METRICS_INSERT = """INSERT INTO inverter_metrics (
                 time, battery_voltage, battery_soc,
                 battery_current_dis, battery_current_chg,
                 pv_power_w, output_power_w, output_voltage,
                 ac_input_voltage, ac_input_frequency, ac_output_load_pct,
                 total_load_w, working_mode, charger_priority, charger_priority_raw,
                 output_priority_raw, mains_status
             ) VALUES (
                 $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17
             ) ON CONFLICT (time) DO NOTHING"""


class EventPersistenceUnverified(Exception):
    """An event's storage could not be confirmed; the batch is rolled back and retained."""


class _PersistenceKeyCollision(Exception):
    """The persistence key is held by a different row; roll back the savepoint and re-key."""


def inserted_rows(status: object) -> int | None:
    """Rows inserted according to a PostgreSQL command tag ("INSERT 0 <n>").

    Returns None when the tag is missing or not an INSERT tag, so an
    unrecognised result is never mistaken for a successful write.
    """
    if not isinstance(status, str):
        return None
    parts = status.split()
    if len(parts) != 3 or parts[0] != "INSERT":
        return None
    try:
        rows = int(parts[2])
    except ValueError:
        return None
    return rows if rows >= 0 else None


try:
    import asyncpg

    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False
    logging.warning("asyncpg not installed. Install: pip install asyncpg")

logger = logging.getLogger(__name__)


class PowerMode(Enum):
    """Режим питания системы"""
    INVERTER = "inverter"  # От солнца/инвертора
    GRID = "grid"  # От сети
    SWITCHING = "switching"  # Переключение режима
    UNKNOWN = "unknown"  # Неизвестно


class TimescaleDataCollector:
    """
    Умный коллектор данных для солнечной системы.
    Адаптирует частоту сбора в зависимости от режима работы.
    """

    def __init__(
            self,
            # Интервалы для разных режимов (секунды)
            inverter_interval: int = 300,  # 5 минут при работе от солнца
            grid_interval: int = 900,  # 15 минут при стабильной сети ночью
            switching_interval: int = 10,  # 10 секунд при переключении
            # Параметры определения режима
            min_inverter_power: float = 50.0,  # Минимальная мощность для режима "инвертор"
            sunrise_hour: int = 6,  # Начало возможной работы от солнца
            sunset_hour: int = 20,  # Конец работы от солнца
            database_url: Optional[str] = None,
            telemetry_recorder: AdaptiveTelemetryRecorder | None = None,
    ):
        """
        Args:
            inverter_interval: Интервал сбора при работе от инвертора (сек)
            grid_interval: Интервал сбора при работе от сети (сек)
            switching_interval: Интервал при переключении режимов (сек)
            min_inverter_power: Минимальная мощность инвертора для определения режима
            sunrise_hour: Час восхода солнца (примерно)
            sunset_hour: Час захода солнца (примерно)
            database_url: URL подключения к PostgreSQL/TimescaleDB
        """
        self.intervals = {
            PowerMode.INVERTER: inverter_interval,
            PowerMode.GRID: grid_interval,
            PowerMode.SWITCHING: switching_interval,
            PowerMode.UNKNOWN: grid_interval,
        }

        self.min_inverter_power = min_inverter_power
        self.sunrise_hour = sunrise_hour
        self.sunset_hour = sunset_hour
        self.database_url = (
            database_url or
            os.getenv("DATABASE_URL")
        )

        self.pool: Optional[asyncpg.Pool] = None
        self._total_records = 0
        self._last_collection = None
        self._is_initialized = False

        # Состояние режима
        self._current_mode = PowerMode.UNKNOWN
        self._previous_mode = PowerMode.UNKNOWN
        self._mode_change_count = 0
        self._switching_detected_at = None
        self._last_weather_write: float = 0.0  # throttle weather writes
        self._charge_detail_until: float = 0.0
        self.telemetry_recorder = telemetry_recorder
        self._last_event_time: datetime | None = None
        self._persist_retry_at: float = 0.0
        # Incremented on every failed event-write attempt; the loop uses it to
        # check (and if needed recreate) the connection pool.
        self.persist_failures = 0
        # Outcome of the last persist_pending_events() call: "written", "idle"
        # (nothing queued), "backoff", "failed", "unavailable" or "no_recorder".
        self.last_persist_outcome = "idle"
        self.last_persist_error: str | None = None
        # event_id -> stable persistence key for events not yet acknowledged.
        self._event_keys: dict[str, datetime] = {}
        if telemetry_recorder is not None:
            telemetry_recorder.attach_writer()

    def _next_event_time(self) -> datetime:
        """Unique, monotonically increasing persistence time (power_mode_events PK)."""
        now = datetime.now(timezone.utc)
        if self._last_event_time is not None and now <= self._last_event_time:
            now = self._last_event_time + timedelta(microseconds=1)
        self._last_event_time = now
        return now

    def _event_key(self, event) -> datetime:
        """Stable persistence time (power_mode_events PK) for one queued event.

        Assigned on the first write attempt and reused on every retry, so a
        retry after a lost COMMIT acknowledgement finds the stored row by key
        instead of inserting a duplicate. It is the first persistence-attempt
        time, not a measurement time.
        """
        key = self._event_keys.get(event.event_id)
        if key is None:
            key = self._next_event_time()
            self._event_keys[event.event_id] = key
        return key

    def _prune_event_keys(self) -> None:
        """Keep keys only for events still queued (bounded by the queue size)."""
        recorder = self.telemetry_recorder
        if recorder is None:
            self._event_keys.clear()
            return
        queued = {event.event_id for event in recorder.pending(recorder.queue_size)}
        for event_id in [k for k in self._event_keys if k not in queued]:
            del self._event_keys[event_id]

    async def _write_event(self, conn, event, charger_mode) -> str:
        """Write one event inside the batch transaction and verify the result.

        Returns "inserted" or "existing" (this exact event is already stored at
        its key). Raises EventPersistenceUnverified otherwise.
        """
        needs_metrics = event.event_kind != "checkpoint" and bool(event.metrics)
        for _attempt in range(EVENT_KEY_COLLISION_ATTEMPTS):
            key = self._event_key(event)
            try:
                async with conn.transaction():  # savepoint: a collision undoes only this event
                    inserted = inserted_rows(await conn.execute(
                        _PME_INSERT, key, event.from_mode or "UNKNOWN", event.to_mode or "UNKNOWN",
                        event.battery_soc, json.dumps(event.metadata(key), separators=(",", ":")),
                    ))
                    if inserted == 1:
                        if needs_metrics:
                            m = event.metrics
                            metrics_inserted = inserted_rows(await conn.execute(
                                _EVENT_METRICS_INSERT,
                                key, m.get("battery_voltage"), m.get("battery_soc"),
                                m.get("battery_current_dis"), m.get("battery_current_chg"),
                                m.get("pv_power_w"), m.get("output_power_w"), m.get("output_voltage"),
                                m.get("ac_input_voltage"), m.get("ac_input_frequency"), m.get("ac_output_load_pct"),
                                m.get("total_load_w"), m.get("working_mode"),
                                charger_mode(m.get("charger_priority_raw")), m.get("charger_priority_raw"),
                                m.get("output_priority_raw"), m.get("mains_status"),
                            ))
                            if metrics_inserted == 0:
                                raise _PersistenceKeyCollision()
                            if metrics_inserted != 1:
                                raise EventPersistenceUnverified("inverter_metrics insert result unverified")
                        return "inserted"
                    if inserted != 0:
                        raise EventPersistenceUnverified("power_mode_events insert result unverified")
            except _PersistenceKeyCollision:
                self._event_keys[event.event_id] = self._next_event_time()
                continue
            # Zero rows: the key is taken. Only this event's own row (stored by an
            # earlier attempt whose acknowledgement was lost) counts as handled.
            # Its metrics row was committed in that same savepoint.
            if await conn.fetchval(_PME_EVENT_ID_AT, key) == event.event_id:
                return "existing"
            self._event_keys[event.event_id] = self._next_event_time()
        raise EventPersistenceUnverified("persistence key collisions not resolved")

    async def persist_pending_events(self) -> int:
        """Single writer for the bounded telemetry hand-off.

        Mode transitions and significant changes are written as soon as the
        loop runs (about every second), not at the next checkpoint. Each event
        gets a power_mode_events row with bounded metadata; observed changes
        also get an inverter_metrics row built from that response's own values.
        Checkpoint markers only add the power_mode_events row (the checkpoint's
        inverter_metrics row was written by collect_data). Never calls Journal v2.

        An event is acknowledged only after its INSERT reported one row, or a
        zero-row INSERT was confirmed to be this event's own earlier row. Any
        other outcome rolls the whole batch back and keeps it queued.
        """
        recorder = self.telemetry_recorder
        if recorder is None:
            self.last_persist_outcome = "no_recorder"
            return 0
        if not self.pool:
            recorder.persistence_status = "unavailable"
            self.last_persist_outcome = "unavailable"
            return 0
        if time.monotonic() < self._persist_retry_at:
            self.last_persist_outcome = "backoff"
            return 0
        pending = recorder.begin_write(MAX_WRITE_BATCH)
        if not pending:
            recorder.release_write()
            if recorder.persistence_status != "degraded":
                recorder.persistence_status = "available"
            self.last_persist_outcome = "idle"
            return 0
        from app.service.battery_charge_controller import charger_mode  # noqa: PLC0415
        handled: list[int] = []
        reconciled = 0
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    for event in pending:
                        if await self._write_event(conn, event, charger_mode) == "existing":
                            reconciled += 1
                        handled.append(event.seq)
        except Exception as exc:
            # Includes a failed/uncertain COMMIT: nothing is acknowledged. Keys
            # are kept so a retry recognises rows that were in fact committed.
            recorder.release_write()
            self._prune_event_keys()
            recorder.persistence_status = "degraded"
            self.persist_failures += 1
            self.last_persist_outcome = "failed"
            self.last_persist_error = type(exc).__name__
            self._persist_retry_at = time.monotonic() + PERSIST_RETRY_SECONDS
            # Rate-limited warnings are emitted by the collection loop; this is
            # per-attempt detail only.
            logger.debug("[TS] telemetry event persistence failed (%s); retry in %ss; %s events retained",
                         type(exc).__name__, PERSIST_RETRY_SECONDS, len(pending))
            return 0
        written = recorder.acknowledge(handled, reconciled=reconciled)
        self._prune_event_keys()
        recorder.persistence_status = "available"
        recorder.last_persisted_at = self._last_event_time
        self.last_persist_outcome = "written"
        self.last_persist_error = None
        return written

    async def initialize(self) -> bool:
        """Инициализация: подключение к БД и создание таблиц"""
        if not self.database_url:
            logger.error(
                "DATABASE_URL not provided. Set DATABASE_URL environment variable "
                "or pass database_url parameter to enable TimescaleDB collection."
            )
            return False

        if not ASYNCPG_AVAILABLE:
            logger.error("asyncpg not available - cannot connect to database")
            return False

        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
                command_timeout=60
            )

            await self._create_tables()
            self._total_records = await self._count_records()
            self._is_initialized = True

            logger.info(
                f"✅ TimescaleDB initialized. "
                f"Existing records: {self._total_records}"
            )
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize TimescaleDB: {e}")
            # Do not keep a half-initialised pool: a retry starts cleanly and
            # persist_pending_events() keeps reporting "unavailable" meanwhile.
            pool, self.pool = self.pool, None
            self._is_initialized = False
            if pool is not None:
                try:
                    await pool.close()
                except Exception as close_exc:  # noqa: BLE001 - best-effort cleanup
                    logger.debug("[TS] closing failed pool: %s", type(close_exc).__name__)
            if self.telemetry_recorder is not None and self.telemetry_recorder.writer_attached:
                self.telemetry_recorder.persistence_status = "unavailable"
            return False

    async def connection_healthy(self) -> bool:
        """Cheap read-only probe of the pool (SELECT 1) with a bounded timeout."""
        pool = self.pool
        if pool is None:
            return False
        try:
            async def probe():
                async with pool.acquire() as conn:
                    return await conn.fetchval("SELECT 1")
            return await asyncio.wait_for(probe(), timeout=CONNECTION_CHECK_TIMEOUT_SECONDS) == 1
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - any failure means "not healthy"
            return False

    async def reset_connection(self) -> None:
        """Discard a broken pool; the loop re-initialises it with back-off.

        Queued events and their stable persistence keys are kept, so rows that
        were committed before the break are recognised on retry, not duplicated.
        """
        pool, self.pool = self.pool, None
        self._is_initialized = False
        if pool is not None:
            # Only this loop uses the pool and it resets between operations, so
            # nothing is in flight: terminate at once. A graceful close() can
            # wait on a connection asyncpg left in a broken state.
            terminate = getattr(pool, "terminate", None)
            try:
                if callable(terminate):
                    terminate()
                else:
                    await asyncio.wait_for(pool.close(), timeout=CONNECTION_CHECK_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - best effort; the pool is discarded anyway
                logger.debug("[TS] discarding broken pool: %s", type(exc).__name__)
        if self.telemetry_recorder is not None and self.telemetry_recorder.writer_attached:
            self.telemetry_recorder.persistence_status = "degraded"

    def initialization_recoverable(self) -> bool:
        """False only for configuration that no retry can fix (no URL / no asyncpg)."""
        return bool(self.database_url) and ASYNCPG_AVAILABLE

    async def _create_tables(self) -> None:
        """Создание таблиц в TimescaleDB"""
        async with self.pool.acquire() as conn:
            # Включаем TimescaleDB extension
            await conn.execute(
                "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
            )

            # ========== DEVICE METRICS ==========
            await conn.execute("""
                               CREATE TABLE IF NOT EXISTS device_metrics
                               (
                                   time
                                   TIMESTAMPTZ
                                   NOT
                                   NULL,
                                   device_name
                                   TEXT
                                   NOT
                                   NULL,
                                   device_type
                                   TEXT,
                                   is_on
                                   BOOLEAN,
                                   power_watts
                                   DOUBLE
                                   PRECISION,
                                   energy_kwh
                                   DOUBLE
                                   PRECISION,
                                   temperature_celsius
                                   DOUBLE
                                   PRECISION,
                                   humidity_percent
                                   DOUBLE
                                   PRECISION,
                                   voltage
                                   DOUBLE
                                   PRECISION,
                                   current_amps
                                   DOUBLE
                                   PRECISION,
                                   power_mode
                                   TEXT,
                                   metadata
                                   JSONB,
                                   PRIMARY
                                   KEY
                               (
                                   time,
                                   device_name
                               )
                                   );
                               """)

            try:
                await conn.execute("""
                    SELECT create_hypertable(
                        'device_metrics', 
                        'time',
                        if_not_exists => TRUE,
                        chunk_time_interval => INTERVAL '1 day'
                    );
                """)
            except Exception as e:
                logger.debug(f"Hypertable creation: {e}")

            # Existing installations may predate the energy capability.
            await conn.execute("""
                ALTER TABLE device_metrics
                ADD COLUMN IF NOT EXISTS energy_kwh DOUBLE PRECISION;
            """)

            # ========== INVERTER METRICS ==========
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS inverter_metrics (
                    time                TIMESTAMPTZ NOT NULL,
                    battery_voltage     DOUBLE PRECISION,
                    battery_soc         DOUBLE PRECISION,
                    battery_current_dis DOUBLE PRECISION,
                    battery_current_chg DOUBLE PRECISION,
                    pv_power_w          DOUBLE PRECISION,
                    output_power_w      DOUBLE PRECISION,
                    output_voltage      DOUBLE PRECISION,
                    ac_input_voltage    DOUBLE PRECISION,
                    ac_input_frequency  DOUBLE PRECISION,
                    ac_output_load_pct  DOUBLE PRECISION,
                    total_load_w        DOUBLE PRECISION,
                    working_mode        TEXT,
                    charger_priority    TEXT,
                    charger_priority_raw TEXT,
                    output_priority_raw TEXT,
                    mains_status        TEXT,
                    PRIMARY KEY (time)
                );
            """)
            # Existing rows remain unknown; add nullable columns without backfill.
            await conn.execute("""
                ALTER TABLE inverter_metrics
                ADD COLUMN IF NOT EXISTS charger_priority TEXT;
                ALTER TABLE inverter_metrics
                ADD COLUMN IF NOT EXISTS charger_priority_raw TEXT;
                ALTER TABLE inverter_metrics
                ADD COLUMN IF NOT EXISTS output_priority_raw TEXT;
                ALTER TABLE inverter_metrics
                ADD COLUMN IF NOT EXISTS mains_status TEXT;
            """)
            try:
                await conn.execute("""
                    SELECT create_hypertable(
                        'inverter_metrics', 'time',
                        if_not_exists => TRUE,
                        chunk_time_interval => INTERVAL '1 day'
                    );
                """)
            except Exception as e:
                logger.debug(f"inverter_metrics hypertable: {e}")

            # ========== POWER MODE EVENTS ==========
            await conn.execute("""
                               CREATE TABLE IF NOT EXISTS power_mode_events
                               (
                                   time
                                   TIMESTAMPTZ
                                   NOT
                                   NULL,
                                   from_mode
                                   TEXT
                                   NOT
                                   NULL,
                                   to_mode
                                   TEXT
                                   NOT
                                   NULL,
                                   inverter_power
                                   DOUBLE
                                   PRECISION,
                                   grid_power
                                   DOUBLE
                                   PRECISION,
                                   battery_soc
                                   DOUBLE
                                   PRECISION,
                                   duration_seconds
                                   INTEGER,
                                   metadata
                                   JSONB,
                                   PRIMARY
                                   KEY
                               (
                                   time
                               )
                                   );
                               """)

            try:
                await conn.execute("""
                    SELECT create_hypertable(
                        'power_mode_events', 
                        'time',
                        if_not_exists => TRUE,
                        chunk_time_interval => INTERVAL '7 days'
                    );
                """)
            except Exception as e:
                logger.debug(f"Hypertable creation for events: {e}")

            # ========== 🆕 WEATHER DATA ==========
            await conn.execute("""
                               CREATE TABLE IF NOT EXISTS weather_data
                               (
                                   time
                                   TIMESTAMPTZ
                                   NOT
                                   NULL
                                   PRIMARY
                                   KEY,

                                   -- Текущее состояние
                                   ambient_temp
                                   DOUBLE
                                   PRECISION,
                                   humidity
                                   DOUBLE
                                   PRECISION,
                                   pressure_hpa
                                   DOUBLE
                                   PRECISION,
                                   wind_speed_mps
                                   DOUBLE
                                   PRECISION,
                                   clouds_pct
                                   DOUBLE
                                   PRECISION,
                                   uvi
                                   DOUBLE
                                   PRECISION,
                                   weather_description
                                   TEXT,

                                   -- Прогноз на следующий час
                                   forecast_temp
                                   DOUBLE
                                   PRECISION,
                                   forecast_rain_mm
                                   DOUBLE
                                   PRECISION,
                                   forecast_clouds_pct
                                   DOUBLE
                                   PRECISION,
                                   forecast_pop
                                   DOUBLE
                                   PRECISION,
                                   forecast_wind_mps
                                   DOUBLE
                                   PRECISION,

                                   -- Агрегаты
                                   forecast_3h_rain_mm
                                   DOUBLE
                                   PRECISION,
                                   forecast_6h_rain_mm
                                   DOUBLE
                                   PRECISION,
                                   forecast_3h_temp_delta
                                   DOUBLE
                                   PRECISION,
                                   forecast_6h_temp_delta
                                   DOUBLE
                                   PRECISION,
                                   will_rain_next_3h
                                   BOOLEAN,
                                   will_rain_next_6h
                                   BOOLEAN,

                                   -- Метаданные
                                   source
                                   TEXT,
                                   metadata
                                   JSONB
                               );
                               """)

            try:
                await conn.execute("""
                    SELECT create_hypertable(
                        'weather_data', 
                        'time',
                        if_not_exists => TRUE,
                        chunk_time_interval => INTERVAL '1 day'
                    );
                """)
            except Exception as e:
                logger.debug(f"Hypertable creation for weather: {e}")

            # ========== ИНДЕКСЫ ==========
            await conn.execute("""
                               CREATE INDEX IF NOT EXISTS idx_device_metrics_device_name
                                   ON device_metrics (device_name, time DESC);
                               """)

            await conn.execute("""
                               CREATE INDEX IF NOT EXISTS idx_device_metrics_mode
                                   ON device_metrics (power_mode, time DESC);
                               """)

            await conn.execute("""
                               CREATE INDEX IF NOT EXISTS idx_power_mode_events_mode
                                   ON power_mode_events (to_mode, time DESC);
                               """)

            await conn.execute("""
                               CREATE INDEX IF NOT EXISTS idx_weather_time
                                   ON weather_data (time DESC);
                               """)

            await conn.execute("""
                               CREATE INDEX IF NOT EXISTS idx_weather_source
                                   ON weather_data (source, time DESC);
                               """)

            logger.info("✅ Tables created/verified")

    async def _collect_weather_data(self, timestamp: datetime) -> None:
        """Write weather data at most once per hour."""
        import time as _time
        if _time.time() - self._last_weather_write < 3600:
            return
        self._last_weather_write = _time.time()
        """Собрать и записать данные о погоде из shared_state"""
        if not self.pool:
            return

        try:
            from shared_state.shared_state import shared_state

            # Текущее состояние
            ambient_temp = shared_state.get("ambient_temp")
            humidity = shared_state.get("humidity")
            pressure_hpa = shared_state.get("pressure_hpa")
            wind_speed_mps = shared_state.get("wind_speed_mps")
            clouds_pct = shared_state.get("clouds")
            uvi = shared_state.get("uvi")
            weather_description = shared_state.get("weather_description")

            # Если нет базовых данных - пропускаем
            if ambient_temp is None:
                return

            # Прогноз
            forecast_hourly = shared_state.get("forecast_hourly") or []
            forecast_source = shared_state.get("forecast_source") or "unknown"

            forecast_temp = None
            forecast_rain_mm = None
            forecast_clouds_pct = None
            forecast_pop = None
            forecast_wind_mps = None

            forecast_3h_rain_mm = None
            forecast_6h_rain_mm = None
            forecast_3h_temp_delta = None
            forecast_6h_temp_delta = None
            will_rain_3h = None
            will_rain_6h = None

            if forecast_hourly and len(forecast_hourly) > 0:
                # Следующий час
                next_hour = forecast_hourly[0]
                forecast_temp = next_hour.get("temp")
                forecast_clouds_pct = next_hour.get("clouds")
                forecast_pop = next_hour.get("pop")
                forecast_wind_mps = next_hour.get("wind_speed")

                # Дождь
                rain_entry = next_hour.get("rain")
                if rain_entry:
                    if isinstance(rain_entry, dict):
                        forecast_rain_mm = rain_entry.get("1h", 0)
                    elif isinstance(rain_entry, (int, float)):
                        forecast_rain_mm = float(rain_entry)

                # Агрегаты 3h и 6h
                if len(forecast_hourly) >= 3:
                    rain_3h = sum(
                        self._extract_rain(h.get("rain"))
                        for h in forecast_hourly[:3]
                    )
                    forecast_3h_rain_mm = rain_3h if rain_3h > 0 else None
                    will_rain_3h = rain_3h > 0.1

                    # Изменение температуры за 3 часа
                    temp_start = forecast_hourly[0].get("temp")
                    temp_end_3h = forecast_hourly[2].get("temp")
                    if temp_start is not None and temp_end_3h is not None:
                        forecast_3h_temp_delta = temp_end_3h - temp_start

                if len(forecast_hourly) >= 6:
                    rain_6h = sum(
                        self._extract_rain(h.get("rain"))
                        for h in forecast_hourly[:6]
                    )
                    forecast_6h_rain_mm = rain_6h if rain_6h > 0 else None
                    will_rain_6h = rain_6h > 0.1

                    # Изменение температуры за 6 часов
                    temp_start = forecast_hourly[0].get("temp")
                    temp_end_6h = forecast_hourly[5].get("temp")
                    if temp_start is not None and temp_end_6h is not None:
                        forecast_6h_temp_delta = temp_end_6h - temp_start

            # Метаданные
            metadata = None
            if forecast_hourly:
                metadata = json.dumps({
                    "forecast_hours": len(forecast_hourly),
                    "source": forecast_source,
                    "updated_at": timestamp.isoformat()
                })

            # Вставка в БД (ON CONFLICT для идемпотентности)
            async with self.pool.acquire() as conn:
                await conn.execute("""
                                   INSERT INTO weather_data (time,
                                                             ambient_temp, humidity, pressure_hpa, wind_speed_mps,
                                                             clouds_pct, uvi, weather_description,
                                                             forecast_temp, forecast_rain_mm, forecast_clouds_pct,
                                                             forecast_pop, forecast_wind_mps,
                                                             forecast_3h_rain_mm, forecast_6h_rain_mm,
                                                             forecast_3h_temp_delta, forecast_6h_temp_delta,
                                                             will_rain_next_3h, will_rain_next_6h,
                                                             source, metadata)
                                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                                           $14, $15, $16, $17, $18, $19, $20, $21) ON CONFLICT (time) DO
                                   UPDATE SET
                                       ambient_temp = EXCLUDED.ambient_temp,
                                       humidity = EXCLUDED.humidity,
                                       forecast_temp = EXCLUDED.forecast_temp,
                                       forecast_rain_mm = EXCLUDED.forecast_rain_mm,
                                       metadata = EXCLUDED.metadata
                                   """,
                                   timestamp,
                                   ambient_temp, humidity, pressure_hpa, wind_speed_mps,
                                   clouds_pct, uvi, weather_description,
                                   forecast_temp, forecast_rain_mm, forecast_clouds_pct,
                                   forecast_pop, forecast_wind_mps,
                                   forecast_3h_rain_mm, forecast_6h_rain_mm,
                                   forecast_3h_temp_delta, forecast_6h_temp_delta,
                                   will_rain_3h, will_rain_6h,
                                   forecast_source, metadata
                                   )

            logger.debug(
                f"🌤️  Weather recorded: {ambient_temp}°C, "
                f"humidity {humidity}%, "
                f"forecast {forecast_temp}°C"
            )

        except Exception as e:
            logger.error(f"Failed to collect weather data: {e}")

    def _extract_rain(self, rain_entry) -> float:
        """Извлечь мм дождя из записи прогноза"""
        if rain_entry is None:
            return 0.0
        if isinstance(rain_entry, (int, float)):
            return float(rain_entry)
        if isinstance(rain_entry, dict):
            return float(rain_entry.get("1h", 0))
        return 0.0

    def _detect_power_mode(self, dev_mgr) -> PowerMode:
        """
        Определение текущего режима работы системы

        Args:
            dev_mgr: DeviceManager с устройствами

        Returns:
            Текущий режим питания
        """
        try:
            # Ищем инвертор
            inverter = None
            for device in dev_mgr.get_devices():
                if hasattr(device, 'device_type') and 'inverter' in device.device_type.lower():
                    inverter = device
                    break

            if inverter and hasattr(inverter, 'power'):
                inverter_power = float(inverter.power) if inverter.power else 0.0

                # Если инвертор даёт достаточно мощности
                if inverter_power >= self.min_inverter_power:
                    return PowerMode.INVERTER

            # Проверяем время суток (грубая оценка)
            current_hour = datetime.now().hour
            if self.sunrise_hour <= current_hour < self.sunset_hour:
                # День, но инвертор не работает - возможно переключение
                if inverter and hasattr(inverter, 'power'):
                    inverter_power = float(inverter.power) if inverter.power else 0.0
                    if 0 < inverter_power < self.min_inverter_power:
                        return PowerMode.SWITCHING

                return PowerMode.INVERTER  # День - предполагаем инвертор
            else:
                return PowerMode.GRID  # Ночь - точно от сети

        except Exception as e:
            logger.warning(f"Error detecting power mode: {e}")
            return PowerMode.UNKNOWN

    async def _log_mode_change_event(
            self,
            from_mode: PowerMode,
            to_mode: PowerMode,
            dev_mgr
    ) -> None:
        """
        Записать событие переключения режима

        Args:
            from_mode: Предыдущий режим
            to_mode: Новый режим
            dev_mgr: DeviceManager
        """
        if not self.pool:
            return

        try:
            # Собираем данные о системе в момент переключения
            inverter_power = None
            grid_power = None
            battery_soc = None

            for device in dev_mgr.get_devices():
                if hasattr(device, 'device_type'):
                    if 'inverter' in device.device_type.lower():
                        if hasattr(device, 'power'):
                            inverter_power = float(device.power) if device.power else 0.0
                        if hasattr(device, 'metadata') and 'soc' in device.metadata:
                            battery_soc = float(device.metadata['soc'])

            # Длительность предыдущего режима
            duration = None
            if self._switching_detected_at:
                duration = int((datetime.now() - self._switching_detected_at).total_seconds())

            async with self.pool.acquire() as conn:
                await conn.execute("""
                                   INSERT INTO power_mode_events (time, from_mode, to_mode,
                                                                  inverter_power, grid_power, battery_soc,
                                                                  duration_seconds, metadata)
                                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                                   """,
                                   datetime.now(),
                                   from_mode.value,
                                   to_mode.value,
                                   inverter_power,
                                   grid_power,
                                   battery_soc,
                                   duration,
                                   json.dumps({"mode_change_count": self._mode_change_count})
                                   )

            logger.warning(
                f"⚡ MODE CHANGE: {from_mode.value} → {to_mode.value} "
                f"(inverter: {inverter_power}W, duration: {duration}s)"
            )

        except Exception as e:
            logger.error(f"Failed to log mode change event: {e}")

    async def _collect_inverter_metrics(self, timestamp: datetime) -> None:
        """Записать данные инвертора из shared_state в inverter_metrics.
        Always uses UTC timestamp regardless of inverter clock (UTC+8).
        """
        from datetime import timezone as _tz
        from shared_state.shared_state import shared_state
        timestamp = datetime.now(_tz.utc)
        try:
            async with self.pool.acquire() as conn:
                status = await conn.execute("""
                    INSERT INTO inverter_metrics (
                        time, battery_voltage, battery_soc,
                        battery_current_dis, battery_current_chg,
                        pv_power_w, output_power_w, output_voltage,
                        ac_input_voltage, ac_input_frequency, ac_output_load_pct,
                        total_load_w, working_mode, charger_priority, charger_priority_raw,
                        output_priority_raw, mains_status
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17
                    ) ON CONFLICT (time) DO NOTHING
                """,
                timestamp,
                shared_state.get("battery_voltage"),
                shared_state.get("battery_soc"),
                shared_state.get("battery_current_dis"),
                shared_state.get("battery_current_chg"),
                shared_state.get("pv_total_power"),
                shared_state.get("output_power"),
                shared_state.get("output_voltage"),
                shared_state.get("ac_input_voltage"),
                shared_state.get("ac_input_frequency"),
                shared_state.get("ac_output_load"),
                shared_state.get("output_power"),
                shared_state.get("working_mode"),
                shared_state.get("charger_priority"),
                shared_state.get("charger_priority_raw"),
                shared_state.get("output_priority_raw"),
                shared_state.get("mains_status"),
                )
            if inserted_rows(status) != 1:
                # A zero-row (conflicting) or unrecognised result is not a stored checkpoint.
                logger.warning("[TS] inverter_metrics checkpoint not stored (%s)", status)
                return False
            logger.debug("[TS] inverter_metrics written at %s", timestamp)
            return True
        except Exception as exc:
            logger.warning("[TS] inverter_metrics insert failed: %s", exc)
            return False

    async def _collect_device_energy_counters(self, timestamp: datetime, devices) -> None:
        """Write add_ele counters to device_energy_counters for daily delta calculation."""
        try:
            async with self.pool.acquire() as conn:
                for device in devices:
                    raw_ele = getattr(device, 'observed_energy_kwh', None)
                    if raw_ele is None:
                        continue
                    await conn.execute("""
                        INSERT INTO device_energy_counters (time, device_name, add_ele_raw, add_ele_kwh)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (time, device_name) DO NOTHING
                    """,
                    timestamp,
                    device.name,
                    raw_ele * 10.0,  # back to raw 0.1 kWh units
                    raw_ele,
                    )
        except Exception as exc:
            logger.warning("[TS] device_energy_counters insert failed: %s", exc)

    async def _collect_device_energy_counters(self, timestamp: datetime, devices) -> None:
        """Write add_ele counters to device_energy_counters for daily delta calculation."""
        try:
            async with self.pool.acquire() as conn:
                for device in devices:
                    raw_ele = getattr(device, 'observed_energy_kwh', None)
                    if raw_ele is None:
                        continue
                    await conn.execute("""
                        INSERT INTO device_energy_counters (time, device_name, add_ele_raw, add_ele_kwh)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (time, device_name) DO NOTHING
                    """,
                    timestamp,
                    device.name,
                    raw_ele * 10.0,  # back to raw 0.1 kWh units
                    raw_ele,
                    )
        except Exception as exc:
            logger.warning("[TS] device_energy_counters insert failed: %s", exc)

    async def collect_data(self, dev_mgr) -> Dict[str, Any]:
        """
        Собирает данные со всех устройств и записывает в БД

        Args:
            dev_mgr: DeviceManager с устройствами

        Returns:
            Статистика сбора данных
        """
        if not self._is_initialized or not self.pool:
            logger.warning("Collector not initialized, skipping collection")
            return {"status": "not_initialized", "records": 0}

        timestamp = datetime.now(timezone.utc)
        records_inserted = 0
        # Определяем текущий режим
        await self._collect_weather_data(timestamp)
        recorder = self.telemetry_recorder
        if recorder is None:
            await self._collect_inverter_metrics(timestamp)
        elif recorder.receipt_is_current(timestamp):
            # Checkpoint of the current DESS receipt. A stored row is not a new measurement.
            if await self._collect_inverter_metrics(timestamp):
                recorder.add_checkpoint(collected_at=timestamp)
            else:
                recorder.persistence_status = "degraded"
        else:
            # DESS outage/no response yet: never re-save the last response as fresh.
            recorder.note_stale_checkpoint()
        current_mode = self._detect_power_mode(dev_mgr)

        # Детект переключения режима. With the telemetry recorder, only observed
        # DESS working_mode changes are recorded (by the recorder); the legacy
        # clock-heuristic mode change is not logged as a power-mode transition.
        if current_mode != self._previous_mode:
            if self._previous_mode != PowerMode.UNKNOWN and recorder is None:
                await self._log_mode_change_event(
                    self._previous_mode,
                    current_mode,
                    dev_mgr
                )
                self._mode_change_count += 1
                self._switching_detected_at = timestamp

            self._previous_mode = current_mode

        self._current_mode = current_mode

        try:
            devices = dev_mgr.get_devices()

            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    for device in devices:
                        try:
                            data = {
                                "time": timestamp,
                                "device_name": device.name,
                                "device_type": device.device_type if hasattr(device, 'device_type') else 'unknown',
                                "is_on": device.observation.is_on if hasattr(getattr(device, "observation", None), "is_on") else None,
                                "power_watts": None,
                                "energy_kwh": None,
                                "temperature_celsius": None,
                                "humidity_percent": None,
                                "voltage": None,
                                "current_amps": None,
                                "power_mode": current_mode.value,
                                "metadata": {}
                            }

                            # Извлекаем метрики
                            # observed_power_w — real power from Tuya cur_power DP
                            if getattr(device, 'observed_power_w', None) is not None:
                                data["power_watts"] = float(device.observed_power_w)
                            elif hasattr(device, 'power') and device.power is not None:
                                data["power_watts"] = float(device.power)
                            elif getattr(device, 'load_in_wt', None) and getattr(getattr(device, 'observation', None), 'is_on', False):
                                data["power_watts"] = float(device.load_in_wt)

                            # observed_energy_kwh — cumulative from Tuya add_ele
                            if getattr(device, 'observed_energy_kwh', None) is not None:
                                data["energy_kwh"] = float(device.observed_energy_kwh)

                            if hasattr(device, 'temperature'):
                                data["temperature_celsius"] = float(device.temperature)

                            if hasattr(device, 'humidity'):
                                data["humidity_percent"] = float(device.humidity)

                            if hasattr(device, 'voltage'):
                                data["voltage"] = float(device.voltage)

                            if hasattr(device, 'current'):
                                data["current_amps"] = float(device.current)

                            # Metadata
                            metadata = {}
                            if hasattr(device, 'status'):
                                metadata['status'] = str(device.status)
                            if hasattr(device, 'mode'):
                                metadata['mode'] = str(device.mode)
                            if hasattr(device, 'speed'):
                                metadata['speed'] = device.speed

                            data["metadata"] = json.dumps(metadata) if metadata else None

                            # Вставка в БД
                            await conn.execute("""
                                               INSERT INTO device_metrics (time, device_name, device_type, is_on,
                                                                           power_watts, temperature_celsius,
                                                                           humidity_percent,
                                                                           voltage, current_amps, power_mode, metadata,
                                                                           energy_kwh)
                                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                                               """,
                                               data["time"],
                                               data["device_name"],
                                               data["device_type"],
                                               data["is_on"],
                                               data["power_watts"],
                                               data["temperature_celsius"],
                                               data["humidity_percent"],
                                               data["voltage"],
                                               data["current_amps"],
                                               data["power_mode"],
                                               data["metadata"],
                                               data["energy_kwh"]
                                               )

                            records_inserted += 1

                        except Exception as e:
                            logger.error(f"Error collecting from {device.name}: {e}")
                            continue

            self._total_records += records_inserted
            self._last_collection = timestamp

            # Эмодзи зависит от режима
            mode_emoji = {
                PowerMode.INVERTER: "☀️",
                PowerMode.GRID: "🌙",
                PowerMode.SWITCHING: "⚡",
                PowerMode.UNKNOWN: "❓"
            }

            logger.info(
                f"{mode_emoji.get(current_mode, '📊')} [{current_mode.value.upper()}] "
                f"Collected {records_inserted} records at {timestamp.strftime('%H:%M:%S')}"
            )

            return {
                "status": "success",
                "timestamp": timestamp.isoformat(),
                "records_inserted": records_inserted,
                "total_records": self._total_records,
                "power_mode": current_mode.value,
                "next_interval": self.intervals[current_mode]
            }

        except Exception as e:
            logger.error(f"❌ Collection failed: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "records_inserted": records_inserted
            }

    def get_current_interval(self) -> int:
        """Получить текущий интервал сбора данных (секунды).
        Persist active charging and its 10-minute grace window every minute.
        Otherwise preserve the existing adaptive intervals.
        """
        from shared_state.shared_state import shared_state
        now = time.monotonic()
        charge_current = shared_state.get("battery_current_chg")
        if shared_state.get("charger_priority") == "solar_and_mains" or (
            isinstance(charge_current, (int, float)) and charge_current > 0
        ):
            self._charge_detail_until = now + 600
        if now < self._charge_detail_until:
            return 60
        if self._current_mode is PowerMode.SWITCHING:
            base = self.intervals[PowerMode.SWITCHING]
        elif self.telemetry_recorder is not None:
            # Observed DESS mode + validated sun-times only; the sunrise_hour/
            # sunset_hour heuristic cannot select the 900 s branch.
            base = self.telemetry_recorder.checkpoint_interval(datetime.now(timezone.utc))
        else:
            base = self.intervals.get(self._current_mode, self.intervals[PowerMode.GRID])
        if self._current_mode == PowerMode.GRID:
            v = shared_state.get("battery_voltage")
            if v is not None:
                if v < 25.0:
                    return 60    # critical — every minute
                elif v < 25.5:
                    return min(base, 300)   # approaching threshold — never lengthened
        return base

    async def _count_records(self) -> int:
        """Подсчёт общего количества записей"""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    "SELECT COUNT(*) FROM device_metrics"
                )
                return result or 0
        except Exception as e:
            logger.warning(f"Could not count records: {e}")
            return 0

    def get_statistics(self) -> Dict[str, Any]:
        """Получить статистику коллектора"""
        return {
            "total_records": self._total_records,
            "last_collection": self._last_collection.isoformat() if self._last_collection else None,
            "current_mode": self._current_mode.value,
            "current_interval_seconds": self.get_current_interval(),
            "mode_changes_count": self._mode_change_count,
            "is_initialized": self._is_initialized,
            "database_connected": self.pool is not None,
            "intervals": {k.value: v for k, v in self.intervals.items()}
        }

    async def close(self) -> None:
        """Закрыть подключение к БД"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("✅ Database connection closed")
        if self.telemetry_recorder is not None and self.telemetry_recorder.writer_attached:
            self.telemetry_recorder.persistence_status = "unavailable"


async def initialize_with_retry(collector: TimescaleDataCollector, stop_event: asyncio.Event) -> bool:
    """Initialise the database, retrying recoverable failures until shutdown.

    Back-off doubles from INIT_RETRY_INITIAL_SECONDS up to INIT_RETRY_MAX_SECONDS
    and waits on ``stop_event``, so there is no busy loop and shutdown is
    immediate. Cancellation propagates. While waiting, the recorder keeps its
    bounded in-memory queue (overflow is counted) and reports "unavailable";
    this task is separate from DESS monitoring and charger protection.
    Returns False on shutdown or unrecoverable configuration.
    """
    delay = INIT_RETRY_INITIAL_SECONDS
    attempt = 0
    while not stop_event.is_set():
        attempt += 1
        if await collector.initialize():
            if attempt > 1:
                logger.info("[TS] database initialised after %s attempts; collection resumes", attempt)
            return True
        if not collector.initialization_recoverable():
            return False
        logger.warning("[TS] database initialisation attempt %s failed; retry in %.0fs", attempt, delay)
        if await _wait_for_stop(stop_event, delay):
            return False
        delay = min(delay * 2, INIT_RETRY_MAX_SECONDS)
    return False


async def _wait_for_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    """Wait up to ``seconds``; True if shutdown was requested meanwhile."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return False
    return True


class _FaultBackoff:
    """Bounded exponential back-off and rate-limited logging for one loop activity."""

    def __init__(self, name: str):
        self.name = name
        self.consecutive = 0
        self.total = 0
        self.retry_at = 0.0
        self._last_log_at: float | None = None
        self._suppressed = 0

    def ready(self, now: float) -> bool:
        return now >= self.retry_at

    def failure(self, exc: BaseException | None, now: float, cause: str | None = None) -> float:
        self.consecutive += 1
        self.total += 1
        # Cap the exponent before exponentiating: 2 ** 1024 cannot become a float
        # (OverflowError after ~85 h of failures). 2 ** 30 already exceeds any cap.
        exponent = min(self.consecutive - 1, LOOP_RETRY_EXPONENT_CAP)
        delay = min(LOOP_RETRY_INITIAL_SECONDS * (2 ** exponent), LOOP_RETRY_MAX_SECONDS)
        self.retry_at = now + delay
        cause = cause or (type(exc).__name__ if exc is not None else "unavailable")
        if self.consecutive == 1:
            logger.warning("[TS] %s failed (%s); retry in %.0fs; queued telemetry events are retained",
                           self.name, cause, delay, exc_info=exc is not None)
            self._last_log_at, self._suppressed = now, 0
        elif self._last_log_at is None or now - self._last_log_at >= LOOP_FAULT_LOG_INTERVAL_SECONDS:
            logger.warning("[TS] %s still failing (%s); %s consecutive failures, %s not logged; retry in %.0fs",
                           self.name, cause, self.consecutive, self._suppressed, delay)
            self._last_log_at, self._suppressed = now, 0
        else:
            self._suppressed += 1
        return delay

    def success(self) -> None:
        if self.consecutive:
            logger.info("[TS] %s recovered after %s consecutive failures", self.name, self.consecutive)
        self.consecutive = 0
        self.retry_at = 0.0
        self._last_log_at, self._suppressed = None, 0


def _safe_interval(collector: TimescaleDataCollector) -> float:
    """get_current_interval() with a conservative fallback; it never ends the loop."""
    try:
        return collector.get_current_interval()
    except Exception as exc:  # noqa: BLE001 - loop boundary
        logger.debug("[TS] interval evaluation failed (%s); using 60 s", type(exc).__name__)
        return 60


async def _after_fault(collector: TimescaleDataCollector) -> None:
    """Mark persistence degraded and recreate the pool if it is broken."""
    recorder = collector.telemetry_recorder
    if recorder is not None and recorder.writer_attached:
        recorder.persistence_status = "degraded"
    if collector.pool is not None and not await collector.connection_healthy():
        await collector.reset_connection()


async def timescale_collection_loop(
        collector: TimescaleDataCollector,
        dev_mgr,
        stop_event: asyncio.Event
) -> None:
    """
    Главный цикл сбора данных с адаптивным интервалом

    Recoverable errors are isolated at the loop boundary: a failing collection
    never stops the event flush, a failing flush or collection marks
    persistence degraded, checks the pool and recreates it when broken, and
    each activity backs off 5-300 s. Queued events stay queued (bounded) until
    a verified write. CancelledError is always re-raised. Only an unrecoverable
    configuration, or an unexpected non-Exception exit, detaches the writer and
    reports persistence "disabled".

    Args:
        collector: Экземпляр TimescaleDataCollector
        dev_mgr: DeviceManager
        stop_event: Event для остановки
    """
    logger.info("🚀 Starting adaptive TimescaleDB collection loop")
    recorder = collector.telemetry_recorder
    clean_exit = False
    try:
        # Инициализация: recoverable database failures are retried, not fatal.
        if not await initialize_with_retry(collector, stop_event):
            if not stop_event.is_set() and not collector.initialization_recoverable():
                logger.error("❌ TimescaleDB collector cannot start (configuration); telemetry persistence disabled")
                if recorder is not None:
                    dropped = recorder.detach_writer()
                    if dropped:
                        logger.warning("[TS] %s queued telemetry events dropped: no writer can run", dropped)
            clean_exit = True
            return

        collect_fault = _FaultBackoff("telemetry collection")
        flush_fault = _FaultBackoff("telemetry event flush")
        reconnect_fault = _FaultBackoff("database reconnect")
        last_collection_at: float | None = None
        flush_interval = EVENT_FLUSH_INTERVAL_SECONDS if recorder is not None else 60.0

        while not stop_event.is_set():
            now = time.monotonic()

            # Broken pool discarded after a fault: re-initialise with back-off.
            if collector.pool is None or not collector._is_initialized:
                if reconnect_fault.ready(now):
                    try:
                        reconnected = await collector.initialize()
                    except Exception as exc:  # noqa: BLE001 - loop boundary
                        reconnected = False
                        reconnect_fault.failure(exc, time.monotonic())
                    else:
                        if reconnected:
                            reconnect_fault.success()
                        else:
                            reconnect_fault.failure(None, time.monotonic())
                    if recorder is not None and recorder.writer_attached and not reconnected:
                        recorder.persistence_status = "degraded"
                if collector.pool is None or not collector._is_initialized:
                    await _wait_for_stop(stop_event, max(0.0, reconnect_fault.retry_at - time.monotonic()))
                    continue

            # Collection (checkpoints, weather, device rows): isolated from the flush.
            interval = _safe_interval(collector)
            now = time.monotonic()
            due = last_collection_at is None or now - last_collection_at >= interval
            if due and collect_fault.ready(now):
                try:
                    stats = await collector.collect_data(dev_mgr)
                except Exception as exc:  # noqa: BLE001 - loop boundary
                    collect_fault.failure(exc, time.monotonic())
                    await _after_fault(collector)
                else:
                    collect_fault.success()
                    last_collection_at = time.monotonic()
                    if stats.get("status") != "success":
                        logger.warning(f"Collection issue: {stats}")
                interval = _safe_interval(collector)

            # Event flush: verified writes only; failures retain the queue.
            if collector.pool is not None and flush_fault.ready(time.monotonic()):
                failures_before = collector.persist_failures
                try:
                    await collector.persist_pending_events()
                except Exception as exc:  # noqa: BLE001 - loop boundary
                    flush_fault.failure(exc, time.monotonic())
                    await _after_fault(collector)
                else:
                    if collector.persist_failures != failures_before:
                        # Write failed inside persist (events retained, 30 s floor);
                        # log rate-limited here and recreate the pool if broken.
                        flush_fault.failure(None, time.monotonic(), cause=collector.last_persist_error)
                        await _after_fault(collector)
                    elif collector.last_persist_outcome in ("written", "idle"):
                        flush_fault.success()

            # Sleep until the next flush tick or collection/retry time; never 0
            # after a failure (retry_at is in the future), so no busy loop.
            now = time.monotonic()
            next_collect = now + interval if last_collection_at is None else last_collection_at + interval
            next_collect = max(next_collect, collect_fault.retry_at)
            wait = min(flush_interval, max(0.0, next_collect - now))
            if collect_fault.consecutive or flush_fault.consecutive:
                wait = max(wait, min(flush_interval, LOOP_RETRY_INITIAL_SECONDS))
            await _wait_for_stop(stop_event, wait)
        clean_exit = True

    except asyncio.CancelledError:
        logger.info("📊 Collection loop cancelled")
        clean_exit = True
        raise
    except Exception as e:
        logger.error(f"❌ Error in collection loop: {e}", exc_info=True)
    finally:
        await collector.close()
        if not clean_exit and recorder is not None and recorder.writer_attached:
            # The worker is gone: never leave an apparently active recorder behind.
            dropped = recorder.detach_writer()
            logger.error("[TS] collection loop stopped unexpectedly; telemetry persistence disabled "
                         "(%s queued events dropped)", dropped)
        logger.info("✅ TimescaleDB collector stopped")
