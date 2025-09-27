import threading
import time
import logging
from collections import defaultdict
from typing import Dict, List

from app.device_initializer   import DeviceInitializer
from app.devices.relay_channel_device import RelayChannelDevice
from tuya_iot import (
    TuyaOpenAPI, TuyaDeviceManager, TuyaOpenMQ, AuthType, TUYA_LOGGER,
    TuyaCloudOpenAPIEndpoint as Endpoint
)

class TuyaStatusUpdater(threading.Thread):
    """
    Пулы каждую N сек. статусы устройств Tuya и обновляет RelayChannelDevice.
    Работает в отдельном потоке — не блокирует основной цикл.
    """

    def __init__(self, interval: int = 120):
        super().__init__(daemon=True)
        self.interval = interval
        self.logger   = logging.getLogger("TuyaStatusUpdater")
        self.device_mgr = DeviceInitializer().device_controller      # наш RelayDeviceManager
        self._stop = threading.Event()

        # ---- авторизация Tuya ----
        tuya_cfg  = DeviceInitializer().get_tuya_config()
        endpoint  = getattr(Endpoint, tuya_cfg.get("ENDPOINT", "EUROPE"))
        access_id = tuya_cfg.get("ACCESS_ID", "")
        access_key= tuya_cfg.get("ACCESS_KEY", "")

        self.openapi = TuyaOpenAPI(endpoint, access_id, access_key)
        self.openapi.connect()                    # токен
        self.openapi.auth_type = AuthType.SMART_HOME
        self.tuya_mgr = TuyaDeviceManager(self.openapi, TuyaOpenMQ(self.openapi))

    # ------------------------------------------------------------------
    def run(self):
        self.logger.info("TuyaStatusUpdater started")
        while not self._stop.is_set():
            start = time.time()
            try:
                self._update_once()
            except Exception as exc:
                self.logger.error(f"Status update failed: {exc}", exc_info=True)

            # спим остаток интервала
            sleep_for = self.interval - (time.time() - start)
            if sleep_for > 0:
                self._stop.wait(sleep_for)

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    def _update_once(self):
        """Собираем stat запросами pack по tuya_device_id."""
        # 1. группы {tuya_device_id: [RelayChannelDevice, ...]}
        groups: Dict[str, List[RelayChannelDevice]] = defaultdict(list)
        for dev in self.device_mgr.get_devices():
            groups[dev.tuya_device_id].append(dev)

        # 2. для каждой группы получаем статус устройства (одного)
        for tuya_id, dev_list in groups.items():
            res = self.tuya_mgr.get_device_detail(tuya_id)        # базовый инфо
            status_arr = res.get("status", []) or \
                         self.tuya_mgr.get_device_status(tuya_id) # fallback

            # status_arr = [{"code": "switch_1", "value": True}, ...]
            # Приведём к удобному dict
            cloud_state = {item["code"]: item["value"] for item in status_arr}

            # 3. накладываем на каждый локальный RelayChannelDevice
            for dev in dev_list:
                if dev.api_key in cloud_state:
                    dev.update_status({dev.api_key: cloud_state[dev.api_key]})
                    self.logger.debug(
                        f"[SYNC] {dev.name} ({dev.id}) = {cloud_state[dev.api_key]}"
                    )

        # Для отладочного/сводного лога
        active = [d.name for d in self.device_mgr.all_devices_on()]
        self.logger.info(f"[SUMMARY] ON: {', '.join(active) if active else 'none'}")

