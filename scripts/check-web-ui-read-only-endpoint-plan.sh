#!/usr/bin/env bash
# check-web-ui-read-only-endpoint-plan.sh
# Validate web UI read-only endpoint implementation plan for PR 0026.

set -euo pipefail

ERRORS=0
MODULE="app/control/web_ui_read_endpoint_plan.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/WEB_UI_READ_ONLY_ENDPOINT_PLAN.md"

echo "=== PR 0026 web UI read-only endpoint plan check ==="

# 1
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2-6
echo -n "  [2] WebUiReadEndpointPlanStatus ... "
grep -q "class WebUiReadEndpointPlanStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [3] WebUiReadEndpointDataSource ... "
grep -q "class WebUiReadEndpointDataSource" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [4] WebUiReadEndpointBoundary ... "
grep -q "class WebUiReadEndpointBoundary" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [5] WebUiReadEndpointImplementationStep ... "
grep -q "class WebUiReadEndpointImplementationStep" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [6] WebUiReadEndpointPlan ... "
grep -q "class WebUiReadEndpointPlan" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7
echo -n "  [7] build_web_ui_read_endpoint_plan ... "
grep -q "def build_web_ui_read_endpoint_plan" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8
echo -n "  [8] DRAFT/READY_FOR_FUTURE_IMPLEMENTATION/BLOCKED ... "
OK=0
for v in DRAFT READY_FOR_FUTURE_IMPLEMENTATION BLOCKED; do grep -q "$v" "$MODULE" && OK=$((OK + 1)); done
[ "$OK" -eq 3 ] && echo "OK" || { echo "FAIL ($OK/3)"; ERRORS=$((ERRORS + 1)); }

# 9-13
echo -n "  [9] route_added_now ... "
grep -q "route_added_now" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [10] writes_allowed ... "
grep -q "writes_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [11] execution_allowed ... "
grep -q "execution_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [12] live_shared_state_reads_allowed ... "
grep -q "live_shared_state_reads_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [13] direct_device_reads_allowed ... "
grep -q "direct_device_reads_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 14
echo -n " [14] Forbidden action strings ... "
FACTIONS=( "direct-hardware-write" "direct-tuya-command" "execute-command" "mutate-shared-state"
    "bypass-control-layer" "bypass-safety-gates" "write-api" "route-write-methods" )
OK=0
for fa in "${FACTIONS[@]}"; do grep -q "\"$fa\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $fa"; done
[ "$OK" -eq 8 ] && echo "OK" || { echo "FAIL ($OK/8)"; ERRORS=$((ERRORS + 1)); }

# 15
echo -n " [15] Required note strings ... "
NOTES=( "endpoint-plan-only" "no-real-api-endpoint" "read-only-endpoint-future" "future-separate-pr-required"
    "no-execution" "no-runtime-wiring" "no-write-api" "control-layer-only" "safety-gates-required"
    "operator-writes-through-control-layer" "contract-not-read-only" "default-contract-used" )
OK=0
for n in "${NOTES[@]}"; do grep -q "\"$n\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $n"; done
[ "$OK" -ge 8 ] && echo "OK" || { echo "FAIL ($OK/12)"; ERRORS=$((ERRORS + 1)); }

# 16
echo -n " [16] __init__.py exports ... "
EXPORTS=( WebUiReadEndpointPlanStatus WebUiReadEndpointDataSource WebUiReadEndpointBoundary WebUiReadEndpointImplementationStep WebUiReadEndpointPlan build_web_ui_read_endpoint_plan )
OK=0
for e in "${EXPORTS[@]}"; do grep -q "\"$e\"" "$INIT_FILE" && OK=$((OK + 1)) || echo "MISSING: $e"; done
[ "$OK" -eq 6 ] && echo "OK" || { echo "FAIL ($OK/6)"; ERRORS=$((ERRORS + 1)); }

# 17
echo -n " [17] Doc exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 18
echo -n " [18] endpoint-plan-only language ... "
grep -qi "endpoint.plan\|implementation plan\|no.*endpoint\|no.*route" "$DOC" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 19
echo -n " [19] No FastAPI/APIRouter ... "
grep -qE '(import|from)\s+FastAPI\b|class\s+FastAPI\b|APIRouter' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 20
echo -n " [20] No @router/@app ... "
grep -qE '@router\b|@app\b' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 21
echo -n " [21] No POST/PUT/PATCH/DELETE ... "
grep -qE '\bpost\s*\(|\bput\s*\(|\bpatch\s*\(|\bdelete\s*\(' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 22
echo -n " [22] No runtime imports ... "
FIMP="app.service app.devices app.tuya app.monitoring app.ml app.weather"
FAILS=0
for fi in $FIMP; do grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "FOUND: $fi"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 23
echo -n " [23] No snapshot/adapter/contract builder calls ... "
grep -qE "build_web_ui_control_state_response|build_runtime_control_snapshot|build_control_state_snapshot" "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 24
echo -n " [24] No executor ... "
grep -qE 'class\s+CommandExecutor|def\s+execute_command' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 25
echo -n " [25] Forbidden calls ... "
FCALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands"
FAILS=0
for fc in $FCALLS; do grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "FOUND: $fc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 26
echo -n " [26] Impurity calls ... "
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

# 27
echo -n " [27] Runtime files ... "
RF="run.py api.py app/service app/devices app/tuya app/monitoring app/ml app/weather examples/energy_policy.example.yaml app/control/domain.py app/control/relay_mapping.py app/control/energy_policy.py app/control/readiness.py app/control/health.py app/control/schedule_profile.py app/control/weather_adjustment.py app/control/policy_models.py app/control/policy_decision.py app/control/manual_control_queue.py app/control/command_arbitration.py app/control/command_safety_gate.py app/control/execution_eligibility.py app/control/control_state_snapshot.py app/control/runtime_snapshot_adapter.py app/control/web_ui_read_contract.py"
CHANGED=$(git diff --name-only HEAD 2>/dev/null || true)
CHANGED="$CHANGED
$(git diff --name-only --cached 2>/dev/null || true)"
FAILS=0
for rf in $RF; do while IFS= read -r cf; do [ -n "$cf" ] || continue; if [ "$cf" = "$rf" ] || echo "$cf" | grep -q "^$rf/"; then echo "MODIFIED: $cf"; FAILS=$((FAILS + 1)); fi; done <<< "$CHANGED"; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All web UI read-only endpoint plan checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
