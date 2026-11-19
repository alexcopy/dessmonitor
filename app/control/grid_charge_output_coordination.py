"""Pure, disconnected shadow coordination; reported settings are not commands."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from app.control.adaptive_output_policy import OutputTelemetry, _mode, evaluate_output_policy
from app.control.adaptive_telemetry import SAMPLE_GAP_SECONDS
from app.control.grid_output_interlock import (
    ChargerProvenance, InterlockAction, InterlockDecision, InterlockReason, InterlockSnapshot, InterlockState,
    evaluate_interlock,
)
from app.control.seasonal_output_strategy import ApprovedBands, EnergyInputs
from app.control.winter_energy_intelligence import (
    InterlockEvaluationInputs, WinterContext, WinterDecision, WinterPolicyConfig,
    WinterState, evaluate_winter_intelligence,
)


class CoordinationState(str, Enum):
    EMERGENCY_RECOVERY = "EMERGENCY_RECOVERY"
    FAULT_HOLD = "FAULT_HOLD"
    CHURN_LOCKOUT = "CHURN_LOCKOUT"
    GRID_HOLD = "GRID_HOLD"
    SOLAR_RETURN_CANDIDATE = "SOLAR_RETURN_CANDIDATE"
    SOLAR_OPERATION = "SOLAR_OPERATION"


@dataclass(frozen=True)
class ReceiptObservation:
    """One receipt and its separately supplied measurement; never infer device time."""

    snapshot: InterlockSnapshot
    received_at: datetime | None
    fingerprint: str | None


@dataclass(frozen=True)
class SettingObservation:
    value: str | None = None
    observed_at: datetime | None = None
    catalogue_verified: bool = False
    readback_verified: bool = False


@dataclass(frozen=True)
class CoordinationDecision:
    state: CoordinationState
    desired_profile: str
    observed_mode: str | None
    reported_priority: str | None
    observed_charger: str | None
    observed_at: datetime | None
    priority_evidence: SettingObservation
    reason_codes: tuple[str, ...]
    recovery_cycles: int
    continuity_uncertain: bool
    mode_changes: int
    lockout_expires_at: datetime | None
    interlock: InterlockDecision | None
    winter: WinterDecision | None
    voltage_candidate: str
    shadow_only: bool = True
    command_issued: bool = False
    hardware_write_attempted: bool = False
    physical_transition_proven: bool = False

    def __post_init__(self):
        if (self.state in (CoordinationState.SOLAR_RETURN_CANDIDATE, CoordinationState.SOLAR_OPERATION)
                or self.shadow_only is not True or self.command_issued is not False
                or self.hardware_write_attempted is not False or self.physical_transition_proven is not False
                or self.desired_profile != "Utility"):
            raise ValueError("coordination is advisory and disconnected")


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _cycles(observations, baseline, now):
    """Count complete observed sequences only; gaps, stale arrivals and duplicates break evidence.

    Ordering and spacing alone are not freshness: each measurement must already
    have been fresh when it was received (receipt minus measurement time within
    the baseline telemetry_max_age_seconds, inclusive, as Task 0076 applies to
    the current snapshot). A stale arrival is a continuity break, never a phase.
    """
    count, phase, uncertain, prior = 0, 0, False, None
    for item in observations:
        t, received = item.snapshot.telemetry.observed_at, item.received_at
        mode = _mode(item.snapshot.telemetry.working_mode)
        valid = (_aware(t) and _aware(received) and t <= received <= now
                 and (received - t).total_seconds() <= baseline.telemetry_max_age_seconds
                 and (now - t).total_seconds() <= baseline.mode_change_window_seconds
                 and mode is not None
                 and isinstance(item.fingerprint, str) and bool(item.fingerprint))
        if not valid:
            phase, prior, uncertain = 0, None, True
            continue
        if prior is not None:
            dt = (t - prior.snapshot.telemetry.observed_at).total_seconds()
            dr = (received - prior.received_at).total_seconds()
            if (dt <= 0 or dr <= 0 or dr > SAMPLE_GAP_SECONDS
                    or dt > baseline.mode_continuity_max_gap_seconds
                    or dt > baseline.max_telemetry_gap_seconds):
                phase, uncertain = 0, True
            elif item.fingerprint == prior.fingerprint:
                current_payload = replace(item.snapshot.telemetry, observed_at=None, received_at=None)
                previous_payload = replace(prior.snapshot.telemetry, observed_at=None, received_at=None)
                if (current_payload != previous_payload or item.snapshot.charger.mode != prior.snapshot.charger.mode
                        or item.snapshot.charger.provenance != prior.snapshot.charger.provenance):
                    phase, uncertain = 0, True
                prior = item
                continue
        if phase == 2 and mode == "line":
            count, phase = count + 1, 0
        elif phase == 1 and mode == "invert":
            phase = 2
        elif phase == 0:
            charger = item.snapshot.charger
            if (charger.mode == "solar_and_mains" and charger.provenance is ChargerProvenance.DEVICE_READBACK
                    and _aware(charger.observed_at) and 0 <= (received - charger.observed_at).total_seconds()
                    <= baseline.telemetry_max_age_seconds):
                phase = 1
        prior = item
    return count, uncertain


def evaluate_coordination(snapshot: OutputTelemetry, inputs: InterlockEvaluationInputs, *,
                          evaluated_at: datetime, receipt_history: tuple[ReceiptObservation, ...] = (),
                          priority: SettingObservation = SettingObservation(),
                          energy: EnergyInputs | None = None, bands: ApprovedBands | None = None,
                          winter_context: WinterContext = WinterContext(),
                          winter_config: WinterPolicyConfig | None = None,
                          reported_interlock: InterlockDecision | None = None,
                          reported_winter: WinterDecision | None = None,
                          manual_utility_hold: bool = False,
                          persistence_available: bool = False) -> CoordinationDecision:
    """Recompute existing authorities; supplied decisions are diagnostic only.

    Current Task 0078 cannot verify reserve. No positive promotion path is
    implemented; any future positive authority requires a separately approved change.
    """
    if type(snapshot) is not OutputTelemetry or type(inputs) is not InterlockEvaluationInputs:
        raise TypeError("snapshot and interlock inputs have invalid types")
    if not _aware(evaluated_at):
        raise ValueError("evaluated_at must be timezone-aware")
    if type(receipt_history) is not tuple or not all(type(x) is ReceiptObservation for x in receipt_history):
        raise TypeError("receipt_history must be a tuple of ReceiptObservation")
    if not all(type(x.snapshot) is InterlockSnapshot for x in receipt_history):
        raise TypeError("receipt history must contain interlock snapshots")
    if type(priority) is not SettingObservation or type(manual_utility_hold) is not bool or type(persistence_available) is not bool:
        raise TypeError("invalid coordination input")
    reasons = []
    base = inputs.baseline.output
    cycles, uncertain = _cycles(receipt_history, base, evaluated_at)
    interlock = winter = None
    setting_fresh = (_aware(priority.observed_at) and
                     0 <= (evaluated_at - priority.observed_at).total_seconds() <= base.telemetry_max_age_seconds)
    setting_valid = (priority.value in ("Utility", "Solar", "SBU") and setting_fresh
                     and priority.catalogue_verified is True and priority.readback_verified is True)
    if inputs.snapshot.output_priority_raw is not None and inputs.snapshot.output_priority_raw != priority.value:
        setting_valid = False
        reasons.append("OUTPUT_SETTING_CONTRADICTORY")
    if inputs.snapshot.telemetry is not snapshot:
        reasons.append("INTERLOCK_SNAPSHOT_MISMATCH")
    else:
        try:
            interlock = evaluate_interlock(inputs.snapshot, inputs.baseline, inputs.recent_history,
                                           inputs.durable_state, evaluated_at=evaluated_at)
        except (ValueError, TypeError, OverflowError):
            reasons.append("AUTHORITY_EVALUATION_UNAVAILABLE")
        else:
            # The winter authority is required: a failure or invalid result is FAULT_HOLD,
            # never a normal-looking GRID_HOLD. It cannot touch the charging controller;
            # the interlock result above (including emergency precedence) is kept.
            try:
                winter = evaluate_winter_intelligence(snapshot, base, inputs.recent_history,
                                                       evaluated_at=evaluated_at, energy=energy, bands=bands,
                                                       interlock=None, context=winter_context,
                                                       config=winter_config, interlock_inputs=inputs)
            except Exception:
                winter = None
            if type(winter) is not WinterDecision:
                winter = None
                reasons.extend(("AUTHORITY_EVALUATION_UNAVAILABLE", "WINTER_EVALUATION_UNAVAILABLE"))
    if reported_interlock is not None or reported_winter is not None:
        reasons.append("REPORTED_DECISIONS_DIAGNOSTIC_ONLY")
    if not setting_valid:
        reasons.append("OUTPUT_SETTING_UNVERIFIED")
    if not persistence_available:
        reasons.append("EVENT_PERSISTENCE_UNAVAILABLE")
    if uncertain:
        reasons.append("OBSERVATION_CONTINUITY_UNCERTAIN")
    if manual_utility_hold:
        reasons.append("MANUAL_UTILITY_HOLD")
    if interlock:
        reasons.extend(reason.value for reason in interlock.reason_codes)
    if winter:
        reasons.extend(winter.reasons)
    active = bool(interlock and interlock.state is InterlockState.GRID_RECOVERY_ACTIVE)
    if active and priority.value == "SBU" and setting_valid:
        reasons.append("GRID_CHARGING_ACTIVE_WITH_SBU")
    fault_reasons = {InterlockReason.STALE_TELEMETRY, InterlockReason.INCOMPLETE_TELEMETRY,
                     InterlockReason.FUTURE_TIMESTAMP, InterlockReason.CONTRADICTORY_TELEMETRY,
                     InterlockReason.INVALID_MEASUREMENT, InterlockReason.WORKING_MODE_UNKNOWN,
                     InterlockReason.INVALID_BASELINE, InterlockReason.DURABLE_STATE_UNAVAILABLE,
                     InterlockReason.DURABLE_STATE_UNRECONSTRUCTABLE, InterlockReason.CHARGER_STATE_UNVERIFIED,
                     InterlockReason.CHARGE_POLICY_UNAVAILABLE, InterlockReason.CHARGE_POLICY_METADATA_UNVERIFIED,
                     InterlockReason.CHARGE_POLICY_CONTRADICTORY, InterlockReason.CHARGER_POLICY_MISMATCH}
    if interlock and InterlockReason.EMERGENCY_RECOVERY_PRECEDENCE in interlock.reason_codes:
        state = CoordinationState.EMERGENCY_RECOVERY
    elif (interlock is None or winter is None or bool(fault_reasons.intersection(interlock.reason_codes))
          or not setting_valid
          or not persistence_available or uncertain):
        state = CoordinationState.FAULT_HOLD
    elif interlock.lockout_expires_at is not None and interlock.lockout_expires_at > evaluated_at:
        state = CoordinationState.CHURN_LOCKOUT
    else:
        state = CoordinationState.GRID_HOLD
    if winter and winter.state is WinterState.BATTERY_USE_ELIGIBLE and interlock and interlock.decision is InterlockAction.ADVISE:
        reasons.append("POSITIVE_AUTHORITY_REQUIRES_SEPARATE_APPROVAL")
    # Existing Task 0076 supplies lockout and mode-change semantics; no new thresholds.
    assessment = evaluate_output_policy(snapshot, base, inputs.recent_history, evaluated_at=evaluated_at)
    return CoordinationDecision(state, "Utility", snapshot.working_mode, priority.value,
                                inputs.snapshot.charger.mode, snapshot.observed_at, priority,
                                tuple(dict.fromkeys(reasons)), cycles, uncertain,
                                assessment.battery_eligibility.recent_mode_changes,
                                interlock.lockout_expires_at if interlock else None, interlock, winter,
                                "NOT_REQUIRED_WHILE_UTILITY" if setting_valid and priority.value == "Utility"
                                else "RESEARCH_ONLY_28.0V_NOT_EXECUTABLE")
