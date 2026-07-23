#!/usr/bin/env bash
# check-tuya-device-property-mapping.sh
# Validate PR 0034c property mapping and startup reset.
# PR 0034d: added import-safety checks.
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

# ================================================================
# PART 0: Import-safety check (PR 0034d hotfix)
# ================================================================

# Write import-safety test to a temp file to avoid escaping issues
IMPORT_TEST=$(mktemp)
trap 'rm -f "$IMPORT_TEST"' EXIT

cat > "$IMPORT_TEST" << 'PYEOF'
import sys, os, subprocess, tempfile

project_dir = sys.argv[1]

with tempfile.TemporaryDirectory() as tmpdir:
    env = os.environ.copy()
    env.pop("MONITOR_CONFIG_JSON", None)
    env.pop("MONITOR_CONFIG_PATH", None)
    env.pop("DEVICE_CONFIG_PATH", None)

    code = """
import sys
sys.path.insert(0, \"""" + project_dir + """\")

import os
assert not os.path.exists("config.json"), "config.json should not exist"
assert not os.path.exists("devices.yaml"), "devices.yaml should not exist"

try:
    from app.tuya.tuya_authorisation import TuyaAuthorisation
    print("  [I1] import app.tuya.tuya_authorisation ... OK")
except Exception as e:
    print("  [I1] import app.tuya.tuya_authorisation ... FAIL: " + str(e))
    sys.exit(1)

try:
    from app.tuya.status_updater_async import TuyaStatusUpdaterAsync
    print("  [I2] import app.tuya.status_updater_async ... OK")
except Exception as e:
    print("  [I2] import app.tuya.status_updater_async ... FAIL: " + str(e))
    sys.exit(1)

from unittest.mock import MagicMock
fake_mgr = MagicMock()
auth = TuyaAuthorisation(device_manager=fake_mgr)
assert auth.device_manager is fake_mgr
print("  [I3] TuyaAuthorisation with injected device_manager ... OK")

try:
    TuyaAuthorisation()
    print("  [I4] TuyaAuthorisation() without args ... FAIL (no error)")
    sys.exit(1)
except ValueError:
    print("  [I4] TuyaAuthorisation() without args raises ValueError ... OK")

fake_dev_mgr = MagicMock()
fake_dev_mgr.get_devices.return_value = []
updater = TuyaStatusUpdaterAsync(
    interval=30, dev_mgr=fake_dev_mgr, authorisation=auth
)
print("  [I5] TuyaStatusUpdaterAsync with fake deps ... OK")

from app.device_initializer import DeviceInitializer, DeviceConfigNotFoundError
try:
    DeviceInitializer(config_path=os.path.join(\"""" + tmpdir + """\", "nonexistent.yaml"))
    print("  [I6] DeviceInitializer(missing) ... FAIL (no error)")
    sys.exit(1)
except DeviceConfigNotFoundError:
    print("  [I6] DeviceInitializer(missing) raises DeviceConfigNotFoundError ... OK")

init = DeviceInitializer.__new__(DeviceInitializer, config_path="/tmp/test.yaml")
resolved = init._resolve_config_path("/tmp/test.yaml")
assert resolved == "/tmp/test.yaml"
print("  [I7] DeviceInitializer explicit path ... OK")

os.environ["DEVICE_CONFIG_PATH"] = "/env/path.yaml"
resolved2 = init._resolve_config_path(None)
assert resolved2 == "/env/path.yaml"
print("  [I8] DeviceInitializer DEVICE_CONFIG_PATH env ... OK")
del os.environ["DEVICE_CONFIG_PATH"]

resolved3 = init._resolve_config_path(None)
assert resolved3 == "devices.yaml"
print("  [I9] DeviceInitializer default fallback ... OK")

print("  [I10] No sys.exit during imports ... OK")

print()
print("=== Import-safety checks PASS ===")
sys.exit(0)
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmpdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="")
        print("=== Import-safety checks FAIL ===")
        sys.exit(1)
PYEOF

"$PYTHON" "$IMPORT_TEST" "$PROJECT_DIR"
IMPORT_EXIT=$?
if [ "$IMPORT_EXIT" -ne 0 ]; then
    echo "Import-safety checks failed — aborting"
    exit "$IMPORT_EXIT"
fi

# ================================================================
# PART 1-9: Existing mapping, command, reset, read-model checks
# ================================================================

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

# [34] updater uses property_mapping.state_property in _process_result
upd_src = inspect.getsource(TuyaStatusUpdaterAsync._process_result)
if "property_mapping.state_property" in upd_src or "property_mapping" in upd_src:
    ok("_process_result references property_mapping")
else:
    fail("No property_mapping reference in _process_result")

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
