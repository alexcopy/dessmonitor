#!/usr/bin/env bash
# check-web-host-bootstrap.sh
# Validate minimal read-only FastAPI web host bootstrap for PR 0028b.

set -euo pipefail

ERRORS=0
MODULE="app/web_host.py"
DOC=".project-memory/WEB_HOST_BOOTSTRAP.md"

echo "=== PR 0028b web host bootstrap check ==="

# 1
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2
echo -n "  [2] create_app exists ... "
grep -q "def create_app" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 3
echo -n "  [3] create_placeholder_control_state_snapshot_provider exists ... "
grep -q "def create_placeholder_control_state_snapshot_provider" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 4
echo -n "  [4] WEB_HOST_READ_ONLY_MODE exists ... "
grep -q "WEB_HOST_READ_ONLY_MODE" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 5
echo -n "  [5] WEB_HOST_READ_ONLY_MODE = True ... "
grep -q "WEB_HOST_READ_ONLY_MODE.*True" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 6
echo -n "  [6] create_control_state_read_router import exists ... "
grep -q "create_control_state_read_router" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7
echo -n "  [7] include_router usage exists ... "
grep -q "include_router" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8
echo -n "  [8] placeholder provider returns None ... "
# Check that the lambda returns None
grep -q "return lambda: None" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 9
echo -n "  [9] no uvicorn.run ... "
grep -qE 'uvicorn\.run\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 10
echo -n " [10] no server start code ... "
grep -qE '\b(serve|run)\s*\(' "$MODULE" && grep -vE 'def (serve|run)' > /dev/null 2>&1 <<<"$(grep -E '\b(serve|run)\s*\(' "$MODULE" || true)" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 11
echo -n " [11] no post(/put(/patch(/delete( ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 12
echo -n " [12] no shared_state string ... "
grep -qi "shared_state" "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 13
echo -n " [13] no app.service/app.devices/app.tuya imports ... "
FIMP="app.service app.devices app.tuya app.monitoring app.ml app.weather"
FAILS=0
for fi in $FIMP; do
    grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "  FOUND: $fi"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 14
echo -n " [14] no send_commands/switch_on_device/switch_off_device/set_numeric calls ... "
FCALLS="send_commands switch_on_device switch_off_device set_numeric"
FAILS=0
for fc in $FCALLS; do
    grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "  FOUND: $fc"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 15
echo -n " [15] no build_runtime_control_snapshot/build_control_state_snapshot calls ... "
FCALLS2="build_runtime_control_snapshot build_control_state_snapshot"
FAILS=0
for fc in $FCALLS2; do
    grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "  FOUND: $fc"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 16
echo -n " [16] run.py is not modified ... "
CHANGED=$( (git diff --name-only HEAD 2>/dev/null || true) )
echo "$CHANGED" | grep -qx "run.py" && { echo "FAIL (run.py modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 17
echo -n " [17] api.py is not modified ... "
echo "$CHANGED" | grep -qx "api.py" && { echo "FAIL (api.py modified)"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 18
echo -n " [18] .project-memory/WEB_HOST_BOOTSTRAP.md exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 19
echo -n " [19] no-write-api/no-execution/no-runtime-wiring language ... "
LANG="no-write-api no-execution no-runtime-wiring"
OK=0
for l in $LANG; do
    grep -q "$l" "$MODULE" && OK=$((OK + 1)) || true
done
# Also check doc
for l in $LANG; do
    grep -q "$l" "$DOC" && OK=$((OK + 1)) || true
done
[ "$OK" -ge 3 ] && echo "OK ($OK)" || { echo "FAIL ($OK)"; ERRORS=$((ERRORS + 1)); }

# 20
echo -n " [20] Required strings in module ... "
RSTRINGS=( "read-only-web-host" "create-app-only" "no-runtime-wiring" "no-server-start"
    "no-write-api" "no-execution" "placeholder-provider" "returns-unavailable"
    "real-provider-deferred" "operator-writes-through-control-layer"
    "safety-gates-required" "no-tuya-hardware" )
OK=0
for rs in "${RSTRINGS[@]}"; do
    grep -q "$rs" "$MODULE" && OK=$((OK + 1)) || echo "  MISSING in module: $rs"
done
[ "$OK" -eq 12 ] && echo "OK" || { echo "FAIL ($OK/12)"; ERRORS=$((ERRORS + 1)); }

# 21
echo -n " [21] Required strings in doc ... "
DOCSTRINGS=( "no Docker" "no write API" "ML control" "Tuya" "shared_state"
    "safety-reviewed" "control-layer" "PR 0029" "UNAVAILABLE" )
OK=0
for ds in "${DOCSTRINGS[@]}"; do
    grep -qi "$ds" "$DOC" && OK=$((OK + 1)) || echo "  MISSING in doc: $ds"
done
[ "$OK" -ge 7 ] && echo "OK ($OK/9)" || { echo "FAIL ($OK/9)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All web host bootstrap checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
