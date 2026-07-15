#!/usr/bin/env bash
# check-policy-engine-models.sh
# Validate passive policy engine model types for PR 0018B.
#
# Checks:
#   - policy_models.py exists and all seven types are present
#   - Required fields exist (configured_load_watts, projected_total_load_watts, etc.)
#   - Battery/pond/forecast fields exist
#   - @dataclass(frozen=True) markers present
#   - __init__.py exports all seven types
#   - evaluate_policy_decision, CommandProposal, CommandQueue absent
#   - Forbidden imports, calls, and impurity calls absent
#   - Runtime files not modified from HEAD

set -euo pipefail

ERRORS=0
MODEL_FILE="app/control/policy_models.py"
INIT_FILE="app/control/__init__.py"

echo "=== PR 0018B policy engine models check ==="

# ---------------------------------------------------------------------------
# 1. Model file exists
# ---------------------------------------------------------------------------
echo -n "  [1] Model file exists ... "
if [ -f "$MODEL_FILE" ]; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 2. All seven model types are present
# ---------------------------------------------------------------------------
echo -n "  [2] Seven model types present ... "
TYPES_OK=0
for cls in BatteryOperatingWindow EnergyBudget PondSafetyContext ForecastStrategyContext LoadCandidate PolicyDecisionInput PolicyDecisionResult; do
    if grep -q "$cls" "$MODEL_FILE"; then
        TYPES_OK=$((TYPES_OK + 1))
    else
        echo "MISSING: $cls"
    fi
done
if [ "$TYPES_OK" -eq 7 ]; then
    echo "OK"
else
    echo "FAIL ($TYPES_OK/7)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 3. configured_load_watts exists
# ---------------------------------------------------------------------------
echo -n "  [3] configured_load_watts ... "
if grep -q "configured_load_watts" "$MODEL_FILE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 4. projected_total_load_watts exists
# ---------------------------------------------------------------------------
echo -n "  [4] projected_total_load_watts ... "
if grep -q "projected_total_load_watts" "$MODEL_FILE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 5. max_total_load_watts exists
# ---------------------------------------------------------------------------
echo -n "  [5] max_total_load_watts ... "
if grep -q "max_total_load_watts" "$MODEL_FILE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 6. current_total_load_watts exists
# ---------------------------------------------------------------------------
echo -n "  [6] current_total_load_watts ... "
if grep -q "current_total_load_watts" "$MODEL_FILE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 7. available_load_budget_watts exists
# ---------------------------------------------------------------------------
echo -n "  [7] available_load_budget_watts ... "
if grep -q "available_load_budget_watts" "$MODEL_FILE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 8. Battery voltage fields exist
# ---------------------------------------------------------------------------
echo -n "  [8] Battery voltage fields ... "
BAT_OK=0
for fld in battery_grid_fallback_voltage battery_morning_minimum_voltage battery_evening_reserve_voltage battery_high_voltage_spend_threshold battery_full_voltage; do
    if grep -q "$fld" "$MODEL_FILE"; then
        BAT_OK=$((BAT_OK + 1))
    else
        echo "MISSING: $fld"
    fi
done
if [ "$BAT_OK" -eq 5 ]; then
    echo "OK"
else
    echo "FAIL ($BAT_OK/5)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 9. Pond safety fields exist
# ---------------------------------------------------------------------------
echo -n "  [9] Pond safety fields ... "
POND_OK=0
for fld in pond_temperature_c pond_hot_water_temperature_c is_summer aeration_load_ids minimum_aeration_count preferred_aeration_count maximum_extra_aeration_count life_support_required; do
    if grep -q "$fld" "$MODEL_FILE"; then
        POND_OK=$((POND_OK + 1))
    else
        echo "MISSING: $fld"
    fi
done
if [ "$POND_OK" -eq 8 ]; then
    echo "OK"
else
    echo "FAIL ($POND_OK/8)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 10. Forecast strategy fields exist
# ---------------------------------------------------------------------------
echo -n " [10] Forecast strategy fields ... "
FCAST_OK=0
for fld in forecast_improves_later_today bad_forecast_all_day sunny_window_expected_hours morning_strategy_active; do
    if grep -q "$fld" "$MODEL_FILE"; then
        FCAST_OK=$((FCAST_OK + 1))
    else
        echo "MISSING: $fld"
    fi
done
if [ "$FCAST_OK" -eq 4 ]; then
    echo "OK"
else
    echo "FAIL ($FCAST_OK/4)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 11. @dataclass(frozen=True) appears at least 7 times
# ---------------------------------------------------------------------------
echo -n " [11] @dataclass(frozen=True) count ... "
FROZEN_COUNT=$(grep -c "@dataclass(frozen=True)" "$MODEL_FILE" || true)
if [ "$FROZEN_COUNT" -ge 7 ]; then
    echo "OK ($FROZEN_COUNT)"
else
    echo "FAIL ($FROZEN_COUNT < 7)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 12. __init__.py exports all seven types
# ---------------------------------------------------------------------------
echo -n " [12] __init__.py exports ... "
INIT_OK=0
for cls in BatteryOperatingWindow EnergyBudget PondSafetyContext ForecastStrategyContext LoadCandidate PolicyDecisionInput PolicyDecisionResult; do
    if grep -q "\"$cls\"" "$INIT_FILE"; then
        INIT_OK=$((INIT_OK + 1))
    else
        echo "MISSING: $cls in __all__"
    fi
done
if [ "$INIT_OK" -eq 7 ]; then
    echo "OK"
else
    echo "FAIL ($INIT_OK/7)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 13. evaluate_policy_decision absent (check for def, not docstring mention)
# ---------------------------------------------------------------------------
echo -n " [13] evaluate_policy_decision absent ... "
if grep -qE 'def\s+evaluate_policy_decision' "$MODEL_FILE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 14. CommandProposal absent (check for class/type definition, not docstring mention)
# ---------------------------------------------------------------------------
echo -n " [14] CommandProposal absent ... "
if grep -qE 'class\s+CommandProposal' "$MODEL_FILE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 15. CommandQueue absent (check for class/type definition, not docstring mention)
# ---------------------------------------------------------------------------
echo -n " [15] CommandQueue absent ... "
if grep -qE 'class\s+CommandQueue' "$MODEL_FILE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 16. Forbidden imports absent (match actual import statements)
# ---------------------------------------------------------------------------
echo -n " [16] Forbidden imports absent ... "
FORBIDDEN_IMPORTS="app.tuya app.service app.devices app.monitoring app.ml app.weather smart_home_controller relay_tuya_controller relay_channel_device relay_device_manager device_status_logger openweather weather_client weather_api dess"
IMP_FAILS=0
for fi in $FORBIDDEN_IMPORTS; do
    # Match only actual import/from statements, not docstring mentions
    if grep -qE "(from|import)\s+${fi}\b" "$MODEL_FILE"; then
        echo "FOUND: $fi"
        IMP_FAILS=$((IMP_FAILS + 1))
    fi
done
if [ "$IMP_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($IMP_FAILS forbidden imports)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 17. Forbidden calls absent (match function calls, not docstring mentions)
# ---------------------------------------------------------------------------
echo -n " [17] Forbidden calls absent ... "
FORBIDDEN_CALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands mark_switched can_switch ready_to_switch_on ready_to_switch_off is_device_on"
CALL_FAILS=0
for fc in $FORBIDDEN_CALLS; do
    # Match only function call patterns (word followed by parentheses)
    if grep -qE "\b${fc}\s*\(" "$MODEL_FILE"; then
        echo "FOUND: $fc"
        CALL_FAILS=$((CALL_FAILS + 1))
    fi
done
if [ "$CALL_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($CALL_FAILS forbidden calls)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 18. Impurity calls absent (match actual calls/usage, not docstring mentions)
# ---------------------------------------------------------------------------
echo -n " [18] Impurity calls absent ... "
IMPURE_FAILS=0

# Check time.time — match call pattern
if grep -qE '\btime\.time\s*\(' "$MODEL_FILE"; then
    echo "FOUND: time.time"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check datetime.now — match call pattern
if grep -qE '\bdatetime\.now\s*\(' "$MODEL_FILE"; then
    echo "FOUND: datetime.now"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check open( — match call pattern
if grep -qE '\bopen\s*\(' "$MODEL_FILE"; then
    echo "FOUND: open("
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check yaml.safe_load — match call pattern
if grep -qE '\byaml\.safe_load\s*\(' "$MODEL_FILE"; then
    echo "FOUND: yaml.safe_load"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check os.getenv — match call pattern
if grep -qE '\bos\.getenv\s*\(' "$MODEL_FILE"; then
    echo "FOUND: os.getenv"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check requests — match import or usage
if grep -qE '(import|from)\s+requests\b' "$MODEL_FILE"; then
    echo "FOUND: requests"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check aiohttp — match import or usage
if grep -qE '(import|from)\s+aiohttp\b' "$MODEL_FILE"; then
    echo "FOUND: aiohttp"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check subprocess — match import or usage
if grep -qE '(import|from)\s+subprocess\b' "$MODEL_FILE"; then
    echo "FOUND: subprocess"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check logging — match import or usage (but exclude docstring references to "logging")
if grep -qE '(import|from)\s+logging\b' "$MODEL_FILE"; then
    echo "FOUND: logging"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# Check urllib — match import or usage
if grep -qE '(import|from)\s+urllib\b' "$MODEL_FILE"; then
    echo "FOUND: urllib"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

if [ "$IMPURE_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($IMPURE_FAILS impurity calls)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 19. Runtime files not modified from HEAD
# ---------------------------------------------------------------------------
echo -n " [19] Runtime files not modified ... "
RUNTIME_FILES=(
    "run.py"
    "app/service"
    "app/devices"
    "app/tuya"
    "app/monitoring"
    "app/ml"
    "app/weather"
    "examples/energy_policy.example.yaml"
)
RT_OK=0
RT_FAILS=0
# Collect all changed files from the diff (staged + unstaged vs HEAD)
CHANGED_FILES=$(git diff --name-only HEAD 2>/dev/null || true)
CHANGED_FILES="$CHANGED_FILES
$(git diff --name-only --cached 2>/dev/null || true)"
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
    echo "FAIL ($RT_FAILS runtime files modified)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "=== PASS: All policy engine model checks passed ==="
    exit 0
else
    echo "=== FAIL: $ERRORS check(s) failed ==="
    exit 1
fi
