"""Runtime adapter for the existing, audited Utility-hold executor."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timezone
from typing import Any

from app.control.easun_live_output_transport import LiveDessTransport
from app.control.easun_output_mapping import OutputField, parse_reported
from app.control.easun_utility_hold import UtilityHoldAuthorization, UtilityHoldCoordinator
from app.control.grid_charge_output_coordination import SettingObservation
from app.control.grid_output_interlock import ChargerObservation, ChargerProvenance
from app.service.battery_charge_controller import charger_mode


def _true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def build_authorization(api: Any) -> UtilityHoldAuthorization:
    identity_values = tuple(getattr(api, name, None) for name in ("pn", "dev_code", "dev_addr", "sn"))
    identity = "|".join(str(value) for value in identity_values)
    digest = (hashlib.sha256(identity.encode("utf-8")).hexdigest()
              if all(value not in (None, "") and not isinstance(value, bool) for value in identity_values)
              else None)
    voltage = os.getenv("DESS_UTILITY_HOLD_VOLTAGE_CLASS")
    return UtilityHoldAuthorization(
        enabled=_true("DESS_UTILITY_HOLD_ENABLED"),
        voltage_class=int(voltage) if voltage and voltage.isdigit() else None,
        model_verified=_true("DESS_UTILITY_HOLD_MODEL_VERIFIED"),
        firmware_verified=_true("DESS_UTILITY_HOLD_FIRMWARE_VERIFIED"),
        catalogue_verified=_true("DESS_UTILITY_HOLD_CATALOGUE_VERIFIED"),
        backup_behavior_verified=_true("DESS_UTILITY_HOLD_BACKUP_VERIFIED"),
        rollback_acknowledged=_true("DESS_UTILITY_HOLD_ROLLBACK_ACKNOWLEDGED"),
        device_identity_digest=digest,
    )


class RuntimeUtilityHold:
    """Read settings once per inverter observation and execute at most one hold."""

    def __init__(self, api: Any, journal: Any, authorization: UtilityHoldAuthorization):
        self.transport = LiveDessTransport(api, enabled=authorization.ready)
        self.executor = UtilityHoldCoordinator(journal, self.transport, authorization)

    async def tick(self, diagnostics: Any, data: Any, *, received_at: datetime,
                   evaluated_at: datetime, fingerprint: str, policy_status: object = None) -> Any | None:
        try:
            # Keep the signed API client single-flight; these are three reads
            # for one observation, not an independent polling loop.
            catalogue = await self.transport.read_catalogue()
            charger_read = await self.transport.read_charger_setting()
            priority_read = await self.transport.read_setting()
            catalogue_verified = self._catalogue_verified(catalogue.catalogue)
            charger = ChargerObservation(
                mode=charger_mode(charger_read.value),
                observed_at=charger_read.received_at,
                provenance=ChargerProvenance.DEVICE_READBACK,
            )
            try:
                reported = parse_reported(OutputField.PRIORITY, priority_read.value)
            except ValueError:
                reported = None
            priority = SettingObservation(
                value=reported,
                observed_at=priority_read.received_at,
                catalogue_verified=catalogue_verified,
                readback_verified=reported is not None,
            )
            decision_at = datetime.now(timezone.utc)
            diagnostics.observe_coordination(
                data, received_at=received_at, evaluated_at=decision_at,
                fingerprint=fingerprint, policy_status=policy_status,
                charger_observation=charger, priority_observation=priority,
            )
            return await diagnostics.execute_utility_hold(self.executor)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Diagnostics must show the failed readback; the charger tick is already complete.
            diagnostics.observe_coordination(
                data, received_at=received_at, evaluated_at=evaluated_at,
                fingerprint=fingerprint, policy_status=policy_status,
                charger_observation=ChargerObservation(provenance=ChargerProvenance.UNKNOWN),
                priority_observation=SettingObservation(),
            )
            return None

    @staticmethod
    def _catalogue_verified(catalogue: object) -> bool:
        if not isinstance(catalogue, dict) or catalogue.get("id") != OutputField.PRIORITY.value:
            return False
        items = catalogue.get("item")
        if not isinstance(items, list):
            return False
        pairs = [(item.get("val"), item.get("key")) for item in items
                 if isinstance(item, dict)]
        return len(pairs) == 3 and len(set(pairs)) == 3 and dict(pairs) == {
            "Utility": "0", "Solar": "1", "SBU": "2"}


__all__ = ["RuntimeUtilityHold", "build_authorization"]
