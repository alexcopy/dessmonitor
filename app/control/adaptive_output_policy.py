"""Pure, deterministic shadow evaluation of inverter output-mode evidence.

The evaluator consumes immutable caller-supplied data. It never reads a clock,
performs I/O, or represents a recommendation as an executed command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import isfinite


class OutputDecision(str, Enum):
    """Shadow result; none of these values is a hardware command."""

    HOLD = "HOLD"
    BLOCK = "BLOCK"
    CONSIDER = "CONSIDER"  # candidate only; never safe-to-switch or a command


class TelemetryQuality(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ForecastQuality(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class SolarOpportunity(str, Enum):
    NIGHT = "NIGHT"
    EARLY_MORNING = "EARLY_MORNING"
    LATE_DAY = "LATE_DAY"
    WEAK = "WEAK"
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DECLINING = "DECLINING"
    UNKNOWN = "UNKNOWN"


class BatteryEligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    TRIAL = "TRIAL"
    STABLE = "STABLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN = "UNKNOWN"


class OutputPolicyReason(str, Enum):
    """Stable machine-readable reasons for a shadow result."""

    SUNRISE_OPPORTUNITY = "SUNRISE_OPPORTUNITY"
    PRODUCTIVE_SOLAR = "PRODUCTIVE_SOLAR"
    DECLINING_SOLAR = "DECLINING_SOLAR"
    INVALID_SUN_TIMES = "INVALID_SUN_TIMES"
    NIGHT = "NIGHT"
    SUNSET_RESERVE = "SUNSET_RESERVE"
    PV_DEFICIT = "PV_DEFICIT"
    HIGH_MEASURED_LOAD = "HIGH_MEASURED_LOAD"
    DEMAND_EXCEEDS_PV = "DEMAND_EXCEEDS_PV"
    FAST_VOLTAGE_SAG = "FAST_VOLTAGE_SAG"
    BATTERY_GUARD = "BATTERY_GUARD"
    BATTERY_VOLTAGE_LIMIT_EXCEEDED = "BATTERY_VOLTAGE_LIMIT_EXCEEDED"
    EXIT_BATTERY_GUARD = "EXIT_BATTERY_GUARD"
    COOLDOWN = "COOLDOWN"
    CHURN_LOCKOUT = "CHURN_LOCKOUT"
    EXCESSIVE_MODE_CHANGES = "EXCESSIVE_MODE_CHANGES"
    MAINS_DWELL_UNPROVEN = "MAINS_DWELL_UNPROVEN"
    BATTERY_TRIAL_IN_PROGRESS = "BATTERY_TRIAL_IN_PROGRESS"
    STALE_TELEMETRY = "STALE_TELEMETRY"
    UNKNOWN_TELEMETRY = "UNKNOWN_TELEMETRY"
    FUTURE_TELEMETRY = "FUTURE_TELEMETRY"
    INVALID_MEASUREMENT = "INVALID_MEASUREMENT"
    INCONSISTENT_BATTERY_CURRENT = "INCONSISTENT_BATTERY_CURRENT"
    HIGH_BATTERY_DISCHARGE = "HIGH_BATTERY_DISCHARGE"
    HIGH_BATTERY_CHARGE = "HIGH_BATTERY_CHARGE"
    UNKNOWN_BATTERY_CURRENT_SAFETY = "UNKNOWN_BATTERY_CURRENT_SAFETY"
    UNKNOWN_BATTERY_VOLTAGE_LIMIT = "UNKNOWN_BATTERY_VOLTAGE_LIMIT"
    FORECAST_UNAVAILABLE = "FORECAST_UNAVAILABLE"
    FORECAST_STALE = "FORECAST_STALE"
    UNKNOWN_OUTPUT_CAPABILITY = "UNKNOWN_OUTPUT_CAPABILITY"
    INVALID_BASELINE = "INVALID_BASELINE"
    UNKNOWN_BATTERY_RISK = "UNKNOWN_BATTERY_RISK"
    INVERT_MODE_OBSERVED = "INVERT_MODE_OBSERVED"
    BATTERY_REBOUND_NOT_RECHARGE = "BATTERY_REBOUND_NOT_RECHARGE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MODE_HISTORY_UNCERTAIN = "MODE_HISTORY_UNCERTAIN"
    SHADOW_ONLY = "SHADOW_ONLY"


@dataclass(frozen=True)
class ForecastObservation:
    """Forecast evidence with explicit issue and validity times."""

    observed_at: datetime | None = None
    valid_at: datetime | None = None
    clouds_pct: float | None = None
    rain_mm: float | None = None
    precipitation_probability_pct: float | None = None
    irradiance_w_m2: float | None = None

    def __post_init__(self) -> None:
        _validate_aware(self.observed_at, "forecast.observed_at")
        _validate_aware(self.valid_at, "forecast.valid_at")
        for name, value in (
            ("clouds_pct", self.clouds_pct),
            ("rain_mm", self.rain_mm),
            ("precipitation_probability_pct", self.precipitation_probability_pct),
            ("irradiance_w_m2", self.irradiance_w_m2),
        ):
            _validate_number(value, f"forecast.{name}")
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        object.__setattr__(self, "valid_at", _utc(self.valid_at))


@dataclass(frozen=True)
class OutputTelemetry:
    """One immutable inverter observation supplied by the caller."""

    observed_at: datetime | None = None
    received_at: datetime | None = None
    working_mode: str | None = None
    output_power_w: float | None = None
    pv_power_w: float | None = None
    battery_voltage: float | None = None
    battery_current_chg: float | None = None
    battery_current_dis: float | None = None
    sunrise: datetime | None = None
    sunset: datetime | None = None
    forecast: ForecastObservation | None = None
    battery_soc: float | None = None  # reported diagnostic only; never evaluated

    def __post_init__(self) -> None:
        for name, value in (
            ("observed_at", self.observed_at),
            ("received_at", self.received_at),
            ("sunrise", self.sunrise),
            ("sunset", self.sunset),
        ):
            _validate_aware(value, name)
        for name, value in (
            ("output_power_w", self.output_power_w),
            ("pv_power_w", self.pv_power_w),
            ("battery_voltage", self.battery_voltage),
            ("battery_current_chg", self.battery_current_chg),
            ("battery_current_dis", self.battery_current_dis),
            ("battery_soc", self.battery_soc),
        ):
            _validate_number(value, name)
        if self.working_mode is not None and not isinstance(self.working_mode, str):
            raise ValueError("working_mode must be a string or None")
        for name in ("observed_at", "received_at", "sunrise", "sunset"):
            object.__setattr__(self, name, _utc(getattr(self, name)))


@dataclass(frozen=True)
class OutputPolicyBaseline:
    """Operator thresholds and generic algorithmic shadow parameters."""

    exit_battery_v: float | None = None
    enter_battery_v: float | None = None
    min_hysteresis_v: float | None = None
    # Optional operator-approved upper battery boundary; None cannot prove voltage safety.
    max_battery_voltage_v: float | None = None
    telemetry_max_age_seconds: float = 180.0
    max_telemetry_gap_seconds: float = 900.0
    forecast_max_age_seconds: float = 180.0 * 60.0
    pv_history_window_seconds: float = 900.0
    pv_minimum_span_seconds: float = 600.0
    early_morning_window_seconds: float = 60.0 * 60.0
    sunset_reserve_seconds: float = 2 * 60 * 60
    min_pv_samples: int = 2
    weak_pv_w: float = 250.0
    productive_pv_w: float = 500.0
    pv_stability_delta_w: float = 150.0
    pv_improvement_delta_w: float = 150.0
    pv_decline_delta_w: float = 100.0
    high_measured_output_w: float = 1500.0
    comparable_load_tolerance_w: float = 250.0
    voltage_sag_window_seconds: float = 900.0
    voltage_sag_min_drop_v: float = 0.30
    voltage_sag_min_rate_v_per_min: float = 0.02
    voltage_rebound_min_v: float = 0.10
    post_transition_settle_seconds: float = 180.0
    mode_continuity_max_gap_seconds: float = 300.0
    failed_trial_max_seconds: float = 10 * 60
    failed_trial_lockout_seconds: float = 60 * 60
    min_mains_dwell_seconds: float = 45 * 60
    min_battery_dwell_seconds: float = 30 * 60
    mode_change_window_seconds: float = 60 * 60
    max_mode_changes: int = 4
    max_battery_discharge_a: float | None = None
    max_battery_charge_a: float | None = None
    significant_battery_discharge_a: float | None = None
    churn_lockout_seconds: float = 60 * 60
    max_churn_lockout_seconds: float = 4 * 60 * 60
    max_failed_trial_escalation: int = 3

    def __post_init__(self) -> None:
        for name, value in (
            ("exit_battery_v", self.exit_battery_v),
            ("enter_battery_v", self.enter_battery_v),
            ("min_hysteresis_v", self.min_hysteresis_v),
            ("max_battery_voltage_v", self.max_battery_voltage_v),
        ):
            _validate_number(value, name)
        if self.exit_battery_v is not None and self.exit_battery_v <= 0:
            raise ValueError("exit_battery_v must be positive")
        if self.enter_battery_v is not None and self.enter_battery_v <= 0:
            raise ValueError("enter_battery_v must be positive")
        if self.max_battery_voltage_v is not None and self.max_battery_voltage_v <= 0:
            raise ValueError("max_battery_voltage_v must be positive")
        for name, value in (
            ("telemetry_max_age_seconds", self.telemetry_max_age_seconds),
            ("max_telemetry_gap_seconds", self.max_telemetry_gap_seconds),
            ("forecast_max_age_seconds", self.forecast_max_age_seconds),
            ("pv_history_window_seconds", self.pv_history_window_seconds),
            ("pv_minimum_span_seconds", self.pv_minimum_span_seconds),
            ("early_morning_window_seconds", self.early_morning_window_seconds),
            ("sunset_reserve_seconds", self.sunset_reserve_seconds),
            ("weak_pv_w", self.weak_pv_w),
            ("productive_pv_w", self.productive_pv_w),
            ("pv_stability_delta_w", self.pv_stability_delta_w),
            ("pv_improvement_delta_w", self.pv_improvement_delta_w),
            ("pv_decline_delta_w", self.pv_decline_delta_w),
            ("high_measured_output_w", self.high_measured_output_w),
            ("comparable_load_tolerance_w", self.comparable_load_tolerance_w),
            ("voltage_sag_window_seconds", self.voltage_sag_window_seconds),
            ("voltage_sag_min_drop_v", self.voltage_sag_min_drop_v),
            ("voltage_sag_min_rate_v_per_min", self.voltage_sag_min_rate_v_per_min),
            ("voltage_rebound_min_v", self.voltage_rebound_min_v),
            ("post_transition_settle_seconds", self.post_transition_settle_seconds),
            ("mode_continuity_max_gap_seconds", self.mode_continuity_max_gap_seconds),
            ("failed_trial_max_seconds", self.failed_trial_max_seconds),
            ("failed_trial_lockout_seconds", self.failed_trial_lockout_seconds),
            ("min_mains_dwell_seconds", self.min_mains_dwell_seconds),
            ("min_battery_dwell_seconds", self.min_battery_dwell_seconds),
            ("mode_change_window_seconds", self.mode_change_window_seconds),
            ("churn_lockout_seconds", self.churn_lockout_seconds),
            ("max_churn_lockout_seconds", self.max_churn_lockout_seconds),
        ):
            _validate_number(value, name, allow_zero=True)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        for name, value in (("max_battery_discharge_a", self.max_battery_discharge_a), ("max_battery_charge_a", self.max_battery_charge_a), ("significant_battery_discharge_a", self.significant_battery_discharge_a)):
            _validate_number(value, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        for name, value in (("min_pv_samples", self.min_pv_samples), ("max_mode_changes", self.max_mode_changes), ("max_failed_trial_escalation", self.max_failed_trial_escalation)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.min_pv_samples < 1 or self.max_mode_changes < 1 or self.max_failed_trial_escalation < 1:
            raise ValueError("sample, mode-change and escalation limits must be positive")
        if self.min_hysteresis_v is not None and self.min_hysteresis_v < 0:
            raise ValueError("min_hysteresis_v must be non-negative")


@dataclass(frozen=True)
class Evidence:
    name: str
    value: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or type(self.value) is not str:
            raise TypeError("Evidence name and value must be strings")


@dataclass(frozen=True)
class SolarOpportunityAssessment:
    classification: SolarOpportunity
    daylight_elapsed_seconds: float | None = None
    daylight_remaining_seconds: float | None = None
    pv_samples: int = 0
    pv_trend_w: float | None = None
    forecast_quality: ForecastQuality = ForecastQuality.UNKNOWN

    def __post_init__(self) -> None:
        _validate_enum_value(self.classification, SolarOpportunity, "classification")
        _validate_enum_value(self.forecast_quality, ForecastQuality, "forecast_quality")
        for name, value in (
            ("daylight_elapsed_seconds", self.daylight_elapsed_seconds),
            ("daylight_remaining_seconds", self.daylight_remaining_seconds),
            ("pv_trend_w", self.pv_trend_w),
        ):
            _validate_immutable_number(value, name)
        _validate_nonnegative_int(self.pv_samples, "pv_samples")


@dataclass(frozen=True)
class BatteryEligibilityAssessment:
    classification: BatteryEligibility
    voltage_slope_v_per_min: float | None = None
    voltage_drop_v: float | None = None
    recent_mode_changes: int = 0
    failed_trial_duration_seconds: float | None = None

    def __post_init__(self) -> None:
        _validate_enum_value(self.classification, BatteryEligibility, "classification")
        for name, value in (
            ("voltage_slope_v_per_min", self.voltage_slope_v_per_min),
            ("voltage_drop_v", self.voltage_drop_v),
            ("failed_trial_duration_seconds", self.failed_trial_duration_seconds),
        ):
            _validate_immutable_number(value, name)
        _validate_nonnegative_int(self.recent_mode_changes, "recent_mode_changes")


@dataclass(frozen=True)
class OutputPolicyDecision:
    """Complete immutable shadow result; no command is represented."""

    decision: OutputDecision
    reason_codes: tuple[OutputPolicyReason, ...]
    data_quality: TelemetryQuality
    solar_opportunity: SolarOpportunityAssessment
    battery_eligibility: BatteryEligibilityAssessment
    observed_at: datetime | None
    evaluated_at: datetime
    data_age_seconds: float | None
    baseline_exit_battery_v: float | None
    baseline_enter_battery_v: float | None
    baseline_max_battery_voltage_v: float | None = None
    effective_exit_battery_v: float | None = None
    effective_enter_battery_v: float | None = None
    lockout_expires_at: datetime | None = None
    supporting_evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    shadow_only: bool = True
    command_executed: bool = False

    def __post_init__(self) -> None:
        _validate_enum_value(self.decision, OutputDecision, "decision")
        _validate_enum_value(self.data_quality, TelemetryQuality, "data_quality")
        if type(self.solar_opportunity) is not SolarOpportunityAssessment:
            raise TypeError("solar_opportunity must be a SolarOpportunityAssessment")
        if type(self.battery_eligibility) is not BatteryEligibilityAssessment:
            raise TypeError("battery_eligibility must be a BatteryEligibilityAssessment")

        for name, value, optional in (
            ("observed_at", self.observed_at, True),
            ("evaluated_at", self.evaluated_at, False),
            ("lockout_expires_at", self.lockout_expires_at, True),
        ):
            _validate_immutable_datetime(value, name, optional=optional)
            object.__setattr__(self, name, _utc(value))
        for name, value in (
            ("data_age_seconds", self.data_age_seconds),
            ("baseline_exit_battery_v", self.baseline_exit_battery_v),
            ("baseline_enter_battery_v", self.baseline_enter_battery_v),
            ("baseline_max_battery_voltage_v", self.baseline_max_battery_voltage_v),
            ("effective_exit_battery_v", self.effective_exit_battery_v),
            ("effective_enter_battery_v", self.effective_enter_battery_v),
        ):
            _validate_immutable_number(value, name)
        if type(self.shadow_only) is not bool or type(self.command_executed) is not bool:
            raise TypeError("shadow_only and command_executed must be bools")

        # Lists are supported at the public boundary, but are copied. Other
        # collection types are rejected to preserve ordering and deterministic equality.
        if type(self.reason_codes) not in (tuple, list):
            raise TypeError("reason_codes must be a tuple or list of reason values")
        reasons = tuple(_coerce_reason_code(code) for code in self.reason_codes)
        if type(self.supporting_evidence) not in (tuple, list):
            raise TypeError("supporting_evidence must be a tuple or list of Evidence items")
        evidence = tuple(self.supporting_evidence)
        if not all(type(item) is Evidence for item in evidence):
            raise TypeError("supporting_evidence must contain Evidence items")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "supporting_evidence", evidence)
        if not self.shadow_only or self.command_executed:
            raise ValueError("Phase 1A decisions are shadow-only and cannot execute commands")


@dataclass(frozen=True)
class _CurrentSafety:
    """Current-safety findings, kept apart from sensor validity and sag evidence."""

    unverified: tuple[str, ...]  # why current safety could not be positively verified
    inconsistent: bool  # positive charge and discharge reported together
    discharge_violation: bool
    charge_violation: bool

    @property
    def verified(self) -> bool:
        return not self.unverified and not self.inconsistent

    @property
    def violated(self) -> bool:
        return self.discharge_violation or self.charge_violation


@dataclass(frozen=True)
class _FailedTrial:
    ended_at: datetime
    duration_seconds: float
    drop_v: float
    slope_v_per_min: float | None


def evaluate_output_policy(
    snapshot: OutputTelemetry,
    baseline: OutputPolicyBaseline,
    history: tuple[OutputTelemetry, ...],
    *,
    evaluated_at: datetime,
) -> OutputPolicyDecision:
    """Evaluate one observation without side effects or a system-clock read."""

    _validate_aware(evaluated_at, "evaluated_at")
    evaluated_at = _utc(evaluated_at)
    points, unknown_time, duplicate_time = _ordered_points(snapshot, history, baseline)
    all_points = points
    points = _points_in_history_window(points, snapshot, baseline)
    reasons: list[OutputPolicyReason] = [OutputPolicyReason.SHADOW_ONLY]
    evidence: list[Evidence] = [
        Evidence("measured_output_power_w", _display(snapshot.output_power_w)),
        Evidence("measured_pv_power_w", _display(snapshot.pv_power_w)),
        Evidence("battery_voltage_v", _display(snapshot.battery_voltage)),
        Evidence("battery_current_dis_a", _display(snapshot.battery_current_dis)),
        Evidence("battery_current_chg_a", _display(snapshot.battery_current_chg)),
    ]

    data_quality = _data_quality(snapshot, points, history, unknown_time, duplicate_time, baseline, evaluated_at)
    if data_quality is TelemetryQuality.STALE:
        reasons.append(OutputPolicyReason.STALE_TELEMETRY)
    elif data_quality is TelemetryQuality.UNKNOWN:
        reasons.append(OutputPolicyReason.UNKNOWN_TELEMETRY)
    if snapshot.observed_at is not None and snapshot.observed_at > evaluated_at:
        reasons.append(OutputPolicyReason.FUTURE_TELEMETRY)

    relevant_points = points
    relevant_history = tuple(point for point in relevant_points if point is not snapshot)
    measurement_issues = _history_measurement_issues(snapshot, relevant_history)
    if measurement_issues:
        reasons.append(OutputPolicyReason.INVALID_MEASUREMENT)
        evidence.append(Evidence("invalid_measurements", ",".join(measurement_issues)))
    current = _current_safety(snapshot, relevant_points, baseline)
    if not current.verified:
        reasons.append(OutputPolicyReason.UNKNOWN_BATTERY_CURRENT_SAFETY)
        evidence.append(Evidence("current_safety_unverified", ",".join(current.unverified)))
    if current.inconsistent:
        reasons.append(OutputPolicyReason.INCONSISTENT_BATTERY_CURRENT)
    if current.discharge_violation:
        reasons.append(OutputPolicyReason.HIGH_BATTERY_DISCHARGE)
    if current.charge_violation:
        reasons.append(OutputPolicyReason.HIGH_BATTERY_CHARGE)

    solar = _assess_solar(snapshot, points, baseline, evaluated_at)
    if solar.forecast_quality is ForecastQuality.STALE:
        reasons.append(OutputPolicyReason.FORECAST_STALE)
    elif solar.forecast_quality is ForecastQuality.UNKNOWN:
        reasons.append(OutputPolicyReason.FORECAST_UNAVAILABLE)
    if solar.classification is SolarOpportunity.LATE_DAY:
        reasons.append(OutputPolicyReason.SUNSET_RESERVE)
    elif solar.classification is SolarOpportunity.NIGHT:
        reasons.append(OutputPolicyReason.NIGHT)
    elif solar.classification is SolarOpportunity.EARLY_MORNING:
        reasons.append(OutputPolicyReason.SUNRISE_OPPORTUNITY)
    elif solar.classification is SolarOpportunity.WEAK:
        reasons.append(OutputPolicyReason.PV_DEFICIT)
    elif solar.classification is SolarOpportunity.UNKNOWN:
        reasons.append(OutputPolicyReason.INSUFFICIENT_HISTORY)
    elif solar.classification is SolarOpportunity.DECLINING:
        reasons.append(OutputPolicyReason.DECLINING_SOLAR)
    else:
        reasons.append(OutputPolicyReason.PRODUCTIVE_SOLAR)
    if _sun_times_invalid(snapshot):
        reasons.append(OutputPolicyReason.INVALID_SUN_TIMES)

    if snapshot.output_power_w is not None and snapshot.pv_power_w is not None:
        if snapshot.output_power_w >= baseline.high_measured_output_w:
            reasons.append(OutputPolicyReason.HIGH_MEASURED_LOAD)
            evidence.append(Evidence("demand_basis", "measured inverter output power; Tuya totals not used"))
        if snapshot.output_power_w > snapshot.pv_power_w + baseline.comparable_load_tolerance_w:
            reasons.append(OutputPolicyReason.DEMAND_EXCEEDS_PV)

    points = relevant_points
    coverage_ok = _history_coverage(all_points, snapshot, baseline)
    failures = _failed_trial_events(points, baseline)
    active_failure_lockout = _lockout_expiry(evaluated_at, failures, baseline)
    churn_expiry = _churn_lockout_expiry(points, evaluated_at, baseline)
    lockout = _later(active_failure_lockout, churn_expiry)
    if churn_expiry is not None:
        reasons.append(OutputPolicyReason.CHURN_LOCKOUT)
    battery = _assess_battery(
        snapshot,
        points,
        baseline,
        coverage_ok,
        measurement_issues,
        lockout,
        data_quality,
        current,
    )
    if battery.voltage_drop_v is not None:
        evidence.append(Evidence("observed_voltage_drop_v", _display(battery.voltage_drop_v)))
    if battery.voltage_slope_v_per_min is not None:
        evidence.append(Evidence("voltage_slope_v_per_min", _display(battery.voltage_slope_v_per_min)))

    reasons.extend(_battery_reasons(snapshot, points, battery, baseline, coverage_ok, failures, active_failure_lockout, lockout))
    baseline_valid = _baseline_is_valid(baseline)
    if not baseline_valid:
        reasons.append(OutputPolicyReason.INVALID_BASELINE)
    if baseline.enter_battery_v is None or baseline.exit_battery_v is None:
        reasons.append(OutputPolicyReason.UNKNOWN_OUTPUT_CAPABILITY)
    if baseline.max_battery_voltage_v is None:
        reasons.append(OutputPolicyReason.UNKNOWN_BATTERY_VOLTAGE_LIMIT)
    elif any(
        point.battery_voltage is not None and point.battery_voltage > baseline.max_battery_voltage_v
        for point in relevant_points
    ):
        reasons.append(OutputPolicyReason.BATTERY_VOLTAGE_LIMIT_EXCEEDED)

    current_mode = _mode(snapshot.working_mode)
    if _battery_guard_breached(snapshot, current_mode, baseline):
        reasons.append(OutputPolicyReason.BATTERY_GUARD)
        if current_mode == "invert":
            reasons.append(OutputPolicyReason.EXIT_BATTERY_GUARD)
    critical_missing = _critical_missing(snapshot)
    if critical_missing:
        reasons.append(OutputPolicyReason.UNKNOWN_TELEMETRY)
        evidence.append(Evidence("missing_critical_fields", ",".join(critical_missing)))
    if not coverage_ok:
        reasons.append(OutputPolicyReason.INSUFFICIENT_HISTORY)
        evidence.append(Evidence("history_requirement_seconds", _display(_required_history_seconds(baseline))))
    if lockout is not None:
        reasons.append(OutputPolicyReason.COOLDOWN)
    if _mode_history_uncertain(points, snapshot, baseline):
        reasons.append(OutputPolicyReason.MODE_HISTORY_UNCERTAIN)

    if current_mode == "invert" and _invert_can_hold(
        data_quality, critical_missing, measurement_issues, battery, lockout, baseline, snapshot,
        not current.verified, current.violated,
        baseline.max_battery_voltage_v is None or (snapshot.battery_voltage is not None and snapshot.battery_voltage > baseline.max_battery_voltage_v),
    ):
        decision = OutputDecision.HOLD
        reasons.append(OutputPolicyReason.INVERT_MODE_OBSERVED)
    elif current_mode == "line" and _line_can_be_considered(
        data_quality, critical_missing, measurement_issues, battery, baseline, solar, reasons,
        not current.verified, current.violated,
    ):
        decision = OutputDecision.CONSIDER
    else:
        decision = OutputDecision.BLOCK

    data_age = None if snapshot.observed_at is None else (evaluated_at - snapshot.observed_at).total_seconds()
    return OutputPolicyDecision(
        decision=decision,
        reason_codes=_unique_reasons(reasons),
        data_quality=data_quality,
        solar_opportunity=solar,
        battery_eligibility=battery,
        observed_at=snapshot.observed_at,
        evaluated_at=evaluated_at,
        data_age_seconds=data_age,
        baseline_exit_battery_v=baseline.exit_battery_v,
        baseline_enter_battery_v=baseline.enter_battery_v,
        baseline_max_battery_voltage_v=baseline.max_battery_voltage_v,
        lockout_expires_at=lockout,
        supporting_evidence=tuple(evidence),
    )


def _validate_aware(value: datetime | None, name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware")


def _validate_number(value: float | None, name: str, allow_zero: bool = True) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"{name} must be finite or None")
    if not allow_zero and float(value) <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_enum_value(value: object, enum_type: type[Enum], name: str) -> None:
    if type(value) is not enum_type:
        raise TypeError(f"{name} must be a {enum_type.__name__}")


def _validate_immutable_number(value: object, name: str) -> None:
    if value is not None and type(value) not in (int, float):
        raise TypeError(f"{name} must be a built-in int, float, or None")
    _validate_number(value, name)


def _validate_nonnegative_int(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise TypeError(f"{name} must be a non-negative int")


def _validate_immutable_datetime(value: object, name: str, *, optional: bool) -> None:
    if value is None and optional:
        return
    if type(value) is not datetime:
        raise TypeError(f"{name} must be a datetime" + (" or None" if optional else ""))
    _validate_aware(value, name)


def _coerce_reason_code(value: object) -> OutputPolicyReason:
    if type(value) is OutputPolicyReason:
        return value
    if type(value) is str:
        return OutputPolicyReason(value)
    raise TypeError("reason_codes items must be OutputPolicyReason values or strings")


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(timezone.utc)


def _display(value: object) -> str:
    return "UNKNOWN" if value is None else str(value)


def _mode(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().lower().split())
    if normalized == "line mode":
        return "line"
    if normalized == "invert mode":
        return "invert"
    return None


def _ordered_points(
    snapshot: OutputTelemetry,
    history: tuple[OutputTelemetry, ...],
    baseline: OutputPolicyBaseline,
) -> tuple[tuple[OutputTelemetry, ...], bool, bool]:
    unknown_time = any(item.observed_at is None for item in history)
    if snapshot.observed_at is None:
        return tuple(), unknown_time, False
    history_start = snapshot.observed_at - timedelta(seconds=_required_history_seconds(baseline))
    history_times = [
        item.observed_at
        for item in history
        if item.observed_at is not None and history_start <= item.observed_at < snapshot.observed_at
    ]
    duplicate_time = len(history_times) != len(set(history_times)) or any(
        item.observed_at == snapshot.observed_at for item in history
    )
    prior = [item for item in history if item.observed_at is not None and item.observed_at < snapshot.observed_at]
    return tuple(sorted((*prior, snapshot), key=lambda item: item.observed_at)), unknown_time, duplicate_time


def _data_quality(
    snapshot: OutputTelemetry,
    points: tuple[OutputTelemetry, ...],
    history: tuple[OutputTelemetry, ...],
    history_has_unknown_time: bool,
    duplicate_time: bool,
    baseline: OutputPolicyBaseline,
    evaluated_at: datetime,
) -> TelemetryQuality:
    if snapshot.observed_at is None or history_has_unknown_time or duplicate_time:
        return TelemetryQuality.UNKNOWN
    age = (evaluated_at - snapshot.observed_at).total_seconds()
    if age < 0:
        return TelemetryQuality.UNKNOWN
    if age > baseline.telemetry_max_age_seconds:
        return TelemetryQuality.STALE
    if snapshot.received_at is not None:
        transport = (snapshot.received_at - snapshot.observed_at).total_seconds()
        if transport < 0 or snapshot.received_at > evaluated_at:
            return TelemetryQuality.UNKNOWN
        if transport > baseline.telemetry_max_age_seconds:
            return TelemetryQuality.STALE
    if any(
        item.observed_at is not None
        and (item.observed_at > evaluated_at or item.observed_at > snapshot.observed_at)
        for item in history
    ):
        return TelemetryQuality.UNKNOWN
    for left, right in zip(points, points[1:]):
        if (right.observed_at - left.observed_at).total_seconds() > baseline.max_telemetry_gap_seconds:
            return TelemetryQuality.STALE
    return TelemetryQuality.FRESH


def _measurement_issues(snapshot: OutputTelemetry) -> tuple[str, ...]:
    issues: list[str] = []
    if snapshot.output_power_w is not None and snapshot.output_power_w < 0:
        issues.append("output_power_w")
    if snapshot.pv_power_w is not None and snapshot.pv_power_w < 0:
        issues.append("pv_power_w")
    if snapshot.battery_voltage is not None and snapshot.battery_voltage <= 0:
        issues.append("battery_voltage")
    for name, value in (
        ("battery_current_chg", snapshot.battery_current_chg),
        ("battery_current_dis", snapshot.battery_current_dis),
    ):
        if value is not None and value < 0:
            issues.append(name)
    return tuple(issues)


def _current_safety(
    snapshot: OutputTelemetry, points: tuple[OutputTelemetry, ...], baseline: OutputPolicyBaseline
) -> _CurrentSafety:
    """Assess current safety separately from sensor validity.

    Absence of a violation is not proof of safety: battery operation needs an
    approved discharge limit even when the present reading is 0 A, observed
    charging needs an approved charge limit, and a missing reading is unknown.
    """
    discharges = [point.battery_current_dis for point in points if point.battery_current_dis is not None]
    charges = [point.battery_current_chg for point in points if point.battery_current_chg is not None]
    unverified: list[str] = []
    if snapshot.battery_current_dis is None:
        unverified.append("discharge_reading_missing")
    if baseline.max_battery_discharge_a is None:
        unverified.append("discharge_limit_unapproved")
    if snapshot.battery_current_chg is None:
        unverified.append("charge_reading_missing")
    if baseline.max_battery_charge_a is None and any(value > 0 for value in charges):
        unverified.append("charge_limit_unapproved")
    # The telemetry contract does not establish whether both directional channels
    # can be positive at once, so such a pair is uncertain rather than invalid.
    inconsistent = any(
        point.battery_current_chg is not None and point.battery_current_dis is not None
        and point.battery_current_chg > 0 and point.battery_current_dis > 0
        for point in points
    )
    if inconsistent:
        unverified.append("conflicting_charge_and_discharge")
    discharge_violation = baseline.max_battery_discharge_a is not None and any(
        value > baseline.max_battery_discharge_a for value in discharges
    )
    if baseline.significant_battery_discharge_a is not None and any(
        value >= baseline.significant_battery_discharge_a for value in discharges
    ):
        discharge_violation = True
    charge_violation = baseline.max_battery_charge_a is not None and any(
        value > baseline.max_battery_charge_a for value in charges
    )
    return _CurrentSafety(tuple(unverified), inconsistent, bool(discharge_violation), bool(charge_violation))


def _history_measurement_issues(
    snapshot: OutputTelemetry,
    history: tuple[OutputTelemetry, ...],
) -> tuple[str, ...]:
    issues: list[str] = list(_measurement_issues(snapshot))
    for item in history:
        issues.extend(f"history.{issue}" for issue in _measurement_issues(item))
    return tuple(dict.fromkeys(issues))


def _points_in_history_window(
    points: tuple[OutputTelemetry, ...], snapshot: OutputTelemetry, baseline: OutputPolicyBaseline
) -> tuple[OutputTelemetry, ...]:
    if snapshot.observed_at is None:
        return points
    start = snapshot.observed_at - timedelta(seconds=_required_history_seconds(baseline))
    return tuple(point for point in points if point.observed_at is not None and point.observed_at >= start)


def _sun_times_invalid(snapshot: OutputTelemetry) -> bool:
    if snapshot.observed_at is None or snapshot.sunrise is None or snapshot.sunset is None:
        return False
    daylight = snapshot.sunset - snapshot.sunrise
    return (
        snapshot.sunset <= snapshot.sunrise
        or daylight > timedelta(hours=24)
        or abs(snapshot.observed_at - snapshot.sunrise) > timedelta(hours=24)
        or abs(snapshot.observed_at - snapshot.sunset) > timedelta(hours=24)
    )


def _critical_missing(snapshot: OutputTelemetry) -> tuple[str, ...]:
    missing: list[str] = []
    if snapshot.observed_at is None:
        missing.append("observed_at")
    if _mode(snapshot.working_mode) is None:
        missing.append("working_mode")
    for name, value in (
        ("output_power_w", snapshot.output_power_w),
        ("pv_power_w", snapshot.pv_power_w),
        ("battery_voltage", snapshot.battery_voltage),
        ("battery_current_dis", snapshot.battery_current_dis),
        ("sunrise", snapshot.sunrise),
        ("sunset", snapshot.sunset),
    ):
        if value is None:
            missing.append(name)
    return tuple(missing)


def _assess_solar(
    snapshot: OutputTelemetry,
    points: tuple[OutputTelemetry, ...],
    baseline: OutputPolicyBaseline,
    evaluated_at: datetime,
) -> SolarOpportunityAssessment:
    forecast_quality = _forecast_quality(snapshot.forecast, baseline, evaluated_at)
    if snapshot.observed_at is None or snapshot.sunrise is None or snapshot.sunset is None:
        return SolarOpportunityAssessment(SolarOpportunity.UNKNOWN, forecast_quality=forecast_quality)
    if _sun_times_invalid(snapshot):
        return SolarOpportunityAssessment(SolarOpportunity.UNKNOWN, forecast_quality=forecast_quality)
    elapsed = (snapshot.observed_at - snapshot.sunrise).total_seconds()
    remaining = (snapshot.sunset - snapshot.observed_at).total_seconds()
    if snapshot.observed_at < snapshot.sunrise or snapshot.observed_at >= snapshot.sunset:
        return SolarOpportunityAssessment(SolarOpportunity.NIGHT, elapsed, remaining, forecast_quality=forecast_quality)
    if remaining <= baseline.sunset_reserve_seconds:
        return SolarOpportunityAssessment(SolarOpportunity.LATE_DAY, elapsed, remaining, forecast_quality=forecast_quality)
    window_start = snapshot.observed_at - timedelta(seconds=baseline.pv_history_window_seconds)
    pv_points = [item for item in points if item.observed_at is not None and item.observed_at >= window_start and item.pv_power_w is not None]
    if snapshot.pv_power_w is None or snapshot.pv_power_w <= baseline.weak_pv_w:
        return SolarOpportunityAssessment(SolarOpportunity.WEAK, elapsed, remaining, len(pv_points), forecast_quality=forecast_quality)
    if len(pv_points) < baseline.min_pv_samples:
        return SolarOpportunityAssessment(SolarOpportunity.UNKNOWN, elapsed, remaining, len(pv_points), forecast_quality=forecast_quality)
    first, last = pv_points[0], pv_points[-1]
    span = (last.observed_at - first.observed_at).total_seconds()
    if span < baseline.pv_minimum_span_seconds:
        return SolarOpportunityAssessment(SolarOpportunity.UNKNOWN, elapsed, remaining, len(pv_points), forecast_quality=forecast_quality)
    duration_min = span / 60.0
    adjacent_rates = [
        (float(right.pv_power_w) - float(left.pv_power_w))
        / ((right.observed_at - left.observed_at).total_seconds() / 60.0)
        for left, right in zip(pv_points, pv_points[1:])
        if right.observed_at is not None and left.observed_at is not None
        and right.observed_at > left.observed_at
        and right.pv_power_w is not None and left.pv_power_w is not None
    ]
    ordered_rates = sorted(adjacent_rates)
    trend = ordered_rates[len(ordered_rates) // 2] if ordered_rates else 0.0
    values = sorted(float(item.pv_power_w) for item in pv_points)
    median_pv = values[len(values) // 2]
    if snapshot.pv_power_w < baseline.productive_pv_w or median_pv <= baseline.weak_pv_w:
        classification = SolarOpportunity.WEAK
    elif trend >= baseline.pv_improvement_delta_w / duration_min:
        classification = SolarOpportunity.IMPROVING
    elif trend <= -baseline.pv_decline_delta_w / duration_min:
        classification = SolarOpportunity.DECLINING
    elif abs(float(last.pv_power_w) - float(first.pv_power_w)) <= baseline.pv_stability_delta_w:
        classification = SolarOpportunity.STABLE
    else:
        classification = SolarOpportunity.WEAK
    if elapsed <= baseline.early_morning_window_seconds and classification in (SolarOpportunity.IMPROVING, SolarOpportunity.STABLE):
        classification = SolarOpportunity.EARLY_MORNING
    return SolarOpportunityAssessment(classification, elapsed, remaining, len(pv_points), trend, forecast_quality)


def _forecast_quality(forecast: ForecastObservation | None, baseline: OutputPolicyBaseline, evaluated_at: datetime) -> ForecastQuality:
    if forecast is None or forecast.observed_at is None or forecast.valid_at is None:
        return ForecastQuality.UNKNOWN
    age = (evaluated_at - forecast.observed_at).total_seconds()
    if age < 0:
        return ForecastQuality.UNKNOWN
    if age > baseline.forecast_max_age_seconds or forecast.valid_at < evaluated_at:
        return ForecastQuality.STALE
    if not any(value is not None for value in (forecast.clouds_pct, forecast.rain_mm, forecast.precipitation_probability_pct, forecast.irradiance_w_m2)):
        return ForecastQuality.UNKNOWN
    return ForecastQuality.FRESH


def _required_history_seconds(baseline: OutputPolicyBaseline) -> float:
    # Rebuild the event that sets a restriction, its escalation context, and
    # the complete safety/anti-flap detection interval.  Lockout duration is
    # capped by max_churn_lockout_seconds in _lockout_expiry.
    failed_lockout = min(
        baseline.failed_trial_lockout_seconds * baseline.max_failed_trial_escalation,
        baseline.max_churn_lockout_seconds,
    )
    restriction = max(failed_lockout, baseline.churn_lockout_seconds)
    escalation_lookback = max(baseline.mode_change_window_seconds, baseline.max_churn_lockout_seconds)
    detection = max(
        baseline.mode_change_window_seconds,
        baseline.voltage_sag_window_seconds,
        baseline.failed_trial_max_seconds,
        baseline.max_telemetry_gap_seconds,
    )
    return restriction + escalation_lookback + detection


def _history_coverage(points: tuple[OutputTelemetry, ...], snapshot: OutputTelemetry, baseline: OutputPolicyBaseline) -> bool:
    if snapshot.observed_at is None or len(points) < 2:
        return False
    required_start = snapshot.observed_at - timedelta(seconds=_required_history_seconds(baseline))
    before = [point for point in points if point.observed_at is not None and point.observed_at <= required_start]
    inside = [point for point in points if point.observed_at is not None and point.observed_at > required_start]
    if not before:
        return False
    relevant = [before[-1], *inside]
    return all((right.observed_at - left.observed_at).total_seconds() <= baseline.mode_continuity_max_gap_seconds for left, right in zip(relevant, relevant[1:]))


def _mode_segments(points: tuple[OutputTelemetry, ...], baseline: OutputPolicyBaseline) -> tuple[tuple[OutputTelemetry, ...], ...]:
    segments: list[tuple[OutputTelemetry, ...]] = []
    current: list[OutputTelemetry] = []
    previous_mode: str | None = None
    previous_at: datetime | None = None
    for point in points:
        mode = _mode(point.working_mode)
        continuous = previous_at is not None and point.observed_at is not None and (point.observed_at - previous_at).total_seconds() <= baseline.mode_continuity_max_gap_seconds
        if mode is None or not current or mode != previous_mode or not continuous:
            if current:
                segments.append(tuple(current))
            current = []
        if mode is not None:
            current.append(point)
        previous_mode = mode
        previous_at = point.observed_at
    if current:
        segments.append(tuple(current))
    return tuple(segments)


def _mode_change_times(points: tuple[OutputTelemetry, ...], baseline: OutputPolicyBaseline) -> tuple[datetime, ...]:
    result: list[datetime] = []
    previous_mode: str | None = None
    previous_at: datetime | None = None
    for point in points:
        mode = _mode(point.working_mode)
        if mode is None:
            previous_mode = None
            previous_at = point.observed_at
            continue
        if previous_at is not None and point.observed_at is not None and (point.observed_at - previous_at).total_seconds() > baseline.mode_continuity_max_gap_seconds:
            previous_mode = None
        if previous_mode is not None and mode != previous_mode:
            result.append(point.observed_at)
        previous_mode = mode
        previous_at = point.observed_at
    return tuple(result)


def _mode_history_uncertain(points: tuple[OutputTelemetry, ...], snapshot: OutputTelemetry, baseline: OutputPolicyBaseline) -> bool:
    if snapshot.observed_at is None:
        return True
    cutoff = snapshot.observed_at - timedelta(seconds=baseline.mode_change_window_seconds)
    recent = [point for point in points if point.observed_at is not None and cutoff <= point.observed_at <= snapshot.observed_at]
    if not recent or recent[0].observed_at > cutoff:
        return True
    return any(_mode(point.working_mode) is None for point in recent)


def _mode_change_count(points: tuple[OutputTelemetry, ...], snapshot: OutputTelemetry, baseline: OutputPolicyBaseline) -> int:
    if snapshot.observed_at is None:
        return 0
    cutoff = snapshot.observed_at - timedelta(seconds=baseline.mode_change_window_seconds)
    return sum(at >= cutoff for at in _mode_change_times(points, baseline))


def _latest_segment(segments: tuple[tuple[OutputTelemetry, ...], ...], mode: str) -> tuple[OutputTelemetry, ...]:
    return segments[-1] if segments and _mode(segments[-1][0].working_mode) == mode else tuple()


def _segment_sag(segment: tuple[OutputTelemetry, ...], baseline: OutputPolicyBaseline) -> tuple[float | None, float | None]:
    if not segment:
        return None, None
    valid = [point for point in segment if point.observed_at is not None]
    if len(valid) < 3:
        return None, None
    settle_until = valid[0].observed_at + timedelta(seconds=baseline.post_transition_settle_seconds)
    settled = [point for point in valid if point.observed_at >= settle_until]
    if len(settled) < 3 or settled[-1] is not segment[-1]:
        return None, None
    terminal_start = settled[-1].observed_at - timedelta(seconds=baseline.voltage_sag_window_seconds)
    terminal: list[OutputTelemetry] = []
    for point in settled:
        if point.observed_at < terminal_start:
            continue
        if _voltage_load_measurement_issues(point):
            terminal = []
            continue
        if terminal and (point.observed_at - terminal[-1].observed_at).total_seconds() > baseline.mode_continuity_max_gap_seconds:
            terminal = []
        terminal.append(point)
    if len(terminal) < 3 or terminal[-1] is not segment[-1]:
        return None, None
    pair = _sag_pair(terminal[0], terminal[-1], baseline, terminal[1:])
    return pair if pair is not None else (None, None)


def _sag_pair(
    first: OutputTelemetry,
    last: OutputTelemetry,
    baseline: OutputPolicyBaseline,
    intervening: tuple[OutputTelemetry, ...] | list[OutputTelemetry] = (),
) -> tuple[float, float] | None:
    if first.observed_at is None or last.observed_at is None:
        return None
    duration = (last.observed_at - first.observed_at).total_seconds()
    if duration <= 0 or duration > baseline.voltage_sag_window_seconds:
        return None
    # A falling load strengthens collapse evidence. A sufficiently rising load
    # can confound ordinary load-normalized sag evidence.
    if max(point.output_power_w for point in (last, *intervening)) - first.output_power_w > baseline.comparable_load_tolerance_w:
        return None
    drop = float(first.battery_voltage) - float(last.battery_voltage)
    return drop, drop / (duration / 60.0)


def _is_fast_sag(drop: float | None, slope: float | None, baseline: OutputPolicyBaseline) -> bool:
    return drop is not None and slope is not None and drop >= baseline.voltage_sag_min_drop_v and slope >= baseline.voltage_sag_min_rate_v_per_min


def _one_sample_trial_failed(previous_line: tuple[OutputTelemetry, ...], invert_sample: OutputTelemetry, baseline: OutputPolicyBaseline) -> tuple[float, float] | None:
    if not previous_line or invert_sample.battery_voltage is None or invert_sample.output_power_w is None:
        return None
    prior = previous_line[-1]
    if prior.battery_voltage is None or prior.output_power_w is None:
        return None
    if invert_sample.output_power_w - prior.output_power_w > baseline.comparable_load_tolerance_w:
        return None
    drop = float(prior.battery_voltage) - float(invert_sample.battery_voltage)
    minimum_drop = max(baseline.voltage_sag_min_drop_v * 2.0, baseline.voltage_sag_min_drop_v + baseline.voltage_rebound_min_v)
    return (drop, 0.0) if drop >= minimum_drop else None


def _failed_trial_events(points: tuple[OutputTelemetry, ...], baseline: OutputPolicyBaseline) -> tuple[_FailedTrial, ...]:
    segments = _mode_segments(points, baseline)
    events: list[_FailedTrial] = []
    for index, segment in enumerate(segments[:-1]):
        if _mode(segment[0].working_mode) != "invert" or _mode(segments[index + 1][0].working_mode) != "line":
            continue
        duration = (segment[-1].observed_at - segment[0].observed_at).total_seconds()
        trial_drop, trial_slope = _trial_sag(segment, baseline)
        if duration <= baseline.failed_trial_max_seconds and _is_fast_sag(trial_drop, trial_slope, baseline):
            events.append(_FailedTrial(segment[-1].observed_at, duration, trial_drop, trial_slope))
        elif duration > baseline.failed_trial_max_seconds:
            terminal_drop, terminal_slope = _segment_sag(segment, baseline)
            if _is_fast_sag(terminal_drop, terminal_slope, baseline):
                events.append(_FailedTrial(segment[-1].observed_at, duration, terminal_drop, terminal_slope))
        elif len(segment) == 1 and index > 0:
            one = _one_sample_trial_failed(segments[index - 1], segment[0], baseline)
            if one is not None:
                events.append(_FailedTrial(segment[-1].observed_at, 0.0, one[0], None))

    return tuple(events)


def _trial_sag(segment: tuple[OutputTelemetry, ...], baseline: OutputPolicyBaseline) -> tuple[float | None, float | None]:
    """Use the whole completed short trial, including its settling samples."""
    runs: list[list[OutputTelemetry]] = []
    current: list[OutputTelemetry] = []
    for point in segment:
        if _voltage_load_measurement_issues(point):
            if current:
                runs.append(current)
                current = []
            continue
        if current and (point.observed_at - current[-1].observed_at).total_seconds() > baseline.mode_continuity_max_gap_seconds:
            runs.append(current)
            current = []
        current.append(point)
    if current:
        runs.append(current)
    best: tuple[float, float] | None = None
    for run in runs:
        for index, first in enumerate(run):
            for last_index in range(index + 1, len(run)):
                last = run[last_index]
                duration_seconds = (last.observed_at - first.observed_at).total_seconds()
                if duration_seconds <= 0 or duration_seconds > baseline.voltage_sag_window_seconds:
                    continue
                pair = _sag_pair(first, last, baseline, run[index + 1:last_index + 1])
                if pair is not None and (best is None or pair > best):
                    best = pair
    if best is not None:
        return best
    return None, None


def _voltage_load_measurement_issues(point: OutputTelemetry) -> bool:
    """Sag evidence validity is independent of optional current limits."""
    return (
        point.battery_voltage is None
        or point.battery_voltage <= 0
        or point.output_power_w is None
        or point.output_power_w < 0
    )


def _is_rebound(previous: OutputTelemetry | None, snapshot: OutputTelemetry, baseline: OutputPolicyBaseline) -> bool:
    return (
        previous is not None
        and previous.observed_at is not None
        and snapshot.observed_at is not None
        and (snapshot.observed_at - previous.observed_at).total_seconds() <= baseline.mode_continuity_max_gap_seconds
        and _mode(previous.working_mode) == "invert"
        and _mode(snapshot.working_mode) == "line"
        and previous.battery_voltage is not None
        and snapshot.battery_voltage is not None
        and snapshot.battery_voltage - previous.battery_voltage >= baseline.voltage_rebound_min_v
    )


def _lockout_expiry(evaluated_at: datetime, failures: tuple[_FailedTrial, ...], baseline: OutputPolicyBaseline) -> datetime | None:
    """Return the latest active expiry without re-aging the triggering event."""

    expiries: list[datetime] = []
    escalation_window = max(baseline.mode_change_window_seconds, baseline.max_churn_lockout_seconds)
    for index, failure in enumerate(failures):
        if failure.ended_at > evaluated_at:
            continue
        window_start = failure.ended_at - timedelta(seconds=escalation_window)
        previous_failures = sum(window_start <= earlier.ended_at <= failure.ended_at for earlier in failures[: index + 1])
        multiplier = min(previous_failures, baseline.max_failed_trial_escalation)
        duration = min(baseline.failed_trial_lockout_seconds * multiplier, baseline.max_churn_lockout_seconds)
        expiry = failure.ended_at + timedelta(seconds=duration)
        if expiry > evaluated_at:
            expiries.append(expiry)
    return max(expiries) if expiries else None


def _churn_lockout_expiry(points: tuple[OutputTelemetry, ...], evaluated_at: datetime, baseline: OutputPolicyBaseline) -> datetime | None:
    changes = [at for at in _mode_change_times(points, baseline) if at <= evaluated_at]
    expiries: list[datetime] = []
    for index, change in enumerate(changes):
        window_start = change - timedelta(seconds=baseline.mode_change_window_seconds)
        recent_count = sum(window_start <= earlier <= change for earlier in changes[: index + 1])
        if recent_count >= baseline.max_mode_changes:
            expiry = change + timedelta(seconds=baseline.churn_lockout_seconds)
            if expiry > evaluated_at:
                expiries.append(expiry)
    return max(expiries) if expiries else None


def _assess_battery(
    snapshot: OutputTelemetry,
    points: tuple[OutputTelemetry, ...],
    baseline: OutputPolicyBaseline,
    coverage_ok: bool,
    measurement_issues: tuple[str, ...],
    lockout: datetime | None,
    data_quality: TelemetryQuality,
    current: _CurrentSafety,
) -> BatteryEligibilityAssessment:
    mode = _mode(snapshot.working_mode)
    segments = _mode_segments(points, baseline)
    segment = _latest_segment(segments, mode or "")
    if mode == "invert" and segment:
        duration = (segment[-1].observed_at - segment[0].observed_at).total_seconds()
        drop, slope = (
            _trial_sag(segment, baseline)
            if duration <= baseline.failed_trial_max_seconds
            else _segment_sag(segment, baseline)
        )
    else:
        drop, slope = None, None
    changes = _mode_change_count(points, snapshot, baseline)
    failures = _failed_trial_events(points, baseline)
    failed_duration = failures[-1].duration_seconds if failures else None
    voltage_limit_violated = baseline.max_battery_voltage_v is not None and any(
        point.battery_voltage is not None and point.battery_voltage > baseline.max_battery_voltage_v
        for point in points
    )
    if mode is None or measurement_issues or data_quality is not TelemetryQuality.FRESH:
        classification = BatteryEligibility.UNKNOWN
    elif current.violated or voltage_limit_violated:
        # A confirmed limit violation is ineligible even when other evidence is incomplete.
        classification = BatteryEligibility.INELIGIBLE
    elif (
        not coverage_ok
        or _mode_history_uncertain(points, snapshot, baseline)
        or baseline.max_battery_voltage_v is None
    ):
        classification = BatteryEligibility.UNKNOWN
    elif mode == "invert":
        if _battery_guard_breached(snapshot, mode, baseline) or _is_fast_sag(drop, slope, baseline):
            classification = BatteryEligibility.INELIGIBLE
        elif changes >= baseline.max_mode_changes or lockout is not None:
            classification = BatteryEligibility.INELIGIBLE
        elif not segment or segment[-1].observed_at - segment[0].observed_at < timedelta(seconds=baseline.min_battery_dwell_seconds):
            classification = BatteryEligibility.TRIAL
        else:
            classification = BatteryEligibility.STABLE
    else:
        prior = points[-2] if len(points) >= 2 else None
        line_segment = _latest_segment(segments, "line")
        dwell = (snapshot.observed_at - line_segment[0].observed_at).total_seconds() if line_segment and snapshot.observed_at is not None else 0.0
        if _is_rebound(prior, snapshot, baseline) or changes >= baseline.max_mode_changes or lockout is not None:
            classification = BatteryEligibility.INELIGIBLE
        elif dwell < baseline.min_mains_dwell_seconds:
            classification = BatteryEligibility.UNKNOWN
        else:
            classification = BatteryEligibility.ELIGIBLE
    # Unverified current safety can never yield a positive class, but confirmed
    # protective evidence (sag, lockout, guard) stays INELIGIBLE.
    if not current.verified and classification in (BatteryEligibility.ELIGIBLE, BatteryEligibility.STABLE, BatteryEligibility.TRIAL):
        classification = BatteryEligibility.UNKNOWN
    return BatteryEligibilityAssessment(classification, slope, drop, changes, failed_duration)


def _line_dwell_seconds(points: tuple[OutputTelemetry, ...], snapshot: OutputTelemetry, baseline: OutputPolicyBaseline) -> float:
    line_segment = _latest_segment(_mode_segments(points, baseline), "line")
    if not line_segment or snapshot.observed_at is None:
        return 0.0
    return (snapshot.observed_at - line_segment[0].observed_at).total_seconds()


def _battery_reasons(snapshot: OutputTelemetry, points: tuple[OutputTelemetry, ...], battery: BatteryEligibilityAssessment, baseline: OutputPolicyBaseline, coverage_ok: bool, failures: tuple[_FailedTrial, ...], failure_lockout: datetime | None, lockout: datetime | None) -> tuple[OutputPolicyReason, ...]:
    reasons: list[OutputPolicyReason] = []
    mode = _mode(snapshot.working_mode)
    if battery.recent_mode_changes >= baseline.max_mode_changes:
        reasons.extend((OutputPolicyReason.EXCESSIVE_MODE_CHANGES, OutputPolicyReason.CHURN_LOCKOUT))
    if _is_fast_sag(battery.voltage_drop_v, battery.voltage_slope_v_per_min, baseline) or failure_lockout is not None:
        reasons.append(OutputPolicyReason.FAST_VOLTAGE_SAG)
    if mode == "line" and len(points) >= 2 and _is_rebound(points[-2], snapshot, baseline):
        reasons.append(OutputPolicyReason.BATTERY_REBOUND_NOT_RECHARGE)
    if (
        mode == "line"
        and battery.classification is BatteryEligibility.UNKNOWN
        and coverage_ok
        and _line_dwell_seconds(points, snapshot, baseline) < baseline.min_mains_dwell_seconds
    ):
        reasons.append(OutputPolicyReason.MAINS_DWELL_UNPROVEN)
    if mode == "invert" and battery.classification is BatteryEligibility.TRIAL:
        reasons.append(OutputPolicyReason.BATTERY_TRIAL_IN_PROGRESS)
    if mode == "invert" and _battery_guard_breached(snapshot, mode, baseline):
        reasons.append(OutputPolicyReason.EXIT_BATTERY_GUARD)
    if battery.classification is BatteryEligibility.UNKNOWN:
        reasons.append(OutputPolicyReason.UNKNOWN_BATTERY_RISK)
    if lockout is not None:
        reasons.append(OutputPolicyReason.COOLDOWN)
    return tuple(reasons)


def _baseline_is_valid(baseline: OutputPolicyBaseline) -> bool:
    if baseline.exit_battery_v is None or baseline.enter_battery_v is None:
        return True
    if baseline.enter_battery_v <= baseline.exit_battery_v:
        return False
    if baseline.max_battery_voltage_v is not None and baseline.max_battery_voltage_v < baseline.enter_battery_v:
        return False
    return baseline.min_hysteresis_v is None or baseline.enter_battery_v - baseline.exit_battery_v >= baseline.min_hysteresis_v


def _battery_guard_breached(snapshot: OutputTelemetry, mode: str | None, baseline: OutputPolicyBaseline) -> bool:
    if snapshot.battery_voltage is None:
        return False
    threshold = baseline.exit_battery_v if mode == "invert" else baseline.enter_battery_v
    return threshold is not None and snapshot.battery_voltage < threshold


def _invert_can_hold(quality: TelemetryQuality, critical_missing: tuple[str, ...], measurement_issues: tuple[str, ...], battery: BatteryEligibilityAssessment, lockout: datetime | None, baseline: OutputPolicyBaseline, snapshot: OutputTelemetry, current_safety_unverified: bool, current_limit_violated: bool, voltage_limit_unverified: bool) -> bool:
    return quality is TelemetryQuality.FRESH and not critical_missing and not measurement_issues and not current_safety_unverified and not current_limit_violated and not voltage_limit_unverified and battery.classification is BatteryEligibility.STABLE and lockout is None and not _battery_guard_breached(snapshot, "invert", baseline)


def _line_can_be_considered(quality: TelemetryQuality, critical_missing: tuple[str, ...], measurement_issues: tuple[str, ...], battery: BatteryEligibilityAssessment, baseline: OutputPolicyBaseline, solar: SolarOpportunityAssessment, reasons: list[OutputPolicyReason], current_safety_unverified: bool, current_limit_violated: bool) -> bool:
    blockers = {reason for reason in reasons if reason not in {OutputPolicyReason.SHADOW_ONLY, OutputPolicyReason.PRODUCTIVE_SOLAR, OutputPolicyReason.SUNRISE_OPPORTUNITY, OutputPolicyReason.DEMAND_EXCEEDS_PV}}
    return quality is TelemetryQuality.FRESH and not critical_missing and not measurement_issues and not current_safety_unverified and not current_limit_violated and battery.classification is BatteryEligibility.ELIGIBLE and baseline.enter_battery_v is not None and baseline.exit_battery_v is not None and solar.classification in (SolarOpportunity.EARLY_MORNING, SolarOpportunity.IMPROVING, SolarOpportunity.STABLE) and solar.forecast_quality is ForecastQuality.FRESH and not blockers


def _later(first: datetime | None, second: datetime | None) -> datetime | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


def _unique_reasons(reasons: list[OutputPolicyReason]) -> tuple[OutputPolicyReason, ...]:
    return tuple(dict.fromkeys(reasons))


__all__ = [
    "BatteryEligibility",
    "BatteryEligibilityAssessment",
    "Evidence",
    "ForecastObservation",
    "ForecastQuality",
    "OutputDecision",
    "OutputPolicyBaseline",
    "OutputPolicyDecision",
    "OutputPolicyReason",
    "OutputTelemetry",
    "SolarOpportunity",
    "SolarOpportunityAssessment",
    "TelemetryQuality",
    "evaluate_output_policy",
]
