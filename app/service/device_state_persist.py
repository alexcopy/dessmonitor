"""Persist device last_switched timestamps across pod restarts.

Saves to /app/app/cache/device_state.json on every switch.
Restores on startup so time_delay is respected after restart.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("FULL")

_STATE_FILE = Path(os.getenv("DEVICE_STATE_PATH", "/app/app/cache/device_state.json"))


def save_last_switched(devices) -> None:
    """Save last_switched for all devices to cache file."""
    try:
        data = {
            dev.name: dev.last_switched
            for dev in devices
            if hasattr(dev, "last_switched") and dev.last_switched
        }
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(data))
        logger.debug("[Persist] Saved last_switched for %d devices", len(data))
    except Exception as exc:
        logger.warning("[Persist] Failed to save device state: %s", exc)


def restore_last_switched(devices) -> None:
    """Restore last_switched from cache file on startup."""
    if not _STATE_FILE.exists():
        logger.info("[Persist] No device state cache found — starting fresh")
        return
    try:
        data = json.loads(_STATE_FILE.read_text())
        restored = 0
        for dev in devices:
            if dev.name in data:
                dev.last_switched = int(data[dev.name])
                restored += 1
        logger.info("[Persist] Restored last_switched for %d devices", restored)
    except Exception as exc:
        logger.warning("[Persist] Failed to restore device state: %s", exc)
