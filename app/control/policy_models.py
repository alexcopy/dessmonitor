"""Passive policy engine input/output model types for dessmonitor.

Defines frozen dataclasses representing the data structures the future
deterministic policy decision engine (PR 0018C) will consume and produce.

Seven types:
  BatteryOperatingWindow — battery voltage operating boundaries
  EnergyBudget — energy budget tracking for inverter max load protection
  PondSafetyContext — pond/fish/aeration safety context
  ForecastStrategyContext — forecast-aware strategy context
  LoadCandidate — a single device load being evaluated
  PolicyDecisionInput — complete input for policy decision evaluation
  PolicyDecisionResult — result of a policy decision evaluation

All types are passive data only:
  - No evaluate_policy_decision() implementation (deferred to 0018C).
  - No command proposal (deferred to 0020).
  - No calculations, sorting, or selection logic.
  - No side effects, file/env/network reads, or hardware calls.
  - No current time reads (time.time / datetime.now).
  - No config loading, logging, or mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.control.energy_policy import (
    DevicePriority,
    EnergyPolicyContext,
    EnergyPolicyDecision,
    HealthCheckResult,
    LoadClass,
    ReadinessResult,
)
from app.control.schedule_profile import LoadScheduleProfile
from app.control.weather_adjustment import WeatherAdjustmentResult


# ---------------------------------------------------------------------------
# Type 1: BatteryOperatingWindow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatteryOperatingWindow:
    """Battery voltage operating boundaries for the policy decision engine.

    All values are configurable defaults; the decision engine (0018C) may
    receive overridden values from runtime input.

    Pure data — no voltage monitoring, no hardware calls, no side effects.
    """
    battery_grid_fallback_voltage: float = 24.5
    battery_morning_minimum_voltage: float = 25.0
    battery_evening_reserve_voltage: float = 26.5
    battery_high_voltage_spend_threshold: float = 28.5
    battery_full_voltage: float = 29.5


# ---------------------------------------------------------------------------
# Type 2: EnergyBudget
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnergyBudget:
    """Energy budget tracking for inverter max load protection.

    max_total_load_watts is the configurable inverter capacity, typical 2500W.
    current_total_load_watts is the estimated sum of currently-on configured loads.
    available_load_budget_watts is the remaining budget under the inverter cap.

    Pure data — no load monitoring, no hardware calls, no side effects.
    """
    max_total_load_watts: float = 2500.0
    current_total_load_watts: float = 0.0
    available_load_budget_watts: float = 0.0


# ---------------------------------------------------------------------------
# Type 3: PondSafetyContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PondSafetyContext:
    """Pond/fish/aeration safety context for the policy decision engine.

    Models the life-support invariant documented in POLICY_DECISION_ENGINE.md:
      - Pond aeration is life-support, not ordinary discretionary load.
      - In summer or at water temperature >= pond_hot_water_temperature_c,
        additional aeration priority increases.
      - The engine should prefer/propose 2-4 additional aeration loads
        when available and safe.

    Pure data — no temperature sensing, no hardware calls, no side effects.
    """
    pond_temperature_c: float | None = None
    pond_hot_water_temperature_c: float = 26.0
    is_summer: bool = False
    aeration_load_ids: tuple[str, ...] = ()
    minimum_aeration_count: int = 1
    preferred_aeration_count: int = 2
    maximum_extra_aeration_count: int = 4
    life_support_required: bool = False


# ---------------------------------------------------------------------------
# Type 4: ForecastStrategyContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastStrategyContext:
    """Forecast-aware strategy context for the policy decision engine.

    Encodes the forecast strategy rules from POLICY_DECISION_ENGINE.md:
      - Sunny forecast later → preserve battery near morning minimum.
      - Bad forecast all day → conserve aggressively, restrict discretionary loads.
      - Weather unknown → conservative defaults, no sunny spending.

    Pure data — no forecast fetching, no weather API calls, no side effects.
    """
    forecast_improves_later_today: bool = False
    bad_forecast_all_day: bool = False
    sunny_window_expected_hours: float | None = None
    morning_strategy_active: bool = False


# ---------------------------------------------------------------------------
# Type 5: LoadCandidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadCandidate:
    """A single device load being evaluated by the policy decision engine.

    configured_load_watts is an estimate from config-derived device metadata
    (e.g. load_in_wt). It is NOT live telemetry.

    readiness and health are results from the evaluators implemented in
    PR 0014 and PR 0015 respectively. They are optional — a load may not
    have been evaluated yet.

    As of PR 0034a, currently_on is nullable: True=ON, False=OFF, None=UNKNOWN.
    Observation fields carry canonical device observation state.

    Pure data — no device queries, no hardware calls, no side effects.
    """
    load_id: str
    display_name: str = ""
    load_class: LoadClass | str | None = None
    priority: DevicePriority | int | str | None = None
    configured_load_watts: float = 0.0
    currently_on: bool | None = False
    controllable: bool = True
    is_life_support: bool = False
    roles: tuple[str, ...] = ()
    readiness: ReadinessResult | None = None
    health: HealthCheckResult | None = None
    schedule_profile: LoadScheduleProfile | None = None
    observed_state: str | None = None
    observed_at: str | None = None
    observation_source: str | None = None
    freshness: str | None = None


# ---------------------------------------------------------------------------
# Type 6: PolicyDecisionInput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDecisionInput:
    """Complete input for a policy decision evaluation.

    Aggregates battery state, energy budget, pond safety, forecast strategy,
    weather adjustment, and a list of load candidates for a single evaluation
    cycle.

    Pure data — no data fetching, no hardware calls, no side effects.
    """
    context: EnergyPolicyContext | None = None
    loads: tuple[LoadCandidate, ...] = field(default_factory=tuple)
    energy_budget: EnergyBudget = field(default_factory=EnergyBudget)
    battery_window: BatteryOperatingWindow = field(
        default_factory=BatteryOperatingWindow,
    )
    pond_safety: PondSafetyContext = field(default_factory=PondSafetyContext)
    forecast_strategy: ForecastStrategyContext = field(
        default_factory=ForecastStrategyContext,
    )
    weather_adjustment: WeatherAdjustmentResult | None = None


# ---------------------------------------------------------------------------
# Type 7: PolicyDecisionResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDecisionResult:
    """Result of a policy decision evaluation for a single load candidate.

    decision: what the engine recommends for this load.
    projected_total_load_watts: estimated total load if this decision is applied.
    blocked_by: list of blocking condition identifiers
        (e.g. "evening-reserve-protected", "below-switch-on-voltage").
    recommended_follow_up: advisory text for operator visibility.

    Pure data — no execution commands, no hardware calls, no side effects.
    """
    decision: EnergyPolicyDecision = EnergyPolicyDecision.NO_ACTION
    target_load_id: str | None = None
    projected_total_load_watts: float | None = None
    reason: str = ""
    explanation: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    recommended_follow_up: str = "none"
