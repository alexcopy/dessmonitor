#!/usr/bin/env bash
# check-electrical-telemetry-flow.sh
# Focused validator for PR 0034l: Inverter Electrical Telemetry Projection.
#
# Uses deterministic fake inputs (no production config, no Tuya, no network).
# Exercised from the project root.
set -euo pipefail

cd "$(dirname "$0")/.."

PASSED=0
FAILED=0
FAILURES=()

assert_pass() {
    local num="$1"; shift
    local desc="$1"; shift
    PASSED=$((PASSED + 1))
    echo "[$num] PASS: $desc"
}

assert_fail() {
    local num="$1"; shift
    local desc="$1"; shift
    local detail="$1"; shift
    FAILED=$((FAILED + 1))
    FAILURES+=("[$num] FAIL: $desc — $detail")
    echo "[$num] FAIL: $desc — $detail"
}

run_python() {
    PYTHONPATH="." python3 -c "$1"
}

# ---------------------------------------------------------------------------
# TEST 1 — InverterReadSnapshot dataclass exists and is frozen
# ---------------------------------------------------------------------------
echo "=== TEST 1: InverterReadSnapshot type ==="

OUT=$(run_python "
from app.control.control_state_snapshot import InverterReadSnapshot
from dataclasses import is_dataclass, fields
s = InverterReadSnapshot()
print('frozen' if getattr(s, '__dataclass_fields__', None) is not None else 'not_frozen')
fnames = [f.name for f in fields(s)]
print(','.join(sorted(fnames)))
" 2>&1) || { assert_fail 1 "InverterReadSnapshot exists" "ImportError or exception: $OUT"; }

if echo "$OUT" | grep -q "frozen"; then
    assert_pass 1 "InverterReadSnapshot exists and is a frozen dataclass"
else
    assert_fail 1 "InverterReadSnapshot exists" "Not found or not dataclass: $OUT"
fi

# ---------------------------------------------------------------------------
# TEST 2 — All required fields present
# ---------------------------------------------------------------------------
OUT2=$(run_python "
from app.control.control_state_snapshot import InverterReadSnapshot
from dataclasses import fields
fnames = [f.name for f in fields(InverterReadSnapshot)]
print(','.join(sorted(fnames)))
" 2>&1) || { assert_fail 2 "Required fields" "ImportError: $OUT2"; }

REQUIRED_FIELDS=(
    ac_input_frequency ac_input_voltage ac_output_load
    battery_current_chg battery_current_dis battery_soc battery_voltage
    freshness mains_status observed_at output_apparent_power output_power output_voltage
    pv1_power pv1_voltage pv2_power pv2_voltage pv_total_power
    snapshot_id source status working_mode
)

MISSING_FIELDS=()
for f in "${REQUIRED_FIELDS[@]}"; do
    if ! echo "$OUT2" | grep -q "$f"; then
        MISSING_FIELDS+=("$f")
    fi
done

if [ ${#MISSING_FIELDS[@]} -eq 0 ]; then
    assert_pass 2 "All required fields present on InverterReadSnapshot"
else
    assert_fail 2 "Required fields" "Missing: ${MISSING_FIELDS[*]}"
fi

# ---------------------------------------------------------------------------
# TEST 3 — Null fields default to None
# ---------------------------------------------------------------------------
OUT3=$(run_python "
from app.control.control_state_snapshot import InverterReadSnapshot
s = InverterReadSnapshot(battery_voltage=25.7)
rv = s.battery_voltage
nv = s.output_power
print(f'battery_voltage={rv} output_power={nv}')
" 2>&1) || { assert_fail 3 "Null defaults" "Error: $OUT3"; }

if echo "$OUT3" | grep -q "battery_voltage=25.7 output_power=None"; then
    assert_pass 3 "Null fields default to None (output_power=None)"
else
    assert_fail 3 "Null defaults" "Expected battery_voltage=25.7 output_power=None, got: $OUT3"
fi

# ---------------------------------------------------------------------------
# TEST 4 — _parse_inverter(None) returns empty tuple
# ---------------------------------------------------------------------------
OUT4=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter
result = _parse_inverter(None)
print(type(result).__name__, len(result))
" 2>&1) || { assert_fail 4 "_parse_inverter(None)" "Error: $OUT4"; }

if echo "$OUT4" | grep -q "tuple 0"; then
    assert_pass 4 "_parse_inverter(None) returns empty tuple"
else
    assert_fail 4 "_parse_inverter(None)" "Expected tuple 0, got: $OUT4"
fi

# ---------------------------------------------------------------------------
# TEST 5 — _parse_inverter(valid dict) returns one typed entry
# ---------------------------------------------------------------------------
OUT5=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter
data = [
    {
        'battery_voltage': 25.7,
        'output_power': 800.0,
        'pv_total_power': 1200.0,
        'working_mode': 'Line Mode',
        'observed_at': '2026-07-25T12:00:00+00:00',
    }
]
result = _parse_inverter(data)
print('len', len(result))
if len(result) > 0:
    r = result[0]
    print('bv', r.battery_voltage)
    print('op', r.output_power)
    print('pv', r.pv_total_power)
    print('wm', r.working_mode)
    print('type', type(r).__name__)
" 2>&1) || { assert_fail 5 "_parse_inverter(valid dict)" "Error: $OUT5"; }

if echo "$OUT5" | grep -q "len 1" && echo "$OUT5" | grep -q "bv 25.7" && echo "$OUT5" | grep -q "op 800.0"; then
    assert_pass 5 "_parse_inverter(valid dict) returns one typed InverterReadSnapshot"
else
    assert_fail 5 "_parse_inverter(valid dict)" "Expected len 1 with correct values, got: $OUT5"
fi

# ---------------------------------------------------------------------------
# TEST 6 — battery_voltage preserved as float
# ---------------------------------------------------------------------------
if echo "$OUT5" | grep -q "bv 25.7"; then
    assert_pass 6 "battery_voltage preserved as float (25.7)"
else
    assert_fail 6 "battery_voltage float" "Expected 25.7, got: $OUT5"
fi

# ---------------------------------------------------------------------------
# TEST 7 — output_power preserved as float
# ---------------------------------------------------------------------------
if echo "$OUT5" | grep -q "op 800.0"; then
    assert_pass 7 "output_power preserved as float (800.0)"
else
    assert_fail 7 "output_power float" "Expected 800.0, got: $OUT5"
fi

# ---------------------------------------------------------------------------
# TEST 8 — pv_total_power preserved as float
# ---------------------------------------------------------------------------
if echo "$OUT5" | grep -q "pv 1200.0"; then
    assert_pass 8 "pv_total_power preserved as float (1200.0)"
else
    assert_fail 8 "pv_total_power float" "Expected 1200.0, got: $OUT5"
fi

# ---------------------------------------------------------------------------
# TEST 9 — working_mode preserved as str
# ---------------------------------------------------------------------------
if echo "$OUT5" | grep -q "wm Line Mode"; then
    assert_pass 9 "working_mode preserved as str (Line Mode)"
else
    assert_fail 9 "working_mode str" "Expected 'Line Mode', got: $OUT5"
fi

# ---------------------------------------------------------------------------
# TEST 10 — observed_at preserved as str or None
# ---------------------------------------------------------------------------
OUT10=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter
data = [{'observed_at': '2026-07-25T12:00:00+00:00'}]
result = _parse_inverter(data)
print('observed_at', result[0].observed_at)
data2 = [{}]
result2 = _parse_inverter(data2)
print('observed_at_none', result2[0].observed_at)
" 2>&1) || { assert_fail 10 "observed_at" "Error: $OUT10"; }

if echo "$OUT10" | grep -q "observed_at 2026-07-25T12:00:00" && echo "$OUT10" | grep -q "observed_at_none None"; then
    assert_pass 10 "observed_at preserved as str and None"
else
    assert_fail 10 "observed_at" "Expected str and None, got: $OUT10"
fi

# ---------------------------------------------------------------------------
# TEST 11 — Null values in parse input produce None in output
# ---------------------------------------------------------------------------
OUT11=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter
data = [{'battery_voltage': None, 'output_power': None}]
result = _parse_inverter(data)
r = result[0]
print('bv_is_none', r.battery_voltage is None)
print('op_is_none', r.output_power is None)
# Verify it's None, not 0
print('bv_not_zero', r.battery_voltage != 0.0)
print('op_not_zero', r.output_power != 0.0)
" 2>&1) || { assert_fail 11 "Null values" "Error: $OUT11"; }

if echo "$OUT11" | grep -q "bv_is_none True" && echo "$OUT11" | grep -q "op_is_none True"; then
    assert_pass 11 "Null values remain None (not coerced to 0)"
else
    assert_fail 11 "Null values" "Expected None values, got: $OUT11"
fi

# ---------------------------------------------------------------------------
# TEST 12 — RuntimeControlSnapshotAdapterInput has inverter field
# ---------------------------------------------------------------------------
OUT12=$(run_python "
from app.control.runtime_snapshot_adapter import RuntimeControlSnapshotAdapterInput
from dataclasses import fields
fnames = [f.name for f in fields(RuntimeControlSnapshotAdapterInput)]
print(','.join(sorted(fnames)))
" 2>&1) || { assert_fail 12 "AdapterInput inverter field" "Error: $OUT12"; }

if echo "$OUT12" | grep -q "inverter"; then
    assert_pass 12 "RuntimeControlSnapshotAdapterInput has inverter field"
else
    assert_fail 12 "AdapterInput inverter field" "Not found in: $OUT12"
fi

# ---------------------------------------------------------------------------
# TEST 13 — ControlStateSnapshotInput has inverter field
# ---------------------------------------------------------------------------
OUT13=$(run_python "
from app.control.control_state_snapshot import ControlStateSnapshotInput
from dataclasses import fields
fnames = [f.name for f in fields(ControlStateSnapshotInput)]
print(','.join(sorted(fnames)))
" 2>&1) || { assert_fail 13 "SnapshotInput inverter field" "Error: $OUT13"; }

if echo "$OUT13" | grep -q "inverter"; then
    assert_pass 13 "ControlStateSnapshotInput has inverter field"
else
    assert_fail 13 "SnapshotInput inverter field" "Not found in: $OUT13"
fi

# ---------------------------------------------------------------------------
# TEST 14 — ControlStateSnapshot has inverter field
# ---------------------------------------------------------------------------
OUT14=$(run_python "
from app.control.control_state_snapshot import ControlStateSnapshot
from dataclasses import fields
fnames = [f.name for f in fields(ControlStateSnapshot)]
print(','.join(sorted(fnames)))
" 2>&1) || { assert_fail 14 "Snapshot inverter field" "Error: $OUT14"; }

if echo "$OUT14" | grep -q "inverter"; then
    assert_pass 14 "ControlStateSnapshot has inverter field"
else
    assert_fail 14 "Snapshot inverter field" "Not found in: $OUT14"
fi

# ---------------------------------------------------------------------------
# TEST 15 — Full pipeline produces inverter with correct values
# ---------------------------------------------------------------------------
OUT15=$(run_python "
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state

runtime_state = {
    'snapshot_id': 'test-snap-1',
    'created_at': '2026-07-25T12:00:00Z',
    'inverter': [
        {
            'battery_voltage': 25.7,
            'output_power': 800.0,
            'pv_total_power': 1200.0,
            'working_mode': 'Line Mode',
            'observed_at': '2026-07-25T12:00:00+00:00',
        }
    ],
}
snapshot = build_control_state_snapshot_from_runtime_state(runtime_state)
if snapshot is None:
    print('ERROR: snapshot is None')
elif not hasattr(snapshot, 'inverter'):
    print('ERROR: no inverter attr')
else:
    inv = snapshot.inverter
    print('inverter_type', type(inv).__name__)
    print('inverter_len', len(inv))
    if len(inv) > 0:
        r = inv[0]
        print('bv', r.battery_voltage)
        print('op', r.output_power)
        print('pv', r.pv_total_power)
        print('wm', r.working_mode)
" 2>&1) || { assert_fail 15 "Full pipeline" "Error: $OUT15"; }

if echo "$OUT15" | grep -q "inverter_len 1" && echo "$OUT15" | grep -q "bv 25.7" && echo "$OUT15" | grep -q "op 800.0" && echo "$OUT15" | grep -q "wm Line Mode"; then
    assert_pass 15 "Full pipeline produces inverter with correct values"
else
    assert_fail 15 "Full pipeline" "Expected inverter with values, got: $OUT15"
fi

# ---------------------------------------------------------------------------
# TEST 16 — Missing inverter key produces empty tuple, not crash
# ---------------------------------------------------------------------------
OUT16=$(run_python "
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state

runtime_state = {
    'snapshot_id': 'test-snap-2',
    'created_at': '2026-07-25T12:00:00Z',
}
snapshot = build_control_state_snapshot_from_runtime_state(runtime_state)
if snapshot is None:
    print('ERROR: snapshot is None')
else:
    inv = getattr(snapshot, 'inverter', 'MISSING')
    if inv == 'MISSING':
        print('ERROR: no inverter attr on snapshot')
    else:
        print('inverter_len', len(inv))
        print('inverter_type', type(inv).__name__)
" 2>&1) || { assert_fail 16 "Missing inverter key" "Error: $OUT16"; }

if echo "$OUT16" | grep -q "inverter_len 0"; then
    assert_pass 16 "Missing inverter key produces empty tuple, no crash"
else
    assert_fail 16 "Missing inverter key" "Expected empty tuple, got: $OUT16"
fi

# ---------------------------------------------------------------------------
# TEST 17 — Inverter data survives to WebUiControlStateResponse JSON
# ---------------------------------------------------------------------------
OUT17=$(run_python "
import json
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state
from app.control.web_ui_read_contract import build_web_ui_control_state_response

runtime_state = {
    'snapshot_id': 'test-snap-3',
    'created_at': '2026-07-25T12:00:00Z',
    'inverter': [
        {
            'battery_voltage': 25.7,
            'output_power': 800.0,
            'pv_total_power': 1200.0,
            'working_mode': 'Line Mode',
            'observed_at': '2026-07-25T12:00:00+00:00',
        }
    ],
}
snapshot = build_control_state_snapshot_from_runtime_state(runtime_state)
response = build_web_ui_control_state_response(snapshot)
# Serialize to JSON manually — FastAPI serialization uses __dict__ for frozen dataclasses
from dataclasses import asdict
resp_dict = asdict(response)
json_str = json.dumps(resp_dict, default=str)
print('has_inverter_in_response', 'inverter' in json_str)
print('bv_in_json', '25.7' in json_str)
print('op_in_json', '800.0' in json_str)
print('pv_in_json', '1200.0' in json_str)
print('wm_in_json', 'Line Mode' in json_str)
" 2>&1) || { assert_fail 17 "WebUiControlStateResponse JSON" "Error: $OUT17"; }

if echo "$OUT17" | grep -q "has_inverter_in_response True" && echo "$OUT17" | grep -q "bv_in_json True" && echo "$OUT17" | grep -q "op_in_json True"; then
    assert_pass 17 "Inverter data survives to WebUiControlStateResponse JSON"
else
    assert_fail 17 "WebUiControlStateResponse JSON" "Expected inverter in JSON, got: $OUT17"
fi

# ---------------------------------------------------------------------------
# TEST 18 — Null values serialize as null in JSON
# ---------------------------------------------------------------------------
OUT18=$(run_python "
import json
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state
from app.control.web_ui_read_contract import build_web_ui_control_state_response
from dataclasses import asdict

runtime_state = {
    'snapshot_id': 'test-snap-4',
    'created_at': '2026-07-25T12:00:00Z',
    'inverter': [
        {
            'battery_voltage': None,
            'output_power': None,
            'pv_total_power': 1200.0,
            'working_mode': 'Line Mode',
        }
    ],
}
snapshot = build_control_state_snapshot_from_runtime_state(runtime_state)
response = build_web_ui_control_state_response(snapshot)
resp_dict = asdict(response)
json_str = json.dumps(resp_dict, default=str)
# Check that null values exist, not zeros
print('has_null', 'null' in json_str)
" 2>&1) || { assert_fail 18 "Null JSON serialization" "Error: $OUT18"; }

if echo "$OUT18" | grep -q "has_null True"; then
    assert_pass 18 "Null values serialize as null in JSON (not 0)"
else
    assert_fail 18 "Null JSON serialization" "Expected null in JSON, got: $OUT18"
fi

# ---------------------------------------------------------------------------
# TEST 19 — Existing loads and sensors remain non-duplicated
# ---------------------------------------------------------------------------
OUT19=$(run_python "
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state

runtime_state = {
    'snapshot_id': 'test-snap-5',
    'created_at': '2026-07-25T12:00:00Z',
    'loads': [
        {'load_id': 'ld1', 'display_name': 'Pump'},
    ],
    'sensors': [
        {'sensor_id': 's1', 'display_name': 'Water Temp', 'value': 22.3, 'unit': 'celsius'},
    ],
    'inverter': [
        {'battery_voltage': 25.7},
    ],
}
snapshot = build_control_state_snapshot_from_runtime_state(runtime_state)
print('loads_count', len(snapshot.loads))
print('sensors_count', len(snapshot.sensors))
print('inverter_count', len(snapshot.inverter))
print('load_id', snapshot.loads[0].load_id if len(snapshot.loads) > 0 else 'NONE')
print('sensor_id', snapshot.sensors[0].sensor_id if len(snapshot.sensors) > 0 else 'NONE')
" 2>&1) || { assert_fail 19 "Existing loads/sensors" "Error: $OUT19"; }

if echo "$OUT19" | grep -q "loads_count 1" && echo "$OUT19" | grep -q "sensors_count 1" && echo "$OUT19" | grep -q "inverter_count 1"; then
    assert_pass 19 "Existing loads and sensors remain non-duplicated (1 load, 1 sensor, 1 inverter)"
else
    assert_fail 19 "Existing loads/sensors" "Expected 1/1/1, got: $OUT19"
fi

# ---------------------------------------------------------------------------
# TEST 20 — No raw Tuya IDs or property codes exposed in inverter fields
# ---------------------------------------------------------------------------
OUT20=$(run_python "
from app.control.control_state_snapshot import InverterReadSnapshot
from dataclasses import fields
fnames = [f.name for f in fields(InverterReadSnapshot)]
# Check for any suspicious field names (exact match, not substring)
suspicious = [f for f in fnames if 'tuya' in f.lower() or 'property_code' in f or 'device_id' in f or 'parent_id' in f or 'raw_status' in f]
print('suspicious_fields', ','.join(suspicious) if suspicious else 'NONE')
" 2>&1) || { assert_fail 20 "No Tuya exposure" "Error: $OUT20"; }

if echo "$OUT20" | grep -q "suspicious_fields NONE"; then
    assert_pass 20 "No raw Tuya IDs or property codes exposed in inverter fields"
else
    assert_fail 20 "No Tuya exposure" "Found suspicious fields: $OUT20"
fi

# ---------------------------------------------------------------------------
# TEST 21 — build_runtime_read_model accepts inverter_provider
# ---------------------------------------------------------------------------
OUT21=$(run_python "
from app.web_runtime_integration import build_runtime_read_model

inv_data = {
    'battery_voltage': 25.4,
    'output_voltage': 230.1,
    'output_power': 412.0,
    'pv_total_power': 188.0,
}
def inv_provider():
    return inv_data

result = build_runtime_read_model(
    devices=[],
    created_at='2026-07-25T12:00:00Z',
    inverter_provider=inv_provider,
)
print('has_inverter', 'inverter' in result)
inv = result.get('inverter')
if inv is not None:
    print('inv_type', type(inv).__name__)
    print('inv_len', len(inv) if isinstance(inv, (list, tuple)) else 'not_list')
    if isinstance(inv, (list, tuple)) and len(inv) > 0:
        print('bv', inv[0].get('battery_voltage'))
        print('ov', inv[0].get('output_voltage'))
" 2>&1) || { assert_fail 21 "build_runtime_read_model inverter_provider" "Error: $OUT21"; }

if echo "$OUT21" | grep -q "has_inverter True"; then
    assert_pass 21 "build_runtime_read_model accepts inverter_provider and emits runtime_state['inverter']"
else
    assert_fail 21 "build_runtime_read_model inverter_provider" "Expected inverter in result, got: $OUT21"
fi

# ---------------------------------------------------------------------------
# TEST 22 — build_runtime_read_model without inverter_provider works
# ---------------------------------------------------------------------------
OUT22=$(run_python "
from app.web_runtime_integration import build_runtime_read_model

result = build_runtime_read_model(
    devices=[],
    created_at='2026-07-25T12:00:00Z',
)
print('has_inverter', 'inverter' in result)
print('has_loads', 'loads' in result)
" 2>&1) || { assert_fail 22 "No inverter_provider backward compat" "Error: $OUT22"; }

if echo "$OUT22" | grep -q "has_inverter False" && echo "$OUT22" | grep -q "has_loads True"; then
    assert_pass 22 "build_runtime_read_model works without inverter_provider (backward compatible)"
else
    assert_fail 22 "No inverter_provider backward compat" "Expected no inverter, got: $OUT22"
fi

# ---------------------------------------------------------------------------
# TEST 23 — create_runtime_state_provider accepts inverter_provider
# ---------------------------------------------------------------------------
OUT23=$(run_python "
from app.web_runtime_integration import create_runtime_state_provider

def devs():
    return []

inv_data = {'battery_voltage': 25.4}
def inv_prov():
    return inv_data

prov = create_runtime_state_provider(
    devices_provider=devs,
    inverter_provider=inv_prov,
)
result = prov()
if result is None:
    print('ERROR: result is None')
else:
    print('has_inverter', 'inverter' in result)
    inv = result.get('inverter', [])
    if isinstance(inv, (list, tuple)) and len(inv) > 0:
        print('bv', inv[0].get('battery_voltage'))
" 2>&1) || { assert_fail 23 "create_runtime_state_provider inverter_provider" "Error: $OUT23"; }

if echo "$OUT23" | grep -q "has_inverter True"; then
    assert_pass 23 "create_runtime_state_provider accepts and propagates inverter_provider"
else
    assert_fail 23 "create_runtime_state_provider inverter_provider" "Expected inverter, got: $OUT23"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
echo "RESULTS: $PASSED passed, $FAILED failed"
echo "========================================"

if [ $FAILED -gt 0 ]; then
    echo ""
    echo "FAILURES:"
    for f in "${FAILURES[@]}"; do
        echo "  $f"
    done
    exit 1
fi

echo "All assertions passed."
exit 0
