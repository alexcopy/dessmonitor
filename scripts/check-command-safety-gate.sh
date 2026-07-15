#!/usr/bin/env bash
# check-command-safety-gate.sh
# Validate command safety gate model for PR 0021.

set -euo pipefail

ERRORS=0
MODULE="app/control/command_safety_gate.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/COMMAND_SAFETY_GATES.md"

echo "=== PR 0021 command safety gate check ==="

# 1. Module exists
echo -n "  [1] Module exists ... "
[ -f "$MODULE" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 2-6. Five public types
echo -n "  [2] SafetyGateStatus ... "
grep -q "class SafetyGateStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [3] SafetyGateCheck ... "
grep -q "class SafetyGateCheck" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [4] CommandSafetyContext ... "
grep -q "class CommandSafetyContext" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [5] CommandSafetyGateInput ... "
grep -q "class CommandSafetyGateInput" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }
echo -n "  [6] CommandSafetyGateResult ... "
grep -q "class CommandSafetyGateResult" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 7. evaluate_command_safety_gate
echo -n "  [7] evaluate_command_safety_gate ... "
grep -q "def evaluate_command_safety_gate" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 8. Status values
echo -n "  [8] Status values ... "
OK=0
for v in PASSED BLOCKED REVIEW_REQUIRED NO_PROPOSAL; do
    grep -q "$v" "$MODULE" && OK=$((OK + 1))
done
[ "$OK" -eq 4 ] && echo "OK" || { echo "FAIL ($OK/4)"; ERRORS=$((ERRORS + 1)); }

# 9. execution_allowed
echo -n "  [9] execution_allowed ... "
grep -q "execution_allowed" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 10. requires_operator_review
echo -n " [10] requires_operator_review ... "
grep -q "requires_operator_review" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 11. kill_switch_active
echo -n " [11] kill_switch_active ... "
grep -q "kill_switch_active" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 12. maintenance_mode
echo -n " [12] maintenance_mode ... "
grep -q "maintenance_mode" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 13. battery_voltage and battery_grid_fallback_voltage
echo -n " [13] battery voltage fields ... "
OK=0
grep -q "battery_voltage" "$MODULE" && OK=$((OK + 1))
grep -q "battery_grid_fallback_voltage" "$MODULE" && OK=$((OK + 1))
[ "$OK" -eq 2 ] && echo "OK" || { echo "FAIL ($OK/2)"; ERRORS=$((ERRORS + 1)); }

# 14. max_total_load_watts and projected_total_load_watts
echo -n " [14] load wattage fields ... "
OK=0
grep -q "max_total_load_watts" "$MODULE" && OK=$((OK + 1))
grep -q "projected_total_load_watts" "$MODULE" && OK=$((OK + 1))
[ "$OK" -eq 2 ] && echo "OK" || { echo "FAIL ($OK/2)"; ERRORS=$((ERRORS + 1)); }

# 15. readiness/health/cooldown passed
echo -n " [15] readiness/health/cooldown ... "
OK=0
grep -q "readiness_passed" "$MODULE" && OK=$((OK + 1))
grep -q "health_passed" "$MODULE" && OK=$((OK + 1))
grep -q "cooldown_passed" "$MODULE" && OK=$((OK + 1))
[ "$OK" -eq 3 ] && echo "OK" || { echo "FAIL ($OK/3)"; ERRORS=$((ERRORS + 1)); }

# 16. Required reason strings
echo -n " [16] Required reason strings ... "
REASONS=( "no-proposal" "proposal-not-executable" "kill-switch-active" "maintenance-mode"
    "battery-fallback-block" "inverter-load-cap-block" "readiness-block" "health-block"
    "cooldown-block" "manual-review-required" "manual-override-not-allowed" "passed" )
OK=0
for r in "${REASONS[@]}"; do
    grep -q "\"$r\"" "$MODULE" && OK=$((OK + 1)) || echo "MISSING: $r"
done
[ "$OK" -eq 12 ] && echo "OK" || { echo "FAIL ($OK/12)"; ERRORS=$((ERRORS + 1)); }

# 17. __init__.py exports
echo -n " [17] __init__.py exports ... "
EXPORTS=( SafetyGateStatus SafetyGateCheck CommandSafetyContext CommandSafetyGateInput CommandSafetyGateResult evaluate_command_safety_gate )
OK=0
for e in "${EXPORTS[@]}"; do
    grep -q "\"$e\"" "$INIT_FILE" && OK=$((OK + 1)) || echo "MISSING: $e"
done
[ "$OK" -eq 6 ] && echo "OK" || { echo "FAIL ($OK/6)"; ERRORS=$((ERRORS + 1)); }

# 18. Doc exists
echo -n " [18] COMMAND_SAFETY_GATES.md ... "
[ -f "$DOC" ] && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# 19. No executor
echo -n " [19] No executor ... "
grep -qE 'class\s+CommandExecutor|def\s+execute_command|def\s+execute_proposal' "$MODULE" && { echo "FAIL"; ERRORS=$((ERRORS + 1)); } || echo "OK"

# 20. Forbidden imports
echo -n " [20] Forbidden imports ... "
FIMP="app.tuya app.service app.devices app.monitoring app.ml app.weather smart_home_controller relay_tuya_controller relay_channel_device relay_device_manager device_status_logger openweather dess"
FAILS=0
for fi in $FIMP; do grep -qE "(from|import)\s+${fi}\b" "$MODULE" && { echo "FOUND: $fi"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 21. Forbidden hardware calls
echo -n " [21] Forbidden calls ... "
FCALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands mark_switched can_switch ready_to_switch_on ready_to_switch_off is_device_on"
FAILS=0
for fc in $FCALLS; do grep -qE "\b${fc}\s*\(" "$MODULE" && { echo "FOUND: $fc"; FAILS=$((FAILS + 1)); }; done
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 22. Impurity calls
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
grep -qE '(import|from)\s+urllib\b' "$MODULE" && { echo "FOUND: urllib"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "OK" || { echo "FAIL ($FAILS)"; ERRORS=$((ERRORS + 1)); }

# 23. Runtime files not modified
echo -n " [23] Runtime files ... "
RF="run.py app/service app/devices app/tuya app/monitoring app/ml app/weather examples/energy_policy.example.yaml app/control/domain.py app/control/relay_mapping.py app/control/energy_policy.py app/control/readiness.py app/control/health.py app/control/schedule_profile.py app/control/weather_adjustment.py app/control/policy_models.py app/control/policy_decision.py app/control/manual_control_queue.py app/control/command_arbitration.py"
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
[ "$ERRORS" -eq 0 ] && echo "=== PASS: All command safety gate checks passed ===" && exit 0
echo "=== FAIL: $ERRORS check(s) failed ===" && exit 1
