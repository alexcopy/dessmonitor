"""Battery charge controller — enables grid charging when voltage is low.

Logic:
  - After CHARGE_HOUR_START (02:00) if battery_voltage < CHARGE_ON_VOLT (24V)
    → set charger to solar_and_mains at 2A
  - When battery_voltage > CHARGE_OFF_VOLT (25.5V)
    → set charger back to solar_only

Logs events to TimescaleDB grid_charge_events table.
"""
import asyncio
import logging
import os
import time
from datetime import datetime

from shared_state.shared_state import shared_state

CHARGE_ON_VOLT      = 24.0
CHARGE_OFF_VOLT     = 25.5
CHARGE_HOUR_START   = 2
CHARGE_HOUR_END     = 7
SOLAR_ONLY          = "solar_only"
SOLAR_AND_MAINS     = "solar_and_mains"
GRID_CURRENT_FIELD  = "eybond_ctrl_5"
GRID_CURRENT_2A_VAL = "1"
GRID_VOLTAGE_V      = 240.0   # nominal AC voltage
GRID_CURRENT_A      = 2.0     # charging current

logger    = logging.getLogger("FULL")
important = logging.getLogger("IMPORTANT")


class BatteryChargeController:
    """Automatic grid charging based on battery voltage."""

    def __init__(self, dess_api):
        self.api = dess_api
        self._charging_active = False
        self._charge_started_at: float | None = None  # unix timestamp

    async def _log_event(self, event: str, voltage: float,
                         duration_min: float | None = None,
                         energy_kwh: float | None = None) -> None:
        """Write charge event to TimescaleDB."""
        try:
            import asyncpg
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return
            conn = await asyncpg.connect(db_url)
            await conn.execute("""
                INSERT INTO grid_charge_events
                    (time, event, voltage, duration_min, energy_kwh)
                VALUES (NOW(), $1, $2, $3, $4)
            """, event, voltage, duration_min, energy_kwh)
            await conn.close()
        except Exception as exc:
            logger.warning("[ChargeCtrl] Failed to log event: %s", exc)

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
                await asyncio.to_thread(
                    self.api.ctrl_device, GRID_CURRENT_FIELD, GRID_CURRENT_2A_VAL
                )
                await asyncio.to_thread(
                    self.api.set_charger_source, SOLAR_AND_MAINS
                )
                self._charging_active = True
                self._charge_started_at = time.time()
                shared_state["grid_charging_active"] = True
                important.warning(
                    "[CHARGE] Grid charging ENABLED at 2A — voltage=%.2fV < %.1fV (hour=%d)",
                    v, CHARGE_ON_VOLT, now_hour,
                )
                await self._log_event("enabled", v)
            except Exception as exc:
                logger.error("[ChargeCtrl] Failed to enable: %s", exc)

        elif not self._charging_active and v < CHARGE_ON_VOLT and not in_window:
            logger.debug(
                "[ChargeCtrl] voltage=%.2fV below threshold but outside window (hour=%d)",
                v, now_hour,
            )

        elif self._charging_active and v > CHARGE_OFF_VOLT:
            # Disable grid charging — calculate duration and energy
            duration_min = None
            energy_kwh = None
            if self._charge_started_at is not None:
                duration_sec = time.time() - self._charge_started_at
                duration_min = round(duration_sec / 60.0, 1)
                energy_kwh = round(
                    GRID_CURRENT_A * GRID_VOLTAGE_V * duration_sec / 3600.0 / 1000.0, 3
                )
            try:
                await asyncio.to_thread(
                    self.api.set_charger_source, SOLAR_ONLY
                )
                self._charging_active = False
                self._charge_started_at = None
                shared_state["grid_charging_active"] = False
                important.info(
                    "[CHARGE] Grid charging DISABLED — voltage=%.2fV > %.1fV "
                    "duration=%.1fmin energy=%.3fkWh",
                    v, CHARGE_OFF_VOLT,
                    duration_min or 0, energy_kwh or 0,
                )
                await self._log_event("disabled", v, duration_min, energy_kwh)
            except Exception as exc:
                logger.error("[ChargeCtrl] Failed to disable: %s", exc)

        elif self._charging_active:
            # Check if solar period has started — switch back to Solar only
            pv_power = shared_state.get("pv_total_power") or 0
            solar_period = shared_state.get("solar_period_active", False)
            if pv_power > 50 or solar_period:
                try:
                    await asyncio.to_thread(
                        self.api.set_charger_source, SOLAR_ONLY
                    )
                    self._charging_active = False
                    shared_state["grid_charging_active"] = False
                    important.info(
                        "[CHARGE] Solar arrived (PV=%.0fW) — switching to Solar only",
                        pv_power,
                    )
                    await self._log_event("solar_takeover", v,
                        duration_min=round((time.time() - self._charge_started_at) / 60.0, 1)
                        if self._charge_started_at else None)
                    self._charge_started_at = None
                except Exception as exc:
                    logger.error("[ChargeCtrl] Failed to switch to solar only: %s", exc)
            else:
                logger.debug(
                    "[ChargeCtrl] voltage=%.2fV charging=%s in_window=%s pv=%.0fW — no action",
                    v, self._charging_active, in_window, pv_power,
                )
        else:
            logger.debug(
                "[ChargeCtrl] voltage=%.2fV charging=%s in_window=%s — no action",
                v, self._charging_active, in_window,
            )
