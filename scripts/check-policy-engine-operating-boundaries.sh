#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Policy Engine Operating Boundaries Check
# ==============================================================================
# Verifies that the policy decision engine operating boundaries document exists
# and contains all required battery extrema, pond life-support invariants,
# forecast strategy rules, scenario matrix, and roadmap split.
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

DOC=".project-memory/POLICY_DECISION_ENGINE.md"

echo "=========================================="
echo " dessmonitor Policy Engine Operating Boundaries Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Document exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking document ---"

if [ -f "$DOC" ]; then
    echo -e "${GREEN}OK: $DOC exists${NC}"
else
    echo -e "${RED}MISSING: $DOC not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

if [ ! -f "$DOC" ]; then
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
# 2. Required battery/pond parameter terms
# ------------------------------------------------------------------
echo ""
echo "--- Checking required battery/pond parameter terms ---"

check_term() {
    local term="$1"
    if grep -q "${term}" "$DOC"; then
        echo -e "${GREEN}OK: '${term}' found in $DOC${NC}"
    else
        echo -e "${RED}MISSING: '${term}' not found in $DOC${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

check_term "battery_grid_fallback_voltage"
check_term "battery_morning_minimum_voltage"
check_term "battery_evening_reserve_voltage"
check_term "battery_high_voltage_spend_threshold"
check_term "battery_full_voltage"
check_term "max_total_load_watts"
check_term "pond_hot_water_temperature_c"

# ------------------------------------------------------------------
# 3. Required numeric values
# ------------------------------------------------------------------
echo ""
echo "--- Checking required numeric values ---"

check_value() {
    local value="$1"
    if grep -q "${value}" "$DOC"; then
        echo -e "${GREEN}OK: value '${value}' found in $DOC${NC}"
    else
        echo -e "${RED}MISSING: value '${value}' not found in $DOC${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

check_value "24.0"
check_value "24.5"
check_value "26.5"
check_value "28.5"
check_value "29.0"
check_value "29.5"
check_value "2500"
check_value "25"
check_value "26"

# ------------------------------------------------------------------
# 4. Required life-support / pond / fish / aeration terms
# ------------------------------------------------------------------
echo ""
echo "--- Checking required pond/fish/aeration life-support terms ---"

check_term "life-support"
check_term "aeration"
check_term "fish"
check_term "pond"

# ------------------------------------------------------------------
# 5. Required strategy terms
# ------------------------------------------------------------------
echo ""
echo "--- Checking required strategy terms ---"

check_term "morning minimum"
check_term "high-voltage spend"
check_term "inverter max load"
check_term "discretionary load shedding"

# ------------------------------------------------------------------
# 6. Required roadmap split terms
# ------------------------------------------------------------------
echo ""
echo "--- Checking required roadmap split terms ---"

check_term "0018A"
check_term "0018B"
check_term "0018C"
check_term "0018D"
check_term "0019"
check_term "0020"
check_term "command proposal before automatic execution"

# ------------------------------------------------------------------
# 7. Required ML control boundary
# ------------------------------------------------------------------
echo ""
echo "--- Checking ML control boundary ---"

check_term "ML control"

# ------------------------------------------------------------------
# 8. Scenario matrix terms
# ------------------------------------------------------------------
echo ""
echo "--- Checking scenario matrix terms ---"

# Each scenario has a key descriptor
check_scenario() {
    local term="$1"
    if grep -qi "${term}" "$DOC"; then
        echo -e "${GREEN}OK: scenario descriptor '${term}' found in $DOC${NC}"
    else
        echo -e "${RED}MISSING: scenario descriptor '${term}' not found in $DOC${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

check_scenario "Cloudy morning"
check_scenario "sunny forecast"
check_scenario "bad forecast"
check_scenario "29.5"
check_scenario "2500"
check_scenario "24.0"
check_scenario "evening reserve"
check_scenario "weather unknown"
check_scenario "pond temperature"
check_scenario "inverter cap"
check_scenario "unhealthy"
check_scenario "2-4"
check_scenario "PV generation"
check_scenario "discretionary"
check_scenario "shed"

# ------------------------------------------------------------------
# 9. Pond invariant exact text (key fragments)
# ------------------------------------------------------------------
echo ""
echo "--- Checking pond life-support invariant text ---"

check_fragment() {
    local fragment="$1"
    if grep -qi "${fragment}" "$DOC"; then
        echo -e "${GREEN}OK: fragment '${fragment}' found in $DOC${NC}"
    else
        echo -e "${RED}MISSING: fragment '${fragment}' not found in $DOC${NC}"
        ERRORS=$((ERRORS + 1))
    fi
}

check_fragment "life-support"
check_fragment "protected when possible"
check_fragment "2-4"
check_fragment "shed before life-support"
check_fragment "unhealthy"
check_fragment "stale"
check_fragment "unreachable"
check_fragment "blindly"

# ------------------------------------------------------------------
# 10. ROADMAP.md has the split
# ------------------------------------------------------------------
echo ""
echo "--- Checking ROADMAP.md 0018 split ---"

ROADMAP=".project-memory/ROADMAP.md"
if [ -f "$ROADMAP" ]; then
    for pr in "0018A" "0018B" "0018C" "0018D" "0019" "0020"; do
        if grep -q "${pr}" "$ROADMAP"; then
            echo -e "${GREEN}OK: '${pr}' found in $ROADMAP${NC}"
        else
            echo -e "${RED}MISSING: '${pr}' not found in $ROADMAP${NC}"
            ERRORS=$((ERRORS + 1))
        fi
    done
else
    echo -e "${RED}MISSING: $ROADMAP not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 11. Runtime files not modified
# ------------------------------------------------------------------
echo ""
echo "--- Checking runtime files unchanged ---"

RUNTIME_FILES=(
    "run.py"
    "app/control/"
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

git diff --name-only HEAD > /tmp/pr0018a_diff.txt 2>/dev/null

if [ -f /tmp/pr0018a_diff.txt ]; then
    for path in "${RUNTIME_FILES[@]}"; do
        if grep -q "^${path}" /tmp/pr0018a_diff.txt; then
            echo -e "${RED}FAIL: Runtime file '$path' was modified${NC}"
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}OK: '$path' not modified${NC}"
        fi
    done
else
    echo -e "${GREEN}OK: No tracked changes detected${NC}"
fi

rm -f /tmp/pr0018a_diff.txt

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
    echo -e "${RED}FAILED: $ERRORS error(s).${NC}"
    exit 1
else
    echo -e "${GREEN}PASSED: Policy engine operating boundaries document present and complete.${NC}"
    exit 0
fi
