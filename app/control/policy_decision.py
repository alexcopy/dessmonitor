"""Pure deterministic policy decision engine for dessmonitor.

Implements evaluate_policy_decision() — the "brain" of the energy-aware control
system. The engine composes battery operating window, energy budget, load wattage,
readiness, health, schedule profile, weather adjustment, forecast strategy, and
pond/fish/aeration life-support context into a single advisory PolicyDecisionResult.

The engine is pure and deterministic: same input always produces the same output.
It does not execute commands, create command proposals, wire into runtime, fetch
weather, or switch devices. All inputs are passive data.

This module uses only:
  - Python standard library (typing)
  - app.control.energy_policy types
  - app.control.policy_models types
  - app.control.weather_adjustment types (via policy_models import)
"""

from __future__ import annotations

from app.control.energy_policy import (
    DevicePriority,
    EnergyPolicyDecision,
    HealthStatus,
    ReadinessResult,
)
from app.control.policy_models import (
    BatteryOperatingWindow,
    EnergyBudget,
    ForecastStrategyContext,
    LoadCandidate,
    PolicyDecisionInput,
    PolicyDecisionResult,
    PondSafetyContext,
)
from app.control.weather_adjustment import WeatherAdjustmentResult

# ---------------------------------------------------------------------------
# Priority ordering constants (deterministic tie-breaking)
# ---------------------------------------------------------------------------

_PRIORITY_ORDER: dict[str, int] = {
    "high": 3,
    "normal": 2,
    "low": 1,
}

# ---------------------------------------------------------------------------
# Voltage extraction — safe, tolerant of None and multiple field names
# ---------------------------------------------------------------------------

_VOLTAGE_FIELD_NAMES = ("battery_voltage", "voltage", "value", "battery_voltage_v")


def _extract_voltage(policy_input: PolicyDecisionInput) -> float | None:
    """Extract battery voltage from policy_input.context, safely.

    Tolerates context=None and supports common voltage field names.
    """
    ctx = policy_input.context
    if ctx is None:
        return None
    voltage_obj = getattr(ctx, "voltage", None)
    if voltage_obj is None:
        return None
    for name in _VOLTAGE_FIELD_NAMES:
        raw = getattr(voltage_obj, name, None)
        if raw is not None and isinstance(raw, (int, float)):
            return float(raw)
    return None


# ---------------------------------------------------------------------------
# Normalisation helpers — tolerate enum and string values
# ---------------------------------------------------------------------------


def _normalize_priority(load: LoadCandidate) -> str:
    """Return a normalised priority string: 'high', 'normal', or 'low'."""
    p = load.priority
    if p is None:
        return "normal"
    if isinstance(p, DevicePriority):
        return p.name.lower()
    if isinstance(p, str):
        return p.lower().strip()
    if isinstance(p, int):
        return "normal"
    return "normal"


def _priority_sort_key(load: LoadCandidate) -> int:
    """Higher number = higher priority for ON candidate selection."""
    return _PRIORITY_ORDER.get(_normalize_priority(load), 2)


def _shed_sort_key(load: LoadCandidate) -> int:
    """Higher number = HIGHER priority, so invert for shedding (low priority first)."""
    return -_PRIORITY_ORDER.get(_normalize_priority(load), 2)


def _is_non_life_support_discretionary(load: LoadCandidate) -> bool:
    """Check if a load is a safe shed target (not life-support, not critical)."""
    if load.is_life_support:
        return False
    lc = load.load_class
    lc_str = ""
    if hasattr(lc, "name"):
        lc_str = lc.name.lower()
    elif isinstance(lc, str):
        lc_str = lc.lower().strip()
    if lc_str == "critical":
        return False
    return True


def _is_healthy(load: LoadCandidate) -> bool:
    """Check if a load's health allows activation.

    HEALTHY or None (unassessed) are OK.
    STALE, MISMATCH, UNREACHABLE, UNKNOWN are not OK.
    """
    health = load.health
    if health is None:
        return True  # unassessed — treat as OK
    status = health.status
    if status is None:
        return True
    if isinstance(status, HealthStatus):
        return status == HealthStatus.HEALTHY
    if isinstance(status, str):
        return status.lower().strip() == "healthy"
    return True


def _is_ready(load: LoadCandidate) -> bool:
    """Check if a load's readiness allows activation.

    ready=True or None (unassessed) are OK.
    ready=False is not OK.
    """
    readiness = load.readiness
    if readiness is None:
        return True  # unassessed — treat as OK
    return bool(readiness.ready)


def _can_turn_on(load: LoadCandidate) -> bool:
    """Load is OFF and safe to turn ON (healthy and ready)."""
    if load.currently_on:
        return False
    if not load.controllable:
        return False
    return _is_healthy(load) and _is_ready(load)


def _safe_watts(watts: float | None) -> float:
    """Clamp missing/invalid/negative configured_load_watts to 0.0."""
    if watts is None or not isinstance(watts, (int, float)) or watts < 0:
        return 0.0
    return float(watts)


# ---------------------------------------------------------------------------
# Aeration identification
# ---------------------------------------------------------------------------

_AERATION_ROLE_TOKENS = ("pond", "aeration", "air", "oxygen", "fish")


def _is_aeration(load: LoadCandidate, pond_safety: PondSafetyContext) -> bool:
    """Identify aeration loads by load_id in aeration_load_ids or by role/name."""
    if load.load_id in pond_safety.aeration_load_ids:
        return True
    for role in load.roles:
        rl = role.lower()
        for token in _AERATION_ROLE_TOKENS:
            if token in rl:
                return True
    dn = load.display_name.lower()
    for token in _AERATION_ROLE_TOKENS:
        if token in dn:
            return True
    return False


# ---------------------------------------------------------------------------
# Shed candidate selection — deterministic tie-breaking
# ---------------------------------------------------------------------------


def _select_shed_candidate(loads: tuple[LoadCandidate, ...]) -> LoadCandidate | None:
    """Select the best non-life-support, currently-on load to shed.

    Tie-breaking: lower priority first, then smaller configured_load_watts (less
    impactful), then load_id alphabetical.
    """
    candidates = [
        l for l in loads
        if l.currently_on and _is_non_life_support_discretionary(l)
    ]
    if not candidates:
        return None
    # Sort: low priority (shed first) → high priority; then smaller watts; then load_id
    candidates.sort(key=lambda l: (
        -_priority_sort_key(l),   # negative so low priority = small sort key
        _safe_watts(l.configured_load_watts),
        l.load_id,
    ))
    return candidates[0]


# ---------------------------------------------------------------------------
# ON candidate selection — deterministic tie-breaking
# ---------------------------------------------------------------------------


def _select_on_candidate(
    loads: tuple[LoadCandidate, ...],
    max_total_load_watts: float,
    current_total_load_watts: float,
    prefer_life_support: bool = False,
) -> LoadCandidate | None:
    """Select the best OFF, healthy, ready load that fits the inverter cap.

    Preference order:
      1. life-support (if prefer_life_support) / critical first
      2. higher priority
      3. larger configured_load_watts
      4. load_id alphabetical

    Returns None if no candidate fits the budget.
    """
    available = [
        l for l in loads
        if _can_turn_on(l)
        and current_total_load_watts + _safe_watts(l.configured_load_watts) <= max_total_load_watts
    ]
    if not available:
        return None
    # Sort by (life_support desc, priority desc, watts desc, load_id asc)
    available.sort(key=lambda l: (
        not l.is_life_support if prefer_life_support else 0,
        -_priority_sort_key(l),
        -_safe_watts(l.configured_load_watts),
        l.load_id,
    ))
    return available[0]


# ---------------------------------------------------------------------------
# Aeration-specific ON candidate selection
# ---------------------------------------------------------------------------


def _select_aeration_candidate(
    loads: tuple[LoadCandidate, ...],
    pond_safety: PondSafetyContext,
    max_total_load_watts: float,
    current_total_load_watts: float,
) -> LoadCandidate | None:
    """Select the best OFF, healthy, ready aeration load that fits the inverter cap."""
    aeration_loads = [
        l for l in loads
        if _is_aeration(l, pond_safety) and _can_turn_on(l)
        and current_total_load_watts + _safe_watts(l.configured_load_watts) <= max_total_load_watts
    ]
    if not aeration_loads:
        return None
    # Life-support aeration first
    aeration_loads.sort(key=lambda l: (
        not l.is_life_support,
        -_safe_watts(l.configured_load_watts),
        l.load_id,
    ))
    return aeration_loads[0]


# ===================================================================
# evaluate_policy_decision — the pure deterministic decision engine
# ===================================================================


def evaluate_policy_decision(
    policy_input: PolicyDecisionInput,
) -> PolicyDecisionResult:
    """Evaluate energy-aware policy and produce a single advisory decision.

    Pure, deterministic, side-effect-free. Consumes PolicyDecisionInput
    and returns PolicyDecisionResult. Does not execute commands, propose
    actions, wire into runtime, fetch weather, or switch devices.

    Priority order (first match wins):
      1. no-loads
      2. battery-fallback-protection
      3. inverter-load-cap-protection
      4. pond-life-support-aeration (incl. shed-discretionary-for-aeration)
      5. morning-minimum-hold-for-sun
      6. bad-forecast-conserve
      7. high-voltage-spend
      8. weather-conserve / weather-spend
      9. neutral-no-action

    Args:
        policy_input: Aggregated PolicyDecisionInput from PR 0018B models.

    Returns:
        PolicyDecisionResult with decision, target_load_id,
        projected_total_load_watts, reason, explanation, blocked_by,
        and recommended_follow_up.
    """
    # Unpack shortcut references
    loads = policy_input.loads
    budget = policy_input.energy_budget
    battery = policy_input.battery_window
    pond = policy_input.pond_safety
    forecast = policy_input.forecast_strategy
    weather_adj = policy_input.weather_adjustment

    max_w = budget.max_total_load_watts
    cur_w = budget.current_total_load_watts
    battery_voltage = _extract_voltage(policy_input)

    # ----- 1. no-loads -----
    if not loads:
        return PolicyDecisionResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            reason="no-loads",
            projected_total_load_watts=cur_w,
            recommended_follow_up="none",
        )

    # ----- 2. battery-fallback-protection -----
    if (
        battery_voltage is not None
        and battery_voltage <= battery.battery_grid_fallback_voltage
    ):
        shed = _select_shed_candidate(loads)
        if shed is not None:
            proj = max(0.0, cur_w - _safe_watts(shed.configured_load_watts))
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.FORCE_OFF,
                target_load_id=shed.load_id,
                projected_total_load_watts=proj,
                reason="battery-fallback-protection",
                explanation=f"Shedding {shed.load_id} — battery at {battery_voltage}V "
                            f"(fallback at {battery.battery_grid_fallback_voltage}V)",
                blocked_by=("battery_grid_fallback_voltage",),
                recommended_follow_up="conserve",
            )
        return PolicyDecisionResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            reason="battery-fallback-protection",
            explanation="No safe non-life-support shed target available at battery fallback",
            recommended_follow_up="conserve",
        )

    # ----- 3. inverter-load-cap-protection -----
    if cur_w >= max_w:
        shed = _select_shed_candidate(loads)
        if shed is not None:
            proj = max(0.0, cur_w - _safe_watts(shed.configured_load_watts))
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.FORCE_OFF,
                target_load_id=shed.load_id,
                projected_total_load_watts=proj,
                reason="inverter-load-cap-protection",
                explanation=f"Shedding {shed.load_id} — current load {cur_w}W "
                            f"at/above inverter cap {max_w}W",
                blocked_by=("max_total_load_watts",),
                recommended_follow_up="shed-discretionary",
            )
        return PolicyDecisionResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            reason="inverter-load-cap-protection",
            explanation="No safe non-life-support shed target at inverter max load cap",
            recommended_follow_up="shed-discretionary",
        )

    # ----- 4. pond-life-support-aeration -----
    pond_temp = pond.pond_temperature_c
    hot_thresh = pond.pond_hot_water_temperature_c
    life_support_active = (
        pond.life_support_required
        or (pond_temp is not None and hot_thresh is not None and pond_temp >= hot_thresh)
    )
    if life_support_active:
        aeration = _select_aeration_candidate(loads, pond, max_w, cur_w)
        if aeration is not None:
            proj = cur_w + _safe_watts(aeration.configured_load_watts)
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.ALLOW_ON,
                target_load_id=aeration.load_id,
                projected_total_load_watts=proj,
                reason="pond-life-support-aeration",
                explanation=f"Turning ON aeration {aeration.load_id} for pond life-support",
                recommended_follow_up="none",
            )
        # No aeration fits → try shedding a discretionary to make room
        shed = _select_shed_candidate(loads)
        if shed is not None:
            proj = max(0.0, cur_w - _safe_watts(shed.configured_load_watts))
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.FORCE_OFF,
                target_load_id=shed.load_id,
                projected_total_load_watts=proj,
                reason="shed-discretionary-for-aeration",
                explanation=f"Shedding {shed.load_id} to free budget for aeration",
                recommended_follow_up="shed-discretionary",
            )
        # Nothing can be done
        blocked = []
        if not any(_is_aeration(l, pond) for l in loads if _can_turn_on(l)):
            blocked.append("no-healthy-aeration-candidate")
        if cur_w + 1 > max_w:
            blocked.append("max_total_load_watts")
        return PolicyDecisionResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            reason="pond-life-support-aeration",
            explanation="Cannot activate aeration — no healthy candidate or budget",
            blocked_by=tuple(blocked) if blocked else ("no-healthy-aeration-candidate",),
            recommended_follow_up="shed-discretionary",
        )

    # ----- 5. morning-minimum-hold-for-sun -----
    if (
        forecast.morning_strategy_active
        and forecast.forecast_improves_later_today
        and battery_voltage is not None
        and battery_voltage <= battery.battery_morning_minimum_voltage
    ):
        shed = _select_shed_candidate(loads)
        if shed is not None:
            proj = max(0.0, cur_w - _safe_watts(shed.configured_load_watts))
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.PREFER_OFF,
                target_load_id=shed.load_id,
                projected_total_load_watts=proj,
                reason="morning-minimum-hold-for-sun",
                explanation=f"Morning minimum hold — forecast improves later; "
                            f"preferring OFF {shed.load_id}",
                recommended_follow_up="hold-morning",
            )
        return PolicyDecisionResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            reason="morning-minimum-hold-for-sun",
            explanation="Holding at morning minimum, no safe shed target",
            recommended_follow_up="hold-morning",
        )

    # ----- 6. bad-forecast-conserve -----
    if forecast.bad_forecast_all_day:
        shed = _select_shed_candidate(loads)
        if shed is not None:
            proj = max(0.0, cur_w - _safe_watts(shed.configured_load_watts))
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.PREFER_OFF,
                target_load_id=shed.load_id,
                projected_total_load_watts=proj,
                reason="bad-forecast-conserve",
                explanation=f"Bad all-day forecast — conserving, shedding {shed.load_id}",
                recommended_follow_up="conserve",
            )
        return PolicyDecisionResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            reason="bad-forecast-conserve",
            explanation="Bad all-day forecast — no safe discretionary shed target available",
            recommended_follow_up="conserve",
        )

    # ----- 7. high-voltage-spend -----
    if (
        battery_voltage is not None
        and battery_voltage >= battery.battery_high_voltage_spend_threshold
    ):
        candidate = _select_on_candidate(
            loads, max_w, cur_w, prefer_life_support=True,
        )
        if candidate is not None:
            proj = cur_w + _safe_watts(candidate.configured_load_watts)
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.ALLOW_ON,
                target_load_id=candidate.load_id,
                projected_total_load_watts=proj,
                reason="high-voltage-spend",
                explanation=f"High voltage spend — activating {candidate.load_id} "
                            f"with surplus energy",
                recommended_follow_up="none",
            )
        return PolicyDecisionResult(
            decision=EnergyPolicyDecision.NO_ACTION,
            reason="high-voltage-spend",
            explanation="High voltage but no ready candidate fits the budget",
            blocked_by=("no-ready-candidate",),
            recommended_follow_up="none",
        )

    # ----- 8. weather-conserve / weather-spend -----
    if weather_adj is not None:
        wd = weather_adj.decision
        wd_name = ""
        if hasattr(wd, "name"):
            wd_name = wd.name.upper()
        elif isinstance(wd, str):
            wd_name = wd.upper().strip()

        # 8a. Weather says conserve (PREFER_OFF, FORCE_OFF)
        if wd_name in ("PREFER_OFF", "FORCE_OFF"):
            shed = _select_shed_candidate(loads)
            if shed is not None:
                proj = max(0.0, cur_w - _safe_watts(shed.configured_load_watts))
                return PolicyDecisionResult(
                    decision=EnergyPolicyDecision.PREFER_OFF,
                    target_load_id=shed.load_id,
                    projected_total_load_watts=proj,
                    reason="weather-conserve",
                    explanation=f"Weather advises conserve — shedding {shed.load_id}",
                    recommended_follow_up="conserve",
                )
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.NO_ACTION,
                reason="weather-conserve",
                explanation="Weather advises conserve — no safe discretionary shed target",
                recommended_follow_up="conserve",
            )

        # 8b. Weather says spend (ALLOW_ON)
        if wd_name == "ALLOW_ON":
            candidate = _select_on_candidate(
                loads, max_w, cur_w, prefer_life_support=True,
            )
            if candidate is not None:
                proj = cur_w + _safe_watts(candidate.configured_load_watts)
                return PolicyDecisionResult(
                    decision=EnergyPolicyDecision.ALLOW_ON,
                    target_load_id=candidate.load_id,
                    projected_total_load_watts=proj,
                    reason="weather-spend",
                    explanation=f"Weather advises spend — activating {candidate.load_id}",
                    recommended_follow_up="none",
                )
            return PolicyDecisionResult(
                decision=EnergyPolicyDecision.NO_ACTION,
                reason="weather-spend",
                explanation="Weather advises spend — no ready candidate fits the budget",
                recommended_follow_up="none",
            )

    # ----- 9. neutral-no-action -----
    return PolicyDecisionResult(
        decision=EnergyPolicyDecision.NO_ACTION,
        reason="neutral-no-action",
        projected_total_load_watts=cur_w,
        recommended_follow_up="none",
    )
