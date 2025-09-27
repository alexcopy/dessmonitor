#!/usr/bin/env python3
import asyncio
import logging
import signal
import sys
from pathlib import Path

from app.api import DessAPI
from app.config import Config
from app.device_initializer import DeviceInitializer
from app.logger import setup_logging, add_file_logger
from app.monitoring.device_status_logger import DeviceStatusLogger
from app.service.smart_home_controller import SmartHomeController
from app.tuya.relay_tuya_controller import RelayTuyaController
from app.tuya.status_updater_async import TuyaStatusUpdaterAsync
from app.tuya.tuya_authorisation import TuyaAuthorisation
from service.inverter_monitor import InverterMonitor


def disable_stdout_logging() -> None:
    """
    Убираем только **реальный** StreamHandler, который пишет в sys.stdout,
    оставляя RotatingFileHandler'ы в покое.
    """
    root = logging.getLogger()
    for h in root.handlers[:]:
        # проверяем и тип, и то, что поток именно stdout
        if type(h) is logging.StreamHandler and getattr(h, "stream", None) is sys.stdout:
            root.removeHandler(h)

    tuya = logging.getLogger("tuya_iot")
    tuya.propagate = False
    tuya.addHandler(logging.NullHandler())

async def main() -> None:
    # ─── 0. ЛОГИРОВАНИЕ ──────────────────────────────────────────
    # FULL + IMPORTANT (5 MB, 3 backup)
    full_log, important_log = setup_logging()

    # отдельный ротационный лог для Dess-монитора
    dess_log = add_file_logger(
        name="DESS",
        path=Path("logs/dessmonitor.log"),
        level=logging.INFO
    )

    # компактный статус-лог устройств
    status_logger = DeviceStatusLogger()
    disable_stdout_logging()

    # ─── 1. ИНИЦИАЛИЗАЦИЯ УСТРОЙСТВ ────────────────────────────
    dev_mgr = DeviceInitializer().device_controller

    # ─── 2. TUYA-авторизация и контроллер ─────────────────────
    auth      = TuyaAuthorisation()
    tuya_ctrl = RelayTuyaController(auth)

    # ─── 3. Асинхронный апдейтер статусов ──────────────────────
    updater      = TuyaStatusUpdaterAsync(interval=120)
    updater_task = asyncio.create_task(updater.run())

    # ─── 4. Монитор инвертора (Dess API) ──────────────────────
    cfg_inv       = Config()
    dess_api      = DessAPI(cfg_inv, dess_log)
    inverter_mon  = InverterMonitor(dess_api, poll_sec=60)
    inverter_task = asyncio.create_task(inverter_mon.run())

    # ─── 5. Бизнес-логика SmartHomeController ─────────────────
    smart_ctrl = SmartHomeController(
        dev_mgr    = dev_mgr,
        tuya_ctrl  = tuya_ctrl,
        switch_int = 180,   # сек между проверками свитчей
        pump_int   = 120,   # сек между коррекцией насоса
    )
    smart_ctrl.start()

    # ─── graceful shutdown ────────────────────────────────────
    loop       = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        while not stop_event.is_set():
            # 5.1 вертикальная сводка
            status_logger.log_snapshot(dev_mgr.get_devices())
            # 5.2 детали (насос, термометр)
            status_logger.log_device_details(dev_mgr.get_devices())
            # 5.3 кто реально ON
            on_names = [d.name for d in dev_mgr.all_devices_on()]
            important_log.info(f"[MAIN] ON devices: {', '.join(on_names) or 'none'}")
            status_logger.log_device_details(dev_mgr.get_devices())

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                # просто прошёл интервал — повторяем
                pass
    finally:
        important_log.info("Shutting down…")

        # останавливаем фоновые сервисы
        updater.stop()
        inverter_mon.stop()
        await smart_ctrl.stop()

        # ждём, пока они корректно завершатся
        await asyncio.gather(updater_task, inverter_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
