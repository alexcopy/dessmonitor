import time
import logging
from datetime import datetime, time as dt_time
from typing import List, Optional, Any

from app.devices.relay_channel_device import RelayChannelDevice


class RelayTuyaController:
    def __init__(self, authorisation):
        self.authorisation = authorisation
        self.logger=logging.getLogger('tuya')
    # ---------- низкоуровневое переключение ----------
    def _send_switch_cmd(self, device: RelayChannelDevice, value) -> bool:
        # команда идёт на РОДИТЕЛЬСКОЕ устройство, а код – это channel/api_key
        cmd = {
            "devId": device.tuya_device_id,
            "commands": [{"code": device.api_key, "value": value}]
        }
        try:
            resp = self.authorisation.device_manager.send_commands(cmd["devId"], cmd["commands"])
            return bool(resp.get("success"))
        except Exception as e:
            logging.error("[RelayTuyaController] Tuya switch error", exc_info=e)
            return False
    #
    # def switch_device(self, device: RelayChannelDevice, value: bool) -> bool:
    #     try:
    #         command = {"devId": device.id, "commands": [{"code": device.api_key, "value": value}]}
    #         response = self.authorisation.device_manager.send_commands(
    #             command["devId"], command["commands"]
    #         )
    #         return bool(response.get("success"))
    #     except Exception as e:
    #         logging.error("[RelayTuyaController] Ошибка при переключении устройства")
    #         logging.error(device)
    #         logging.error(e)
    #         return False

    # ---------- публичные методы управления ----------
    def switch_on_device(self, device: RelayChannelDevice) -> bool:
        if self._send_switch_cmd(device, True):
            device.update_status({device.api_key: True})
            device.mark_switched()
            return True
        return False

    def switch_off_device(self, device: RelayChannelDevice) -> bool:
        if self._send_switch_cmd(device, False):
            device.update_status({device.api_key: False})
            device.mark_switched()
            return True
        return False

    # ---------- batch-helpers ----------
    def switch_all_on_soft(self, devices, inverter_voltage):
        for dev in devices:
            if dev.ready_to_switch_on(inverter_voltage):
                logging.info(f"[TuyaCtl] SOFT-ON: {dev.name}")
                self.switch_on_device(dev)
                time.sleep(1)

    def switch_all_off_soft(self, devices, inverter_voltage, inverter_on):
        for dev in devices:
            if dev.ready_to_switch_off(inverter_voltage, inverter_on):
                logging.info(f"[TuyaCtl] SOFT-OFF: {dev.name}")
                self.switch_off_device(dev)
                time.sleep(1)

    def switch_all_on_hard(self, devices: List[RelayChannelDevice]):
        for device in devices:
            if not device.is_device_on():
                logging.info(f"[RelayTuyaController] Жестко включаем: {device.name}")
                self.switch_on_device(device)
                time.sleep(1)

    def switch_all_off_hard(self, devices: List[RelayChannelDevice]):
        for device in devices:
            logging.info(f"[RelayTuyaController] Жестко выключаем: {device.name}")
            self.switch_off_device(device)
            time.sleep(1)

    def update_devices_status(self, devices: List[RelayChannelDevice]):
        try:
            device_ids = [d.id for d in devices]
            statuses = self.authorisation.device_manager.get_device_list_status(device_ids)
            for result in statuses.get("result", []):
                dev_id = result.get("id")
                device = self.select_device_by_id(devices, dev_id)
                if device and "status" in result:
                    device.update_status(device.extract_status(result["status"]))
        except Exception as e:
            logging.error(f"[RelayTuyaController] Ошибка получения статуса устройств: {str(e)}")

    @staticmethod
    def is_before_1830() -> bool:
        return datetime.now().time() <= dt_time(18, 30)

    @staticmethod
    def select_device_by_id(devices: List[RelayChannelDevice], dev_id: str) -> Optional[RelayChannelDevice]:
        return next((d for d in devices if str(d.id) == str(dev_id)), None)

    def switch_all_logic(self, devices: List[RelayChannelDevice], inverter_voltage: float):
        inverter = next((d for d in devices if d.name.lower() == "inverter"), None)
        if not inverter:
            logging.warning("[RelayTuyaController] Инвертор не найден среди устройств.")
            return

        if self.is_before_1830():
            self.switch_all_on_soft(devices, inverter_voltage)
        self.switch_all_off_soft(devices, inverter_voltage, inverter_on=inverter.is_device_on())


    # ──────────────────────────── публичные методы ────────────────────────────
    def switch_binary(self, dev: RelayChannelDevice, on: bool) -> bool:
        """Для «обычных» реле – true/false."""
        return self._send(dev, value=on)

    def set_numeric(self, dev: RelayChannelDevice, value: int) -> bool:
        """Для аналоговых каналов – скорость насоса, яркость …"""
        return self._send(dev, value=value)

    # универсальный «старый» alias – сохранится обратная совместимость
    def switch_device(self, dev: RelayChannelDevice, value: Any) -> bool:
        return self._send(dev, value)

    # ──────────────────────────── внутренняя логика ───────────────────────────
    def _send(self, dev: RelayChannelDevice, value: Any) -> bool:
        """
        • Выбираем code так:
            1.  dev.control_key  (новый атрибут – `P`, `switch_1` …)
            2.  fallback – dev.api_key  (оставлен для старых конфигов)
        • Формируем/шлём команду через SDK.
        """
        code = (getattr(dev, "control_key", None) or
                getattr(dev, "api_key", None))

        if not code:
            self.logger.error(f"[Tuya] device {dev.name}: no control_key/api_key")
            return False

        cmd = {"devId": dev.tuya_device_id or dev.id,
               "commands": [{"code": code, "value": value}]}

        try:
            resp = self.authorisation.deviceManager.send_commands(cmd["devId"], cmd["commands"])
            ok   = bool(resp.get("success"))
            if not ok:
                self.logger.warning(f"[Tuya] command failed {resp}")
            return ok
        except Exception as e:
            self.logger.error(f"[Tuya] send_commands error for {dev.name}: {e}", exc_info=True)
            return False