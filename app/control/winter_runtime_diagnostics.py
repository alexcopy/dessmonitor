"""Read-only runtime adapter for the committed Task 0078 winter evaluator.

The adapter is fed only from the successful DESS observation path in run.py.
It builds one OutputTelemetry object per response, binds the Task 0076
interlock recomputation to that exact object and calls the committed
``evaluate_winter_intelligence``. It never accepts reserve, verification,
source or interlock claims from callers or web input, and it has no write,
command or hardware path. No runtime result can be ``BATTERY_USE_ELIGIBLE``:
no trusted BMS/shunt reserve integration exists.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from app.control.adaptive_output_policy import (
    OutputPolicyBaseline,
    OutputTelemetry,
    evaluate_output_policy,
)
from app.control.adaptive_telemetry import validate_sun_times
from app.control.grid_output_interlock import (
    ChargePolicyProvenance,
    ChargePolicyStatus,
    ChargerObservation,
    ChargerProvenance,
    DurableInterlockState,
    InterlockBaseline,
    InterlockSnapshot,
    PvContext,
    StateSource,
    evaluate_interlock,
)
from app.control.grid_charge_output_coordination import (
    ReceiptObservation,
    SettingObservation,
    evaluate_coordination,
)
from app.control.coordinated_charger_output_priority import evaluate_coordinated_control
from app.control.winter_energy_intelligence import (
    InterlockEvaluationInputs,
    WinterContext,
    WinterState,
    evaluate_winter_intelligence,
)

UNAVAILABLE = "UNAVAILABLE"
RESERVE_QUALITY_UNVERIFIED = "UNAVAILABLE_UNVERIFIED"
RUNTIME_RESERVE_REASON = "RUNTIME_TRUSTED_RESERVE_INTEGRATION_ABSENT"
DEADLOCK_REASON = "POST_RECOVERY_EVIDENCE_DEADLOCK"
EVALUATION_FAILED_REASON = "DIAGNOSTIC_EVALUATION_FAILED"
HISTORICAL_LABEL = "HISTORICAL_NOT_CURRENT"
# Fields copied into the labelled historical block after a failed evaluation.
_HISTORICAL_FIELDS = (
    "decision", "profile", "solar_opportunity", "charging_context",
    "charger_priority_reported", "charge_current_a_reported", "reserve_evidence_quality",
    "interlock_decision", "interlock_state", "received_at", "last_successful_evaluation",
)
_PERSISTENCE_STATES = frozenset({"available", "degraded", "unavailable", "disabled"})
_COORDINATION_HISTORY_SIZE = 256


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and number not in (float("inf"), float("-inf")) else None


_IDENTITY_FIELDS = (
    "working_state", "output_power", "pv_total_power", "battery_voltage",
    "battery_charging_current", "battery_discharging_current", "battery_capacity",
    "charger_priority", "output_priority",
)


def _response_identity(data: object) -> tuple[object, ...]:
    """Canonical identity for the response fields consumed by diagnostics."""
    result: list[object] = []
    for name in _IDENTITY_FIELDS:
        value = getattr(data, name, None)
        result.append(_number(value) if name not in ("working_state", "charger_priority", "output_priority")
                      else value if isinstance(value, str) else None)
    return tuple(result)


@dataclass(frozen=True)
class WinterDiagnosticSnapshot:
    """Display-only Task 0078 result. Not a control proposal or eligibility input."""

    status: str
    decision: str
    profile: str
    solar_opportunity: str
    charging_context: str
    charger_priority_reported: str | None
    charge_current_a_reported: float | None
    reserve_evidence_quality: str
    interlock_decision: str
    interlock_state: str
    interlock_reasons: tuple[str, ...]
    reasons: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    post_recovery_deadlock: bool
    freshness: str
    received_at: str | None
    receipt_age_seconds: float | None
    measurement_time: str | None
    timestamp_quality: str
    last_successful_evaluation: str | None
    last_failure_at: str | None
    diagnostic_failure: str | None
    failure_count: int
    persistence_status: str
    telemetry: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    # Only set when the current evaluation failed: the last successful result,
    # labelled HISTORICAL_NOT_CURRENT with its original receipt/evaluation times.
    historical_last_success: dict[str, Any] | None = None
    shadow_only: bool = True
    command_issued: bool = False
    hardware_write_path: bool = False
    coordination: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for name in ("interlock_reasons", "reasons", "missing_requirements"):
            result[name] = list(result[name])
        result["telemetry"] = dict(self.telemetry)
        result["historical_last_success"] = (dict(self.historical_last_success)
                                              if self.historical_last_success is not None else None)
        return result


@dataclass(frozen=True)
class RuntimeWinterInputs:
    """Exact objects passed to the committed evaluator for one response."""

    snapshot: OutputTelemetry
    interlock_inputs: InterlockEvaluationInputs
    context: WinterContext
    history: tuple[OutputTelemetry, ...]
    source_fingerprint: str | None = None
    source_identity: tuple[object, ...] = ()


class WinterRuntimeDiagnostics:
    """Evaluate Task 0078 from the current DESS observation, never from web input."""

    def __init__(self, baseline: OutputPolicyBaseline | None = None,
                 history_provider: Callable[[], Any] | None = None):
        self.baseline = baseline if type(baseline) is OutputPolicyBaseline else OutputPolicyBaseline()
        self.history_provider = history_provider
        self.persistence_status = "disabled"
        self.failure_count = 0
        # Receipt time of the data the current result actually represents;
        # None whenever the current result holds no evaluated data.
        self._received_at: datetime | None = None
        self._last_success: WinterDiagnosticSnapshot | None = None
        self._pending_inputs: RuntimeWinterInputs | None = None
        self._coordination_history: deque[ReceiptObservation] = deque(maxlen=_COORDINATION_HISTORY_SIZE)
        self._latest = WinterDiagnosticSnapshot(
            status="no_observation",
            decision=UNAVAILABLE,
            profile=UNAVAILABLE,
            solar_opportunity=UNAVAILABLE,
            charging_context=UNAVAILABLE,
            charger_priority_reported=None,
            charge_current_a_reported=None,
            reserve_evidence_quality=RESERVE_QUALITY_UNVERIFIED,
            interlock_decision=UNAVAILABLE,
            interlock_state=UNAVAILABLE,
            interlock_reasons=(),
            reasons=("NO_SUCCESSFUL_DESS_OBSERVATION", RUNTIME_RESERVE_REASON),
            missing_requirements=("current DESS observation",
                                  "independently verified BMS/shunt usable reserve"),
            post_recovery_deadlock=False,
            freshness=UNAVAILABLE,
            received_at=None,
            receipt_age_seconds=None,
            measurement_time=None,
            timestamp_quality="device_time_unverified",
            last_successful_evaluation=None,
            last_failure_at=None,
            diagnostic_failure=None,
            failure_count=0,
            persistence_status=self.persistence_status,
            coordination={
                "state": UNAVAILABLE,
                "shadow_only": True,
                "command_issued": False,
                "hardware_write_attempted": False,
                "physical_transition_proven": False,
                "reasons": ["NO_COORDINATION_EVALUATION"],
            },
        )

    # -- inputs -----------------------------------------------------------

    def _history(self) -> tuple[OutputTelemetry, ...]:
        if self.history_provider is None:
            return ()
        return tuple(item for item in self.history_provider() if type(item) is OutputTelemetry)

    def build_inputs(self, data: Any, *, received_at: datetime, sun_times: object | None,
                     evaluated_at: datetime) -> RuntimeWinterInputs:
        """Build evaluator inputs. Device measurement time is never trusted here."""
        now = _utc(evaluated_at)
        received = _utc(received_at)
        if now is None or received is None:
            raise ValueError("received_at and evaluated_at must be timezone-aware")
        sun = validate_sun_times(sun_times, now, self.baseline.forecast_max_age_seconds)
        sunrise, sunset = (sun[1], sun[2]) if sun else (None, None)
        daylight_remaining = None
        if sun is not None:
            daylight_remaining = max(0.0, (sunset - now).total_seconds() / 3600.0)
        pv_context = PvContext(timezone_verified=sun is not None)
        snapshot = OutputTelemetry(
            observed_at=None,  # DESS timestamp/gts/device_observed_at are unverified
            received_at=received,
            working_mode=getattr(data, "working_state", None),
            output_power_w=_number(getattr(data, "output_power", None)),
            pv_power_w=_number(getattr(data, "pv_total_power", None)),
            battery_voltage=_number(getattr(data, "battery_voltage", None)),
            battery_current_chg=_number(getattr(data, "battery_charging_current", None)),
            battery_current_dis=_number(getattr(data, "battery_discharging_current", None)),
            battery_soc=_number(getattr(data, "battery_capacity", None)),
            sunrise=sunrise,
            sunset=sunset,
        )
        history = self._history()
        priority = getattr(data, "output_priority", None)
        interlock_inputs = InterlockEvaluationInputs(
            snapshot=InterlockSnapshot(telemetry=snapshot, pv_context=pv_context,
                                       output_priority_raw=priority if isinstance(priority, str) else None),
            baseline=InterlockBaseline(output=self.baseline),
            recent_history=history,
            durable_state=DurableInterlockState(source=StateSource.UNAVAILABLE, recovery_in_progress=None),
        )
        context = WinterContext(
            month=now.month,
            daylight_remaining_hours=daylight_remaining,
            measured_pv_observed_at=None,
            measured_pv_provenance=None,
            forecast_independently_verified=False,
            reserve_provenance=None,
            independent_reserve_evidence=None,
            pv_context=pv_context,
        )
        return RuntimeWinterInputs(snapshot, interlock_inputs, context, history,
                                   source_identity=_response_identity(data))

    # -- evaluation -------------------------------------------------------

    def observe(self, data: Any, *, received_at: datetime, sun_times: object | None = None,
                evaluated_at: datetime | None = None,
                fingerprint: str | None = None) -> WinterDiagnosticSnapshot:
        now = _utc(evaluated_at) or datetime.now(timezone.utc)
        try:
            inputs = self.build_inputs(data, received_at=received_at, sun_times=sun_times, evaluated_at=now)
            self._pending_inputs = replace(
                inputs,
                source_fingerprint=fingerprint if isinstance(fingerprint, str) else None,
            )
            inputs = self._pending_inputs
            result = evaluate_winter_intelligence(
                inputs.snapshot, self.baseline, inputs.history, evaluated_at=now,
                energy=None, bands=None, interlock=None, context=inputs.context,
                interlock_inputs=inputs.interlock_inputs,
            )
            interlock = evaluate_interlock(
                inputs.interlock_inputs.snapshot, inputs.interlock_inputs.baseline,
                inputs.interlock_inputs.recent_history, inputs.interlock_inputs.durable_state,
                evaluated_at=now,
            )
            policy = evaluate_output_policy(inputs.snapshot, self.baseline, inputs.history, evaluated_at=now)
            reasons = list(result.reasons)
            decision = result.state.value
            if result.state is WinterState.BATTERY_USE_ELIGIBLE:
                # Unreachable with the committed evaluator; kept as a final fail-closed guard.
                decision = WinterState.RECOVERY_OR_FAULT_HOLD.value
            reasons.append(RUNTIME_RESERVE_REASON)
            reserve = next((item for item in result.evidence if item.name == "usable_battery_wh"), None)
            reserve_quality = RESERVE_QUALITY_UNVERIFIED
            if reserve is not None:
                reserve_quality = "%s_UNVERIFIED" % reserve.measurement_validity.value
            charge_a = inputs.snapshot.battery_current_chg
            if charge_a is None:
                charging = "UNKNOWN"
            elif charge_a > 0:
                charging = "CHARGING_REPORTED"
            else:
                charging = "NOT_CHARGING_REPORTED"
            charger = getattr(data, "charger_priority", None)
            self._received_at = inputs.snapshot.received_at
            self._latest = WinterDiagnosticSnapshot(
                status="ok",
                decision=decision,
                profile=result.profile.value,
                solar_opportunity=policy.solar_opportunity.classification.value,
                charging_context=charging,
                charger_priority_reported=charger if isinstance(charger, str) else None,
                charge_current_a_reported=charge_a,
                reserve_evidence_quality=reserve_quality,
                interlock_decision=interlock.decision.value,
                interlock_state=interlock.state.value,
                interlock_reasons=tuple(reason.value for reason in interlock.reason_codes),
                reasons=tuple(dict.fromkeys(reasons)),
                missing_requirements=tuple(result.missing_requirements),
                post_recovery_deadlock=DEADLOCK_REASON in reasons,
                freshness=UNAVAILABLE,
                received_at=self._received_at.isoformat(),
                receipt_age_seconds=None,
                measurement_time=None,
                timestamp_quality="device_time_unverified",
                last_successful_evaluation=now.isoformat(),
                last_failure_at=self._latest.last_failure_at,
                diagnostic_failure=None,
                failure_count=self.failure_count,
                persistence_status=self.persistence_status,
                explanation=result.explanation,
                coordination={
                    "state": UNAVAILABLE,
                    "shadow_only": True,
                    "command_issued": False,
                    "hardware_write_attempted": False,
                    "physical_transition_proven": False,
                    "reasons": ["COORDINATION_PENDING_POST_CHARGE_STATUS"],
                },
            )
            self._last_success = self._latest
        except Exception as exc:
            self.failure_count += 1
            self._latest = self._failed(now, exc, received_at)
        return self._latest

    def _failed(self, now: datetime, exc: Exception, received_at: object) -> WinterDiagnosticSnapshot:
        """Result for a response whose evaluation failed.

        Nothing derived from an earlier response is presented as current: every
        decision/solar/charging/reserve/interlock field is UNAVAILABLE, there is
        no represented receipt (so no receipt freshness), and the previous
        successful values appear only in ``historical_last_success`` with
        their original timestamps.
        """
        self._received_at = None
        failed_receipt = _utc(received_at)
        historical = None
        if self._last_success is not None:
            historical = {name: getattr(self._last_success, name) for name in _HISTORICAL_FIELDS}
            historical["label"] = HISTORICAL_LABEL
        return WinterDiagnosticSnapshot(
            status="evaluation_failed",
            decision=UNAVAILABLE,
            profile=UNAVAILABLE,
            solar_opportunity=UNAVAILABLE,
            charging_context=UNAVAILABLE,
            charger_priority_reported=None,
            charge_current_a_reported=None,
            reserve_evidence_quality=RESERVE_QUALITY_UNVERIFIED,
            interlock_decision=UNAVAILABLE,
            interlock_state=UNAVAILABLE,
            interlock_reasons=(),
            reasons=(EVALUATION_FAILED_REASON, RUNTIME_RESERVE_REASON),
            missing_requirements=("successful diagnostic evaluation of the current DESS observation",
                                  "independently verified BMS/shunt usable reserve"),
            post_recovery_deadlock=False,
            freshness=UNAVAILABLE,
            received_at=None,
            receipt_age_seconds=None,
            measurement_time=None,
            timestamp_quality="device_time_unverified",
            last_successful_evaluation=(self._last_success.last_successful_evaluation
                                        if self._last_success is not None else None),
            last_failure_at=now.isoformat(),
            diagnostic_failure=type(exc).__name__,
            failure_count=self.failure_count,
            persistence_status=self.persistence_status,
            explanation=("Evaluation of the latest DESS response failed%s; no current winter diagnostic "
                         "is available." % (" (receipt %s)" % failed_receipt.isoformat() if failed_receipt else "")),
            historical_last_success=historical,
            coordination={
                "state": UNAVAILABLE,
                "shadow_only": True,
                "command_issued": False,
                "hardware_write_attempted": False,
                "physical_transition_proven": False,
                "reasons": ["COORDINATION_UNAVAILABLE_AFTER_EVALUATION_FAILURE"],
            },
        )

    @staticmethod
    def _charger_mode(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = "_".join(value.strip().lower().split())
        return {
            "solar_only": "solar_only",
            "only_solar": "solar_only",
            "solar_and_mains": "solar_and_mains",
            "solar_and_utility": "solar_and_mains",
            "solar_priority": "solar_priority",
            "solar_first": "solar_priority",
        }.get(normalized)

    @staticmethod
    def _charge_policy_status(value: object) -> ChargePolicyStatus | None:
        """Adapt shared_state status without promoting it to verified evidence."""
        if not isinstance(value, dict):
            return None
        state = value.get("state") if isinstance(value.get("state"), str) else None
        emergency = value.get("emergency_active") if type(value.get("emergency_active")) is bool else None
        desired = value.get("desired_charger_mode")
        if not isinstance(desired, str):
            desired = value.get("desired_mode") if isinstance(value.get("desired_mode"), str) else None
        try:
            return ChargePolicyStatus(
                state=state,
                emergency_active=emergency,
                desired_charger_mode=desired,
                evaluated_at=None,
                provenance=ChargePolicyProvenance.RUNTIME_STATUS,
                verified=False,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coordination_payload(decision: object, received_at: datetime) -> dict[str, Any]:
        interlock = decision.interlock
        charger = interlock and getattr(interlock, "observed_charger_mode", None)
        reasons = list(decision.reason_codes)
        return {
            "state": decision.state.value,
            "desired_profile": decision.desired_profile,
            "reported_output_priority": decision.reported_priority,
            "output_priority_quality": "verified_readback" if decision.priority_evidence.readback_verified else "unverified",
            "charger_state": charger,
            "charger_provenance": (interlock.charger_provenance.value if interlock is not None else "UNKNOWN"),
            "charge_policy_state": None,
            "charge_policy_provenance": "UNKNOWN",
            "charge_policy_verified": False,
            "conflict": bool({"OUTPUT_SETTING_CONTRADICTORY", "GRID_CHARGING_ACTIVE_WITH_SBU"}
                              .intersection(reasons)),
            "reasons": reasons,
            "recovery_cycles": decision.recovery_cycles,
            "continuity_uncertain": decision.continuity_uncertain,
            "mode_changes": decision.mode_changes,
            "lockout_expires_at": (decision.lockout_expires_at.isoformat()
                                    if decision.lockout_expires_at is not None else None),
            "received_at": received_at.isoformat(),
            "shadow_only": decision.shadow_only,
            "command_issued": decision.command_issued,
            "hardware_write_attempted": decision.hardware_write_attempted,
            "physical_transition_proven": decision.physical_transition_proven,
        }

    def observe_coordination(self, data: Any, *, received_at: datetime,
                             evaluated_at: datetime | None = None,
                             fingerprint: str | None = None,
                             policy_status: object = None) -> WinterDiagnosticSnapshot:
        """Evaluate Task 0080 after charging status publication for this response."""
        now = _utc(evaluated_at) or datetime.now(timezone.utc)
        received = _utc(received_at)
        pending = self._pending_inputs
        if (received is None or pending is None or pending.snapshot.received_at != received
                or not isinstance(fingerprint, str)
                or fingerprint != pending.source_fingerprint
                or _response_identity(data) != pending.source_identity):
            self._latest = replace(self._latest, coordination={
                "state": "FAULT_HOLD",
                "shadow_only": True,
                "command_issued": False,
                "hardware_write_attempted": False,
                "physical_transition_proven": False,
                "reasons": ["COORDINATION_OBSERVATION_IDENTITY_UNAVAILABLE"],
            })
            return self._latest
        charger = ChargerObservation(
            mode=self._charger_mode(getattr(data, "charger_priority", None)),
            observed_at=received,
            provenance=ChargerProvenance.UNKNOWN,
        )
        policy = self._charge_policy_status(policy_status)
        interlock_snapshot = replace(pending.interlock_inputs.snapshot, charger=charger, policy=policy)
        interlock_inputs = replace(pending.interlock_inputs, snapshot=interlock_snapshot)
        receipt = ReceiptObservation(snapshot=interlock_snapshot, received_at=received,
                                     fingerprint=fingerprint if isinstance(fingerprint, str) else None)
        self._coordination_history.append(receipt)
        priority = SettingObservation(
            value=(getattr(data, "output_priority", None)
                   if isinstance(getattr(data, "output_priority", None), str) else None),
            observed_at=received,
            catalogue_verified=False,
            readback_verified=False,
        )
        try:
            decision = evaluate_coordination(
                pending.snapshot,
                interlock_inputs,
                evaluated_at=now,
                receipt_history=tuple(self._coordination_history),
                priority=priority,
                persistence_available=False,
            )
            control = evaluate_coordinated_control(
                decision,
                charger=charger,
                priority=priority,
                evaluated_at=now,
                emergency_recovery=bool(policy is not None and policy.emergency_active is True),
            )
            payload = self._coordination_payload(decision, received)
            payload.update({
                "control_state": control.state.value,
                "utility_hold_requested": control.utility_hold_requested,
                "return_blocked": control.return_blocked,
                "control_reasons": list(control.reason_codes),
                "control_shadow_only": control.shadow_only,
                "control_command_issued": control.command_issued,
                "control_hardware_write_attempted": control.hardware_write_attempted,
                "control_physical_transition_proven": control.physical_transition_proven,
            })
            if policy is not None:
                payload["charge_policy_state"] = policy.state
                payload["charge_policy_provenance"] = policy.provenance.value
                payload["charge_policy_verified"] = policy.verified
            self._latest = replace(self._latest, coordination=payload)
        except Exception as exc:
            self._latest = replace(self._latest, coordination={
                "state": "FAULT_HOLD",
                "shadow_only": True,
                "command_issued": False,
                "hardware_write_attempted": False,
                "physical_transition_proven": False,
                "reasons": ["COORDINATION_EVALUATION_FAILED", type(exc).__name__],
            })
        return self._latest

    def set_persistence_status(self, status: str) -> None:
        self.persistence_status = status if status in _PERSISTENCE_STATES else "degraded"

    def snapshot(self, now: datetime | None = None,
                 telemetry: dict[str, Any] | None = None) -> WinterDiagnosticSnapshot:
        """Return the latest result with receipt freshness computed at read time."""
        current = _utc(now) or datetime.now(timezone.utc)
        freshness, age = UNAVAILABLE, None
        # Receipt freshness describes the evaluated data only; a failed or
        # absent evaluation has none, whatever DESS responses arrived since.
        if self._latest.status == "ok" and self._received_at is not None:
            age = round((current - self._received_at).total_seconds(), 1)
            if age < 0:
                freshness = "RECEIPT_IN_FUTURE"
            elif age <= self.baseline.telemetry_max_age_seconds:
                freshness = "RECEIPT_CURRENT_MEASUREMENT_UNVERIFIED"
            else:
                freshness = "RECEIPT_STALE"
        return replace(self._latest, freshness=freshness, receipt_age_seconds=age,
                       persistence_status=self.persistence_status,
                       telemetry=dict(telemetry or {}))


__all__ = ["RuntimeWinterInputs", "WinterDiagnosticSnapshot", "WinterRuntimeDiagnostics"]
