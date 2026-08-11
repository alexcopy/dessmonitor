import logging
from datetime import datetime

from app.devices.relay_device_manager import RelayDeviceManager
from app.tuya.relay_tuya_controller import RelayTuyaController
from shared_state.shared_state import shared_state


class SolarAwareController:
    HARD_FLOOR_VOLT = 25.0

    def __init__(
        self,
        dev_mgr: RelayDeviceManager,
        tuya_ctrl: RelayTuyaController,
        startup_reset_coordinator=None,
    ):
        self.dev_mgr = dev_mgr
        self.ctrl = tuya_ctrl
        self._reset_coordinator = startup_reset_coordinator
        self._important = logging.getLogger("IMPORTANT")
        self._logger = logging.getLogger("SolarAwareController")
        self._last_tick_context: tuple[bool, dict[str, float]] = (False, {})

    async def tick(self) -> None:
        if self._reset_coordinator is not None and not self._reset_coordinator.is_gate_open:
            self._important.info("[SOLAR] reset gate closed, skipping")
            return

        raw_vbat = shared_state.get("battery_voltage")
        try:
            battery_voltage = float(raw_vbat)
        except (TypeError, ValueError):
            self._important.info("[SOLAR] missing battery voltage, skipping")
            return

        devices = self.dev_mgr.get_devices()
        if battery_voltage < self.HARD_FLOOR_VOLT:
            self._important.warning(
                "[SOLAR] hard floor hit: voltage=%.2f < %.2f, soft-off all switches",
                battery_voltage,
                self.HARD_FLOOR_VOLT,
            )
            await self.ctrl.switch_all_off_soft(
                devices,
                inverter_voltage=battery_voltage,
                inverter_on=False,
                decision_logger=self._decision_logger,
            )
            return

        solar_period, clouds_pct, sunrise_hour, sunset_hour = self._is_solar_period()
        min_volt_overrides = self._build_min_volt_overrides(
            devices, solar_period, sunrise_hour, sunset_hour
        )
        self._last_tick_context = (solar_period, min_volt_overrides)
        phase = self._solar_phase(sunrise_hour, sunset_hour)

        self._important.info(
            "[SOLAR] phase=%s solar_period=%s voltage=%.2f clouds=%s sunrise=%s sunset=%s",
            phase,
            solar_period,
            battery_voltage,
            "n/a" if clouds_pct is None else f"{clouds_pct:.0f}%",
            sunrise_hour,
            sunset_hour,
        )
        await self.ctrl.switch_all_logic(
            devices,
            inverter_voltage=battery_voltage,
            allow_switch_on=solar_period,
            min_volt_overrides=min_volt_overrides,
            decision_logger=self._decision_logger,
        )

    def _is_solar_period(self) -> tuple[bool, float | None, int, int]:
        now_hour = datetime.now().hour
        sunrise_hour = self._coerce_hour(shared_state.get("sunrise_hour"), fallback=6)
        sunset_hour = self._coerce_hour(shared_state.get("sunset_hour"), fallback=20)
        clouds_pct = self._forecast_clouds_pct()
        solar_cutoff = max(sunrise_hour, sunset_hour - 3)
        is_day_window = sunrise_hour <= now_hour < solar_cutoff
        is_clear_enough = clouds_pct is not None and clouds_pct < 50.0
        return is_day_window and is_clear_enough, clouds_pct, sunrise_hour, sunset_hour

    # Phase constants (hours before sunset)
    PHASE_ACTIVE_END   = 4   # sunset-4h: start tapering
    PHASE_TAPER_END    = 1   # sunset-1h: target 26.5V
    TARGET_SUNSET_VOLT = 26.5

    def _solar_phase(self, sunrise_hour: int, sunset_hour: int) -> str:
        """Return current solar phase: 'active', 'taper', 'off', 'night'."""
        now_hour = datetime.now().hour
        if now_hour < sunrise_hour or now_hour >= sunset_hour:
            return "night"
        hours_to_sunset = sunset_hour - now_hour
        if hours_to_sunset <= self.PHASE_TAPER_END:
            return "off"      # last hour before sunset — no coefficient
        if hours_to_sunset <= self.PHASE_ACTIVE_END:
            return "taper"    # 4..1 hours before sunset — gradually reduce
        return "active"       # main solar period

    def _taper_factor(self, sunset_hour: int) -> float:
        """Linear factor 1.0→0.0 during taper phase (sunset-4h → sunset-1h)."""
        now_hour = datetime.now().hour
        hours_to_sunset = sunset_hour - now_hour
        # taper window: 4h → 1h before sunset
        taper_range = self.PHASE_ACTIVE_END - self.PHASE_TAPER_END  # = 3
        elapsed = self.PHASE_ACTIVE_END - hours_to_sunset  # 0..3
        return max(0.0, 1.0 - elapsed / taper_range)

    def _build_min_volt_overrides(
        self, devices, solar_period: bool,
        sunrise_hour: int = 6, sunset_hour: int = 20,
    ) -> dict[str, float]:
        phase = self._solar_phase(sunrise_hour, sunset_hour)
        overrides: dict[str, float] = {}
        for dev in devices:
            if dev.device_type.lower() != "switch" or dev.name.lower() == "inverter":
                continue
            try:
                min_v = float(dev.min_volt)
            except (TypeError, ValueError, AttributeError):
                continue  # skip devices without min_volt
            try:
                coef = float(dev.coefficient)
            except (TypeError, ValueError, AttributeError):
                coef = 0.0  # no coefficient — treat as normal switch
            if phase == "active" and solar_period:
                # Full coefficient — allow deeper discharge
                effective = max(self.HARD_FLOOR_VOLT, min_v - coef)
            elif phase == "taper" and solar_period:
                # Partial coefficient — taper toward min_volt
                factor = self._taper_factor(sunset_hour)
                effective = max(self.HARD_FLOOR_VOLT, min_v - coef * factor)
                # Also target 26.5V at sunset — raise floor if needed
                effective = max(effective, self.TARGET_SUNSET_VOLT * factor
                                + min_v * (1 - factor))
            else:
                # night / off / cloudy — normal thresholds
                effective = min_v
            overrides[dev.id] = effective
        return overrides

    def _forecast_clouds_pct(self) -> float | None:
        direct_value = shared_state.get("forecast_clouds_pct")
        if direct_value is not None:
            try:
                return float(direct_value)
            except (TypeError, ValueError):
                pass

        hourly = shared_state.get("forecast_hourly") or []
        if hourly:
            clouds = hourly[0].get("clouds")
            try:
                return float(clouds)
            except (TypeError, ValueError, AttributeError):
                return None
        return None

    @staticmethod
    def _coerce_hour(value, fallback: int) -> int:
        try:
            hour = int(value)
        except (TypeError, ValueError):
            return fallback
        return max(0, min(hour, 23))

    def _decision_logger(
        self,
        action: str,
        dev,
        reason: str,
        voltage: float,
        effective_min_volt: float | None,
    ) -> None:
        solar_period, last_overrides = self._last_tick_context
        if effective_min_volt is None:
            effective_min_volt = last_overrides.get(dev.id, float(dev.min_volt))
        self._important.info(
            "[SOLAR] %s %s, voltage=%.2f, effective_min_volt=%.2f, solar_period=%s, reason=%s",
            action,
            dev.name,
            voltage,
            effective_min_volt,
            solar_period,
            reason,
        )
