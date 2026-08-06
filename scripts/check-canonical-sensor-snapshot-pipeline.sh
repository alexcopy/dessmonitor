#!/usr/bin/env bash
# check-canonical-sensor-snapshot-pipeline.sh
# Validate PR 0034j sensor snapshot pipeline end-to-end.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -n "${PYTHON_BIN:-}" ] && [ -x "$PYTHON_BIN" ]; then
    PYTHON="$PYTHON_BIN"
elif [ -x "$PROJECT_DIR/.venv3/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv3/bin/python3"
elif [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    echo "ERROR: No Python interpreter found" >&2
    exit 127
fi

echo "Using Python interpreter: $PYTHON"
echo "=== PR 0034j canonical sensor snapshot pipeline check ==="

"$PYTHON" - "$PROJECT_DIR" <<'PYEOF'
import sys, os
sys.path.insert(0, sys.argv[1])

errors = []
test_num = 0

def ok(msg=""):
    global test_num
    test_num += 1
    print(f"  [{test_num}] {msg} ... OK")
    return True

def fail(msg=""):
    global test_num, errors
    test_num += 1
    print(f"  [{test_num}] {msg} ... FAIL")
    errors.append(f"  [{test_num}] {msg}")

# ================================================================
# PART 1: SensorReadSnapshot contract
# ================================================================

from app.control.control_state_snapshot import SensorReadSnapshot

# [1] SensorReadSnapshot exists and is a dataclass
if hasattr(SensorReadSnapshot, "sensor_id"):
    ok("SensorReadSnapshot dataclass exists")
else:
    fail("SensorReadSnapshot missing")

# [2] All required fields present
s = SensorReadSnapshot(
    sensor_id="test-1",
    display_name="Test Sensor",
    description="A test sensor",
    device_type="thermo",
    metric="water_temperature",
    value=20.9,
    unit="celsius",
    observed_at="2026-07-24T08:00:00+00:00",
    source="tuya",
    freshness="fresh",
    status="valid",
    communication_status="active",
)
if s.sensor_id == "test-1" and s.value == 20.9 and s.description == "A test sensor":
    ok("SensorReadSnapshot all fields set correctly")
else:
    fail(f"SensorReadSnapshot fields: {s}")

# [3] Null value and observed_at
s2 = SensorReadSnapshot(sensor_id="test-2", display_name="Empty")
if s2.value is None and s2.observed_at is None:
    ok("SensorReadSnapshot null value and observed_at")
else:
    fail(f"Null fields: value={s2.value} observed_at={s2.observed_at}")

# [4] Defaults
if s2.freshness == "" and s2.status == "" and s2.device_type == "":
    ok("SensorReadSnapshot defaults empty string")
else:
    fail(f"Defaults: freshness={s2.freshness} status={s2.status}")

# ================================================================
# PART 2: TelemetryRegistry description preservation
# ================================================================

from app.service.telemetry_registry import TelemetryRegistry

reg = TelemetryRegistry()
reg.register_sensor_descriptor(
    sensor_id="s1",
    display_name="watertemp",
    description="Water Thermometer",
)

# [5] Descriptor description preserved in get_all_readings
readings = reg.get_all_readings()
if len(readings) == 1 and readings[0].description == "Water Thermometer":
    ok("get_all_readings preserves description")
else:
    fail(f"get_all_readings description: {readings[0].description if readings else 'empty'}")

# [6] Descriptor description preserved in get_all_readings_dict
dicts = reg.get_all_readings_dict()
if len(dicts) == 1 and dicts[0].get("description") == "Water Thermometer":
    ok("get_all_readings_dict preserves description")
else:
    fail(f"get_all_readings_dict description: {dicts}")

# [7] Descriptor has null value
if dicts[0].get("value") is None:
    ok("Descriptor value is None")
else:
    fail(f"Descriptor value: {dicts[0].get('value')}")

# [8] Descriptor freshness is unavailable
if dicts[0].get("freshness") == "unavailable":
    ok("Descriptor freshness is unavailable")
else:
    fail(f"Descriptor freshness: {dicts[0].get('freshness')}")

# ================================================================
# PART 3: _parse_sensors helper
# ================================================================

from app.control.runtime_snapshot_adapter import _parse_sensors

# [9] _parse_sensors exists
if callable(_parse_sensors):
    ok("_parse_sensors function exists")
else:
    fail("_parse_sensors not callable")

# [10] Parse valid sensor dict
parsed = _parse_sensors([dicts[0]])
if len(parsed) == 1 and parsed[0].sensor_id == "s1":
    ok("_parse_sensors parses valid dict")
else:
    fail(f"_parse_sensors: {parsed}")

# [10b] Load-only observed_power_w does not make sensor parsing drop entries
sensor_with_load_field = dict(dicts[0])
sensor_with_load_field["observed_power_w"] = 12.3
parsed_with_load_field = _parse_sensors([sensor_with_load_field])
if len(parsed_with_load_field) == 1 and parsed_with_load_field[0].sensor_id == "s1":
    ok("_parse_sensors ignores load-only observed_power_w")
else:
    fail(f"_parse_sensors with observed_power_w: {parsed_with_load_field}")

# [11] Parse preserves description
if parsed[0].description == "Water Thermometer":
    ok("_parse_sensors preserves description")
else:
    fail(f"_parse_sensors description: {parsed[0].description}")

# [12] Parse preserves null value
if parsed[0].value is None:
    ok("_parse_sensors preserves null value")
else:
    fail(f"_parse_sensors value: {parsed[0].value}")

# [13] Parse None returns empty tuple
parsed_none = _parse_sensors(None)
if len(parsed_none) == 0:
    ok("_parse_sensors(None) returns empty")
else:
    fail(f"_parse_sensors(None): {parsed_none}")

# [14] Parse empty list returns empty
parsed_empty = _parse_sensors([])
if len(parsed_empty) == 0:
    ok("_parse_sensors([]) returns empty")
else:
    fail(f"_parse_sensors([]): {parsed_empty}")

# [15] Parse malformed entry safely
parsed_bad = _parse_sensors([{"no_sensor_id": True}])
if len(parsed_bad) == 1 and parsed_bad[0].sensor_id == "":
    ok("_parse_sensors handles malformed entry safely")
else:
    fail(f"_parse_sensors malformed: {parsed_bad}")

# ================================================================
# PART 4: RuntimeControlSnapshotAdapterInput sensors field
# ================================================================

from app.control.runtime_snapshot_adapter import RuntimeControlSnapshotAdapterInput

# [16] Adapter input has sensors field
ai = RuntimeControlSnapshotAdapterInput()
if hasattr(ai, "sensors"):
    ok("RuntimeControlSnapshotAdapterInput has sensors field")
else:
    fail("No sensors field on adapter input")

# [17] Default sensors is empty tuple
if ai.sensors == ():
    ok("Default sensors is empty tuple")
else:
    fail(f"Default sensors: {ai.sensors}")

# ================================================================
# PART 5: ControlStateSnapshotInput sensors field
# ================================================================

from app.control.control_state_snapshot import ControlStateSnapshotInput

# [18] Snapshot input has sensors field
si = ControlStateSnapshotInput()
if hasattr(si, "sensors"):
    ok("ControlStateSnapshotInput has sensors field")
else:
    fail("No sensors field on snapshot input")

# [19] Default sensors is empty tuple
if si.sensors == ():
    ok("Default sensors is empty tuple")
else:
    fail(f"Default sensors: {si.sensors}")

# ================================================================
# PART 6: ControlStateSnapshot sensors field
# ================================================================

from app.control.control_state_snapshot import ControlStateSnapshot

# [20] Snapshot has sensors field
snap = ControlStateSnapshot()
if hasattr(snap, "sensors"):
    ok("ControlStateSnapshot has sensors field")
else:
    fail("No sensors field on snapshot")

# [21] Default sensors is empty tuple
if snap.sensors == ():
    ok("Default sensors is empty tuple")
else:
    fail(f"Default sensors: {snap.sensors}")

# ================================================================
# PART 7: End-to-end pipeline — configured descriptor
# ================================================================

from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state

# Build a runtime_state dict as the real provider would
runtime_state = {
    "snapshot_id": "test-snap-1",
    "created_at": "2026-07-24T08:00:00+00:00",
    "loads": [],
    "sensors": [
        {
            "sensor_id": "s1_water_temp",
            "display_name": "watertemp",
            "description": "Water Thermometer",
            "device_type": "thermo",
            "metric": "water_temperature",
            "value": None,
            "unit": "celsius",
            "observed_at": None,
            "source": "tuya",
            "freshness": "unavailable",
            "status": "unavailable",
            "communication_status": "active",
        }
    ],
}

# [22] Pipeline produces snapshot with sensors
result = build_control_state_snapshot_from_runtime_state(runtime_state)
if result is not None and hasattr(result, "sensors"):
    ok("Pipeline produces snapshot with sensors")
else:
    fail("Pipeline produced None or no sensors")

# [23] Sensors tuple has one entry
if len(result.sensors) == 1:
    ok("Sensors tuple has one entry")
else:
    fail(f"Sensors count: {len(result.sensors)}")

# [24] Value is None
if result.sensors[0].value is None:
    ok("Sensor value is None")
else:
    fail(f"Sensor value: {result.sensors[0].value}")

# [25] Description preserved
if result.sensors[0].description == "Water Thermometer":
    ok("Sensor description preserved")
else:
    fail(f"Sensor description: {result.sensors[0].description}")

# [26] device_type preserved
if result.sensors[0].device_type == "thermo":
    ok("Sensor device_type preserved")
else:
    fail(f"Sensor device_type: {result.sensors[0].device_type}")

# [27] freshness preserved
if result.sensors[0].freshness == "unavailable":
    ok("Sensor freshness preserved")
else:
    fail(f"Sensor freshness: {result.sensors[0].freshness}")

# ================================================================
# PART 8: End-to-end pipeline — valid telemetry
# ================================================================

runtime_state2 = {
    "snapshot_id": "test-snap-2",
    "created_at": "2026-07-24T08:00:00+00:00",
    "loads": [],
    "sensors": [
        {
            "sensor_id": "s1_water_temp",
            "display_name": "watertemp",
            "description": "Water Thermometer",
            "device_type": "thermo",
            "metric": "water_temperature",
            "value": 20.9,
            "unit": "celsius",
            "observed_at": "2026-07-24T08:00:00+00:00",
            "source": "tuya",
            "freshness": "fresh",
            "status": "valid",
            "communication_status": "active",
        }
    ],
}

result2 = build_control_state_snapshot_from_runtime_state(runtime_state2)

# [28] Value preserved
if result2 is not None and result2.sensors[0].value == 20.9:
    ok("Sensor value 20.9 preserved through pipeline")
else:
    val = result2.sensors[0].value if result2 is not None else "None"
    fail(f"Sensor value: {val}")

# [29] No second normalization
if result2 is not None and result2.sensors[0].value == 20.9:
    ok("No second normalization (value still 20.9)")
else:
    fail("Value changed during pipeline")

# ================================================================
# PART 9: Load metadata preservation
# ================================================================

runtime_state3 = {
    "snapshot_id": "test-snap-3",
    "created_at": "2026-07-24T08:00:00+00:00",
    "loads": [
        {
            "load_id": "load-1",
            "display_name": "dush_heater",
            "description": "Dush Heater in House",
            "device_type": "switch",
            "configured_load_watts": 2000,
            "currently_on": True,
            "controllable": True,
            "is_life_support": False,
            "roles": ("switch",),
            "status": "healthy",
            "notes": "",
            "observed_state": "on",
            "observed_at": "2026-07-24T08:00:00+00:00",
            "observation_source": "tuya",
            "freshness": "fresh",
            "mapping_status": "valid",
            "startup_reset_result": "confirmed_off",
            "enabled": True,
            "communication_status": "active",
        }
    ],
}

result3 = build_control_state_snapshot_from_runtime_state(runtime_state3)

# [30] Load description preserved
if result3 is not None and result3.loads[0].description == "Dush Heater in House":
    ok("Load description preserved")
else:
    desc = result3.loads[0].description if result3 is not None else "None"
    fail(f"Load description: {desc}")

# [31] Load device_type preserved
if result3 is not None and result3.loads[0].device_type == "switch":
    ok("Load device_type preserved")
else:
    dt = result3.loads[0].device_type if result3 is not None else "None"
    fail(f"Load device_type: {dt}")

# [32] Load mapping_status preserved
if result3 is not None and result3.loads[0].mapping_status == "valid":
    ok("Load mapping_status preserved")
else:
    ms = result3.loads[0].mapping_status if result3 is not None else "None"
    fail(f"Load mapping_status: {ms}")

# [33] Load startup_reset_result preserved
if result3 is not None and result3.loads[0].startup_reset_result == "confirmed_off":
    ok("Load startup_reset_result preserved")
else:
    srr = result3.loads[0].startup_reset_result if result3 is not None else "None"
    fail(f"Load startup_reset_result: {srr}")

# [34] Load enabled preserved
if result3 is not None and result3.loads[0].enabled is True:
    ok("Load enabled preserved")
else:
    en = result3.loads[0].enabled if result3 is not None else "None"
    fail(f"Load enabled: {en}")

# [35] Load communication_status preserved
if result3 is not None and result3.loads[0].communication_status == "active":
    ok("Load communication_status preserved")
else:
    cs = result3.loads[0].communication_status if result3 is not None else "None"
    fail(f"Load communication_status: {cs}")

# ================================================================
# PART 10: Status determination with sensors but no loads
# ================================================================

# [36] Snapshot with sensors but no loads is not UNKNOWN
if result2 is not None and result2.status.name != "UNKNOWN":
    ok("Snapshot with sensors but no loads is not UNKNOWN")
else:
    st = result2.status.name if result2 is not None else "None"
    fail(f"Status with sensors only: {st}")

# ================================================================
# PART 11: WebUiControlStateResponse serialization
# ================================================================

from app.control.web_ui_read_contract import (
    WebUiControlStateResponse,
    build_web_ui_control_state_response,
)

# [37] Response builds with sensors
response = build_web_ui_control_state_response(result2)
if response is not None and response.snapshot is not None:
    ok("WebUiControlStateResponse built with sensors")
else:
    fail("Response is None")

# [38] Sensors in response snapshot
if response is not None and response.snapshot is not None and len(response.snapshot.sensors) == 1:
    ok("Response snapshot has one sensor")
else:
    cnt = len(response.snapshot.sensors) if response and response.snapshot else 0
    fail(f"Response sensor count: {cnt}")

# ================================================================
# PART 12: No raw Tuya IDs exposed
# ================================================================

import json
from dataclasses import asdict

# [39] No tuya_device_id in sensor snapshot
s_dict = asdict(s)
if "tuya_device_id" not in str(s_dict):
    ok("No tuya_device_id in sensor snapshot")
else:
    fail("tuya_device_id exposed")

# [40] No control_key or state_key
if "control_key" not in str(s_dict) and "state_key" not in str(s_dict):
    ok("No control_key or state_key in sensor snapshot")
else:
    fail("control_key or state_key exposed")

# ================================================================
# PART 13: RuntimeLoadState extended fields
# ================================================================

from app.control.runtime_snapshot_adapter import RuntimeLoadState

# [41] RuntimeLoadState has description field
rls = RuntimeLoadState()
if hasattr(rls, "description"):
    ok("RuntimeLoadState has description field")
else:
    fail("No description on RuntimeLoadState")

# [42] RuntimeLoadState has device_type field
if hasattr(rls, "device_type"):
    ok("RuntimeLoadState has device_type field")
else:
    fail("No device_type on RuntimeLoadState")

# [43] RuntimeLoadState has mapping_status field
if hasattr(rls, "mapping_status"):
    ok("RuntimeLoadState has mapping_status field")
else:
    fail("No mapping_status on RuntimeLoadState")

# [44] RuntimeLoadState has startup_reset_result field
if hasattr(rls, "startup_reset_result"):
    ok("RuntimeLoadState has startup_reset_result field")
else:
    fail("No startup_reset_result on RuntimeLoadState")

# [45] RuntimeLoadState has enabled field
if hasattr(rls, "enabled"):
    ok("RuntimeLoadState has enabled field")
else:
    fail("No enabled on RuntimeLoadState")

# [46] RuntimeLoadState has communication_status field
if hasattr(rls, "communication_status"):
    ok("RuntimeLoadState has communication_status field")
else:
    fail("No communication_status on RuntimeLoadState")

# ================================================================
# PART 14: LoadControlSnapshot extended fields
# ================================================================

from app.control.control_state_snapshot import LoadControlSnapshot

# [47] LoadControlSnapshot has description field
lcs = LoadControlSnapshot()
if hasattr(lcs, "description"):
    ok("LoadControlSnapshot has description field")
else:
    fail("No description on LoadControlSnapshot")

# [48] LoadControlSnapshot has device_type field
if hasattr(lcs, "device_type"):
    ok("LoadControlSnapshot has device_type field")
else:
    fail("No device_type on LoadControlSnapshot")

# [49] LoadControlSnapshot has mapping_status field
if hasattr(lcs, "mapping_status"):
    ok("LoadControlSnapshot has mapping_status field")
else:
    fail("No mapping_status on LoadControlSnapshot")

# [50] LoadControlSnapshot has startup_reset_result field
if hasattr(lcs, "startup_reset_result"):
    ok("LoadControlSnapshot has startup_reset_result field")
else:
    fail("No startup_reset_result on LoadControlSnapshot")

# [51] LoadControlSnapshot has enabled field
if hasattr(lcs, "enabled"):
    ok("LoadControlSnapshot has enabled field")
else:
    fail("No enabled on LoadControlSnapshot")

# [52] LoadControlSnapshot has communication_status field
if hasattr(lcs, "communication_status"):
    ok("LoadControlSnapshot has communication_status field")
else:
    fail("No communication_status on LoadControlSnapshot")

# ================================================================
# PART 15: _parse_loads extended for metadata
# ================================================================

from app.web_control_state_provider import _parse_loads

# [53] _parse_loads preserves description
parsed_loads = _parse_loads(runtime_state3.get("loads"))
if len(parsed_loads) == 1 and parsed_loads[0].description == "Dush Heater in House":
    ok("_parse_loads preserves description")
else:
    desc = parsed_loads[0].description if parsed_loads else "None"
    fail(f"_parse_loads description: {desc}")

# [54] _parse_loads preserves device_type
if parsed_loads[0].device_type == "switch":
    ok("_parse_loads preserves device_type")
else:
    fail(f"_parse_loads device_type: {parsed_loads[0].device_type}")

# [55] _parse_loads preserves mapping_status
if parsed_loads[0].mapping_status == "valid":
    ok("_parse_loads preserves mapping_status")
else:
    fail(f"_parse_loads mapping_status: {parsed_loads[0].mapping_status}")

# [56] _parse_loads preserves startup_reset_result
if parsed_loads[0].startup_reset_result == "confirmed_off":
    ok("_parse_loads preserves startup_reset_result")
else:
    fail(f"_parse_loads startup_reset_result: {parsed_loads[0].startup_reset_result}")

# [57] _parse_loads preserves enabled
if parsed_loads[0].enabled is True:
    ok("_parse_loads preserves enabled")
else:
    fail(f"_parse_loads enabled: {parsed_loads[0].enabled}")

# [58] _parse_loads preserves communication_status
if parsed_loads[0].communication_status == "active":
    ok("_parse_loads preserves communication_status")
else:
    fail(f"_parse_loads communication_status: {parsed_loads[0].communication_status}")

# [59] _parse_loads preserves observed_power_w
runtime_state4 = {
    "loads": [
        {
            "load_id": "load-2",
            "display_name": "metered_load",
            "configured_load_watts": 100,
            "observed_power_w": 42.5,
        }
    ],
}
parsed_metered_loads = _parse_loads(runtime_state4.get("loads"))
if len(parsed_metered_loads) == 1 and parsed_metered_loads[0].observed_power_w == 42.5:
    ok("_parse_loads preserves observed_power_w")
else:
    val = parsed_metered_loads[0].observed_power_w if parsed_metered_loads else "None"
    fail(f"_parse_loads observed_power_w: {val}")

# ================================================================
# PART 16: Tuya batch sensor parent ID compatibility
# ================================================================

from datetime import datetime, timezone
from types import SimpleNamespace
from app.tuya.status_updater_async import TuyaStatusUpdaterAsync
from shared_state.shared_state import shared_state

reg2 = TelemetryRegistry()
updater = TuyaStatusUpdaterAsync(telemetry_registry=reg2)
dev = SimpleNamespace(
    id="pond",
    name="pond thermo",
    desc="Pond thermometer",
    device_type="thermo",
    enabled=True,
)
shared_state.pop("water_temp", None)
updater._process_result(
    {
        "result": [
            {
                "device_id": "tuya-parent-1",
                "status": [{"code": "temp_current", "value": 237}],
            }
        ]
    },
    {"tuya-parent-1": [dev]},
    datetime.now(timezone.utc),
    0,
)
reading = reg2.get_reading("pond_water_temp")
if reading is not None and reading.value == 23.7 and shared_state.get("water_temp") == 23.7:
    ok("_process_result accepts Tuya device_id for sensor telemetry")
else:
    value = reading.value if reading is not None else "None"
    fail(f"_process_result device_id sensor value={value} shared_state={shared_state.get('water_temp')}")

# [62] Non-standard temp_humidity_way_1 updates only that sensor
reg3 = TelemetryRegistry()
updater2 = TuyaStatusUpdaterAsync(telemetry_registry=reg3)
cats = SimpleNamespace(
    id="cats",
    name="Cats_Home",
    desc="Cats home thermometer",
    device_type="thermo",
    enabled=True,
)
updater2._process_result(
    {
        "result": [
            {
                "device_id": "001TH0202",
                "status": [
                    {"code": "temp_humidity_way_1", "value": "...t1-26.52,c1=28.04"},
                ],
            }
        ]
    },
    {"001TH0202": [cats]},
    datetime.now(timezone.utc),
    0,
)
cats_reading = reg3.get_reading("cats_water_temp")
if cats_reading is not None and cats_reading.value == 26.5 and shared_state.get("water_temp_cats") == 26.5:
    ok("temp_humidity_way_1 parsed into per-device Cats_Home temperature")
else:
    value = cats_reading.value if cats_reading is not None else "None"
    fail(f"temp_humidity_way_1 value={value} shared_state={shared_state.get('water_temp_cats')}")

# [63] run.py sensors provider does not use generic water_temp fallback
run_path = os.path.join(sys.argv[1], "run.py")
with open(run_path, encoding="utf-8") as f:
    run_src = f.read()
if '_ss.get("water_temp")' not in run_src and "_ss.get('water_temp')" not in run_src:
    ok("Sensors provider avoids generic water_temp fallback collision")
else:
    fail("Sensors provider still uses generic water_temp fallback")

# ================================================================
# Results
# ================================================================
print()
if errors:
    print(f"=== FAIL: {len(errors)} check(s) failed ===")
    for e in errors:
        print(f"  FAILED: {e}")
    sys.exit(1)
else:
    print(f"=== PASS: All {test_num} sensor snapshot pipeline checks passed ===")
    sys.exit(0)
PYEOF

TEST_EXIT=$?
exit "$TEST_EXIT"
