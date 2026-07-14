#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Schedule Profile Model Check
# ==============================================================================
# Verifies that the passive schedule profile model module exists, contains
# the required frozen dataclasses, fields, and types, has no forbidden imports
# or hardware calls, has no impurity calls, and does not modify runtime files.
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

MODULE="app/control/schedule_profile.py"
INIT="app/control/__init__.py"

echo "=========================================="
echo " dessmonitor Schedule Profile Model Check"
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
    echo "  Create app/control/schedule_profile.py with passive schedule profile model."
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
# 2. Required types exist
# ------------------------------------------------------------------
echo ""
echo "--- Checking required types ---"

check_type() {
    local type_name="$1"
    if grep -q "^class ${type_name}" "$MODULE"; then
        echo -e "${GREEN}OK: '${type_name}' found in $MODULE${NC}"
    else
        echo -e "${RED}MISSING: '${type_name}' not found in $MODULE${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

check_type "ScheduleWindow"
check_type "ScheduleProfile"
check_type "LoadScheduleProfile"

# ------------------------------------------------------------------
# 3. Required fields and constants
# ------------------------------------------------------------------
echo ""
echo "--- Checking required fields and constants ---"

if grep -q "DEFAULT_CHECK_INTERVAL_SECONDS" "$MODULE"; then
    echo -e "${GREEN}OK: DEFAULT_CHECK_INTERVAL_SECONDS found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: DEFAULT_CHECK_INTERVAL_SECONDS not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "check_interval_seconds" "$MODULE"; then
    echo -e "${GREEN}OK: check_interval_seconds field found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: check_interval_seconds not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "days_of_week" "$MODULE"; then
    echo -e "${GREEN}OK: days_of_week field found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: days_of_week not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "TimeOfDay" "$MODULE"; then
    echo -e "${GREEN}OK: TimeOfDay used in $MODULE${NC}"
else
    echo -e "${RED}MISSING: TimeOfDay not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "Season" "$MODULE"; then
    echo -e "${GREEN}OK: Season used in $MODULE${NC}"
else
    echo -e "${RED}MISSING: Season not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "enabled" "$MODULE"; then
    echo -e "${GREEN}OK: enabled field found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: enabled not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 4. Frozen dataclasses
# ------------------------------------------------------------------
echo ""
echo "--- Checking frozen dataclasses ---"

if grep -q "@dataclass(frozen=True)" "$MODULE"; then
    echo -e "${GREEN}OK: @dataclass(frozen=True) found in $MODULE${NC}"
else
    echo -e "${RED}MISSING: @dataclass(frozen=True) not found in $MODULE${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 5. __init__.py exports all three model classes
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

check_export "ScheduleWindow"
check_export "ScheduleProfile"
check_export "LoadScheduleProfile"

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
check_forbidden "dess"                      "dess"
check_forbidden "app\.control\.readiness"   "app.control.readiness"
check_forbidden "app\.control\.health"      "app.control.health"

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
with open('app/control/schedule_profile.py') as f:
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

git diff --name-only HEAD > /tmp/pr0016_diff.txt 2>/dev/null

if [ -f /tmp/pr0016_diff.txt ]; then
    for path in "${RUNTIME_FILES[@]}"; do
        if grep -q "^${path}" /tmp/pr0016_diff.txt; then
            echo -e "${RED}FAIL: Runtime file '$path' was modified${NC}"
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}OK: '$path' not modified${NC}"
        fi
    done
else
    echo -e "${GREEN}OK: No tracked changes detected${NC}"
fi

rm -f /tmp/pr0016_diff.txt

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
    echo -e "${GREEN}✅ PASSED: Schedule profile model present, passive, and safe.${NC}"
    exit 0
fi
