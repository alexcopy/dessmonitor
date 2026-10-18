"""Battery charge controller — enables grid charging when voltage is low.

Logic:
  - After CHARGE_HOUR_START (02:00) if battery_voltage < CHARGE_ON_VOLT (24V)
    → set charger to 'solar_and_mains' at 2A
  - When battery_voltage > CHARGE_OFF_VOLT (25.5V)
    → set charger back to 'solar_only'

Hysteresis prevents oscillation. Runs on every inverter tick (~2 min).
"""
import logging
from datetime import datetime
from shared_state.shared_state import shared_state

CHARGE_ON_VOLT      = 24.0   # enable grid charging below this
CHARGE_OFF_VOLT     = 25.5   # disable grid charging above this
CHARGE_HOUR_START   = 2      # only charge after 02:00
CHARGE_HOUR_END     = 7      # stop before sunrise 07:00
SOLAR_ONLY          = "solar_only"
SOLAR_AND_MAINS     = "solar_and_mains"
GRID_CURRENT_FIELD  = "eybond_ctrl_5"  # GRID-tie current
GRID_CURRENT_2A_VAL = "1"              # key for 2A

logger    = logging.getLogger("FULL")
important = logging.getLogger("IMPORTANT")


class BatteryChargeController:
    """Automatic grid charging based on battery voltage."""

    def __init__(self, dess_api):
        self.api = dess_api
        self._charging_active = False

    async def tick(self, battery_voltage: float | None = None) -> None:
        """Called on every inverter data cycle."""
        if battery_voltage is None:
            battery_voltage = shared_state.get("battery_voltage")
        if battery_voltage is None:
            logger.debug("[ChargeCtrl] No battery voltage available")
            return

        v = float(battery_voltage)
        now_hour = datetime.now().hour
        in_window = CHARGE_HOUR_START <= now_hour < CHARGE_HOUR_END

        if not self._charging_active and v < CHARGE_ON_VOLT and in_window:
            # Enable grid charging at 2A
            try:
                import asyncio
                await asyncio.to_thread(
                    self.api.ctrl_device, GRID_CURRENT_FIELD, GRID_CURRENT_2A_VAL
                )
                await asyncio.to_thread(
                    self.api.set_charger_source, SOLAR_AND_MAINS
                )
                self._charging_active = True
                shared_state["grid_charging_active"] = True
                important.warning(
                    "[CHARGE] Grid charging ENABLED at 2A — voltage=%.2fV < %.1fV (hour=%d)",
                    v, CHARGE_ON_VOLT, now_hour,
                )
            except Exception as exc:
                logger.error("[ChargeCtrl] Failed to enable grid charging: %s", exc)

        elif not self._charging_active and v < CHARGE_ON_VOLT and not in_window:
            logger.debug(
                "[ChargeCtrl] voltage=%.2fV below threshold but outside window (hour=%d)",
                v, now_hour,
            )

        elif self._charging_active and v > CHARGE_OFF_VOLT:
            # Disable grid charging
            try:
                import asyncio
                await asyncio.to_thread(
                    self.api.set_charger_source, SOLAR_ONLY
                )
                self._charging_active = False
                shared_state["grid_charging_active"] = False
                important.info(
                    "[CHARGE] Grid charging DISABLED — voltage=%.2fV > %.1fV",
                    v, CHARGE_OFF_VOLT,
                )
            except Exception as exc:
                logger.error("[ChargeCtrl] Failed to disable grid charging: %s", exc)

        else:
            logger.debug(
                "[ChargeCtrl] voltage=%.2fV charging=%s in_window=%s — no action",
                v, self._charging_active, in_window,
            )
