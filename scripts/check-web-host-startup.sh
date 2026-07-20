#!/usr/bin/env bash
# check-web-host-startup.sh
# Validate runtime read-only web host startup for PR 0030.

set -euo pipefail

ERRORS=0
MODULE="app/web_host_startup.py"
DOC=".project-memory/WEB_HOST_STARTUP.md"

echo "=== PR 0030 web host startup check ==="

# 1
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2
echo -n "  [2] WEB_HOST_STARTUP_READ_ONLY_MODE is True ... "
grep -q "WEB_HOST_STARTUP_READ_ONLY_MODE.*True" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 3
echo -n "  [3] DEFAULT_WEB_HOST is 0.0.0.0 ... "
grep -q 'DEFAULT_WEB_HOST.*"0.0.0.0"' "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 4
echo -n "  [4] DEFAULT_WEB_PORT is 8000 ... "
grep -q "DEFAULT_WEB_PORT.*8000" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 5
echo -n "  [5] create_startup_app exists ... "
grep -q "def create_startup_app" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 6
echo -n "  [6] run_read_only_web_host exists ... "
grep -q "def run_read_only_web_host" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7
echo -n "  [7] create_app is used ... "
grep -q "create_app" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8
echo -n "  [8] runtime_state_provider is passed through ... "
grep -q "runtime_state_provider" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 9
echo -n "  [9] uvicorn import is inside run_read_only_web_host ... "
grep -A 100 "def run_read_only_web_host" "$MODULE" | grep -q "import uvicorn" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 10
echo -n " [10] uvicorn-unavailable handling exists ... "
grep -q "uvicorn-unavailable" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 11
echo -n " [11] uvicorn.run exists ... "
grep -q "uvicorn.run" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 12
echo -n " [12] reload is not enabled (uvicorn.run has no reload=True) ... "
UVICORN_RUN_LINE=$(grep "uvicorn.run" "$MODULE" || true)
if echo "$UVICORN_RUN_LINE" | grep -qi "reload"; then
    echo "FAIL"; ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# 13
echo -n " [13] workers are not configured (uvicorn.run has no workers) ... "
if echo "$UVICORN_RUN_LINE" | grep -qi "workers"; then
    echo "FAIL"; ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# 14
echo -n " [14] __main__ block exists ... "
grep -q 'if __name__ == "__main__"' "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 15
echo -n " [15] no shared_state reference ... "
grep -qi "shared_state" "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 16
echo -n " [16] no app.service/app.devices/app.tuya imports ... "
FIMP="app.service app.devices app.tuya app.monitoring app.ml app.weather"
FAILS=0
for fi in $FIMP; do
    grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "  FOUND: $fi"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 17
echo -n " [17] no hardware command calls ... "
FCALLS="send_commands switch_on_device switch_off_device set_numeric"
FAILS=0
for fc in $FCALLS; do
    grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "  FOUND: $fc"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 18
echo -n " [18] no write routes ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 19
echo -n " [19] run.py is not changed relative to HEAD ... "
CHANGED=$( (git diff --name-only HEAD 2>/dev/null || true) )
echo "$CHANGED" | grep -qx "run.py" && { echo "FAIL (run.py modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 20
echo -n " [20] api.py is not changed relative to HEAD ... "
echo "$CHANGED" | grep -qx "api.py" && { echo "FAIL (api.py modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 21
echo -n " [21] Dockerfile is not changed relative to HEAD ... "
echo "$CHANGED" | grep -qx "Dockerfile" && { echo "FAIL (Dockerfile modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 22
echo -n " [22] docker-compose.yml is not changed relative to HEAD ... "
echo "$CHANGED" | grep -qx "docker-compose.yml" && { echo "FAIL (docker-compose.yml modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 23
echo -n " [23] WEB_HOST_STARTUP.md exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 24
echo -n " [24] Required safety markers in module ... "
MARKERS=( "read-only-web-host-startup" "manual-startup-only"
    "existing-container-startup-unchanged" "no-run-py-wiring"
    "no-container-entrypoint-change" "no-deployment-wiring"
    "no-write-api" "no-execution" "no-tuya-hardware"
    "uvicorn-lazy-import" "uvicorn-unavailable"
    "operator-writes-through-control-layer" "safety-gates-required"
    "ml-control-deferred" )
OK=0
TOTAL=${#MARKERS[@]}
for m in "${MARKERS[@]}"; do
    grep -q "$m" "$MODULE" && OK=$((OK + 1)) || echo "  MISSING in module: $m"
done
[ "$OK" -eq "$TOTAL" ] && echo "OK" || { echo "FAIL ($OK/$TOTAL)"; ERRORS=$((ERRORS + 1)); }

# 25
echo -n " [25] Required markers in doc ... "
DMARKERS=( "PR 0030" "run.py" "no write API" "Tuya" "manual startup"
    "runtime_state_provider" "control layer" "ML control" "UNAVAILABLE"
    "container" "Dockerfile" "docker-compose" )
OK=0
TOTAL_D=${#DMARKERS[@]}
for dm in "${DMARKERS[@]}"; do
    grep -qi "$dm" "$DOC" && OK=$((OK + 1)) || echo "  MISSING in doc: $dm"
done
[ "$OK" -ge 9 ] && echo "OK ($OK/$TOTAL_D)" || { echo "FAIL ($OK/$TOTAL_D)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All web host startup checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
