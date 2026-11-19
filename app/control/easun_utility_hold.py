"""One-way Utility protection for a verified Solar + Mains session.

This controller is deliberately disconnected from runtime startup. It never
touches BatteryChargeController and has no restore-to-SBU operation.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from app.control.adaptive_output_policy import OutputPolicyBaseline
from app.control.easun_output_mapping import OutputField, PRIORITY_KEYS, parse_reported, validate_catalogue_value
from app.control.grid_output_interlock import ChargerObservation, ChargerProvenance
from app.control.grid_charge_output_coordination import SettingObservation


SOLAR_AND_MAINS = "solar_and_mains"
UTILITY = "Utility"
SBU = "SBU"


@dataclass(frozen=True)
class UtilityHoldAuthorization:
    enabled: bool = False
    voltage_class: int | None = None
    model_verified: bool = False
    firmware_verified: bool = False
    catalogue_verified: bool = False
    backup_behavior_verified: bool = False
    rollback_acknowledged: bool = False
    device_identity_digest: str | None = None

    @property
    def ready(self) -> bool:
        return (self.enabled is True and self.voltage_class == 24
                and self.model_verified is True and self.firmware_verified is True
                and self.catalogue_verified is True and self.backup_behavior_verified is True
                and self.rollback_acknowledged is True
                and isinstance(self.device_identity_digest, str)
                and bool(self.device_identity_digest))


@dataclass(frozen=True)
class UtilityHoldResult:
    status: str
    reason: str
    command_id: str | None = None
    reported_priority: str | None = None
    observed_working_mode: str | None = None
    hardware_write_attempted: bool = False
    physical_transition_proven: bool = False


def _aware(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _fresh(value: object, now: datetime, limit: float) -> bool:
    stamp = _aware(value)
    if stamp is None:
        return False
    age = (now - stamp).total_seconds()
    return 0 <= age <= limit


def _catalogue_is_exact(catalogue: object) -> bool:
    if not isinstance(catalogue, dict) or catalogue.get("id") != OutputField.PRIORITY.value:
        return False
    items = catalogue.get("item")
    if not isinstance(items, list):
        return False
    pairs = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not isinstance(item.get("val"), str):
            return False
        pairs.append((item["val"], item["key"]))
    if len(pairs) != 3 or len(set(pairs)) != 3:
        return False
    return dict(pairs) == dict(PRIORITY_KEYS)


def _command_key(auth: UtilityHoldAuthorization, charger: ChargerObservation,
                 priority: SettingObservation, fingerprint: str, target_key: str) -> str:
    material = "|".join((
        "utility-hold/v1",
        auth.device_identity_digest or "",
        charger.observed_at.isoformat() if charger.observed_at else "",
        priority.observed_at.isoformat() if priority.observed_at else "",
        fingerprint,
        priority.value or "",
        target_key,
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class UtilityHoldCoordinator:
    """Execute at most one separately authorized Utility hold request."""

    def __init__(self, journal: Any, transport: Any, authorization: UtilityHoldAuthorization,
                 *, freshness_limit_seconds: float | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.journal = journal
        self.transport = transport
        self.authorization = authorization
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.freshness_limit_seconds = (OutputPolicyBaseline().telemetry_max_age_seconds
                                        if freshness_limit_seconds is None else freshness_limit_seconds)
        if isinstance(self.freshness_limit_seconds, bool) or not isinstance(self.freshness_limit_seconds, (int, float)) \
                or self.freshness_limit_seconds < 0:
            raise ValueError("freshness_limit_seconds must be non-negative")

    async def _record_uncertain_reconciliation(self, command_id: str, prefix: str) -> None:
        observed = prefix + "_UNAVAILABLE"
        try:
            after = await self.transport.read_setting()
            value = parse_reported(OutputField.PRIORITY, after.value)
            check_now = _aware(self.clock())
            if check_now is not None and _fresh(after.received_at, check_now, self.freshness_limit_seconds):
                observed = prefix + "_" + value.upper()
            else:
                observed = prefix + "_STALE"
        except asyncio.CancelledError:
            observed = prefix + "_READ_CANCELLED"
        except BaseException:
            pass
        try:
            await self.journal.record_observation(str(uuid4()), command_id,
                                                  "POST_WRITE_READBACK", observed)
        except BaseException:
            # The intent remains unresolved when reconciliation cannot be
            # appended; never convert an uncertain outcome into success.
            pass

    async def hold(self, charger: ChargerObservation, priority: SettingObservation, *,
                   evaluated_at: datetime, observation_fingerprint: str,
                   working_mode: str | None = None, manual_override: bool = False) -> UtilityHoldResult:
        now = _aware(evaluated_at)
        if now is None:
            return UtilityHoldResult("REFUSED", "EVALUATION_TIME_INVALID", observed_working_mode=working_mode)
        if self.authorization.ready is not True:
            return UtilityHoldResult("REFUSED", "LIVE_AUTHORIZATION_INCOMPLETE", observed_working_mode=working_mode)
        if manual_override is not False:
            return UtilityHoldResult("REFUSED", "MANUAL_OVERRIDE", observed_working_mode=working_mode)
        if not isinstance(observation_fingerprint, str) or not observation_fingerprint:
            return UtilityHoldResult("REFUSED", "OBSERVATION_FINGERPRINT_UNAVAILABLE", observed_working_mode=working_mode)
        if (type(charger) is not ChargerObservation or charger.mode != SOLAR_AND_MAINS
                or charger.provenance is not ChargerProvenance.DEVICE_READBACK
                or not _fresh(charger.observed_at, now, self.freshness_limit_seconds)):
            return UtilityHoldResult("REFUSED", "CHARGER_READBACK_UNVERIFIED", observed_working_mode=working_mode)
        if (type(priority) is not SettingObservation or priority.value not in (SBU, UTILITY)
                or priority.catalogue_verified is not True or priority.readback_verified is not True
                or not _fresh(priority.observed_at, now, self.freshness_limit_seconds)):
            return UtilityHoldResult("REFUSED", "OUTPUT_READBACK_UNVERIFIED", observed_working_mode=working_mode)

        try:
            catalogue_read = await self.transport.read_catalogue()
            catalogue = getattr(catalogue_read, "catalogue", None)
            catalogue_at = getattr(catalogue_read, "received_at", None)
            catalogue_check_now = _aware(self.clock())
            if (catalogue_check_now is None or not _catalogue_is_exact(catalogue)
                    or not _fresh(catalogue_at, catalogue_check_now, self.freshness_limit_seconds)):
                return UtilityHoldResult("REFUSED", "CATALOGUE_MAPPING_UNVERIFIED",
                                         reported_priority=priority.value, observed_working_mode=working_mode)
            target_key = validate_catalogue_value(OutputField.PRIORITY, UTILITY, catalogue,
                                                  installation_voltage_class=24)
            current_read = await self.transport.read_setting()
            current = parse_reported(OutputField.PRIORITY, current_read.value)
            current_check_now = _aware(self.clock())
            if current_check_now is None or not _fresh(current_read.received_at, current_check_now,
                                                       self.freshness_limit_seconds):
                return UtilityHoldResult("REFUSED", "PREWRITE_READBACK_STALE",
                                         reported_priority=current, observed_working_mode=working_mode)
            if current != priority.value:
                # A completed command may have changed the setting since the
                # gated observation. Resolve the stable key before treating
                # that changed readback as a new race, so a repeated logical
                # event cannot issue a second write.
                command_id = _command_key(self.authorization, charger, priority,
                                          observation_fingerprint, target_key)
                acquired = False
                try:
                    if await self.journal.acquire():
                        acquired = True
                        await self.journal.available()
                        prior = await self.journal.find_by_command_key(command_id)
                        if prior and prior.get("ambiguous"):
                            return UtilityHoldResult("REFUSED", "IDEMPOTENCY_KEY_AMBIGUOUS", command_id,
                                                     current, working_mode)
                        if prior is not None:
                            if prior.get("status") == "VERIFIED":
                                return UtilityHoldResult("VERIFIED", "DUPLICATE_ALREADY_VERIFIED", command_id,
                                                         UTILITY, working_mode)
                            return UtilityHoldResult("REFUSED", "IDEMPOTENCY_NONRETRYABLE_" + str(prior.get("status")),
                                                     command_id, current, working_mode)
                finally:
                    if acquired:
                        try:
                            await self.journal.release()
                        except Exception:
                            pass
                return UtilityHoldResult("REFUSED", "CURRENT_VALUE_CHANGED",
                                         reported_priority=current, observed_working_mode=working_mode)
            if current == UTILITY:
                return UtilityHoldResult("NOOP", "ALREADY_UTILITY", reported_priority=current,
                                         observed_working_mode=working_mode)
        except Exception:
            return UtilityHoldResult("REFUSED", "READ_VALIDATION_FAILED", reported_priority=priority.value,
                                     observed_working_mode=working_mode)

        command_id = _command_key(self.authorization, charger, priority, observation_fingerprint, target_key)
        acquired = False
        write_attempted = False
        try:
            if not await self.journal.acquire():
                return UtilityHoldResult("REFUSED", "LOCK_UNAVAILABLE", command_id, priority.value, working_mode)
            acquired = True
            await self.journal.available()
            prior = await self.journal.find_by_command_key(command_id)
            if prior and prior.get("ambiguous"):
                return UtilityHoldResult("REFUSED", "IDEMPOTENCY_KEY_AMBIGUOUS", command_id, priority.value, working_mode)
            if prior is not None:
                status = prior.get("status")
                if status == "VERIFIED":
                    return UtilityHoldResult("VERIFIED", "DUPLICATE_ALREADY_VERIFIED", command_id, UTILITY, working_mode)
                return UtilityHoldResult("REFUSED", "IDEMPOTENCY_NONRETRYABLE_" + str(status), command_id,
                                         priority.value, working_mode)
            if await self.journal.unresolved():
                return UtilityHoldResult("REFUSED", "UNRESOLVED_INTENT", command_id, priority.value, working_mode)
            race_read = await self.transport.read_setting()
            race_value = parse_reported(OutputField.PRIORITY, race_read.value)
            race_check_now = _aware(self.clock())
            if (race_check_now is None or not _fresh(race_read.received_at, race_check_now,
                                                     self.freshness_limit_seconds)):
                return UtilityHoldResult("REFUSED", "PREWRITE_RACE_READBACK_UNAVAILABLE", command_id,
                                         race_value, working_mode)
            if race_value != priority.value or race_value != SBU:
                return UtilityHoldResult("REFUSED", "PREWRITE_RACE", command_id,
                                         race_value, working_mode)
            await self.journal.intent(command_id, OutputField.PRIORITY.value, priority.value, UTILITY, command_id)
            await self.journal.record_observation(str(uuid4()), command_id, "WRITE_ATTEMPT", target_key)
            try:
                await self.transport.write_output_priority(target_key)
                write_attempted = True
            except asyncio.CancelledError:
                write_attempted = True
                await asyncio.shield(self._record_uncertain_reconciliation(command_id, "CANCELLED"))
                raise
            except BaseException:
                write_attempted = True
                await asyncio.shield(self._record_uncertain_reconciliation(command_id, "UNCERTAIN"))
                return UtilityHoldResult("UNKNOWN", "WRITE_OUTCOME_UNCERTAIN", command_id,
                                         priority.value, working_mode, write_attempted, False)
            cancelled_during_readback = False
            try:
                after = await self.transport.read_setting()
                after_value = parse_reported(OutputField.PRIORITY, after.value)
                post_readback_check_now = _aware(self.clock())
                readback_fresh = (post_readback_check_now is not None
                                  and _fresh(after.received_at, post_readback_check_now,
                                             self.freshness_limit_seconds))
                readback_value = after_value if readback_fresh else "STALE"
            except asyncio.CancelledError:
                cancelled_during_readback = True
                after_value, readback_value, readback_fresh = None, "CANCELLED", False
            except BaseException:
                after_value, readback_value, readback_fresh = None, "UNAVAILABLE", False
            await self.journal.record_observation(str(uuid4()), command_id, "POST_WRITE_READBACK", str(readback_value))
            if cancelled_during_readback:
                raise asyncio.CancelledError
            if readback_fresh and after_value == UTILITY:
                await self.journal.finish(command_id, "VERIFIED")
                return UtilityHoldResult("VERIFIED", "REPORTED_SETTING_ONLY", command_id, after_value,
                                         working_mode, write_attempted, False)
            await self.journal.finish(command_id, "MISMATCH" if readback_fresh else "UNKNOWN")
            return UtilityHoldResult("MISMATCH" if readback_fresh else "UNKNOWN",
                                     "POST_WRITE_READBACK_MISMATCH" if readback_fresh else "POST_WRITE_READBACK_UNAVAILABLE",
                                     command_id, after_value, working_mode, write_attempted, False)
        except asyncio.CancelledError:
            raise
        except BaseException:
            # Leave INTENT unresolved if audit finalization itself failed.
            return UtilityHoldResult("UNKNOWN", "WRITE_OR_AUDIT_UNCERTAIN", command_id,
                                     priority.value, working_mode, write_attempted, False)
        finally:
            if acquired:
                try:
                    await self.journal.release()
                except Exception:
                    pass


__all__ = ["UtilityHoldAuthorization", "UtilityHoldCoordinator", "UtilityHoldResult"]
