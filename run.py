# run.py
import asyncio
import logging
import signal
from pathlib import Path

from app.api                     import DessAPI
from app.config                  import Config
from app.device_initializer      import DeviceInitializer
from app.logger                  import setup_logging, CustomLogHandler
from app.monitoring.device_status_logger import DeviceStatusLogger
from app.service.smart_home_controller import SmartHomeController

from app.tuya.relay_tuya_controller import RelayTuyaController
from app.tuya.status_updater_async  import TuyaStatusUpdaterAsync
from app.tuya.tuya_authorisation    import TuyaAuthorisation

from service.inverter_monitor       import InverterMonitor



# ───────────────────────── helpers ──────────────────────────
def disable_stdout_logging() -> None:
    """
    У Tuya-SDK по-умолчанию добавляется StreamHandler на stdout —
    убираем, чтобы не засорять консоль.
    """
    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, logging.StreamHandler):
            root.removeHandler(h)

    logging.getLogger("tuya_iot").propagate = False
    logging.getLogger("tuya_iot").addHandler(logging.NullHandler())


# ───────────────────────── main() ───────────────────────────
async def main() -> None:
    # 0️⃣  ЛОГИРОВАНИЕ
    full_log, important_log = setup_logging()                    # FULL / IMPORTANT
    dess_log = logging.getLogger("DESS")                         # отдельный лог для Dess-монитора
    dess_log.setLevel(logging.INFO)
    dess_log.addHandler(CustomLogHandler(Path("logs/dessmonitor.log"), ch_name="dess"))

    status_logger = DeviceStatusLogger()
    disable_stdout_logging()

    # 1️⃣  УСТРОЙСТВА
    dev_mgr = DeviceInitializer().device_controller

    # 2️⃣  TUYA auth + controller
    auth        = TuyaAuthorisation()            # singleton
    tuya_ctrl   = RelayTuyaController(auth)

    # 3️⃣  STATUS-UPDATER (асинхронный)
    updater        = TuyaStatusUpdaterAsync(interval=30)
    updater_task   = asyncio.create_task(updater.run())

    # 4️⃣  INVERTER-MONITOR (Dess API)
    cfg_inverter   = Config()                                   # читает config.json
    dess_api       = DessAPI(cfg_inverter, dess_log)
    inverter_mon   = InverterMonitor(dess_api, poll_sec=60)
    inverter_task  = asyncio.create_task(inverter_mon.run())

    # 5️⃣  BUSINESS-LOGIC (SmartHomeController)
    smart_ctrl = SmartHomeController(
        dev_mgr   = dev_mgr,
        tuya_ctrl = tuya_ctrl,
        switch_int=60,          # период проверки switches  ON/OFF
        pump_int  =60           # период коррекции насоса
    )
    smart_ctrl.start()          # создаёт два внутренних таска

    # 6️⃣  graceful-shutdown
    loop       = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # ───────────── основной цикл (раз в минуту) ─────────────
    try:
        while not stop_event.is_set():
            # 6.1 вертикальная сводка устройств
            status_logger.log_snapshot(dev_mgr.get_devices())

            # 6.2 детали (насос, термометр)
            status_logger.log_device_details(dev_mgr.get_devices())

            # 6.3 список реально включённых
            on_names = [d.name for d in dev_mgr.all_devices_on()]
            important_log.info(f"[MAIN] ON devices: {', '.join(on_names) or 'none'}")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass           # просто очередной цикл
    finally:
        # ─── корректная остановка всех фоновых задач ───
        important_log.info("Shutting down…")

        updater.stop()
        inverter_mon.stop()
        await smart_ctrl.stop()

        # ждём завершения
        await asyncio.gather(
            updater_task,
            inverter_task,
            return_exceptions=True
        )


# ───────────────────────── entry-point ──────────────────────
if __name__ == "__main__":
    asyncio.run(main())
