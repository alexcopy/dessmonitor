#!/usr/bin/env python3
from typing import Any
import asyncio
import logging
import math
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from app.api import DessAPI
from app.config import Config
from app.device_initializer import DeviceInitializer
from app.logger import setup_logging, add_file_logger
# ML модули
from app.ml.ml_data_collector import MLDataCollector, ml_collection_loop
from app.ml.timescale_data_collector import TimescaleDataCollector, timescale_collection_loop
from app.control.adaptive_telemetry import AdaptiveTelemetryRecorder
from app.control.winter_runtime_diagnostics import WinterRuntimeDiagnostics
from app.monitoring.device_status_logger import DeviceStatusLogger
from app.service.smart_home_controller import SmartHomeController
from app.service.overload_protector import OverloadProtector
from app.service.solar_aware_controller import SolarAwareController
from app.service.battery_charge_controller import BatteryChargeController
from app.service.device_state_persist import save_last_switched, restore_last_switched
from app.service.startup_reset_coordinator import StartupResetCoordinator
from app.service.telemetry_registry import TelemetryRegistry
from app.devices.sensor_telemetry import compute_sensor_freshness
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
from shared_state.shared_state import shared_state


_WATER_TEMP_CACHE_PROPERTIES = frozenset({
    "temp_current",
    "va_temperature",
    "temp_humidity_way_1",
})
_WATER_TEMP_CACHE_SCALES = frozenset({"tenths", "celsius"})
_WATER_TEMP_CACHE_RAW_TYPES = frozenset({"int", "float", "string"})


def _parse_aware_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate_water_temp_provenance(
    cache: object,
    sensor_id: str,
    now_utc: datetime | None = None,
) -> tuple[dict[str, Any], datetime] | None:
    """Validate the exact verified shared-state provenance contract."""
    if type(cache) is not dict:
        return None
    if cache.get("sensor_id") != sensor_id:
        return None

    value = cache.get("value")
    if type(value) not in {int, float} or isinstance(value, bool) or not math.isfinite(float(value)):
        return None

    observed_at = _parse_aware_iso_timestamp(cache.get("observed_at"))
    comparison_now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed_at is None or observed_at > comparison_now:
        return None
    if cache.get("source") != "tuya":
        return None
    source_property = cache.get("source_property")
    scale = cache.get("scale")
    raw_type = cache.get("raw_type")
    if source_property not in _WATER_TEMP_CACHE_PROPERTIES:
        return None
    if scale not in _WATER_TEMP_CACHE_SCALES or raw_type not in _WATER_TEMP_CACHE_RAW_TYPES:
        return None
    if cache.get("status") != "valid":
        return None

    expected_combo = {
        "temp_current": {("tenths", "int"), ("celsius", "float")},
        "va_temperature": {("tenths", "int"), ("celsius", "float")},
        "temp_humidity_way_1": {("celsius", "string")},
    }
    if (scale, raw_type) not in expected_combo[source_property]:
        return None
    return dict(cache), observed_at


def _make_sensors_provider(registry):
    """Build the read-only sensor provider with provenance-gated fallback."""
    from shared_state.shared_state import shared_state as _ss

    def _provider():
        readings = registry.get_all_readings_dict()
        now_utc = datetime.now(timezone.utc)
        result = []

        for reading in readings:
            if (
                reading.get("status") == "valid"
                and reading.get("freshness") == "fresh"
            ):
                result.append(reading)
                continue

            registry_status = reading.get("status")
            if registry_status not in {"valid", "stale", "unavailable"}:
                result.append(reading)
                continue

            sensor_id = reading.get("sensor_id", "")
            validated = _validate_water_temp_provenance(
                _ss.get(f"water_temp_observation_{sensor_id}"),
                sensor_id,
                now_utc=now_utc,
            )
            if validated is None:
                result.append(reading)
                continue

            cache, observed_at = validated
            registry_observed_at = _parse_aware_iso_timestamp(reading.get("observed_at"))
            if registry_observed_at is not None and observed_at < registry_observed_at:
                result.append(reading)
                continue

            cached_reading = {
                **reading,
                "value": float(cache["value"]),
                "observed_at": cache["observed_at"],
                "freshness": compute_sensor_freshness(observed_at, now_utc).value,
                "status": "cached",
                "source": "shared_state_cache",
            }
            result.append(cached_reading)

        return result

    return _provider


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
    t0 = __import__("time").monotonic()
    await auth.connect_async()  # non-blocking Tuya token fetch
    logging.getLogger("FULL").info("[STARTUP] Tuya connect_async done in %.1fs", __import__("time").monotonic() - t0)
    tuya_ctrl = RelayTuyaController(auth)
    from shared_state.shared_state import shared_state as _shared_state
    _shared_state["_tuya_ctrl"] = tuya_ctrl

    # ─── 3. Telemetry registry + sensor descriptor bootstrap ────
    telemetry_registry = TelemetryRegistry()

    # Register configured sensor descriptors BEFORE any polling or reset
    from app.devices.relay_channel_device import (
        classify_projection_kind, DeviceProjectionKind,
    )
    for dev in dev_mgr.get_devices():
        proj = classify_projection_kind(dev.device_type)
        if proj == DeviceProjectionKind.SENSOR:
            # Skip meter devices — they register with energy sensor_id separately
            if getattr(dev, "device_type", "").lower() == "meter":
                continue
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
    # Authenticate DESS API asynchronously to avoid blocking startup
    if not dess_api.token:
        t1 = __import__("time").monotonic()
        try:
            await asyncio.to_thread(dess_api.authenticate)
            logging.getLogger("FULL").info(
                "[STARTUP] DESS auth done in %.1fs", __import__("time").monotonic() - t1
            )
        except Exception as dess_exc:
            logging.getLogger("FULL").warning(
                "[STARTUP] DESS auth failed in %.1fs: %s — monitor will retry official auth on fetch",
                __import__("time").monotonic() - t1, dess_exc,
            )
    charge_ctrl = BatteryChargeController(dess_api)
    utility_hold_runtime = None
    utility_hold_pool = None
    if os.getenv("DESS_UTILITY_HOLD_ENABLED", "").lower() in ("1", "true", "yes", "on"):
        try:
            import asyncpg
            from app.control.easun_output_actuator import PostgresJournal
            from app.control.runtime_utility_hold import RuntimeUtilityHold, build_authorization
            utility_auth = build_authorization(dess_api)
            database_url = os.getenv("DATABASE_URL")
            if not utility_auth.ready:
                important_log.warning("[UTILITY_HOLD] disabled: commissioning authorization incomplete")
            elif not database_url:
                important_log.warning("[UTILITY_HOLD] disabled: DATABASE_URL unavailable for durable journal")
            else:
                utility_hold_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1,
                                                              command_timeout=10)
                utility_hold_runtime = RuntimeUtilityHold(
                    dess_api, PostgresJournal(utility_hold_pool), utility_auth)
                important_log.warning("[UTILITY_HOLD] enabled by explicit operator authorization")
        except Exception:
            important_log.exception("[UTILITY_HOLD] startup failed; charger control remains active")
    shadow_monitor = None
    if os.getenv("ADAPTIVE_OUTPUT_SHADOW_ENABLED", "false").lower() in ("1", "true", "yes", "on"):
        try:
            from app.control.output_policy_config import load_output_policy, OutputPolicyConfig
            from app.control.shadow_output_monitor import ShadowOutputMonitor
            config_path = DeviceInitializer._resolve_config_path(None)
            try:
                policy_config = load_output_policy(config_path)
                baseline = policy_config.to_baseline() if isinstance(policy_config, OutputPolicyConfig) else None
                if baseline is None:
                    important_log.warning("[OUTPUT_SHADOW] policy unconfigured; limits remain unknown")
            except Exception as exc:
                important_log.warning("[OUTPUT_SHADOW] policy unavailable (%s); limits remain unknown", type(exc).__name__)
                baseline = None
            shadow_monitor = ShadowOutputMonitor(baseline, important_log)
            important_log.info("[OUTPUT_SHADOW] enabled; observe only")
        except Exception:
            important_log.exception("[OUTPUT_SHADOW] startup failed; monitoring continues")
    # Task 0079: read-only telemetry recorder + winter diagnostics. Both consume
    # the response the existing poll already fetched; no request, no command.
    shadow_baseline = getattr(shadow_monitor, "baseline", None)
    telemetry_recorder = AdaptiveTelemetryRecorder(
        forecast_max_age_seconds=getattr(shadow_baseline, "forecast_max_age_seconds", 180.0 * 60.0),
    )
    winter_diagnostics = WinterRuntimeDiagnostics(
        baseline=shadow_baseline,
        history_provider=lambda: tuple(shadow_monitor.history) if shadow_monitor is not None else (),
    )
    _winter_error_logged_at: list[float] = []

    def _observe_dess_response(dd, *, received_at, sun_times=None) -> None:
        telemetry_recorder.observe(dd, received_at=received_at, sun_times=sun_times)
        try:
            winter_diagnostics.observe(
                dd,
                received_at=received_at,
                sun_times=sun_times,
                fingerprint=telemetry_recorder.latest_fingerprint,
            )
        except Exception:
            # observe() already records failures; this only guards the monitor loop.
            now_mono = __import__("time").monotonic()
            if not _winter_error_logged_at or now_mono - _winter_error_logged_at[0] >= 300:
                important_log.exception("[WINTER_DIAG] evaluation failed; monitoring continues")
                _winter_error_logged_at[:] = [now_mono]

    async def _observe_post_charge_coordination(dd, *, received_at, evaluated_at) -> None:
        """Attach post-charge runtime status to the exact already-recorded response."""
        if utility_hold_runtime is not None:
            await utility_hold_runtime.tick(
                winter_diagnostics, dd, received_at=received_at, evaluated_at=evaluated_at,
                fingerprint=telemetry_recorder.latest_fingerprint or "",
                policy_status=shared_state.get("charge_policy_status"),
            )
            return
        winter_diagnostics.observe_coordination(
            dd, received_at=received_at, evaluated_at=evaluated_at,
            fingerprint=telemetry_recorder.latest_fingerprint,
            policy_status=shared_state.get("charge_policy_status"))

    inverter_mon = InverterMonitor(
        dess_api,
        poll_sec=60,
        charge_controller=charge_ctrl,
        shadow_monitor=shadow_monitor,
        observation_observer=_observe_dess_response,
        post_charge_observer=_observe_post_charge_coordination,
    )
    inverter_task = asyncio.create_task(inverter_mon.run())

    # ─── 4b. OPTIONAL WEB HOST (start early for reset visibility) ──
    web_host_handle: RuntimeWebHostHandle | None = None

    # Create inverter data provider lambda (reads from InverterMonitor.last_data)
    def _inverter_provider() -> dict[str, Any] | None:
        """Read latest inverter data from InverterMonitor for web dashboard.

        Returns None when no DeviceData has been received yet. The DESS
        timestamp has no verified measurement-time contract, so it is not
        exposed as observed_at; the DESS receipt time is the only freshness
        clock and is labelled as receipt time.
        """
        if inverter_mon.last_data is None:
            return None
        dd = inverter_mon.last_data
        received = telemetry_recorder.latest_received_at
        received_iso = received.isoformat() if received is not None else None

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
            # Device measurement time is unverified; never presented as observed_at.
            "observed_at": None,
            "received_at": received_iso,
            "timestamp_quality": "device_time_unverified",
            "freshness": "receipt_only" if received_iso else "unavailable",
        }

    def _winter_provider():
        """Display-only winter block for /control/state; evaluation happens on the DESS path."""
        winter_diagnostics.set_persistence_status(telemetry_recorder.persistence_status)
        return winter_diagnostics.snapshot(telemetry=telemetry_recorder.statistics())

    # ─── 5. STARTUP RESET — command all binary switches OFF ──
    reset_coordinator = StartupResetCoordinator(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        status_updater=updater,
    )

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
            winter_diagnostics_provider=_winter_provider,
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

    solar_ctrl = SolarAwareController(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        startup_reset_coordinator=reset_coordinator,
    )
    inverter_mon.solar_controller = solar_ctrl

    # ─── 6. Бизнес-логика SmartHomeController ─────────────────
    pump_automation_enabled = os.getenv("PUMP_AUTOMATION_ENABLED", "").lower() in ("true", "1", "yes")

    smart_ctrl = SmartHomeController(
        dev_mgr=dev_mgr,
        tuya_ctrl=tuya_ctrl,
        switch_int=180,  # сек между проверками свитчей
        pump_int=120,  # сек между коррекцией насоса
        switch_automation_enabled=False,
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
        ts_inverter_interval = int(os.getenv("TS_INVERTER_INTERVAL", "300"))
        ts_grid_interval = int(os.getenv("TS_GRID_INTERVAL", "900"))
        ts_switching_interval = int(os.getenv("TS_SWITCHING_INTERVAL", "10"))
        ts_collector = TimescaleDataCollector(
            inverter_interval=ts_inverter_interval,                              # 5 (300) минут при солнце ☀️ (без recorder)
            grid_interval=ts_grid_interval,                                      # 15 (900) минут ночью 🌙 (без recorder)
            switching_interval=ts_switching_interval,                            # 10 секунд при переключении ⚡
            min_inverter_power=float(os.getenv("TS_MIN_INVERTER_POWER", "50.0")),# Порог определения режима "инвертор"
            sunrise_hour=int(os.getenv("TS_SUNRISE_HOUR", "6")),                 # ~восход
            sunset_hour=int(os.getenv("TS_SUNSET_HOUR", "20")),                  # ~закат
            telemetry_recorder=telemetry_recorder,
        )

        ts_stats = ts_collector.get_statistics()
        important_log.info(
            f"[ML DB] Adaptive collector initialized. "
            f"Intervals: ☀️{ts_stats['intervals']['inverter']}s / "
            f"🌙{ts_stats['intervals']['grid']}s / "
            f"⚡{ts_stats['intervals']['switching']}s"
        )
        important_log.info(
            "[ML DB] Adaptive telemetry: checkpoints 300s day/transition/unknown, "
            "900s only for validated stable grid night; observed changes persisted between checkpoints"
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
    # ─── Overload protection ───────────────────────────────────
    overload_protector = OverloadProtector(dev_mgr=dev_mgr, tuya_ctrl=tuya_ctrl)
    overload_task = asyncio.create_task(
        overload_protector.run(stop_event)
    )

    if ts_enabled and ts_collector is not None:
        ml_db_task = asyncio.create_task(
            timescale_collection_loop(ts_collector, dev_mgr, stop_event)
        )
    else:
        ml_db_task = None

    # The learning worker reads stored history and writes diagnostic entries
    # only. It never receives an inverter client or actuator.
    learning_task = None
    if os.getenv("ADAPTIVE_OUTPUT_LEARNING_ENABLED", "false").lower() in ("true", "1", "yes", "on"):
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            from app.control.online_output_learning import learning_loop
            learning_task = asyncio.create_task(learning_loop(database_url, stop_event))
        else:
            important_log.warning("[OUTPUT_LEARNING] DATABASE_URL unavailable; diagnostics disabled")

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
        if learning_task is not None:
            learning_task.cancel()
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
        if learning_task is not None:
            gather_tasks.append(learning_task)

        # Ждём, пока они корректно завершатся
        await asyncio.gather(
            *gather_tasks,
            return_exceptions=True
        )
        if utility_hold_pool is not None:
            await utility_hold_pool.close()

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
