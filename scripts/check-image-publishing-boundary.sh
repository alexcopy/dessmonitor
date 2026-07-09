#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Image Publishing Boundary Check
# ==============================================================================
# Verifies that the deployment boundary documentation exists and contains
# required boundary concepts:
#   - Docker Hub
#   - external GitOps repository
#   - ArgoCD
#   - non-authoritative
#
# This script is read-only. It does not query network services, Docker Hub,
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

BOUNDARY_FILE=".project-memory/DEPLOYMENT_BOUNDARY.md"

echo "=========================================="
echo " dessmonitor Image Publishing Boundary Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Boundary documentation file exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking deployment boundary documentation ---"

if [ -f "$BOUNDARY_FILE" ]; then
    echo -e "${GREEN}OK: $BOUNDARY_FILE exists${NC}"
else
    echo -e "${RED}MISSING: $BOUNDARY_FILE not found${NC}"
    echo "  Create .project-memory/DEPLOYMENT_BOUNDARY.md documenting the"
    echo "  boundary between this repository (image publishing) and the"
    echo "  external GitOps/ArgoCD repository (real deployment state)."
    ERRORS=$((ERRORS + 1))
    # Cannot continue with content checks if file is missing
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
# 2. Required boundary concepts
# ------------------------------------------------------------------
echo ""
echo "--- Checking required boundary concepts ---"

check_concept() {
    local pattern="$1"
    local label="$2"
    if grep -q "$pattern" "$BOUNDARY_FILE"; then
        echo -e "${GREEN}OK: '$label' found${NC}"
    else
        echo -e "${RED}MISSING: '$label' not found in $BOUNDARY_FILE${NC}"
        echo "  The deployment boundary documentation must reference '$label'."
        ERRORS=$((ERRORS + 1))
    fi
}

check_concept "Docker Hub"          "Docker Hub"
check_concept "GitOps.*external\|external.*GitOps"    "external GitOps repository"
check_concept "ArgoCD"              "ArgoCD"
check_concept "non-authoritative"   "non-authoritative"

# ------------------------------------------------------------------
# 3. CURRENT_STATE.md contains corrected boundary
# ------------------------------------------------------------------
echo ""
echo "--- Checking CURRENT_STATE.md for corrected boundary ---"

CURRENT_STATE=".project-memory/CURRENT_STATE.md"

if [ -f "$CURRENT_STATE" ]; then
    if grep -q "external repository" "$CURRENT_STATE"; then
        echo -e "${GREEN}OK: CURRENT_STATE.md references external repository${NC}"
    else
        echo -e "${RED}MISSING: CURRENT_STATE.md does not reference external repository${NC}"
        echo "  CURRENT_STATE.md should state that the real ArgoCD/GitOps"
        echo "  source of truth is in a separate external repository."
        ERRORS=$((ERRORS + 1))
    fi

    if grep -q "non-authoritative" "$CURRENT_STATE"; then
        echo -e "${GREEN}OK: CURRENT_STATE.md references non-authoritative app/docker files${NC}"
    else
        echo -e "${RED}MISSING: CURRENT_STATE.md does not reference non-authoritative app/docker files${NC}"
        echo "  CURRENT_STATE.md should state that app/docker/ files are"
        echo "  legacy, auxiliary, or non-authoritative."
        ERRORS=$((ERRORS + 1))
    fi
else
    echo -e "${RED}MISSING: $CURRENT_STATE not found${NC}"
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
    echo -e "${GREEN}✅ PASSED: All boundary checks passed.${NC}"
    exit 0
fi
