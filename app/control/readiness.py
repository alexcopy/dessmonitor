"""Pure readiness evaluator for dessmonitor.

Determines whether a specific device load is allowed to be switched ON
at the current moment, using only data already present in ReadinessInput.

The evaluator is a pure, deterministic function with no side effects:
  - Reads no files, env vars, or network resources.
  - Calls no hardware, runtime services, ML, or weather APIs.
  - Does not call time.time() or datetime.now().
  - Uses context.voltage.timestamp for cooldown calculations.
  - Same input always produces the same output.

Readiness is separate from health (deferred to PR 0015).
Readiness is not hardware execution — it never switches devices.

This module uses only:
  - Python standard library (typing)
  - app.control.energy_policy types
"""

from __future__ import annotations

from app.control.energy_policy import (
    DeviceEnergyPolicy,
    EnergyPolicyContext,
    EnergyPolicyDecision,
    LoadClass,
    PowerSource,
    ReadinessInput,
    ReadinessResult,
    TimeOfDay,
    WeatherCondition,
)

# ---------------------------------------------------------------------------
# Private pure helper functions — each returns (blocked: bool, reason: str)
# ---------------------------------------------------------------------------


def _check_load_id(policy: DeviceEnergyPolicy) -> tuple[bool, str]:
    """Verify load_id is non-empty and valid."""
    if not policy.load_id or not policy.load_id.strip():
        return (True, "invalid-load-id")
    return (False, "")


def _check_voltage_threshold(policy: DeviceEnergyPolicy, context: EnergyPolicyContext) -> tuple[bool, str]:
    """Check whether voltage is below minimum_voltage_to_switch_on."""
    voltage = context.voltage.voltage
    # Invalid voltage: must be numeric and > 0
    if not isinstance(voltage, (int, float)) or voltage <= 0:
        return (True, "invalid-voltage")
    # Threshold check
    threshold = policy.minimum_voltage_to_switch_on
    if threshold is not None and isinstance(threshold, (int, float)) and voltage < threshold:
        return (True, "below-switch-on-voltage")
    return (False, "")


def _check_power_source_conservation(policy: DeviceEnergyPolicy, context: EnergyPolicyContext) -> tuple[bool, str]:
    """Check grid/mains/network conservation for discretionary loads."""
    if policy.load_class != LoadClass.DISCRETIONARY:
        return (False, "")
    power_source = context.voltage.power_source
    if power_source in (PowerSource.GRID, PowerSource.MAINS, PowerSource.NETWORK):
        return (True, "grid-or-mains-conservation")
    return (False, "")


def _check_time_window(policy: DeviceEnergyPolicy, context: EnergyPolicyContext) -> tuple[bool, str]:
    """Check whether current time_of_day is within allowed windows."""
    windows = policy.allowed_time_windows
    if not windows:
        return (False, "")
    tod = context.time_of_day
    # Build set of allowed window identifiers from both .value and .name
    allowed = set()
    for w in windows:
        w_lower = w.lower().strip()
        allowed.add(w_lower)
    # Current time-of-day identifier (value string and name string)
    current_ids = {tod.value.lower(), tod.name.lower()}
    if not allowed.intersection(current_ids):
        return (True, "outside-allowed-time-window")
    return (False, "")


def _check_cooldown(policy: DeviceEnergyPolicy, context: EnergyPolicyContext, last_switch_timestamp: float | None) -> tuple[bool, str]:
    """Check whether cooldown period has elapsed since last switch."""
    cooldown = policy.cooldown_after_switch_seconds
    if cooldown <= 0:
        return (False, "")
    if last_switch_timestamp is None:
        return (False, "")
    elapsed = context.voltage.timestamp - last_switch_timestamp
    if elapsed < cooldown:
        return (True, "cooldown-active")
    return (False, "")


def _check_weather_skip(policy: DeviceEnergyPolicy, context: EnergyPolicyContext) -> tuple[bool, str]:
    """Check whether weather condition triggers a skip."""
    if not policy.skip_when_cloudy_or_rainy:
        return (False, "")
    condition = context.weather.condition
    if condition in (WeatherCondition.CLOUDY, WeatherCondition.RAINY,
                     WeatherCondition.STORM, WeatherCondition.SNOWY):
        return (True, "weather-skip")
    return (False, "")


def _check_evening_reserve(policy: DeviceEnergyPolicy, context: EnergyPolicyContext) -> tuple[bool, str]:
    """Check evening/night reserve protection for discretionary loads."""
    if policy.load_class != LoadClass.DISCRETIONARY:
        return (False, "")
    tod = context.time_of_day
    if tod not in (TimeOfDay.EVENING, TimeOfDay.NIGHT):
        return (False, "")
    voltage = context.voltage.voltage
    reserve_voltage = context.reserve.evening_reserve_voltage
    if isinstance(voltage, (int, float)) and isinstance(reserve_voltage, (int, float)):
        if voltage <= reserve_voltage:
            return (True, "evening-reserve-protected")
    return (False, "")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_readiness(readiness_input: ReadinessInput) -> ReadinessResult:
    """Evaluate whether a device load is ready to be switched ON.

    Pure, deterministic, side-effect-free. Uses only data from readiness_input.
    Does not call system clock, hardware, runtime services, ML, or file I/O.

    Args:
        readiness_input: ReadinessInput with policy, context, and optional
                         last_switch_timestamp.

    Returns:
        ReadinessResult with ready flag, reason string, and policy decision.
    """
    policy = readiness_input.policy
    context = readiness_input.context

    # 1. Load ID validation
    blocked, reason = _check_load_id(policy)
    if blocked:
        return ReadinessResult(ready=False, reason=reason,
                               decision=EnergyPolicyDecision.NO_ACTION)

    # 2. Voltage validation
    blocked, reason = _check_voltage_threshold(policy, context)
    if blocked:
        if reason == "invalid-voltage":
            return ReadinessResult(ready=False, reason=reason,
                                   decision=EnergyPolicyDecision.NO_ACTION)
        return ReadinessResult(ready=False, reason=reason,
                               decision=EnergyPolicyDecision.PREFER_OFF)

    # 3. Grid/mains conservation
    blocked, reason = _check_power_source_conservation(policy, context)
    if blocked:
        return ReadinessResult(ready=False, reason=reason,
                               decision=EnergyPolicyDecision.PREFER_OFF)

    # 4. Time window check
    blocked, reason = _check_time_window(policy, context)
    if blocked:
        return ReadinessResult(ready=False, reason=reason,
                               decision=EnergyPolicyDecision.PREFER_OFF)

    # 5. Cooldown check
    blocked, reason = _check_cooldown(policy, context,
                                      readiness_input.last_switch_timestamp)
    if blocked:
        return ReadinessResult(ready=False, reason=reason,
                               decision=EnergyPolicyDecision.NO_ACTION)

    # 6. Weather skip check
    blocked, reason = _check_weather_skip(policy, context)
    if blocked:
        return ReadinessResult(ready=False, reason=reason,
                               decision=EnergyPolicyDecision.PREFER_OFF)

    # 7. Evening reserve check
    blocked, reason = _check_evening_reserve(policy, context)
    if blocked:
        return ReadinessResult(ready=False, reason=reason,
                               decision=EnergyPolicyDecision.FORCE_OFF)

    # All checks passed — load is ready
    return ReadinessResult(ready=True, reason="ready",
                           decision=EnergyPolicyDecision.ALLOW_ON)
