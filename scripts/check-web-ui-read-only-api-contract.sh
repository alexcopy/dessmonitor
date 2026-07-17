#!/usr/bin/env bash
# check-web-ui-read-only-api-contract.sh
# Validate web UI read-only API contract for PR 0025.

set -euo pipefail

ERRORS=0
MODULE="app/control/web_ui_read_contract.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/WEB_UI_READ_ONLY_API_CONTRACT.md"

echo "=== PR 0025 web UI read-only API contract check ==="

# 1
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2-5
echo -n "  [2] WebUiReadContractStatus ... "
grep -q "class WebUiReadContractStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [3] WebUiReadEndpointContract ... "
grep -q "class WebUiReadEndpointContract" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [4] WebUiControlStateResponse ... "
grep -q "class WebUiControlStateResponse" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [5] WebUiReadContract ... "
grep -q "class WebUiReadContract" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 6
echo -n "  [6] build_web_ui_control_state_response ... "
grep -q "def build_web_ui_control_state_response" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7
echo -n "  [7] OK/DEGRADED/UNAVAILABLE ... "
OK=0
for v in OK DEGRADED UNAVAILABLE; do grep -q "$v" "$MODULE" && OK=$((OK + 1)); done
[ "$OK" -eq 3 ] && echo "OK" || { echo "FAIL ($OK/3)"; ERRORS=$((ERRORS + 1)); }

# 8-11
echo -n "  [8] read_only ... "
grep -q "read_only" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [9] allowed_actions ... "
grep -q "allowed_actions" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [10] forbidden_actions ... "
grep -q "forbidden_actions" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [11] write_actions_allowed ... "
grep -q "write_actions_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 12
echo -n " [12] Forbidden action strings ... "
FACTIONS=( "direct-hardware-write" "direct-tuya-command" "execute-command" "mutate-shared-state"
    "bypass-control-layer" "bypass-safety-gates" "write-api" )
OK=0
for fa in "${FACTIONS[@]}"; do
    grep -q "\"$fa\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $fa"
done
[ "$OK" -eq 7 ] && echo "OK" || { echo "FAIL ($OK/7)"; ERRORS=$((ERRORS + 1)); }

# 13
echo -n " [13] Required note/warning strings ... "
NOTES=( "read-only-api-contract" "future-web-ui-read-model" "operator-writes-through-control-layer"
    "no-execution" "contract-only" "no-real-api-endpoint" "read-control-state"
    "control-state-snapshot-unavailable" "control-state-snapshot-degraded" "control-state-snapshot-unknown" )
OK=0
for n in "${NOTES[@]}"; do
    grep -q "\"$n\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $n"
done
[ "$OK" -ge 6 ] && echo "OK" || { echo "FAIL ($OK/10)"; ERRORS=$((ERRORS + 1)); }

# 14
echo -n " [14] __init__.py exports ... "
EXPORTS=( WebUiReadContractStatus WebUiReadEndpointContract WebUiControlStateResponse WebUiReadContract build_web_ui_control_state_response )
OK=0
for e in "${EXPORTS[@]}"; do
    grep -q "\"$e\"" "$INIT_FILE" && OK=$((OK + 1)) || echo "MISSING: $e"
done
[ "$OK" -eq 5 ] && echo "OK" || { echo "FAIL ($OK/5)"; ERRORS=$((ERRORS + 1)); }

# 15
echo -n " [15] Doc exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 16
echo -n " [16] contract-only language ... "
grep -qi "contract.only\|no.*endpoint\|no.*route" "$DOC" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 17
echo -n " [17] No FastAPI/APIRouter ... "
BADCALLS="FastAPI APIRouter"
FAILS=0
for bc in $BADCALLS; do grep -qE "(import|from)\s+${bc}\b|class\s+${bc}\b" "$MODULE" && { echo "FOUND: $bc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 18
echo -n " [18] No @router/@app ... "
grep -qE '@router\b|@app\b' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 19
echo -n " [19] No POST/PUT/PATCH/DELETE ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 20
echo -n " [20] No runtime imports ... "
FIMP="app.service app.devices app.tuya app.monitoring app.ml app.weather"
FAILS=0
for fi in $FIMP; do grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "FOUND: $fi"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 21
echo -n " [21] No snapshot/adapter calls ... "
grep -qE "build_control_state_snapshot|build_runtime_control_snapshot" "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 22
echo -n " [22] No executor ... "
grep -qE 'class\s+CommandExecutor|def\s+execute_command' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 23
echo -n " [23] Forbidden calls ... "
FCALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands"
FAILS=0
for fc in $FCALLS; do grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "FOUND: $fc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 24
echo -n " [24] Impurity calls ... "
FAILS=0
grep -qE '\btime\.time\s*\(' "$MODULE" && { echo "FOUND: time.time"; FAILS=$((FAILS + 1)); }
grep -qE '\bdatetime\.now\s*\(' "$MODULE" && { echo "FOUND: datetime.now"; FAILS=$((FAILS + 1)); }
grep -qE '\bopen\s*\(' "$MODULE" && { echo "FOUND: open("; FAILS=$((FAILS + 1)); }
grep -qE '\bos\.getenv\s*\(' "$MODULE" && { echo "FOUND: os.getenv"; FAILS=$((FAILS + 1)); }
grep -qE '\byaml\.safe_load\s*\(' "$MODULE" && { echo "FOUND: yaml.safe_load"; FAILS=$((FAILS + 1)); }
grep -qE '(import|from)\s+requests\b' "$MODULE" && { echo "FOUND: requests"; FAILS=$((FAILS + 1)); }
grep -qE '(import|from)\s+aiohttp\b' "$MODULE" && { echo "FOUND: aiohttp"; FAILS=$((FAILS + 1)); }
grep -qE '(import|from)\s+subprocess\b' "$MODULE" && { echo "FOUND: subprocess"; FAILS=$((FAILS + 1)); }
grep -qE '(import|from)\s+logging\b' "$MODULE" && { echo "FOUND: logging"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 25
echo -n " [25] Runtime files ... "
RF="run.py api.py app/service app/devices app/tuya app/monitoring app/ml app/weather examples/energy_policy.example.yaml app/control/domain.py app/control/relay_mapping.py app/control/energy_policy.py app/control/readiness.py app/control/health.py app/control/schedule_profile.py app/control/weather_adjustment.py app/control/policy_models.py app/control/policy_decision.py app/control/manual_control_queue.py app/control/command_arbitration.py app/control/command_safety_gate.py app/control/execution_eligibility.py app/control/control_state_snapshot.py app/control/runtime_snapshot_adapter.py"
CHANGED=$(git diff --name-only HEAD 2>/dev/null || true)
CHANGED="$CHANGED
$(git diff --name-only --cached 2>/dev/null || true)"
FAILS=0
for rf in $RF; do while IFS= read -r cf; do [ -n "$cf" ] || continue; if [ "$cf" = "$rf" ] || echo "$cf" | grep -q "^$rf/"; then echo "MODIFIED: $cf"; FAILS=$((FAILS + 1)); fi; done <<< "$CHANGED"; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All web UI read-only API contract checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
