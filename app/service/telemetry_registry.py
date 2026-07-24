"""In-memory telemetry registry for dessmonitor.

Owned by the runtime composition root (run.py).  Provides typed,
deterministic read-only snapshots of sensor telemetry readings.

Rebuilt on process restart.  No persistence, no network, no hardware.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.devices.sensor_telemetry import (
    SensorTelemetryReading,
    SensorMetric,
    SensorFreshness,
    SensorStatus,
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
        raw_value: Any,
        description: str = "",
        observed_at: datetime | None = None,
        source: str = "tuya",
        communication_status: str = "active",
    ) -> None:
        """Update or create a water temperature reading.

        Normalizes the raw value using the mapping's scale and offset.
        Accepts: int, float.
        Rejects: None, bool, str, NaN, infinity, impossible values.
        """
        now = observed_at if observed_at is not None else datetime.now(timezone.utc)

        normalized = self._normalize_temperature(raw_value)
        if normalized is None:
            # Invalid or missing — preserve prior reading, do not refresh observed_at
            existing = self._readings.get(sensor_id)
            if existing is not None:
                freshness = compute_sensor_freshness(existing.observed_at, now)
                status = self._status_from_freshness(freshness)
                self._readings[sensor_id] = SensorTelemetryReading(
                    sensor_id=existing.sensor_id,
                    display_name=existing.display_name,
                    description=existing.description,
                    metric=SensorMetric.WATER_TEMPERATURE,
                    value=existing.value,
                    unit="celsius",
                    observed_at=existing.observed_at,
                    source=existing.source,
                    freshness=freshness,
                    status=status,
                    communication_status=communication_status,
                )
            return

        freshness = SensorFreshness.FRESH
        status = SensorStatus.VALID
        desc = description or (self._readings.get(sensor_id).description if self._readings.get(sensor_id) else "")
        self._readings[sensor_id] = SensorTelemetryReading(
            sensor_id=sensor_id,
            display_name=display_name,
            description=desc,
            metric=SensorMetric.WATER_TEMPERATURE,
            value=normalized,
            unit="celsius",
            observed_at=now,
            source=source,
            freshness=freshness,
            status=status,
            communication_status=communication_status,
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
            status = self._status_from_freshness(freshness)
            result.append(SensorTelemetryReading(
                sensor_id=reading.sensor_id,
                display_name=reading.display_name,
                metric=reading.metric,
                value=reading.value,
                unit=reading.unit,
                observed_at=reading.observed_at,
                source=reading.source,
                freshness=freshness,
                status=status,
                communication_status=reading.communication_status,
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
    def _normalize_temperature(raw_value: Any) -> float | None:
        """Normalize a raw temperature value to Celsius.

        Accepts:
        - int or float directly (already Celsius).
        - int representing tenths of a degree (e.g., 225 -> 22.5).
          Detected when the value is an int > 100 (likely tenths).

        Rejects:
        - None, bool, str, NaN, infinity, negative impossible values.
        """
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return None
        if isinstance(raw_value, str):
            return None
        if isinstance(raw_value, (int, float)):
            if raw_value != raw_value:  # NaN check
                return None
            if raw_value == float("inf") or raw_value == float("-inf"):
                return None
            # If int > 100, likely tenths of a degree (e.g., 225 = 22.5 C)
            if isinstance(raw_value, int) and raw_value > 100:
                return round(float(raw_value) / 10.0, 1)
            return round(float(raw_value), 1)
        return None

    @staticmethod
    def _status_from_freshness(freshness: SensorFreshness) -> SensorStatus:
        if freshness == SensorFreshness.FRESH:
            return SensorStatus.VALID
        if freshness == SensorFreshness.STALE:
            return SensorStatus.STALE
        return SensorStatus.UNAVAILABLE
