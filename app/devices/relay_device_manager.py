import logging
from typing import List, Dict

from app.devices.relay_channel_device import RelayChannelDevice


class RelayDeviceManager:
    def __init__(self, power_limit: float = 10000):
        self._devices: Dict[str, RelayChannelDevice] = {}
        self._device_order: List[RelayChannelDevice] = []
        self._power_limit = power_limit
        self.logger = logging.getLogger("RelayDeviceManager")

    def add_device(self, device: RelayChannelDevice):
        if device.id in self._devices:
            raise ValueError(f"Device with ID '{device.id}' already exists.")

        self._devices[device.id] = device
        self._device_order.append(device)

    def remove_device(self, device: RelayChannelDevice):
        if device.id not in self._devices:
            raise ValueError(f"Device with ID '{device.id}' does not exist.")

        del self._devices[device.id]
        self._device_order.remove(device)

    def get_devices(self) -> List[RelayChannelDevice]:
        return self._device_order

    def get_device_by_id(self, device_id: str) -> RelayChannelDevice:
        if device_id not in self._devices:
            raise ValueError(f"Device with ID '{device_id}' does not exist.")
        return self._devices[device_id]

    def get_devices_by_name(self, name: str) -> List[RelayChannelDevice]:
        return [d for d in self._device_order if d.name == name]

    def get_devices_by_priority(self) -> List[RelayChannelDevice]:
        return sorted(self._device_order, key=lambda d: d.priority)

    def get_devices_by_desc(self, desc: str) -> List[RelayChannelDevice]:
        return [d for d in self._device_order if desc.lower() in d.desc.lower()]

    def update_all_statuses(self, status_provider):
        """
        status_provider: Callable[[RelayChannelDevice], dict]
        """
        for device in self._device_order:
            status = status_provider(device)
            if status:
                device.update_status(status)

    def get_available_power(self) -> float:
        used_power = sum(device.power_consumption() for device in self._device_order)
        return self._power_limit - used_power

    def total_power(self) -> float:
        return sum(device.power_consumption() for device in self._device_order)

    def sort_devices_by_priority(self):
        self._device_order.sort(key=lambda d: d.priority)

    def all_devices_on(self) -> List[RelayChannelDevice]:
        return [d for d in self._device_order if d.is_device_on()]

    def all_devices_off(self) -> List[RelayChannelDevice]:
        return [d for d in self._device_order if not d.is_device_on()]


    def toggle_device(self, device_id: str, turn_on: bool, tuya_controller) -> bool:
        """
        Включить/выключить устройство и синхронизировать статус.
        :param device_id: id в self._devices (для multi-switch это '1-3', '1-4', ...)
        :param turn_on:   True → ON, False → OFF
        :param tuya_controller: объект, умеющий отправлять команду в Tuya Cloud
        """
        if device_id not in self._devices:
            raise ValueError(f"Device '{device_id}' not found")

        dev = self._devices[device_id]

        # команда в облако Tuya (пример: {"code": dev.api_key, "value": turn_on})
        ok = tuya_controller.switch_device(dev, turn_on)

        if ok:
            if turn_on:
                dev.set_on()
            else:
                dev.set_off()
            self.logger.info(f"[MANAGER] {'ON' if turn_on else 'OFF'}  ->  {dev.name}")
        else:
            self.logger.error(f"[MANAGER] Tuya command failed for {dev.name}")

        return ok
