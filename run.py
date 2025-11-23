#!/usr/bin/env python3
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from app.api import DessAPI
from app.config import Config
from app.device_initializer import DeviceInitializer
from app.logger import setup_logging, add_file_logger
# ML модули
from app.ml.ml_data_collector import MLDataCollector, ml_collection_loop
from app.ml.timescale_data_collector import TimescaleDataCollector, timescale_collection_loop
from app.monitoring.device_status_logger import DeviceStatusLogger
from app.service.smart_home_controller import SmartHomeController
from app.tuya.relay_tuya_controller import RelayTuyaController
from app.tuya.status_updater_async import TuyaStatusUpdaterAsync
from app.tuya.tuya_authorisation import TuyaAuthorisation
from app.weather.openweather_service import OpenWeatherService
from service.inverter_monitor import InverterMonitor


def disable_stdout_logging() -> None:
    """
    Убираем только **реальный** StreamHandler, который пишет в sys.stdout,
    оставляя RotatingFileHandler'ы в покое.
    """
    root = logging.getLogger()
    for h in root.handlers[:]:
        if type(h) is logging.StreamHandler and getattr(h, "stream", None) is sys.stdout:
            root.removeHandler(h)

    tuya = logging.getLogger("tuya_iot")
    tuya.propagate = False
    tuya.addHandler(logging.NullHandler())


async def main() -> None:
    # ─── 0. ЛОГИРОВАНИЕ ──────────────────────────────────────────
    full_log, important_log = setup_logging()

    dess_log = add_file_logger(
        name="DESS",
        path=Path("logs/dessmonitor.log"),
        level=logging.INFO
    )

    status_logger = DeviceStatusLogger()
    disable_stdout_logging()

    # ─── 1. ИНИЦИАЛИЗАЦИЯ УСТРОЙСТВ ────────────────────────────
    dev_mgr = DeviceInitializer().device_controller

    # ─── 2. TUYA-авторизация и контроллер ─────────────────────
    auth = TuyaAuthorisation()
    tuya_ctrl = RelayTuyaController(auth)

    # ─── 3. Асинхронный апдейтер статусов ──────────────────────
    updater = TuyaStatusUpdaterAsync(interval=120)
    updater_task = asyncio.create_task(updater.run())

    # ─── 4. Монитор инвертора (Dess API) ──────────────────────
    cfg_inv = Config()
    dess_api = DessAPI(cfg_inv, dess_log)
    inverter_mon = InverterMonitor(dess_api, poll_sec=60)
    inverter_task = asyncio.create_task(inverter_mon.run())

    # ─── 5. Бизнес-логика SmartHomeController ─────────────────
    smart_ctrl = SmartHomeController(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        switch_int=180,  # сек между проверками свитчей
        pump_int=120,  # сек между коррекцией насоса
    )
    smart_ctrl.start()

    # ─── 6. GRACEFUL SHUTDOWN SETUP ────────────────────────────
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # ─── 7. DUAL ML DATA COLLECTORS (CSV + TimescaleDB) ────────
    # CSV/JSON коллектор (бэкап)
    ml_data_dir = Path(os.getenv("ML_DATA_DIR", "ml_data"))
    ml_sqlite_path = Path(os.getenv("ML_SQLITE_PATH", str(ml_data_dir / "data.sqlite")))
    ml_csv_path = Path(os.getenv("ML_CSV_PATH", str(ml_data_dir / "training_data.csv")))
    ml_jsonl_path = Path(os.getenv("ML_JSONL_PATH", str(ml_data_dir / "training_data.jsonl")))

    # Интервалы и флаги экспорта
    ml_collect_interval = int(os.getenv("ML_COLLECT_INTERVAL", "300"))
    ml_csv_export = os.getenv("ML_CSV_EXPORT", "true").lower() in ("true", "1", "yes")
    ml_jsonl_export = os.getenv("ML_JSONL_EXPORT", "true").lower() in ("true", "1", "yes")

    # CSV/JSON коллектор
    ml_collector = MLDataCollector(
        db_path=ml_sqlite_path,
        csv_path=ml_csv_path,
        json_path=ml_jsonl_path,
        csv_export_enabled=ml_csv_export,
        jsonl_export_enabled=ml_jsonl_export,
        collect_interval=ml_collect_interval,
    )

    # TimescaleDB коллектор
    ts_inverter_interval = int(os.getenv("TS_INVERTER_INTERVAL", "120"))
    ts_grid_interval = int(os.getenv("TS_GRID_INTERVAL", "1800"))
    ts_switching_interval = int(os.getenv("TS_SWITCHING_INTERVAL", "10"))
    ts_collector = TimescaleDataCollector(
        inverter_interval=ts_inverter_interval,                              # 2 (120) минуты при солнце ☀️
        grid_interval=ts_grid_interval,                                      # 30 (1800) минут ночью 🌙
        switching_interval=ts_switching_interval,                            # 10 секунд при переключении ⚡
        min_inverter_power=float(os.getenv("TS_MIN_INVERTER_POWER", "50.0")),# Порог определения режима "инвертор"
        sunrise_hour=int(os.getenv("TS_SUNRISE_HOUR", "6")),                 # ~восход
        sunset_hour=int(os.getenv("TS_SUNSET_HOUR", "20")),                  # ~закат
    )

    # Показываем статистику
    ml_stats = ml_collector.get_statistics()
    ts_stats = ts_collector.get_statistics()

    important_log.info(
        f"[ML CSV] Data collection initialized. "
        f"Records: {ml_stats.get('total_records', 0)}"
        f"CSV: {ml_collector.csv_export_enabled}, "
        f"JSONL: {ml_collector.jsonl_export_enabled}"
    )
    important_log.info(
        f"[ML DB] Adaptive collector initialized. "
        f"Intervals: ☀️{ts_stats['intervals']['inverter']}s / "
        f"🌙{ts_stats['intervals']['grid']}s / "
        f"⚡{ts_stats['intervals']['switching']}s"
    )

    # ─── X. WEATHER SERVICE ─────────────────────────────────────
    openweather_api_key = os.getenv("OPENWEATHER_API_KEY")
    try:
        weather_lat = float(os.getenv("WEATHER_LAT", "51.5074"))
        weather_lon = float(os.getenv("WEATHER_LON", "-0.1278"))
    except ValueError:
        weather_lat = 51.5074
        weather_lon = -0.1278
        important_log.warning("Invalid WEATHER_LAT/LON, using defaults")

    weather_task = None

    if not openweather_api_key:
        important_log.warning("⚠️  OPENWEATHER_API_KEY not set! Weather service disabled.")
        weather_task = None

    else:
        try:
            weather_service = OpenWeatherService(
                api_key=openweather_api_key,
                lat=weather_lat,
                lon=weather_lon,
                update_interval=600  # 10 минут
            )
            important_log.info(
                f"[Weather] Service starting: lat={weather_lat}, lon={weather_lon}"
            )

            weather_task = asyncio.create_task(
                weather_service.run(stop_event)
            )
        except Exception as e:
            important_log.error(f"Failed to start weather service: {e}", exc_info=True)

    # Две отдельные task!
    ml_csv_task = asyncio.create_task(
        ml_collection_loop(ml_collector, dev_mgr, stop_event)
    )

    ml_db_task = asyncio.create_task(
        timescale_collection_loop(ts_collector, dev_mgr, stop_event)
    )

    # ─── 8. ОСНОВНОЙ ЦИКЛ ──────────────────────────────────────
    try:
        important_log.info("All services started. Running main loop...")
        last_stats_minute = -1  # для отслеживания логирования статистики

        while not stop_event.is_set():
            # 8.1 вертикальная сводка
            status_logger.log_snapshot(dev_mgr.get_devices())

            # 8.2 детали (насос, термометр)
            status_logger.log_device_details(dev_mgr.get_devices())

            # 8.3 кто реально ON
            on_names = [d.name for d in dev_mgr.all_devices_on()]
            important_log.info(f"[MAIN] ON devices: {', '.join(on_names) or 'none'}")

            # 8.4 статистика ОБОИХ коллекторов каждые 30 минут
            current_minute = datetime.now().minute
            if current_minute % 30 == 0 and current_minute != last_stats_minute:
                last_stats_minute = current_minute
                csv_stats = ml_collector.get_statistics()
                db_stats = ts_collector.get_statistics()
                important_log.info(
                    f"[ML CSV] {csv_stats['total_records']} records | "
                    f"[ML DB] {db_stats['total_records']} records, "
                    f"mode: {db_stats.get('current_mode', 'unknown')}"
                )

            # 8.5 пауза или ждём stop_event
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    except Exception as e:
        important_log.error(f"Error in main loop: {e}", exc_info=True)

    finally:
        # ─── 9. ОСТАНОВКА ВСЕХ СЕРВИСОВ ───────────────────────
        important_log.info("Shutting down...")

        # Останавливаем фоновые сервисы
        updater.stop()
        inverter_mon.stop()
        await smart_ctrl.stop()
        # Отменяем ВСЕ задачи
        if weather_task:
            weather_task.cancel()
        ml_csv_task.cancel()
        ml_db_task.cancel()
        updater_task.cancel()
        inverter_task.cancel()

        # Ждём, пока они корректно завершатся
        await asyncio.gather(
            updater_task,
            inverter_task,
            ml_csv_task,
            ml_db_task,
            weather_task if weather_task else asyncio.sleep(0),
            return_exceptions=True
        )

        important_log.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        logging.exception("Fatal error in main")
        sys.exit(1)