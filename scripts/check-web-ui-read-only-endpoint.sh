#!/usr/bin/env bash
# check-web-ui-read-only-endpoint.sh
# Validate web UI read-only control state endpoint for PR 0027.

set -euo pipefail

ERRORS=0
MODULE="app/control/web_ui_read_endpoint.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/WEB_UI_READ_ONLY_ENDPOINT.md"

echo "=== PR 0027 web UI read-only control state endpoint check ==="

# 1
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2-5: four public types
echo -n "  [2] WebUiReadEndpointStatus ... "
grep -q "class WebUiReadEndpointStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [3] WebUiReadEndpointConfig ... "
grep -q "class WebUiReadEndpointConfig" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [4] WebUiReadEndpointProviderResult ... "
grep -q "class WebUiReadEndpointProviderResult" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [5] WebUiReadEndpointRuntime ... "
grep -q "class WebUiReadEndpointRuntime" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 6-7: both public constants
echo -n "  [6] CONTROL_STATE_READ_PATH ... "
grep -q "CONTROL_STATE_READ_PATH" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [7] CONTROL_STATE_READ_METHOD ... "
grep -q "CONTROL_STATE_READ_METHOD" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8-9: both public functions
echo -n "  [8] build_control_state_endpoint_response ... "
grep -q "def build_control_state_endpoint_response" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [9] create_control_state_read_router ... "
grep -q "def create_control_state_read_router" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 10: OK/DEGRADED/UNAVAILABLE/FASTAPI_UNAVAILABLE
echo -n " [10] OK/DEGRADED/UNAVAILABLE/FASTAPI_UNAVAILABLE ... "
OK=0
for v in OK DEGRADED UNAVAILABLE FASTAPI_UNAVAILABLE; do grep -qE "${v} = " "$MODULE" && OK=$((OK + 1)); done
[ "$OK" -eq 4 ] && echo "OK" || { echo "FAIL ($OK/4)"; ERRORS=$((ERRORS + 1)); }

# 11-12: /control/state and GET
echo -n " [11] /control/state ... "
grep -q '"/control/state"' "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [12] GET ... "
grep -q '"GET"' "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 13-15: route_wired_now, writes_allowed, execution_allowed
echo -n " [13] route_wired_now ... "
grep -q "route_wired_now" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [14] writes_allowed ... "
grep -q "writes_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [15] execution_allowed ... "
grep -q "execution_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 16: required warning/note strings
echo -n " [16] Required warning strings ... "
WSTRINGS=( "snapshot-provider-missing" "snapshot-provider-error" "snapshot-unavailable" )
OK=0
for ws in "${WSTRINGS[@]}"; do grep -q "\"$ws\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $ws"; done
[ "$OK" -eq 3 ] && echo "OK" || { echo "FAIL ($OK/3)"; ERRORS=$((ERRORS + 1)); }

# 17: required note strings
echo -n " [17] Required note strings ... "
NOTES=( "read-only-endpoint" "get-control-state" "no-write-api" "no-execution"
    "no-runtime-wiring" "route-not-wired" "caller-provided-snapshot-provider"
    "operator-writes-through-control-layer" "safety-gates-required"
    "fastapi-lazy-import" "fastapi-unavailable" )
OK=0
for n in "${NOTES[@]}"; do grep -q "\"$n\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $n"; done
[ "$OK" -gt 0 ] && echo "OK ($OK/11)" || { echo "FAIL ($OK/11)"; ERRORS=$((ERRORS + 1)); }

# 18: forbidden action strings
echo -n " [18] Forbidden action strings ... "
FACTIONS=( "direct-hardware-write" "direct-tuya-command" "execute-command"
    "mutate-shared-state" "bypass-control-layer" "bypass-safety-gates"
    "write-api" "route-write-methods" )
OK=0
for fa in "${FACTIONS[@]}"; do grep -q "\"$fa\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $fa"; done
[ "$OK" -eq 8 ] && echo "OK" || { echo "FAIL ($OK/8)"; ERRORS=$((ERRORS + 1)); }

# 19: __init__.py exports
echo -n " [19] __init__.py exports ... "
EXPORTS=( CONTROL_STATE_READ_PATH CONTROL_STATE_READ_METHOD WebUiReadEndpointStatus
    WebUiReadEndpointConfig WebUiReadEndpointProviderResult WebUiReadEndpointRuntime
    build_control_state_endpoint_response create_control_state_read_router )
OK=0
for e in "${EXPORTS[@]}"; do grep -q "\"$e\"" "$INIT_FILE" && OK=$((OK + 1)) || echo "MISSING: $e"; done
[ "$OK" -eq 8 ] && echo "OK" || { echo "FAIL ($OK/8)"; ERRORS=$((ERRORS + 1)); }

# 20: doc exists
echo -n " [20] Doc exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 21: isolated/no-runtime-wiring language
echo -n " [21] Doc: isolated/no-runtime-wiring language ... "
grep -qi "isolated\|no.*runtime.*wiring\|not wired\|route_wired_now" "$DOC" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 22: api.py and run.py not modified
echo -n " [22] api.py and run.py not modified ... "
CHANGED=$( (git diff --name-only HEAD 2>/dev/null || true) && (git diff --name-only --cached 2>/dev/null || true) )
FAILS=0
for rf in api.py run.py; do echo "$CHANGED" | grep -qx "$rf" && { echo "MODIFIED: $rf"; FAILS=$((FAILS + 1)); } || true; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 23: no top-level FastAPI/APIRouter import
echo -n " [23] No top-level FastAPI/APIRouter import ... "
# Get all lines before the first 'def ' and check for fastapi imports there
TOPLEVEL_LINES=$(awk '/^def /{exit} {print}' "$MODULE")
if echo "$TOPLEVEL_LINES" | grep -qE '(import|from)\s+fastapi\b'; then
    echo "FAIL (top-level fastapi import found)"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# 24: lazy APIRouter import inside create_control_state_read_router
echo -n " [24] Lazy APIRouter import inside create_control_state_read_router ... "
# Extract the function body: from 'def create_control_state_read_router' to end of file
awk '/^def create_control_state_read_router/,0' "$MODULE" | grep -qE '(import|from)\s+fastapi\b' && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 25: no POST/PUT/PATCH/DELETE write route
echo -n " [25] No POST/PUT/PATCH/DELETE write route ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 26: no runtime/service/tuya/device imports
echo -n " [26] No runtime/service/tuya/device imports ... "
FIMP="app.service app.devices app.tuya app.monitoring app.ml app.weather"
FAILS=0
for fi in $FIMP; do grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "FOUND: $fi"; FAILS=$((FAILS + 1)); } || true; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 27: no executor introduced
echo -n " [27] No executor ... "
grep -qE 'class\s+CommandExecutor|def\s+execute_command' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 28: forbidden hardware calls
echo -n " [28] Forbidden hardware calls ... "
FCALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands build_runtime_control_snapshot build_control_state_snapshot"
FAILS=0
for fc in $FCALLS; do grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "FOUND: $fc"; FAILS=$((FAILS + 1)); } || true; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 29: impurity calls
echo -n " [29] Impurity calls ... "
FAILS=0
grep -qE '\btime\.time\s*\(' "$MODULE" && { echo "FOUND: time.time"; FAILS=$((FAILS + 1)); } || true
grep -qE '\bdatetime\.now\s*\(' "$MODULE" && { echo "FOUND: datetime.now"; FAILS=$((FAILS + 1)); } || true
grep -qE '\bopen\s*\(' "$MODULE" && { echo "FOUND: open("; FAILS=$((FAILS + 1)); } || true
grep -qE '\bos\.getenv\s*\(' "$MODULE" && { echo "FOUND: os.getenv"; FAILS=$((FAILS + 1)); } || true
grep -qE '\byaml\.safe_load\s*\(' "$MODULE" && { echo "FOUND: yaml.safe_load"; FAILS=$((FAILS + 1)); } || true
grep -qE '(import|from)\s+requests\b' "$MODULE" && { echo "FOUND: requests"; FAILS=$((FAILS + 1)); } || true
grep -qE '(import|from)\s+aiohttp\b' "$MODULE" && { echo "FOUND: aiohttp"; FAILS=$((FAILS + 1)); } || true
grep -qE '(import|from)\s+subprocess\b' "$MODULE" && { echo "FOUND: subprocess"; FAILS=$((FAILS + 1)); } || true
grep -qE '(import|from)\s+logging\b' "$MODULE" && { echo "FOUND: logging"; FAILS=$((FAILS + 1)); } || true
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 30: runtime files not modified from HEAD
echo -n " [30] Runtime files not modified from HEAD ... "
RF="run.py api.py app/service app/devices app/tuya app/monitoring app/ml app/weather app/control/domain.py app/control/relay_mapping.py app/control/energy_policy.py app/control/readiness.py app/control/health.py app/control/schedule_profile.py app/control/weather_adjustment.py app/control/policy_models.py app/control/policy_decision.py app/control/manual_control_queue.py app/control/command_arbitration.py app/control/command_safety_gate.py app/control/execution_eligibility.py app/control/control_state_snapshot.py app/control/runtime_snapshot_adapter.py app/control/web_ui_read_contract.py app/control/web_ui_read_endpoint_plan.py"
FAILS=0
for rf in $RF; do
    while IFS= read -r cf; do
        [ -n "$cf" ] || continue
        if [ "$cf" = "$rf" ] || echo "$cf" | grep -q "^$rf/"; then
            echo "MODIFIED: $cf"
            FAILS=$((FAILS + 1))
        fi
    done <<< "$CHANGED"
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All web UI read-only endpoint checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
