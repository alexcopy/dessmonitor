"""Canonical device observation state for dessmonitor.

Provides the single source of truth for physical device ON/OFF/UNKNOWN state,
with explicit observation source, timestamp, and freshness semantics.

Separates:
- Inventory (devices.yaml configuration)
- Observation (physical state from Tuya)
- Command result (submitted, not yet confirmed)
- Freshness (time-decay from last successful observation)

No imports of Tuya, hardware, web, or runtime modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# ObservationValue — discrete physical state
# ---------------------------------------------------------------------------


class ObservationValue(Enum):
    """Canonical physical state of a device channel.

    ON: last observation confirmed the channel is physically ON.
    OFF: last observation confirmed the channel is physically OFF.
    UNKNOWN: no trustworthy observation is currently available.
        UNKNOWN is NOT OFF. UNKNOWN is NOT ON.
    """
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ObservationFreshness — how current the last observation is
# ---------------------------------------------------------------------------


class ObservationFreshness(Enum):
    """Time-decay classification of the last observation.

    FRESH: observation age < FRESH_MAX_AGE_SECONDS.
    STALE: observation exists but age >= FRESH_MAX_AGE_SECONDS.
    UNAVAILABLE: no observation has ever been made (observed_at is None).
    """
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Freshness thresholds
# ---------------------------------------------------------------------------

# Aligned with the production Tuya polling interval (120s).
# 180s = 1.5x the polling interval, providing headroom for
# occasional single-cycle delays without going stale.
FRESH_MAX_AGE_SECONDS: float = 180.0  # 3 minutes

# 360s = 3x the polling interval. Observations older than this
# are considered unsafe for automation decisions.
STALE_MAX_AGE_SECONDS: float = 360.0  # 6 minutes


# ---------------------------------------------------------------------------
# DeviceObservationState — frozen canonical observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceObservationState:
    """Immutable snapshot of the last trusted physical observation.

    All attributes are read-only after construction.  Consumers must
    create a new instance to represent a changed observation.

    Attributes:
        observed_state: ON, OFF, or UNKNOWN.
        observed_at: UTC timestamp of the last successful observation,
            or None when no observation has ever been made.
        observation_source: The system that produced the observation
            (currently "tuya"), or None when no observation exists.
        freshness: Whether the observation is currently FRESH, STALE,
            or UNAVAILABLE.  Computed dynamically by
            :func:`compute_freshness`, not stored statically.
    """
    observed_state: ObservationValue = ObservationValue.UNKNOWN
    observed_at: datetime | None = None
    observation_source: str | None = None

    @property
    def is_on(self) -> bool:
        """True only when the last observation was confirmed ON."""
        return self.observed_state == ObservationValue.ON

    @property
    def is_off(self) -> bool:
        """True only when the last observation was confirmed OFF."""
        return self.observed_state == ObservationValue.OFF

    @property
    def is_unknown(self) -> bool:
        """True when no trustworthy ON/OFF observation is available."""
        return self.observed_state == ObservationValue.UNKNOWN

    @property
    def has_observation(self) -> bool:
        """True when at least one observation has ever been made."""
        return self.observed_at is not None


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_observation_on(
    source: str = "tuya",
    observed_at: datetime | None = None,
) -> DeviceObservationState:
    """Create a confirmed-ON observation."""
    return DeviceObservationState(
        observed_state=ObservationValue.ON,
        observed_at=observed_at or _utcnow(),
        observation_source=source,
    )


def make_observation_off(
    source: str = "tuya",
    observed_at: datetime | None = None,
) -> DeviceObservationState:
    """Create a confirmed-OFF observation."""
    return DeviceObservationState(
        observed_state=ObservationValue.OFF,
        observed_at=observed_at or _utcnow(),
        observation_source=source,
    )


def make_observation_unavailable() -> DeviceObservationState:
    """Create a no-observation-ever state (UNKNOWN, no timestamp, no source)."""
    return DeviceObservationState(
        observed_state=ObservationValue.UNKNOWN,
        observed_at=None,
        observation_source=None,
    )


# ---------------------------------------------------------------------------
# Freshness computation
# ---------------------------------------------------------------------------

# Default UTC clock — replaceable for deterministic testing.
_utcnow: object = datetime.now


def _get_utcnow() -> datetime:
    """Return the current UTC datetime via the injectable clock."""
    clock = _utcnow
    # Support both datetime.now and datetime.utcnow() patterns
    if clock is datetime.now:
        return datetime.now(timezone.utc)
    result = clock()  # type: ignore[call-arg]
    if isinstance(result, datetime):
        if result.tzinfo is None:
            # Naive datetime — assume the caller intended UTC
            result = result.replace(tzinfo=timezone.utc)
        return result
    raise TypeError(f"Clock returned non-datetime: {type(result)}")


def set_clock(clock: object) -> None:
    """Inject a custom clock for deterministic testing.

    Args:
        clock: A callable that returns a timezone-aware datetime.
    """
    global _utcnow
    _utcnow = clock


def reset_clock() -> None:
    """Restore the default UTC clock."""
    global _utcnow
    _utcnow = datetime.now


def compute_freshness(
    observation: DeviceObservationState,
    now_utc: datetime | None = None,
    fresh_threshold: float | None = None,
    stale_threshold: float | None = None,
) -> ObservationFreshness:
    """Compute the freshness of an observation at a given instant.

    Args:
        observation: The observation to evaluate.
        now_utc: The reference time (defaults to current UTC).
        fresh_threshold: Maximum age in seconds for FRESH
            (defaults to FRESH_MAX_AGE_SECONDS).
        stale_threshold: Maximum age in seconds before UNAVAILABLE
            (defaults to STALE_MAX_AGE_SECONDS).

    Returns:
        FRESH, STALE, or UNAVAILABLE.

    Rules:
        - No observation (observed_at is None) → UNAVAILABLE.
        - Malformed observed_at (not a datetime) → UNAVAILABLE.
        - Future observed_at (clock skew) → STALE.
        - Age < fresh_threshold → FRESH.
        - Age >= fresh_threshold and age < stale_threshold → STALE.
        - Age >= stale_threshold → UNAVAILABLE.
    """
    fresh_sec = fresh_threshold if fresh_threshold is not None else FRESH_MAX_AGE_SECONDS
    stale_sec = stale_threshold if stale_threshold is not None else STALE_MAX_AGE_SECONDS

    if observation.observed_at is None:
        return ObservationFreshness.UNAVAILABLE

    if not isinstance(observation.observed_at, datetime):
        return ObservationFreshness.UNAVAILABLE

    ref = now_utc if now_utc is not None else _get_utcnow()

    # Ensure both datetimes are timezone-aware for comparison
    obs_at = observation.observed_at
    if obs_at.tzinfo is None:
        obs_at = obs_at.replace(tzinfo=timezone.utc)

    age_seconds = (ref - obs_at).total_seconds()

    # Future timestamp (clock skew) → STALE, never FRESH
    if age_seconds < 0:
        return ObservationFreshness.STALE

    if age_seconds < fresh_sec:
        return ObservationFreshness.FRESH

    if age_seconds < stale_sec:
        return ObservationFreshness.STALE

    return ObservationFreshness.UNAVAILABLE
