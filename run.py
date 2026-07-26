#!/usr/bin/env python3
from typing import Any
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo  # stdlib Python 3.9+, requires tzdata in Docker image
from app.api import DessAPI
from app.config import Config
from app.device_initializer import DeviceInitializer
from app.logger import setup_logging, add_file_logger
# ML модули
from app.ml.ml_data_collector import MLDataCollector, ml_collection_loop
from app.ml.timescale_data_collector import TimescaleDataCollector, timescale_collection_loop
from app.monitoring.device_status_logger import DeviceStatusLogger
from app.service.smart_home_controller import SmartHomeController
from app.service.startup_reset_coordinator import StartupResetCoordinator
from app.service.telemetry_registry import TelemetryRegistry
from app.tuya.relay_tuya_controller import RelayTuyaController
from app.tuya.status_updater_async import TuyaStatusUpdaterAsync
from app.tuya.tuya_authorisation import TuyaAuthorisation
from app.weather.openweather_service import OpenWeatherService
from app.web_runtime_integration import (
    RuntimeWebHostHandle,
    start_runtime_read_only_web_host,
    stop_runtime_read_only_web_host,
)
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
    tuya_cfg = DeviceInitializer().get_tuya_config()
    access_id = tuya_cfg.get("ACCESS_ID", "")
    access_key = tuya_cfg.get("ACCESS_KEY", "")
    if not access_id or not access_key:
        important_log.error(
            "[TUYA] Missing ACCESS_ID or ACCESS_KEY in device configuration"
        )
        sys.exit(1)
    auth = TuyaAuthorisation(access_id=access_id, access_key=access_key)
    tuya_ctrl = RelayTuyaController(auth)

    # ─── 3. Telemetry registry + sensor descriptor bootstrap ────
    telemetry_registry = TelemetryRegistry()

    # Register configured sensor descriptors BEFORE any polling or reset
    from app.devices.relay_channel_device import (
        classify_projection_kind, DeviceProjectionKind,
    )
    for dev in dev_mgr.get_devices():
        proj = classify_projection_kind(dev.device_type)
        if proj == DeviceProjectionKind.SENSOR:
            sensor_id = f"{dev.id}_water_temp"
            display_name = getattr(dev, "name", "Sensor") or "Sensor"
            description = getattr(dev, "desc", "") or ""
            telemetry_registry.register_sensor_descriptor(
                sensor_id=sensor_id,
                display_name=display_name,
                description=description,
                communication_status="unknown",
            )
            important_log.info(
                "[SENSOR] Registered configured sensor: %s (id=%s)",
                display_name, sensor_id,
            )

    updater = TuyaStatusUpdaterAsync(
        interval=120, dev_mgr=dev_mgr, authorisation=auth,
        telemetry_registry=telemetry_registry,
    )
    updater_task = asyncio.create_task(updater.run())

    # ─── 4. Монитор инвертора (Dess API) ──────────────────────
    cfg_inv = Config()
    dess_api = DessAPI(cfg_inv, dess_log)
    inverter_mon = InverterMonitor(dess_api, poll_sec=60)
    inverter_task = asyncio.create_task(inverter_mon.run())

    # ─── 4b. OPTIONAL WEB HOST (start early for reset visibility) ──
    web_host_handle: RuntimeWebHostHandle | None = None

    # Create inverter data provider lambda (reads from InverterMonitor.last_data)
    def _inverter_provider() -> dict[str, Any] | None:
        """Read latest inverter data from InverterMonitor for web dashboard.

        Returns None when no DeviceData has been received yet.
        Normalises observed_at to ISO 8601 with Europe/London timezone offset
        using zoneinfo.ZoneInfo.  Assumes naive local-time strings from the
        DESS API represent Europe/London local time.
        """
        if inverter_mon.last_data is None:
            return None
        dd = inverter_mon.last_data

        # --- observed_at: normalise to ISO 8601 Europe/London ---
        observed_at_iso: str | None = None
        raw_ts = dd.timestamp
        if raw_ts and isinstance(raw_ts, str) and raw_ts.strip():
            raw_ts = raw_ts.strip()
            # If already ISO-like (contains T, Z, or offset), pass as-is
            if "T" in raw_ts or "Z" in raw_ts or "+" in raw_ts or raw_ts.endswith("00:00"):
                observed_at_iso = raw_ts
            else:
                # Naive "YYYY-MM-DD HH:MM:SS" assumed to be Europe/London local time
                try:
                    naive_dt = datetime.strptime(raw_ts, "%Y-%m-%d %H:%M:%S")
                    london_dt = naive_dt.replace(tzinfo=ZoneInfo("Europe/London"))
                    observed_at_iso = london_dt.isoformat()
                except (ValueError, TypeError, KeyError):
                    observed_at_iso = raw_ts  # fallback: pass original
        elif raw_ts is not None:
            observed_at_iso = str(raw_ts)

        return {
            "battery_voltage": dd.battery_voltage,
            "battery_soc": dd.battery_capacity,
            "battery_current_chg": dd.battery_charging_current,
            "battery_current_dis": dd.battery_discharging_current,
            "pv1_voltage": dd.pv1_voltage,
            "pv1_power": dd.pv1_power,
            "pv2_voltage": dd.pv2_voltage,
            "pv2_power": dd.pv2_power,
            "pv_total_power": dd.pv_total_power,
            "output_voltage": dd.output_voltage,
            "output_power": dd.output_power,
            "output_apparent_power": dd.output_apparent_power,
            "ac_input_voltage": dd.ac_input_voltage,
            "ac_input_frequency": dd.ac_input_frequency,
            "ac_output_load": dd.ac_output_load,
            "working_mode": dd.working_state if dd.working_state else "",
            "mains_status": dd.mains_status if dd.mains_status else "",
            "observed_at": observed_at_iso,
        }

    # ─── 5. STARTUP RESET — command all binary switches OFF ──
    reset_coordinator = StartupResetCoordinator(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        status_updater=updater,
    )


    def _make_sensors_provider(registry):
        """Sensors provider with shared_state fallback.
        
        TelemetryRegistry gets data only when Tuya polling succeeds.
        If Tuya is in permission_denied, registry has no fresh data.
        Fallback reads water_temp directly from shared_state (written
        by ML collector every 5 min regardless of Tuya state).
        """
        from datetime import datetime, timezone
        from shared_state.shared_state import shared_state as _ss

        def _provider():
            readings = registry.get_all_readings_dict()
            # Check if any reading is fresh
            has_fresh = any(r.get("freshness") == "fresh" for r in readings)
            if has_fresh:
                return readings

            # Fallback: build sensor dict from shared_state
            water_temp = _ss.get("water_temp")
            if water_temp is None:
                # Return registry readings as-is (may be unavailable)
                return readings

            now_iso = datetime.now(timezone.utc).isoformat()
            fallback = [{
                "sensor_id": "watertemp_fallback",
                "display_name": "watertemp",
                "description": "Water Thermometer",
                "device_type": "thermo",
                "metric": "water_temperature",
                "value": float(water_temp),
                "unit": "celsius",
                "observed_at": now_iso,
                "source": "shared_state",
                "freshness": "fresh",
                "status": "valid",
                "communication_status": "active",
            }]
            # Merge: use fallback for sensors without fresh readings
            result = []
            fallback_ids = {r["sensor_id"] for r in fallback}
            for r in readings:
                if r["freshness"] != "fresh" and r.get("display_name", "").lower() in ("watertemp", "pond thermo", "water temperature"):
                    continue  # replace with fallback
                result.append(r)
            result.extend(fallback)
            return result

        return _provider

    # Start web host before reset so dashboard shows progress
    try:
        web_host_handle = await start_runtime_read_only_web_host(
            devices_provider=dev_mgr.get_devices,
            logger=important_log,
            startup_reset_status_provider=lambda: reset_coordinator.reset_status,
            startup_reset_gate_open_provider=lambda: reset_coordinator.is_gate_open,
            per_device_results_provider=reset_coordinator.get_per_device_results,
            sensors_provider=_make_sensors_provider(telemetry_registry),
            inverter_provider=_inverter_provider,
        )
    except Exception as exc:
        important_log.warning(f"[WEB] Read-only host not started: {exc}")

    important_log.info(
        "[RESET] Starting startup reset (max %.0fs) ...",
        reset_coordinator._timeout,
    )
    await reset_coordinator.execute()
    important_log.info(
        "[RESET] Startup reset complete — status=%s gate_open=%s",
        reset_coordinator.reset_status, reset_coordinator.is_gate_open,
    )

    # ─── 6. Бизнес-логика SmartHomeController ─────────────────
    pump_automation_enabled = os.getenv("PUMP_AUTOMATION_ENABLED", "").lower() in ("true", "1", "yes")

    smart_ctrl = SmartHomeController(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        switch_int=180,  # сек между проверками свитчей
        pump_int=120,  # сек между коррекцией насоса
        pump_automation_enabled=pump_automation_enabled,
        startup_reset_coordinator=reset_coordinator,
    )

    important_log.info(
        f"[PUMP] Pump automation: {'ENABLED' if pump_automation_enabled else 'DISABLED'} "
        f"(set PUMP_AUTOMATION_ENABLED=true to enable)"
    )
    smart_ctrl.start()

    # ─── 6. GRACEFUL SHUTDOWN SETUP ────────────────────────────
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # ─── 6b. OPTIONAL WEB HOST ──────────────────────────────────
    # (started earlier in step 4b for reset visibility)

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

    # TimescaleDB коллектор (только если TS_ENABLED)
    ts_enabled = os.getenv("TS_ENABLED", "").lower() in ("true", "1", "yes", "on")

    if ts_enabled:
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

        ts_stats = ts_collector.get_statistics()
        important_log.info(
            f"[ML DB] Adaptive collector initialized. "
            f"Intervals: ☀️{ts_stats['intervals']['inverter']}s / "
            f"🌙{ts_stats['intervals']['grid']}s / "
            f"⚡{ts_stats['intervals']['switching']}s"
        )
    else:
        ts_collector = None
        important_log.info("[ML DB] TimescaleDB collection disabled (set TS_ENABLED=true to enable)")

    # Показываем статистику CSV коллектора
    ml_stats = ml_collector.get_statistics()

    important_log.info(
        f"[ML CSV] Data collection initialized. "
        f"Records: {ml_stats.get('total_records', 0)}"
        f"CSV: {ml_collector.csv_export_enabled}, "
        f"JSONL: {ml_collector.jsonl_export_enabled}"
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

    if ts_enabled and ts_collector is not None:
        ml_db_task = asyncio.create_task(
            timescale_collection_loop(ts_collector, dev_mgr, stop_event)
        )
    else:
        ml_db_task = None

    # ─── 7b. OPTIONAL EMBEDDED WEB HOST START ───────────────────
    # (started earlier in step 4b)

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

            # 8.4 статистика коллекторов каждые 30 минут
            current_minute = datetime.now().minute
            if current_minute % 30 == 0 and current_minute != last_stats_minute:
                last_stats_minute = current_minute
                csv_stats = ml_collector.get_statistics()
                important_log.info(
                    f"[ML CSV] {csv_stats['total_records']} records"
                )
                if ts_enabled and ts_collector is not None:
                    db_stats = ts_collector.get_statistics()
                    important_log.info(
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

        # Останавливаем web-хост перед device-сервисами
        if web_host_handle is not None:
            await stop_runtime_read_only_web_host(web_host_handle, logger=important_log)

        # Останавливаем фоновые сервисы
        updater.stop()
        inverter_mon.stop()
        await smart_ctrl.stop()
        # Отменяем ВСЕ задачи
        if weather_task:
            weather_task.cancel()
        ml_csv_task.cancel()
        if ml_db_task is not None:
            ml_db_task.cancel()
        updater_task.cancel()
        inverter_task.cancel()

        # Собираем задачи для ожидания
        gather_tasks = [
            updater_task,
            inverter_task,
            ml_csv_task,
            weather_task if weather_task else asyncio.sleep(0),
        ]
        if ml_db_task is not None:
            gather_tasks.insert(3, ml_db_task)

        # Ждём, пока они корректно завершатся
        await asyncio.gather(
            *gather_tasks,
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
