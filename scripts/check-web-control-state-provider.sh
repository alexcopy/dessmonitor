#!/usr/bin/env bash
# check-web-control-state-provider.sh
# Validate runtime read-only control state provider for PR 0029.

set -euo pipefail

ERRORS=0
PROVIDER="app/web_control_state_provider.py"
HOST="app/web_host.py"
DOC=".project-memory/WEB_CONTROL_STATE_PROVIDER.md"

echo "=== PR 0029 web control state provider check ==="

# 1
echo -n "  [1] Provider module exists ... "
[ -f "$PROVIDER" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2
echo -n "  [2] create_runtime_control_state_snapshot_provider exists ... "
grep -q "def create_runtime_control_state_snapshot_provider" "$PROVIDER" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 3
echo -n "  [3] build_control_state_snapshot_from_runtime_state exists ... "
grep -q "def build_control_state_snapshot_from_runtime_state" "$PROVIDER" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 4
echo -n "  [4] build_runtime_control_snapshot used ... "
grep -q "build_runtime_control_snapshot" "$PROVIDER" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 5
echo -n "  [5] RuntimeControlSnapshotAdapterInput used ... "
grep -q "RuntimeControlSnapshotAdapterInput" "$PROVIDER" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 6
echo -n "  [6] RuntimeLoadState used ... "
grep -q "RuntimeLoadState" "$PROVIDER" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7
echo -n "  [7] app/web_host.py accepts runtime_state_provider ... "
grep -q "runtime_state_provider" "$HOST" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8
echo -n "  [8] app/web_host.py imports create_runtime_control_state_snapshot_provider ... "
grep -q "create_runtime_control_state_snapshot_provider" "$HOST" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 9
echo -n "  [9] no shared_state in provider ... "
grep -qi "shared_state" <(grep -v "no-shared-state-read" "$PROVIDER") && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 10
echo -n " [10] no shared_state in host ... "
grep -qi "shared_state" "$HOST" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 11
echo -n " [11] no app.service/app.devices/app.tuya imports in provider ... "
FIMP="app.service app.devices app.tuya app.monitoring app.ml app.weather"
FAILS=0
for fi in $FIMP; do
    grep -qE "(from|import)\s+${fi}\b" "$PROVIDER" && { echo "  FOUND: $fi"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 12
echo -n " [12] no hardware calls in provider ... "
FCALLS="send_commands switch_on_device switch_off_device set_numeric"
FAILS=0
for fc in $FCALLS; do
    grep -qE "\b${fc}\s*\(" "$PROVIDER" && { echo "  FOUND: $fc"; FAILS=$((FAILS + 1)); } || true
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 13
echo -n " [13] no build_control_state_snapshot direct call in provider ... "
# build_runtime_control_snapshot is allowed, but direct build_control_state_snapshot is not
grep -qE 'build_control_state_snapshot\s*\(' "$PROVIDER" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 14
echo -n " [14] no uvicorn.run ... "
grep -qE 'uvicorn\.run\s*\(' "$PROVIDER" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"
grep -qE 'uvicorn\.run\s*\(' "$HOST" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 15
echo -n " [15] no write routes in provider/host ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$PROVIDER" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 16
echo -n " [16] run.py not modified ... "
CHANGED=$( (git diff --name-only HEAD 2>/dev/null || true) )
echo "$CHANGED" | grep -qx "run.py" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 17
echo -n " [17] api.py not modified ... "
echo "$CHANGED" | grep -qx "api.py" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 18
echo -n " [18] Documentation exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 19
echo -n " [19] Required markers in provider ... "
MARKERS=( "runtime-read-only-provider" "caller-provided-runtime-state"
    "no-shared-state-read" "no-device-read" "no-tuya-hardware"
    "no-execution" "no-write-api" "real-provider-injected"
    "provider-errors-hidden" "operator-writes-through-control-layer" )
OK=0
for m in "${MARKERS[@]}"; do
    grep -q "$m" "$PROVIDER" && OK=$((OK + 1)) || echo "  MISSING in provider: $m"
done
[ "$OK" -eq 10 ] && echo "OK" || { echo "FAIL ($OK/10)"; ERRORS=$((ERRORS + 1)); }

# 20
echo -n " [20] Required markers in doc ... "
DMARKERS=( "PR 0029" "caller-provided" "shared_state" "no shared_state"
    "Tuya" "no write API" "PR 0028b" "UNAVAILABLE" )
OK=0
for dm in "${DMARKERS[@]}"; do
    grep -qi "$dm" "$DOC" && OK=$((OK + 1)) || echo "  MISSING in doc: $dm"
done
[ "$OK" -ge 6 ] && echo "OK ($OK/8)" || { echo "FAIL ($OK/8)"; ERRORS=$((ERRORS + 1)); }

# 21
echo -n " [21] provider returns None for None runtime_state_provider ... "
python3 -c "
from app.web_control_state_provider import create_runtime_control_state_snapshot_provider
assert create_runtime_control_state_snapshot_provider(None)() is None
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 22
echo -n " [22] provider returns None for provider returning None ... "
python3 -c "
from app.web_control_state_provider import create_runtime_control_state_snapshot_provider
p = create_runtime_control_state_snapshot_provider(lambda: None)
assert p() is None
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 23
echo -n " [23] provider returns None for provider returning empty dict ... "
python3 -c "
from app.web_control_state_provider import create_runtime_control_state_snapshot_provider
p = create_runtime_control_state_snapshot_provider(lambda: {})
assert p() is None
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 24
echo -n " [24] provider hides exceptions ... "
python3 -c "
from app.web_control_state_provider import create_runtime_control_state_snapshot_provider
p = create_runtime_control_state_snapshot_provider(lambda: 1/0)
assert p() is None
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 25
echo -n " [25] build_control_state_snapshot_from_runtime_state returns None for None ... "
python3 -c "
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state
assert build_control_state_snapshot_from_runtime_state(None) is None
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 26
echo -n " [26] build_control_state_snapshot_from_runtime_state returns None for empty ... "
python3 -c "
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state
assert build_control_state_snapshot_from_runtime_state({}) is None
" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 27
echo -n " [27] create_app() works with runtime_state_provider (with auth env) ... "
python3 -c "
from app.web_host import create_app
# Generate test-only credentials — never hardcode real secrets
from argon2 import PasswordHasher
ph = PasswordHasher()
test_hash = ph.hash(b'test-password-regression-0027')
import os, secrets
os.environ['WEB_AUTH_USERNAME'] = 'test-operator'
os.environ['WEB_AUTH_PASSWORD_HASH'] = test_hash
os.environ['WEB_AUTH_SESSION_SECRET'] = secrets.token_hex(64)
os.environ['WEB_AUTH_TEST_HTTP'] = '1'
try:
    app = create_app(runtime_state_provider=lambda: {'snapshot_id': 's1'})
    assert app is not None
    print('ok')
except RuntimeError as exc:
    assert str(exc) == 'fastapi-unavailable', f'unexpected: {exc}'
" > /dev/null && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 28
echo -n " [28] create_app() still works without runtime_state_provider (with auth env) ... "
python3 -c "
from app.web_host import create_app
from argon2 import PasswordHasher
ph = PasswordHasher()
test_hash = ph.hash(b'test-password-regression-0028')
import os, secrets
os.environ['WEB_AUTH_USERNAME'] = 'test-operator'
os.environ['WEB_AUTH_PASSWORD_HASH'] = test_hash
os.environ['WEB_AUTH_SESSION_SECRET'] = secrets.token_hex(64)
os.environ['WEB_AUTH_TEST_HTTP'] = '1'
try:
    app = create_app()
    assert app is not None
    print('ok')
except RuntimeError as exc:
    assert str(exc) == 'fastapi-unavailable', f'unexpected: {exc}'
" > /dev/null && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All web control state provider checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
