"""Bounded, read-only adapter for shadow output-policy observations."""

from collections import deque
from datetime import datetime, timedelta, timezone
import logging
import time

from app.control.adaptive_output_policy import OutputPolicyBaseline, OutputTelemetry, evaluate_output_policy


def _observed_at(value):
    """Accept only timestamps that explicitly identify their timezone."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


class ShadowOutputMonitor:
    """Evaluate actual samples; this object has no actuator references."""

    def __init__(self, baseline=None, logger=None):
        self.baseline = baseline if baseline is not None else OutputPolicyBaseline()
        self.logger = logger or logging.getLogger("IMPORTANT")
        # Configuration bounds limit the required lookback to less than eight days.
        self.history = deque(maxlen=12000)
        self._last_log = None

    def observe(self, data, *, evaluated_at):
        observed = _observed_at(data.timestamp)
        snapshot = OutputTelemetry(
            observed_at=observed, working_mode=data.working_state,
            output_power_w=data.output_power, pv_power_w=data.pv_total_power,
            battery_voltage=data.battery_voltage,
            battery_current_chg=data.battery_charging_current,
            battery_current_dis=data.battery_discharging_current,
            # Current weather integration exposes integer hours, not verified minute times.
            sunrise=None, sunset=None, forecast=None,
        )
        if observed is not None:
            cutoff = observed - timedelta(days=8)
            while self.history and self.history[0].observed_at < cutoff:
                self.history.popleft()
        result = evaluate_output_policy(snapshot, self.baseline, tuple(self.history), evaluated_at=evaluated_at)
        # Never insert duplicate, out-of-order, or future observations into history.
        if observed is not None and observed <= evaluated_at and (not self.history or observed > self.history[-1].observed_at):
            self.history.append(snapshot)
        now = time.monotonic()
        if self._last_log is None or now - self._last_log >= 300:
            self.logger.info(
                "[OUTPUT_SHADOW] decision=%s reasons=%s quality=%s observed_at=%s output_w=%s pv_w=%s battery_v=%s charge_a=%s discharge_a=%s mode=%s lockout_until=%s history=%s",
                result.decision.value, ",".join(reason.value for reason in result.reason_codes),
                result.data_quality.value, observed, data.output_power, data.pv_total_power,
                data.battery_voltage, data.battery_charging_current, data.battery_discharging_current,
                data.working_state, result.lockout_expires_at, len(self.history),
            )
            self._last_log = now
        return result
