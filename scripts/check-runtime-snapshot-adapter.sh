#!/usr/bin/env bash
# check-runtime-snapshot-adapter.sh
# Validate runtime read-only control snapshot adapter for PR 0024.

set -euo pipefail

ERRORS=0
MODULE="app/control/runtime_snapshot_adapter.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/RUNTIME_CONTROL_SNAPSHOT_ADAPTER.md"

echo "=== PR 0024 runtime snapshot adapter check ==="

# 1
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2-6
echo -n "  [2] RuntimeSnapshotAdapterStatus ... "
grep -q "class RuntimeSnapshotAdapterStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [3] RuntimeLoadState ... "
grep -q "class RuntimeLoadState" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [4] RuntimeControlModeState ... "
grep -q "class RuntimeControlModeState" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [5] RuntimeControlSnapshotAdapterInput ... "
grep -q "class RuntimeControlSnapshotAdapterInput" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [6] RuntimeControlSnapshotAdapterResult ... "
grep -q "class RuntimeControlSnapshotAdapterResult" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7
echo -n "  [7] build_runtime_control_snapshot ... "
grep -q "def build_runtime_control_snapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8
echo -n "  [8] OK/DEGRADED/UNKNOWN ... "
OK=0
for v in OK DEGRADED UNKNOWN; do grep -q "$v" "$MODULE" && OK=$((OK + 1)); done
[ "$OK" -eq 3 ] && echo "OK" || { echo "FAIL ($OK/3)"; ERRORS=$((ERRORS + 1)); }

# 9-12 type exists checks (additional)
echo -n "  [9] RuntimeLoadState type ... "
grep -q "class RuntimeLoadState" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [10] RuntimeControlModeState type ... "
grep -q "class RuntimeControlModeState" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [11] RuntimeControlSnapshotAdapterInput type ... "
grep -q "class RuntimeControlSnapshotAdapterInput" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n " [12] RuntimeControlSnapshotAdapterResult type ... "
grep -q "class RuntimeControlSnapshotAdapterResult" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 13
echo -n " [13] runtime_state ... "
grep -q "runtime_state" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 14
echo -n " [14] read-only/no-execution strings ... "
OK=0
grep -q "read-only-adapter" "$MODULE" && OK=$((OK + 1))
grep -q "no-execution" "$MODULE" && OK=$((OK + 1))
grep -q "no-runtime-wiring" "$MODULE" && OK=$((OK + 1))
grep -q "future-web-ui-read-model" "$MODULE" && OK=$((OK + 1))
[ "$OK" -ge 2 ] && echo "OK" || { echo "FAIL ($OK/4)"; ERRORS=$((ERRORS + 1)); }

# 15
echo -n " [15] Warning strings ... "
WARNINGS=( "no-input" "missing-runtime-state" "missing-loads" "partial-runtime-state"
    "read-only-snapshot" "read-only-adapter" "no-execution" "no-runtime-wiring"
    "caller-provided-state" "future-web-ui-read-model" )
OK=0
for w in "${WARNINGS[@]}"; do
    grep -q "\"$w\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $w"
done
[ "$OK" -ge 6 ] && echo "OK" || { echo "FAIL ($OK/10)"; ERRORS=$((ERRORS + 1)); }

# 16
echo -n " [16] __init__.py exports ... "
EXPORTS=( RuntimeSnapshotAdapterStatus RuntimeLoadState RuntimeControlModeState RuntimeControlSnapshotAdapterInput RuntimeControlSnapshotAdapterResult build_runtime_control_snapshot )
OK=0
for e in "${EXPORTS[@]}"; do
    grep -q "\"$e\"" "$INIT_FILE" && OK=$((OK + 1)) || echo "MISSING: $e"
done
[ "$OK" -eq 6 ] && echo "OK" || { echo "FAIL ($OK/6)"; ERRORS=$((ERRORS + 1)); }

# 17
echo -n " [17] Doc exists ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 18
echo -n " [18] No runtime imports ... "
FIMP="app.service app.devices app.tuya app.monitoring app.ml app.weather shared_state import run"
FAILS=0
for fi in $FIMP; do
    grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "FOUND: $fi"; FAILS=$((FAILS + 1)); }
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 19
echo -n " [19] No evaluator/arbitrator calls ... "
BADCALLS="evaluate_policy_decision arbitrate_command_intent evaluate_command_safety_gate evaluate_execution_eligibility"
FAILS=0
for bc in $BADCALLS; do grep -qE "\b${bc}\s*\(" "$MODULE" && { echo "FOUND: $bc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 20
echo -n " [20] No executor ... "
grep -qE 'class\s+CommandExecutor|def\s+execute_command' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 21
echo -n " [21] Forbidden calls ... "
FCALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands"
FAILS=0
for fc in $FCALLS; do grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "FOUND: $fc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 22
echo -n " [22] Impurity calls ... "
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

# 23
echo -n " [23] Runtime files ... "
RF="run.py app/service app/devices app/tuya app/monitoring app/ml app/weather examples/energy_policy.example.yaml app/control/domain.py app/control/relay_mapping.py app/control/energy_policy.py app/control/readiness.py app/control/health.py app/control/schedule_profile.py app/control/weather_adjustment.py app/control/policy_models.py app/control/policy_decision.py app/control/manual_control_queue.py app/control/command_arbitration.py app/control/command_safety_gate.py app/control/execution_eligibility.py app/control/control_state_snapshot.py"
CHANGED=$(git diff --name-only HEAD 2>/dev/null || true)
CHANGED="$CHANGED
$(git diff --name-only --cached 2>/dev/null || true)"
FAILS=0
for rf in $RF; do while IFS= read -r cf; do [ -n "$cf" ] || continue; if [ "$cf" = "$rf" ] || echo "$cf" | grep -q "^$rf/"; then echo "MODIFIED: $cf"; FAILS=$((FAILS + 1)); fi; done <<< "$CHANGED"; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All runtime snapshot adapter checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
