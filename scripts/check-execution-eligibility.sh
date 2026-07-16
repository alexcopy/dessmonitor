#!/usr/bin/env bash
# check-execution-eligibility.sh
# Validate controlled execution eligibility model for PR 0022.

set -euo pipefail

ERRORS=0
MODULE="app/control/execution_eligibility.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md"

echo "=== PR 0022 execution eligibility check ==="

# 1. Module exists
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2-6. Five public types
echo -n "  [2] ExecutionEligibilityStatus ... "
grep -q "class ExecutionEligibilityStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [3] ExecutionEligibilityMode ... "
grep -q "class ExecutionEligibilityMode" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [4] ExecutionEligibilityContext ... "
grep -q "class ExecutionEligibilityContext" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [5] ExecutionEligibilityInput ... "
grep -q "class ExecutionEligibilityInput" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [6] ExecutionEligibilityResult ... "
grep -q "class ExecutionEligibilityResult" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7. evaluate_execution_eligibility
echo -n "  [7] evaluate_execution_eligibility ... "
grep -q "def evaluate_execution_eligibility" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8. Status values
echo -n "  [8] ELIGIBLE/BLOCKED/REVIEW_REQUIRED/NO_PROPOSAL ... "
OK=0
for v in ELIGIBLE BLOCKED REVIEW_REQUIRED NO_PROPOSAL; do
    grep -q "$v" "$MODULE" && OK=$((OK + 1))
done
[ "$OK" -eq 4 ] && echo "OK" || { echo "FAIL ($OK/4)"; ERRORS=$((ERRORS + 1)); }

# 9. Mode values
echo -n "  [9] AUTONOMOUS/MANUAL_OPERATOR/DISABLED ... "
OK=0
for v in AUTONOMOUS MANUAL_OPERATOR DISABLED; do
    grep -q "$v" "$MODULE" && OK=$((OK + 1))
done
[ "$OK" -eq 3 ] && echo "OK" || { echo "FAIL ($OK/3)"; ERRORS=$((ERRORS + 1)); }

# 10. eligible_for_future_executor
echo -n " [10] eligible_for_future_executor ... "
grep -q "eligible_for_future_executor" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 11. execution_allowed_now
echo -n " [11] execution_allowed_now ... "
grep -q "execution_allowed_now" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 12. controlled_execution_enabled
echo -n " [12] controlled_execution_enabled ... "
grep -q "controlled_execution_enabled" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 13. dry_run_only
echo -n " [13] dry_run_only ... "
grep -q "dry_run_only" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 14. Required reason strings
echo -n " [14] Required reason strings ... "
REASONS=( "no-proposal" "no-safety-gate" "safety-gate-blocked" "safety-review-required"
    "controlled-execution-disabled" "disabled-load" "autonomous-disabled"
    "manual-operator-disabled" "autonomous-review-required" "manual-review-required"
    "dry-run-only" "eligible" )
OK=0
for r in "${REASONS[@]}"; do
    grep -q "\"$r\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $r"
done
[ "$OK" -eq 12 ] && echo "OK" || { echo "FAIL ($OK/12)"; ERRORS=$((ERRORS + 1)); }

# 15. __init__.py exports
echo -n " [15] __init__.py exports ... "
EXPORTS=( ExecutionEligibilityStatus ExecutionEligibilityMode ExecutionEligibilityContext ExecutionEligibilityInput ExecutionEligibilityResult evaluate_execution_eligibility )
OK=0
for e in "${EXPORTS[@]}"; do
    grep -q "\"$e\"" "$INIT_FILE" && OK=$((OK + 1)) || echo "MISSING: $e"
done
[ "$OK" -eq 6 ] && echo "OK" || { echo "FAIL ($OK/6)"; ERRORS=$((ERRORS + 1)); }

# 16. Doc exists
echo -n " [16] CONTROLLED_EXECUTION_ELIGIBILITY.md ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 17. No executor
echo -n " [17] No executor ... "
grep -qE 'class\s+CommandExecutor|def\s+execute_command|def\s+execute_proposal' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 18. Forbidden imports
echo -n " [18] Forbidden imports ... "
FIMP="app.tuya app.service app.devices app.monitoring app.ml app.weather smart_home_controller relay_tuya_controller relay_channel_device relay_device_manager device_status_logger openweather dess"
FAILS=0
for fi in $FIMP; do grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "FOUND: $fi"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 19. Forbidden hardware calls
echo -n " [19] Forbidden calls ... "
FCALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands mark_switched can_switch ready_to_switch_on ready_to_switch_off is_device_on"
FAILS=0
for fc in $FCALLS; do grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "FOUND: $fc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 20. Impurity calls
echo -n " [20] Impurity calls ... "
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

# 21. execution_allowed_now not True
echo -n " [21] execution_allowed_now never True ... "
# Look for any assignment of True to execution_allowed_now
if grep -qE 'execution_allowed_now\s*=\s*True' "$MODULE"; then
    echo "FAIL (execution_allowed_now = True found)"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# 22. Runtime files not modified
echo -n " [22] Runtime files ... "
RF="run.py app/service app/devices app/tuya app/monitoring app/ml app/weather examples/energy_policy.example.yaml app/control/domain.py app/control/relay_mapping.py app/control/energy_policy.py app/control/readiness.py app/control/health.py app/control/schedule_profile.py app/control/weather_adjustment.py app/control/policy_models.py app/control/policy_decision.py app/control/manual_control_queue.py app/control/command_arbitration.py app/control/command_safety_gate.py"
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
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All execution eligibility checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
