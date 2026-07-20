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
echo -n "  [2] create_startup_app exists ... "
grep -q "def create_startup_app" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 3
echo -n "  [3] run_read_only_web_host exists ... "
grep -q "def run_read_only_web_host" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 4
echo -n "  [4] DEFAULT_WEB_HOST exists ... "
grep -q "DEFAULT_WEB_HOST" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 5
echo -n "  [5] DEFAULT_WEB_PORT exists ... "
grep -q "DEFAULT_WEB_PORT" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 6
echo -n "  [6] WEB_HOST_STARTUP_READ_ONLY_MODE exists and is True ... "
grep -q "WEB_HOST_STARTUP_READ_ONLY_MODE.*True" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7
echo -n "  [7] uvicorn lazy import exists ... "
grep -q "import uvicorn" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8
echo -n "  [8] uvicorn.run call exists ... "
grep -q "uvicorn.run" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 9
echo -n "  [9] uvicorn-unavailable error exists ... "
grep -q "uvicorn-unavailable" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 10
echo -n " [10] __main__ block exists ... "
grep -q 'if __name__ == "__main__"' "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 11
echo -n " [11] run.py is not modified ... "
CHANGED=$( (git diff --name-only HEAD 2>/dev/null || true) )
echo "$CHANGED" | grep -qx "run.py" && { echo "FAIL (run.py modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 12
echo -n " [12] api.py is not modified ... "
echo "$CHANGED" | grep -qx "api.py" && { echo "FAIL (api.py modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 13
echo -n " [13] no Docker/deploy edits ... "
DOCKER_FILES="Dockerfile docker-compose.yml"
FAILS=0
for df in $DOCKER_FILES; do
    echo "$CHANGED" | grep -qx "$df" && FAILS=$((FAILS + 1)) || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 14
echo -n " [14] no write routes ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 15
echo -n " [15] no shared_state string ... "
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
echo -n " [17] no hardware command strings ... "
FCALLS="send_commands switch_on_device switch_off_device set_numeric"
FAILS=0
for fc in $FCALLS; do
    grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "  FOUND: $fc"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 18
echo -n " [18] Documentation exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 19
echo -n " [19] Required markers in module ... "
MARKERS=( "read-only-web-host-startup" "manual-startup-only" "no-run-py-wiring"
    "no-deployment-wiring" "no-write-api" "no-execution" "no-tuya-hardware"
    "uvicorn-lazy-import" "uvicorn-unavailable"
    "operator-writes-through-control-layer" "ml-control-deferred" )
OK=0
for m in "${MARKERS[@]}"; do
    grep -q "$m" "$MODULE" && OK=$((OK + 1)) || echo "  MISSING in module: $m"
done
[ "$OK" -eq 11 ] && echo "OK" || { echo "FAIL ($OK/11)"; ERRORS=$((ERRORS + 1)); }

# 20
echo -n " [20] Required markers in doc ... "
DMARKERS=( "PR 0030" "run.py" "no write API" "Tuya" "manual startup"
    "runtime_state_provider" "control layer" "ML control" "UNAVAILABLE" )
OK=0
for dm in "${DMARKERS[@]}"; do
    grep -qi "$dm" "$DOC" && OK=$((OK + 1)) || echo "  MISSING in doc: $dm"
done
[ "$OK" -ge 7 ] && echo "OK ($OK/9)" || { echo "FAIL ($OK/9)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All web host startup checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
