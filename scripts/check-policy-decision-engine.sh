#!/usr/bin/env bash
# check-policy-decision-engine.sh
# Validate pure deterministic policy decision engine for PR 0018C.
#
# Checks:
#   - policy_decision.py exists and evaluate_policy_decision is present
#   - Required types used (PolicyDecisionInput, PolicyDecisionResult, etc.)
#   - Required wattage fields referenced
#   - All 11 required reason strings present
#   - __init__.py exports evaluate_policy_decision
#   - No CommandProposal, CommandQueue
#   - No forbidden runtime imports
#   - No hardware calls
#   - No impurity calls
#   - Runtime files not modified

set -euo pipefail

ERRORS=0
MODULE="app/control/policy_decision.py"
INIT_FILE="app/control/__init__.py"

echo "=== PR 0018C policy decision engine check ==="

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
# 2. evaluate_policy_decision function exists
# ---------------------------------------------------------------------------
echo -n "  [2] evaluate_policy_decision exists ... "
if grep -qE 'def\s+evaluate_policy_decision' "$MODULE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 3. PolicyDecisionInput used
# ---------------------------------------------------------------------------
echo -n "  [3] PolicyDecisionInput used ... "
if grep -q "PolicyDecisionInput" "$MODULE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 4. PolicyDecisionResult used
# ---------------------------------------------------------------------------
echo -n "  [4] PolicyDecisionResult used ... "
if grep -q "PolicyDecisionResult" "$MODULE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 5. LoadCandidate, EnergyBudget, BatteryOperatingWindow, PondSafetyContext,
#    ForecastStrategyContext used
# ---------------------------------------------------------------------------
echo -n "  [5] Model types used ... "
MODEL_OK=0
for cls in LoadCandidate EnergyBudget BatteryOperatingWindow PondSafetyContext ForecastStrategyContext; do
    if grep -q "$cls" "$MODULE"; then
        MODEL_OK=$((MODEL_OK + 1))
    else
        echo "MISSING: $cls"
    fi
done
if [ "$MODEL_OK" -eq 5 ]; then
    echo "OK"
else
    echo "FAIL ($MODEL_OK/5)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 6. EnergyPolicyDecision used
# ---------------------------------------------------------------------------
echo -n "  [6] EnergyPolicyDecision used ... "
if grep -q "EnergyPolicyDecision" "$MODULE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 7. configured_load_watts referenced
# ---------------------------------------------------------------------------
echo -n "  [7] configured_load_watts ... "
if grep -q "configured_load_watts" "$MODULE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 8. projected_total_load_watts referenced
# ---------------------------------------------------------------------------
echo -n "  [8] projected_total_load_watts ... "
if grep -q "projected_total_load_watts" "$MODULE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 9. max_total_load_watts referenced
# ---------------------------------------------------------------------------
echo -n "  [9] max_total_load_watts ... "
if grep -q "max_total_load_watts" "$MODULE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 10. All 11 required reason strings
# ---------------------------------------------------------------------------
echo -n " [10] Required reason strings ... "
REASONS=(
    "no-loads"
    "battery-fallback-protection"
    "inverter-load-cap-protection"
    "pond-life-support-aeration"
    "shed-discretionary-for-aeration"
    "morning-minimum-hold-for-sun"
    "bad-forecast-conserve"
    "high-voltage-spend"
    "weather-conserve"
    "weather-spend"
    "neutral-no-action"
)
REASON_OK=0
for r in "${REASONS[@]}"; do
    if grep -q "\"$r\"" "$MODULE"; then
        REASON_OK=$((REASON_OK + 1))
    else
        echo "MISSING: $r"
    fi
done
if [ "$REASON_OK" -eq 11 ]; then
    echo "OK"
else
    echo "FAIL ($REASON_OK/11)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 11. __init__.py exports evaluate_policy_decision
# ---------------------------------------------------------------------------
echo -n " [11] __init__.py exports evaluate_policy_decision ... "
if grep -q '"evaluate_policy_decision"' "$INIT_FILE"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 12. CommandProposal absent
# ---------------------------------------------------------------------------
echo -n " [12] CommandProposal absent ... "
if grep -qE 'class\s+CommandProposal' "$MODULE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 13. CommandQueue absent
# ---------------------------------------------------------------------------
echo -n " [13] CommandQueue absent ... "
if grep -qE 'class\s+CommandQueue' "$MODULE"; then
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
else
    echo "OK"
fi

# ---------------------------------------------------------------------------
# 14. Forbidden runtime imports absent
# ---------------------------------------------------------------------------
echo -n " [14] Forbidden imports absent ... "
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
    echo "FAIL ($IMP_FAILS forbidden imports)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 15. Forbidden hardware calls absent
# ---------------------------------------------------------------------------
echo -n " [15] Forbidden calls absent ... "
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
    echo "FAIL ($CALL_FAILS forbidden calls)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 16. Impurity calls absent
# ---------------------------------------------------------------------------
echo -n " [16] Impurity calls absent ... "
IMPURE_FAILS=0

# time.time
if grep -qE '\btime\.time\s*\(' "$MODULE"; then
    echo "FOUND: time.time"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# datetime.now
if grep -qE '\bdatetime\.now\s*\(' "$MODULE"; then
    echo "FOUND: datetime.now"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# open(
if grep -qE '\bopen\s*\(' "$MODULE"; then
    echo "FOUND: open("
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# os.getenv
if grep -qE '\bos\.getenv\s*\(' "$MODULE"; then
    echo "FOUND: os.getenv"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# yaml.safe_load
if grep -qE '\byaml\.safe_load\s*\(' "$MODULE"; then
    echo "FOUND: yaml.safe_load"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# requests
if grep -qE '(import|from)\s+requests\b' "$MODULE"; then
    echo "FOUND: requests"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# aiohttp
if grep -qE '(import|from)\s+aiohttp\b' "$MODULE"; then
    echo "FOUND: aiohttp"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# subprocess
if grep -qE '(import|from)\s+subprocess\b' "$MODULE"; then
    echo "FOUND: subprocess"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# logging
if grep -qE '(import|from)\s+logging\b' "$MODULE"; then
    echo "FOUND: logging"
    IMPURE_FAILS=$((IMPURE_FAILS + 1))
fi

# urllib
if grep -qE '(import|from)\s+urllib\b' "$MODULE"; then
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
# 17. Runtime files not modified from HEAD
# ---------------------------------------------------------------------------
echo -n " [17] Runtime files not modified ... "
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
    echo "FAIL ($RT_FAILS runtime files modified)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "=== PASS: All policy decision engine checks passed ==="
    exit 0
else
    echo "=== FAIL: $ERRORS check(s) failed ==="
    exit 1
fi
