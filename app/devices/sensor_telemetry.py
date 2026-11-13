"""Sensor telemetry reading model for dessmonitor.

Typed, immutable representation of a numeric sensor measurement
(e.g., water temperature).  Independent from binary DeviceObservationState.

No imports of Tuya, hardware, web, or runtime modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Mapping


class SensorMetric(Enum):
    """Controlled sensor metric types."""
    WATER_TEMPERATURE = "water_temperature"
    ENERGY = "energy"


class SensorFreshness(Enum):
    """Time-decay classification of a sensor reading."""
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class SensorStatus(Enum):
    """Overall status of a sensor reading."""
    VALID = "valid"
    INVALID = "invalid"
    UNVERIFIED = "unverified"
    CACHED = "cached"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TemperatureDecodeResult:
    """Immutable result of decoding one Tuya temperature observation.

    ``raw_value`` is diagnostic-only data.  Callers crossing the web, ML, or
    API boundaries must use the normalized ``value`` and quality instead.
    """

    value: float | None
    raw_value: object | None
    source_property: str | None
    scale: str
    raw_type: str
    quality: str


_TEMPERATURE_PROPERTIES = (
    "temp_current",
    "temp",
    "va_temperature",
    "temperature",
    "temp_value",
    "temp_humidity_way_1",
)
_NUMERIC_TEMPERATURE_PROPERTIES = frozenset({
    "temp_current",
    "temp",
    "va_temperature",
    "temperature",
    "temp_value",
})
_VERIFIED_TENTHS_PROPERTIES = frozenset({"temp_current", "va_temperature"})
_T1_RE = re.compile(r"\bt1\s*[=-]\s*(-?\d+(?:\.\d+)?)")


def _invalid_temperature_result(
    source_property: str,
    raw_type: str,
    raw_value: object | None = None,
) -> TemperatureDecodeResult:
    return TemperatureDecodeResult(
        value=None,
        raw_value=raw_value,
        source_property=source_property,
        scale="unknown",
        raw_type=raw_type,
        quality="invalid",
    )


def _decode_temperature_candidate(
    source_property: str,
    raw_value: object,
) -> TemperatureDecodeResult:
    """Decode one present candidate according to the fixed table."""
    if raw_value is None:
        return _invalid_temperature_result(source_property, "missing")
    if isinstance(raw_value, bool):
        return _invalid_temperature_result(source_property, "bool")

    raw_type = type(raw_value)
    if raw_type is int:
        value_is_finite = True
        scalar_type = "int"
    elif raw_type is float:
        value_is_finite = math.isfinite(raw_value)
        scalar_type = "float"
    elif raw_type is str:
        value_is_finite = True
        scalar_type = "string"
    else:
        return _invalid_temperature_result(source_property, "unsupported")

    if not value_is_finite:
        return _invalid_temperature_result(source_property, "non_finite")

    if source_property in _VERIFIED_TENTHS_PROPERTIES and scalar_type == "int":
        return TemperatureDecodeResult(
            value=float(raw_value) / 10.0,
            raw_value=raw_value,
            source_property=source_property,
            scale="tenths",
            raw_type="int",
            quality="verified",
        )

    if source_property in _VERIFIED_TENTHS_PROPERTIES and scalar_type == "float":
        return TemperatureDecodeResult(
            value=float(raw_value),
            raw_value=raw_value,
            source_property=source_property,
            scale="celsius",
            raw_type="float",
            quality="verified",
        )

    if source_property == "temp_humidity_way_1" and scalar_type == "string":
        match = _T1_RE.search(raw_value)
        if match is None:
            return _invalid_temperature_result(source_property, "malformed", raw_value)
        return TemperatureDecodeResult(
            value=float(match.group(1)),
            raw_value=raw_value,
            source_property=source_property,
            scale="celsius",
            raw_type="string",
            quality="verified",
        )

    if source_property in _NUMERIC_TEMPERATURE_PROPERTIES and scalar_type in {"int", "float"}:
        return TemperatureDecodeResult(
            value=None,
            raw_value=raw_value,
            source_property=source_property,
            scale="unknown",
            raw_type=scalar_type,
            quality="unverified",
        )

    if source_property in _NUMERIC_TEMPERATURE_PROPERTIES and scalar_type == "string":
        return _invalid_temperature_result(source_property, "malformed", raw_value)

    if source_property == "temp_humidity_way_1" and scalar_type == "string":
        return _invalid_temperature_result(source_property, "malformed", raw_value)

    return _invalid_temperature_result(source_property, "unsupported")


def decode_water_temperature(
    status_by_code: Mapping[str, object] | object,
) -> TemperatureDecodeResult:
    """Decode water temperature using the complete, fixed property table.

    Candidate order and quality precedence are deliberately explicit: the
    first verified result wins, otherwise the first unverified result, then
    the first invalid result.
    """
    if not isinstance(status_by_code, Mapping):
        return TemperatureDecodeResult(
            value=None,
            raw_value=None,
            source_property=None,
            scale="unknown",
            raw_type="unsupported",
            quality="invalid",
        )

    first_unverified: TemperatureDecodeResult | None = None
    first_invalid: TemperatureDecodeResult | None = None
    for source_property in _TEMPERATURE_PROPERTIES:
        if source_property not in status_by_code:
            continue
        result = _decode_temperature_candidate(
            source_property,
            status_by_code[source_property],
        )
        if result.quality == "verified":
            return result
        if result.quality == "unverified" and first_unverified is None:
            first_unverified = result
        elif result.quality == "invalid" and first_invalid is None:
            first_invalid = result

    if first_unverified is not None:
        return first_unverified
    if first_invalid is not None:
        return first_invalid

    if not status_by_code:
        return TemperatureDecodeResult(
            value=None,
            raw_value=None,
            source_property=None,
            scale="unknown",
            raw_type="missing",
            quality="invalid",
        )
    return TemperatureDecodeResult(
        value=None,
        raw_value=None,
        source_property=None,
        scale="unknown",
        raw_type="unsupported",
        quality="invalid",
    )


# Freshness thresholds — reuse observation constants.
SENSOR_FRESH_MAX_AGE_SECONDS: float = 180.0
SENSOR_STALE_MAX_AGE_SECONDS: float = 360.0


@dataclass(frozen=True)
class SensorTelemetryReading:
    """Immutable snapshot of a numeric sensor measurement.

    Attributes:
        sensor_id: Stable logical identifier.
        display_name: Safe operator-facing name.
        description: Operator-configured safe description.
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
    description: str = ""
    metric: SensorMetric = SensorMetric.WATER_TEMPERATURE
    value: float | None = None
    unit: str = "celsius"
    observed_at: datetime | None = None
    source: str = "tuya"
    freshness: SensorFreshness = SensorFreshness.UNAVAILABLE
    status: SensorStatus = SensorStatus.UNAVAILABLE
    communication_status: str = "unknown"
    source_property: str | None = None
    scale: str = "unknown"
    raw_type: str = "missing"


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
