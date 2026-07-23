# app/status_updater_async.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.utils.time_utils import smart_sleep
from shared_state.shared_state import shared_state

TUYA_RPC_TIMEOUT = 20
MAX_ISOLATION_REQUESTS = 16
PERMISSION_DENIED_RETRY_SECONDS = 300
PERMISSION_DENIED_MAX_RETRY_SECONDS = 3600

logger = logging.getLogger("TuyaStatusUpdater")


class ParentCommState:
    """In-memory communication state for a single Tuya parent device."""

    __slots__ = ("status", "retry_at", "retry_interval")

    def __init__(self):
        self.status: str = "active"  # active | permission_denied | transient_error | disabled
        self.retry_at: float = 0.0
        self.retry_interval: float = PERMISSION_DENIED_RETRY_SECONDS


class TuyaStatusUpdaterAsync:
    """
    Periodically polls Tuya device status and updates RelayChannelDevice
    observations.  Uses property_mapping.state_property for DP code lookup.

    PR 0034e additions:
    - Filters to enabled, observable devices only.
    - Validates batch response success.
    - Recursive bisection isolation for code 1106 (permission deny).
    - In-memory quarantine with bounded exponential backoff.
    - Disabled devices are not polled, not updated.
    """

    def __init__(self, interval: int = 30, dev_mgr=None, authorisation=None):
        self.interval = interval
        self._stop = asyncio.Event()
        self.dev_mgr = dev_mgr
        self.auth = authorisation
        self._parent_states: dict[str, ParentCommState] = {}
        self._isolation_budget: int = 0

    # -------------------------------------------------------------
    async def run(self):
        logger.info("Async-status-updater started")
        while not self._stop.is_set():
            try:
                await self._update_once()
            except Exception as exc:
                logger.error(f"status update failed: {exc}", exc_info=True)
            await smart_sleep(self._stop, self.interval)
        logger.info("Async-status-updater stopped")

    # -------------------------------------------------------------
    async def refresh_once(self) -> None:
        """Perform exactly one observation cycle."""
        await self._update_once()

    # -------------------------------------------------------------
    def _build_poll_targets(self) -> tuple[list[str], dict[str, list[Any]]]:
        """Build ordered list of unique parent IDs and parent->devices index.

        Returns:
            (parent_ids, parent_to_devices) where parent_ids contains only
            enabled, observable parents with non-empty tuya_device_id.
        """
        devices = self.dev_mgr.get_devices()
        parent_to_devices: dict[str, list[Any]] = {}
        seen_parents: dict[str, int] = {}

        for dev in devices:
            if not dev.enabled:
                continue
            if not dev.property_mapping.observable:
                continue
            pid = dev.tuya_device_id
            if not pid:
                continue
            if pid not in parent_to_devices:
                parent_to_devices[pid] = []
                seen_parents[pid] = len(seen_parents)
            parent_to_devices[pid].append(dev)

        # Ordered by first occurrence
        ordered = sorted(parent_to_devices.keys(), key=lambda p: seen_parents[p])
        return ordered, parent_to_devices

    # -------------------------------------------------------------
    def _get_healthy_parents(self, all_parents: list[str]) -> list[str]:
        """Return parents that are not in quarantine."""
        now = datetime.now(timezone.utc).timestamp()
        healthy = []
        for pid in all_parents:
            state = self._parent_states.get(pid)
            if state is None:
                healthy.append(pid)
            elif state.status == "active":
                healthy.append(pid)
            elif state.status == "permission_denied" and now >= state.retry_at:
                # Retry due
                healthy.append(pid)
            elif state.status == "transient_error" and now >= state.retry_at:
                healthy.append(pid)
            # disabled or not yet retry_due: skip
        return healthy

    # -------------------------------------------------------------
    async def _update_once(self) -> None:
        all_parents, parent_to_devices = self._build_poll_targets()
        if not all_parents:
            logger.debug("[Updater] No enabled observable parents to poll")
            return

        healthy = self._get_healthy_parents(all_parents)
        if not healthy:
            logger.debug("[Updater] All parents quarantined — skipping cycle")
            return

        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())

        # Healthy fast path: single batch
        if len(healthy) == len(all_parents):
            await self._poll_and_process(healthy, parent_to_devices, now_utc, now_ts)
        else:
            # Mixed: poll healthy batch, then probe quarantined individually
            await self._poll_and_process(healthy, parent_to_devices, now_utc, now_ts)
            for pid in all_parents:
                state = self._parent_states.get(pid)
                if state and state.status == "permission_denied" and now_utc.timestamp() >= state.retry_at:
                    await self._poll_and_process(
                        [pid], parent_to_devices, now_utc, now_ts
                    )

    # -------------------------------------------------------------
    async def _poll_and_process(
        self,
        parent_ids: list[str],
        parent_to_devices: dict[str, list[Any]],
        now_utc: datetime,
        now_ts: int,
    ) -> None:
        """Poll a list of parent IDs and process results."""
        if not parent_ids:
            return

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.auth.device_manager.get_device_list_status,
                    parent_ids,
                ),
                timeout=TUYA_RPC_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[Updater] Tuya RPC > %ds for %d parent(s)",
                TUYA_RPC_TIMEOUT, len(parent_ids),
            )
            return
        except Exception as exc:
            logger.error(
                "[Updater] Tuya RPC exception for %d parent(s): %s",
                len(parent_ids), exc,
            )
            return

        # Validate response
        if not isinstance(result, dict):
            logger.warning("[Updater] Invalid response type: %s", type(result).__name__)
            return

        success = result.get("success")
        if success is False:
            code = result.get("code")
            msg = result.get("msg", "")
            if code == 1106 and len(parent_ids) > 1:
                # Permission deny on multi-parent batch — bisect
                await self._bisect_failed(parent_ids, parent_to_devices, now_utc, now_ts)
            elif code == 1106 and len(parent_ids) == 1:
                # Single parent permission denied
                self._mark_permission_denied(parent_ids[0])
                logger.warning(
                    "[Updater] Parent %s: permission denied",
                    parent_ids[0],
                )
            else:
                logger.warning(
                    "[Updater] Batch failed: success=false code=%s msg=%s",
                    code, msg,
                )
            return

        if "result" not in result:
            logger.warning("[Updater] Batch response missing result")
            return

        # Process successful response
        self._process_result(result, parent_to_devices, now_utc, now_ts)

        # Clear quarantine for successfully polled parents
        for pid in parent_ids:
            state = self._parent_states.get(pid)
            if state is not None:
                state.status = "active"
                state.retry_at = 0.0
                state.retry_interval = PERMISSION_DENIED_RETRY_SECONDS

        logger.debug("[Updater] statuses synced for %d parent(s)", len(parent_ids))

    # -------------------------------------------------------------
    async def _bisect_failed(
        self,
        parent_ids: list[str],
        parent_to_devices: dict[str, list[Any]],
        now_utc: datetime,
        now_ts: int,
    ) -> None:
        """Recursively bisect a failed batch to isolate permission-denied parents."""
        self._isolation_budget += 1
        if self._isolation_budget > MAX_ISOLATION_REQUESTS:
            logger.warning("[Updater] Isolation budget exceeded — giving up")
            return

        mid = len(parent_ids) // 2
        left = parent_ids[:mid]
        right = parent_ids[mid:]

        for half in (left, right):
            if not half:
                continue
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.auth.device_manager.get_device_list_status,
                        half,
                    ),
                    timeout=TUYA_RPC_TIMEOUT,
                )
            except (asyncio.TimeoutError, Exception):
                # Do not split on transient errors
                logger.warning(
                    "[Updater] Bisect half failed with exception — not splitting further"
                )
                continue

            if not isinstance(result, dict):
                continue

            success = result.get("success")
            if success is False and result.get("code") == 1106:
                if len(half) == 1:
                    self._mark_permission_denied(half[0])
                    logger.warning(
                        "[Updater] Isolated parent %s: permission denied", half[0],
                    )
                else:
                    await self._bisect_failed(half, parent_to_devices, now_utc, now_ts)
            elif success is not False and "result" in result:
                # This half succeeded — process it
                self._process_result(result, parent_to_devices, now_utc, now_ts)
                for pid in half:
                    state = self._parent_states.get(pid)
                    if state is not None:
                        state.status = "active"
                        state.retry_at = 0.0
                        state.retry_interval = PERMISSION_DENIED_RETRY_SECONDS

    # -------------------------------------------------------------
    def _mark_permission_denied(self, parent_id: str) -> None:
        """Mark a parent as permission_denied with exponential backoff."""
        state = self._parent_states.get(parent_id)
        if state is None:
            state = ParentCommState()
            self._parent_states[parent_id] = state
        state.status = "permission_denied"
        now = datetime.now(timezone.utc).timestamp()
        state.retry_at = now + state.retry_interval
        state.retry_interval = min(
            state.retry_interval * 2,
            PERMISSION_DENIED_MAX_RETRY_SECONDS,
        )

    # -------------------------------------------------------------
    def _process_result(
        self,
        result: dict[str, Any],
        parent_to_devices: dict[str, list[Any]],
        now_utc: datetime,
        now_ts: int,
    ) -> None:
        """Process a successful Tuya batch result."""
        for dev_res in result.get("result", []):
            tuya_id = dev_res.get("id")
            if tuya_id not in parent_to_devices:
                continue
            status_list = dev_res.get("status", [])

            status_by_code: dict[str, object] = {}
            for item in status_list:
                code = item.get("code")
                if code is not None:
                    status_by_code[str(code)] = item.get("value")

            for dev in parent_to_devices[tuya_id]:
                if not dev.enabled:
                    continue
                sp = dev.property_mapping.state_property
                if sp is None:
                    continue

                value = status_by_code.get(str(sp))
                if value is not None:
                    dev.update_observation_from_tuya(value, now_utc)

                parsed = dev.extract_status(status_list)
                dev.update_status(parsed)
                dev.tick(now_ts)

                if dev.device_type.lower() != "pump":
                    continue
                mode_val = next(
                    (item["value"] for item in status_list
                     if item["code"] == dev.tuya_code_mode()),
                    None,
                )
                if mode_val is not None:
                    try:
                        shared_state["pump_mode"] = int(mode_val)
                    except (ValueError, TypeError):
                        logger.debug("[Updater] Problem with sync")

    # -------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
