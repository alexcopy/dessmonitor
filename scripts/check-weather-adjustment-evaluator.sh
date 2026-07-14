#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Weather Adjustment Evaluator Check
# ==============================================================================
# Verifies that the pure weather adjustment evaluator module exists, contains
# the required frozen result type, function, types, reason strings, has no
# forbidden imports or hardware calls, has no impurity calls, and does not
# modify runtime files.
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

MODULE="app/control/weather_adjustment.py"
INIT="app/control/__init__.py"

echo "=========================================="
echo " dessmonitor Weather Adjustment Evaluator Check"
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
    echo "  Create app/control/weather_adjustment.py with pure weather adjustment evaluator."
    ERRORS=$((ERRORS + 1))
fi

if [ ! -f "$MODULE" ]; then
    echo ""
    echo "=========================================="
    echo " Summary"
    echo "=========================================="
    echo " Errors:   $ERRORS"
    echo ""
    echo -e "${RED}FAILED: $ERRORS error(s).${NC}"
    exit 1
fi

# ------------------------------------------------------------------
# 2. Required result type and function
# ------------------------------------------------------------------
echo ""
echo "--- Checking required type and function ---"

if grep -q "^class WeatherAdjustmentResult" "$MODULE"; then
    echo -e "${GREEN}OK: WeatherAdjustmentResult found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: WeatherAdjustmentResult not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "^def evaluate_weather_adjustment" "$MODULE"; then
    echo -e "${GREEN}OK: evaluate_weather_adjustment function found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: evaluate_weather_adjustment not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "WeatherForecastSignal" "$MODULE"; then
    echo -e "${GREEN}OK: WeatherForecastSignal used in $MODULE${NC}"
else
    echo -e "${RED}MISSING: WeatherForecastSignal not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "WeatherCondition" "$MODULE"; then
    echo -e "${GREEN}OK: WeatherCondition used in $MODULE${NC}"
else
    echo -e "${RED}MISSING: WeatherCondition not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "EnergyPolicyDecision" "$MODULE"; then
    echo -e "${GREEN}OK: EnergyPolicyDecision used in $MODULE${NC}"
else
    echo -e "${RED}MISSING: EnergyPolicyDecision not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "adjustment_factor" "$MODULE"; then
    echo -e "${GREEN}OK: adjustment_factor field found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: adjustment_factor not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 3. Frozen dataclass
# ------------------------------------------------------------------
echo ""
echo "--- Checking frozen dataclass ---"

if grep -q "@dataclass(frozen=True)" "$MODULE"; then
    echo -e "${GREEN}OK: @dataclass(frozen=True) found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: @dataclass(frozen=True) not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 4. Required reason strings
# ------------------------------------------------------------------
echo ""
echo "--- Checking required reason strings ---"

check_reason() {
    local reason="$1"
    if grep -q "\"${reason}\"" "$MODULE" || grep -q "'${reason}'" "$MODULE"; then
        echo -e "${GREEN}OK: reason '${reason}' found in $MODULE${NC}"
    else
        echo -e "${RED}MISSING: reason '${reason}' not found in $MODULE${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

check_reason "sunny-spend"
check_reason "cloudy-conserve"
check_reason "rainy-conserve"
check_reason "storm-protect"
check_reason "snowy-protect"
check_reason "neutral-weather"
check_reason "unknown-weather"

# ------------------------------------------------------------------
# 5. __init__.py exports
# ------------------------------------------------------------------
echo ""
echo "--- Checking __init__.py exports ---"

check_export() {
    local name="$1"
    if grep -q "\"${name}\"" "$INIT"; then
        echo -e "${GREEN}OK: '${name}' exported from $INIT${NC}"
    else
        echo -e "${RED}MISSING: '${name}' not exported from $INIT${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

check_export "WeatherAdjustmentResult"
check_export "evaluate_weather_adjustment"

# ------------------------------------------------------------------
# 6. Forbidden imports absent
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden imports absent ---"

check_forbidden() {
    local pattern="$1"
    local label="$2"
    if grep -qiE "(from ${pattern} import|import ${pattern})" "$MODULE"; then
        echo -e "${RED}FAIL: Forbidden import '${label}' found in $MODULE${NC}"
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
check_forbidden "device_status_logger"      "device_status_logger"
check_forbidden "openweather"               "openweather"
check_forbidden "weather_client"            "weather_client"
check_forbidden "weather_api"               "weather_api"
check_forbidden "dess"                      "dess"
check_forbidden "app\.control\.readiness"   "app.control.readiness"
check_forbidden "app\.control\.health"      "app.control.health"
check_forbidden "app\.control\.schedule_profile" "app.control.schedule_profile"

# ------------------------------------------------------------------
# 7. Forbidden hardware/action calls absent
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden hardware calls absent ---"

check_no_call() {
    local method="$1"
    if grep -q "${method}" "$MODULE"; then
        echo -e "${RED}FAIL: Forbidden call '${method}' found in $MODULE${NC}"
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
# 8. Forbidden impurity calls absent
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden impurity calls absent ---"

# Build a clean version of the module with docstrings and comments stripped
CLEAN_MODULE=$(python3 << 'PYEOF'
import re
with open('app/control/weather_adjustment.py') as f:
    content = f.read()
# Remove docstrings (multiline triple-quoted)
content = re.sub(r'"""[\s\S]*?"""', '', content)
content = re.sub(r"'''[\s\S]*?'''", '', content)
# Remove single-line comments
content = re.sub(r'#.*', '', content)
print(content)
PYEOF
)

check_no_impurity() {
    local pattern="$1"
    local label="$2"
    if echo "$CLEAN_MODULE" | grep -q "${pattern}"; then
        echo -e "${RED}FAIL: Forbidden impurity call '${label}' found in $MODULE${NC}"
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '${label}' not found in $MODULE${NC}"
    fi
}

check_no_impurity "time\.time"       "time.time"
check_no_impurity "datetime\.now"    "datetime.now"
check_no_impurity "open("            "open("
check_no_impurity "yaml\.safe_load"  "yaml.safe_load"
check_no_impurity "os\.getenv"       "os.getenv"
check_no_impurity "requests"         "requests"
check_no_impurity "aiohttp"          "aiohttp"
check_no_impurity "subprocess"       "subprocess"
check_no_impurity "logging"          "logging"
check_no_impurity "urllib"           "urllib"

# ------------------------------------------------------------------
# 9. Runtime files not modified
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
    "examples/energy_policy.example.yaml"
    "service/"
    "shared_state/"
)

git diff --name-only HEAD > /tmp/pr0017_diff.txt 2>/dev/null

if [ -f /tmp/pr0017_diff.txt ]; then
    for path in "${RUNTIME_FILES[@]}"; do
        if grep -q "^${path}" /tmp/pr0017_diff.txt; then
            echo -e "${RED}FAIL: Runtime file '$path' was modified${NC}"
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}OK: '$path' not modified${NC}"
        fi
    done
else
    echo -e "${GREEN}OK: No tracked changes detected${NC}"
fi

rm -f /tmp/pr0017_diff.txt

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
    echo -e "${GREEN}✅ PASSED: Weather adjustment evaluator present, pure, and safe.${NC}"
    exit 0
fi
