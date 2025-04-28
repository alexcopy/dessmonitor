# app/logic/smart_home_controller.py
import asyncio
import logging
from pathlib import Path

from app.devices.pond_pump_controller import PondPumpController
from app.devices.relay_device_manager import RelayDeviceManager
from app.tuya.relay_tuya_controller import RelayTuyaController
from shared_state.shared_state import shared_state


class SmartHomeController:
    LOG_BUSINESS_PATH = Path("logs/business_decisions.log")
    AC_MODE           = "LINE MODE"           # «Сеть» – в UPPER-case

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
    async def _switch_loop(self):
        """ON/OFF для реле-каналов"""
        while not self._stop.is_set():
            try:
                volt   = shared_state.get("battery_voltage", 0.0)
                mode   = (shared_state.get("working_mode") or "").upper()
                on_ac  = mode == self.AC_MODE

                for dev in (d for d in self.dev_mgr.get_devices()
                            if d.device_type.lower() == "switch"):

                    if (not on_ac) and volt >= dev.max_volt and not dev.is_device_on():
                        if self.ctrl.switch_on_device(dev):
                            self.log_business.info(
                                f"[ON ] {dev.name}  — Vbat={volt:.2f} ≥ {dev.max_volt}")

                    elif (on_ac or volt <= dev.min_volt) and dev.is_device_on():
                        if self.ctrl.switch_off_device(dev):
                            reason = "AC-grid" if on_ac else f"Vbat {volt:.2f} ≤ {dev.min_volt}"
                            self.log_business.info(f"[OFF] {dev.name} — {reason}")

            except Exception as exc:
                self.log_business.error(f"switch_loop error: {exc}", exc_info=True)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.switch_int)
            except asyncio.TimeoutError:
                pass      # очередной цикл

    # ────────────────────────────────────────────────────────────────
    async def _pump_loop(self):
        """Плавная регулировка скорости насосов"""
        while not self._stop.is_set():
            try:
                volt   = shared_state.get("battery_voltage", 0.0)
                mode   = (shared_state.get("working_mode") or "").upper()
                inv_on = mode != self.AC_MODE     # true, если питаемся не от сети

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
                        direction = "↑" if target_p > cur_p else "↓"
                        self.log_business.info(
                            (f"[PUMP] {pump.name}: {cur_p} → {target_p} {direction} | "
                             f"Vbat={volt:.2f} | inverter_on={inv_on}")
                        )

            except Exception as exc:
                self.log_business.error(f"pump_loop error: {exc}", exc_info=True)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.pump_int)
            except asyncio.TimeoutError:
                pass
