"""In-memory telemetry registry for dessmonitor.

Owned by the runtime composition root (run.py).  Provides typed,
deterministic read-only snapshots of sensor telemetry readings.

Rebuilt on process restart.  No persistence, no network, no hardware.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from app.devices.sensor_telemetry import (
    SensorTelemetryReading,
    SensorMetric,
    SensorFreshness,
    SensorStatus,
    TemperatureDecodeResult,
    compute_sensor_freshness,
)


class TelemetryRegistry:
    """In-memory registry for sensor telemetry readings.

    Keyed by stable logical sensor_id.  Provides deterministic
    read-only snapshots for the web read model.
    """

    def __init__(self):
        self._readings: dict[str, SensorTelemetryReading] = {}

    # ------------------------------------------------------------------
    # Configured sensor descriptors
    # ------------------------------------------------------------------

    def register_sensor_descriptor(
        self,
        sensor_id: str,
        display_name: str,
        description: str = "",
        metric: SensorMetric = SensorMetric.WATER_TEMPERATURE,
        unit: str = "celsius",
        communication_status: str = "unknown",
    ) -> None:
        """Register a configured sensor descriptor before any reading exists.

        This ensures the sensor appears in the Sensors section even before
        the first valid telemetry reading.  Idempotent — only sets the
        descriptor if no reading exists yet for this sensor_id.
        """
        if sensor_id not in self._readings:
            self._readings[sensor_id] = SensorTelemetryReading(
                sensor_id=sensor_id,
                display_name=display_name,
                description=description,
                metric=metric,
                value=None,
                unit=unit,
                observed_at=None,
                source="tuya",
                freshness=SensorFreshness.UNAVAILABLE,
                status=SensorStatus.UNAVAILABLE,
                communication_status=communication_status,
            )

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_water_temperature(
        self,
        sensor_id: str,
        display_name: str,
        observation: TemperatureDecodeResult,
        description: str = "",
        observed_at: datetime | None = None,
        source: str = "tuya",
        communication_status: str = "active",
    ) -> bool:
        """Update a reading from an already-decoded temperature observation.

        The decoder owns all scale decisions.  The registry only accepts a
        finite normalized Celsius value for a verified observation and rounds
        it for display/storage.
        """
        now = observed_at if observed_at is not None else datetime.now(timezone.utc)

        existing = self._readings.get(sensor_id)
        desc = description or (existing.description if existing else "")
        if (
            isinstance(observation, TemperatureDecodeResult)
            and observation.quality == "verified"
            and self._is_finite_normalized(observation.value)
        ):
            normalized = round(float(observation.value), 1)
            freshness = SensorFreshness.FRESH
            status = SensorStatus.VALID
            accepted_source = source
            accepted_observed_at = now
            accepted_value = normalized
            accepted_property = observation.source_property
            accepted_scale = observation.scale
            accepted_raw_type = observation.raw_type
            accepted = True
        else:
            quality = (
                observation.quality
                if isinstance(observation, TemperatureDecodeResult)
                and observation.quality in {"unverified", "invalid"}
                else "invalid"
            )
            status = SensorStatus.UNVERIFIED if quality == "unverified" else SensorStatus.INVALID
            freshness = compute_sensor_freshness(existing.observed_at if existing else None, now)
            accepted_source = existing.source if existing else source
            accepted_observed_at = existing.observed_at if existing else None
            accepted_value = existing.value if existing else None
            accepted_property = observation.source_property if isinstance(observation, TemperatureDecodeResult) else None
            accepted_scale = observation.scale if isinstance(observation, TemperatureDecodeResult) else "unknown"
            accepted_raw_type = observation.raw_type if isinstance(observation, TemperatureDecodeResult) else "unsupported"
            accepted = False

        self._readings[sensor_id] = SensorTelemetryReading(
            sensor_id=sensor_id,
            display_name=display_name,
            description=desc,
            metric=SensorMetric.WATER_TEMPERATURE,
            value=accepted_value,
            unit="celsius",
            observed_at=accepted_observed_at,
            source=accepted_source,
            freshness=freshness,
            status=status,
            communication_status=communication_status,
            source_property=accepted_property,
            scale=accepted_scale,
            raw_type=accepted_raw_type,
        )
        return accepted

    def update_energy_reading(
        self,
        sensor_id: str,
        display_name: str,
        energy_kwh: float,
        power_w: float | None = None,
        description: str = "",
        observed_at: "datetime | None" = None,
        source: str = "tuya",
    ) -> None:
        """Update or create an energy meter reading."""
        now = observed_at if observed_at is not None else datetime.now(timezone.utc)
        desc = description or (self._readings.get(sensor_id).description if self._readings.get(sensor_id) else "")
        display_val = f"{energy_kwh:.2f} kWh"
        if power_w is not None:
            display_val += f" ({power_w:.0f}W)"
        self._readings[sensor_id] = SensorTelemetryReading(
            sensor_id=sensor_id,
            display_name=display_name,
            description=desc,
            metric=SensorMetric.ENERGY,
            value=energy_kwh,
            unit="kwh",
            observed_at=now,
            source=source,
            freshness=SensorFreshness.FRESH,
            status=SensorStatus.VALID,
            communication_status="active",
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_reading(self, sensor_id: str) -> SensorTelemetryReading | None:
        """Get a single reading by sensor_id."""
        return self._readings.get(sensor_id)

    def get_all_readings(self) -> list[SensorTelemetryReading]:
        """Get all readings with current freshness computed."""
        now = datetime.now(timezone.utc)
        result: list[SensorTelemetryReading] = []
        for reading in self._readings.values():
            freshness = compute_sensor_freshness(reading.observed_at, now)
            status = (
                self._status_from_freshness(freshness)
                if reading.status == SensorStatus.VALID
                else reading.status
            )
            result.append(SensorTelemetryReading(
                sensor_id=reading.sensor_id,
                display_name=reading.display_name,
                description=reading.description,
                metric=reading.metric,
                value=reading.value,
                unit=reading.unit,
                observed_at=reading.observed_at,
                source=reading.source,
                freshness=freshness,
                status=status,
                communication_status=reading.communication_status,
                source_property=reading.source_property,
                scale=reading.scale,
                raw_type=reading.raw_type,
            ))
        return result

    def get_all_readings_dict(self) -> list[dict[str, Any]]:
        """Get all readings as safe dicts for the web read model."""
        return [
            {
                "sensor_id": r.sensor_id,
                "display_name": r.display_name,
                "description": r.description,
                "device_type": "thermo",
                "metric": r.metric.value,
                "value": r.value,
                "unit": r.unit,
                "observed_at": r.observed_at.isoformat() if r.observed_at else None,
                "source": r.source,
                "freshness": r.freshness.value,
                "status": r.status.value,
                "communication_status": r.communication_status,
            }
            for r in self.get_all_readings()
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_finite_normalized(value: object | None) -> bool:
        return (
            type(value) in {int, float}
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )

    @staticmethod
    def _status_from_freshness(freshness: SensorFreshness) -> SensorStatus:
        if freshness == SensorFreshness.FRESH:
            return SensorStatus.VALID
        if freshness == SensorFreshness.STALE:
            return SensorStatus.STALE
        return SensorStatus.UNAVAILABLE
