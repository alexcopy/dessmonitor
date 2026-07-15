#!/usr/bin/env bash
# check-command-arbitration.sh
# Validate command intent/proposal arbitration for PR 0020.
#
# Checks:
#   - Module exists, 6 public types, arbitrate_command_intent function
#   - Enum values (AUTO_POLICY, MANUAL_OPERATOR)
#   - Key fields (execution_eligible, requires_operator_review, safety_blocked_by)
#   - All required reason strings
#   - __init__.py exports
#   - Document exists with autonomous/override language
#   - No CommandQueue, no executor
#   - No forbidden imports, hardware calls, impurity
#   - Runtime files not modified

set -euo pipefail

ERRORS=0
MODULE="app/control/command_arbitration.py"
INIT_FILE="app/control/__init__.py"
DOC=".project-memory/AUTONOMOUS_CONTROL_AND_OPERATOR_OVERRIDE.md"

echo "=== PR 0020 command arbitration check ==="

# ---------------------------------------------------------------------------
# 1. Module exists
# ---------------------------------------------------------------------------
echo -n "  [1] Module exists ... "
if [ -f "$MODULE" ]; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 2-7. Six public types
# ---------------------------------------------------------------------------
echo -n "  [2] CommandIntentSource ... "
grep -q "class CommandIntentSource" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [3] CommandProposalStatus ... "
grep -q "class CommandProposalStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [4] CommandIntent ... "
grep -q "class CommandIntent" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [5] CommandProposal ... "
grep -q "class CommandProposal" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [6] CommandArbitrationInput ... "
grep -q "class CommandArbitrationInput" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [7] CommandArbitrationResult ... "
grep -q "class CommandArbitrationResult" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 8. arbitrate_command_intent function
# ---------------------------------------------------------------------------
echo -n "  [8] arbitrate_command_intent ... "
grep -q "def arbitrate_command_intent" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 9. AUTO_POLICY and MANUAL_OPERATOR
# ---------------------------------------------------------------------------
echo -n "  [9] Enum values ... "
ENUM_OK=0
grep -q "AUTO_POLICY" "$MODULE" && ENUM_OK=$((ENUM_OK + 1))
grep -q "MANUAL_OPERATOR" "$MODULE" && ENUM_OK=$((ENUM_OK + 1))
if [ "$ENUM_OK" -eq 2 ]; then echo "OK"; else echo "FAIL ($ENUM_OK/2)"; ERRORS=$((ERRORS + 1)); fi

# ---------------------------------------------------------------------------
# 10. CommandProposal exists
# ---------------------------------------------------------------------------
echo -n " [10] CommandProposal ... "
grep -q "class CommandProposal" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 11. execution_eligible
# ---------------------------------------------------------------------------
echo -n " [11] execution_eligible ... "
grep -q "execution_eligible" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 12. requires_operator_review
# ---------------------------------------------------------------------------
echo -n " [12] requires_operator_review ... "
grep -q "requires_operator_review" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 13. safety_blocked_by
# ---------------------------------------------------------------------------
echo -n " [13] safety_blocked_by ... "
grep -q "safety_blocked_by" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 14. Required reason strings
# ---------------------------------------------------------------------------
echo -n " [14] Required reason strings ... "
REASONS=(
    "safety-blocked"
    "manual-operator-override"
    "auto-policy-intent"
    "autonomous-disabled"
    "no-target-load"
    "no-actionable-intent"
)
REASON_OK=0
for r in "${REASONS[@]}"; do
    if grep -q "\"$r\"" "$MODULE"; then
        REASON_OK=$((REASON_OK + 1))
    else
        echo "MISSING: $r"
    fi
done
if [ "$REASON_OK" -eq 6 ]; then
    echo "OK"
else
    echo "FAIL ($REASON_OK/6)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 15. __init__.py exports
# ---------------------------------------------------------------------------
echo -n " [15] __init__.py exports ... "
EXPORT_OK=0
for name in CommandIntentSource CommandProposalStatus CommandIntent CommandProposal CommandArbitrationInput CommandArbitrationResult arbitrate_command_intent; do
    if grep -q "\"$name\"" "$INIT_FILE"; then
        EXPORT_OK=$((EXPORT_OK + 1))
    else
        echo "MISSING: $name"
    fi
done
if [ "$EXPORT_OK" -eq 7 ]; then
    echo "OK"
else
    echo "FAIL ($EXPORT_OK/7)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 16. Document exists
# ---------------------------------------------------------------------------
echo -n " [16] AUTONOMOUS_CONTROL doc exists ... "
if [ -f "$DOC" ]; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 17. Autonomous default language
# ---------------------------------------------------------------------------
echo -n " [17] Autonomous default language ... "
grep -qi "autonomous.*default" "$DOC" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 18. Operator override/correction language
# ---------------------------------------------------------------------------
echo -n " [18] Operator override language ... "
grep -qi "override.*correction\|override and correction" "$DOC" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 19. No exact class CommandQueue
# ---------------------------------------------------------------------------
echo -n " [19] No class CommandQueue ... "
if grep -qE 'class\s+CommandQueue\b' "$MODULE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 20. No exact def CommandQueue
# ---------------------------------------------------------------------------
echo -n " [20] No def CommandQueue ... "
if grep -qE 'def\s+CommandQueue\b' "$MODULE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 21. No executor
# ---------------------------------------------------------------------------
echo -n " [21] No executor ... "
# Check for executor-like class/function names that would indicate execution
if grep -qE 'class\s+CommandExecutor|def\s+execute_command|def\s+execute_proposal|def\s+dispatch_command' "$MODULE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 22. Forbidden runtime imports absent
# ---------------------------------------------------------------------------
echo -n " [22] Forbidden imports absent ... "
FORBIDDEN_IMPORTS="app.tuya app.service app.devices app.monitoring app.ml app.weather smart_home_controller relay_tuya_controller relay_channel_device relay_device_manager device_status_logger openweather dess"
IMP_FAILS=0
for fi in $FORBIDDEN_IMPORTS; do
    if grep -qE "(from|import)\s+${fi}\b" "$MODULE"; then
        echo "FOUND: $fi"
        IMP_FAILS=$((IMP_FAILS + 1))
    fi
done
if [ "$IMP_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($IMP_FAILS)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 23. Forbidden hardware calls absent
# ---------------------------------------------------------------------------
echo -n " [23] Forbidden calls absent ... "
FORBIDDEN_CALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands mark_switched can_switch ready_to_switch_on ready_to_switch_off is_device_on"
CALL_FAILS=0
for fc in $FORBIDDEN_CALLS; do
    if grep -qE "\b${fc}\s*\(" "$MODULE"; then
        echo "FOUND: $fc"
        CALL_FAILS=$((CALL_FAILS + 1))
    fi
done
if [ "$CALL_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($CALL_FAILS)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 24. Impurity calls absent
# ---------------------------------------------------------------------------
echo -n " [24] Impurity calls absent ... "
IMPURE_FAILS=0

if grep -qE '\btime\.time\s*\(' "$MODULE"; then
    echo "FOUND: time.time"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '\bdatetime\.now\s*\(' "$MODULE"; then
    echo "FOUND: datetime.now"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '\bopen\s*\(' "$MODULE"; then
    echo "FOUND: open("
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '\bos\.getenv\s*\(' "$MODULE"; then
    echo "FOUND: os.getenv"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '\byaml\.safe_load\s*\(' "$MODULE"; then
    echo "FOUND: yaml.safe_load"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '(import|from)\s+requests\b' "$MODULE"; then
    echo "FOUND: requests"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '(import|from)\s+aiohttp\b' "$MODULE"; then
    echo "FOUND: aiohttp"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '(import|from)\s+subprocess\b' "$MODULE"; then
    echo "FOUND: subprocess"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '(import|from)\s+logging\b' "$MODULE"; then
    echo "FOUND: logging"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi
if grep -qE '(import|from)\s+urllib\b' "$MODULE"; then
    echo "FOUND: urllib"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

if [ "$IMPURE_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($IMPURE_FAILS)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 25. Runtime files not modified
# ---------------------------------------------------------------------------
echo -n " [25] Runtime files not modified ... "
RUNTIME_FILES=(
    "run.py"
    "app/service"
    "app/devices"
    "app/tuya"
    "app/monitoring"
    "app/ml"
    "app/weather"
    "examples/energy_policy.example.yaml"
    "app/control/domain.py"
    "app/control/relay_mapping.py"
    "app/control/energy_policy.py"
    "app/control/readiness.py"
    "app/control/health.py"
    "app/control/schedule_profile.py"
    "app/control/weather_adjustment.py"
    "app/control/policy_models.py"
    "app/control/policy_decision.py"
    "app/control/manual_control_queue.py"
)
CHANGED_FILES=$(git diff --name-only HEAD 2>/dev/null || true)
CHANGED_FILES="$CHANGED_FILES
$(git diff --name-only --cached 2>/dev/null || true)"
RT_FAILS=0
for rf in "${RUNTIME_FILES[@]}"; do
    while IFS= read -r cf; do
        if [ -n "$cf" ]; then
            if [ "$cf" = "$rf" ] || echo "$cf" | grep -q "^$rf/"; then
                echo "MODIFIED: $cf"
                RT_FAILS=$((RT_FAILS + 1))
            fi
        fi
    done <<< "$CHANGED_FILES"
done
if [ "$RT_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($RT_FAILS)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "=== PASS: All command arbitration checks passed ==="
    exit 0
else
    echo "=== FAIL: $ERRORS check(s) failed ==="
    exit 1
fi
