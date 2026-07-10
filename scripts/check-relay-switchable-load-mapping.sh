#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Relay-to-SwitchableLoad Mapping Check
# ==============================================================================
# Verifies that the relay-to-SwitchableLoad mapping module exists, contains
# the required mapping functions, uses SwitchableLoad, has no forbidden
# imports, and does not modify runtime files.
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

MAPPING="app/control/relay_mapping.py"
INIT="app/control/__init__.py"

echo "=========================================="
echo " dessmonitor Relay-to-SwitchableLoad Mapping Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Mapping module exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking mapping module ---"

if [ -f "$MAPPING" ]; then
    echo -e "${GREEN}OK: $MAPPING exists${NC}"
else
    echo -e "${RED}MISSING: $MAPPING not found${NC}"
    echo "  Create app/control/relay_mapping.py with relay-to-SwitchableLoad mapping."
    ERRORS=$((ERRORS + 1))
fi

if [ ! -f "$MAPPING" ]; then
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
# 2. Required functions exist
# ------------------------------------------------------------------
echo ""
echo "--- Checking required functions ---"

check_function() {
    local func="$1"
    if grep -q "def ${func}" "$MAPPING"; then
        echo -e "${GREEN}OK: ${func}() found in $MAPPING${NC}"
    else
        echo -e "${RED}MISSING: ${func}() not found in $MAPPING${NC}"
        echo "  Expected function ${func}() must be defined."
        ERRORS=$((ERRORS + 1))
    fi
}

check_function "relay_channel_to_switchable_load"
check_function "relay_channels_to_switchable_loads"

# ------------------------------------------------------------------
# 3. SwitchableLoad is imported/used
# ------------------------------------------------------------------
echo ""
echo "--- Checking SwitchableLoad usage ---"

if grep -q "SwitchableLoad" "$MAPPING"; then
    echo -e "${GREEN}OK: SwitchableLoad referenced in $MAPPING${NC}"
else
    echo -e "${RED}MISSING: SwitchableLoad not found in $MAPPING${NC}"
    echo "  The mapping must import and use SwitchableLoad from app.control.domain."
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 4. Mapping functions exported from __init__.py
# ------------------------------------------------------------------
echo ""
echo "--- Checking __init__.py exports mapping functions ---"

if grep -q "relay_channel_to_switchable_load" "$INIT"; then
    echo -e "${GREEN}OK: relay_channel_to_switchable_load exported from $INIT${NC}"
else
    echo -e "${RED}MISSING: relay_channel_to_switchable_load not exported from $INIT${NC}"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "relay_channels_to_switchable_loads" "$INIT"; then
    echo -e "${GREEN}OK: relay_channels_to_switchable_loads exported from $INIT${NC}"
else
    echo -e "${RED}MISSING: relay_channels_to_switchable_loads not exported from $INIT${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 5. Forbidden imports absent
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden imports absent ---"

check_forbidden() {
    local pattern="$1"
    local label="$2"
    # Check only actual import/from lines, not string literals or comments
    if grep -qiE "(^import ${pattern}|^from .*${pattern}.*import)" "$MAPPING"; then
        echo -e "${RED}FAIL: Forbidden import '${label}' found in $MAPPING${NC}"
        echo "  $MAPPING must not import '${label}'."
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '${label}' not found in $MAPPING${NC}"
    fi
}

check_forbidden "app\.devices"              "app.devices"
check_forbidden "app\.tuya"                 "app.tuya"
check_forbidden "app\.service"              "app.service"
check_forbidden "app\.monitoring"           "app.monitoring"
check_forbidden "app\.ml"                   "app.ml"
check_forbidden "app\.weather"              "app.weather"
check_forbidden "smart_home_controller"     "smart_home_controller"
check_forbidden "relay_tuya_controller"     "relay_tuya_controller"
check_forbidden "device_initializer"        "device_initializer"

# ------------------------------------------------------------------
# 6. Forbidden hardware/action method calls absent
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden method calls absent ---"

check_no_call() {
    local method="$1"
    if grep -q "${method}" "$MAPPING"; then
        echo -e "${RED}FAIL: Forbidden call '${method}' found in $MAPPING${NC}"
        echo "  The mapping must not call ${method}() on relay objects."
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '${method}' not found in $MAPPING${NC}"
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
# 7. No runtime files changed
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

git diff --name-only HEAD > /tmp/pr0010_diff.txt 2>/dev/null

if [ -f /tmp/pr0010_diff.txt ]; then
    for path in "${RUNTIME_FILES[@]}"; do
        if grep -q "^${path}" /tmp/pr0010_diff.txt; then
            echo -e "${RED}FAIL: Runtime file '$path' was modified${NC}"
            echo "  PR 0010 must not change runtime files."
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}OK: '$path' not modified${NC}"
        fi
    done
else
    echo -e "${GREEN}OK: No tracked changes detected${NC}"
fi

rm -f /tmp/pr0010_diff.txt

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
    echo -e "${GREEN}✅ PASSED: Relay-to-SwitchableLoad mapping present and safe.${NC}"
    exit 0
fi
