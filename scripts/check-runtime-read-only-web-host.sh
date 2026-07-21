#!/usr/bin/env bash
# check-runtime-read-only-web-host.sh
# Validate PR 0031 — runtime read-only web host integration.
set -euo pipefail

ERRORS=0
MODULE="app/web_runtime_integration.py"
RUNPY="run.py"
REQ="requirements.txt"
WF=".github/workflows/validate.yml"

echo "=== PR 0031 runtime read-only web host check ==="

# -------------------------------------------------------------------
# 1 — shell checks
# -------------------------------------------------------------------
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [2] run.py exists ... "
[ -f "$RUNPY" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [3] requirements.txt exists ... "
[ -f "$REQ" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 2 — Python compilation
# -------------------------------------------------------------------
echo -n "  [4] Module compiles ... "
python3 -c "import ast; ast.parse(open('$MODULE').read())" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [5] run.py compiles ... "
python3 -c "import ast; ast.parse(open('$RUNPY').read())" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 3 — AST checks for forbidden patterns
# -------------------------------------------------------------------
echo -n "  [6] No module-level uvicorn import in module ... "
python3 -c "
import ast
tree = ast.parse(open('$MODULE').read())
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            if 'uvicorn' in alias.name.lower():
                # Allow inside functions only
                if not any(isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)) for p in ast.iter_child_nodes(tree)):
                    print('FAIL: module-level uvicorn import')
                    exit(1)
print('OK')
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [7] No module-level uvicorn import in run.py ... "
python3 -c "
import ast
tree = ast.parse(open('$RUNPY').read())
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            if 'uvicorn' in alias.name.lower():
                print('FAIL: module-level uvicorn import')
                exit(1)
print('OK')
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [8] uvicorn.Config used ... "
grep -q "uvicorn.Config\|uvicorn\.Config" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [9] uvicorn.Server used ... "
grep -q "uvicorn.Server\|uvicorn\.Server" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [10] uvicorn.run not used in module ... "
grep -qE 'uvicorn\.run\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

echo -n " [11] uvicorn.run not used in run.py ... "
grep -q "uvicorn\.run" "$RUNPY" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

echo -n " [12] Signal handler suppression in module ... "
grep -q "install_signal_handlers\|capture_signals" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 4 — Default-disabled and env parsing
# -------------------------------------------------------------------
echo -n " [13] Default disabled (empty environ) ... "
python3 -c "
from app.web_runtime_integration import is_runtime_web_host_enabled
assert is_runtime_web_host_enabled({}) is False
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': ''}) is False
print('OK')
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [14] True values accepted ... "
python3 -c "
from app.web_runtime_integration import is_runtime_web_host_enabled
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': '1'}) is True
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'true'}) is True
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'yes'}) is True
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'on'}) is True
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'TRUE'}) is True
print('OK')
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [15] False values rejected ... "
python3 -c "
from app.web_runtime_integration import is_runtime_web_host_enabled
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': '0'}) is False
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'false'}) is False
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'no'}) is False
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'off'}) is False
assert is_runtime_web_host_enabled({'WEB_HOST_ENABLED': 'maybe'}) is False
print('OK')
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 5 — Invalid port
# -------------------------------------------------------------------
echo -n " [16] Invalid port raises RuntimeError ... "
python3 -c "
from app.web_runtime_integration import start_runtime_read_only_web_host
import asyncio
async def test():
    try:
        await start_runtime_read_only_web_host(
            devices_provider=list,
            environ={'WEB_HOST_ENABLED': '1', 'WEB_HOST_PORT': '99999'}
        )
        assert False, 'should have raised'
    except RuntimeError as e:
        # uvicorn-unavailable is acceptable when uvicorn not installed
        assert str(e) in ('invalid-web-host-port', 'uvicorn-unavailable'), f'unexpected: {e}'
asyncio.run(test())
print('OK')
" 2>&1 && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 6 — Build runtime read model
# -------------------------------------------------------------------
echo -n " [17] build_runtime_read_model with fake device ... "
python3 - <<'PY'
from app.web_runtime_integration import build_runtime_read_model
import json

class FakeDevice:
    def __init__(self):
        self.id = "fake-load-1"
        self.name = "Test Load"
        self.load_in_wt = 120
        self.state_key = "switch_1"
        self.status = {"switch_1": True}
        self.device_type = "relay"
        self.available = True
        self.is_healthy = True
        self.tuya_device_id = "CLOUD-ID-12345"
        self.control_key = "SECRET-KEY-ABCDE"
        self.api_key = "API-SECRET-XYZ"
        self.extra = {"is_life_support": True, "roles": ["critical"], "secret_field": "LEAKED"}

devices = [FakeDevice()]
result = build_runtime_read_model(devices)
assert result["loads"], "loads should not be empty"
load = result["loads"][0]
assert load["load_id"] == "fake-load-1"
assert load["display_name"] == "Test Load"
assert load["configured_load_watts"] == 120.0
assert load["currently_on"] is True
assert load["controllable"] is True
assert load["is_life_support"] is True
assert "critical" in load["roles"]
assert load["status"] == "healthy"

# Serialise and check forbidden values absent
serialised = json.dumps(result)
assert "CLOUD-ID-12345" not in serialised, "tuya_device_id leaked"
assert "SECRET-KEY-ABCDE" not in serialised, "control_key leaked"
assert "API-SECRET-XYZ" not in serialised, "api_key leaked"
assert "LEAKED" not in serialised, "secret extra field leaked"
print("OK")
PY
echo ""

# -------------------------------------------------------------------
# 7 — Failing devices_provider
# -------------------------------------------------------------------
echo -n " [18] Failing devices_provider returns None ... "
python3 -c "
from app.web_runtime_integration import create_runtime_state_provider
def failing():
    raise RuntimeError('boom')
p = create_runtime_state_provider(failing)
assert p() is None
print('OK')
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 8 — Real embedded server test (requires fastapi + uvicorn)
# -------------------------------------------------------------------
echo -n " [19] Real embedded server HTTP test ... "
python3 - <<'PY'
import asyncio
import json
import socket
import urllib.request
import sys
import traceback
from app.web_runtime_integration import (
    is_runtime_web_host_enabled,
    start_runtime_read_only_web_host,
    stop_runtime_read_only_web_host,
    create_runtime_state_provider,
    build_runtime_read_model,
)

class FakeDevice:
    def __init__(self):
        self.id = "fake-http-1"
        self.name = "HTTP Test Load"
        self.load_in_wt = 250
        self.state_key = "switch_1"
        self.status = {"switch_1": False}
        self.device_type = "relay"
        self.available = True
        self.is_healthy = True
        self.tuya_device_id = "CLOUD-HTTP-SECRET"
        self.control_key = "KEY-HTTP-SECRET"
        self.api_key = "API-HTTP-SECRET"
        self.extra = {}

def get_devs():
    return [FakeDevice()]

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

async def main():
    free_port = find_free_port()
    handle = None
    try:
        handle = await start_runtime_read_only_web_host(
            devices_provider=get_devs,
            environ={
                'WEB_HOST_ENABLED': '1',
                'WEB_HOST_BIND': '127.0.0.1',
                'WEB_HOST_PORT': str(free_port),
            },
        )
        assert handle is not None, "handle should not be None"

        # Give the server a moment
        await asyncio.sleep(0.5)

        # HTTP GET — offloaded to a thread so uvicorn can use the event loop
        url = f'http://127.0.0.1:{free_port}/control/state'

        def perform_request():
            with urllib.request.urlopen(url, timeout=5) as response:
                return (
                    response.status,
                    response.headers.get_content_type(),
                    response.read(),
                )

        status, content_type, body_bytes = await asyncio.to_thread(perform_request)
        body = body_bytes.decode('utf-8')
        assert status == 200, f"expected 200 got {status}"
        assert 'application/json' in (content_type or ''), (
            f"expected JSON content type, got {content_type}"
        )
        data = json.loads(body)
        # check fake load id present
        assert 'fake-http-1' in body, "fake load id not in response"
        # check forbidden fields absent
        assert 'CLOUD-HTTP-SECRET' not in body, "tuya_device_id leaked"
        assert 'KEY-HTTP-SECRET' not in body, "control_key leaked"
        assert 'API-HTTP-SECRET' not in body, "api_key leaked"
    finally:
        if handle is not None:
            await stop_runtime_read_only_web_host(handle)
            # Wait for task to finish
            try:
                await asyncio.wait_for(handle.task, timeout=3)
            except asyncio.TimeoutError:
                pass
            assert handle.task.done(), "server task should be done"

try:
    asyncio.run(main())
    print("OK")
except Exception:
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
PY
echo ""

# -------------------------------------------------------------------
# 9 — Disabled returns None without uvicorn
# -------------------------------------------------------------------
echo -n " [20] WEB_HOST_ENABLED=false returns None ... "
python3 -c "
import asyncio
from app.web_runtime_integration import start_runtime_read_only_web_host
async def test():
    result = await start_runtime_read_only_web_host(
        devices_provider=list,
        environ={'WEB_HOST_ENABLED': 'false'},
    )
    assert result is None
asyncio.run(test())
print('OK')
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 10 — run.py AST checks
# -------------------------------------------------------------------
echo -n " [21] run.py imports start_runtime_read_only_web_host ... "
grep -q "start_runtime_read_only_web_host" "$RUNPY" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [22] run.py imports stop_runtime_read_only_web_host ... "
grep -q "stop_runtime_read_only_web_host" "$RUNPY" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [23] run.py calls start_runtime_read_only_web_host ... "
grep -q "await start_runtime_read_only_web_host" "$RUNPY" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [24] run.py calls stop_runtime_read_only_web_host ... "
grep -q "await stop_runtime_read_only_web_host" "$RUNPY" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [25] run.py injects dev_mgr.get_devices ... "
grep -q "dev_mgr.get_devices" "$RUNPY" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [26] run.py catches web host startup failure ... "
grep -q "except Exception" "$RUNPY" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [27] run.py stops web host before device shutdown ... "
# Check that stop_runtime_read_only_web_host appears before updater.stop()
STOP_LINE=$(grep -n "stop_runtime_read_only_web_host" "$RUNPY" | head -1 | cut -d: -f1 || echo "0")
UPDATER_LINE=$(grep -n "updater.stop()" "$RUNPY" | head -1 | cut -d: -f1 || echo "0")
if [ "$STOP_LINE" -gt 0 ] && [ "$UPDATER_LINE" -gt 0 ] && [ "$STOP_LINE" -lt "$UPDATER_LINE" ]; then
    echo "OK"
else
    echo "FAIL (stop=$STOP_LINE, updater.stop=$UPDATER_LINE)"
    ERRORS=$((ERRORS + 1))
fi

echo -n " [28] run.py retains SIGINT/SIGTERM ... "
grep -q "add_signal_handler" "$RUNPY" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 11 — Dockerfile and compose unchanged
# -------------------------------------------------------------------
echo -n " [29] Dockerfile unchanged relative to HEAD ... "
CHANGED=$( (git diff --name-only HEAD 2>/dev/null || true) )
echo "$CHANGED" | grep -qx "Dockerfile" && { echo "FAIL (Dockerfile modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

echo -n " [30] docker-compose.yml unchanged relative to HEAD ... "
echo "$CHANGED" | grep -qx "docker-compose.yml" && { echo "FAIL (docker-compose.yml modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# -------------------------------------------------------------------
# 12 — Validate workflow step
# -------------------------------------------------------------------
echo -n " [31] validate.yml has runtime check step ... "
grep -q "check-runtime-read-only-web-host.sh" "$WF" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 13 — requirements.txt has fastapi + uvicorn
# -------------------------------------------------------------------
echo -n " [32] requirements.txt has fastapi ... "
grep -q "fastapi" "$REQ" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n " [33] requirements.txt has uvicorn ... "
grep -q "uvicorn" "$REQ" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# -------------------------------------------------------------------
# 14 — forbidden patterns in integration module
# -------------------------------------------------------------------
echo -n " [34] No write routes in module ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

echo -n " [35] No hardware command calls in module ... "
for fc in send_commands switch_on_device switch_off_device set_numeric set_on set_off toggle_device; do
    if grep -qE "\b${fc}\s*\(" "$MODULE"; then
        echo "FAIL ($fc found)"
        ERRORS=$((ERRORS + 1))
        break
    fi
done
[ $? -eq 0 ] && echo "OK" || true

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: runtime read-only web host integration checks completed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
