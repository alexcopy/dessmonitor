#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Generic Control Domain Check
# ==============================================================================
# Verifies that the generic control domain module exists, contains all
# required concept names, and has no forbidden runtime adapter imports.
# Also verifies that no runtime files were changed.
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

DOMAIN="app/control/domain.py"
INIT="app/control/__init__.py"

echo "=========================================="
echo " dessmonitor Generic Control Domain Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Domain module files exist
# ------------------------------------------------------------------
echo ""
echo "--- Checking domain module files ---"

if [ -f "$DOMAIN" ]; then
    echo -e "${GREEN}OK: $DOMAIN exists${NC}"
else
    echo -e "${RED}MISSING: $DOMAIN not found${NC}"
    echo "  Create app/control/domain.py with generic control domain types."
    ERRORS=$((ERRORS + 1))
fi

if [ -f "$INIT" ]; then
    echo -e "${GREEN}OK: $INIT exists${NC}"
else
    echo -e "${RED}MISSING: $INIT not found${NC}"
    echo "  Create app/control/__init__.py that re-exports domain types."
    ERRORS=$((ERRORS + 1))
fi

# If domain.py is missing, we cannot continue with content checks
if [ ! -f "$DOMAIN" ]; then
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
# 2. Required concept names
# ------------------------------------------------------------------
echo ""
echo "--- Checking required concept names ---"

check_concept() {
    local pattern="$1"
    local label="$2"
    if grep -q "$pattern" "$DOMAIN"; then
        echo -e "${GREEN}OK: '$label' found in $DOMAIN${NC}"
    else
        echo -e "${RED}MISSING: '$label' not found in $DOMAIN${NC}"
        echo "  Expected type '$label' must be defined in $DOMAIN."
        ERRORS=$((ERRORS + 1))
    fi
}

check_concept "SwitchableLoad"   "SwitchableLoad"
check_concept "ControlCommand"   "ControlCommand"
check_concept "ControlState"     "ControlState"
check_concept "ObservedState"    "ObservedState"
check_concept "DesiredState"     "DesiredState"
check_concept "CommandResult"    "CommandResult"
check_concept "CommandSource"    "CommandSource"
check_concept "TelemetryPoint"   "TelemetryPoint"
check_concept "PolicyDecision"   "PolicyDecision"

# ------------------------------------------------------------------
# 3. Forbidden imports absent from domain.py
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden imports absent ---"

check_forbidden() {
    local pattern="$1"
    local label="$2"
    # Check only actual import/from lines, not docstring mentions.
    # Match patterns like:
    #   import tuya
    #   from app.tuya import ...
    #   from tuya import ...
    if grep -qiE "(^import ${pattern}|^from .*${pattern}.*import)" "$DOMAIN"; then
        echo -e "${RED}FAIL: Forbidden import '$label' found in $DOMAIN${NC}"
        echo "  $DOMAIN must not import or reference '$label'."
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '$label' not found in $DOMAIN${NC}"
    fi
}

check_forbidden "tuya"                     "tuya"
check_forbidden "dess"                     "dess"
check_forbidden "openweather"              "openweather"
check_forbidden "ml"                       "ml"
check_forbidden "smart_home_controller"    "smart_home_controller"
check_forbidden "relay_tuya_controller"    "relay_tuya_controller"
check_forbidden "relay_channel_device"     "relay_channel_device"
check_forbidden "relay_device_manager"     "relay_device_manager"

# ------------------------------------------------------------------
# 4. Forbidden imports absent from __init__.py
# ------------------------------------------------------------------
echo ""
echo "--- Checking forbidden imports absent from __init__.py ---"

for pattern in tuya dess openweather ml smart_home_controller relay_tuya_controller relay_channel_device relay_device_manager; do
    if grep -qiE "(^import ${pattern}|^from .*${pattern}.*import)" "$INIT"; then
        echo -e "${RED}FAIL: Forbidden import '$pattern' found in $INIT${NC}"
        echo "  $INIT must not import or reference '$pattern'."
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '$pattern' not found in $INIT${NC}"
    fi
done

# ------------------------------------------------------------------
# 5. No runtime files changed
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

git diff --name-only HEAD > /tmp/pr0009_diff.txt 2>/dev/null

if [ -f /tmp/pr0009_diff.txt ]; then
    for path in "${RUNTIME_FILES[@]}"; do
        if grep -q "^${path}" /tmp/pr0009_diff.txt; then
            echo -e "${RED}FAIL: Runtime file '$path' was modified${NC}"
            echo "  PR 0009 must not change runtime files."
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}OK: '$path' not modified${NC}"
        fi
    done
else
    echo -e "${GREEN}OK: No tracked changes detected${NC}"
fi

# Clean up temp file
rm -f /tmp/pr0009_diff.txt

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
    echo -e "${GREEN}✅ PASSED: Generic control domain types present and safe.${NC}"
    exit 0
fi
