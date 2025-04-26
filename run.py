import asyncio
import logging
import signal
from pathlib import Path

from app.api import DessAPI
from app.config import Config
from app.device_initializer          import DeviceInitializer
from app.logger import setup_logging, CustomLogHandler
from app.monitoring.device_status_logger import DeviceStatusLogger
from app.tuya.relay_tuya_controller  import RelayTuyaController
from app.tuya.status_updater_async   import TuyaStatusUpdaterAsync
from app.tuya.tuya_authorisation     import TuyaAuthorisation
from service.inverter_monitor import InverterMonitor


# ──────────────────────────────────────────────────────────────
def disable_stdout_logging() -> None:
    """
    У Tuya-SDK по-умолчанию есть StreamHandler → stdout.
    Удаляем, чтобы не захламлять консоль.
    """
    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, logging.StreamHandler):
            root.removeHandler(h)

    logging.getLogger("tuya_iot").propagate = False
    logging.getLogger("tuya_iot").addHandler(logging.NullHandler())

# def dess_monitor():
#     api = DessAPI(config, full_logger)
#
#     # Старт фоновых веток SmartHomeService
#     home_service = start_smart_home()
#
#     print(f"[INFO] Начинаем опрос данных каждые {config.interval} секунд...")
#
#     while True:
#         try:
#             data = api.fetch_device_data()
# ──────────────────────────────────────────────────────────────
async def main() -> None:
    # 0️⃣  логирование
    full_log, important_log = setup_logging()
    dess_log = logging.getLogger("DESS")
    dess_log.setLevel(logging.INFO)
    dess_log.addHandler(CustomLogHandler(path=Path("logs/dessmonitor.log"), ch_name="dess"))
    status_logger = DeviceStatusLogger()
    disable_stdout_logging()

    # 1️⃣  устройства
    dev_mgr = DeviceInitializer().device_controller

    # 2️⃣  Tuya → controller
    auth       = TuyaAuthorisation()          # singleton
    controller = RelayTuyaController(auth)

    # 3️⃣  асинхронный обновитель статусов
    updater      = TuyaStatusUpdaterAsync(interval=30)
    updater_task = asyncio.create_task(updater.run())

    # ───────── 4. Inverter-monitor (Dess API) ─────────
    cfg_inverter   = Config()                          # берёт email/пароль и т.д.
    dess_api       = DessAPI(cfg_inverter, dess_log)
    inverter_mon   = InverterMonitor(dess_api, poll_sec=60)
    inverter_task  = asyncio.create_task(inverter_mon.run())

    # ───────── 5.   graceful-shutdown ─────────
    loop       = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        while not stop_event.is_set():
            # 5.1: вертикальная сводка
            status_logger.log_snapshot(dev_mgr.get_devices())

            # 5.2: детали (насос, термометр)
            status_logger.log_device_details(dev_mgr.get_devices())

            # 5.3: кто реально ON
            on_names = [d.name for d in dev_mgr.all_devices_on()]
            important_log.info(f"[MAIN] ON devices: {', '.join(on_names) or 'none'}")

            # ждём либо Ctrl-C, либо таймау

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass      # просто цикл каждые 60 с
    finally:
        # ───── корректное завершение всех фоновых корутин ─────
        important_log.info("Shutting down…")
        updater.stop()                # мягкий стоп для Tuya-апдейтера
        inverter_mon.stop()           # то же самое для InverterMonitor
        # ждём окончания работы задач
        await asyncio.gather(updater_task, inverter_task, return_exceptions=True)



# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())
