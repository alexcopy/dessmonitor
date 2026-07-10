"""Passive energy policy domain types for dessmonitor.

These types define the vocabulary for power source, time-of-day scheduling,
seasonal profiles, weather conditions, device policy, readiness, health,
and policy decisions — without evaluating or executing any policy.

They are passive data definitions only. They are NOT wired into any runtime
component (SmartHomeController, RelayTuyaController, DeviceInitializer,
weather, ML, monitoring) and must NOT execute hardware calls.

This module uses only Python standard library: dataclasses, enum, typing, time.

Policy concepts referenced:
  - 26.5V evening reserve target (BatteryReservePolicy.evening_reserve_voltage)
  - switch ON / switch OFF controlled by voltage, forecast, time windows
  - readiness evaluates whether a device may be switched ON now (passive)
  - health evaluates whether observed state matches expected (passive)
  - weather forecast adjusts voltage thresholds and allowed windows
  - ML advisory may recommend thresholds/windows but must NOT switch devices
  - ML control remains deferred behind separate safety-reviewed gates (ADR-0003)

ML advisory is advice only. ML control is deferred and disabled.
No type may be pump-specific. No type may require Tuya command keys as core fields.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Policy Enums
# ---------------------------------------------------------------------------


class PowerSource(Enum):
    """Represents where the system's energy is coming from (passive vocabulary).

    When on SOLAR, discretionary loads are candidates for switch ON.
    When on GRID/MAINS/NETWORK, only critical loads may run — this is a
    signal for conservation.
    """
    SOLAR = "solar"
    BATTERY = "battery"
    GRID = "grid"
    MAINS = "mains"         # synonym for grid/network power
    NETWORK = "network"
    UNKNOWN = "unknown"


class TimeOfDay(Enum):
    """Time slot for scheduling. Evening may need more frequent checks."""
    MORNING = "morning"     # e.g., 06:00-12:00
    DAY = "day"             # e.g., 12:00-18:00
    EVENING = "evening"     # e.g., 18:00-22:00 — protect evening reserve (26.5V)
    NIGHT = "night"         # e.g., 22:00-06:00
    UNKNOWN = "unknown"


class Season(Enum):
    """Seasonal profile selector. Summer more permissive, Winter conservative."""
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"
    UNKNOWN = "unknown"


class WeatherCondition(Enum):
    """Simplified weather classification for policy adjustment.

    SUNNY may relax thresholds (more energy spending allowed).
    CLOUDY/RAINY should tighten thresholds (conserve energy).
    """
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAINY = "rainy"
    SNOWY = "snowy"
    STORM = "storm"
    UNKNOWN = "unknown"


class LoadClass(Enum):
    """Whether a load is critical or discretionary.

    CRITICAL loads must never be automatically switched OFF.
    DISCRETIONARY loads may be shed when energy is scarce.
    """
    CRITICAL = "critical"
    DISCRETIONARY = "discretionary"


class DevicePriority(Enum):
    """Priority level for load shedding and allocation."""
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class HealthStatus(Enum):
    """Result of a health check.

    Health assessment is a passive, side-effect-free computation.
    A device flagged UNHEALTHY should be excluded from automatic
    switching but must NOT trigger repeated switching loops.
    """
    HEALTHY = "healthy"
    STALE = "stale"             # status too old — observed state may be unreliable
    MISMATCH = "mismatch"       # observed state != expected state (unexpected ON/OFF)
    UNREACHABLE = "unreachable" # device not responding to queries
    UNKNOWN = "unknown"


class EnergyPolicyDecision(Enum):
    """Policy outcome vocabulary — future decision types only.

    These represent recommendations, NOT hardware execution.
    They must NOT directly switch devices.
    """
    ALLOW_ON = "allow_on"       # conditions favorable, may switch ON
    PREFER_OFF = "prefer_off"   # conditions marginal, prefer OFF
    FORCE_OFF = "force_off"     # immediate switch OFF recommended
    HOLD = "hold"               # keep current state, no change
    NO_ACTION = "no_action"     # no policy input, no recommendation


# ---------------------------------------------------------------------------
# Policy Dataclasses (frozen — passive data only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoltageSnapshot:
    """Current voltage state — passive data only.

    Voltage is the primary signal for energy-aware control decisions.
    Rising voltage (charging) may allow more aggressive energy spending.
    Falling voltage (discharging) should trigger conservation earlier.
    """
    voltage: float                          # Current battery voltage in volts
    trend: Optional[float] = None           # positive = rising, negative = falling
    timestamp: float = field(default_factory=time.time)
    charging_state: Optional[str] = None    # "charging", "discharging", or None
    power_source: PowerSource = PowerSource.UNKNOWN
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WeatherForecastSignal:
    """Current or near-term weather forecast for policy adjustment (passive).

    Weather forecast is advisory input, NOT direct hardware action.
    Forecast failures must fall back to conservative defaults.
    """
    condition: WeatherCondition = WeatherCondition.UNKNOWN
    temperature: Optional[float] = None             # Ambient temperature °C
    confidence: Optional[float] = None              # 0.0–1.0 forecast confidence
    expected_solar_opportunity: Optional[float] = None  # 0.0–1.0 solar potential
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BatteryReservePolicy:
    """Evening battery reserve configuration.

    Targets an evening reserve around 26.5V after sunset.
    The reserve target is configurable and may vary by season.
    """
    evening_reserve_voltage: float = 26.5   # Target voltage after sunset
    protect_after_sunset: bool = True       # Enforce reserve after sunset
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceEnergyPolicy:
    """Per-device energy policy configuration (passive data only).

    This is a requirements representation — not a config schema implementation
    and not wired to any runtime component.

    Represents future per-device policy fields documented in
    .project-memory/ENERGY_AWARE_CONTROL_POLICY.md.
    """
    load_id: str
    priority: DevicePriority = DevicePriority.NORMAL
    load_class: LoadClass = LoadClass.DISCRETIONARY
    allowed_time_windows: Tuple[str, ...] = field(default_factory=tuple)
    # Voltage thresholds for switch ON / switch OFF decisions
    minimum_voltage_to_switch_on: Optional[float] = None
    minimum_voltage_to_stay_on: Optional[float] = None
    voltage_to_switch_off: Optional[float] = None
    cooldown_after_switch_seconds: int = 0
    # Check intervals for periodic readiness / health evaluation
    readiness_check_interval_seconds: Optional[int] = None
    health_check_interval_seconds: Optional[int] = None
    # Weather and seasonal behaviour
    weather_sensitivity: str = "normal"
    season_profile: Dict[str, object] = field(default_factory=dict)
    allow_always_on_when_good_conditions: bool = False
    skip_when_cloudy_or_rainy: bool = False
    # Manual override and fail-safe
    manual_override_allowed: bool = True
    fail_safe_off: bool = True
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EnergyPolicyContext:
    """Aggregated passive context for energy policy evaluation.

    Combines voltage, weather, time-of-day, season, and reserve policy
    into a single passive data structure. No evaluation logic.
    """
    voltage: VoltageSnapshot
    weather: WeatherForecastSignal = field(
        default_factory=WeatherForecastSignal,
    )
    time_of_day: TimeOfDay = TimeOfDay.UNKNOWN
    season: Season = Season.UNKNOWN
    reserve: BatteryReservePolicy = field(
        default_factory=BatteryReservePolicy,
    )
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadinessInput:
    """Context for a readiness evaluation (passive data only).

    Readiness is a side-effect-free computation that determines whether
    a device is allowed to be switched ON at the current moment.
    Readiness must NOT switch devices directly.
    """
    policy: DeviceEnergyPolicy
    context: EnergyPolicyContext
    current_state: Optional[object] = None          # Current observed state
    last_switch_timestamp: Optional[float] = None   # For cooldown checks
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadinessResult:
    """Output of a readiness evaluation (passive data only).

    Readiness is separate from health. A device can be ready but unhealthy,
    or healthy but not ready.
    """
    ready: bool
    reason: str = ""
    decision: EnergyPolicyDecision = EnergyPolicyDecision.NO_ACTION
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthInput:
    """Context for a health evaluation (passive data only).

    Health is a side-effect-free computation that assesses whether a
    device is operating as expected. Health evaluation must NOT create
    unsafe repeated switching loops.
    """
    load_id: str
    expected_state: Optional[object] = None      # What we think the device state is
    observed_state: Optional[object] = None      # What the device actually reports
    status_age_seconds: Optional[float] = None   # Seconds since last known-good status
    failure_count: int = 0                        # Consecutive switch failures
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthCheckResult:
    """Output of a health evaluation (passive data only).

    A device flagged as UNHEALTHY should be excluded from automatic
    switching until manually cleared or a cooldown expires.
    """
    status: HealthStatus = HealthStatus.UNKNOWN
    reason: str = ""
    recommended_follow_up: str = ""   # "none", "retry", "flag_operator", "force_off"
    metadata: Dict[str, object] = field(default_factory=dict)
