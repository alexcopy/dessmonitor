#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Energy Policy Domain Types Check
# ==============================================================================
# Verifies that the passive energy policy domain types module exists,
# contains all 17 required type names and concept phrases, has no
# forbidden imports or hardware calls, and does not modify runtime files.
#
# This script is read-only. It does not require network access, Docker Hub,
# GitHub API, Kubernetes, or ArgoCD. It does not require secrets. It does
# not mutate files.
#
# Exits 0 if all checks pass, 1 if any check fails.
# ==============================================================================

set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

ERRORS=0

MODULE="app/control/energy_policy.py"

echo "=========================================="
echo " dessmonitor Energy Policy Domain Types Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Module exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking module ---"

if [ -f "$MODULE" ]; then
    echo -e "${GREEN}OK: $MODULE exists${NC}"
else
    echo -e "${RED}MISSING: $MODULE not found${NC}"
    echo "  Create app/control/energy_policy.py with passive energy policy domain types."
    ERRORS=$((ERRORS + 1))
fi

if [ ! -f "$MODULE" ]; then
    echo ""
    echo "=========================================="
    echo " Summary"
    echo "=========================================="
    echo " Errors:   $ERRORS"
    echo ""
    echo -e "${RED}❌ FAILED: $ERRORS error(s).${NC}"
    exit 1
fi

# ------------------------------------------------------------------
# 2. Required type names
# ------------------------------------------------------------------
echo ""
echo "--- Checking required type names ---"

check_type() {
    local type_name="$1"
    if grep -q "^class ${type_name}[(\":]" "$MODULE"; then
        echo -e "${GREEN}OK: '${type_name}' found in $MODULE${NC}"
    else
        echo -e "${RED}MISSING: '${type_name}' not found in $MODULE${NC}"
        echo "  Expected type '${type_name}' must be defined."
        ERRORS=$((ERRORS + 1))
    fi
}

check_type "PowerSource"
check_type "TimeOfDay"
check_type "Season"
check_type "WeatherCondition"
check_type "LoadClass"
check_type "DevicePriority"
check_type "VoltageSnapshot"
check_type "WeatherForecastSignal"
check_type "BatteryReservePolicy"
check_type "DeviceEnergyPolicy"
check_type "ReadinessInput"
check_type "ReadinessResult"
check_type "HealthInput"
check_type "HealthStatus"
check_type "HealthCheckResult"
check_type "EnergyPolicyContext"
check_type "EnergyPolicyDecision"

# ------------------------------------------------------------------
# 3. Required concept phrases
# ------------------------------------------------------------------
echo ""
echo "--- Checking required concept phrases ---"

check_phrase() {
    local pattern="$1"
    local label="$2"
    if grep -qi "$pattern" "$MODULE"; then
        echo -e "${GREEN}OK: '${label}' found in $MODULE${NC}"
    else
        echo -e "${RED}MISSING: '${label}' not found in $MODULE${NC}"
        echo "  The module must contain the concept '${label}'."
        ERRORS=$((ERRORS + 1))
    fi
}

check_phrase "26\\.5"               "26.5"
check_phrase "switch ON"            "switch ON"
check_phrase "switch OFF"           "switch OFF"
check_phrase "readiness"            "readiness"
check_phrase "health"               "health"
check_phrase "weather forecast"     "weather forecast"
check_phrase "ML advisory"          "ML advisory"
check_phrase "ML control"           "ML control"

# ------------------------------------------------------------------
# 4. Forbidden imports absent
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden imports absent ---"

check_forbidden() {
    local pattern="$1"
    local label="$2"
    # Check only actual import/from lines, not docstring mentions
    if grep -qiE "(^import ${pattern}|^from .*${pattern}.*import)" "$MODULE"; then
        echo -e "${RED}FAIL: Forbidden import '${label}' found in $MODULE${NC}"
        echo "  $MODULE must not import or reference '${label}'."
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '${label}' not found in $MODULE${NC}"
    fi
}

check_forbidden "app\.tuya"                 "app.tuya"
check_forbidden "app\.service"              "app.service"
check_forbidden "app\.devices"              "app.devices"
check_forbidden "app\.monitoring"           "app.monitoring"
check_forbidden "app\.ml"                   "app.ml"
check_forbidden "app\.weather"              "app.weather"
check_forbidden "smart_home_controller"     "smart_home_controller"
check_forbidden "relay_tuya_controller"     "relay_tuya_controller"
check_forbidden "relay_channel_device"      "relay_channel_device"
check_forbidden "relay_device_manager"      "relay_device_manager"
check_forbidden "openweather"               "openweather"
check_forbidden "dess"                      "dess"

# ------------------------------------------------------------------
# 5. Forbidden hardware/action method calls absent
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden hardware calls absent ---"

check_no_call() {
    local method="$1"
    if grep -q "${method}" "$MODULE"; then
        echo -e "${RED}FAIL: Forbidden call '${method}' found in $MODULE${NC}"
        echo "  The energy policy module must not call ${method}()."
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '${method}' not found in $MODULE${NC}"
    fi
}

check_no_call "switch_on_device"
check_no_call "switch_off_device"
check_no_call "switch_binary"
check_no_call "switch_device"
check_no_call "toggle_device"
check_no_call "set_numeric"
check_no_call "update_status"
check_no_call "mark_switched"
check_no_call "can_switch"
check_no_call "ready_to_switch_on"
check_no_call "ready_to_switch_off"
check_no_call "is_device_on"

# ------------------------------------------------------------------
# 6. Runtime files not modified
# ------------------------------------------------------------------
echo ""
echo "--- Checking runtime files unchanged ---"

RUNTIME_FILES=(
    "run.py"
    "app/service/"
    "app/devices/"
    "app/tuya/"
    "app/monitoring/"
    "app/ml/"
    "app/weather/"
    "service/"
    "shared_state/"
)

git diff --name-only HEAD > /tmp/pr0012_diff.txt 2>/dev/null

if [ -f /tmp/pr0012_diff.txt ]; then
    for path in "${RUNTIME_FILES[@]}"; do
        if grep -q "^${path}" /tmp/pr0012_diff.txt; then
            echo -e "${RED}FAIL: Runtime file '$path' was modified${NC}"
            echo "  PR 0012 must not change runtime files."
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}OK: '$path' not modified${NC}"
        fi
    done
else
    echo -e "${GREEN}OK: No tracked changes detected${NC}"
fi

rm -f /tmp/pr0012_diff.txt

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo "=========================================="
echo " Summary"
echo "=========================================="
echo " Errors:   $ERRORS"
echo ""

if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}❌ FAILED: $ERRORS error(s).${NC}"
    exit 1
else
    echo -e "${GREEN}✅ PASSED: Energy policy domain types present and safe.${NC}"
    exit 0
fi
