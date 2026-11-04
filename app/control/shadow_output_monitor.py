"""Bounded, read-only adapter for shadow output-policy observations."""

from collections import deque
from datetime import datetime, timedelta, timezone
import logging
import time

from app.control.adaptive_output_policy import OutputPolicyBaseline, OutputTelemetry, evaluate_output_policy


def _observed_at(value):
    """Normalize an aware timestamp within the evaluator's supported domain."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    try:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            return None
        value = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None
    return value if 2 <= value.year <= 9998 else None


class ShadowOutputMonitor:
    """Evaluate actual samples; this object has no actuator references."""

    def __init__(self, baseline=None, logger=None):
        self.baseline = baseline if baseline is not None else OutputPolicyBaseline()
        self.logger = logger or logging.getLogger("IMPORTANT")
        # Configuration bounds limit the required lookback to less than eight days.
        self.history = deque(maxlen=12000)
        self._last_log = None
        self._last_signature = None
        self._last_reported_mode = None
        self._last_reported_at = None
        self._last_reported_change_at = None

    def _record_mode_report(self, mode, received, evaluated):
        """Log changes seen at receipt; never use them as measurement-time evidence."""
        if (mode not in ("Line Mode", "Invert Mode") or received is None or received > evaluated
                or (self._last_reported_at is not None and received <= self._last_reported_at)):
            self._last_reported_mode = self._last_reported_at = self._last_reported_change_at = None
            return
        if self._last_reported_mode is not None and mode != self._last_reported_mode:
            interval = (received - self._last_reported_change_at).total_seconds() if self._last_reported_change_at else None
            self.logger.info(
                "[OUTPUT_SHADOW_REPORT] from=%s to=%s received_at=%s interval_between_reported_changes_s=%s device_time=not_used",
                self._last_reported_mode, mode, received, interval,
            )
            self._last_reported_change_at = received
        self._last_reported_mode = mode
        self._last_reported_at = received

    def observe(self, data, *, evaluated_at, received_at=None, sun_times=None):
        evaluated = _observed_at(evaluated_at)
        if evaluated is None:
            raise ValueError("evaluated_at must be timezone-aware and within years 2–9998")
        received = _observed_at(received_at)
        if received_at is not None and received is None:
            raise ValueError("received_at must be timezone-aware and within years 2–9998")
        # Legacy display timestamps, including gts, have unverified provenance.
        observed = _observed_at(getattr(data, "device_observed_at", None))
        sunrise = sunset = None
        if isinstance(sun_times, tuple) and len(sun_times) == 3:
            weather_time, start, end = (_observed_at(value) for value in sun_times)
            if (weather_time is not None and start is not None and end is not None
                    and timedelta(0) <= evaluated - weather_time <= timedelta(seconds=self.baseline.forecast_max_age_seconds)):
                sunrise, sunset = start, end
        snapshot = OutputTelemetry(
            observed_at=observed, received_at=received, working_mode=data.working_state,
            output_power_w=data.output_power, pv_power_w=data.pv_total_power,
            battery_voltage=data.battery_voltage,
            battery_current_chg=data.battery_charging_current,
            battery_current_dis=data.battery_discharging_current,
            # Forecast issue time is not available from the current integration.
            sunrise=sunrise, sunset=sunset, forecast=None,
        )
        result = evaluate_output_policy(snapshot, self.baseline, tuple(self.history), evaluated_at=evaluated)
        self._record_mode_report(data.working_state, received, evaluated)
        # Only accepted, ordered, non-future samples may prune retained history.
        if (observed is not None and observed <= evaluated
                and (received is None or observed <= received <= evaluated)
                and (not self.history or observed > self.history[-1].observed_at)):
            cutoff = observed - timedelta(days=8)
            while self.history and self.history[0].observed_at < cutoff:
                self.history.popleft()
            self.history.append(snapshot)
        now = time.monotonic()
        signature = (result.decision, result.reason_codes, result.data_quality,
                     result.battery_eligibility.classification, data.working_state, result.lockout_expires_at)
        if self._last_log is None or signature != self._last_signature or now - self._last_log >= 300:
            self.logger.info(
                "[OUTPUT_SHADOW] decision=%s reasons=%s quality=%s battery=%s observed_at=%s received_at=%s output_w=%s pv_w=%s battery_v=%s charge_a=%s discharge_a=%s reported_mode=%s lockout_until=%s history=%s",
                result.decision.value, ",".join(reason.value for reason in result.reason_codes),
                result.data_quality.value, result.battery_eligibility.classification.value,
                observed, received, data.output_power, data.pv_total_power,
                data.battery_voltage, data.battery_charging_current, data.battery_discharging_current,
                data.working_state, result.lockout_expires_at, len(self.history),
            )
            self._last_log = now
            self._last_signature = signature
        return result
