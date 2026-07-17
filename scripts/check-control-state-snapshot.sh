#!/usr/bin/env bash
# check-control-state-snapshot.sh
# Validate read-only control state snapshot for PR 0023.

set -euo pipefail

ERRORS=0
MODULE="app/control/control_state_snapshot.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/CONTROL_STATE_SNAPSHOT.md"

echo "=== PR 0023 control state snapshot check ==="

# 1. Module exists
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2-7. Six public types
echo -n "  [2] ControlStateSnapshotStatus ... "
grep -q "class ControlStateSnapshotStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [3] LoadControlSnapshot ... "
grep -q "class LoadControlSnapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [4] ControlPipelineSnapshot ... "
grep -q "class ControlPipelineSnapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [5] ControlModeSnapshot ... "
grep -q "class ControlModeSnapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [6] ControlStateSnapshotInput ... "
grep -q "class ControlStateSnapshotInput" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [7] ControlStateSnapshot ... "
grep -q "class ControlStateSnapshot\b" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8. build_control_state_snapshot
echo -n "  [8] build_control_state_snapshot ... "
grep -q "def build_control_state_snapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 9. Status values
echo -n "  [9] OK/DEGRADED/BLOCKED/UNKNOWN ... "
OK=0
for v in OK DEGRADED BLOCKED UNKNOWN; do grep -q "$v" "$MODULE" && OK=$((OK + 1)); done
[ "$OK" -eq 4 ] && echo "OK" || { echo "FAIL ($OK/4)"; ERRORS=$((ERRORS + 1)); }

# 10. LoadControlSnapshot fields
echo -n " [10] LoadControlSnapshot ... "
grep -q "class LoadControlSnapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 11. ControlPipelineSnapshot
echo -n " [11] ControlPipelineSnapshot ... "
grep -q "class ControlPipelineSnapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 12. ControlModeSnapshot
echo -n " [12] ControlModeSnapshot ... "
grep -q "class ControlModeSnapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 13. execution_allowed_now
echo -n " [13] execution_allowed_now ... "
grep -q "execution_allowed_now" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 14. eligible_for_future_executor
echo -n " [14] eligible_for_future_executor ... "
grep -q "eligible_for_future_executor" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 15. Required warning strings
echo -n " [15] Required warning strings ... "
WARNINGS=( "no-input" "missing-policy-decision" "missing-command-proposal"
    "missing-safety-gate-result" "missing-execution-eligibility"
    "safety-gate-blocked" "execution-eligibility-blocked" "review-required"
    "read-only-snapshot" "no-execution" )
OK=0
for w in "${WARNINGS[@]}"; do
    grep -q "\"$w\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $w"
done
[ "$OK" -eq 10 ] && echo "OK" || { echo "FAIL ($OK/10)"; ERRORS=$((ERRORS + 1)); }

# 16. __init__.py exports
echo -n " [16] __init__.py exports ... "
EXPORTS=( ControlStateSnapshotStatus LoadControlSnapshot ControlPipelineSnapshot ControlModeSnapshot ControlStateSnapshotInput ControlStateSnapshot build_control_state_snapshot )
OK=0
for e in "${EXPORTS[@]}"; do
    grep -q "\"$e\"" "$INIT_FILE" && OK=$((OK + 1)) || echo "MISSING: $e"
done
[ "$OK" -eq 7 ] && echo "OK" || { echo "FAIL ($OK/7)"; ERRORS=$((ERRORS + 1)); }

# 17. Doc exists
echo -n " [17] CONTROL_STATE_SNAPSHOT.md ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 18. Read-only/no-execution language in doc
echo -n " [18] read-only/no-execution language ... "
OK=0
grep -qi "read.only" "$DOC" && OK=$((OK + 1))
grep -qi "no.*execute\|does not execute" "$DOC" && OK=$((OK + 1))
[ "$OK" -ge 1 ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 19. No evaluator/arbitrator calls
echo -n " [19] No evaluator/arbitrator calls ... "
BADCALLS="evaluate_policy_decision arbitrate_command_intent evaluate_command_safety_gate evaluate_execution_eligibility"
FAILS=0
for bc in $BADCALLS; do grep -qE "\b${bc}\s*\(" "$MODULE" && { echo "FOUND: $bc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 20. No executor
echo -n " [20] No executor ... "
grep -qE 'class\s+CommandExecutor|def\s+execute_command|def\s+execute_proposal' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 21. Forbidden imports
echo -n " [21] Forbidden imports ... "
FIMP="app.tuya app.service app.devices app.monitoring app.ml app.weather smart_home_controller relay_tuya_controller relay_channel_device relay_device_manager device_status_logger openweather dess"
FAILS=0
for fi in $FIMP; do grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "FOUND: $fi"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 22. Forbidden hardware calls
echo -n " [22] Forbidden calls ... "
FCALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands mark_switched can_switch ready_to_switch_on ready_to_switch_off is_device_on"
FAILS=0
for fc in $FCALLS; do grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "FOUND: $fc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 23. Impurity calls
echo -n " [23] Impurity calls ... "
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
grep -qE '(import|from)\s+urllib\b' "$MODULE" && { echo "FOUND: urllib"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 24. Runtime files not modified
echo -n " [24] Runtime files ... "
RF="run.py app/service app/devices app/tuya app/monitoring app/ml app/weather examples/energy_policy.example.yaml app/control/domain.py app/control/relay_mapping.py app/control/energy_policy.py app/control/readiness.py app/control/health.py app/control/schedule_profile.py app/control/weather_adjustment.py app/control/policy_models.py app/control/policy_decision.py app/control/manual_control_queue.py app/control/command_arbitration.py app/control/command_safety_gate.py app/control/execution_eligibility.py"
CHANGED=$(git diff --name-only HEAD 2>/dev/null || true)
CHANGED="$CHANGED
$(git diff --name-only --cached 2>/dev/null || true)"
FAILS=0
for rf in $RF; do
    while IFS= read -r cf; do
        [ -n "$cf" ] || continue
        if [ "$cf" = "$rf" ] || echo "$cf" | grep -q "^$rf/"; then
            echo "MODIFIED: $cf"; FAILS=$((FAILS + 1))
        fi
    done <<< "$CHANGED"
done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

echo ""
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All control state snapshot checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
