"""Hot-reloaded charging-source policy; never controls output source priority."""
import asyncio
import logging
import math
import os
import time
from datetime import datetime

from app.device_initializer import DeviceInitializer
from shared_state.shared_state import shared_state

SOLAR_ONLY = "solar_only"
SOLAR_AND_MAINS = "solar_and_mains"
GRID_CURRENT_FIELD = "eybond_ctrl_5"
GRID_CURRENT_2A_VAL = "1"  # Only verified current mapping: 2 A.
GRID_VOLTAGE_V = 240.0
logger = logging.getLogger("FULL")
important = logging.getLogger("IMPORTANT")


def charger_mode(value):
    # Explicit known labels only; neither numeric codes nor unverified inverter
    # abbreviations are inferred from the write-command enum.
    if not isinstance(value, str):
        return None
    normalized = "_".join(value.strip().lower().split())
    aliases = {
        "solar_only": SOLAR_ONLY,
        "only_solar": SOLAR_ONLY,
        "solar_and_mains": SOLAR_AND_MAINS,
        "solar_and_utility": SOLAR_AND_MAINS,
        "solar_priority": "solar_priority",
        "solar_first": "solar_priority",
    }
    return aliases.get(normalized)


def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def in_time_window(now, start, end):
    def minutes(value):
        hour, minute = map(int, value.split(":"))
        return hour * 60 + minute
    current = now.hour * 60 + now.minute
    start, end = minutes(start), minutes(end)
    return start <= current < end if start < end else current >= start or current < end


class BatteryChargeController:
    def __init__(self, dess_api, config_path=None):
        self.api = dess_api
        self.config_path = DeviceInitializer._resolve_config_path(config_path)
        self.policy = DeviceInitializer.normalize_charge_policy({})
        self.state = "idle"
        self._signature = None
        self._policy_error = None
        self._last_mode = None
        self._last_command_at = 0.0
        self._current_set = None
        self._reconciled = False
        self._charge_started_at = None

    def _load_policy(self):
        try:
            stat = os.stat(self.config_path)
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if signature != self._signature:
                policy = DeviceInitializer.read_charge_policy_config(self.config_path)
                self.policy = policy
                self._signature = signature
            self._policy_error = None
        except (OSError, ValueError) as exc:
            # Validator errors contain fixed field names, never raw YAML values.
            detail = str(exc) if isinstance(exc, ValueError) else "Configuration file unavailable"
            message = f"{detail}; retaining last valid charge policy"
            if self._policy_error != message:
                logger.warning("[ChargeCtrl] %s", message)
            self._policy_error = message

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

    async def tick(self, battery_voltage=None):
        await asyncio.to_thread(self._load_policy)
        p = self.policy
        e, seasonal = p["emergency"], p["seasonal_topup"]
        v = finite_number(battery_voltage if battery_voltage is not None else shared_state.get("battery_voltage"))
        pv = finite_number(shared_state.get("pv_total_power"))
        observed = charger_mode(shared_state.get("charger_priority"))
        actual = observed if observed is not None else self._last_mode
        sunrise = finite_number(shared_state.get("sunrise_hour"))
        sunset = finite_number(shared_state.get("sunset_hour"))
        daylight = sunset - sunrise if sunrise is not None and sunset is not None and 0 <= sunrise < sunset <= 24 else None
        long_night = daylight < seasonal["daylight_threshold_hours"] if daylight is not None else None
        window = in_time_window(datetime.now(), seasonal["start_time"], seasonal["end_time"])
        season_allowed = seasonal["enabled"] and (
            seasonal["mode"] == "always" or seasonal["mode"] == "auto_long_night" and long_night is True)
        eligible = season_allowed and window
        solar = p["solar_takeover"]["enabled"] and pv is not None and pv >= p["solar_takeover"]["min_pv_w"]
        desired = None
        reason = "Battery above emergency threshold"
        event = "disabled"
        if not p["enabled"]:
            # Disabling automation relinquishes control; it is not a manual OFF
            # command. Preserve observed state, and reconcile again on re-enable.
            self.state = "idle"
            self._reconciled = False
            reason = "Automatic battery protection disabled; charger unchanged"
        elif v is None or v <= 0:
            reason = "Battery voltage unavailable; charger unchanged"
        else:
            if not self._reconciled and observed is not None:
                self._reconciled = True
                # After restart, an observed grid session may be a legitimate
                # recovery. Finish to release, or to target in an eligible window.
                # Healthy idle batteries never initiate scheduled charging.
                if observed == SOLAR_AND_MAINS:
                    if v < e["release_above_v"]:
                        self.state = "emergency_recovery"
                    elif eligible and v < seasonal["target_voltage_v"]:
                        self.state = "long_night_topup"
            if v < e["grid_charge_below_v"]:
                self.state = "emergency_recovery"
            if self.state in ("emergency_recovery", "long_night_topup"):
                if v < e["release_above_v"]:
                    self.state = "emergency_recovery"
                    desired = SOLAR_AND_MAINS
                    reason = "Emergency recovery below release voltage"
                elif eligible and v < seasonal["target_voltage_v"] and not solar:
                    self.state = "long_night_topup"
                    desired = SOLAR_AND_MAINS
                    reason = "Emergency recovery extended during long-night window"
                else:
                    if solar:
                        reason, event = "Emergency released; sufficient PV available", "solar_takeover"
                    elif v >= seasonal["target_voltage_v"]:
                        reason = "Recovery target reached"
                    elif not window:
                        reason = "Emergency released; recovery window inactive"
                    else:
                        reason = "Emergency released; seasonal extension inactive"
                    self.state = "idle"
                    desired = SOLAR_ONLY
            else:
                desired = SOLAR_ONLY
        error = None
        if desired is not None:
            try:
                actual = await self._request_mode(desired, observed, actual, v, event)
            except Exception:
                error = "Charger command failed; will retry on next observation"
                logger.warning("[ChargeCtrl] %s", error)
        grid = actual == SOLAR_AND_MAINS if actual is not None else None
        shared_state["grid_charging_active"] = grid
        shared_state["charge_policy_status"] = {
            "state": self.state, "reason": reason,
            "battery_voltage": v, "pv_power_w": pv,
            "desired_charger_mode": desired, "observed_charger_mode": observed,
            "grid_charging": grid,
            "emergency_active": self.state == "emergency_recovery",
            "seasonal_extension_active": self.state == "long_night_topup",
            "seasonal_mode": seasonal["mode"], "long_night": long_night,
            "daylight_hours": daylight, "window_active": window,
            "window_start": seasonal["start_time"], "window_end": seasonal["end_time"],
            "target_voltage_v": seasonal["target_voltage_v"] if self.state == "long_night_topup" else e["release_above_v"],
            "seasonal_reason": "Sunrise/sunset data unavailable" if daylight is None else (
                "Seasonal extension eligible" if eligible else "Seasonal extension inactive"),
            "observation_reason": "Charger mode unavailable or unrecognized; using last successful command" if observed is None else None,
            "policy_error": self._policy_error, "command_error": error,
        }

    async def _request_mode(self, desired, observed, actual, voltage, event):
        # Source telemetry does not prove the current setting. Assert the only
        # verified current once per process, even when the source already matches.
        if desired == SOLAR_AND_MAINS and self._current_set != self.policy["grid_charge"]["current_a"]:
            await asyncio.to_thread(self.api.ctrl_device, GRID_CURRENT_FIELD, GRID_CURRENT_2A_VAL)
            self._current_set = self.policy["grid_charge"]["current_a"]
        if observed == desired:
            self._last_mode = desired
            return observed
        # Missing telemetry and persistent mismatches both get a bounded grace
        # period after a successful write. Never trust the cache indefinitely.
        if self._last_mode == desired and time.monotonic() - self._last_command_at < 120:
            return actual
        await asyncio.to_thread(self.api.set_charger_source, desired)
        self._last_mode = desired
        self._last_command_at = time.monotonic()
        if desired == SOLAR_AND_MAINS:
            if self._charge_started_at is None:
                self._charge_started_at = time.time()
                await self._log_event("enabled", voltage)
        else:
            duration = energy = None
            if self._charge_started_at is not None:
                seconds = time.time() - self._charge_started_at
                duration = round(seconds / 60, 1)
                energy = round(self.policy["grid_charge"]["current_a"] * GRID_VOLTAGE_V * seconds / 3600000, 3)
            self._charge_started_at = None
            await self._log_event(event, voltage, duration, energy)
        important.info("[CHARGE] Charger requested: %s at %.2f V", desired, voltage)
        return desired
