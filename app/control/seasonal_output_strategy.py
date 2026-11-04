"""Offline seasonal output proposals. This module never commands hardware.

All energy figures and voltage bands are caller-supplied, explicitly approved
inputs; illustrative arithmetic is not a battery model or a hardware setting.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import isfinite

from app.control.adaptive_output_policy import (
    BatteryEligibility, ForecastQuality, OutputDecision, OutputPolicyBaseline,
    OutputTelemetry, TelemetryQuality, evaluate_output_policy,
)


class Strategy(str, Enum):
    CONSERVE = "CONSERVE"
    SOLAR_OPPORTUNITY = "SOLAR_OPPORTUNITY"


@dataclass(frozen=True)
class EnergyInputs:
    # Forecasted remaining PV and measured available battery energy in Wh.
    expected_pv_wh: float | None = None
    forecast_issued_at: datetime | None = None
    forecast_valid_through: datetime | None = None
    forecast_confidence: float | None = None
    usable_battery_wh: float | None = None
    battery_energy_observed_at: datetime | None = None
    battery_energy_verified: bool = False
    # Operator-defined energy to retain after meeting forecast demand.
    reserve_wh: float | None = None
    minimum_confidence: float | None = None
    max_energy_age_seconds: float | None = None
    proposal_ttl_seconds: float | None = None


@dataclass(frozen=True)
class ApprovedBands:
    """Explicitly approved proposal ranges and maximum upward deltas in volts."""
    utility_min_v: float
    utility_max_v: float
    battery_min_v: float
    battery_max_v: float
    max_utility_increase_v: float
    max_battery_increase_v: float


@dataclass(frozen=True)
class SeasonalDecision:
    strategy: Strategy
    reasons: tuple[str, ...]
    explanation: str
    evaluated_at: datetime
    policy_decision: OutputDecision
    expected_pv_wh: float | None = None
    estimated_load_wh: float | None = None
    required_reserve_wh: float | None = None
    confidence: float | None = None
    proposed_utility_v: float | None = None
    proposed_battery_v: float | None = None
    shadow_only: bool = True
    command_executed: bool = False
    expires_at: datetime | None = None

    def __post_init__(self):
        instant = _time(self.evaluated_at)
        if (type(self.strategy) is not Strategy or type(self.policy_decision) is not OutputDecision
                or type(self.reasons) is not tuple or not all(type(reason) is str for reason in self.reasons)
                or type(self.explanation) is not str or instant is None
                or self.shadow_only is not True or self.command_executed is not False):
            raise ValueError("invalid shadow decision")
        for name in ("expected_pv_wh", "estimated_load_wh", "required_reserve_wh", "confidence",
                     "proposed_utility_v", "proposed_battery_v"):
            value = getattr(self, name)
            if value is not None and not _number(value, maximum=1 if name == "confidence" else None):
                raise ValueError(f"invalid {name}")
        expiry = _time(self.expires_at) if self.expires_at is not None else None
        if ((self.expires_at is not None and expiry is None)
                or ((self.proposed_utility_v is not None or self.proposed_battery_v is not None)
                    and (expiry is None or expiry <= instant))):
            raise ValueError("proposals require a future expiry")
        object.__setattr__(self, "evaluated_at", instant)
        object.__setattr__(self, "expires_at", expiry)


def _number(value, *, minimum=0.0, maximum=None):
    try:
        return (type(value) in (int, float) and isfinite(value) and value >= minimum
                and (maximum is None or value <= maximum))
    except OverflowError:
        return False


def _time(value):
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        return value.astimezone(timezone.utc) if value.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _valid_bands(bands, baseline):
    if type(bands) is not ApprovedBands:
        return False
    values = (bands.utility_min_v, bands.utility_max_v, bands.battery_min_v, bands.battery_max_v,
              bands.max_utility_increase_v, bands.max_battery_increase_v)
    if not all(_number(value) for value in values):
        return False
    u, b, gap, ceiling = (baseline.exit_battery_v, baseline.enter_battery_v,
                          baseline.min_hysteresis_v, baseline.max_battery_voltage_v)
    return (all(_number(v) and v > 0 for v in (u, b, ceiling)) and _number(gap)
            and bands.utility_min_v > 0 and bands.battery_min_v > 0
            and bands.utility_min_v <= u <= bands.utility_max_v < ceiling
            and bands.battery_min_v <= b <= bands.battery_max_v <= ceiling
            and b > u and b - u >= gap)


def evaluate_seasonal_strategy(snapshot: OutputTelemetry, baseline: OutputPolicyBaseline,
                               history: tuple[OutputTelemetry, ...], *, evaluated_at: datetime,
                               energy: EnergyInputs | None, bands: ApprovedBands | None) -> SeasonalDecision:
    """Return a bounded simulation gated by the unchanged Phase 1A decision."""
    policy = evaluate_output_policy(snapshot, baseline, history, evaluated_at=evaluated_at)
    now = policy.evaluated_at
    reasons = []
    if policy.decision is not OutputDecision.CONSIDER or policy.data_quality is not TelemetryQuality.FRESH:
        reasons.append("POLICY_NOT_ELIGIBLE")
        reasons.extend("POLICY_" + reason.value for reason in policy.reason_codes if reason.value != "SHADOW_ONLY")
    if policy.battery_eligibility.classification is not BatteryEligibility.ELIGIBLE:
        reasons.append("BATTERY_NOT_ELIGIBLE")
    if policy.solar_opportunity.forecast_quality is not ForecastQuality.FRESH:
        reasons.append("POLICY_FORECAST_UNVERIFIED")
    if not all(_number(value) and value > 0 for value in
               (baseline.max_battery_voltage_v, baseline.max_battery_discharge_a, baseline.max_battery_charge_a)):
        reasons.append("HARDWARE_LIMITS_UNAPPROVED")
    if not _valid_bands(bands, baseline):
        reasons.append("PROPOSAL_BOUNDS_UNAPPROVED")
    if type(energy) is not EnergyInputs:
        reasons.append("ENERGY_EVIDENCE_MISSING")
        energy = None
    if energy is not None:
        issued, valid, measured = _time(energy.forecast_issued_at), _time(energy.forecast_valid_through), _time(energy.battery_energy_observed_at)
        sunset = _time(snapshot.sunset)
        if (not _number(energy.expected_pv_wh) or not _number(energy.forecast_confidence, maximum=1)
                or not _number(energy.minimum_confidence, maximum=1)
                or energy.forecast_confidence < energy.minimum_confidence or issued is None or valid is None
                or sunset is None or not (issued <= now <= sunset <= valid)
                or (now - issued).total_seconds() > baseline.forecast_max_age_seconds):
            reasons.append("ENERGY_FORECAST_UNVERIFIED")
        if (energy.battery_energy_verified is not True or not _number(energy.usable_battery_wh)
                or not _number(energy.reserve_wh) or not _number(energy.max_energy_age_seconds, maximum=86400)
                or measured is None or not (measured <= now)
                or (now - measured).total_seconds() > energy.max_energy_age_seconds):
            reasons.append("BATTERY_RESERVE_UNVERIFIED")
        if not _number(energy.proposal_ttl_seconds, maximum=86400) or energy.proposal_ttl_seconds == 0:
            reasons.append("PROPOSAL_EXPIRY_UNVERIFIED")
    if reasons:
        return SeasonalDecision(Strategy.CONSERVE, tuple(reasons),
                                "Retain the existing conservative settings: required safety or energy evidence is unavailable.",
                                now, policy.decision)

    hours = (sunset - now).total_seconds() / 3600
    load_wh = snapshot.output_power_w * hours
    # Confidence-weighted PV is only an estimate. It can never override Phase 1A.
    pv_wh = energy.expected_pv_wh * energy.forecast_confidence
    required_wh = energy.reserve_wh + max(0.0, load_wh - pv_wh)
    if not all(isfinite(x) for x in (load_wh, pv_wh, required_wh)):
        return SeasonalDecision(Strategy.CONSERVE, ("ENERGY_CALCULATION_INVALID",),
                                "Retain existing settings because the energy estimate is invalid.", now, policy.decision)
    opportunity = energy.usable_battery_wh >= required_wh
    expiry = min(now + timedelta(seconds=energy.proposal_ttl_seconds),
                 issued + timedelta(seconds=baseline.forecast_max_age_seconds),
                 measured + timedelta(seconds=energy.max_energy_age_seconds),
                 snapshot.observed_at + timedelta(seconds=baseline.telemetry_max_age_seconds),
                 snapshot.forecast.observed_at + timedelta(seconds=baseline.forecast_max_age_seconds),
                 snapshot.forecast.valid_at, valid, sunset)
    if expiry <= now:
        return SeasonalDecision(Strategy.CONSERVE, ("PROPOSAL_EVIDENCE_EXPIRED",),
                                "Retain existing settings because proposal evidence has expired.", now, policy.decision)
    # Only upward adjustments relative to the approved Phase 1A switching
    # baselines are simulated. Sunny conditions never relax those baselines.
    utility = baseline.exit_battery_v if opportunity else min(bands.utility_max_v, baseline.exit_battery_v + bands.max_utility_increase_v)
    battery = baseline.enter_battery_v if opportunity else min(bands.battery_max_v, baseline.enter_battery_v + bands.max_battery_increase_v)
    if battery <= utility or battery - utility < baseline.min_hysteresis_v:
        return SeasonalDecision(Strategy.CONSERVE, ("PROPOSED_HYSTERESIS_UNAVAILABLE",),
                                "Retain existing settings because the approved bands cannot preserve hysteresis.", now, policy.decision,
                                pv_wh, load_wh, required_wh, energy.forecast_confidence)
    return SeasonalDecision(
        Strategy.SOLAR_OPPORTUNITY if opportunity else Strategy.CONSERVE,
        ("ADEQUATE_SOLAR_AND_RESERVE",) if opportunity else ("RESERVE_DEFICIT",),
        ("Consider earlier battery use in simulation; estimated energy covers the reserve."
         if opportunity else "Prefer mains in simulation; estimated energy does not cover the reserve."),
        now, policy.decision, pv_wh, load_wh, required_wh, energy.forecast_confidence, utility, battery,
        expires_at=expiry,
    )


class WriteOutcome(str, Enum):
    REPORTED_SENT = "REPORTED_SENT"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"
    READBACK_MATCHED = "READBACK_MATCHED"
    READBACK_MISMATCHED = "READBACK_MISMATCHED"


@dataclass(frozen=True)
class ReportedWrite:
    """An external report, not independently verified by this offline module."""
    reported_at: datetime
    field_id: str
    outcome: WriteOutcome

    def __post_init__(self):
        instant = _time(self.reported_at)
        if instant is None or type(self.field_id) is not str or not self.field_id or type(self.outcome) is not WriteOutcome:
            raise ValueError("invalid externally reported write")
        object.__setattr__(self, "reported_at", instant)


@dataclass(frozen=True)
class JournalEntry:
    kind: str
    recommendation: SeasonalDecision | None = None
    reported_write: ReportedWrite | None = None


class DecisionJournal:
    """Bounded in-memory entries distinguish proposals from external reports.

    Nothing here observes or executes a write. A matched setting read-back is
    not evidence that the inverter physically changed operating mode.
    """
    def __init__(self, capacity: int = 100):
        if type(capacity) is not int or capacity < 1 or capacity > 10000:
            raise ValueError("capacity must be an integer from 1 to 10000")
        self._entries = deque(maxlen=capacity)

    def record(self, decision: SeasonalDecision) -> None:
        if type(decision) is not SeasonalDecision or decision.shadow_only is not True or decision.command_executed is not False:
            raise ValueError("only shadow recommendations may be journalled")
        self._entries.append(JournalEntry("RECOMMENDATION", recommendation=decision))

    def record_reported_write(self, event: ReportedWrite) -> None:
        if type(event) is not ReportedWrite:
            raise ValueError("write report must be a ReportedWrite")
        self._entries.append(JournalEntry("EXTERNAL_WRITE_REPORT", reported_write=event))

    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)
