"""Pure weather adjustment evaluator for dessmonitor.

Translates a passive WeatherForecastSignal into an advisory energy adjustment
result with a suggested decision, an adjustment factor, a reason string, and a
recommended follow-up.

The evaluator is pure, deterministic, and side-effect-free:
  - Does not fetch weather or call external APIs.
  - Does not import weather runtime modules (aiohttp, requests, etc.).
  - Does not read files, env vars, or network resources.
  - Does not switch devices or call hardware.
  - Does not read current time.
  - Same input always produces the same output.

This module uses only:
  - Python standard library (dataclasses, typing)
  - app.control.energy_policy types: WeatherForecastSignal, WeatherCondition,
    EnergyPolicyDecision
"""

from __future__ import annotations

from dataclasses import dataclass

from app.control.energy_policy import (
    EnergyPolicyDecision,
    WeatherCondition,
    WeatherForecastSignal,
)

# ---------------------------------------------------------------------------
# Weather adjustment mapping (immutable, defined at module level)
# ---------------------------------------------------------------------------

_ADJUSTMENT_MAP: dict[WeatherCondition, tuple[EnergyPolicyDecision, float, str, str]] = {
    WeatherCondition.SUNNY:  (EnergyPolicyDecision.ALLOW_ON,   1.15, "sunny-spend",       "none"),
    WeatherCondition.CLOUDY: (EnergyPolicyDecision.PREFER_OFF, 0.75, "cloudy-conserve",   "monitor-weather"),
    WeatherCondition.RAINY:  (EnergyPolicyDecision.PREFER_OFF, 0.60, "rainy-conserve",    "monitor-weather"),
    WeatherCondition.STORM:  (EnergyPolicyDecision.FORCE_OFF,  0.30, "storm-protect",     "check-forecast"),
    WeatherCondition.SNOWY:  (EnergyPolicyDecision.FORCE_OFF,  0.30, "snowy-protect",     "check-forecast"),
}

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeatherAdjustmentResult:
    """Advisory result of a weather-based energy adjustment evaluation.

    Pure data — no hardware calls, no weather fetch, no side effects.
    """
    decision: EnergyPolicyDecision
    adjustment_factor: float = 1.0
    reason: str = ""
    recommended_follow_up: str = "none"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_weather_adjustment(
    weather: WeatherForecastSignal | None,
) -> WeatherAdjustmentResult:
    """Evaluate weather condition and return an advisory energy adjustment.

    Pure, deterministic, side-effect-free. Uses only the weather input.
    Does not fetch weather, call external APIs, switch devices, or import
    runtime services.

    Args:
        weather: A WeatherForecastSignal, or None if forecast is unavailable.

    Returns:
        WeatherAdjustmentResult with decision, adjustment_factor, reason,
        and recommended_follow_up.
    """
    # 1. None input → unknown weather
    if weather is None:
        return WeatherAdjustmentResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            adjustment_factor=1.0,
            reason="unknown-weather",
            recommended_follow_up="check-forecast",
        )

    condition = weather.condition

    # 2. UNKNOWN condition → unknown weather
    if condition is None:
        return WeatherAdjustmentResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            adjustment_factor=1.0,
            reason="unknown-weather",
            recommended_follow_up="check-forecast",
        )

    # 3. Look up in mapping by enum identity
    if isinstance(condition, WeatherCondition):
        entry = _ADJUSTMENT_MAP.get(condition)
        if entry is not None:
            decision, factor, reason, follow_up = entry
            return WeatherAdjustmentResult(
                decision=decision,
                adjustment_factor=factor,
                reason=reason,
                recommended_follow_up=follow_up,
            )
        # UNKNOWN enum member → fall through to unknown
        if condition == WeatherCondition.UNKNOWN:
            return WeatherAdjustmentResult(
                decision=EnergyPolicyDecision.NO_ACTION,
                adjustment_factor=1.0,
                reason="unknown-weather",
                recommended_follow_up="check-forecast",
            )

    # 4. String-like condition → match by value or name
    if isinstance(condition, str):
        condition_lower = condition.lower().strip()
        # Try matching by value
        for wc, entry in _ADJUSTMENT_MAP.items():
            if wc.value == condition_lower:
                decision, factor, reason, follow_up = entry
                return WeatherAdjustmentResult(
                    decision=decision,
                    adjustment_factor=factor,
                    reason=reason,
                    recommended_follow_up=follow_up,
                )
        # Try matching by name
        for wc, entry in _ADJUSTMENT_MAP.items():
            if wc.name.lower() == condition_lower:
                decision, factor, reason, follow_up = entry
                return WeatherAdjustmentResult(
                    decision=decision,
                    adjustment_factor=factor,
                    reason=reason,
                    recommended_follow_up=follow_up,
                )
        # Check for "unknown" as a string
        if condition_lower == "unknown":
            return WeatherAdjustmentResult(
                decision=EnergyPolicyDecision.NO_ACTION,
                adjustment_factor=1.0,
                reason="unknown-weather",
                recommended_follow_up="check-forecast",
            )

    # 5. Known but unmapped → neutral weather
    return WeatherAdjustmentResult(
        decision=EnergyPolicyDecision.NO_ACTION,
        adjustment_factor=1.0,
        reason="neutral-weather",
        recommended_follow_up="none",
    )
