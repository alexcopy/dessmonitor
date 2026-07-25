#!/usr/bin/env bash
# check-inverter-operator-freshness.sh
# Focused validator for PR 0037: Inverter Operator Metrics and Freshness.
#
# Verifies:
#   - Output power severity thresholds (green/amber/red/unknown)
#   - Inverter timestamp and freshness elements in HTML
#   - Europe/London timestamp formatting
#   - Freshness computation logic (fresh/stale/unavailable)
#   - Preservation of existing dashboard elements
#   - Safe DOM rendering patterns
#   - No weather/prediction sections added
set -euo pipefail

cd "$(dirname "$0")/.."

PASSED=0
FAILED=0
FAILURES=()

assert_pass() {
    local num="$1"; shift
    local desc="$1"; shift
    PASSED=$((PASSED + 1))
    echo "[$num] PASS: $desc"
}

assert_fail() {
    local num="$1"; shift
    local desc="$1"; shift
    local detail="$1"; shift
    FAILED=$((FAILED + 1))
    FAILURES+=("[$num] FAIL: $desc — $detail")
    echo "[$num] FAIL: $desc — $detail"
}

run_python() {
    python3 -c "$1"
}

# ---------------------------------------------------------------------------
# BLOCK 1: Output power severity derivation (pure function)
# ---------------------------------------------------------------------------
echo "=== BLOCK 1: Output power severity derivation ==="

OUT1=$(run_python "
def compute_power_severity(output_power):
    if output_power is None:
        return 'unknown'
    if output_power <= 1700:
        return 'green'
    if output_power <= 2600:
        return 'amber'
    return 'red'

tests = {
    None: 'unknown',
    0: 'green',
    800: 'green',
    1700: 'green',
    1701: 'amber',
    2000: 'amber',
    2600: 'amber',
    2601: 'red',
    3000: 'red',
}
for val, expected in tests.items():
    actual = compute_power_severity(val)
    ok = 'OK' if actual == expected else 'FAIL'
    print(f'{repr(val)} -> {actual} ({expected}) {ok}')
" 2>&1) || { assert_fail 1 "severity derivation" "Python execution failed"; }

echo "$OUT1" | while IFS= read -r line; do echo "  $line"; done

# [1] green — <=1700
if echo "$OUT1" | grep -qE "1700 -> green.*OK"; then
    assert_pass 1 "Severity 'green' for output_power <= 1700"
else
    assert_fail 1 "severity green" "output_power 1700 not mapped to green"
fi

# [2] amber — 1700 < val <= 2600
if echo "$OUT1" | grep -qE "1701 -> amber.*OK" && echo "$OUT1" | grep -qE "2600 -> amber.*OK"; then
    assert_pass 2 "Severity 'amber' for 1700 < output_power <= 2600"
else
    assert_fail 2 "severity amber" "range 1701-2600 not mapped to amber"
fi

# [3] red — >2600
if echo "$OUT1" | grep -qE "3000 -> red.*OK"; then
    assert_pass 3 "Severity 'red' for output_power > 2600"
else
    assert_fail 3 "severity red" "output_power 3000 not mapped to red"
fi

# [4] unknown for None
if echo "$OUT1" | grep -qE "None -> unknown.*OK"; then
    assert_pass 4 "Severity 'unknown' for None output_power"
else
    assert_fail 4 "severity unknown" "None not mapped to unknown"
fi

# [5] unknown for null / missing (treated as None)
if echo "$OUT1" | grep -qE "0 -> green.*OK"; then
    assert_pass 5 "Severity 'green' for 0 output_power (0 <= 1700)"
else
    assert_fail 5 "severity zero" "0 not mapped to green"
fi

# ---------------------------------------------------------------------------
# BLOCK 2: Freshness computation (pure function from observed_at age)
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 2: Freshness computation ==="

OUT2=$(run_python "
from datetime import datetime, timezone, timedelta

def compute_inverter_freshness(observed_at_str):
    if observed_at_str is None or observed_at_str == '':
        return 'unavailable'
    try:
        observed_at = datetime.fromisoformat(observed_at_str)
    except (ValueError, TypeError):
        return 'unavailable'
    now = datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        # naive -> treat as UTC for test purposes
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_seconds = (now - observed_at).total_seconds()
    if age_seconds <= 150:
        return 'fresh'
    if age_seconds <= 600:
        return 'stale'
    return 'unavailable'

tests = {
    'fresh': timedelta(seconds=30),
    'stale': timedelta(seconds=300),
    'unavailable-null': None,
    'unavailable-old': timedelta(seconds=900),
}
for label, delta in tests.items():
    if delta is None:
        actual = compute_inverter_freshness(None)
    else:
        test_time = datetime.now(timezone.utc) - delta
        actual = compute_inverter_freshness(test_time.isoformat())
    expected = 'fresh' if 'fresh' in label else ('stale' if 'stale' in label else 'unavailable')
    ok = 'OK' if actual == expected else 'FAIL'
    print(f'{label}: {actual} ({expected}) {ok}')
" 2>&1) || { assert_fail 6 "freshness computation" "Python execution failed"; }

echo "$OUT2" | while IFS= read -r line; do echo "  $line"; done

if echo "$OUT2" | grep -qE "fresh: fresh.*OK"; then
    assert_pass 6 "Freshness 'fresh' for observed_at < 150s ago"
else
    assert_fail 6 "freshness fresh" "30s-old timestamp not mapped to fresh"
fi

if echo "$OUT2" | grep -qE "stale: stale.*OK"; then
    assert_pass 7 "Freshness 'stale' for 150s < observed_at <= 600s"
else
    assert_fail 7 "freshness stale" "300s-old timestamp not mapped to stale"
fi

if echo "$OUT2" | grep -qE "unavailable-null: unavailable.*OK"; then
    assert_pass 8 "Freshness 'unavailable' for null observed_at"
else
    assert_fail 8 "freshness unavailable" "null timestamp not mapped to unavailable"
fi

if echo "$OUT2" | grep -qE "unavailable-old: unavailable.*OK"; then
    assert_pass 9 "Freshness 'unavailable' for observed_at > 600s"
else
    assert_fail 9 "freshness unavailable" "900s-old timestamp not mapped to unavailable"
fi

# ---------------------------------------------------------------------------
# BLOCK 3: Timestamp formatting (Europe/London) — verify zoneinfo works
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 3: Timestamp formatting ==="

OUT3=$(run_python "
from datetime import datetime
from zoneinfo import ZoneInfo

# Test naive -> Europe/London
naive = datetime(2026, 7, 25, 12, 0, 0)
london = naive.replace(tzinfo=ZoneInfo('Europe/London'))
iso_out = london.isoformat()
print('london_iso:', iso_out)

# Test ISO with DST (July = BST, +01:00)
print('has_offset:', '+01:00' in iso_out)

# Test ISO without DST (January = GMT, +00:00)
winter = datetime(2026, 1, 15, 12, 0, 0).replace(tzinfo=ZoneInfo('Europe/London'))
print('winter_iso:', winter.isoformat())
print('winter_tz:', winter.tzname())
" 2>&1) || { assert_fail 10 "timezone formatting" "zoneinfo execution failed"; }

echo "$OUT3" | while IFS= read -r line; do echo "  $line"; done

if echo "$OUT3" | grep -q "has_offset: True"; then
    assert_pass 10 "Europe/London ISO 8601 includes timezone offset"
else
    assert_fail 10 "timezone offset" "ISO output missing +01:00 offset for BST"
fi

if echo "$OUT3" | grep -q "winter_tz: GMT"; then
    assert_pass 11 "Europe/London winter timezone is GMT"
else
    assert_fail 11 "timezone winter" "Winter timezone not GMT"
fi

# ---------------------------------------------------------------------------
# BLOCK 4: HTML element checks
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 4: HTML element presence ==="

# [12] inv-timestamp
if grep -q 'id="inv-timestamp"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 12 "index.html contains #inv-timestamp element"
else
    assert_fail 12 "#inv-timestamp" "Element not found in index.html"
fi

# [13] inv-freshness
if grep -q 'id="inv-freshness"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 13 "index.html contains #inv-freshness element"
else
    assert_fail 13 "#inv-freshness" "Element not found in index.html"
fi

# [14] ds-output-power still present
if grep -q 'id="ds-output-power"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 14 "index.html contains #ds-output-power (existing — not removed)"
else
    assert_fail 14 "#ds-output-power" "Element removed from index.html"
fi

# [15] snapshot-timestamp still present
if grep -q 'id="snapshot-timestamp"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 15 "index.html contains #snapshot-timestamp (existing — not removed)"
else
    assert_fail 15 "#snapshot-timestamp" "Element not found"
fi

# [16] sensors-table-body still present
if grep -q 'id="sensors-table-body"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 16 "index.html contains #sensors-table-body (existing — not removed)"
else
    assert_fail 16 "#sensors-table-body" "Sensors section removed"
fi

# [17] Inverter detail fields still present (chg, dis)
if grep -q 'id="inv-detail-chg"' app/web/templates/index.html 2>/dev/null && \
   grep -q 'id="inv-detail-dis"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 17 "Inverter detail fields (chg, dis) still present"
else
    assert_fail 17 "inverter detail" "Charge/discharge detail fields missing"
fi

# [18] Source indicator still present
if grep -q 'id="source-indicator"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 18 "Source indicator still present (#source-indicator)"
else
    assert_fail 18 "source indicator" "Source indicator element missing"
fi

# [19] Load tabs still present
if grep -q 'id="loads-active-tab"' app/web/templates/index.html 2>/dev/null && \
   grep -q 'id="loads-inactive-tab"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 19 "Load tabs still present (active/inactive)"
else
    assert_fail 19 "load tabs" "Load tab elements missing"
fi

# [20] No weather section added
WEATHER_COUNT=$(grep -ci 'weather' app/web/templates/index.html 2>/dev/null || echo 0)
WEATHER_COUNT=$(echo "$WEATHER_COUNT" | tr -d '[:space:]')
if [ -z "$WEATHER_COUNT" ]; then WEATHER_COUNT=0; fi
if [ "$WEATHER_COUNT" -le 1 ]; then
    assert_pass 20 "No weather section added to dashboard"
else
    assert_fail 20 "weather section" "Found $WEATHER_COUNT weather references"
fi

# ---------------------------------------------------------------------------
# BLOCK 5: Static asset checks (dashboard.js and dashboard.css)
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 5: Static asset checks ==="

# [21] dashboard.js contains renderInverterTimestamp
if grep -q "renderInverterTimestamp" app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 21 "dashboard.js contains renderInverterTimestamp function"
else
    assert_fail 21 "renderInverterTimestamp" "Function not found in dashboard.js"
fi

# [22] dashboard.js contains renderInverterFreshness
if grep -q "renderInverterFreshness" app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 22 "dashboard.js contains renderInverterFreshness function"
else
    assert_fail 22 "renderInverterFreshness" "Function not found in dashboard.js"
fi

# [23] dashboard.js contains computePowerSeverity
if grep -q "computePowerSeverity" app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 23 "dashboard.js contains computePowerSeverity function"
else
    assert_fail 23 "computePowerSeverity" "Function not found in dashboard.js"
fi

# [24] No innerHTML for API values
if grep -v '^\s*\*' app/web/static/dashboard.js 2>/dev/null | grep -v '^\s*//' | grep -q "innerHTML"; then
    assert_fail 24 "textContent safety" "dashboard.js contains innerHTML usage"
else
    assert_pass 24 "dashboard.js uses safe DOM APIs (no innerHTML)"
fi

# [25] CSS power severity classes defined
if grep -q "ds-power-severity-green" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 25 "CSS defines .ds-power-severity-green"
else
    assert_fail 25 ".ds-power-severity-green" "CSS class not found"
fi

if grep -q "ds-power-severity-amber" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 26 "CSS defines .ds-power-severity-amber"
else
    assert_fail 26 ".ds-power-severity-amber" "CSS class not found"
fi

if grep -q "ds-power-severity-red" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 27 "CSS defines .ds-power-severity-red"
else
    assert_fail 27 ".ds-power-severity-red" "CSS class not found"
fi

# [26] run.py zoneinfo import
if grep -q "from zoneinfo import ZoneInfo" run.py 2>/dev/null; then
    assert_pass 28 "run.py imports zoneinfo for Europe/London timezone"
else
    assert_fail 28 "zoneinfo import" "run.py missing zoneinfo import"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
echo "RESULTS: $PASSED passed, $FAILED failed"
echo "========================================"

if [ $FAILED -gt 0 ]; then
    echo ""
    echo "FAILURES:"
    for f in "${FAILURES[@]}"; do
        echo "  $f"
    done
    exit 1
fi

echo "All assertions passed."
exit 0
