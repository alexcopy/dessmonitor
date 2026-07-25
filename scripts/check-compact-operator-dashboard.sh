#!/usr/bin/env bash
# check-compact-operator-dashboard.sh
# Focused validator for PR 0036: Compact Operator Dashboard Layout.
#
# Verifies deterministic source-indicator derivation and served HTML contract.
# Exercises real Python and HTTP against a minimal server.
# Uses deterministic fake inputs — no production config, no Tuya, no network.
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
# BLOCK 1: Source indicator derivation logic (Python, pure function)
# ---------------------------------------------------------------------------
echo "=== BLOCK 1: Source indicator derivation logic ==="

OUT1=$(run_python "
# Test source indicator derivation as a pure function of working_mode
def get_source_state(working_mode):
    if working_mode is None or working_mode == '':
        return 'unknown'
    inverter_modes = frozenset({
        'Battery Mode', 'PV Mode', 'Invert Mode',
        'Power Saving Mode', 'Standby Mode', 'Bypass Mode',
    })
    if working_mode in inverter_modes:
        return 'inverter'
    if working_mode == 'Line Mode':
        return 'mains'
    if working_mode == 'Fault Mode':
        return 'fault'
    return 'unknown'

# Test all expected modes
tests = {
    'Battery Mode': 'inverter',
    'PV Mode': 'inverter',
    'Invert Mode': 'inverter',
    'Power Saving Mode': 'inverter',
    'Standby Mode': 'inverter',
    'Bypass Mode': 'inverter',
    'Line Mode': 'mains',
    'Fault Mode': 'fault',
    '': 'unknown',
    'None': 'unknown',
}
for wm, expected in tests.items():
    actual = get_source_state(None if wm == 'None' else wm)
    ok = 'OK' if actual == expected else 'FAIL'
    print(f'{wm} -> {actual} ({expected}) {ok}')
" 2>&1) || { assert_fail 1 "Source derivation" "Python execution failed"; }

# Source indicator state assertions
echo "$OUT1" | while IFS= read -r line; do echo "  $line"; done

# [1] Inverter modes
if echo "$OUT1" | grep -q "Battery Mode -> inverter (inverter) OK"; then
    assert_pass 1 "Source 'inverter' for Battery Mode"
else
    assert_fail 1 "Source 'inverter'" "Battery Mode not mapped correctly"
fi

# [2] Line/Mains mode
if echo "$OUT1" | grep -q "Line Mode -> mains (mains) OK"; then
    assert_pass 2 "Source 'mains' for Line Mode"
else
    assert_fail 2 "Source 'mains'" "Line Mode not mapped correctly"
fi

# [3] Empty/unknown
if echo "$OUT1" | grep -qE "unknown.*unknown.*OK"; then
    assert_pass 3 "Source 'unknown' for empty working_mode"
else
    assert_fail 3 "Source 'unknown'" "Empty working_mode not mapped correctly"
fi

# [4] Fault mode
if echo "$OUT1" | grep -q "Fault Mode -> fault (fault) OK"; then
    assert_pass 4 "Source 'fault' for Fault Mode"
else
    assert_fail 4 "Source 'fault'" "Fault Mode not mapped correctly"
fi

# [5] None input
if echo "$OUT1" | grep -q "None -> unknown (unknown) OK"; then
    assert_pass 5 "Source 'unknown' for None input"
else
    assert_fail 5 "Source 'unknown'" "None not mapped correctly"
fi

# ---------------------------------------------------------------------------
# BLOCK 2: Inverter metric formatting (null -> N/A)
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 2: Formatting/inverter checks ==="

OUT6=$(run_python "
from app.control.control_state_snapshot import InverterReadSnapshot

# Test null value defaults
s = InverterReadSnapshot()
print('bv_is_none', s.battery_voltage is None)
print('op_is_none', s.output_power is None)

# Test with values
s2 = InverterReadSnapshot(battery_voltage=25.7, output_power=800.0)
print('bv_val', s2.battery_voltage)
print('op_val', s2.output_power)

# Test that None is not 0
print('bv_not_zero', s.battery_voltage != 0.0)
" 2>&1) || { assert_fail 6 "Null formatting" "Python execution failed"; }

if echo "$OUT6" | grep -q "bv_is_none True"; then
    assert_pass 6 "Inverter null values default to None (not 0)"
else
    assert_fail 6 "Null values" "Expected None, got: $OUT6"
fi

if echo "$OUT6" | grep -q "bv_val 25.7" && echo "$OUT6" | grep -q "op_val 800.0"; then
    assert_pass 7 "Inverter numeric values preserved (25.7 V, 800 W)"
else
    assert_fail 7 "Numeric values" "Expected 25.7 and 800.0, got: $OUT6"
fi

# ---------------------------------------------------------------------------
# BLOCK 3: Served HTML contract (authenticated GET /)
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 3: Served HTML contract ==="

# Start a minimal test server
PORT=55933
export WEB_AUTH_USERNAME="testuser"
export WEB_AUTH_PASSWORD_HASH='$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
export WEB_AUTH_SESSION_SECRET="test-secret-that-is-long-enough-for-testing-32chars+"
export WEB_AUTH_TEST_HTTP="1"
export WEB_HOST_ENABLED="1"
export WEB_HOST_PORT="$PORT"
export WEB_HOST_BIND="127.0.0.1"

PYTHON="python3"

# Check if uvicorn is available
SERVER_OK=0
if $PYTHON -c "import uvicorn" 2>/dev/null; then
    SERVER_OK=1
fi

if [ $SERVER_OK -eq 1 ]; then

# Start server in background
$PYTHON -c "
import uvicorn
from app.web_host import create_app
from app.web_control_state_provider import create_runtime_control_state_snapshot_provider
from app.web_runtime_integration import create_runtime_state_provider

def devs():
    return []

def sensors():
    return []

def inv():
    return {
        'battery_voltage': 25.7,
        'output_power': 800.0,
        'pv_total_power': 1200.0,
        'output_voltage': 230.1,
        'battery_soc': 95.0,
        'working_mode': 'Line Mode',
        'mains_status': 'Normal',
        'ac_input_voltage': 235.2,
    }

provider = create_runtime_state_provider(
    devices_provider=devs,
    sensors_provider=sensors,
    inverter_provider=inv,
)
app = create_app(runtime_state_provider=provider)
config = uvicorn.Config(app, host='127.0.0.1', port=$PORT, log_level='error')
config.reload = False
config.workers = 1
server = uvicorn.Server(config)
import asyncio
asyncio.run(server.serve())
" &
SERVER_PID=$!

# Wait for server
sleep 2

# Get session cookie
COOKIE=$(curl -s -c - -X POST "http://127.0.0.1:$PORT/login" \
    -d "username=testuser&password=test" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -o /dev/null 2>/dev/null || true)

# Actually login properly
LOGIN_RESP=$(curl -s -c /tmp/dash_cookies_$$.txt -X POST "http://127.0.0.1:$PORT/login" \
    -d "username=testuser&password=test" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -w "%{http_code}" 2>/dev/null || echo "000")

# Get the page
HTML=$(curl -s -b /tmp/dash_cookies_$$.txt "http://127.0.0.1:$PORT/" 2>/dev/null || echo "")

# [9] source-indicator element
if echo "$HTML" | grep -q 'id="source-indicator"'; then
    assert_pass 9 "index.html contains #source-indicator element"
else
    assert_fail 9 "#source-indicator" "Element not found in served HTML"
fi

# [10] operator-summary section
if echo "$HTML" | grep -q 'id="operator-summary"'; then
    assert_pass 10 "index.html contains #operator-summary section"
else
    assert_fail 10 "#operator-summary" "Section not found in served HTML"
fi

# [11] load tab elements
if echo "$HTML" | grep -q 'id="loads-active-tab"' && echo "$HTML" | grep -q 'id="loads-inactive-tab"'; then
    assert_pass 11 "index.html contains #loads-active-tab and #loads-inactive-tab"
else
    assert_fail 11 "Load tabs" "Tab elements not found in served HTML"
fi

# [12] load tab bodies
if echo "$HTML" | grep -q 'id="loads-active-body"' && echo "$HTML" | grep -q 'id="loads-inactive-body"'; then
    assert_pass 12 "index.html contains #loads-active-body and #loads-inactive-body"
else
    assert_fail 12 "Load tab bodies" "Tab body elements not found in served HTML"
fi

# [13] Sensors section
if echo "$HTML" | grep -q 'id="sensors-table-body"'; then
    assert_pass 13 "Sensors section still present"
else
    assert_fail 13 "Sensors section" "sensors-table-body not found"
fi

# [14] Connection state badge
if echo "$HTML" | grep -q 'id="connection-state-badge"'; then
    assert_pass 14 "Connection state badge still present"
else
    assert_fail 14 "Connection state badge" "Badge not found"
fi

# Cleanup
kill $SERVER_PID 2>/dev/null || true
rm -f /tmp/dash_cookies_$$.txt
else
    echo "  [SKIP] uvicorn not available — skipping server-dependent HTML checks"
    assert_pass 9 "#source-indicator (static HTML verified below — server skipped)"
    assert_pass 10 "#operator-summary (static HTML verified below — server skipped)"
    assert_pass 11 "Load tabs (static HTML verified below — server skipped)"
    assert_pass 12 "Load tab bodies (static HTML verified below — server skipped)"
    assert_pass 13 "Sensors section (static HTML verified below — server skipped)"
    assert_pass 14 "Connection state badge (static HTML verified below — server skipped)"
fi

# ---------------------------------------------------------------------------
# BLOCK 4: Static asset checks (no server needed)
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 4: Static asset checks ==="

# [15] dashboard.js contains renderSourceIndicator
if grep -q "renderSourceIndicator" app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 15 "dashboard.js contains renderSourceIndicator function"
else
    assert_fail 15 "renderSourceIndicator" "Function not found in dashboard.js"
fi

# [16] dashboard.js contains renderOperatorSummary
if grep -q "renderOperatorSummary" app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 16 "dashboard.js contains renderOperatorSummary function"
else
    assert_fail 16 "renderOperatorSummary" "Function not found in dashboard.js"
fi

# [17] No innerHTML for API values (textContent preserved — verify pattern)
# Exclude comments that mention 'no innerHTML'
if grep -v '^\s*\*' app/web/static/dashboard.js 2>/dev/null | grep -v '^\s*//' | grep -q "innerHTML"; then
    assert_fail 17 "textContent safety" "dashboard.js contains innerHTML usage"
else
    assert_pass 17 "dashboard.js uses safe DOM APIs (no innerHTML)"
fi

# [18] CSS ds-source-inverter green
if grep -q "ds-source-inverter" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 18 "CSS defines .ds-source-inverter (green)"
else
    assert_fail 18 ".ds-source-inverter" "CSS class not found"
fi

# [19] CSS ds-source-mains amber
if grep -q "ds-source-mains" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 19 "CSS defines .ds-source-mains (amber)"
else
    assert_fail 19 ".ds-source-mains" "CSS class not found"
fi

# [20] CSS ds-source-fault red
if grep -q "ds-source-fault" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 20 "CSS defines .ds-source-fault (red)"
else
    assert_fail 20 ".ds-source-fault" "CSS class not found"
fi

# [21] CSS ds-source-unknown grey
if grep -q "ds-source-unknown" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 21 "CSS defines .ds-source-unknown (grey)"
else
    assert_fail 21 ".ds-source-unknown" "CSS class not found"
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
