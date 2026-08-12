import asyncio
import logging
from typing import List, Optional, Any, Callable

from app.devices.relay_channel_device import RelayChannelDevice
from app.devices.device_property_mapping import (
    CommandResult,
    CommandKind,
)
from shared_state.shared_state import shared_state


class RelayTuyaController:
    """Controller for Tuya device commands with canonical property mapping.

    As of PR 0034c, all command submission uses the resolved
    DevicePropertyMapping.control_property.  No universal fallbacks.
    Five competing command methods consolidated into three:
    switch_on, switch_off, set_numeric.
    """

    def __init__(self, authorisation):
        self.authorisation = authorisation
        self.logger = logging.getLogger('tuya')

    # ---------- canonical internal command path ----------

    def _submit_command(
        self, device: RelayChannelDevice, value: Any
    ) -> CommandResult:
        """Submit a command to Tuya using the canonical property mapping."""
        if not device.enabled:
            self.logger.debug(
                "[Tuya] %s: device disabled — refusing command", device.name,
                extra={"evt": "cmd_disabled", "dev": device.name},
            )
            return CommandResult(
                success=False, accepted=False, error="device-disabled",
            )

        mapping = device.property_mapping
        if not mapping.command_capable:
            self.logger.debug(
                "[Tuya] %s: not command capable", device.name,
                extra={"evt": "cmd_skip", "dev": device.name},
            )
            return CommandResult.not_capable()

        cp = mapping.control_property
        if not cp:
            self.logger.warning(
                "[Tuya] %s: missing control property", device.name,
                extra={"evt": "cmd_no_prop", "dev": device.name},
            )
            return CommandResult.not_capable()

        cmd = {
            "devId": device.tuya_device_id,
            "commands": [{"code": cp, "value": value}],
        }

        try:
            resp = self.authorisation.device_manager.send_commands(
                cmd["devId"], cmd["commands"]
            )
            ok = bool(resp.get("success"))
            if ok:
                return CommandResult.ok()
            self.logger.warning(
                "[Tuya] %s: command rejected", device.name,
                extra={"evt": "cmd_rejected", "dev": device.name},
            )
            return CommandResult.rejected()
        except Exception as e:
            self.logger.error(
                "[Tuya] %s: send_commands error", device.name,
                extra={"evt": "cmd_error", "dev": device.name},
                exc_info=True,
            )
            return CommandResult(
                success=False, accepted=False,
                error="command-exception",
            )

    # ---------- public command methods ----------

    def switch_on(self, device: RelayChannelDevice) -> CommandResult:
        """Submit an ON command using the canonical property mapping.

        Validates command_kind is binary.  Does NOT update canonical
        observation.
        """
        mapping = device.property_mapping
        if mapping.command_kind != CommandKind.BINARY:
            return CommandResult(
                success=False, accepted=False,
                error="not-binary-device",
            )
        result = self._submit_command(device, True)
        if result.accepted:
            cp = mapping.control_property
            if cp:
                device.update_status({cp: True})
            device.mark_switched()
        return result

    def switch_off(self, device: RelayChannelDevice) -> CommandResult:
        """Submit an OFF command using the canonical property mapping."""
        mapping = device.property_mapping
        if mapping.command_kind != CommandKind.BINARY:
            return CommandResult(
                success=False, accepted=False,
                error="not-binary-device",
            )
        result = self._submit_command(device, False)
        if result.accepted:
            cp = mapping.control_property
            if cp:
                device.update_status({cp: False})
            device.mark_switched()
        return result

    def set_numeric(self, device: RelayChannelDevice, value: int) -> CommandResult:
        """Submit a numeric command using the canonical property mapping.

        Validates command_kind is numeric.  Rejects binary-only devices.
        """
        mapping = device.property_mapping
        if mapping.command_kind != CommandKind.NUMERIC:
            return CommandResult(
                success=False, accepted=False,
                error="not-numeric-device",
            )
        result = self._submit_command(device, value)
        if result.accepted:
            cp = mapping.control_property
            if cp:
                device.update_status({cp: value})
            device.mark_switched()
        return result

    # ---------- deprecated backward-compat stubs ----------

    def switch_on_device(self, device: RelayChannelDevice) -> bool:
        """Deprecated — use switch_on()."""
        return self.switch_on(device).accepted

    def switch_off_device(self, device: RelayChannelDevice) -> bool:
        """Deprecated — use switch_off()."""
        return self.switch_off(device).accepted

    def switch_binary(self, dev: RelayChannelDevice, on: bool) -> bool:
        """Deprecated — use switch_on/switch_off."""
        if on:
            return self.switch_on(dev).accepted
        return self.switch_off(dev).accepted

    def switch_device(self, dev: RelayChannelDevice, value: Any) -> bool:
        """Deprecated — use switch_on/switch_off/set_numeric."""
        if isinstance(value, bool):
            if value:
                return self.switch_on(dev).accepted
            return self.switch_off(dev).accepted
        return self.set_numeric(dev, int(value)).accepted

    # ---------- batch helpers ----------

    async def switch_all_on_soft(
        self,
        devices,
        inverter_voltage,
        max_volt_overrides: dict[str, float] | None = None,
        decision_logger: Callable[[str, RelayChannelDevice, str, float, float | None], None] | None = None,
    ):
        # Sort by priority ascending — low priority number = important = switch on first
        sorted_devices = sorted(
            [d for d in devices if d.device_type.lower() == "switch" and d.name.lower() != "inverter"],
            key=lambda d: getattr(d, "priority", 99)
        )
        for dev in sorted_devices:
            max_ovr = (max_volt_overrides or {}).get(dev.id)
            if dev.ready_to_switch_on(inverter_voltage, max_volt_override=max_ovr):
                logging.info(f"[TuyaCtl] SOFT-ON: {dev.name}")
                if decision_logger:
                    decision_logger(
                        "ON",
                        dev,
                        f"voltage {inverter_voltage:.2f} > max_volt {(max_ovr or dev.max_volt):.2f}",
                        inverter_voltage,
                        None,
                    )
                self.switch_on_device(dev)
                await asyncio.sleep(1)

    async def switch_all_off_soft(
        self,
        devices,
        inverter_voltage,
        inverter_on,
        min_volt_overrides: dict[str, float] | None = None,
        decision_logger: Callable[[str, RelayChannelDevice, str, float, float | None], None] | None = None,
    ):
        # Sort by priority descending — less important (high number) switches off first
        sorted_devices = sorted(
            [d for d in devices if d.device_type.lower() == "switch" and d.name.lower() != "inverter"],
            key=lambda d: getattr(d, "priority", 0),
            reverse=True,
        )
        for dev in sorted_devices:
            # Important devices (low priority number) ignore min_trashhold — prevents oscillation
            ignore_thresh = getattr(dev, "priority", 99) <= 5
            should_off, reason = self._should_switch_off(
                dev=dev,
                inverter_voltage=inverter_voltage,
                inverter_on=inverter_on,
                min_volt_override=(min_volt_overrides or {}).get(dev.id),
                ignore_min_trashhold=ignore_thresh,
            )
            if should_off:
                logging.info(f"[TuyaCtl] SOFT-OFF: {dev.name}")
                if decision_logger:
                    decision_logger(
                        "OFF",
                        dev,
                        reason,
                        inverter_voltage,
                        (min_volt_overrides or {}).get(dev.id),
                    )
                self.switch_off_device(dev)
                await asyncio.sleep(1)

    async def switch_all_on_hard(self, devices: List[RelayChannelDevice]):
        for device in devices:
            if not device.is_device_on():
                logging.info(f"[RelayTuyaController] Жестко включаем: {device.name}")
                self.switch_on_device(device)
                await asyncio.sleep(1)

    async def switch_all_off_hard(self, devices: List[RelayChannelDevice]):
        """Command every device OFF using the canonical property mapping."""
        for device in devices:
            logging.info(
                "[RESET] %s: submitting OFF", device.name,
                extra={"evt": "startup_reset_cmd", "dev": device.name},
            )
            self.switch_off_device(device)
            await asyncio.sleep(0.5)  # 500ms inter-command delay

    def update_devices_status(self, devices: list[RelayChannelDevice]) -> None:
        try:
            tuya_ids = [d.id for d in devices]
            response = self.authorisation.device_manager.get_device_list_status(tuya_ids)

            for dev_json in response.get("result", []):
                dev_id = dev_json.get("id")
                device = self.select_device_by_id(devices, dev_id)
                if not (device and "status" in dev_json):
                    continue

                # распарсим и сохраним статус
                parsed = device.extract_status(dev_json["status"])
                device.update_status(parsed)

                # --- датчик температуры пруда --------------------------------
                if device.name.lower() in ("watertemp", "pondtemp"):
                    raw_t = parsed.get("temp_current")
                    if raw_t is not None:
                        from shared_state.shared_state import shared_state
                        shared_state["ambient_temp"] = raw_t / 10
                        logging.getLogger("FULL").debug(
                            "[TempSensor] ambient_temp set to %.1f °C", raw_t / 10
                        )

        except Exception as exc:
            logging.error(
                "[RelayTuyaController] Ошибка получения статуса устройств: %s", exc,
                exc_info=True
            )

    @staticmethod
    def select_device_by_id(devices: List[RelayChannelDevice], dev_id: str) -> Optional[RelayChannelDevice]:
        return next((d for d in devices if str(d.id) == str(dev_id)), None)

    def _should_switch_off(
        self,
        dev: RelayChannelDevice,
        inverter_voltage: float,
        inverter_on: bool,
        min_volt_override: float | None = None,
        ignore_min_trashhold: bool = False,
    ) -> tuple[bool, str]:
        if not dev.is_device_on():
            logging.debug(f"[{dev.name}] Already OFF")
            return False, "already off"
        if not inverter_on:
            return True, "inverter off"
        if not ignore_min_trashhold:
            min_trashhold = float(dev.extra.get("min_trashhold", 0))
            if inverter_voltage < min_trashhold:
                return True, f"voltage {inverter_voltage:.2f} < min_trashhold {min_trashhold:.2f}"
        if not dev.can_switch():
            return False, "time delay active"
        effective_min = float(min_volt_override if min_volt_override is not None else dev.min_volt)
        if inverter_voltage < effective_min:
            return True, f"voltage {inverter_voltage:.2f} < min_volt {effective_min:.2f}"
        return False, "threshold not reached"

    async def switch_all_logic(
        self,
        devices: List[RelayChannelDevice],
        inverter_voltage: float,
        allow_switch_on: bool = True,
        min_volt_overrides: dict[str, float | tuple] | None = None,
        decision_logger: Callable[[str, RelayChannelDevice, str, float, float | None], None] | None = None,
        inverter_on: bool = True,
    ):
        # Split overrides into min/max dicts (support tuple (min,max) or float (min only))
        _min_overrides: dict[str, float] = {}
        _max_overrides: dict[str, float] = {}
        for dev_id, val in (min_volt_overrides or {}).items():
            if isinstance(val, tuple):
                _min_overrides[dev_id] = val[0]
                _max_overrides[dev_id] = val[1]
            else:
                _min_overrides[dev_id] = val
        # inverter_on can be passed directly; fallback to device lookup
        inverter = next((d for d in devices if d.name.lower() == "inverter"), None)
        if inverter is not None:
            inverter_on = inverter.is_device_on()
        if allow_switch_on and inverter_on:
            await self.switch_all_on_soft(
                devices,
                inverter_voltage,
                max_volt_overrides=_max_overrides,
                decision_logger=decision_logger,
            )
        await self.switch_all_off_soft(
            devices,
            inverter_voltage,
            inverter_on=inverter_on,
            min_volt_overrides=_min_overrides,
            decision_logger=decision_logger,
        )
