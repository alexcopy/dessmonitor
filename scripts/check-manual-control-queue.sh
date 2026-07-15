#!/usr/bin/env bash
# check-manual-control-queue.sh
# Validate manual control queue boundary for PR 0019.
#
# Checks:
#   - Module exists, 5 public types, 2 functions
#   - Required fields and statuses
#   - @dataclass(frozen=True)
#   - __init__.py exports
#   - No CommandProposal, no exact CommandQueue class/function
#   - No forbidden imports, hardware calls, impurity
#   - Runtime files not modified

set -euo pipefail

ERRORS=0
MODULE="app/control/manual_control_queue.py"
INIT_FILE="app/control/__init__.py"

echo "=== PR 0019 manual control queue check ==="

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
# 2-6. Five public types
# ---------------------------------------------------------------------------
echo -n "  [2] ManualControlStatus ... "
grep -q "class ManualControlStatus" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [3] ManualControlCommand ... "
grep -q "class ManualControlCommand" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [4] ManualControlQueueItem ... "
grep -q "class ManualControlQueueItem" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [5] ManualControlQueueSnapshot ... "
grep -q "class ManualControlQueueSnapshot" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

echo -n "  [6] ManualControlQueueResult ... "
grep -q "class ManualControlQueueResult" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 7. enqueue_manual_control_command
# ---------------------------------------------------------------------------
echo -n "  [7] enqueue_manual_control_command ... "
grep -q "def enqueue_manual_control_command" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 8. cancel_manual_control_command
# ---------------------------------------------------------------------------
echo -n "  [8] cancel_manual_control_command ... "
grep -q "def cancel_manual_control_command" "$MODULE" && echo "OK" || { echo "FAIL"; ERRORS=$((ERRORS + 1)); }

# ---------------------------------------------------------------------------
# 9. Required fields in ManualControlCommand
# ---------------------------------------------------------------------------
echo -n "  [9] ManualControlCommand fields ... "
CMD="class ManualControlCommand"
FIELDS_OK=0
for field in command_id load_id desired_state source requested_by idempotency_key; do
    if sed -n "/$CMD/,/^class /p" "$MODULE" | head -n 30 | grep -q "$field"; then
        FIELDS_OK=$((FIELDS_OK + 1))
    else
        echo "MISSING: $field"
    fi
done
if [ "$FIELDS_OK" -eq 6 ]; then
    echo "OK"
else
    echo "FAIL ($FIELDS_OK/6)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 10. Status values
# ---------------------------------------------------------------------------
echo -n " [10] ManualControlStatus values ... "
STATUS_OK=0
for val in QUEUED CANCELLED REJECTED EXPIRED; do
    if grep -q "$val" "$MODULE"; then
        STATUS_OK=$((STATUS_OK + 1))
    else
        echo "MISSING: $val"
    fi
done
if [ "$STATUS_OK" -eq 4 ]; then
    echo "OK"
else
    echo "FAIL ($STATUS_OK/4)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 11. @dataclass(frozen=True)
# ---------------------------------------------------------------------------
echo -n " [11] @dataclass(frozen=True) ... "
FROZEN_COUNT=$(grep -c '@dataclass(frozen=True)' "$MODULE" 2>/dev/null || echo 0)
if [ "$FROZEN_COUNT" -ge 4 ]; then
    echo "OK ($FROZEN_COUNT)"
else
    echo "FAIL ($FROZEN_COUNT)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 12. __init__.py exports
# ---------------------------------------------------------------------------
echo -n " [12] __init__.py exports ... "
EXPORT_OK=0
for name in ManualControlStatus ManualControlCommand ManualControlQueueItem ManualControlQueueSnapshot ManualControlQueueResult enqueue_manual_control_command cancel_manual_control_command; do
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
# 13. CommandProposal absent
# ---------------------------------------------------------------------------
echo -n " [13] CommandProposal absent ... "
if grep -qE 'class\s+CommandProposal' "$MODULE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 14. Exact CommandQueue class/function absent
# ---------------------------------------------------------------------------
echo -n " [14] CommandQueue class/function absent ... "
CQ_FAILS=0
if grep -qE 'class\s+CommandQueue\b' "$MODULE"; then
    echo "FOUND: class CommandQueue"
    CQ_FAILS=1
fi
if grep -qE 'def\s+CommandQueue\b' "$MODULE"; then
    echo "FOUND: def CommandQueue"
    CQ_FAILS=$((CQ_FAILS + 1))
fi
if [ "$CQ_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($CQ_FAILS)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 15. Forbidden runtime imports absent
# ---------------------------------------------------------------------------
echo -n " [15] Forbidden imports absent ... "
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
# 16. Forbidden hardware calls absent
# ---------------------------------------------------------------------------
echo -n " [16] Forbidden calls absent ... "
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
# 17. Impurity calls absent
# ---------------------------------------------------------------------------
echo -n " [17] Impurity calls absent ... "
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
# 18. Runtime files not modified
# ---------------------------------------------------------------------------
echo -n " [18] Runtime files not modified ... "
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
    echo "=== PASS: All manual control queue checks passed ==="
    exit 0
else
    echo "=== FAIL: $ERRORS check(s) failed ==="
    exit 1
fi
