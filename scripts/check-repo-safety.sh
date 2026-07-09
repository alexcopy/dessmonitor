#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Repository Safety Check
# ==============================================================================
# Checks for accidentally tracked runtime or secret artifacts.
# Exits nonzero if tracked forbidden files are found.
# Does not require network access or external services.
# Does not read secret values.
# ==============================================================================

set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

echo "=========================================="
echo " dessmonitor Repository Safety Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Check for tracked forbidden patterns
# ------------------------------------------------------------------
echo ""
echo "--- Checking tracked forbidden artifacts ---"

FORBIDDEN_PATTERNS=(
    "config\.json"
    "config_cache\.json"
    "fallback_data\.json"
    "devices\.yaml"
    "devices_prod\.yaml"
    "web_fallback_url\.txt"
    "app/cache/"
    "logs/"
    "ml_data/"
    "\.sqlite"
    "\.sqlite3"
    "\.sqlite-wal"
    "\.sqlite-shm"
    "\.csv"
    "\.jsonl"
    "\.env"
    "\.env\."
    "\.pem"
    "\.key"
    "\.crt"
)

TRACKED_FORBIDDEN=$(git ls-files 2>/dev/null | grep -E "$(IFS='|'; echo "${FORBIDDEN_PATTERNS[*]}")" || true)

if [ -n "$TRACKED_FORBIDDEN" ]; then
    echo -e "${RED}ERROR: Found tracked forbidden artifacts:${NC}"
    echo "$TRACKED_FORBIDDEN"
    ERRORS=$((ERRORS + 1))
else
    echo -e "${GREEN}OK: No tracked forbidden artifacts found.${NC}"
fi

# ------------------------------------------------------------------
# 2. Check for untracked forbidden patterns (warning only)
# ------------------------------------------------------------------
echo ""
echo "--- Checking for untracked forbidden files (warning only) ---"

UNTRACKED_FORBIDDEN=$(git ls-files --others --exclude-standard 2>/dev/null | grep -E "$(IFS='|'; echo "${FORBIDDEN_PATTERNS[*]}")" || true)

if [ -n "$UNTRACKED_FORBIDDEN" ]; then
    echo -e "${YELLOW}WARNING: Found untracked forbidden files (may need .gitignore update):${NC}"
    echo "$UNTRACKED_FORBIDDEN"
    WARNINGS=$((WARNINGS + 1))
else
    echo -e "${GREEN}OK: No untracked forbidden files found.${NC}"
fi

# ------------------------------------------------------------------
# 3. Check .dockerignore coverage
# ------------------------------------------------------------------
echo ""
echo "--- Checking .dockerignore coverage ---"

REQUIRED_DOCKERIGNORE=(
    "logs/"
    "app/cache/"
    "config.json"
    "config_cache.json"
    "fallback_data.json"
    "devices.yaml"
    "devices_prod.yaml"
    "web_fallback_url.txt"
    "ml_data/"
    "\.sqlite"
    "\.sqlite3"
    "\.sqlite-wal"
    "\.sqlite-shm"
    "\.csv"
    "\.jsonl"
    "\.env"
    "\.env\."
    "\.pem"
    "\.key"
    "\.crt"
    "__pycache__/"
    "\.py\[cod\]"
    "\.git/"
    "\.github/"
    "\.project-memory/"
    "agents/"
    "__MACOSX/"
    "\.DS_Store"
    "\.idea/"
    "\.vscode/"
    "\.pytest_cache/"
    "\.mypy_cache/"
)

if [ ! -f ".dockerignore" ]; then
    echo -e "${RED}ERROR: .dockerignore file not found.${NC}"
    ERRORS=$((ERRORS + 1))
else
    MISSING=0
    for pattern in "${REQUIRED_DOCKERIGNORE[@]}"; do
        if ! grep -q "$pattern" .dockerignore 2>/dev/null; then
            echo -e "${YELLOW}MISSING: $pattern${NC}"
            MISSING=$((MISSING + 1))
        fi
    done

    if [ "$MISSING" -eq 0 ]; then
        echo -e "${GREEN}OK: All required patterns found in .dockerignore.${NC}"
    else
        echo -e "${YELLOW}WARNING: $MISSING pattern(s) missing from .dockerignore.${NC}"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo "=========================================="
echo " Summary"
echo "=========================================="
echo " Errors:   $ERRORS"
echo " Warnings: $WARNINGS"
echo ""

if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}❌ FAILED: $ERRORS error(s) found.${NC}"
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}⚠️  PASSED WITH WARNINGS: $WARNINGS warning(s).${NC}"
    exit 0
else
    echo -e "${GREEN}✅ PASSED: All checks clean.${NC}"
    exit 0
fi
