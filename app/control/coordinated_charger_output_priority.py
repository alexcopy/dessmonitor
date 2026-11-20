"""Coordinate existing charger and output-priority authorities.

This module is deliberately transport-free. It consumes the existing Task
0080 decision and can call the Task 0083 Utility holder only when an explicit
executor is injected by a separately authorized caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.control.grid_charge_output_coordination import (
    CoordinationDecision,
    CoordinationState,
    SettingObservation,
)
from app.control.grid_output_interlock import ChargerObservation, ChargerProvenance


SOLAR_AND_MAINS = "solar_and_mains"
SOLAR_ONLY = "solar_only"
UTILITY = "Utility"
SBU = "SBU"


class CoordinatedControlState(str, Enum):
    GRID_RECOVERY_REQUESTED = "GRID_RECOVERY_REQUESTED"
    UTILITY_HOLD = "UTILITY_HOLD"
    RECOVERY_SETTLING = "RECOVERY_SETTLING"
    SOLAR_RETURN_BLOCKED = "SOLAR_RETURN_BLOCKED"
    SOLAR_RETURN_ELIGIBLE = "SOLAR_RETURN_ELIGIBLE"
    SOLAR_OPERATION = "SOLAR_OPERATION"
    FAULT_HOLD = "FAULT_HOLD"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    EMERGENCY_RECOVERY = "EMERGENCY_RECOVERY"


@dataclass(frozen=True)
class CoordinatedControlDecision:
    state: CoordinatedControlState
    reason_codes: tuple[str, ...]
    utility_hold_requested: bool = False
    return_blocked: bool = True
    shadow_only: bool = True
    command_issued: bool = False
    hardware_write_attempted: bool = False
    physical_transition_proven: bool = False

    def __post_init__(self) -> None:
        if (self.shadow_only is not True or self.command_issued is not False
                or self.hardware_write_attempted is not False
                or self.physical_transition_proven is not False):
            raise ValueError("coordinated control is shadow-only")
        if self.state in (CoordinatedControlState.SOLAR_RETURN_ELIGIBLE,
                          CoordinatedControlState.SOLAR_OPERATION):
            raise ValueError("solar return is not executable in this task")
        if self.state is not CoordinatedControlState.UTILITY_HOLD and self.utility_hold_requested:
            raise ValueError("Utility hold request requires UTILITY_HOLD")


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _fresh(value: object, now: datetime, limit: float) -> bool:
    stamp = _utc(value)
    if stamp is None:
        return False
    age = (now - stamp).total_seconds()
    return 0 <= age <= limit


def _setting_is_verified(priority: SettingObservation, now: datetime, limit: float) -> bool:
    return (type(priority) is SettingObservation
            and priority.value in (UTILITY, SBU)
            and priority.catalogue_verified is True
            and priority.readback_verified is True
            and _fresh(priority.observed_at, now, limit))


def _charger_is_verified(charger: ChargerObservation, now: datetime, limit: float) -> bool:
    return (type(charger) is ChargerObservation
            and charger.provenance is ChargerProvenance.DEVICE_READBACK
            and charger.mode in (SOLAR_AND_MAINS, SOLAR_ONLY)
            and _fresh(charger.observed_at, now, limit))


_UTILITY_PROTECTION_BLOCKERS = frozenset({
    "OUTPUT_SETTING_CONTRADICTORY", "INTERLOCK_SNAPSHOT_MISMATCH",
    "AUTHORITY_EVALUATION_UNAVAILABLE", "WINTER_EVALUATION_UNAVAILABLE",
    "STALE_TELEMETRY", "FUTURE_TIMESTAMP", "CONTRADICTORY_TELEMETRY",
    "INCOMPLETE_TELEMETRY", "INVALID_MEASUREMENT", "WORKING_MODE_UNKNOWN",
    "CHARGER_STATE_UNVERIFIED", "CHARGE_POLICY_CONTRADICTORY",
    "CHARGER_POLICY_MISMATCH", "OUTPUT_SETTING_UNVERIFIED",
})
_UTILITY_PROTECTION_DIAGNOSTIC_REASONS = frozenset({
    "EVENT_PERSISTENCE_UNAVAILABLE", "OBSERVATION_CONTINUITY_UNCERTAIN",
    "BATTERY_RESERVE_UNVERIFIED", "RESERVE_EVIDENCE_MISSING",
    "RESERVE_VALIDATION_CONTEXT_MISSING", "RESERVE_EVIDENCE_STALE",
    "RESERVE_TIMESTAMP_UNVERIFIED", "RESERVE_TRUSTED_INTEGRATION_UNAVAILABLE",
    "POST_RECOVERY_EVIDENCE_DEADLOCK", "SUPERVISED_TRIAL_REQUIRES_SEPARATE_APPROVAL",
    "FORECAST_UNVERIFIED", "PV_MEASUREMENT_UNVERIFIED", "DAYLIGHT_UNVERIFIED",
    "TIMEZONE_UNVERIFIED", "PV_SOURCE_CONTEXT_UNVERIFIED", "EXTERNAL_CHARGING_UNKNOWN",
})


def _shadow_allows_utility_protection(shadow: CoordinationDecision) -> bool:
    """Allow only reserve/diagnostic holds through the winter return fault state."""
    reasons = set(shadow.reason_codes)
    return (shadow.state is CoordinationState.FAULT_HOLD
            and bool(reasons & _UTILITY_PROTECTION_DIAGNOSTIC_REASONS)
            and not reasons & _UTILITY_PROTECTION_BLOCKERS
            and reasons <= (_UTILITY_PROTECTION_DIAGNOSTIC_REASONS | {"GRID_CHARGING_ACTIVE_WITH_SBU"}))


def evaluate_coordinated_control(
    shadow: CoordinationDecision,
    *,
    charger: ChargerObservation,
    priority: SettingObservation,
    evaluated_at: datetime,
    manual_utility_hold: bool = False,
    manual_sbu_request: bool = False,
    emergency_recovery: bool = False,
    freshness_limit_seconds: float = 180.0,
) -> CoordinatedControlDecision:
    """Return one conservative coordination state without side effects."""

    now = _utc(evaluated_at)
    if now is None or type(shadow) is not CoordinationDecision:
        return CoordinatedControlDecision(
            CoordinatedControlState.FAULT_HOLD,
            ("COORDINATION_INPUT_UNAVAILABLE",),
        )
    if (isinstance(freshness_limit_seconds, bool)
            or not isinstance(freshness_limit_seconds, (int, float))
            or freshness_limit_seconds < 0):
        raise ValueError("freshness_limit_seconds must be a finite non-negative number")
    if type(manual_utility_hold) is not bool or type(manual_sbu_request) is not bool:
        raise TypeError("manual override flags must be bools")

    if emergency_recovery or shadow.state is CoordinationState.EMERGENCY_RECOVERY:
        return CoordinatedControlDecision(
            CoordinatedControlState.EMERGENCY_RECOVERY,
            ("EMERGENCY_RECOVERY_PRECEDENCE",),
            return_blocked=True,
        )
    if manual_utility_hold or manual_sbu_request:
        return CoordinatedControlDecision(
            CoordinatedControlState.MANUAL_OVERRIDE,
            ("MANUAL_OVERRIDE", "AUTOMATIC_OUTPUT_COORDINATION_BLOCKED"),
            return_blocked=True,
        )

    if type(charger) is not ChargerObservation or type(priority) is not SettingObservation:
        return CoordinatedControlDecision(
            CoordinatedControlState.FAULT_HOLD,
            ("COORDINATION_EVIDENCE_UNAVAILABLE",),
        )

    charger_valid = _charger_is_verified(charger, now, freshness_limit_seconds)
    priority_valid = _setting_is_verified(priority, now, freshness_limit_seconds)

    if shadow.state is CoordinationState.FAULT_HOLD and not _shadow_allows_utility_protection(shadow):
        return CoordinatedControlDecision(
            CoordinatedControlState.FAULT_HOLD,
            ("SHADOW_COORDINATION_BLOCKED",) + shadow.reason_codes,
            return_blocked=True,
        )
    if shadow.state is CoordinationState.CHURN_LOCKOUT:
        return CoordinatedControlDecision(
            CoordinatedControlState.SOLAR_RETURN_BLOCKED,
            ("SHADOW_COORDINATION_BLOCKED",) + shadow.reason_codes,
            return_blocked=True,
        )

    if charger.mode == SOLAR_AND_MAINS:
        if not charger_valid:
            return CoordinatedControlDecision(
                CoordinatedControlState.GRID_RECOVERY_REQUESTED,
                ("CHARGER_READBACK_UNVERIFIED",),
            )
        if not priority_valid:
            return CoordinatedControlDecision(
                CoordinatedControlState.GRID_RECOVERY_REQUESTED,
                ("OUTPUT_PRIORITY_READBACK_UNVERIFIED",),
            )
        if priority.value == UTILITY:
            return CoordinatedControlDecision(
                CoordinatedControlState.UTILITY_HOLD,
                ("ALREADY_UTILITY",),
                return_blocked=True,
            )
        if priority.value == SBU:
            return CoordinatedControlDecision(
                CoordinatedControlState.UTILITY_HOLD,
                ("GRID_CHARGING_ACTIVE_WITH_SBU",),
                utility_hold_requested=True,
                return_blocked=True,
            )

    if charger.mode == SOLAR_ONLY:
        if not charger_valid:
            return CoordinatedControlDecision(
                CoordinatedControlState.FAULT_HOLD,
                ("CHARGER_READBACK_UNVERIFIED",),
            )
        if not priority_valid:
            return CoordinatedControlDecision(
                CoordinatedControlState.RECOVERY_SETTLING,
                ("OUTPUT_PRIORITY_READBACK_UNVERIFIED",),
            )
        return CoordinatedControlDecision(
            CoordinatedControlState.SOLAR_RETURN_BLOCKED,
            ("SOLAR_ONLY_NOT_BATTERY_ELIGIBILITY", "AUTOMATIC_SBU_DISABLED"),
            return_blocked=True,
        )

    return CoordinatedControlDecision(
        CoordinatedControlState.FAULT_HOLD,
        ("CHARGER_STATE_UNAVAILABLE",),
    )


async def execute_utility_hold(
    decision: CoordinatedControlDecision,
    executor: Any | None,
    *,
    charger: ChargerObservation,
    priority: SettingObservation,
    evaluated_at: datetime,
    observation_fingerprint: str,
    working_mode: str | None = None,
    manual_override: bool = False,
) -> Any | None:
    """Call the injected Task 0083 executor once; never retry or restore SBU."""

    if decision.state is not CoordinatedControlState.UTILITY_HOLD \
            or not decision.utility_hold_requested or executor is None:
        return None
    hold = getattr(executor, "hold", None)
    if not callable(hold):
        raise TypeError("utility hold executor must provide hold()")
    try:
        return await hold(
            charger,
            priority,
            evaluated_at=evaluated_at,
            observation_fingerprint=observation_fingerprint,
            working_mode=working_mode,
            manual_override=manual_override,
        )
    except asyncio.CancelledError:
        raise


__all__ = [
    "CoordinatedControlDecision",
    "CoordinatedControlState",
    "evaluate_coordinated_control",
    "execute_utility_hold",
]
