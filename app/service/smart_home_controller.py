# app/logic/smart_home_controller.py
import asyncio
import logging
from enum import IntEnum
from pathlib import Path

from app.devices.pond_pump_controller import PondPumpController
from app.devices.pump_power_map import PRESET_DESCR
from app.devices.relay_device_manager import RelayDeviceManager
from app.tuya.relay_tuya_controller import RelayTuyaController
from shared_state.shared_state import shared_state


class PumpPreset(IntEnum):
    STRICT = 1
    SUMMER = 4
    WINTER = 5
    AUTO   = 6           # заводской «Manual»

class SmartHomeController:
    LOG_BUSINESS_PATH = Path("logs/business_decisions.log")
    AC_MODE           = "LINE MODE"           # «Сеть»

    # ────────────────────────────────────────────────────────────────
    def __init__(
        self,
        dev_mgr:    RelayDeviceManager,
        tuya_ctrl:  RelayTuyaController,
        switch_int: int = 60,
        pump_int:   int = 30,
    ):
        self.dev_mgr    = dev_mgr
        self.ctrl       = tuya_ctrl
        self.switch_int = switch_int
        self.pump_int   = pump_int

        self.pump_logic = PondPumpController()
        self._stop      = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._last_preset_logged: int or None = None
        # ⚙️ создаём/получаем нужный логгер ОДИН раз
        self.log_business = self._make_logger()

    # ────────────────────────────────────────────────────────────────
    @classmethod
    def _make_logger(cls) -> logging.Logger:
        """
        Отдельный файловый лог: logs/business_decisions.log
        """
        cls.LOG_BUSINESS_PATH.parent.mkdir(exist_ok=True)          # ← создаём каталог
        lg = logging.getLogger("BusinessDecisions")
        if not lg.handlers:                                        # ← чтобы не плодить
            lg.setLevel(logging.INFO)
            fh = logging.FileHandler(cls.LOG_BUSINESS_PATH, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
            lg.addHandler(fh)
        return lg

    # ────────────────────────────────────────────────────────────────
    def start(self):
        loop = asyncio.get_running_loop()
        self._stop.clear()
        self._tasks.append(loop.create_task(self._switch_loop()))
        self._tasks.append(loop.create_task(self._pump_loop()))

    async def stop(self):
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ────────────────────────────────────────────────────────────────
    async def _switch_loop(self) -> None:
        """
        • STRICT / SUMMER / WINTER ─ выключаем всю автоматику реле
        • AC-grid                ─ жёстко OFF
        • Battery                ─ мягкая логика (min/max-volt)
        """
        while not self._stop.is_set():

            try:
                # ─── 0. preset насоса ───────────────────────────────
                try:
                    preset_val = int(shared_state.get("pump_mode", 6))
                    preset = PumpPreset(preset_val)
                except ValueError:
                    preset_val = 6
                    preset = PumpPreset.AUTO

                if preset in (PumpPreset.STRICT,
                              PumpPreset.SUMMER,
                              PumpPreset.WINTER):
                    if preset_val != getattr(self, "_last_switch_preset", None):
                        self.log_business.info(
                            f"[SWITCH] preset = {preset.name} – AUTO-control OFF"
                        )
                        self._last_switch_preset = preset_val
                    # спим и переходим к следующему циклу
                    await self._sleep()
                    continue

                # ─── 1. основная логика ON / OFF ────────────────────
                vbat = float(shared_state.get("battery_voltage", 0.0))
                mode = (shared_state.get("working_mode") or "").upper()
                on_ac = mode == self.AC_MODE

                switches = [d for d in self.dev_mgr.get_devices()
                            if d.device_type.lower() == "switch"]

                if on_ac:  # 1-A. питаемся от сети → всё OFF
                    for dev in switches:
                        if dev.is_device_on() and self.ctrl.switch_off_device(dev):
                            self.log_business.info(f"[OFF] {dev.name} – AC-grid")
                    await self._sleep()
                    continue

                # 1-B. питаемся от АКБ → мягкая логика
                for dev in switches:
                    if vbat >= dev.max_volt and not dev.is_device_on():
                        if self.ctrl.switch_on_device(dev):
                            self.log_business.info(
                                f"[ON ] {dev.name} – Vbat={vbat:.2f} ≥ {dev.max_volt}"
                            )
                    elif vbat <= dev.min_volt and dev.is_device_on():
                        if self.ctrl.switch_off_device(dev):
                            self.log_business.info(
                                f"[OFF] {dev.name} – Vbat={vbat:.2f} ≤ {dev.min_volt}"
                            )

            except Exception as exc:
                self.log_business.error(f"switch_loop error: {exc}", exc_info=True)

            # ─── пауза между итерациями ─────────────────────────────
            await self._sleep()

    # ────────────────────────────────────────────────────────────────
    async def _sleep(self) -> None:
        """Ждём switch_int секунд или пока self._stop не будет поднят."""
        try:
            await asyncio.wait_for(
                asyncio.shield(self._stop.wait()),
                timeout=self.switch_int
            )
        except asyncio.TimeoutError:
            # тай-аут → нормальный переход к следующему циклу
            pass
        except asyncio.CancelledError:
            # корутину отменили (controller.stop()) – пробрасываем выше
            raise

    # ───────────────────────────────────────────────────────────────
    async def _pump_loop(self) -> None:
        """Плавная подстройка скорости насосов (код «P»)."""

        while not self._stop.is_set():
            try:
                volt = shared_state.get("battery_voltage", 0.0)
                mode = (shared_state.get("working_mode") or "").upper()
                inv_on = mode != self.AC_MODE  # False → питаемся от сети

                # ----- текущий preset, выбранный в мобильном приложении -----
                preset_val = int(shared_state.get("pump_mode", 6))
                preset = PumpPreset(preset_val)

                # логируем ТОЛЬКО при смене preset’а
                if preset_val != self._last_preset_logged:
                    self.log_business.info(
                        f"[PUMP] preset = {preset.name}  –  {PRESET_DESCR.get(preset_val, '???')}"
                    )
                    self._last_preset_logged = preset_val

                # ①  «ручные» пресеты → никакой авто-регулировки
                if preset in (PumpPreset.STRICT, PumpPreset.SUMMER, PumpPreset.WINTER):
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.pump_int)
                    except asyncio.TimeoutError:
                        pass
                    continue  # переходим к следующей итерации while

                # ②  AUTO-режим (preset 6)  → обычная логика
                for pump in (d for d in self.dev_mgr.get_devices()
                             if d.device_type.lower() == "pump"):

                    target_p = await self.pump_logic.deside_speed(pump, volt, inv_on)
                    if target_p is None or not pump.can_switch():
                        continue

                    cur_p = int(pump.status.get("P", 0))
                    if target_p == cur_p:
                        continue

                    if self.ctrl.set_numeric(pump, target_p):
                        pump.update_status({"P": target_p})
                        pump.mark_switched()
                        dir_sym = "↑" if target_p > cur_p else "↓"
                        self.log_business.info(
                            (f"[PUMP] {pump.name}: {cur_p} → {target_p} {dir_sym} | "
                             f"Vbat={volt:.2f} | inverter_on={inv_on}")
                        )

            except Exception as exc:
                self.log_business.error(f"pump_loop error: {exc}", exc_info=True)

            # ————————————————————— пауза между циклами ————————————————————
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.pump_int)
            except asyncio.TimeoutError:
                pass

