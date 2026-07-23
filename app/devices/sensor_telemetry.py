"""Sensor telemetry reading model for dessmonitor.

Typed, immutable representation of a numeric sensor measurement
(e.g., water temperature).  Independent from binary DeviceObservationState.

No imports of Tuya, hardware, web, or runtime modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class SensorMetric(Enum):
    """Controlled sensor metric types."""
    WATER_TEMPERATURE = "water_temperature"


class SensorFreshness(Enum):
    """Time-decay classification of a sensor reading."""
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class SensorStatus(Enum):
    """Overall status of a sensor reading."""
    VALID = "valid"
    INVALID = "invalid"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


# Freshness thresholds — reuse observation constants.
SENSOR_FRESH_MAX_AGE_SECONDS: float = 180.0
SENSOR_STALE_MAX_AGE_SECONDS: float = 360.0


@dataclass(frozen=True)
class SensorTelemetryReading:
    """Immutable snapshot of a numeric sensor measurement.

    Attributes:
        sensor_id: Stable logical identifier.
        display_name: Safe operator-facing name.
        metric: Controlled metric type.
        value: Normalized float value, or None when unavailable.
        unit: Controlled unit string (e.g., "celsius").
        observed_at: UTC datetime of the last valid measurement, or None.
        source: Controlled safe source string (e.g., "tuya").
        freshness: FRESH, STALE, or UNAVAILABLE.
        status: VALID, INVALID, STALE, or UNAVAILABLE.
        communication_status: active, disabled, permission_denied,
            transient_error, or unavailable.
    """
    sensor_id: str = ""
    display_name: str = ""
    metric: SensorMetric = SensorMetric.WATER_TEMPERATURE
    value: float | None = None
    unit: str = "celsius"
    observed_at: datetime | None = None
    source: str = "tuya"
    freshness: SensorFreshness = SensorFreshness.UNAVAILABLE
    status: SensorStatus = SensorStatus.UNAVAILABLE
    communication_status: str = "unknown"


def compute_sensor_freshness(
    observed_at: datetime | None,
    now_utc: datetime | None = None,
) -> SensorFreshness:
    """Compute freshness for a sensor reading.

    Args:
        observed_at: UTC timestamp of the last valid measurement, or None.
        now_utc: Reference time (defaults to current UTC).

    Returns:
        FRESH, STALE, or UNAVAILABLE.
    """
    if observed_at is None:
        return SensorFreshness.UNAVAILABLE
    if not isinstance(observed_at, datetime):
        return SensorFreshness.UNAVAILABLE

    ref = now_utc if now_utc is not None else datetime.now(timezone.utc)
    obs = observed_at
    if obs.tzinfo is None:
        obs = obs.replace(tzinfo=timezone.utc)

    age = (ref - obs).total_seconds()
    if age < 0:
        return SensorFreshness.STALE
    if age < SENSOR_FRESH_MAX_AGE_SECONDS:
        return SensorFreshness.FRESH
    if age < SENSOR_STALE_MAX_AGE_SECONDS:
        return SensorFreshness.STALE
    return SensorFreshness.UNAVAILABLE
