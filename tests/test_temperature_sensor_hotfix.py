from __future__ import annotations

import math
import ast
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.devices.sensor_telemetry import (
    SensorStatus,
    TemperatureDecodeResult,
    decode_water_temperature,
)
from app.service.telemetry_registry import TelemetryRegistry
from app.tuya.status_updater_async import TuyaStatusUpdaterAsync
from shared_state.shared_state import shared_state


class FakeSensor:
    id = "sensor-1"
    name = "Pond probe"
    desc = "Water temperature"
    enabled = True


def observation(value: float, property_name: str = "temp_current") -> TemperatureDecodeResult:
    return TemperatureDecodeResult(
        value=value,
        raw_value=value,
        source_property=property_name,
        scale="celsius",
        raw_type="float",
        quality="verified",
    )


class TemperatureDecoderTests(unittest.TestCase):
    def test_exact_verified_table_and_order(self):
        cases = [
            ({"temp_current": 60}, (6.0, "temp_current", "tenths", "int", "verified")),
            ({"temp_current": 91}, (9.1, "temp_current", "tenths", "int", "verified")),
            ({"temp_current": 237}, (23.7, "temp_current", "tenths", "int", "verified")),
            ({"va_temperature": 278}, (27.8, "va_temperature", "tenths", "int", "verified")),
            ({"temp_current": 10.5}, (10.5, "temp_current", "celsius", "float", "verified")),
            ({"va_temperature": -5.0}, (-5.0, "va_temperature", "celsius", "float", "verified")),
            ({"temp_humidity_way_1": "x t1=19.9"}, (19.9, "temp_humidity_way_1", "celsius", "string", "verified")),
        ]
        for payload, expected in cases:
            result = decode_water_temperature(payload)
            self.assertEqual((result.value, result.source_property, result.scale, result.raw_type, result.quality), expected)

        self.assertEqual(decode_water_temperature({"temp": 60, "va_temperature": 278}).value, 27.8)
        self.assertEqual(decode_water_temperature({"temp": 60, "temp_current": None, "va_temperature": 278}).value, 27.8)
        self.assertEqual(decode_water_temperature({"temp_current": 0}).value, 0.0)
        self.assertEqual(decode_water_temperature({"temp_current": -5}).value, -0.5)
        self.assertEqual(decode_water_temperature({"temp_current": -50}).value, -5.0)

    def test_unknown_and_invalid_metadata_is_exact(self):
        for prop in ("temp", "temperature", "temp_value"):
            for raw, raw_type in ((60, "int"), (10.5, "float")):
                result = decode_water_temperature({prop: raw})
                self.assertEqual((result.value, result.scale, result.raw_type, result.quality), (None, "unknown", raw_type, "unverified"))
                self.assertIs(result.raw_value, raw)

        invalid_cases = [
            ({"temp_current": None}, "missing", None),
            ({"temp_current": True}, "bool", None),
            ({"temp_current": float("nan")}, "non_finite", None),
            ({"temp_current": float("inf")}, "non_finite", None),
            ({"temp_current": "23.7"}, "malformed", "23.7"),
            ({"temp_current": Decimal("23.7")}, "unsupported", None),
            ({"temp_humidity_way_1": "t1:23.7"}, "malformed", "t1:23.7"),
            ({"temp_humidity_way_1": 23}, "unsupported", None),
            ({"other": 23}, "unsupported", None),
            ({}, "missing", None),
        ]
        for payload, raw_type, raw_value in invalid_cases:
            result = decode_water_temperature(payload)
            self.assertEqual((result.value, result.source_property, result.scale, result.raw_type, result.quality), (None, next(iter(payload), None) if next(iter(payload), None) in {"temp_current", "temp", "temperature", "temp_value", "va_temperature", "temp_humidity_way_1"} else None, "unknown", raw_type, "invalid"))
            self.assertEqual(result.raw_value, raw_value)

        class IntSubclass(int):
            pass

        self.assertEqual(decode_water_temperature({"temp_current": IntSubclass(237)}).raw_type, "unsupported")


class RegistryAndUpdaterTests(unittest.TestCase):
    def setUp(self):
        shared_state.clear()

    def tearDown(self):
        shared_state.clear()

    def test_registry_never_double_normalizes(self):
        registry = TelemetryRegistry()
        at = datetime.now(timezone.utc)
        for value in (10.5, 19.9, 23.7, 27.8):
            self.assertTrue(registry.update_water_temperature("s", "Probe", observation(value), observed_at=at))
            self.assertEqual(registry.get_reading("s").value, value)

    def test_updater_publishes_only_verified_normalized_provenance(self):
        registry = TelemetryRegistry()
        updater = TuyaStatusUpdaterAsync(telemetry_registry=registry)
        now = datetime.now(timezone.utc)
        updater._update_sensor_telemetry(FakeSensor(), {"temp_current": 237}, now, "active")
        reading = registry.get_reading("sensor-1_water_temp")
        self.assertEqual(reading.value, 23.7)
        self.assertEqual(shared_state["water_temp"], 23.7)
        self.assertEqual(shared_state["water_temp_observation_sensor-1_water_temp"]["scale"], "tenths")
        self.assertEqual(shared_state["water_temp_observation_sensor-1_water_temp"]["raw_type"], "int")

        updater._update_sensor_telemetry(FakeSensor(), {"temp": 91}, now + timedelta(seconds=1), "active")
        self.assertEqual(registry.get_reading("sensor-1_water_temp").status, SensorStatus.UNVERIFIED)
        self.assertEqual(registry.get_reading("sensor-1_water_temp").value, 23.7)
        self.assertEqual(shared_state["water_temp"], 23.7)

        shared_state.pop("water_temp_observation_sensor-1_water_temp")
        updater._update_sensor_telemetry(FakeSensor(), {"temp_current": "23.7"}, now + timedelta(seconds=2), "active")
        self.assertEqual(registry.get_reading("sensor-1_water_temp").status, SensorStatus.INVALID)
        self.assertNotIn("water_temp_observation_sensor-1_water_temp", shared_state)
        self.assertEqual(shared_state["water_temp"], 23.7)

    def test_quality_statuses_preserve_last_observation_and_descriptor_state(self):
        registry = TelemetryRegistry()
        registry.register_sensor_descriptor("s", "Probe")
        descriptor = registry.get_all_readings_dict()[0]
        self.assertEqual((descriptor["value"], descriptor["observed_at"], descriptor["freshness"], descriptor["status"]), (None, None, "unavailable", "unavailable"))

        at = datetime.now(timezone.utc) - timedelta(seconds=30)
        registry.update_water_temperature("s", "Probe", observation(12.34), observed_at=at)
        registry.update_water_temperature(
            "s", "Probe",
            TemperatureDecodeResult(None, 60, "temp", "unknown", "int", "unverified"),
            observed_at=at + timedelta(seconds=1),
        )
        reading = registry.get_all_readings_dict()[0]
        self.assertEqual((reading["value"], reading["observed_at"], reading["status"]), (12.3, at.isoformat(), "unverified"))

        registry.update_water_temperature(
            "s", "Probe",
            TemperatureDecodeResult(None, "bad", "temp_current", "unknown", "malformed", "invalid"),
            observed_at=at + timedelta(seconds=2),
        )
        self.assertEqual(registry.get_all_readings_dict()[0]["status"], "invalid")

    def test_freshness_status_for_verified_reading_decays(self):
        registry = TelemetryRegistry()
        registry.update_water_temperature("s", "Probe", observation(12.3), observed_at=datetime.now(timezone.utc) - timedelta(seconds=200))
        reading = registry.get_all_readings_dict()[0]
        self.assertEqual((reading["freshness"], reading["status"]), ("stale", "stale"))
        registry.update_water_temperature("s", "Probe", observation(12.3), observed_at=datetime.now(timezone.utc) - timedelta(seconds=400))
        reading = registry.get_all_readings_dict()[0]
        self.assertEqual((reading["freshness"], reading["status"]), ("unavailable", "unavailable"))


class ProvenanceProviderTests(unittest.TestCase):
    def setUp(self):
        shared_state.clear()

    def tearDown(self):
        shared_state.clear()

    def test_provider_accepts_only_validated_original_timestamp_cache(self):
        from pathlib import Path

        from app.devices.sensor_telemetry import compute_sensor_freshness

        source = Path(__file__).resolve().parents[1].joinpath("run.py").read_text()
        tree = ast.parse(source)
        selected = []
        wanted_functions = {"_parse_aware_iso_timestamp", "_validate_water_temp_provenance", "_make_sensors_provider"}
        wanted_assignments = {"_WATER_TEMP_CACHE_PROPERTIES", "_WATER_TEMP_CACHE_SCALES", "_WATER_TEMP_CACHE_RAW_TYPES"}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
                selected.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in wanted_assignments
                for target in node.targets
            ):
                selected.append(node)
        helper_namespace = {
            "Any": object,
            "datetime": datetime,
            "timezone": timezone,
            "math": math,
            "compute_sensor_freshness": compute_sensor_freshness,
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "run.py", "exec"), helper_namespace)
        _make_sensors_provider = helper_namespace["_make_sensors_provider"]
        _validate_water_temp_provenance = helper_namespace["_validate_water_temp_provenance"]

        now = datetime.now(timezone.utc).replace(microsecond=0)
        valid = {
            "sensor_id": "s_water_temp", "value": 23.7, "observed_at": now.isoformat(),
            "source": "tuya", "source_property": "temp_current", "scale": "tenths",
            "raw_type": "int", "status": "valid",
        }
        self.assertIsNotNone(_validate_water_temp_provenance(valid, "s_water_temp", now))
        for field, bad in {
            "sensor_id": "other_water_temp", "value": True,
            "observed_at": "2026-11-13T12:00:00", "source": "shared_state",
            "source_property": "temp", "scale": "celsius", "raw_type": "string",
            "status": "cached",
        }.items():
            candidate = dict(valid)
            candidate[field] = bad
            self.assertIsNone(_validate_water_temp_provenance(candidate, "s_water_temp", now), field)

        registry = TelemetryRegistry()
        registry.register_sensor_descriptor("s_water_temp", "Probe")
        shared_state["water_temp"] = 99.0
        shared_state["water_temp_s"] = 88.0
        shared_state["water_temp_observation_s_water_temp"] = valid
        result = _make_sensors_provider(registry)()
        self.assertEqual((result[0]["value"], result[0]["source"], result[0]["status"], result[0]["observed_at"]), (23.7, "shared_state_cache", "cached", now.isoformat()))

        registry.update_water_temperature("s_water_temp", "Probe", observation(22.0), observed_at=now - timedelta(seconds=200))
        shared_state["water_temp_observation_s_water_temp"] = {**valid, "observed_at": (now - timedelta(seconds=201)).isoformat()}
        self.assertEqual(_make_sensors_provider(registry)()[0]["value"], 22.0)


if __name__ == "__main__":
    unittest.main()
