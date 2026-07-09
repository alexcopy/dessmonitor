#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Platform Control Redesign Check
# ==============================================================================
# Verifies that the platform control redesign strategy documentation exists
# and contains required concepts and migration roadmap references.
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

STRATEGY_FILE=".project-memory/PLATFORM_CONTROL_REDESIGN.md"

echo "=========================================="
echo " dessmonitor Platform Control Redesign Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Strategy documentation file exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking platform control redesign documentation ---"

if [ -f "$STRATEGY_FILE" ]; then
    echo -e "${GREEN}OK: $STRATEGY_FILE exists${NC}"
else
    echo -e "${RED}MISSING: $STRATEGY_FILE not found${NC}"
    echo "  Create .project-memory/PLATFORM_CONTROL_REDESIGN.md documenting the"
    echo "  platform control redesign strategy."
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

check_concept() {
    local pattern="$1"
    local label="$2"
    if grep -q "$pattern" "$STRATEGY_FILE"; then
        echo -e "${GREEN}OK: '$label' found${NC}"
    else
        echo -e "${RED}MISSING: '$label' not found in $STRATEGY_FILE${NC}"
        echo "  The strategy documentation must reference '$label'."
        ERRORS=$((ERRORS + 1))
    fi
}

check_concept "pump"                "pump"
check_concept "water pump"          "water pump"
check_concept "ON/OFF"              "ON/OFF"
check_concept "SwitchableLoad"      "SwitchableLoad"
check_concept "ControlCommand"      "ControlCommand"
check_concept "0008-disable-pump-automation" "0008-disable-pump-automation"
check_concept "ML control"          "ML control"
check_concept "external GitOps"     "external GitOps"

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
    echo -e "${GREEN}✅ PASSED: All platform control redesign checks passed.${NC}"
    exit 0
fi
