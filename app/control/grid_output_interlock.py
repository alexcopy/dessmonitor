"""Task 0076: advisory grid-charge / output-source interlock (shadow only).

Pure and deterministic. The evaluator receives every input from the caller: it
never reads a clock, never performs I/O, never imports device, charger, relay or
Tuya clients, and never represents a recommendation as an executed command.

Central invariant: GRID_RECOVERY_COMPLETE does not imply BATTERY_RETURN_ELIGIBLE.
Completing grid-assisted recovery only ends charger recovery; battery-return
eligibility needs independent evidence (productive solar against measured demand,
verified charger readback, known PV context, and a comparable loaded Invert Mode
period that is not superseded by a later grid recovery or failed trial).

Adapter boundary (not wired in this task): a future adapter would build
``InterlockSnapshot`` from InverterMonitor shared state (``OutputTelemetry``),
from ``charge_policy_status`` (``ChargePolicyStatus``) and from the charger
priority readback normalised with
``app.service.battery_charge_controller.charger_mode`` (``ChargerObservation``),
and would persist ``InterlockDecision.updated_state``. The evaluator itself must
not obtain these by calling runtime command or device APIs.

Precedence (first match wins; reason codes from later checks are still reported):
  1. GRID_RECOVERY_ACTIVE  — fresh charge-policy recovery or verified grid-charging
                             readback. Emergency charging always has precedence.
  2. FAULT_HOLD            — unavailable durable state, stale/future/incomplete/
                             contradictory telemetry, unknown working mode,
                             invalid baseline.
  3. CHURN_LOCKOUT         — active lockout from history, durable state or an
                             ongoing failed battery trial.
  4. GRID_RECOVERY_COMPLETE — recovery completion inside the report window.
  5. BATTERY_TRIAL         — Invert Mode observed (observation label only).
  6. GRID_OUTPUT_HOLD / SOLAR_RETURN_CANDIDATE / BATTERY_RETURN_ELIGIBLE (Line Mode).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import isfinite
from typing import Iterable, Mapping

from app.control.adaptive_output_policy import (
    BatteryEligibility,
    Evidence,
    OutputDecision,
    OutputPolicyBaseline,
    OutputPolicyDecision,
    OutputPolicyReason,
    OutputTelemetry,
    SolarOpportunity,
    TelemetryQuality,
    evaluate_output_policy,
)
# Reused, side-effect-free helpers of the existing shadow evaluator. They keep mode
# segmentation and sag detection identical between the two evaluators.
from app.control.adaptive_output_policy import (  # noqa: E402
    _is_fast_sag,
    _mode,
    _mode_segments,
    _ordered_points,
    _points_in_history_window,
    _segment_sag,
    _trial_sag,
)

# Charger labels match app.service.battery_charge_controller (not imported: that
# module pulls in runtime state and the device initializer).
SOLAR_ONLY = "solar_only"
SOLAR_AND_MAINS = "solar_and_mains"
SOLAR_PRIORITY = "solar_priority"
RECOVERY_POLICY_STATES = frozenset({"emergency_recovery", "long_night_topup"})
KNOWN_POLICY_STATES = RECOVERY_POLICY_STATES | {"idle"}


class InterlockAction(str, Enum):
    """Advisory result. None of these values is, or authorises, a hardware command."""

    HOLD = "HOLD"
    ADVISE = "ADVISE"  # advisory eligibility only; never command authorisation
    BLOCK = "BLOCK"


class InterlockState(str, Enum):
    GRID_RECOVERY_ACTIVE = "GRID_RECOVERY_ACTIVE"
    GRID_RECOVERY_COMPLETE = "GRID_RECOVERY_COMPLETE"
    GRID_OUTPUT_HOLD = "GRID_OUTPUT_HOLD"
    SOLAR_RETURN_CANDIDATE = "SOLAR_RETURN_CANDIDATE"
    BATTERY_RETURN_ELIGIBLE = "BATTERY_RETURN_ELIGIBLE"
    BATTERY_TRIAL = "BATTERY_TRIAL"
    CHURN_LOCKOUT = "CHURN_LOCKOUT"
    FAULT_HOLD = "FAULT_HOLD"


class InterlockReason(str, Enum):
    SHADOW_ONLY_NO_COMMAND = "SHADOW_ONLY_NO_COMMAND"
    OBSERVED_MODE_NOT_COMMAND = "OBSERVED_MODE_NOT_COMMAND"
    # Grid recovery
    GRID_RECOVERY_ACTIVE = "GRID_RECOVERY_ACTIVE"
    EMERGENCY_RECOVERY_PRECEDENCE = "EMERGENCY_RECOVERY_PRECEDENCE"
    GRID_CHARGE_NOT_SOLAR = "GRID_CHARGE_NOT_SOLAR"
    GRID_RECOVERY_COMPLETE_NOT_ELIGIBILITY = "GRID_RECOVERY_COMPLETE_NOT_ELIGIBILITY"
    POST_RECOVERY_RESERVE_UNPROVEN = "POST_RECOVERY_RESERVE_UNPROVEN"
    # Solar and demand
    WEAK_SOLAR_SUPPORT = "WEAK_SOLAR_SUPPORT"
    SOLAR_NOT_PRODUCTIVE = "SOLAR_NOT_PRODUCTIVE"
    LOAD_EXCEEDS_PV = "LOAD_EXCEEDS_PV"
    HIGH_MEASURED_LOAD = "HIGH_MEASURED_LOAD"
    PV_CONTEXT_UNKNOWN = "PV_CONTEXT_UNKNOWN"
    PV_CURTAILMENT_OR_EXTERNAL_CONTROLLER = "PV_CURTAILMENT_OR_EXTERNAL_CONTROLLER"
    TIMEZONE_UNVERIFIED = "TIMEZONE_UNVERIFIED"
    # Voltage and charging source
    VOLTAGE_REBOUND_NOT_RECOVERY = "VOLTAGE_REBOUND_NOT_RECOVERY"
    CHARGING_SOURCE_UNVERIFIED = "CHARGING_SOURCE_UNVERIFIED"
    CHARGER_STATE_UNVERIFIED = "CHARGER_STATE_UNVERIFIED"
    CHARGER_POLICY_MISMATCH = "CHARGER_POLICY_MISMATCH"
    CHARGE_POLICY_UNAVAILABLE = "CHARGE_POLICY_UNAVAILABLE"
    CHARGE_POLICY_METADATA_UNVERIFIED = "CHARGE_POLICY_METADATA_UNVERIFIED"
    CHARGE_POLICY_CONTRADICTORY = "CHARGE_POLICY_CONTRADICTORY"
    # Telemetry and state
    INCOMPLETE_TELEMETRY = "INCOMPLETE_TELEMETRY"
    STALE_TELEMETRY = "STALE_TELEMETRY"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    CONTRADICTORY_TELEMETRY = "CONTRADICTORY_TELEMETRY"
    INVALID_MEASUREMENT = "INVALID_MEASUREMENT"
    WORKING_MODE_UNKNOWN = "WORKING_MODE_UNKNOWN"
    INVALID_BASELINE = "INVALID_BASELINE"
    DURABLE_STATE_UNAVAILABLE = "DURABLE_STATE_UNAVAILABLE"
    DURABLE_STATE_UNRECONSTRUCTABLE = "DURABLE_STATE_UNRECONSTRUCTABLE"
    LOCKOUT_PRESERVED_ACROSS_RESTART = "LOCKOUT_PRESERVED_ACROSS_RESTART"
    PROPOSAL_EXPIRED_DISCARDED = "PROPOSAL_EXPIRED_DISCARDED"
    # Trials and lockout
    BATTERY_TRIAL_OBSERVED = "BATTERY_TRIAL_OBSERVED"
    FAILED_BATTERY_TRIAL = "FAILED_BATTERY_TRIAL"
    CHURN_LOCKOUT = "CHURN_LOCKOUT"
    POST_LOCKOUT_EVIDENCE_PENDING = "POST_LOCKOUT_EVIDENCE_PENDING"
    # Eligibility
    RESERVE_EVIDENCE_MISSING = "RESERVE_EVIDENCE_MISSING"
    BASE_POLICY_NOT_SATISFIED = "BASE_POLICY_NOT_SATISFIED"
    SOLAR_RETURN_CANDIDATE = "SOLAR_RETURN_CANDIDATE"
    BATTERY_RETURN_ELIGIBLE = "BATTERY_RETURN_ELIGIBLE"


class ChargerProvenance(str, Enum):
    DEVICE_READBACK = "DEVICE_READBACK"  # value read back from the inverter
    LAST_COMMAND_CACHE = "LAST_COMMAND_CACHE"  # a request is never proof of state
    UNKNOWN = "UNKNOWN"


class ChargePolicyProvenance(str, Enum):
    """Origin of the published charging-policy metadata."""

    RUNTIME_STATUS = "RUNTIME_STATUS"
    DEVICE_READBACK = "DEVICE_READBACK"
    UNKNOWN = "UNKNOWN"


class KnownStatus(str, Enum):
    ABSENT = "ABSENT"  # verified not present / not active
    PRESENT = "PRESENT"
    UNKNOWN = "UNKNOWN"


class StateSource(str, Enum):
    PERSISTED = "PERSISTED"
    RECONSTRUCTED = "RECONSTRUCTED"  # rebuilt from recent observations
    UNAVAILABLE = "UNAVAILABLE"
    EVALUATOR_OUTPUT = "EVALUATOR_OUTPUT"  # proposed next state; not yet persisted


class TransitionOrigin(str, Enum):
    # No command correlation exists in this scope, so observed physical transitions
    # are never attributed to the application.
    UNKNOWN_EXTERNAL_OR_DEVICE = "unknown_external_or_device"


class TrialPhase(str, Enum):
    NONE = "NONE"
    IN_PROGRESS = "IN_PROGRESS"
    SUSTAINED = "SUSTAINED"
    FAILED = "FAILED"


class ReplayLabel(str, Enum):
    VERIFIED_FROM_EXPORT = "VERIFIED_FROM_EXPORT"
    UNKNOWN_FROM_EXPORT = "UNKNOWN_FROM_EXPORT"
    SIMULATED_SCENARIO = "SIMULATED_SCENARIO"
    IMPORTED_UNVERIFIED = "IMPORTED_UNVERIFIED"
    UNVERIFIED_PROVENANCE = "UNVERIFIED_PROVENANCE"
    INDEPENDENTLY_VALIDATED = "INDEPENDENTLY_VALIDATED"


class ReplayMeasurementValidity(str, Enum):
    """Schema/semantic quality, independent from the row's source provenance."""

    VALID = "VALID"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


# ── Inputs ────────────────────────────────────────────────────────────────────


def _aware(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime or None")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ChargerObservation:
    """Charger-source priority as observed. NULL mode is unknown, never 'stopped'."""

    mode: str | None = None
    observed_at: datetime | None = None
    provenance: ChargerProvenance = ChargerProvenance.UNKNOWN

    def __post_init__(self) -> None:
        if self.mode is not None and not isinstance(self.mode, str):
            raise ValueError("mode must be a string or None")
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at"))
        object.__setattr__(self, "provenance", ChargerProvenance(self.provenance))


@dataclass(frozen=True)
class ChargePolicyStatus:
    """Read-only copy of BatteryChargeController's published status."""

    state: str | None = None
    emergency_active: bool | None = None
    desired_charger_mode: str | None = None
    evaluated_at: datetime | None = None
    provenance: ChargePolicyProvenance = ChargePolicyProvenance.UNKNOWN
    verified: bool = False

    def __post_init__(self) -> None:
        if self.state is not None and not isinstance(self.state, str):
            raise ValueError("state must be a string or None")
        if self.emergency_active is not None and type(self.emergency_active) is not bool:
            raise ValueError("emergency_active must be a bool or None")
        if self.desired_charger_mode is not None and not isinstance(self.desired_charger_mode, str):
            raise ValueError("desired_charger_mode must be a string or None")
        object.__setattr__(self, "evaluated_at", _aware(self.evaluated_at, "evaluated_at"))
        object.__setattr__(self, "provenance", ChargePolicyProvenance(self.provenance))
        if type(self.verified) is not bool:
            raise ValueError("verified must be a bool")


@dataclass(frozen=True)
class PvContext:
    curtailment: KnownStatus = KnownStatus.UNKNOWN
    external_controller: KnownStatus = KnownStatus.UNKNOWN
    timezone_verified: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "curtailment", KnownStatus(self.curtailment))
        object.__setattr__(self, "external_controller", KnownStatus(self.external_controller))
        if type(self.timezone_verified) is not bool:
            raise TypeError("timezone_verified must be a bool")


@dataclass(frozen=True)
class InterlockSnapshot:
    telemetry: OutputTelemetry
    charger: ChargerObservation = field(default_factory=ChargerObservation)
    policy: ChargePolicyStatus | None = None
    pv_context: PvContext = field(default_factory=PvContext)
    output_priority_raw: str | None = None  # observation only; never a command acknowledgement
    managed_load_w_diagnostic: float | None = None  # Tuya-managed total; diagnostic, never used as demand

    def __post_init__(self) -> None:
        if type(self.telemetry) is not OutputTelemetry:
            raise TypeError("telemetry must be an OutputTelemetry")


@dataclass(frozen=True)
class ShadowProposal:
    """Bounded, expiring shadow proposal. It never alters the operator baseline."""

    description: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if _aware(self.expires_at, "expires_at") is None:
            raise ValueError("expires_at is required")
        object.__setattr__(self, "expires_at", _aware(self.expires_at, "expires_at"))


@dataclass(frozen=True)
class DurableInterlockState:
    """Restart-safe state. Project Memory is not runtime storage; nothing is wired here."""

    source: StateSource = StateSource.UNAVAILABLE
    recovery_in_progress: bool | None = None  # None: unknown
    recovery_ended_at: datetime | None = None
    lockout_expires_at: datetime | None = None
    last_lockout_expires_at: datetime | None = None  # kept after expiry for post-lockout evidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", StateSource(self.source))
        for name in ("recovery_ended_at", "lockout_expires_at", "last_lockout_expires_at"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))


@dataclass(frozen=True)
class InterlockBaseline:
    output: OutputPolicyBaseline = field(default_factory=OutputPolicyBaseline)
    charger_max_age_seconds: float = 180.0
    policy_max_age_seconds: float = 180.0
    completion_report_seconds: float = 300.0

    def __post_init__(self) -> None:
        if type(self.output) is not OutputPolicyBaseline:
            raise TypeError("output must be an OutputPolicyBaseline")
        for name in ("charger_max_age_seconds", "policy_max_age_seconds", "completion_report_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")


# ── Output ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InterlockDecision:
    decision: InterlockAction
    state: InterlockState
    reason_codes: tuple[InterlockReason, ...]
    evidence: tuple[Evidence, ...]
    evaluated_at: datetime
    observed_working_mode: str | None  # inverter's reported mode; distinct from the advisory state
    observed_charger_mode: str | None
    charger_provenance: ChargerProvenance
    output_priority_raw: str | None
    lockout_expires_at: datetime | None
    trial_phase: TrialPhase
    transition_origin: TransitionOrigin
    base_decision: OutputDecision
    base_reason_codes: tuple[OutputPolicyReason, ...]
    updated_state: DurableInterlockState
    active_proposal: ShadowProposal | None = None
    application_authorized_trial: bool = False
    advisory_only: bool = True
    command_issued: bool = False

    def __post_init__(self) -> None:
        if self.command_issued or not self.advisory_only or self.application_authorized_trial:
            raise ValueError("Task 0076 decisions are advisory only and can never issue or authorise a command")
        if self.decision is InterlockAction.ADVISE and self.state is not InterlockState.BATTERY_RETURN_ELIGIBLE:
            raise ValueError("ADVISE is only valid for BATTERY_RETURN_ELIGIBLE")


_ACTIONS = {
    InterlockState.GRID_RECOVERY_ACTIVE: InterlockAction.BLOCK,
    InterlockState.FAULT_HOLD: InterlockAction.BLOCK,
    InterlockState.CHURN_LOCKOUT: InterlockAction.BLOCK,
    InterlockState.GRID_RECOVERY_COMPLETE: InterlockAction.HOLD,
    InterlockState.GRID_OUTPUT_HOLD: InterlockAction.HOLD,
    InterlockState.SOLAR_RETURN_CANDIDATE: InterlockAction.HOLD,
    InterlockState.BATTERY_TRIAL: InterlockAction.HOLD,
    InterlockState.BATTERY_RETURN_ELIGIBLE: InterlockAction.ADVISE,
}


# ── Evaluator ─────────────────────────────────────────────────────────────────


def _later(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _age(evaluated_at: datetime, at: datetime | None) -> float | None:
    return None if at is None else (evaluated_at - at).total_seconds()


def _fresh(evaluated_at: datetime, at: datetime | None, max_age: float) -> bool:
    age = _age(evaluated_at, at)
    return age is not None and 0 <= age <= max_age


def _show(value: object) -> str:
    return "UNKNOWN" if value is None else (value.isoformat() if isinstance(value, datetime) else str(value))


def _reserve_evidence(points: tuple[OutputTelemetry, ...], snapshot: OutputTelemetry, baseline: OutputPolicyBaseline,
                      not_before: datetime | None) -> Evidence | None:
    """Independent reserve evidence: a completed, comparable loaded Invert Mode period.

    The period must last at least min_battery_dwell_seconds, show no fast sag, stay at
    or above the approved exit guard, report discharge current within the approved limit
    on every sample, and end after ``not_before`` (a later grid recovery, failed trial or
    lockout supersedes it). Unknown limits mean no evidence; thresholds are never invented.
    """
    if (snapshot.output_power_w is None or baseline.exit_battery_v is None
            or baseline.max_battery_discharge_a is None):
        return None
    # The newest completed Invert segment is decisive. A later unsafe, short or
    # incomparable run must not be skipped in favour of older reserve evidence.
    segments = _mode_segments(points, baseline)[:-1]
    latest_invert = next((segment for segment in reversed(segments)
                          if _mode(segment[0].working_mode) == "invert"), None)
    if latest_invert is None:
        return None
    segment = latest_invert
    end = segment[-1].observed_at
    if not_before is not None and end <= not_before:
        return None  # older evidence is superseded
    duration = (end - segment[0].observed_at).total_seconds()
    if duration < baseline.min_battery_dwell_seconds:
        return None
    if any(point.output_power_w is None or point.battery_voltage is None or point.battery_current_dis is None
           for point in segment):
        return None
    loads = sorted(point.output_power_w for point in segment)
    if abs(loads[len(loads) // 2] - snapshot.output_power_w) > baseline.comparable_load_tolerance_w:
        return None
    if min(point.battery_voltage for point in segment) < baseline.exit_battery_v:
        return None
    if max(point.battery_current_dis for point in segment) > baseline.max_battery_discharge_a:
        return None
    if _is_fast_sag(*_segment_sag(segment, baseline), baseline) or _is_fast_sag(*_trial_sag(segment, baseline), baseline):
        return None
    return Evidence("reserve_evidence", "invert segment %s–%s, %.0f s, median load %.0f W" % (
        segment[0].observed_at.isoformat(), end.isoformat(), duration, loads[len(loads) // 2]))


def _line_dwell_since(points: tuple[OutputTelemetry, ...], baseline: OutputPolicyBaseline,
                      snapshot: OutputTelemetry, since: datetime | None) -> float:
    segments = _mode_segments(points, baseline)
    if not segments or _mode(segments[-1][0].working_mode) != "line" or snapshot.observed_at is None:
        return 0.0
    start = _later(segments[-1][0].observed_at, since)
    return max(0.0, (snapshot.observed_at - start).total_seconds())


def evaluate_interlock(
    snapshot: InterlockSnapshot,
    baseline: InterlockBaseline,
    recent_history: tuple[OutputTelemetry, ...],
    durable_state: DurableInterlockState,
    *,
    evaluated_at: datetime,
    proposal: ShadowProposal | None = None,
) -> InterlockDecision:
    """Evaluate one advisory interlock decision. No side effects, no clock, no commands."""

    evaluated_at = _aware(evaluated_at, "evaluated_at")
    if evaluated_at is None:
        raise ValueError("evaluated_at is required")
    if type(baseline) is not InterlockBaseline or type(durable_state) is not DurableInterlockState:
        raise TypeError("baseline and durable_state must be InterlockBaseline and DurableInterlockState")
    out = baseline.output
    telemetry = snapshot.telemetry
    base: OutputPolicyDecision = evaluate_output_policy(telemetry, out, tuple(recent_history), evaluated_at=evaluated_at)
    base_reasons = set(base.reason_codes)
    reasons: list[InterlockReason] = [InterlockReason.SHADOW_ONLY_NO_COMMAND, InterlockReason.OBSERVED_MODE_NOT_COMMAND]
    evidence: list[Evidence] = list(base.supporting_evidence)
    mode = _mode(telemetry.working_mode)
    points, _, _ = _ordered_points(telemetry, tuple(recent_history), out)
    points = _points_in_history_window(points, telemetry, out)

    # Shadow proposal: reported only; the operator baseline is immutable.
    active_proposal = None
    if proposal is not None:
        if proposal.expires_at <= evaluated_at:
            reasons.append(InterlockReason.PROPOSAL_EXPIRED_DISCARDED)
        else:
            active_proposal = proposal
            evidence.append(Evidence("shadow_proposal", proposal.description))

    # Charger readback and charge policy (inputs only; never commanded here).
    charger = snapshot.charger
    readback_valid = (charger.provenance is ChargerProvenance.DEVICE_READBACK
                      and _fresh(evaluated_at, charger.observed_at, baseline.charger_max_age_seconds)
                      and charger.mode in (SOLAR_ONLY, SOLAR_AND_MAINS))
    if not readback_valid:
        reasons.append(InterlockReason.CHARGER_STATE_UNVERIFIED)
    policy = snapshot.policy
    policy_state_fresh = (policy is not None and policy.state in KNOWN_POLICY_STATES
                          and _fresh(evaluated_at, policy.evaluated_at, baseline.policy_max_age_seconds))
    policy_mode_valid = policy is not None and policy.desired_charger_mode in (SOLAR_ONLY, SOLAR_AND_MAINS)
    policy_emergency_valid = policy is not None and type(policy.emergency_active) is bool
    policy_contradictory = bool(
        policy_state_fresh and policy_emergency_valid
        and ((policy.state == "emergency_recovery") != policy.emergency_active)
    )
    policy_provenance_valid = bool(
        policy is not None
        and policy.provenance is not ChargePolicyProvenance.UNKNOWN
        and policy.verified
    )
    policy_metadata_valid = (policy_state_fresh and policy_mode_valid and policy_emergency_valid
                             and policy_provenance_valid and not policy_contradictory)
    policy_fresh = policy_metadata_valid
    if not policy_state_fresh:
        reasons.append(InterlockReason.CHARGE_POLICY_UNAVAILABLE)
    elif policy_contradictory:
        reasons.append(InterlockReason.CHARGE_POLICY_CONTRADICTORY)
    elif not policy_metadata_valid:
        reasons.append(InterlockReason.CHARGE_POLICY_METADATA_UNVERIFIED)
    evidence += [Evidence("charger_mode_observed", _show(charger.mode)),
                 Evidence("charger_provenance", charger.provenance.value),
                 Evidence("charger_observed_age_s", _show(_age(evaluated_at, charger.observed_at))),
                 Evidence("charge_policy_state", _show(policy.state if policy else None)),
                 Evidence("charge_policy_provenance", policy.provenance.value if policy else "UNKNOWN"),
                 Evidence("charge_policy_verified", _show(policy.verified if policy else None)),
                 Evidence("observed_mode_changes_in_window", str(base.battery_eligibility.recent_mode_changes))]
    if snapshot.managed_load_w_diagnostic is not None:
        evidence.append(Evidence("managed_load_w_diagnostic_not_used", _show(snapshot.managed_load_w_diagnostic)))

    policy_recovery = policy_fresh and policy.state in RECOVERY_POLICY_STATES
    emergency = policy_fresh and (policy.emergency_active is True or policy.state == "emergency_recovery")
    readback_grid = readback_valid and charger.mode == SOLAR_AND_MAINS
    recovery_active = policy_recovery or readback_grid
    if policy_fresh and readback_valid and policy.desired_charger_mode in (SOLAR_ONLY, SOLAR_AND_MAINS) \
            and policy.desired_charger_mode != charger.mode:
        reasons.append(InterlockReason.CHARGER_POLICY_MISMATCH)

    # Recovery completion: the policy's own release AND a fresh readback that grid charging stopped.
    recovery_in_progress = durable_state.recovery_in_progress
    recovery_ended_at = durable_state.recovery_ended_at
    completed_now = False
    if recovery_active:
        recovery_in_progress = True
    elif (policy_fresh and policy.state == "idle" and readback_valid and charger.mode == SOLAR_ONLY):
        if durable_state.recovery_in_progress is True:
            recovery_ended_at = _later(policy.evaluated_at, charger.observed_at)
            completed_now = True
            recovery_in_progress = False
        elif durable_state.recovery_in_progress is None:
            # A fresh idle/readback pair cannot reconstruct whether an unseen
            # recovery happened before restart.
            recovery_in_progress = None
        else:
            recovery_in_progress = False
    # Otherwise unknown/stale: the previous belief is kept; a stale charger is never proof of stop.

    # Lockouts: history-derived, durable (survives restart) and an ongoing failed trial.
    durable_lockout = durable_state.lockout_expires_at if (
        durable_state.lockout_expires_at is not None and durable_state.lockout_expires_at > evaluated_at) else None
    failed_trial_now = False
    battery = base.battery_eligibility
    segments = _mode_segments(points, out)
    current_segment = segments[-1] if segments and mode is not None and _mode(segments[-1][0].working_mode) == mode else ()
    if mode == "invert" and current_segment:
        duration = (current_segment[-1].observed_at - current_segment[0].observed_at).total_seconds()
        if (duration <= out.failed_trial_max_seconds and battery.classification is BatteryEligibility.INELIGIBLE
                and (OutputPolicyReason.FAST_VOLTAGE_SAG in base_reasons or OutputPolicyReason.EXIT_BATTERY_GUARD in base_reasons)):
            failed_trial_now = True
    ongoing_lockout = evaluated_at + timedelta(seconds=out.failed_trial_lockout_seconds) if failed_trial_now else None
    lockout = _later(base.lockout_expires_at, durable_lockout, ongoing_lockout)
    if durable_lockout is not None and (base.lockout_expires_at is None or durable_lockout > base.lockout_expires_at):
        reasons.append(InterlockReason.LOCKOUT_PRESERVED_ACROSS_RESTART)
    # The base evaluator reports FAST_VOLTAGE_SAG outside Invert Mode only for an active
    # failed-trial lockout (a completed short Invert period that collapsed).
    if failed_trial_now or (mode != "invert" and OutputPolicyReason.FAST_VOLTAGE_SAG in base_reasons):
        reasons.append(InterlockReason.FAILED_BATTERY_TRIAL)
    if lockout is not None:
        reasons.append(InterlockReason.CHURN_LOCKOUT)
        evidence.append(Evidence("lockout_expires_at", lockout.isoformat()))

    # Context reasons (reported in every state).
    solar = base.solar_opportunity.classification
    pv, load = telemetry.pv_power_w, telemetry.output_power_w
    if pv is not None and pv <= out.weak_pv_w:
        reasons.append(InterlockReason.WEAK_SOLAR_SUPPORT)
    if solar not in (SolarOpportunity.STABLE, SolarOpportunity.IMPROVING, SolarOpportunity.EARLY_MORNING):
        reasons.append(InterlockReason.SOLAR_NOT_PRODUCTIVE)
    if OutputPolicyReason.DEMAND_EXCEEDS_PV in base_reasons:
        reasons.append(InterlockReason.LOAD_EXCEEDS_PV)
    if OutputPolicyReason.HIGH_MEASURED_LOAD in base_reasons:
        reasons.append(InterlockReason.HIGH_MEASURED_LOAD)
    if OutputPolicyReason.BATTERY_REBOUND_NOT_RECHARGE in base_reasons:
        reasons.append(InterlockReason.VOLTAGE_REBOUND_NOT_RECOVERY)
    ctx = snapshot.pv_context
    if ctx.curtailment is KnownStatus.UNKNOWN or ctx.external_controller is KnownStatus.UNKNOWN:
        reasons.append(InterlockReason.PV_CONTEXT_UNKNOWN)
    if ctx.curtailment is KnownStatus.PRESENT or ctx.external_controller is KnownStatus.PRESENT:
        reasons.append(InterlockReason.PV_CURTAILMENT_OR_EXTERNAL_CONTROLLER)
    if not ctx.timezone_verified:
        reasons.append(InterlockReason.TIMEZONE_UNVERIFIED)
    charging = telemetry.battery_current_chg is not None and telemetry.battery_current_chg > 0
    if readback_grid and charging:
        reasons.append(InterlockReason.GRID_CHARGE_NOT_SOLAR)
    elif charging and mode == "line" and (not readback_valid or pv is None or pv <= out.weak_pv_w):
        reasons.append(InterlockReason.CHARGING_SOURCE_UNVERIFIED)

    # Fault conditions.
    faults: list[InterlockReason] = []
    if durable_state.source in (StateSource.UNAVAILABLE, StateSource.EVALUATOR_OUTPUT):
        faults.append(InterlockReason.DURABLE_STATE_UNAVAILABLE)
    if durable_state.recovery_in_progress is None:
        faults.append(InterlockReason.DURABLE_STATE_UNRECONSTRUCTABLE)
    if base.data_quality is TelemetryQuality.STALE:
        faults.append(InterlockReason.STALE_TELEMETRY)
    elif base.data_quality is TelemetryQuality.UNKNOWN:
        faults.append(InterlockReason.INCOMPLETE_TELEMETRY)
    if OutputPolicyReason.FUTURE_TELEMETRY in base_reasons or (
            charger.observed_at is not None and charger.observed_at > evaluated_at) or (
            policy is not None and policy.evaluated_at is not None and policy.evaluated_at > evaluated_at):
        faults.append(InterlockReason.FUTURE_TIMESTAMP)
    if OutputPolicyReason.UNKNOWN_TELEMETRY in base_reasons:
        faults.append(InterlockReason.INCOMPLETE_TELEMETRY)
    if OutputPolicyReason.INVALID_MEASUREMENT in base_reasons:
        faults.append(InterlockReason.INVALID_MEASUREMENT)
    if OutputPolicyReason.INCONSISTENT_BATTERY_CURRENT in base_reasons:
        faults.append(InterlockReason.CONTRADICTORY_TELEMETRY)
    if mode is None:
        faults.append(InterlockReason.WORKING_MODE_UNKNOWN)
    if OutputPolicyReason.INVALID_BASELINE in base_reasons:
        faults.append(InterlockReason.INVALID_BASELINE)
    reasons += faults

    # Reserve evidence must postdate any later grid recovery, failed trial or lockout expiry.
    not_before = _later(recovery_ended_at, durable_state.last_lockout_expires_at)
    reserve = None if (
        durable_state.recovery_in_progress is None
        or durable_state.source is StateSource.EVALUATOR_OUTPUT
    ) else _reserve_evidence(points, telemetry, out, not_before)
    if reserve is not None:
        evidence.append(reserve)

    # ── State selection (precedence documented in the module docstring) ──
    trial_phase = TrialPhase.NONE
    if recovery_active:
        state = InterlockState.GRID_RECOVERY_ACTIVE
        reasons.append(InterlockReason.GRID_RECOVERY_ACTIVE)
        if emergency:
            reasons.append(InterlockReason.EMERGENCY_RECOVERY_PRECEDENCE)
    elif faults:
        state = InterlockState.FAULT_HOLD
    elif lockout is not None:
        state = InterlockState.CHURN_LOCKOUT
        if failed_trial_now:
            trial_phase = TrialPhase.FAILED
    elif recovery_ended_at is not None and 0 <= (evaluated_at - recovery_ended_at).total_seconds() <= baseline.completion_report_seconds:
        state = InterlockState.GRID_RECOVERY_COMPLETE
        reasons.append(InterlockReason.GRID_RECOVERY_COMPLETE_NOT_ELIGIBILITY)
    elif mode == "invert":
        state = InterlockState.BATTERY_TRIAL
        reasons.append(InterlockReason.BATTERY_TRIAL_OBSERVED)
        trial_phase = TrialPhase.SUSTAINED if battery.classification is BatteryEligibility.STABLE else TrialPhase.IN_PROGRESS
    else:
        candidate_blockers = {
            InterlockReason.SOLAR_NOT_PRODUCTIVE, InterlockReason.WEAK_SOLAR_SUPPORT, InterlockReason.LOAD_EXCEEDS_PV,
            InterlockReason.HIGH_MEASURED_LOAD, InterlockReason.VOLTAGE_REBOUND_NOT_RECOVERY,
            InterlockReason.CHARGER_STATE_UNVERIFIED, InterlockReason.CHARGE_POLICY_UNAVAILABLE,
            InterlockReason.CHARGE_POLICY_METADATA_UNVERIFIED, InterlockReason.CHARGE_POLICY_CONTRADICTORY,
            InterlockReason.PV_CONTEXT_UNKNOWN, InterlockReason.PV_CURTAILMENT_OR_EXTERNAL_CONTROLLER,
            InterlockReason.TIMEZONE_UNVERIFIED, InterlockReason.CHARGING_SOURCE_UNVERIFIED,
            InterlockReason.CHARGER_POLICY_MISMATCH, InterlockReason.GRID_CHARGE_NOT_SOLAR,
            InterlockReason.DURABLE_STATE_UNRECONSTRUCTABLE}
        if recovery_in_progress is not False:
            # Unknown or still-believed recovery: completion was never confirmed.
            candidate_blockers.add(InterlockReason.CHARGER_STATE_UNVERIFIED)
            reasons.append(InterlockReason.CHARGER_STATE_UNVERIFIED)
        if any(reason in candidate_blockers for reason in reasons):
            state = InterlockState.GRID_OUTPUT_HOLD
        else:
            state = InterlockState.SOLAR_RETURN_CANDIDATE
            reasons.append(InterlockReason.SOLAR_RETURN_CANDIDATE)
            eligible = True
            if base.decision is not OutputDecision.CONSIDER:
                reasons.append(InterlockReason.BASE_POLICY_NOT_SATISFIED)
                eligible = False
            if reserve is None:
                reasons.append(InterlockReason.RESERVE_EVIDENCE_MISSING)
                if recovery_ended_at is not None:
                    reasons.append(InterlockReason.POST_RECOVERY_RESERVE_UNPROVEN)
                eligible = False
            if durable_state.last_lockout_expires_at is not None and _line_dwell_since(
                    points, out, telemetry, durable_state.last_lockout_expires_at) < out.min_mains_dwell_seconds:
                reasons.append(InterlockReason.POST_LOCKOUT_EVIDENCE_PENDING)
                eligible = False
            if durable_state.source is StateSource.RECONSTRUCTED and OutputPolicyReason.INSUFFICIENT_HISTORY in base_reasons:
                reasons.append(InterlockReason.DURABLE_STATE_UNRECONSTRUCTABLE)
                eligible = False
            if eligible:
                state = InterlockState.BATTERY_RETURN_ELIGIBLE
                reasons.append(InterlockReason.BATTERY_RETURN_ELIGIBLE)
    if state is not InterlockState.BATTERY_RETURN_ELIGIBLE and reserve is None and mode == "line" \
            and InterlockReason.RESERVE_EVIDENCE_MISSING not in reasons:
        reasons.append(InterlockReason.RESERVE_EVIDENCE_MISSING)
    if completed_now and state is not InterlockState.GRID_RECOVERY_COMPLETE:
        reasons.append(InterlockReason.GRID_RECOVERY_COMPLETE_NOT_ELIGIBILITY)

    updated = DurableInterlockState(
        source=StateSource.EVALUATOR_OUTPUT,
        recovery_in_progress=recovery_in_progress,
        recovery_ended_at=recovery_ended_at,
        lockout_expires_at=lockout,
        last_lockout_expires_at=_later(durable_state.last_lockout_expires_at, lockout),
    )
    return InterlockDecision(
        decision=_ACTIONS[state], state=state, reason_codes=tuple(dict.fromkeys(reasons)),
        evidence=tuple(evidence), evaluated_at=evaluated_at,
        observed_working_mode=telemetry.working_mode, observed_charger_mode=charger.mode,
        charger_provenance=charger.provenance, output_priority_raw=snapshot.output_priority_raw,
        lockout_expires_at=lockout, trial_phase=trial_phase,
        transition_origin=TransitionOrigin.UNKNOWN_EXTERNAL_OR_DEVICE,
        base_decision=base.decision, base_reason_codes=base.reason_codes,
        updated_state=updated, active_proposal=active_proposal,
    )


# ── Replay of collection-time exports (e.g. /api/charts/inverter) ────────────


EXPORT_FIELDS = ("time", "pv_power_w", "battery_voltage", "output_power_w", "working_mode")
# Fields the evaluator uses that /api/charts/inverter does not export. They stay unknown.
NOT_IN_EXPORT = ("battery_current_chg", "battery_current_dis", "charger_mode", "charge_policy",
                 "output_priority_raw", "sunrise", "sunset", "device_timestamp", "curtailment",
                 "external_controller", "command_history")


@dataclass(frozen=True)
class ReplayResult:
    index: int
    label: ReplayLabel
    collection_time: str | None
    missing_fields: tuple[str, ...]
    decision: InterlockDecision | None  # None when the record has no usable collection time
    provenance: ReplayLabel = ReplayLabel.UNVERIFIED_PROVENANCE
    measurement_validity: ReplayMeasurementValidity = ReplayMeasurementValidity.INCOMPLETE
    invalid_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplaySummary:
    records: int
    evaluated: int
    observed_mode_changes: int  # counted from working_mode only, independent of charger requests
    mode_changes_across_unknown: int
    states: Mapping[str, int]
    advise_count: int
    results: tuple[ReplayResult, ...]
    unknown_fields: tuple[str, ...] = NOT_IN_EXPORT


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) else None


def _replay_measurements(row: Mapping[str, object], at: datetime | None,
                         previous_at: datetime | None) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, float]]:
    """Return missing fields, invalid fields and only semantically valid numbers.

    Export provenance is deliberately not consulted here. A row can come from an
    approved export and still be malformed; those are separate dimensions in the
    replay result.
    """
    missing: list[str] = []
    invalid: list[str] = []
    values: dict[str, float] = {}

    raw_time = row.get("time")
    if raw_time is None:
        missing.append("time")
    elif at is None or (previous_at is not None and at < previous_at):
        invalid.append("time")

    for name, predicate in (
        ("pv_power_w", lambda value: value >= 0),
        ("battery_voltage", lambda value: value > 0),
        ("output_power_w", lambda value: value >= 0),
    ):
        raw = row.get(name)
        if raw is None:
            missing.append(name)
            continue
        value = _number(raw)
        if value is None or not predicate(value):
            invalid.append(name)
        else:
            values[name] = value

    raw_mode = row.get("working_mode")
    if raw_mode is None:
        missing.append("working_mode")
    elif not isinstance(raw_mode, str) or _mode(raw_mode) is None:
        invalid.append("working_mode")

    return tuple(missing), tuple(invalid), values


def replay_records(records: Iterable[Mapping[str, object]], *, baseline: InterlockBaseline | None = None,
                   source: ReplayLabel = ReplayLabel.UNVERIFIED_PROVENANCE) -> ReplaySummary:
    """Replay collection-time records in order without filling gaps or nulls.

    Each record is evaluated at its own collection time with every earlier record as
    history. Charger, policy, currents, sun times and PV context are not in the export
    and remain unknown, so results fail closed. Provenance is explicit: synthetic,
    imported-unverified and unverified-provenance sources are never promoted to
    VERIFIED_FROM_EXPORT; only an explicitly supplied VERIFIED_FROM_EXPORT or
    INDEPENDENTLY_VALIDATED source can produce that label for a complete row. Durable
    state is RECONSTRUCTED from the replayed observations themselves.
    """
    baseline = baseline or InterlockBaseline()
    source = ReplayLabel(source)
    rows = list(records)
    history: list[OutputTelemetry] = []
    state = DurableInterlockState(source=StateSource.RECONSTRUCTED)
    results: list[ReplayResult] = []
    counts: dict[str, int] = {}
    changes = across_unknown = 0
    previous_mode: str | None = None
    previous_at: datetime | None = None
    unknown_since_previous = False
    for index, row in enumerate(rows):
        at = _parse_time(row.get("time"))
        missing, invalid, values = _replay_measurements(row, at, previous_at)
        validity = (ReplayMeasurementValidity.INVALID if invalid
                    else ReplayMeasurementValidity.INCOMPLETE if missing
                    else ReplayMeasurementValidity.VALID)
        if source in (ReplayLabel.SIMULATED_SCENARIO, ReplayLabel.IMPORTED_UNVERIFIED,
                      ReplayLabel.UNVERIFIED_PROVENANCE):
            label = source
        elif validity is not ReplayMeasurementValidity.VALID:
            label = ReplayLabel.UNKNOWN_FROM_EXPORT
        else:
            label = source
        mode_label = row.get("working_mode") if isinstance(row.get("working_mode"), str) else None
        mode = _mode(mode_label)
        if mode is None:
            unknown_since_previous = True
        else:
            if previous_mode is not None and mode != previous_mode:
                if unknown_since_previous:
                    across_unknown += 1
                else:
                    changes += 1
            previous_mode, unknown_since_previous = mode, False
        if at is None:
            results.append(ReplayResult(index, label, _show(row.get("time")), missing, None,
                                        source, validity, invalid))
            continue
        telemetry = OutputTelemetry(observed_at=at, working_mode=mode_label, pv_power_w=values.get("pv_power_w"),
                                    battery_voltage=values.get("battery_voltage"),
                                    output_power_w=values.get("output_power_w"))
        decision = evaluate_interlock(InterlockSnapshot(telemetry=telemetry), baseline, tuple(history), state,
                                      evaluated_at=at)
        state = replace(decision.updated_state, source=StateSource.RECONSTRUCTED)
        history.append(telemetry)
        counts[decision.state.value] = counts.get(decision.state.value, 0) + 1
        results.append(ReplayResult(index, label, at.isoformat(), missing, decision,
                                    source, validity, invalid))
        if "time" not in invalid:
            previous_at = at
    return ReplaySummary(records=len(rows), evaluated=sum(1 for r in results if r.decision is not None),
                         observed_mode_changes=changes, mode_changes_across_unknown=across_unknown,
                         states=dict(sorted(counts.items())),
                         advise_count=sum(1 for r in results if r.decision and r.decision.decision is InterlockAction.ADVISE),
                         results=tuple(results))


__all__ = [
    "ChargePolicyProvenance", "ChargePolicyStatus", "ChargerObservation", "ChargerProvenance", "DurableInterlockState", "InterlockAction",
    "InterlockBaseline", "InterlockDecision", "InterlockReason", "InterlockSnapshot", "InterlockState", "KnownStatus",
    "PvContext", "ReplayLabel", "ReplayMeasurementValidity", "ReplayResult", "ReplaySummary", "ShadowProposal", "StateSource", "TransitionOrigin",
    "TrialPhase", "evaluate_interlock", "replay_records",
]
