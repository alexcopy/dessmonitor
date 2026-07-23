# app/status_updater_async.py
import asyncio
import logging
from datetime import datetime, timezone

from app.device_initializer import DeviceInitializer
from app.tuya.tuya_authorisation import TuyaAuthorisation
from app.utils.time_utils import smart_sleep
from shared_state.shared_state import shared_state
TUYA_RPC_TIMEOUT = 20
logger = logging.getLogger("TuyaStatusUpdater")


class TuyaStatusUpdaterAsync:
    """
    Периодически (interval с) запрашивает статусы у облака Tuya
    и обновляет объекты RelayChannelDevice.
    Работает как корутина, которую удобно запускать через
    asyncio.create_task().

    As of PR 0034c, uses property_mapping.state_property instead
    of device.state_key.  Includes refresh_once() for one-shot
    observation used by startup reset.
    """

    def __init__(self, interval: int = 30, dev_mgr=None):
        self.interval = interval
        self._stop = asyncio.Event()
        self.dev_mgr = dev_mgr or DeviceInitializer().device_controller
        self.auth = TuyaAuthorisation()

    # -------------------------------------------------------------
    async def run(self):
        logger.info("Async-status-updater started")
        while not self._stop.is_set():
            try:
                await self._update_once()
            except Exception as exc:
                logger.error(f"status update failed: {exc}", exc_info=True)
            await smart_sleep(self._stop, self.interval)
        logger.info("Async-status-updater stopped")

    # -------------------------------------------------------------
    async def refresh_once(self) -> None:
        """Perform exactly one observation cycle.

        Used by the startup reset coordinator for immediate
        observation after OFF commands.  Does not affect the
        background polling schedule.
        """
        await self._update_once()

    # -------------------------------------------------------------
    async def _update_once(self) -> None:
        devices = self.dev_mgr.get_devices()
        tuya_ids = list({d.tuya_device_id for d in devices})

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.auth.device_manager.get_device_list_status,
                    tuya_ids
                ),
                timeout=TUYA_RPC_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning(f"[Updater] Tuya RPC > {TUYA_RPC_TIMEOUT}s – пропустили цикл")
            return

        except Exception as exc:
            logger.error(f"[Updater] Tuya RPC exception: {exc}", exc_info=True)
            return

        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())

        for dev_res in result.get("result", []):
            tuya_id = dev_res["id"]
            status_list = dev_res.get("status", [])

            status_by_code: dict[str, object] = {}
            for item in status_list:
                code = item.get("code")
                if code is not None:
                    status_by_code[str(code)] = item.get("value")

            for dev in (d for d in devices if d.tuya_device_id == tuya_id):
                # Use resolved state_property from canonical mapping
                sp = dev.property_mapping.state_property
                if sp is None:
                    continue

                value = status_by_code.get(str(sp))
                if value is not None:
                    dev.update_observation_from_tuya(value, now_utc)

                parsed = dev.extract_status(status_list)
                dev.update_status(parsed)
                dev.tick(now_ts)

                if dev.device_type.lower() != "pump":
                    continue
                mode_val = next(
                    (item["value"] for item in status_list
                     if item["code"] == dev.tuya_code_mode()),
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
