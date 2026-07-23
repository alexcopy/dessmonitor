#!/usr/bin/env python3
"""
TimescaleDB Data Collector для солнечной системы
Адаптивный сбор данных: интенсивный при работе от инвертора, редкий от сети
"""

import asyncio
import logging
import os
from datetime import datetime, time as dt_time
from typing import Dict, Any, Optional, Literal
from enum import Enum
import json

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
            inverter_interval: int = 120,  # 2 минуты при работе от солнца
            grid_interval: int = 1800,  # 30 минут при работе от сети
            switching_interval: int = 10,  # 10 секунд при переключении
            # Параметры определения режима
            min_inverter_power: float = 50.0,  # Минимальная мощность для режима "инвертор"
            sunrise_hour: int = 6,  # Начало возможной работы от солнца
            sunset_hour: int = 20,  # Конец работы от солнца
            database_url: Optional[str] = None,
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
            return False

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

        timestamp = datetime.now()
        records_inserted = 0
        # Определяем текущий режим
        await self._collect_weather_data(timestamp)
        current_mode = self._detect_power_mode(dev_mgr)

        # Детект переключения режима
        if current_mode != self._previous_mode:
            if self._previous_mode != PowerMode.UNKNOWN:
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
                                "temperature_celsius": None,
                                "humidity_percent": None,
                                "voltage": None,
                                "current_amps": None,
                                "power_mode": current_mode.value,
                                "metadata": {}
                            }

                            # Извлекаем метрики
                            if hasattr(device, 'power'):
                                data["power_watts"] = float(device.power)

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
                                                                           voltage, current_amps, power_mode, metadata)
                                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
                                               data["metadata"]
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
        """Получить текущий интервал сбора данных (секунды)"""
        return self.intervals.get(self._current_mode, self.intervals[PowerMode.GRID])

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
            logger.info("✅ Database connection closed")


async def timescale_collection_loop(
        collector: TimescaleDataCollector,
        dev_mgr,
        stop_event: asyncio.Event
) -> None:
    """
    Главный цикл сбора данных с адаптивным интервалом

    Args:
        collector: Экземпляр TimescaleDataCollector
        dev_mgr: DeviceManager
        stop_event: Event для остановки
    """
    logger.info("🚀 Starting adaptive TimescaleDB collection loop")

    # Инициализация
    if not await collector.initialize():
        logger.error("❌ Failed to initialize collector, exiting")
        return

    try:
        while not stop_event.is_set():
            # Собираем данные
            stats = await collector.collect_data(dev_mgr)

            if stats["status"] != "success":
                logger.warning(f"Collection issue: {stats}")

            # Используем АДАПТИВНЫЙ интервал
            interval = collector.get_current_interval()

            # Ждём интервал или stop_event
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=interval
                )
            except asyncio.TimeoutError:
                # Нормально - просто прошёл интервал
                pass

    except asyncio.CancelledError:
        logger.info("📊 Collection loop cancelled")
    except Exception as e:
        logger.error(f"❌ Error in collection loop: {e}", exc_info=True)
    finally:
        await collector.close()
        logger.info("✅ TimescaleDB collector stopped")