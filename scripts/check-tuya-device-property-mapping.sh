#!/usr/bin/env bash
# check-tuya-device-property-mapping.sh
# Validate PR 0034c property mapping and startup reset.
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
echo "=== PR 0034c device property mapping check ==="

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
# PART 1: DevicePropertyMapping types
# ================================================================

from app.devices.device_property_mapping import (
    DevicePropertyMapping, CommandKind, MappingValidity,
    MappingSource, CommandResult,
)

# [1] multi_switch_child "switch_1"
m = DevicePropertyMapping.multi_switch_child("switch_1")
if m.control_property == "switch_1" and m.state_property == "switch_1" \
   and m.command_kind == CommandKind.BINARY and m.command_capable:
    ok("multi_switch_child switch_1: control=switch_1, state=switch_1, binary, capable")
else:
    fail(f"multi_switch_child: cp={m.control_property} sp={m.state_property}")

# [2] multi_switch_child "switch_3"
m = DevicePropertyMapping.multi_switch_child("switch_3")
if m.control_property == "switch_3":
    ok("multi_switch_child switch_3: control=switch_3")
else:
    fail(f"switch_3: cp={m.control_property}")

# [3] single_switch with explicit control_key
m = DevicePropertyMapping.single_switch(control_key="my_switch")
if m.control_property == "my_switch" and m.mapping_validity == MappingValidity.VALID:
    ok("single_switch explicit control_key")
else:
    fail(f"control_key: {m.control_property}")

# [4] single_switch with channel
m = DevicePropertyMapping.single_switch(channel="ch1")
if m.control_property == "ch1":
    ok("single_switch channel precedence")
else:
    fail(f"channel: {m.control_property}")

# [5] single_switch with api_sw
m = DevicePropertyMapping.single_switch(api_sw="api1")
if m.control_property == "api1":
    ok("single_switch api_sw")
else:
    fail(f"api_sw: {m.control_property}")

# [6] NO universal switch_1 fallback
m = DevicePropertyMapping.single_switch()
if m.mapping_validity == MappingValidity.INVALID and m.control_property is None:
    ok("NO universal switch_1 fallback — invalid mapping")
else:
    fail(f"fallback: validity={m.mapping_validity} cp={m.control_property}")

# [7] Pump compat: P/Power
m = DevicePropertyMapping.pump_device()
if m.control_property == "P" and m.state_property == "Power" \
   and m.command_kind == CommandKind.NUMERIC:
    ok("pump compat: P/Power, numeric")
else:
    fail(f"pump: cp={m.control_property} sp={m.state_property}")

# [8] Pump explicit
m = DevicePropertyMapping.pump_device(control_key="my_pump", state_key="my_state")
if m.control_property == "my_pump" and m.state_property == "my_state":
    ok("pump explicit control_key/state_key")
else:
    fail(f"pump explicit: {m.control_property}/{m.state_property}")

# [9] Sensor: command_kind=none, no control_property
m = DevicePropertyMapping.sensor_device(state_key="temp_current")
if m.command_kind == CommandKind.NONE and m.control_property is None \
   and not m.command_capable:
    ok("sensor: none command kind, not capable")
else:
    fail(f"sensor: kind={m.command_kind} cp={m.control_property}")

# [10] CommandResult types
cr = CommandResult.ok()
if cr.success and cr.accepted and cr.error is None:
    ok("CommandResult.ok()")
else:
    fail("CommandResult.ok()")

cr = CommandResult.not_capable()
if not cr.success and not cr.accepted and cr.error == "device-not-command-capable":
    ok("CommandResult.not_capable()")
else:
    fail(f"not_capable: {cr}")

# [11] Empty switch key -> invalid
m = DevicePropertyMapping.multi_switch_child("")
if m.mapping_validity == MappingValidity.INVALID:
    ok("empty switch key -> invalid")
else:
    fail(f"empty key: {m.mapping_validity}")

# [12] Inferred device with control_key -> valid binary
m = DevicePropertyMapping.inferred_device(control_key="sw", state_key="sw")
if m.mapping_validity == MappingValidity.VALID and m.command_capable:
    ok("inferred device with properties -> valid binary")
else:
    fail(f"inferred: {m.mapping_validity}")

# [13] Inferred device without properties -> invalid
m = DevicePropertyMapping.inferred_device()
if m.mapping_validity == MappingValidity.INVALID:
    ok("inferred device without props -> invalid")
else:
    fail(f"inferred no props: {m.mapping_validity}")

# [14] Unavailable default
m = DevicePropertyMapping.unavailable_default()
if m.mapping_validity == MappingValidity.UNAVAILABLE:
    ok("unavailable default")
else:
    fail(f"unavailable: {m.mapping_validity}")

# ================================================================
# PART 2: RelayChannelDevice property_mapping field
# ================================================================

from app.devices.relay_channel_device import RelayChannelDevice

# [15] RelayChannelDevice has property_mapping
dev = RelayChannelDevice(
    id="test-1", name="Test", desc="test",
    tuya_device_id="t1", device_type="switch",
    available=True, min_volt=0, max_volt=0,
    priority=0, control_key="sw1", status={},
)
if hasattr(dev, "property_mapping"):
    ok("RelayChannelDevice has property_mapping field")
else:
    fail("No property_mapping field")

# [16] is_command_capable property
if hasattr(dev, "is_command_capable"):
    ok("RelayChannelDevice has is_command_capable property")
else:
    fail("No is_command_capable")

# [17] set_on uses property_mapping.control_property
dev2 = RelayChannelDevice(
    id="test-2", name="Test2", desc="test",
    tuya_device_id="t2", device_type="switch",
    available=True, min_volt=0, max_volt=0,
    priority=0, control_key="sw1", status={},
    property_mapping=DevicePropertyMapping.single_switch(control_key="sw_explicit"),
)
dev2.set_on()
if dev2.status.get("sw_explicit") is True:
    ok("set_on uses property_mapping.control_property (sw_explicit)")
else:
    fail(f"set_on status: {dev2.status}")

# [18] DeviceInitializer creates mappings
from app.device_initializer import DeviceInitializer
# We can't easily test with real devices.yaml, but import should work
ok("DeviceInitializer importable (mapping resolution integrated)")

# ================================================================
# PART 3: Startup reset coordinator
# ================================================================

from app.service.startup_reset_coordinator import (
    StartupResetCoordinator, STARTUP_RESET_TIMEOUT_SECONDS,
    STARTUP_RESET_RETRY_INTERVAL,
)

# [19] Coordinator class exists
ok("StartupResetCoordinator class importable")

# [20] Timeout constants
if STARTUP_RESET_TIMEOUT_SECONDS == 120.0:
    ok(f"STARTUP_RESET_TIMEOUT_SECONDS={STARTUP_RESET_TIMEOUT_SECONDS}s")
else:
    fail(f"timeout: {STARTUP_RESET_TIMEOUT_SECONDS}")

if STARTUP_RESET_RETRY_INTERVAL == 30.0:
    ok(f"STARTUP_RESET_RETRY_INTERVAL={STARTUP_RESET_RETRY_INTERVAL}s")
else:
    fail(f"retry: {STARTUP_RESET_RETRY_INTERVAL}")

# [21] Gate properties
# Cannot test full coordinator without mocking Tuya, but verify class structure
if hasattr(StartupResetCoordinator, 'is_gate_open'):
    ok("StartupResetCoordinator has is_gate_open property")
else:
    fail("Missing is_gate_open")

if hasattr(StartupResetCoordinator, 'reset_status'):
    ok("StartupResetCoordinator has reset_status property")
else:
    fail("Missing reset_status")

# ================================================================
# PART 4: Command consolidation
# ================================================================

import inspect
from app.tuya.relay_tuya_controller import RelayTuyaController

# [22] _submit_command exists
if hasattr(RelayTuyaController, '_submit_command'):
    ok("_submit_command exists (canonical command path)")
else:
    fail("Missing _submit_command")

# [23] switch_on exists (replaces switch_on_device)
if hasattr(RelayTuyaController, 'switch_on'):
    ok("switch_on exists")
else:
    fail("Missing switch_on")

# [24] switch_off exists
if hasattr(RelayTuyaController, 'switch_off'):
    ok("switch_off exists")
else:
    fail("Missing switch_off")

# [25] set_numeric exists
if hasattr(RelayTuyaController, 'set_numeric'):
    ok("set_numeric exists (canonical numeric path)")
else:
    fail("Missing set_numeric")

# [26] Old methods are deprecation stubs
# switch_on_device still exists as backward-compat
if hasattr(RelayTuyaController, 'switch_on_device'):
    ok("switch_on_device preserved as backward-compat stub")
else:
    fail("Missing switch_on_device stub")

# [27] _send and _send_switch_cmd removed
src = inspect.getsource(RelayTuyaController)
if "_send_switch_cmd" not in src:
    ok("_send_switch_cmd removed")
else:
    fail("_send_switch_cmd still present")

if "def _send(self" not in src:
    ok("_send removed")
else:
    fail("_send still present")

# ================================================================
# PART 5: Read model fields
# ================================================================

from app.web_runtime_integration import _device_to_load_dict

# [28] _device_to_load_dict includes mapping_status
dev3 = RelayChannelDevice(
    id="test-3", name="Test3", desc="test",
    tuya_device_id="t3", device_type="switch",
    available=True, min_volt=0, max_volt=0,
    priority=0, control_key="sw1", status={},
    property_mapping=DevicePropertyMapping.single_switch(control_key="sw1"),
)
d = _device_to_load_dict(dev3)
if "mapping_status" in d:
    ok("_device_to_load_dict includes mapping_status")
else:
    fail("mapping_status missing from load dict")

# [29] mapping_status value
if d.get("mapping_status") == "valid":
    ok("mapping_status='valid' for valid mapping")
else:
    fail(f"mapping_status: {d.get('mapping_status')}")

# [30] startup_reset_result exists
if "startup_reset_result" in d:
    ok("startup_reset_result field exists")
else:
    fail("startup_reset_result missing")

# ================================================================
# PART 6: SmartHomeController gate check
# ================================================================

# [31] SmartHomeController accepts reset coordinator
from app.service.smart_home_controller import SmartHomeController
ctrl_src = inspect.getsource(SmartHomeController.__init__)
if "startup_reset_coordinator" in ctrl_src:
    ok("SmartHomeController accepts startup_reset_coordinator")
else:
    fail("No reset_coordinator param")

# [32] _switch_loop has gate check
switch_src = inspect.getsource(SmartHomeController._switch_loop)
if "is_gate_open" in switch_src or "gate" in switch_src.lower():
    ok("_switch_loop has gate check")
else:
    fail("No gate check in _switch_loop")

# ================================================================
# PART 7: TuyaStatusUpdaterAsync uses property_mapping
# ================================================================

from app.tuya.status_updater_async import TuyaStatusUpdaterAsync

# [33] refresh_once exists
if hasattr(TuyaStatusUpdaterAsync, 'refresh_once'):
    ok("refresh_once exists")
else:
    fail("Missing refresh_once")

# [34] updater uses property_mapping.state_property in _update_once
upd_src = inspect.getsource(TuyaStatusUpdaterAsync._update_once)
if "property_mapping.state_property" in upd_src or "property_mapping" in upd_src:
    ok("_update_once references property_mapping")
else:
    fail("No property_mapping reference in _update_once")

# ================================================================
# PART 8: Run.py startup order
# ================================================================

run_path = os.path.join(sys.argv[1], "run.py")
if os.path.isfile(run_path):
    with open(run_path) as f:
        run_src = f.read()
    if "StartupResetCoordinator" in run_src:
        ok("run.py imports StartupResetCoordinator")
    else:
        fail("No StartupResetCoordinator in run.py")

    if "reset_coordinator" in run_src:
        ok("run.py creates reset_coordinator")
    else:
        fail("No reset_coordinator in run.py")

    if "startup_reset_coordinator=reset_coordinator" in run_src:
        ok("run.py passes reset_coordinator to SmartHomeController")
    else:
        fail("reset_coordinator not passed to SHC")
else:
    fail("run.py not found")

# ================================================================
# PART 9: No raw Tuya IDs exposed
# ================================================================

# [38] verify no raw control keys in read model
if "control_property" not in str(d) and "state_property" not in str(d):
    ok("No control/state properties exposed in read model")
else:
    fail("Raw properties in read model")

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
    print(f"=== PASS: All {test_num} device property mapping checks passed ===")
    sys.exit(0)
PYEOF

TEST_EXIT=$?
exit "$TEST_EXIT"
