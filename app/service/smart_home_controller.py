# app/logic/smart_home_controller.py
import asyncio
from enum import IntEnum
from pathlib import Path

from app.devices.pond_pump_controller import PondPumpController
from app.devices.pump_power_map import PRESET_DESCR
from app.devices.relay_device_manager import RelayDeviceManager
from app.logger import add_file_logger
from app.tuya.relay_tuya_controller import RelayTuyaController
from app.utils.time_utils import night_multiplier
from shared_state.shared_state import shared_state


class PumpPreset(IntEnum):
    STRICT = 1
    SUMMER = 4
    WINTER = 5
    AUTO = 6  # штатный «Manual»-режим


class SmartHomeController:
    LOG_BUSINESS_PATH = Path("logs/business_decisions.log")
    AC_MODE = "LINE MODE"

    # ------------------------------------------------------------------
    def __init__(
            self,
            dev_mgr: RelayDeviceManager,
            tuya_ctrl: RelayTuyaController,
            switch_int: int,
            pump_int: int,
    ):
        self.dev_mgr = dev_mgr
        self.ctrl = tuya_ctrl
        self.switch_int = switch_int
        self.pump_int = pump_int

        self.log_business = add_file_logger("BusinessDecisions",
                                            self.LOG_BUSINESS_PATH)
        self.pump_logic = PondPumpController()

        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._last_preset_logged: int or None = None
        self._last_switch_preset: int or None = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._stop.clear()
        self._tasks.append(loop.create_task(self._switch_loop()))
        self._tasks.append(loop.create_task(self._pump_loop()))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ───────────────────── helpers ───────────────────────────────────
    async def _sleep(self, base: int) -> None:
        """
        Унифицированная пауза: ночью (22-07) умножаем `base` на 5.
        Прерываемся мгновенно, если `self._stop.set()`.
        """
        delay = base * night_multiplier()  # 1 днём / 5 ночью
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass  # обычный переход к новой итерации

    # ───────────────────── SWITСH-цикл ───────────────────────────────
    async def _switch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                # --- 0. проверка пресета насоса ----------------------
                preset_val = int(shared_state.get("pump_mode", 6))
                preset = PumpPreset(preset_val)
                if preset in (PumpPreset.STRICT, PumpPreset.SUMMER, PumpPreset.WINTER):
                    if preset_val != self._last_switch_preset:
                        self.log_business.info(
                            f"[SWITCH] preset={preset.name} → реле-логика выключена")
                        self._last_switch_preset = preset_val
                    await self._sleep(self.switch_int)
                    continue

                # --- 1. основная логика -----------------------------
                vbat = float(shared_state.get("battery_voltage", 0.0))
                mode = (shared_state.get("working_mode") or "").upper()
                on_ac = mode == self.AC_MODE

                switches = [d for d in self.dev_mgr.get_devices()
                            if d.device_type.lower() == "switch"]

                # 1-A. питаемся от сети → все реле OFF
                if on_ac:
                    for dev in switches:
                        if dev.is_device_on() and self.ctrl.switch_off_device(dev):
                            self.log_business.info(f"[OFF] {dev.name} — AC-grid")
                    await self._sleep(self.switch_int)
                    continue

                # 1-B. питаемся от АКБ → мягкая логика
                for dev in switches:
                    if vbat >= dev.max_volt and not dev.is_device_on():
                        if self.ctrl.switch_on_device(dev):
                            self.log_business.info(
                                f"[ON ] {dev.name} — Vbat={vbat:.2f} ≥ {dev.max_volt}")
                    elif vbat <= dev.min_volt and dev.is_device_on():
                        if self.ctrl.switch_off_device(dev):
                            self.log_business.info(
                                f"[OFF] {dev.name} — Vbat={vbat:.2f} ≤ {dev.min_volt}")

            except Exception as exc:
                self.log_business.error(f"switch_loop error: {exc}", exc_info=True)

            await self._sleep(self.switch_int)

    # ───────────────────── PUMP-цикл ─────────────────────────────────
    async def _pump_loop(self) -> None:
        while not self._stop.is_set():
            try:
                volt = shared_state.get("battery_voltage", 20)
                mode = (shared_state.get("working_mode") or "").upper()
                inv_on = mode != self.AC_MODE

                preset_val = int(shared_state.get("pump_mode", 6))
                preset = PumpPreset(preset_val)

                if preset_val != self._last_preset_logged:
                    self.log_business.info(
                        f"[PUMP] preset={preset.name} — {PRESET_DESCR.get(preset_val, '')}")
                    self._last_preset_logged = preset_val

                # ручные пресеты – просто пауза
                if preset in (PumpPreset.STRICT, PumpPreset.SUMMER, PumpPreset.WINTER):
                    await self._sleep(self.pump_int)
                    continue

                # AUTO-preset
                for pump in (d for d in self.dev_mgr.get_devices()
                             if d.device_type.lower() == "pump"):

                    target = await self.pump_logic.deside_speed(pump, volt, inv_on)
                    if target is None or not pump.can_switch():
                        continue

                    cur = int(pump.status.get("P", 20))
                    if target == cur:
                        continue

                    if self.ctrl.set_numeric(pump, target):
                        pump.update_status({"P": target})
                        pump.mark_switched()
                        direction = "↑" if target > cur else "↓"
                        self.log_business.info(
                            f"[PUMP] {pump.name}: {cur} → {target} {direction} | "
                            f"Vbat={volt:.2f} | inverter_on={inv_on}")

            except Exception as exc:
                self.log_business.error(f"pump_loop error: {exc}", exc_info=True)

            await self._sleep(self.pump_int)
