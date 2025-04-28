# app/status_updater_async.py
import asyncio
import logging
import time
from datetime import datetime

from app.utils.time_utils import smart_sleep
from shared_state.shared_state import shared_state
from app.device_initializer import DeviceInitializer
from app.tuya.tuya_authorisation import TuyaAuthorisation

logger = logging.getLogger("TuyaStatusUpdater")


class TuyaStatusUpdaterAsync:
    """
    Периодически (interval с) запрашивает статусы у облака Tuya
    и обновляет объекты RelayChannelDevice.
    Работает как корутина, которую удобно запускать через
    asyncio.create_task().
    """

    def __init__(self, interval: int = 30):
        self.interval = interval
        self._stop = asyncio.Event()

        self.dev_mgr = DeviceInitializer().device_controller
        self.auth = TuyaAuthorisation()  # уже подключён / авторизован

    # -------------------------------------------------------------
    async def run(self):
        logger.info("Async-status-updater started")
        while not self._stop.is_set():
            try:
                await self._update_once()
            except Exception as exc:
                logger.error(f"status update failed: {exc}", exc_info=True)
            # одна-единственная «умная» пауза
            await smart_sleep(self._stop, self.interval)
        logger.info("Async-status-updater stopped")

    # -------------------------------------------------------------
    async def _update_once(self) -> None:
        devices = self.dev_mgr.get_devices()
        tuya_ids = list({d.tuya_device_id for d in devices})

        # вызываем облако в ThreadPool-е, чтобы не блокировать event-loop
        result = await asyncio.to_thread(
            self.auth.device_manager.get_device_list_status, tuya_ids
        )

        for dev_res in result.get("result", []):
            tuya_id = dev_res["id"]
            status = dev_res.get("status", [])

            # все логические каналы, «сидящие» на этом tuya-устройстве
            for dev in (d for d in devices if d.tuya_device_id == tuya_id):
                parsed = dev.extract_status(status)
                dev.update_status(parsed)
                now_ts = int(datetime.now().timestamp())
                dev.tick(now_ts)
                if dev.device_type.lower() != "pump":
                    continue
                mode_val = next(
                    (item["value"] for item in status
                     if item["code"] == dev.tuya_code_mode()),  # обычно "mode"
                    None
                )
                if mode_val is not None:
                    try:
                        shared_state["pump_mode"] = int(mode_val)
                    except (ValueError, TypeError):
                        logger.debug("[Updater] Problem with sync")

        logger.debug("[Updater] statuses synced")

    def stop(self) -> None:
        """Сообщить циклу run(), что пора завершаться."""
        self._stop.set()
