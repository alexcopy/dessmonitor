#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Project-Memory Structure Check
# ==============================================================================
# Verifies that required .project-memory governance files exist.
# Exits 0 if all files are present, 1 if any are missing.
# Does not require network access or external services.
# ==============================================================================

set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

ERRORS=0

echo "=========================================="
echo " dessmonitor Project-Memory Structure Check"
echo "=========================================="

# ------------------------------------------------------------------
# Required governance files
# ------------------------------------------------------------------
REQUIRED_FILES=(
    ".project-memory/AGENT_STANDARD.txt"
    ".project-memory/ORCHESTRATOR_STANDARD.txt"
    ".project-memory/ROADMAP.md"
    ".project-memory/CURRENT_STATE.md"
    ".project-memory/ADR/ADR-0001-agent-workflow.md"
    ".project-memory/ADR/ADR-0002-dockerhub-argocd-deployment.md"
    ".project-memory/ADR/ADR-0003-runtime-safety-before-ml-control.md"
)

echo ""
echo "--- Checking required governance files ---"

for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "OK: $file"
    else
        echo -e "${RED}MISSING: $file${NC}"
        ERRORS=$((ERRORS + 1))
    fi
done

# ------------------------------------------------------------------
# PR 0003 artifact directory check
# ------------------------------------------------------------------
echo ""
echo "--- Checking PR 0003 artifact directory ---"

PR_DIR=".project-memory/pr/0003-validation-baseline"

if [ -d "$PR_DIR" ]; then
    echo -e "OK: $PR_DIR directory exists"
else
    echo -e "${RED}MISSING: $PR_DIR directory not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check PLAN.md
if [ -f "$PR_DIR/PLAN.md" ]; then
    echo -e "OK: $PR_DIR/PLAN.md"
else
    echo -e "${RED}MISSING: $PR_DIR/PLAN.md${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check PLAN_REVIEW.yaml
if [ -f "$PR_DIR/PLAN_REVIEW.yaml" ]; then
    echo -e "OK: $PR_DIR/PLAN_REVIEW.yaml"
else
    echo -e "${RED}MISSING: $PR_DIR/PLAN_REVIEW.yaml${NC}"
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
    echo -e "${RED}❌ FAILED: $ERRORS missing file(s).${NC}"
    exit 1
else
    echo -e "${GREEN}✅ PASSED: All required files present.${NC}"
    exit 0
fi
