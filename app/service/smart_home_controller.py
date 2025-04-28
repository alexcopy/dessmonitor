# app/smart_home/smart_home_controller.py
import asyncio, logging
from typing import Sequence

from app.devices.pond_pump_controller import PondPumpController
from app.devices.relay_channel_device import RelayChannelDevice
from app.devices.relay_device_manager   import RelayDeviceManager
from app.tuya.relay_tuya_controller     import RelayTuyaController
from shared_state.shared_state          import shared_state


class SmartHomeController:
    def __init__(
        self,
        dev_mgr: RelayDeviceManager,
        tuya_ctrl: RelayTuyaController,
        switch_interval: int = 30,
        pump_interval:   int = 15,
    ):
        self.dev_mgr        = dev_mgr
        self.ctrl           = tuya_ctrl
        self.switch_int     = switch_interval
        self.pump_int       = pump_interval
        self._stop          = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.log            = logging.getLogger("SmartHome")
        self.pump_logic = PondPumpController()

    # ────────────────────────────────────────────────────────────────
    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._switch_loop()),
            loop.create_task(self._pump_loop()),
        ]
        self._stop.clear()

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ────────────────────────────────────────────────────────────────
    async def _switch_loop(self):
        while not self._stop.is_set():
            try:
                volt   = shared_state.get("battery_voltage", 0.0)   # float
                mode   = shared_state.get("working_mode",   "").upper()     # str
                devs   = self.dev_mgr.get_devices()
                ac = "Line Mode".upper()

                for d in devs:
                    if d.device_type.lower() != "switch":
                        continue   # насос/датчики не трогаем здесь

                    # ------ выбирать команду ------
                    if mode != ("%s" % ac) and volt >= d.max_volt and not d.is_device_on():
                        self._on(d)
                    elif (volt <= d.min_volt or mode == ac) and d.is_device_on():
                        self._off(d)

            except Exception as exc:
                self.log.error(f"switch_loop error: {exc}", exc_info=True)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.switch_int)
            except asyncio.TimeoutError:
                pass   # нормальный цикл

    # ────────────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────
    async def _pump_loop(self):
        while not self._stop.is_set():
            try:
                volt  = shared_state.get("battery_voltage", 0.0)
                mode  = shared_state.get("working_mode", "").upper()
                on_ac = mode == "Line Mode".upper()

                for p in self._pumps():
                    new_p = await self.pump_logic.deside_speed(p, volt, not on_ac)
                    if new_p is not None and p.can_switch():
                        if self.ctrl.set_numeric(p, new_p):
                            p.update_status({"P": new_p})
                            p.mark_switched()
                            self.log.info(f"[PUMP] {p.name} → P={new_p}")
            except Exception as exc:
                self.log.error(f"pump_loop error: {exc}", exc_info=True)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.pump_int)
            except asyncio.TimeoutError:
                pass

    # ───────────────────────────────────── helpers
    def _pumps(self) -> Sequence[RelayChannelDevice]:
        return [d for d in self.dev_mgr.get_devices()
                if d.device_type.lower() == "pump"]

    def _on(self, d: RelayChannelDevice):
        if self.ctrl.switch_binary(d, True):
            d.set_on()

    def _off(self, d: RelayChannelDevice):
        if self.ctrl.switch_binary(d, False):
            d.set_off()



