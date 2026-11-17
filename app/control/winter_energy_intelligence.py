"""Shadow-only winter energy labels composed from existing evaluators.

This module is a decision explanation layer, not a controller. It reuses the
seasonal strategy for forecast/reserve arithmetic and the Task 0076 interlock
for recovery, reserve and anti-flap gates. It never estimates battery capacity
from voltage and never issues or authorises a hardware command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Mapping

from app.control.adaptive_output_policy import OutputDecision, OutputPolicyBaseline, OutputTelemetry
from app.battery_intervals import calculate_battery_intervals
from app.control.grid_output_interlock import (
    DurableInterlockState, InterlockAction, InterlockBaseline, InterlockDecision, InterlockReason,
    InterlockSnapshot, InterlockState, KnownStatus, PvContext, evaluate_interlock,
)
from app.control.seasonal_output_strategy import (
    ApprovedBands, EnergyInputs, SeasonalDecision, Strategy, evaluate_seasonal_strategy,
)


class WinterState(str, Enum):
    WINTER_GRID_HOLD = "WINTER_GRID_HOLD"
    SOLAR_CHARGING_OBSERVATION = "SOLAR_CHARGING_OBSERVATION"
    SOLAR_RETURN_CANDIDATE = "SOLAR_RETURN_CANDIDATE"
    BATTERY_USE_ELIGIBLE = "BATTERY_USE_ELIGIBLE"
    EVENING_RESERVE_CONSERVATION = "EVENING_RESERVE_CONSERVATION"
    RECOVERY_OR_FAULT_HOLD = "RECOVERY_OR_FAULT_HOLD"


class WinterProfile(str, Enum):
    WINTER = "WINTER"
    SHOULDER = "SHOULDER"
    OUT_OF_PROFILE = "OUT_OF_PROFILE"
    UNKNOWN = "UNKNOWN"


class EvidenceConfidence(str, Enum):
    VERIFIED = "VERIFIED"
    OBSERVED = "OBSERVED"
    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"


class MeasurementValidity(str, Enum):
    VALID = "VALID"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    UNVERIFIED = "UNVERIFIED"


class EvidenceFreshness(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    FUTURE = "FUTURE"
    UNKNOWN = "UNKNOWN"


class TimestampValidity(str, Enum):
    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"


# These are explicitly observational research labels. No branch below uses them
# as voltage guards or as a command threshold.
RESEARCH_VOLTAGE_CANDIDATES_V = (25.5, 26.5, 27.0, 27.5, 25.0, 25.4)
INDEPENDENT_RESERVE_SOURCES = frozenset({"BMS", "SHUNT", "BMS_SHUNT"})
WINTER_MONTHS = frozenset({11, 12, 1, 2})
SHOULDER_MONTHS = frozenset({3, 10})


def _time(value: datetime | None) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _number(value: object, *, minimum: float = 0.0) -> bool:
    return type(value) in (int, float) and isfinite(value) and value >= minimum


@dataclass(frozen=True)
class WinterPolicyConfig:
    """Conservative calendar labels, not inverter settings."""

    shoulder_max_daylight_remaining_hours: float = 8.0
    evening_reserve_window_hours: float = 4.0

    def __post_init__(self) -> None:
        for name in ("shoulder_max_daylight_remaining_hours", "evening_reserve_window_hours"):
            value = getattr(self, name)
            if not _number(value) or value == 0:
                raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True)
class WinterContext:
    """Caller-supplied context with explicit provenance requirements."""

    month: int | None = None
    daylight_remaining_hours: float | None = None
    measured_pv_observed_at: datetime | None = None
    measured_pv_provenance: str | None = None
    forecast_provenance: str | None = None
    forecast_independently_verified: bool = False
    reserve_provenance: str | None = None
    reserve_measurement_validity: MeasurementValidity = MeasurementValidity.UNVERIFIED
    reserve_observations_validated: bool = False
    independent_reserve_evidence: "IndependentReserveEvidence | None" = None
    reported_interval_status: str | None = None
    reported_interval_provenance: str = "app.battery_intervals"
    reported_interval_observations: tuple[Mapping[str, object], ...] | None = None
    external_charging: KnownStatus = KnownStatus.UNKNOWN
    pv_context: PvContext = field(default_factory=PvContext)
    evening_demand_wh: float | None = None
    evening_reserve_wh: float | None = None
    supervised_trial_approved: bool = False

    def __post_init__(self) -> None:
        if self.month is not None and (type(self.month) is not int or self.month < 1 or self.month > 12):
            raise ValueError("month must be 1-12 or None")
        for name in ("daylight_remaining_hours", "evening_demand_wh", "evening_reserve_wh"):
            value = getattr(self, name)
            if value is not None and not _number(value):
                raise ValueError(f"{name} must be a finite non-negative number or None")
        if self.measured_pv_provenance is not None and not isinstance(self.measured_pv_provenance, str):
            raise ValueError("measured_pv_provenance must be a string or None")
        if self.forecast_provenance is not None and not isinstance(self.forecast_provenance, str):
            raise ValueError("forecast_provenance must be a string or None")
        if type(self.forecast_independently_verified) is not bool:
            raise ValueError("forecast_independently_verified must be a bool")
        if self.reserve_provenance is not None and not isinstance(self.reserve_provenance, str):
            raise ValueError("reserve_provenance must be a string or None")
        object.__setattr__(self, "reserve_measurement_validity", MeasurementValidity(self.reserve_measurement_validity))
        if type(self.reserve_observations_validated) is not bool:
            raise ValueError("reserve_observations_validated must be a bool")
        if (self.independent_reserve_evidence is not None
                and type(self.independent_reserve_evidence) is not IndependentReserveEvidence):
            raise TypeError("independent_reserve_evidence must be IndependentReserveEvidence or None")
        if self.reported_interval_status is not None and not isinstance(self.reported_interval_status, str):
            raise ValueError("reported_interval_status must be a string or None")
        if not isinstance(self.reported_interval_provenance, str) or not self.reported_interval_provenance:
            raise ValueError("reported_interval_provenance must be a non-empty string")
        if self.reported_interval_observations is not None:
            if type(self.reported_interval_observations) is not tuple:
                raise ValueError("reported_interval_observations must be a tuple or None")
            if not all(isinstance(row, Mapping) for row in self.reported_interval_observations):
                raise ValueError("reported_interval_observations must contain mappings")
        object.__setattr__(self, "external_charging", KnownStatus(self.external_charging))
        if type(self.pv_context) is not PvContext:
            raise TypeError("pv_context must be a PvContext")
        if type(self.supervised_trial_approved) is not bool:
            raise ValueError("supervised_trial_approved must be a bool")


@dataclass(frozen=True)
class WinterEvidence:
    name: str
    value: str
    provenance: str
    observed_at: datetime | None
    age_seconds: float | None
    timestamp_validity: TimestampValidity
    measurement_validity: MeasurementValidity
    freshness: EvidenceFreshness
    confidence: EvidenceConfidence
    independently_verified: bool = False
    required: bool = True

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.name, self.value, self.provenance)):
            raise ValueError("evidence text fields must be non-empty strings")
        if self.observed_at is not None:
            instant = _time(self.observed_at)
            if instant is None:
                raise ValueError("observed_at must be timezone-aware or None")
            object.__setattr__(self, "observed_at", instant)
        if self.age_seconds is not None and (type(self.age_seconds) not in (int, float)
                                             or not isfinite(self.age_seconds)):
            raise ValueError("age_seconds must be a finite signed number or None")
        object.__setattr__(self, "timestamp_validity", TimestampValidity(self.timestamp_validity))
        object.__setattr__(self, "measurement_validity", MeasurementValidity(self.measurement_validity))
        object.__setattr__(self, "freshness", EvidenceFreshness(self.freshness))
        object.__setattr__(self, "confidence", EvidenceConfidence(self.confidence))
        if type(self.independently_verified) is not bool:
            raise ValueError("independently_verified must be a bool")
        if type(self.required) is not bool:
            raise ValueError("required must be a bool")


@dataclass(frozen=True)
class ReserveObservation:
    """One raw, independently sourced reserve observation."""

    observed_at: datetime
    usable_reserve_wh: float
    measurement_validity: MeasurementValidity = MeasurementValidity.VALID

    def __post_init__(self) -> None:
        if _time(self.observed_at) is None or not _number(self.usable_reserve_wh):
            raise ValueError("invalid reserve observation")
        object.__setattr__(self, "observed_at", _time(self.observed_at))
        object.__setattr__(self, "measurement_validity", MeasurementValidity(self.measurement_validity))


@dataclass(frozen=True)
class IndependentReserveEvidence:
    """Inspectable BMS/shunt evidence; this module does not attest to authenticity."""

    source_identity: str
    provenance: str
    battery_identity: str
    measurement_unit: str
    observations: tuple[ReserveObservation, ...]
    claimed_usable_reserve_wh: float
    safety_limits_validated: bool
    synthetic: bool

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
                self.source_identity, self.provenance, self.battery_identity, self.measurement_unit)):
            raise ValueError("reserve evidence identity and units are required")
        if type(self.observations) is not tuple or not self.observations \
                or not all(type(item) is ReserveObservation for item in self.observations):
            raise ValueError("reserve evidence requires underlying observations")
        if not _number(self.claimed_usable_reserve_wh):
            raise ValueError("claimed reserve must be finite and non-negative")
        if type(self.safety_limits_validated) is not bool or type(self.synthetic) is not bool:
            raise ValueError("reserve evidence flags must be bools")


@dataclass(frozen=True)
class InterlockEvaluationInputs:
    """Inputs used to recompute Task 0076 for this exact winter snapshot."""

    snapshot: InterlockSnapshot
    baseline: InterlockBaseline
    recent_history: tuple[OutputTelemetry, ...]
    durable_state: DurableInterlockState

    def __post_init__(self) -> None:
        if (type(self.snapshot) is not InterlockSnapshot
                or type(self.baseline) is not InterlockBaseline
                or type(self.recent_history) is not tuple
                or not all(type(item) is OutputTelemetry for item in self.recent_history)
                or type(self.durable_state) is not DurableInterlockState):
            raise TypeError("invalid interlock evaluation inputs")


@dataclass(frozen=True)
class WinterDecision:
    state: WinterState
    profile: WinterProfile
    reasons: tuple[str, ...]
    explanation: str
    evaluated_at: datetime
    seasonal_decision: SeasonalDecision
    interlock_state: InterlockState | None
    evidence: tuple[WinterEvidence, ...]
    missing_requirements: tuple[str, ...]
    confidence: float | None = None
    research_voltage_candidates_v: tuple[float, ...] = RESEARCH_VOLTAGE_CANDIDATES_V
    shadow_only: bool = True
    command_issued: bool = False

    def __post_init__(self) -> None:
        instant = _time(self.evaluated_at)
        if (type(self.state) is not WinterState or type(self.profile) is not WinterProfile
                or instant is None or type(self.seasonal_decision) is not SeasonalDecision
                or type(self.reasons) is not tuple or not all(isinstance(reason, str) for reason in self.reasons)
                or type(self.evidence) is not tuple or not all(type(item) is WinterEvidence for item in self.evidence)
                or type(self.missing_requirements) is not tuple
                or not all(isinstance(item, str) for item in self.missing_requirements)
                or self.shadow_only is not True or self.command_issued is not False):
            raise ValueError("invalid winter decision")
        if self.interlock_state is not None:
            object.__setattr__(self, "interlock_state", InterlockState(self.interlock_state))
        if self.confidence is not None and not _number(self.confidence) or (
                self.confidence is not None and self.confidence > 1):
            raise ValueError("confidence must be in [0, 1] or None")
        if (type(self.research_voltage_candidates_v) is not tuple
                or self.research_voltage_candidates_v != RESEARCH_VOLTAGE_CANDIDATES_V):
            raise ValueError("research candidates are fixed observational labels")
        object.__setattr__(self, "evaluated_at", instant)


def _profile(context: WinterContext, config: WinterPolicyConfig) -> WinterProfile:
    if context.month is None:
        return WinterProfile.UNKNOWN
    if context.month in WINTER_MONTHS:
        return WinterProfile.WINTER
    if context.month in SHOULDER_MONTHS:
        if (context.daylight_remaining_hours is not None
                and context.daylight_remaining_hours <= config.shoulder_max_daylight_remaining_hours):
            return WinterProfile.SHOULDER
        return WinterProfile.OUT_OF_PROFILE
    return WinterProfile.OUT_OF_PROFILE


def _age(now: datetime, observed_at: datetime | None) -> float | None:
    if observed_at is None:
        return None
    return (now - observed_at).total_seconds()


def _freshness(now: datetime, observed_at: datetime | None, max_age_seconds: float | None) -> EvidenceFreshness:
    instant = _time(observed_at)
    if instant is None or not _number(max_age_seconds) or max_age_seconds == 0:
        return EvidenceFreshness.UNKNOWN
    age = _age(now, instant)
    if age is None:
        return EvidenceFreshness.UNKNOWN
    if age < 0:
        return EvidenceFreshness.FUTURE
    return EvidenceFreshness.CURRENT if age <= max_age_seconds else EvidenceFreshness.STALE


def _known_provenance(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.upper() not in {"UNKNOWN", "UNVERIFIED"}


def _timestamp_validity(value: object) -> TimestampValidity:
    if value is None:
        return TimestampValidity.MISSING
    return TimestampValidity.VALID if _time(value) is not None else TimestampValidity.INVALID


def _evidence(name: str, value: object, provenance: str, measurement_validity: MeasurementValidity,
              *, now: datetime, observed_at: datetime | None = None, max_age_seconds: float | None = None,
              confidence: EvidenceConfidence | None = None, independently_verified: bool = False,
              timestamp_validity: TimestampValidity | None = None, required: bool = True) -> WinterEvidence:
    instant = _time(observed_at)
    freshness = _freshness(now, instant, max_age_seconds)
    if confidence is None:
        confidence = (EvidenceConfidence.VERIFIED
                      if measurement_validity is MeasurementValidity.VALID and freshness is EvidenceFreshness.CURRENT
                      and independently_verified
                      else EvidenceConfidence.OBSERVED if measurement_validity is MeasurementValidity.VALID
                      else EvidenceConfidence.UNKNOWN if measurement_validity is not MeasurementValidity.INCOMPLETE
                      else EvidenceConfidence.MISSING)
    return WinterEvidence(name, "UNKNOWN" if value is None else str(value), provenance or "UNKNOWN", instant,
                          _age(now, instant), timestamp_validity or _timestamp_validity(observed_at),
                          measurement_validity, freshness, confidence, independently_verified, required)


def _forecast_evidence(now: datetime, snapshot: OutputTelemetry, baseline: OutputPolicyBaseline,
                       energy: EnergyInputs | None, context: WinterContext) -> tuple[WinterEvidence, bool]:
    if energy is None or energy.expected_pv_wh is None:
        return (_evidence("forecast_pv_wh", None, context.forecast_provenance or "UNKNOWN",
                          MeasurementValidity.INCOMPLETE, now=now), False)
    validity = MeasurementValidity.VALID if _number(energy.expected_pv_wh) else MeasurementValidity.INVALID
    issued = _time(energy.forecast_issued_at)
    valid_through = _time(energy.forecast_valid_through)
    freshness = _freshness(now, issued, baseline.forecast_max_age_seconds)
    sunset = _time(snapshot.sunset)
    window_ok = (issued is not None and valid_through is not None and sunset is not None
                 and issued <= now <= valid_through and sunset <= valid_through)
    confidence_ok = (isinstance(energy.forecast_confidence, (int, float))
                     and isinstance(energy.minimum_confidence, (int, float))
                     and isfinite(energy.forecast_confidence) and isfinite(energy.minimum_confidence)
                     and energy.forecast_confidence >= energy.minimum_confidence)
    current = bool(validity is MeasurementValidity.VALID and freshness is EvidenceFreshness.CURRENT
                   and window_ok and confidence_ok and _known_provenance(context.forecast_provenance)
                   and context.forecast_independently_verified is True)
    return (_evidence("forecast_pv_wh", energy.expected_pv_wh, context.forecast_provenance or "UNKNOWN",
                      validity, now=now, observed_at=energy.forecast_issued_at,
                      max_age_seconds=baseline.forecast_max_age_seconds,
                      confidence=EvidenceConfidence.VERIFIED if current else EvidenceConfidence.UNKNOWN,
                      independently_verified=current), current)


def _reserve_evidence(now: datetime, energy: EnergyInputs | None,
                      context: WinterContext) -> tuple[WinterEvidence, bool, tuple[str, ...]]:
    if energy is None or energy.usable_battery_wh is None:
        return (_evidence("usable_battery_wh", None, context.reserve_provenance or "UNKNOWN",
                          MeasurementValidity.INCOMPLETE, now=now), False,
                ("RESERVE_EVIDENCE_MISSING",))
    evidence = context.independent_reserve_evidence
    if type(evidence) is not IndependentReserveEvidence:
        item = _evidence("usable_battery_wh", energy.usable_battery_wh,
                         context.reserve_provenance or "UNKNOWN", MeasurementValidity.UNVERIFIED,
                         now=now, observed_at=energy.battery_energy_observed_at,
                         max_age_seconds=energy.max_energy_age_seconds,
                         confidence=EvidenceConfidence.UNKNOWN)
        return item, False, ("RESERVE_VALIDATION_CONTEXT_MISSING",)

    reasons: list[str] = []
    observations = evidence.observations
    timestamps = [item.observed_at for item in observations]
    max_age = energy.max_energy_age_seconds
    if evidence.provenance.upper() not in INDEPENDENT_RESERVE_SOURCES:
        reasons.append("RESERVE_SOURCE_UNAPPROVED")
    if evidence.measurement_unit != "Wh":
        reasons.append("RESERVE_UNITS_UNSUPPORTED")
    if evidence.synthetic:
        reasons.append("RESERVE_SYNTHETIC_EVIDENCE")
    if evidence.safety_limits_validated is not True:
        reasons.append("RESERVE_SAFETY_LIMITS_UNVERIFIED")
    if not _number(max_age) or max_age == 0:
        reasons.append("RESERVE_FRESHNESS_BOUND_INVALID")
    if not _number(energy.usable_battery_wh) or energy.usable_battery_wh != evidence.claimed_usable_reserve_wh:
        reasons.append("RESERVE_CLAIM_MISMATCH")
    if any(item.measurement_validity is not MeasurementValidity.VALID for item in observations):
        reasons.append("RESERVE_MEASUREMENT_INVALID")
    if any(item.usable_reserve_wh != observations[0].usable_reserve_wh for item in observations[1:]):
        reasons.append("RESERVE_OBSERVATIONS_CONTRADICTORY")
    if evidence.claimed_usable_reserve_wh > min(item.usable_reserve_wh for item in observations):
        reasons.append("RESERVE_CLAIM_NOT_SUPPORTED")
    current_statuses = [_freshness(now, stamp, max_age) for stamp in timestamps]
    if any(status is EvidenceFreshness.FUTURE for status in current_statuses):
        reasons.append("RESERVE_TIMESTAMP_FUTURE")
    elif any(status is EvidenceFreshness.STALE for status in current_statuses):
        reasons.append("RESERVE_EVIDENCE_STALE")
    elif any(status is EvidenceFreshness.UNKNOWN for status in current_statuses):
        reasons.append("RESERVE_TIMESTAMP_UNVERIFIED")
    if any(status is not EvidenceFreshness.CURRENT for status in current_statuses):
        reference_at = (max(timestamps) if EvidenceFreshness.FUTURE in current_statuses
                        else min(timestamps))
    else:
        reference_at = max(timestamps)
    structurally_valid = not reasons
    reasons.append("RESERVE_TRUSTED_INTEGRATION_UNAVAILABLE")
    validity = MeasurementValidity.VALID if structurally_valid else MeasurementValidity.UNVERIFIED
    value = "%s Wh; source=%s; battery=%s; observations=%d; validation=%s" % (
        evidence.claimed_usable_reserve_wh, evidence.source_identity, evidence.battery_identity,
        len(observations), ";".join(reasons) if reasons else "VALID")
    item = _evidence("usable_battery_wh", value, evidence.provenance, validity,
                     now=now, observed_at=reference_at, max_age_seconds=max_age,
                     confidence=EvidenceConfidence.OBSERVED if structurally_valid else EvidenceConfidence.UNKNOWN,
                     independently_verified=False)
    return item, False, tuple(dict.fromkeys(reasons))


def _bound_interlock(snapshot: OutputTelemetry, now: datetime,
                     inputs: InterlockEvaluationInputs | None) -> tuple[InterlockDecision | None, str | None]:
    if type(inputs) is not InterlockEvaluationInputs:
        return None, "INTERLOCK_BINDING_UNVERIFIED"
    if inputs.snapshot.telemetry is not snapshot:
        return None, "INTERLOCK_SNAPSHOT_MISMATCH"
    try:
        return evaluate_interlock(inputs.snapshot, inputs.baseline, inputs.recent_history,
                                 inputs.durable_state, evaluated_at=now), None
    except (TypeError, ValueError, OverflowError):
        return None, "INTERLOCK_EVALUATION_UNAVAILABLE"


def _interval_evidence(now: datetime, baseline: OutputPolicyBaseline,
                       context: WinterContext) -> WinterEvidence:
    observations = context.reported_interval_observations
    if not observations:
        validity = (MeasurementValidity.UNVERIFIED if context.reported_interval_status is not None
                    else MeasurementValidity.INCOMPLETE)
        value = context.reported_interval_status
        return _evidence("reported_interval_status", value, context.reported_interval_provenance,
                          validity, now=now, confidence=EvidenceConfidence.UNKNOWN)
    result = calculate_battery_intervals(observations)
    complete = result.get("complete") is True
    validity = MeasurementValidity.VALID if complete else MeasurementValidity.INCOMPLETE
    last_time = observations[-1].get("time")
    value = "%s; underlying=%s; rejected=%s" % (
        context.reported_interval_status or "UNSPECIFIED", result.get("status"), result.get("rejected"))
    return _evidence("reported_interval_status", value, context.reported_interval_provenance,
                      validity, now=now, observed_at=last_time,
                      max_age_seconds=baseline.telemetry_max_age_seconds,
                      confidence=EvidenceConfidence.OBSERVED)


def evaluate_winter_intelligence(
    snapshot: OutputTelemetry,
    baseline: OutputPolicyBaseline,
    history: tuple[OutputTelemetry, ...],
    *,
    evaluated_at: datetime,
    energy: EnergyInputs | None,
    bands: ApprovedBands | None,
    interlock: InterlockDecision | None,
    context: WinterContext,
    config: WinterPolicyConfig | None = None,
    interlock_inputs: InterlockEvaluationInputs | None = None,
) -> WinterDecision:
    """Derive an operator-readable winter label from existing shadow decisions."""
    now = _time(evaluated_at)
    if now is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if type(snapshot) is not OutputTelemetry or type(baseline) is not OutputPolicyBaseline:
        raise TypeError("snapshot and baseline must use existing policy types")
    if type(history) is not tuple or type(context) is not WinterContext:
        raise TypeError("history and context have invalid types")
    config = config or WinterPolicyConfig()
    seasonal = evaluate_seasonal_strategy(snapshot, baseline, history, evaluated_at=now,
                                           energy=energy, bands=bands)
    trusted_interlock, interlock_binding_reason = _bound_interlock(snapshot, now, interlock_inputs)
    displayed_interlock = trusted_interlock or interlock
    profile = _profile(context, config)
    reasons: list[str] = []
    missing: list[str] = []
    evidence: list[WinterEvidence] = []

    evidence.append(_evidence("profile_month", context.month, "calendar_input",
                              MeasurementValidity.VALID if context.month is not None else MeasurementValidity.INCOMPLETE,
                              now=now, confidence=EvidenceConfidence.OBSERVED if context.month is not None else EvidenceConfidence.MISSING))
    evidence.append(_evidence("daylight_remaining_hours", context.daylight_remaining_hours, "daylight_input",
                              MeasurementValidity.VALID if context.daylight_remaining_hours is not None else MeasurementValidity.INCOMPLETE,
                              now=now, confidence=EvidenceConfidence.OBSERVED if context.daylight_remaining_hours is not None else EvidenceConfidence.MISSING))
    pv_observed_at = _time(context.measured_pv_observed_at)
    pv_freshness = _freshness(now, pv_observed_at, baseline.telemetry_max_age_seconds)
    pv_validity = MeasurementValidity.VALID if snapshot.pv_power_w is not None else MeasurementValidity.INCOMPLETE
    pv_current = bool(pv_validity is MeasurementValidity.VALID and pv_freshness is EvidenceFreshness.CURRENT
                      and _known_provenance(context.measured_pv_provenance))
    evidence.append(_evidence("measured_pv_w", snapshot.pv_power_w,
                              context.measured_pv_provenance or "UNKNOWN", pv_validity,
                              now=now, observed_at=context.measured_pv_observed_at,
                              max_age_seconds=baseline.telemetry_max_age_seconds,
                              confidence=EvidenceConfidence.OBSERVED if pv_current else EvidenceConfidence.UNKNOWN))
    telemetry_validity = MeasurementValidity.VALID if snapshot.observed_at is not None else MeasurementValidity.INCOMPLETE
    evidence.append(_evidence("output_demand_w", snapshot.output_power_w, "EASUN_REPORTED_CHANNEL", telemetry_validity,
                              now=now, observed_at=snapshot.observed_at,
                              max_age_seconds=baseline.telemetry_max_age_seconds,
                              confidence=EvidenceConfidence.OBSERVED if snapshot.output_power_w is not None else EvidenceConfidence.MISSING))
    evidence.append(_evidence("battery_voltage_v", snapshot.battery_voltage, "EASUN_REPORTED_CHANNEL", telemetry_validity,
                              now=now, observed_at=snapshot.observed_at,
                              max_age_seconds=baseline.telemetry_max_age_seconds,
                              confidence=EvidenceConfidence.OBSERVED if snapshot.battery_voltage is not None else EvidenceConfidence.MISSING))
    forecast_item, forecast_current = _forecast_evidence(now, snapshot, baseline, energy, context)
    evidence.append(forecast_item)
    reserve_item, reserve_verified, reserve_reasons = _reserve_evidence(now, energy, context)
    evidence.append(reserve_item)
    reasons.extend(reserve_reasons)
    evidence.append(_interval_evidence(now, baseline, context))
    evidence.append(_evidence("charge_current_a_reported", snapshot.battery_current_chg, "EASUN_REPORTED_CHANNEL",
                              telemetry_validity, now=now, observed_at=snapshot.observed_at,
                              max_age_seconds=baseline.telemetry_max_age_seconds,
                              confidence=EvidenceConfidence.OBSERVED if snapshot.battery_current_chg is not None else EvidenceConfidence.MISSING))
    evidence.append(_evidence("discharge_current_a_reported", snapshot.battery_current_dis, "EASUN_REPORTED_CHANNEL",
                              telemetry_validity, now=now, observed_at=snapshot.observed_at,
                              max_age_seconds=baseline.telemetry_max_age_seconds,
                              confidence=EvidenceConfidence.OBSERVED if snapshot.battery_current_dis is not None else EvidenceConfidence.MISSING))
    evidence.append(_evidence("external_charging", context.external_charging.value, "caller_readback",
                              MeasurementValidity.VALID if context.external_charging is not KnownStatus.UNKNOWN else MeasurementValidity.UNVERIFIED,
                              now=now, confidence=EvidenceConfidence.OBSERVED if context.external_charging is not KnownStatus.UNKNOWN else EvidenceConfidence.UNKNOWN))

    if profile is WinterProfile.UNKNOWN:
        reasons.append("SEASON_PROFILE_UNKNOWN")
        missing.append("verified calendar month")
    elif profile is WinterProfile.OUT_OF_PROFILE:
        reasons.append("WINTER_PROFILE_INACTIVE")
    elif profile is WinterProfile.SHOULDER:
        reasons.append("SHOULDER_PROFILE_ACTIVE")
    else:
        reasons.append("WINTER_PROFILE_ACTIVE")

    if context.daylight_remaining_hours is None:
        reasons.append("DAYLIGHT_UNVERIFIED")
        missing.append("remaining daylight")
    if context.pv_context.curtailment is not KnownStatus.ABSENT or context.pv_context.external_controller is not KnownStatus.ABSENT:
        reasons.append("PV_SOURCE_CONTEXT_UNVERIFIED")
        missing.append("verified PV curtailment/external-controller status")
    if not context.pv_context.timezone_verified:
        reasons.append("TIMEZONE_UNVERIFIED")
        missing.append("verified installation timezone")
    if not forecast_current:
        reasons.append("FORECAST_UNVERIFIED")
        missing.append("fresh forecast provenance and confidence")
    if not reserve_verified:
        reasons.append("BATTERY_RESERVE_UNVERIFIED")
        missing.append("independently verified BMS/shunt usable reserve")
    if not pv_current:
        reasons.append("PV_MEASUREMENT_UNVERIFIED")
        missing.append("fresh measured PV observation with provenance")
    if context.external_charging is KnownStatus.UNKNOWN:
        reasons.append("EXTERNAL_CHARGING_UNKNOWN")
        missing.append("external charger status")
    elif context.external_charging is KnownStatus.PRESENT:
        reasons.append("EXTERNAL_CHARGING_PRESENT")

    if displayed_interlock is None:
        reasons.append("INTERLOCK_DECISION_MISSING")
        missing.append("Task 0076 interlock decision")
    else:
        reasons.extend("INTERLOCK_" + reason.value for reason in displayed_interlock.reason_codes
                       if reason in (InterlockReason.GRID_CHARGE_NOT_SOLAR,
                                     InterlockReason.POST_RECOVERY_RESERVE_UNPROVEN,
                                     InterlockReason.CHARGER_STATE_UNVERIFIED,
                                     InterlockReason.CHARGE_POLICY_UNAVAILABLE,
                                     InterlockReason.CHARGE_POLICY_METADATA_UNVERIFIED,
                                     InterlockReason.DURABLE_STATE_UNRECONSTRUCTABLE))
    if interlock_binding_reason is not None:
        reasons.append(interlock_binding_reason)
        missing.append("snapshot-bound Task 0076 interlock evaluation")
    elif interlock is not None and trusted_interlock is not None and (
            interlock.decision is not trusted_interlock.decision
            or interlock.state is not trusted_interlock.state
            or interlock.evaluated_at != trusted_interlock.evaluated_at):
        reasons.append("INTERLOCK_REPORTED_DECISION_MISMATCH")
    if interlock is not None:
        evidence.append(_evidence(
            "interlock_reported_decision",
            "%s/%s at %s; binding=%s" % (
                interlock.decision.value, interlock.state.value,
                interlock.evaluated_at.isoformat() if isinstance(interlock.evaluated_at, datetime) else "INVALID",
                "VALID" if trusted_interlock is not None else (interlock_binding_reason or "UNVERIFIED")),
            "Task_0076_reported_decision", MeasurementValidity.UNVERIFIED, now=now,
            observed_at=interlock.evaluated_at if isinstance(interlock.evaluated_at, datetime) else None,
            max_age_seconds=baseline.telemetry_max_age_seconds, confidence=EvidenceConfidence.UNKNOWN,
            required=True))

    charger_mode = (trusted_interlock.observed_charger_mode if trusted_interlock is not None
                    else interlock.observed_charger_mode if interlock is not None else None)
    charging = snapshot.battery_current_chg is not None and snapshot.battery_current_chg > 0
    grid_recovery_states = {InterlockState.GRID_RECOVERY_ACTIVE, InterlockState.GRID_RECOVERY_COMPLETE,
                            InterlockState.CHURN_LOCKOUT, InterlockState.FAULT_HOLD}
    interlock_fault = (displayed_interlock is None
                       or displayed_interlock.state in grid_recovery_states
                       or displayed_interlock.decision is InterlockAction.BLOCK)
    interlock_authority = bool(
        type(trusted_interlock) is InterlockDecision
        and trusted_interlock.decision is InterlockAction.ADVISE
        and trusted_interlock.state is InterlockState.BATTERY_RETURN_ELIGIBLE
        and trusted_interlock.advisory_only is True
        and trusted_interlock.command_issued is False
        and trusted_interlock.application_authorized_trial is False
    )
    grid_charging = charger_mode == "solar_and_mains" or context.external_charging is KnownStatus.PRESENT
    if grid_charging:
        reasons.append("GRID_CHARGING_NOT_SOLAR")
    if charging and charger_mode == "solar_only" and snapshot.pv_power_w is not None:
        reasons.append("SOLAR_CHARGING_OBSERVED_FROM_REPORTED_CHANNELS")

    if (context.daylight_remaining_hours is not None and context.daylight_remaining_hours <= config.evening_reserve_window_hours):
        if (context.evening_demand_wh is not None and context.evening_reserve_wh is not None
                and context.evening_reserve_wh < context.evening_demand_wh):
            reasons.append("EVENING_RESERVE_DEFICIT")
            return WinterDecision(WinterState.EVENING_RESERVE_CONSERVATION, profile, tuple(dict.fromkeys(reasons)),
                                  "Conserve utility-backed reserve: independently estimated evening demand exceeds the available reserve.",
                                  now, seasonal, displayed_interlock.state if displayed_interlock else None, tuple(evidence), tuple(dict.fromkeys(missing)))
        if context.evening_demand_wh is None or context.evening_reserve_wh is None:
            reasons.append("EVENING_RESERVE_UNVERIFIED")
            missing.append("evening demand and reserve estimate")

    deadlock = bool(displayed_interlock
                    and InterlockReason.POST_RECOVERY_RESERVE_UNPROVEN in displayed_interlock.reason_codes)
    if deadlock:
        reasons.append("POST_RECOVERY_EVIDENCE_DEADLOCK")
        if reserve_verified:
            reasons.append("INDEPENDENT_RESERVE_AVAILABLE_INTERLOCK_PENDING")
        else:
            reasons.append("SUPERVISED_TRIAL_REQUIRES_SEPARATE_APPROVAL")
            missing.append("post-recovery loaded Invert evidence or independently verified reserve")

    unreliable_evidence = (not reserve_verified or not forecast_current or not pv_current
                           or context.external_charging is KnownStatus.UNKNOWN
                           or context.pv_context.curtailment is not KnownStatus.ABSENT
                           or context.pv_context.external_controller is not KnownStatus.ABSENT
                           or not context.pv_context.timezone_verified)
    if interlock_fault or grid_charging or deadlock and not reserve_verified or unreliable_evidence:
        return WinterDecision(WinterState.RECOVERY_OR_FAULT_HOLD, profile, tuple(dict.fromkeys(reasons)),
                              "Hold utility preference: recovery, lockout, unreliable evidence, or the post-recovery reserve deadlock prevents optimisation.",
                              now, seasonal, displayed_interlock.state if displayed_interlock else None, tuple(evidence), tuple(dict.fromkeys(missing)))
    if context.daylight_remaining_hours is not None and context.daylight_remaining_hours <= config.evening_reserve_window_hours:
        return WinterDecision(WinterState.EVENING_RESERVE_CONSERVATION, profile, tuple(dict.fromkeys(reasons)),
                              "Conserve evening reserve because the remaining daylight window is short and reserve evidence is incomplete.",
                              now, seasonal, displayed_interlock.state if displayed_interlock else None, tuple(evidence), tuple(dict.fromkeys(missing)))
    if charging and charger_mode == "solar_only" and not grid_charging:
        return WinterDecision(WinterState.SOLAR_CHARGING_OBSERVATION, profile, tuple(dict.fromkeys(reasons)),
                              "Observe reported solar charging; voltage rise and EASUN current do not by themselves prove usable reserve.",
                              now, seasonal, displayed_interlock.state if displayed_interlock else None, tuple(evidence), tuple(dict.fromkeys(missing)))
    if profile in (WinterProfile.UNKNOWN, WinterProfile.OUT_OF_PROFILE):
        return WinterDecision(WinterState.WINTER_GRID_HOLD, profile, tuple(dict.fromkeys(reasons)),
                              "The winter profile is inactive or unidentified; no winter optimisation is proposed.",
                              now, seasonal, displayed_interlock.state if displayed_interlock else None, tuple(evidence), tuple(dict.fromkeys(missing)))
    if seasonal.strategy is Strategy.SOLAR_OPPORTUNITY and interlock_authority:
        return WinterDecision(WinterState.BATTERY_USE_ELIGIBLE, profile,
                              tuple(dict.fromkeys(reasons + ["ADVISORY_ELIGIBILITY_ONLY"])),
                              "Advisory opportunity only: existing seasonal and interlock gates, independent reserve, and solar evidence are satisfied.",
                              now, seasonal, displayed_interlock.state, tuple(evidence), tuple(dict.fromkeys(missing)),
                              confidence=seasonal.confidence)
    if (snapshot.pv_power_w is not None and snapshot.pv_power_w >= baseline.productive_pv_w
            and seasonal.strategy is Strategy.SOLAR_OPPORTUNITY):
        reasons.append("SOLAR_RETURN_AWAITING_EXISTING_INTERLOCK_GATE")
        return WinterDecision(WinterState.SOLAR_RETURN_CANDIDATE, profile, tuple(dict.fromkeys(reasons)),
                              "Solar conditions are promising, but the existing interlock has not produced battery-use eligibility.",
                              now, seasonal, displayed_interlock.state if displayed_interlock else None, tuple(evidence), tuple(dict.fromkeys(missing)),
                              confidence=seasonal.confidence)
    return WinterDecision(WinterState.WINTER_GRID_HOLD, profile, tuple(dict.fromkeys(reasons)),
                          "Prefer utility while preserving reserve: solar evidence or the existing policy gates are not sufficient for battery use.",
                          now, seasonal, displayed_interlock.state if displayed_interlock else None, tuple(evidence), tuple(dict.fromkeys(missing)))


__all__ = [
    "EvidenceConfidence", "EvidenceFreshness", "INDEPENDENT_RESERVE_SOURCES", "MeasurementValidity",
    "TimestampValidity",
    "RESEARCH_VOLTAGE_CANDIDATES_V",
    "IndependentReserveEvidence", "InterlockEvaluationInputs", "ReserveObservation",
    "WinterContext", "WinterDecision", "WinterEvidence", "WinterPolicyConfig", "WinterProfile", "WinterState",
    "evaluate_winter_intelligence",
]
