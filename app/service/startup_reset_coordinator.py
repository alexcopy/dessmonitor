"""Startup reset coordinator for dessmonitor.

Ensures every available valid binary switch is commanded OFF and
confirmed OFF through independent Tuya observation before autonomous
SmartHomeController switching may begin.

Executes once per full process startup.  Timeout never opens the gate.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("StartupReset")

# Maximum total reset duration in seconds.
STARTUP_RESET_TIMEOUT_SECONDS: float = 120.0

# Observation retry interval in seconds.
STARTUP_RESET_RETRY_INTERVAL: float = 30.0

# Inter-command delay for rate limiting (seconds).
INTER_COMMAND_DELAY_SECONDS: float = 0.5


@dataclass
class TargetResetState:
    """Per-target reset tracking state.

    Attributes:
        device: The RelayChannelDevice being reset.
        command_result: Result of the OFF command submission, or None.
        confirmed: True when a fresh OFF observation was received after
            the command.
        contradictory: True when ON was observed after the OFF command.
        skipped: True when the device was excluded from reset
            (not command-capable, invalid mapping, unavailable).
    """
    device: object  # RelayChannelDevice (avoid circular import)
    command_result: object | None = None  # CommandResult
    confirmed: bool = False
    contradictory: bool = False
    skipped: bool = False
    skipped_reason: str = ""


class StartupResetCoordinator:
    """Coordinates startup reset: command OFF -> observe -> gate."""

    def __init__(
        self,
        dev_mgr,  # RelayDeviceManager
        tuya_ctrl,  # RelayTuyaController
        status_updater,  # TuyaStatusUpdaterAsync
        timeout: float = STARTUP_RESET_TIMEOUT_SECONDS,
        retry_interval: float = STARTUP_RESET_RETRY_INTERVAL,
    ):
        self._dev_mgr = dev_mgr
        self._ctrl = tuya_ctrl
        self._updater = status_updater
        self._timeout = timeout
        self._retry_interval = retry_interval

        self._gate_open: bool = False
        self._reset_status: str = "not_started"
        self._targets: dict[str, TargetResetState] = {}
        self._logger = logging.getLogger("StartupReset")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_gate_open(self) -> bool:
        """True when the startup reset gate is open."""
        return self._gate_open

    @property
    def reset_status(self) -> str:
        """Current reset status string."""
        return self._reset_status

    @property
    def target_count(self) -> int:
        """Total number of reset targets."""
        return len(self._targets)

    @property
    def confirmed_count(self) -> int:
        """Number of confirmed-OFF targets."""
        return sum(1 for t in self._targets.values() if t.confirmed)

    def get_target_result(self, device_id: str) -> str | None:
        """Get the reset result for a device, or None."""
        t = self._targets.get(device_id)
        if t is None:
            return None
        if t.skipped:
            return f"skipped-{t.skipped_reason}" if t.skipped_reason else "skipped"
        if t.confirmed:
            return "confirmed_off"
        if t.contradictory:
            return "contradictory"
        if t.command_result is not None:
            cr = t.command_result
            if cr.accepted:  # type: ignore[union-attr]
                return "pending"
            return "command_failed"
        return "pending"

    def get_per_device_results(self) -> dict[str, str]:
        """Return device_id -> reset_result for read model."""
        return {
            dev_id: (self.get_target_result(dev_id) or "unknown")
            for dev_id in self._targets
        }

    # ------------------------------------------------------------------
    # Target selection
    # ------------------------------------------------------------------

    def _build_target_set(self) -> list[TargetResetState]:
        """Select devices that must participate in startup reset.

        Returns all available, valid, command-capable binary switches.
        """
        targets: list[TargetResetState] = []
        devices = self._dev_mgr.get_devices()

        for dev in devices:
            mapping = dev.property_mapping

            # Only binary command-capable devices
            if not mapping.command_capable:
                t = TargetResetState(device=dev, skipped=True)
                if mapping.command_kind.name == "NONE":
                    t.skipped_reason = "not-command-capable"
                elif mapping.mapping_validity.name == "INVALID":
                    t.skipped_reason = "invalid-mapping"
                else:
                    t.skipped_reason = "not-binary-or-numeric"
                targets.append(t)
                self._logger.debug(
                    "[RESET] %s: skipped — %s",
                    dev.name, t.skipped_reason,
                    extra={"evt": "reset_skip", "dev": dev.name,
                           "reason": t.skipped_reason},
                )
                continue

            if not dev.available:
                t = TargetResetState(device=dev, skipped=True,
                                     skipped_reason="unavailable")
                targets.append(t)
                continue

            # Valid binary switch — must be reset
            t = TargetResetState(device=dev)
            targets.append(t)
            self._logger.info(
                "[RESET] %s: target added for startup reset",
                dev.name,
                extra={"evt": "reset_target", "dev": dev.name},
            )

        return targets

    # ------------------------------------------------------------------
    # Execute reset
    # ------------------------------------------------------------------

    async def execute(self) -> None:
        """Execute startup reset once.  Returns when gate opens or
        timeout expires."""
        self._reset_status = "in_progress"
        self._gate_open = False

        self._targets = {
            t.device.id: t  # type: ignore[attr-defined]
            for t in self._build_target_set()
        }
        # Collect only non-skipped targets for command and observation
        active_targets = [
            t for t in self._targets.values() if not t.skipped
        ]

        if not active_targets:
            self._logger.info(
                "[RESET] No binary switch targets — gate opens immediately",
                extra={"evt": "reset_no_targets"},
            )
            self._gate_open = True
            self._reset_status = "confirmed"
            return

        # Step 5: Submit OFF commands
        self._logger.info(
            "[RESET] Submitting OFF to %d target(s)",
            len(active_targets),
            extra={"evt": "reset_commands", "count": len(active_targets)},
        )
        for t in active_targets:
            dev = t.device
            result = self._ctrl.switch_off(dev)
            t.command_result = result
            if result.accepted:
                self._logger.info(
                    "[RESET] %s: OFF accepted", dev.name,
                    extra={"evt": "reset_cmd_ok", "dev": dev.name},
                )
            else:
                self._logger.warning(
                    "[RESET] %s: OFF failed — %s",
                    dev.name, result.error or "rejected",
                    extra={"evt": "reset_cmd_fail", "dev": dev.name,
                           "error": result.error or "rejected"},
                )
            await asyncio.sleep(INTER_COMMAND_DELAY_SECONDS)

        # Step 7: Immediate observation refresh
        try:
            await self._updater.refresh_once()
        except Exception as exc:
            self._logger.warning(
                "[RESET] Initial observation refresh failed: %s", exc,
                extra={"evt": "reset_refresh_fail"},
            )

        # Step 8: Bounded observation retries
        elapsed: float = 0.0
        while elapsed < self._timeout:
            self._check_confirmations()

            all_confirmed = all(
                t.confirmed for t in active_targets
            )
            if all_confirmed:
                self._gate_open = True
                self._reset_status = "confirmed"
                n = len(active_targets)
                self._logger.info(
                    "[RESET] All %d target(s) confirmed OFF — gate OPEN",
                    n,
                    extra={"evt": "reset_gate_open", "count": n},
                )
                return

            await asyncio.sleep(self._retry_interval)
            elapsed += self._retry_interval

            # Refresh observation
            try:
                await self._updater.refresh_once()
            except Exception:
                pass

        # Step 9: Timeout
        unconfirmed = [
            t.device.name for t in active_targets if not t.confirmed
        ]
        self._reset_status = "blocked"
        self._gate_open = False
        self._logger.error(
            "[RESET] Startup reset timeout — %d target(s) not confirmed OFF: %s",
            len(unconfirmed), ", ".join(unconfirmed),
            extra={"evt": "reset_timeout", "unconfirmed": unconfirmed},
        )

    # ------------------------------------------------------------------
    # Confirmation check
    # ------------------------------------------------------------------

    def _check_confirmations(self) -> None:
        """Check each target's canonical observation for fresh OFF."""
        from app.devices.device_observation import (
            ObservationValue,
            compute_freshness,
            ObservationFreshness,
        )
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for t in self._targets.values():
            if t.skipped or t.confirmed or t.contradictory:
                continue

            obs = t.device.observation
            if obs.is_unknown:
                continue

            freshness = compute_freshness(obs, now_utc=now)
            if freshness != ObservationFreshness.FRESH:
                continue

            if obs.observed_state == ObservationValue.OFF:
                t.confirmed = True
                self._logger.info(
                    "[RESET] %s: confirmed OFF", t.device.name,
                    extra={"evt": "reset_confirmed", "dev": t.device.name},
                )
            elif obs.observed_state == ObservationValue.ON:
                t.contradictory = True
                self._logger.warning(
                    "[RESET] %s: accepted OFF but observed ON — contradictory",
                    t.device.name,
                    extra={"evt": "reset_contradictory", "dev": t.device.name},
                )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cancel(self) -> None:
        """Cancel pending reset operations (called during shutdown)."""
        self._reset_status = "cancelled"
        self._logger.info("[RESET] Startup reset cancelled")
