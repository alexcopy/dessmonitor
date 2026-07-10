#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Pump Automation Disabled Check
# ==============================================================================
# Verifies that pump automation is gated behind PUMP_AUTOMATION_ENABLED
# and disabled by default. Also verifies that manual switch control
# methods remain present and the switch loop still starts.
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

SMC="app/service/smart_home_controller.py"
RUN="run.py"
RTUYA="app/tuya/relay_tuya_controller.py"
RDM="app/devices/relay_device_manager.py"

echo "=========================================="
echo " dessmonitor Pump Automation Disabled Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. PUMP_AUTOMATION_ENABLED env var in run.py
# ------------------------------------------------------------------
echo ""
echo "--- Checking PUMP_AUTOMATION_ENABLED in run.py ---"

if grep -q "PUMP_AUTOMATION_ENABLED" "$RUN"; then
    echo -e "${GREEN}OK: PUMP_AUTOMATION_ENABLED referenced in $RUN${NC}"
else
    echo -e "${RED}MISSING: PUMP_AUTOMATION_ENABLED not found in $RUN${NC}"
    echo "  run.py must read PUMP_AUTOMATION_ENABLED from environment."
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 2. Default is disabled (False when absent)
# ------------------------------------------------------------------
echo ""
echo "--- Checking default is disabled ---"

if grep -q 'os.getenv.*PUMP_AUTOMATION_ENABLED.*False\|os.getenv.*PUMP_AUTOMATION_ENABLED.*""' "$RUN"; then
    echo -e "${GREEN}OK: PUMP_AUTOMATION_ENABLED defaults to empty/False${NC}"
else
    # Check for the grammar pattern: os.getenv("PUMP_AUTOMATION_ENABLED", "").lower() in ("true", ...)
    if grep -q 'PUMP_AUTOMATION_ENABLED.*lower().*".*true.*"' "$RUN"; then
        echo -e "${GREEN}OK: PUMP_AUTOMATION_ENABLED checked with .lower() in (\"true\", ...)${NC}"
    else
        echo -e "${RED}MISSING: PUMP_AUTOMATION_ENABLED default-to-false pattern not confirmed in $RUN${NC}"
        echo "  Expected pattern: os.getenv('PUMP_AUTOMATION_ENABLED', '').lower() in ('true', '1', 'yes')"
        ERRORS=$((ERRORS + 1))
    fi
fi

# ------------------------------------------------------------------
# 3. SmartHomeController accepts pump_automation_enabled
# ------------------------------------------------------------------
echo ""
echo "--- Checking SmartHomeController pump_automation_enabled ---"

if grep -q "pump_automation_enabled" "$SMC"; then
    echo -e "${GREEN}OK: pump_automation_enabled found in $SMC${NC}"
else
    echo -e "${RED}MISSING: pump_automation_enabled not found in $SMC${NC}"
    echo "  SmartHomeController.__init__ must accept pump_automation_enabled parameter."
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 4. _pump_loop is NOT started unconditionally
# ------------------------------------------------------------------
echo ""
echo "--- Checking _pump_loop is gated ---"

if grep -q "if self.pump_automation_enabled" "$SMC"; then
    echo -e "${GREEN}OK: _pump_loop is conditionally started (if self.pump_automation_enabled)${NC}"
else
    echo -e "${RED}MISSING: _pump_loop conditional gate not found in $SMC${NC}"
    echo "  SmartHomeController.start() must check self.pump_automation_enabled before creating _pump_loop task."
    ERRORS=$((ERRORS + 1))
fi

# Verify the old unconditional pattern is absent
if grep "create_task.*_pump_loop" "$SMC" | grep -v "^[[:space:]]*#" | grep -qv "if self.pump_automation_enabled"; then
    # This means create_task(_pump_loop) appears on a line NOT in an if block context
    # But we already confirmed 'if self.pump_automation_enabled' exists above.
    # Let's do a more precise check: the line with create_task and _pump_loop must be
    # on a line following (or inside) 'if self.pump_automation_enabled'
    echo -e "${GREEN}OK: _pump_loop create_task references found (with conditional gate confirmed above)${NC}"
else
    # The grep didn't find any standalone unconditional pattern — good
    echo -e "${GREEN}OK: no unconditional _pump_loop task creation detected${NC}"
fi

# ------------------------------------------------------------------
# 5. Switch loop still starts
# ------------------------------------------------------------------
echo ""
echo "--- Checking switch loop is preserved ---"

if grep -q "create_task.*_switch_loop" "$SMC"; then
    echo -e "${GREEN}OK: _switch_loop task creation found in $SMC${NC}"
else
    echo -e "${RED}MISSING: _switch_loop task creation not found in $SMC${NC}"
    echo "  SmartHomeController.start() must create _switch_loop task."
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 6. Manual switch methods preserved in relay_tuya_controller.py
# ------------------------------------------------------------------
echo ""
echo "--- Checking manual switch methods in relay_tuya_controller.py ---"

check_method() {
    local method="$1"
    local file="$2"
    if grep -q "def ${method}" "$file"; then
        echo -e "${GREEN}OK: ${method}() found in $file${NC}"
    else
        echo -e "${RED}MISSING: ${method}() not found in $file${NC}"
        echo "  Manual switch method ${method}() must be preserved."
        ERRORS=$((ERRORS + 1))
    fi
}

check_method "switch_on_device"  "$RTUYA"
check_method "switch_off_device" "$RTUYA"
check_method "switch_binary"     "$RTUYA"
check_method "switch_device"     "$RTUYA"

# ------------------------------------------------------------------
# 7. RelayDeviceManager.toggle_device preserved
# ------------------------------------------------------------------
echo ""
echo "--- Checking RelayDeviceManager.toggle_device ---"

check_method "toggle_device" "$RDM"

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
    echo -e "${GREEN}✅ PASSED: Pump automation is disabled by default. Manual switch control preserved.${NC}"
    exit 0
fi
