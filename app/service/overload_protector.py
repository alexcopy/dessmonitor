"""
OverloadProtector — monitors battery current and output power,
triggers alerts and device shedding when thresholds are exceeded
for sustained periods.

Thresholds:
  SOFT:     battery_current_dis > 40A for > 5 min  → dashboard alert
  HARD:     battery_current_dis > 50A for > 2 min  → shed low-priority loads
  CRITICAL: output_power_w      > 2000W for > 5 min → shed all non-life-support
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from shared_state.shared_state import shared_state

logger = logging.getLogger("OverloadProtector")

# ── Thresholds ────────────────────────────────────────────────────────────────
SOFT_CURRENT_A      = 40.0   # A
SOFT_DURATION_S     = 300    # 5 min

HARD_CURRENT_A      = 50.0   # A
HARD_DURATION_S     = 120    # 2 min

CRITICAL_POWER_W    = 2000.0 # W
CRITICAL_DURATION_S = 300    # 5 min

POLL_INTERVAL_S     = 30     # check every 30s
COOLDOWN_S          = 300    # 5 min between shedding actions


@dataclass
class OverloadState:
    soft_triggered_at:     Optional[float] = None
    hard_triggered_at:     Optional[float] = None
    critical_triggered_at: Optional[float] = None
    last_shed_at:          float = 0.0
    alert_level:           str = "ok"   # ok | soft | hard | critical


class OverloadProtector:
    """
    Async service that watches shared_state and sheds loads when needed.

    Usage in run.py:
        protector = OverloadProtector(dev_mgr=dev_mgr, tuya_ctrl=tuya_ctrl)
        asyncio.create_task(protector.run(stop_event))
    """

    def __init__(self, dev_mgr, tuya_ctrl, stop_event: asyncio.Event = None):
        self.dev_mgr   = dev_mgr
        self.tuya_ctrl = tuya_ctrl
        self._stop     = stop_event or asyncio.Event()
        self._state    = OverloadState()

    async def run(self, stop_event: asyncio.Event = None) -> None:
        if stop_event:
            self._stop = stop_event
        logger.info("[OverloadProtector] started — polling every %ds", POLL_INTERVAL_S)
        while not self._stop.is_set():
            try:
                await self._check_once()
            except Exception as exc:
                logger.error("[OverloadProtector] check failed: %s", exc, exc_info=True)
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _check_once(self) -> None:
        now = time.time()
        dis  = shared_state.get("battery_current_dis") or 0.0
        pwr  = shared_state.get("output_power") or shared_state.get("output_apparent_power") or 0.0
        mode = (shared_state.get("working_mode") or "").upper()

        # Only protect in Invert Mode — grid mode has no battery risk
        if "INVERT" not in mode:
            self._reset_timers()
            self._publish_alert("ok", dis, pwr)
            return

        # ── track onset times ─────────────────────────────────────────────
        if dis > SOFT_CURRENT_A:
            if self._state.soft_triggered_at is None:
                self._state.soft_triggered_at = now
                logger.warning("[OverloadProtector] SOFT onset: dis=%.1fA", dis)
        else:
            self._state.soft_triggered_at = None

        if dis > HARD_CURRENT_A:
            if self._state.hard_triggered_at is None:
                self._state.hard_triggered_at = now
                logger.warning("[OverloadProtector] HARD onset: dis=%.1fA", dis)
        else:
            self._state.hard_triggered_at = None

        if pwr > CRITICAL_POWER_W:
            if self._state.critical_triggered_at is None:
                self._state.critical_triggered_at = now
                logger.warning("[OverloadProtector] CRITICAL onset: pwr=%.0fW", pwr)
        else:
            self._state.critical_triggered_at = None

        # ── evaluate and act ──────────────────────────────────────────────
        critical_due = (
            self._state.critical_triggered_at is not None
            and now - self._state.critical_triggered_at >= CRITICAL_DURATION_S
        )
        hard_due = (
            self._state.hard_triggered_at is not None
            and now - self._state.hard_triggered_at >= HARD_DURATION_S
        )
        soft_due = (
            self._state.soft_triggered_at is not None
            and now - self._state.soft_triggered_at >= SOFT_DURATION_S
        )

        cooldown_ok = (now - self._state.last_shed_at) >= COOLDOWN_S

        if critical_due:
            self._publish_alert("critical", dis, pwr)
            if cooldown_ok:
                await self._shed_all_non_life_support()
                self._state.last_shed_at = now
        elif hard_due:
            self._publish_alert("hard", dis, pwr)
            if cooldown_ok:
                await self._shed_low_priority()
                self._state.last_shed_at = now
        elif soft_due:
            self._publish_alert("soft", dis, pwr)
        else:
            level = "soft" if self._state.soft_triggered_at else "ok"
            self._publish_alert(level, dis, pwr)

    def _reset_timers(self) -> None:
        self._state.soft_triggered_at     = None
        self._state.hard_triggered_at     = None
        self._state.critical_triggered_at = None

    def _publish_alert(self, level: str, dis: float, pwr: float) -> None:
        if level != self._state.alert_level:
            logger.info("[OverloadProtector] alert level: %s → %s (dis=%.1fA pwr=%.0fW)",
                        self._state.alert_level, level, dis, pwr)
        self._state.alert_level = level
        shared_state["overload_alert"] = {
            "level": level,          # ok | soft | hard | critical
            "battery_current_dis": dis,
            "output_power_w": pwr,
            "ts": time.time(),
        }

    async def _shed_low_priority(self) -> None:
        """Turn off devices with priority > 5 (lower importance)."""
        devices = self.dev_mgr.get_devices()
        candidates = [
            d for d in devices
            if getattr(d, "enabled", False)
            and not getattr(getattr(d, "extra", {}), "get", lambda k, v=None: v)("is_life_support", False)
            and getattr(d, "priority", 0) > 1   # never shed priority=1 devices
            and getattr(getattr(d, "observation", None), "is_on", False)
        ]
        # Shed highest priority NUMBER first (least important devices first)
        candidates.sort(key=lambda d: -getattr(d, "priority", 0))
        if not candidates:
            logger.info("[OverloadProtector] HARD: no sheddable devices")
            return
        # Shed ONE device at a time — highest priority number first
        dev = candidates[0]
        logger.warning("[OverloadProtector] HARD shed: turning off %s (priority=%d)",
                       dev.name, getattr(dev, "priority", 0))
        try:
            await asyncio.to_thread(self.tuya_ctrl.switch_off_device, dev)
            logger.info("[OverloadProtector] shed: %s OFF", dev.name)
        except Exception as exc:
            logger.error("[OverloadProtector] shed %s failed: %s", dev.name, exc)

    async def _shed_all_non_life_support(self) -> None:
        """Turn off all devices except life_support."""
        devices = self.dev_mgr.get_devices()
        candidates = [
            d for d in devices
            if getattr(d, "enabled", False)
            and not getattr(getattr(d, "extra", {}), "get", lambda k, v=None: v)("is_life_support", False)
            and getattr(d, "priority", 0) > 1   # never shed priority=1 devices
            and getattr(getattr(d, "observation", None), "is_on", False)
        ]
        # Shed highest priority NUMBER first (least important devices first)
        candidates.sort(key=lambda d: -getattr(d, "priority", 0))
        logger.warning("[OverloadProtector] CRITICAL shed: turning off %d devices", len(candidates))
        for dev in candidates:
            try:
                await asyncio.to_thread(self.tuya_ctrl.switch_off_device, dev)
                logger.info("[OverloadProtector] critical shed: %s OFF", dev.name)
            except Exception as exc:
                logger.error("[OverloadProtector] critical shed %s failed: %s", dev.name, exc)
