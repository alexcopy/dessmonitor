#!/usr/bin/env bash
# ==============================================================================
# dessmonitor Project-Memory Structure Check (PR 0034b)
# ==============================================================================
# Verifies required .project-memory governance files and validates
# PR directory artifact conventions.
#
# Canonical artifact contract (PR 0034a+):
#   PLAN.txt              (plan agent, root)
#   reviews/PLAN_REVIEW.txt       (plan-review agent, reviews/)
#   CODER_REPORT.txt              (coder agent, root)
#   reviews/PRECOMMIT_REVIEW.txt  (precommit-review agent, reviews/)
#
# Transition boundary: 0034a
#   numeric_part < 34          → LEGACY (accept all)
#   numeric_part == 34, ''     → LEGACY
#   numeric_part == 34, 'a'    → CANONICAL (boundary)
#   numeric_part == 34, >'a'   → CANONICAL
#   numeric_part > 34          → CANONICAL
#   numeric_part empty         → check frozen allowlist
#
# Frozen historical non-numeric allowlist:
#   fix-auth-validation-python-discovery
#
# Exits 0 if all checks pass, 1 if errors found.
# Deterministic, offline, non-mutating, CI-safe.
# ==============================================================================

set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0
PR_BASE=".project-memory/pr"

# Frozen historical non-numeric directory allowlist.
# Only actual approved historical exceptions belong here.
ALLOWLIST_NON_NUMERIC=(
    "fix-auth-validation-python-discovery"
)

# Legacy artifact names (forbidden in canonical directories)
LEGACY_PLAN_NAMES=("PLAN.md")
LEGACY_REVIEW_NAMES=("PLAN_REVIEW.yaml" "reviews/plan-review.yml")
LEGACY_PRECOMMIT_NAMES=("PRECOMMIT_REVIEW.yaml" "reviews/precommit-review.yml")
LEGACY_CODER_ALT_NAMES=("implementation-report.md")

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
# Structural numeric identifier parser
# ------------------------------------------------------------------

# parse_pr_id <basename>
# Echoes: numeric_part|suffix_part (pipe-separated to avoid IFS issues)
#   numeric_part: leading decimal digits, empty string if none
#   suffix_part: single lowercase alpha after digits, before '-', or empty
parse_pr_id() {
    local basename="$1"
    local numeric_part=""
    local suffix_part=""

    # Extract leading digits using shell parameter expansion.
    # ${var%%[!0-9]*} removes everything from the first non-digit to end.
    numeric_part="${basename%%[!0-9]*}"

    # If the result equals the whole basename, there are no leading digits.
    if [ "$numeric_part" = "$basename" ]; then
        numeric_part=""
        echo "|"
        return
    fi

    # Get remainder after numeric part
    local remainder="${basename#"$numeric_part"}"

    # Check for optional single lowercase alpha suffix
    if [ -n "$remainder" ]; then
        local first_char="${remainder:0:1}"
        case "$first_char" in
            [a-z])
                suffix_part="$first_char"
                ;;
        esac
    fi

    echo "${numeric_part}|${suffix_part}"
}

# ------------------------------------------------------------------
# Frozen allowlist check
# ------------------------------------------------------------------

is_allowlisted() {
    local name="$1"
    for allowed in "${ALLOWLIST_NON_NUMERIC[@]}"; do
        if [ "$name" = "$allowed" ]; then
            return 0
        fi
    done
    return 1
}

# ------------------------------------------------------------------
# Classification: legacy or canonical
# Returns 0 for legacy, 1 for canonical
# ------------------------------------------------------------------

classify_pr_dir() {
    local basename="$1"

    local parsed="$(parse_pr_id "$basename")"
    local numeric_part="${parsed%%|*}"
    local suffix_part="${parsed#*|}"
    # Handle case where parsed is just "|" (no numeric, no suffix)
    if [ "$parsed" = "|" ]; then
        numeric_part=""
        suffix_part=""
    fi

    # No numeric part -> non-numeric directory
    if [ -z "$numeric_part" ]; then
        if is_allowlisted "$basename"; then
            return 0  # legacy (allowlisted)
        else
            echo -e "${RED}REJECTED: $basename — unknown non-numeric PR directory (not in frozen historical allowlist)${NC}"
            ERRORS=$((ERRORS + 1))
            return 2  # rejected
        fi
    fi

    # Numeric part: compare as integers
    local num_val=$((10#$numeric_part))

    if [ "$num_val" -lt 34 ]; then
        return 0  # legacy
    elif [ "$num_val" -gt 34 ]; then
        return 1  # canonical
    fi

    # numeric_part == 34
    if [ -z "$suffix_part" ]; then
        return 0  # legacy (0034, no suffix)
    elif [ "$suffix_part" = "a" ]; then
        return 1  # canonical (boundary 0034a)
    elif [[ "$suffix_part" > "a" ]]; then
        return 1  # canonical (0034b+)
    else
        # suffix_part < "a" -> legacy (shouldn't exist, but handle)
        return 0
    fi
}

# ------------------------------------------------------------------
# Legacy stage detection
# ------------------------------------------------------------------

detect_legacy_stage() {
    local dir="$1"
    local basename="$2"

    local has_plan_md=false
    local has_plan_review_yaml=false
    local has_plan_review_yml=false
    local has_coder_report=false
    local has_precommit_yaml=false
    local has_precommit_yml=false

    [ -f "$dir/PLAN.md" ] && has_plan_md=true
    [ -f "$dir/PLAN_REVIEW.yaml" ] && has_plan_review_yaml=true
    [ -f "$dir/reviews/plan-review.yml" ] && has_plan_review_yml=true
    [ -f "$dir/CODER_REPORT.txt" ] && has_coder_report=true
    [ -f "$dir/PRECOMMIT_REVIEW.yaml" ] && has_precommit_yaml=true
    [ -f "$dir/reviews/precommit-review.yml" ] && has_precommit_yml=true

    # COMPLETE: PRECOMMIT exists
    if $has_precommit_yaml || $has_precommit_yml; then
        echo "COMPLETE (legacy)"
        return 0
    fi

    # IMPLEMENTATION: CODER exists, no PRECOMMIT
    if $has_coder_report; then
        echo "IMPLEMENTATION (legacy)"
        return 0
    fi

    # PLANNING: PLAN_REVIEW exists, no CODER
    if $has_plan_review_yaml || $has_plan_review_yml; then
        echo "PLANNING (legacy)"
        return 0
    fi

    # DRAFT: PLAN.md exists, no other artifacts
    if $has_plan_md; then
        echo "DRAFT (legacy)"
        return 0
    fi

    # EMPTY
    echo "EMPTY"
}

# ------------------------------------------------------------------
# Canonical stage detection
# ------------------------------------------------------------------

detect_canonical_stage() {
    local dir="$1"
    local basename="$2"

    local has_plan_txt=false
    local has_plan_review_txt=false
    local has_coder_report=false
    local has_precommit_txt=false
    local has_plan_md=false
    local has_plan_review_yaml=false
    local has_plan_review_yml=false
    local has_precommit_yaml=false
    local has_precommit_yml=false
    local has_coder_alt=false

    [ -f "$dir/PLAN.txt" ] && has_plan_txt=true
    [ -f "$dir/reviews/PLAN_REVIEW.txt" ] && has_plan_review_txt=true
    [ -f "$dir/CODER_REPORT.txt" ] && has_coder_report=true
    [ -f "$dir/reviews/PRECOMMIT_REVIEW.txt" ] && has_precommit_txt=true

    # Check for forbidden legacy names
    [ -f "$dir/PLAN.md" ] && has_plan_md=true
    [ -f "$dir/PLAN_REVIEW.yaml" ] && has_plan_review_yaml=true
    [ -f "$dir/reviews/plan-review.yml" ] && has_plan_review_yml=true
    [ -f "$dir/PRECOMMIT_REVIEW.yaml" ] && has_precommit_yaml=true
    [ -f "$dir/reviews/precommit-review.yml" ] && has_precommit_yml=true
    [ -f "$dir/implementation-report.md" ] && has_coder_alt=true

    # REJECT: legacy artifact names in canonical directory
    local reject=false
    if $has_plan_md; then
        echo -e "${RED}REJECTED: $basename — legacy PLAN.md present in canonical directory${NC}"
        reject=true
    fi
    if $has_plan_review_yaml; then
        echo -e "${RED}REJECTED: $basename — legacy PLAN_REVIEW.yaml present in canonical directory${NC}"
        reject=true
    fi
    if $has_plan_review_yml; then
        echo -e "${RED}REJECTED: $basename — legacy reviews/plan-review.yml present in canonical directory${NC}"
        reject=true
    fi
    if $has_precommit_yaml; then
        echo -e "${RED}REJECTED: $basename — legacy PRECOMMIT_REVIEW.yaml present in canonical directory${NC}"
        reject=true
    fi
    if $has_precommit_yml; then
        echo -e "${RED}REJECTED: $basename — legacy reviews/precommit-review.yml present in canonical directory${NC}"
        reject=true
    fi
    if $has_coder_alt; then
        echo -e "${RED}REJECTED: $basename — legacy implementation-report.md present in canonical directory${NC}"
        reject=true
    fi

    # Check for any .yaml or .yml files
    local yaml_files
    yaml_files=$(find "$dir" -maxdepth 2 -name '*.yaml' -o -name '*.yml' 2>/dev/null || true)
    if [ -n "$yaml_files" ]; then
        while IFS= read -r yf; do
            echo -e "${RED}REJECTED: $basename — YAML/YML file not allowed in canonical directory: $yf${NC}"
            reject=true
        done <<< "$yaml_files"
    fi

    # Check for wrong casing (use find -name which IS case-sensitive everywhere)
    for wrong_name in "plan.txt" "Plan.txt" "plan.TXT" "PLAN.TXT"; do
        local found
        found=$(find "$dir" -maxdepth 1 -name "$wrong_name" 2>/dev/null || true)
        if [ -n "$found" ]; then
            echo -e "${RED}REJECTED: $basename — wrong casing: $wrong_name (must be PLAN.txt)${NC}"
            reject=true
        fi
    done

    # Check for PLAN_REVIEW.txt in root instead of reviews/
    local wrong_placement
    wrong_placement=$(find "$dir" -maxdepth 1 -name "PLAN_REVIEW.txt" 2>/dev/null || true)
    if [ -n "$wrong_placement" ]; then
        echo -e "${RED}REJECTED: $basename — PLAN_REVIEW.txt in root directory (must be in reviews/)${NC}"
        reject=true
    fi

    # Check for PRECOMMIT_REVIEW.txt in root instead of reviews/
    wrong_placement=$(find "$dir" -maxdepth 1 -name "PRECOMMIT_REVIEW.txt" 2>/dev/null || true)
    if [ -n "$wrong_placement" ]; then
        echo -e "${RED}REJECTED: $basename — PRECOMMIT_REVIEW.txt in root directory (must be in reviews/)${NC}"
        reject=true
    fi

    # Check for CODER_REPORT.txt in reviews/ instead of root
    wrong_placement=$(find "$dir/reviews" -maxdepth 1 -name "CODER_REPORT.txt" 2>/dev/null || true)
    if [ -n "$wrong_placement" ]; then
        echo -e "${RED}REJECTED: $basename — CODER_REPORT.txt in reviews/ (must be in root)${NC}"
        reject=true
    fi

    if $reject; then
        return 1
    fi

    # COMPLETE: PRECOMMIT exists
    if $has_precommit_txt; then
        # All four must exist
        local missing=false
        if ! $has_plan_txt; then
            echo -e "${RED}FAIL: $basename — PRECOMMIT_COMPLETE but missing PLAN.txt${NC}"
            missing=true
        fi
        if ! $has_plan_review_txt; then
            echo -e "${RED}FAIL: $basename — PRECOMMIT_COMPLETE but missing reviews/PLAN_REVIEW.txt${NC}"
            missing=true
        fi
        if ! $has_coder_report; then
            echo -e "${RED}FAIL: $basename — PRECOMMIT_COMPLETE but missing CODER_REPORT.txt${NC}"
            missing=true
        fi
        if $missing; then
            return 1
        fi
        echo "PRECOMMIT COMPLETE"
        return 0
    fi

    # IMPLEMENTATION (coder complete): CODER exists, no PRECOMMIT
    if $has_coder_report; then
        local missing=false
        if ! $has_plan_txt; then
            echo -e "${RED}FAIL: $basename — CODER_COMPLETE but missing PLAN.txt${NC}"
            missing=true
        fi
        if ! $has_plan_review_txt; then
            echo -e "${RED}FAIL: $basename — CODER_COMPLETE but missing reviews/PLAN_REVIEW.txt${NC}"
            missing=true
        fi
        if $missing; then
            return 1
        fi
        echo "CODER COMPLETE"
        return 0
    fi

    # PLANNING COMPLETE: PLAN_REVIEW exists, no CODER
    if $has_plan_review_txt; then
        if ! $has_plan_txt; then
            echo -e "${RED}FAIL: $basename — reviews/PLAN_REVIEW.txt exists but PLAN.txt missing${NC}"
            return 1
        fi
        echo "PLANNING COMPLETE"
        return 0
    fi

    # PLANNING DRAFT: PLAN.txt exists, no PLAN_REVIEW, no CODER
    if $has_plan_txt; then
        echo "PLANNING DRAFT"
        return 0
    fi

    # EMPTY
    echo "EMPTY"
    return 0
}

# ------------------------------------------------------------------
# PR directory validation
# ------------------------------------------------------------------

echo ""
echo "--- Validating PR directories ---"

pr_count=0
legacy_count=0
canonical_count=0
rejected_count=0

for pr_dir in "$PR_BASE"/*/; do
    [ -d "$pr_dir" ] || continue
    pr_count=$((pr_count + 1))
    basename=$(basename "$pr_dir")

    # Classify
    classify_pr_dir "$basename"
    classification=$?

    if [ "$classification" -eq 2 ]; then
        rejected_count=$((rejected_count + 1))
        continue
    elif [ "$classification" -eq 0 ]; then
        legacy_count=$((legacy_count + 1))
        stage=$(detect_legacy_stage "$pr_dir" "$basename")
        echo -e "OK: $basename [$stage]"
    else
        canonical_count=$((canonical_count + 1))
        stage=$(detect_canonical_stage "$pr_dir" "$basename")
        canon_exit=$?
        if [ "$canon_exit" -ne 0 ]; then
            rejected_count=$((rejected_count + 1))
        else
            echo -e "OK: $basename [$stage]"
        fi
    fi
done

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo "=========================================="
echo " Summary"
echo "=========================================="
echo " PR directories found:     $pr_count"
echo " Legacy directories:       $legacy_count"
echo " Canonical directories:    $canonical_count"
echo " Rejected directories:     $rejected_count"
echo " Errors:                   $ERRORS"
echo ""

if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}FAILED: $ERRORS error(s).${NC}"
    exit 1
else
    echo -e "${GREEN}PASSED: All project-memory checks passed.${NC}"
    exit 0
fi
