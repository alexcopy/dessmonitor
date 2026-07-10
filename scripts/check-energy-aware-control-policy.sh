#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Energy-Aware Control Policy Check
# ==============================================================================
# Verifies that the energy-aware control policy document exists and contains
# all required concepts and phrases. Also verifies that PR 0011 does NOT
# enable runtime automation or modify runtime behavior.
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

POLICY=".project-memory/ENERGY_AWARE_CONTROL_POLICY.md"

echo "=========================================="
echo " dessmonitor Energy-Aware Control Policy Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Policy document exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking policy document ---"

if [ -f "$POLICY" ]; then
    echo -e "${GREEN}OK: $POLICY exists${NC}"
else
    echo -e "${RED}MISSING: $POLICY not found${NC}"
    echo "  Create .project-memory/ENERGY_AWARE_CONTROL_POLICY.md."
    ERRORS=$((ERRORS + 1))
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
# 2. Required concepts
# ------------------------------------------------------------------
echo ""
echo "--- Checking required concepts ---"

check_phrase() {
    local pattern="$1"
    local label="$2"
    if grep -qi "$pattern" "$POLICY"; then
        echo -e "${GREEN}OK: '$label' found${NC}"
    else
        echo -e "${RED}MISSING: '$label' not found in $POLICY${NC}"
        echo "  The policy document must contain '$label'."
        ERRORS=$((ERRORS + 1))
    fi
}

check_phrase "energy-aware control"   "energy-aware control"
check_phrase "voltage"                "voltage"
check_phrase "switch ON"             "switch ON"
check_phrase "switch OFF"            "switch OFF"
check_phrase "readiness"             "readiness"
check_phrase "health"                "health"
check_phrase "weather forecast"      "weather forecast"
check_phrase "26\.5"                 "26.5"
check_phrase "evening reserve"       "evening reserve"
check_phrase "ML advisory"           "ML advisory"
check_phrase "ML control"            "ML control"
check_phrase "manual override"       "manual override"
check_phrase "deterministic fallback" "deterministic fallback"
check_phrase "external GitOps"       "external GitOps"

# ------------------------------------------------------------------
# 3. PR 0011 does NOT enable runtime automation
# ------------------------------------------------------------------
echo ""
echo "--- Checking PR 0011 runtime automation disclaimer ---"

if grep -qi "not enabled by PR 0011\|does not enable runtime automation\|PR 0011 does not enable" "$POLICY"; then
    echo -e "${GREEN}OK: Policy document states runtime automation is not enabled by PR 0011${NC}"
else
    echo -e "${RED}MISSING: Policy document does not state runtime automation is not enabled by PR 0011${NC}"
    echo "  The policy document must state that PR 0011 does not enable runtime automation."
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 4. PR 0011 does NOT modify runtime behavior
# ------------------------------------------------------------------
echo ""
echo "--- Checking PR 0011 runtime behavior disclaimer ---"

if grep -qi "does not modify runtime behavior\|no runtime behavior modification\|does not change runtime" "$POLICY"; then
    echo -e "${GREEN}OK: Policy document states runtime behavior is not modified by PR 0011${NC}"
else
    echo -e "${RED}MISSING: Policy document does not state runtime behavior is not modified by PR 0011${NC}"
    echo "  The policy document must state that PR 0011 does not modify runtime behavior."
    ERRORS=$((ERRORS + 1))
fi

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
    echo -e "${GREEN}✅ PASSED: Energy-aware control policy document present and complete.${NC}"
    exit 0
fi
