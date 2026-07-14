#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Energy Policy Config Example Check
# ==============================================================================
# Verifies that the static no-secret energy policy config example YAML exists,
# contains all required phrases, has no secret-like terms, and is valid YAML.
# Also verifies that no runtime files were modified.
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

EXAMPLE="examples/energy_policy.example.yaml"
DOC=".project-memory/ENERGY_POLICY_CONFIG_EXAMPLE.md"

echo "=========================================="
echo " dessmonitor Energy Policy Config Example Check"
echo "=========================================="

# ------------------------------------------------------------------
# 1. Example YAML exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking example file ---"

if [ -f "$EXAMPLE" ]; then
    echo -e "${GREEN}OK: $EXAMPLE exists${NC}"
else
    echo -e "${RED}MISSING: $EXAMPLE not found${NC}"
    echo "  Create examples/energy_policy.example.yaml."
    ERRORS=$((ERRORS + 1))
fi

if [ ! -f "$EXAMPLE" ]; then
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
# 2. Documentation file exists
# ------------------------------------------------------------------
echo ""
echo "--- Checking documentation file ---"

if [ -f "$DOC" ]; then
    echo -e "${GREEN}OK: $DOC exists${NC}"
else
    echo -e "${RED}MISSING: $DOC not found${NC}"
    echo "  Create .project-memory/ENERGY_POLICY_CONFIG_EXAMPLE.md."
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 3. Required example phrases
# ------------------------------------------------------------------
echo ""
echo "--- Checking required example phrases ---"

check_phrase() {
    local pattern="$1"
    local label="$2"
    if grep -q "$pattern" "$EXAMPLE"; then
        echo -e "${GREEN}OK: '${label}' found in $EXAMPLE${NC}"
    else
        echo -e "${RED}MISSING: '${label}' not found in $EXAMPLE${NC}"
        echo "  The example must contain '${label}'."
        ERRORS=$((ERRORS + 1))
    fi
}

check_phrase "energy_aware_policy_version"        "energy_aware_policy_version"
check_phrase "runtime_loaded: false"               "runtime_loaded: false"
check_phrase "example_only: true"                   "example_only: true"
check_phrase "evening_reserve_voltage: 26\.5"       "evening_reserve_voltage: 26.5"
check_phrase "grid_or_mains_conservation"           "grid_or_mains_conservation"
check_phrase "weather_adjustment"                   "weather_adjustment"
check_phrase "readiness"                            "readiness"
check_phrase "health"                               "health"
check_phrase "manual_override"                      "manual_override"
check_phrase "deterministic_fallback"               "deterministic_fallback"
check_phrase "ml_advisory_enabled: false"           "ml_advisory_enabled: false"
check_phrase "ml_control_enabled: false"            "ml_control_enabled: false"
check_phrase "minimum_voltage_to_switch_on"         "minimum_voltage_to_switch_on"
check_phrase "voltage_to_switch_off"                "voltage_to_switch_off"
check_phrase "fail_safe_off"                        "fail_safe_off"

# ------------------------------------------------------------------
# 4. No secret-like terms
# ------------------------------------------------------------------
echo ""
echo "--- Checking no secret-like terms ---"

check_no_secret() {
    local term="$1"
    if grep -qi "$term" "$EXAMPLE"; then
        echo -e "${RED}FAIL: Forbidden term '${term}' found in $EXAMPLE${NC}"
        echo "  The example must not contain '${term}'."
        ERRORS=$((ERRORS + 1))
    else
        echo -e "${GREEN}OK: '${term}' not found in $EXAMPLE${NC}"
    fi
}

check_no_secret "api_key"
check_no_secret "token"
check_no_secret "secret"
check_no_secret "password"
check_no_secret "credential"
check_no_secret "tuya_device_id"
check_no_secret "device_id"
check_no_secret "local_ip"
check_no_secret "kubeconfig"
check_no_secret "bearer"
check_no_secret "private_key"

# ------------------------------------------------------------------
# 5. Validate YAML syntax
# ------------------------------------------------------------------
echo ""
echo "--- Validating YAML syntax ---"

if python3 -c "import yaml; yaml.safe_load(open('$EXAMPLE')); print('YAML: OK')" 2>/dev/null; then
    echo -e "${GREEN}OK: $EXAMPLE is valid YAML${NC}"
else
    echo -e "${RED}FAIL: $EXAMPLE is not valid YAML${NC}"
    echo "  Fix YAML syntax in $EXAMPLE."
    ERRORS=$((ERRORS + 1))
fi

# ------------------------------------------------------------------
# 6. Runtime files not modified
# ------------------------------------------------------------------
echo ""
echo "--- Checking runtime files unchanged ---"

RUNTIME_FILES=(
    "run.py"
    "app/"
    "service/"
    "shared_state/"
    "devices.yaml"
    "devices_prod.yaml"
    "config.json"
    "config_cache.json"
    "fallback_data.json"
    "web_fallback_url.txt"
    "app/cache/"
    "logs/"
    "ml_data/"
)

git diff --name-only HEAD > /tmp/pr0013_diff.txt 2>/dev/null

if [ -f /tmp/pr0013_diff.txt ]; then
    for path in "${RUNTIME_FILES[@]}"; do
        if grep -q "^${path}" /tmp/pr0013_diff.txt; then
            echo -e "${RED}FAIL: Runtime file '$path' was modified${NC}"
            echo "  PR 0013 must not change runtime files."
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}OK: '$path' not modified${NC}"
        fi
    done
else
    echo -e "${GREEN}OK: No tracked changes detected${NC}"
fi

rm -f /tmp/pr0013_diff.txt

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
    echo -e "${GREEN}✅ PASSED: Energy policy config example present and safe.${NC}"
    exit 0
fi
