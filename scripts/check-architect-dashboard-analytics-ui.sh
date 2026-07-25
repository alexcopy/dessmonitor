#!/usr/bin/env bash
# check-architect-dashboard-analytics-ui.sh
# Focused validator for PR 0038: Architect Dashboard and Analytics UI.
#
# Verifies architect layout anchors exist in served templates and static assets.
# Must fail before implementation and pass after implementation.
# Non-production-safe: exercises static file analysis and Python import checks only.
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

# ---------------------------------------------------------------------------
# BLOCK 1: Analytics page/route check
# ---------------------------------------------------------------------------
echo "=== BLOCK 1: Analytics page and route ==="

# [1] analytics.html template exists
if [ -f "app/web/templates/analytics.html" ]; then
    assert_pass 1 "analytics.html template exists"
else
    assert_fail 1 "analytics.html" "Template file not found"
fi

# [2] Analytics route exists in web_routes.py
ANALYTICS_ROUTE=$(grep -c "analytics" app/web_routes.py 2>/dev/null || echo 0)
ANALYTICS_ROUTE=$(echo "$ANALYTICS_ROUTE" | tr -d '[:space:]')
if [ -n "$ANALYTICS_ROUTE" ] && [ "$ANALYTICS_ROUTE" -gt 0 ] 2>/dev/null; then
    assert_pass 2 "Analytics route present in web routes"
else
    assert_fail 2 "analytics route" "No analytics route found in web_routes.py"
fi

# ---------------------------------------------------------------------------
# BLOCK 2: Architect nav layout
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 2: Architect nav layout ==="

# [3] Dashboard nav link with architect class (dm-nav-link)
if grep -q "Dashboard" app/web/templates/index.html 2>/dev/null && grep -q "Analytics" app/web/templates/index.html 2>/dev/null; then
    assert_pass 3 "Dashboard and Analytics nav labels present in index.html"
else
    assert_fail 3 "nav labels" "Dashboard or Analytics nav label missing in index.html"
fi

# [4] Controls and Settings nav labels
if grep -q "Controls" app/web/templates/index.html 2>/dev/null && grep -q "Settings" app/web/templates/index.html 2>/dev/null; then
    assert_pass 4 "Controls and Settings nav labels present in index.html"
else
    assert_fail 4 "nav labels" "Controls or Settings nav label missing in index.html"
fi

# [5] Sticky nav class present
if grep -q "dm-nav" app/web/templates/index.html 2>/dev/null; then
    assert_pass 5 "Architect dm-nav present in index.html"
else
    assert_fail 5 "dm-nav" "Architect nav class not found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 3: Degraded/OK badge
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 3: Degraded/OK badge ==="

# [6] Status badge element with dm-badge class
if grep -q "dm-badge" app/web/templates/index.html 2>/dev/null; then
    assert_pass 6 "dm-badge status element present in index.html"
else
    assert_fail 6 "dm-badge" "Status badge not found in index.html"
fi

# [7] Alert strip element
if grep -q "dm-alert" app/web/templates/index.html 2>/dev/null; then
    assert_pass 7 "dm-alert strip present in index.html"
else
    assert_fail 7 "dm-alert" "Alert strip not found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 4: Inverter hero
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 4: Inverter hero ==="

# [8] Inverter hero section
if grep -q "dm-inverter-hero" app/web/templates/index.html 2>/dev/null; then
    assert_pass 8 "dm-inverter-hero present in index.html"
else
    assert_fail 8 "inverter hero" "dm-inverter-hero not found in index.html"
fi

# [9] Inverter metrics section
if grep -q "dm-inverter-metrics" app/web/templates/index.html 2>/dev/null; then
    assert_pass 9 "dm-inverter-metrics present in index.html"
else
    assert_fail 9 "inverter metrics" "dm-inverter-metrics not found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 5: SOC semicircle gauge
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 5: SOC semicircle gauge ==="

# [10] SOC gauge element (canvas or SVG)
if grep -q "socGauge\|dm-gauge-canvas\|semicircle\|gauge" app/web/templates/index.html 2>/dev/null; then
    assert_pass 10 "SOC gauge element present in index.html"
else
    assert_fail 10 "SOC gauge" "No SOC gauge element found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 6: Mini stat cards
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 6: Mini stat cards ==="

# [11] Mini stat cards section
if grep -q "dm-stats-strip\|dm-stat-mini" app/web/templates/index.html 2>/dev/null; then
    assert_pass 11 "Mini stat cards present in index.html"
else
    assert_fail 11 "mini stats" "dm-stats-strip or dm-stat-mini not found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 7: Loads table
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 7: Loads table ==="

# [12] Loads table with architect dm-table class
if grep -q "dm-table\|loads-table\|loads-active-body\|loads-table-body" app/web/templates/index.html 2>/dev/null; then
    assert_pass 12 "Loads table present in index.html"
else
    assert_fail 12 "loads table" "No loads table found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 8: Sensors panel
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 8: Sensors panel ==="

# [13] Sensors panel
if grep -q "dm-sensor\|sensors-table-body" app/web/templates/index.html 2>/dev/null; then
    assert_pass 13 "Sensors panel present in index.html"
else
    assert_fail 13 "sensors panel" "No sensors panel found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 9: Footer raw inverter line
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 9: Footer raw inverter line ==="

# [14] Footer bar element
if grep -q "dm-footer-bar\|inv-footer\|inverter-detail-section" app/web/templates/index.html 2>/dev/null; then
    assert_pass 14 "Footer raw inverter line present in index.html"
else
    assert_fail 14 "footer bar" "No footer/inverter detail line found in index.html"
fi

# ---------------------------------------------------------------------------
# BLOCK 10: Analytics page elements (in analytics.html)
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 10: Analytics page elements ==="

if [ -f "app/web/templates/analytics.html" ]; then
    # [15] Date range buttons
    if grep -q "dm-range-btn\|dm-daterange\|date.*range\|6H\|12H\|24H\|7D\|30D" app/web/templates/analytics.html 2>/dev/null; then
        assert_pass 15 "Analytics date range buttons present"
    else
        assert_fail 15 "date range" "No date range buttons found in analytics.html"
    fi

    # [16] Chart containers
    if grep -q "dm-chart-wrap\|canvas.*id=\|Chart\|chart-container" app/web/templates/analytics.html 2>/dev/null; then
        assert_pass 16 "Analytics chart containers present"
    else
        assert_fail 16 "chart containers" "No chart containers in analytics.html"
    fi

    # [17] Summary strip
    if grep -q "dm-summary-row\|dm-summary-card" app/web/templates/analytics.html 2>/dev/null; then
        assert_pass 17 "Analytics summary strip present"
    else
        assert_fail 17 "summary strip" "No summary strip in analytics.html"
    fi

    # [18] Events table
    if grep -q "dm-table\|Events\|events-table" app/web/templates/analytics.html 2>/dev/null; then
        assert_pass 18 "Analytics events table present"
    else
        assert_fail 18 "events table" "No events table in analytics.html"
    fi

    # [19] Analytics nav active
    if grep -q "Analytics" app/web/templates/analytics.html 2>/dev/null; then
        assert_pass 19 "Analytics nav present in analytics.html"
    else
        assert_fail 19 "analytics nav" "Analytics nav missing in analytics.html"
    fi
else
    assert_fail 15 "analytics.html" "File does not exist — skipping analytics checks"
    assert_fail 16 "analytics.html" "File does not exist — skipping chart check"
    assert_fail 17 "analytics.html" "File does not exist — skipping summary check"
    assert_fail 18 "analytics.html" "File does not exist — skipping events check"
    assert_fail 19 "analytics.html" "File does not exist — skipping nav check"
fi

# ---------------------------------------------------------------------------
# BLOCK 11: External URL / CDN safety
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 11: External URL and CDN safety ==="

# [20] No external URLs in dashboard HTML
EXTERNAL_COUNT=$(grep -cE 'https?://' app/web/templates/index.html 2>/dev/null || echo 0)
EXTERNAL_COUNT=$(echo "$EXTERNAL_COUNT" | tr -d '[:space:]')
if [ -z "$EXTERNAL_COUNT" ] || [ "$EXTERNAL_COUNT" -eq 0 ] 2>/dev/null; then
    assert_pass 20 "No external URLs in index.html"
else
    assert_fail 20 "external URLs" "Found $EXTERNAL_COUNT external URLs in index.html"
fi

# [21] No CDN references in CSS
CDN_CSS=$(grep -cE 'cdn\.|jsdelivr|unpkg|cloudflare' app/web/static/dashboard.css 2>/dev/null || echo 0)
CDN_CSS=$(echo "$CDN_CSS" | tr -d '[:space:]')
if [ -z "$CDN_CSS" ] || [ "$CDN_CSS" -eq 0 ] 2>/dev/null; then
    assert_pass 21 "No CDN references in dashboard.css"
else
    assert_fail 21 "CDN in CSS" "Found $CDN_CSS CDN references in dashboard.css"
fi

# [22] No external URLs in dashboard.js
JS_EXTERNAL=$(grep -cE 'https?://' app/web/static/dashboard.js 2>/dev/null || echo 0)
JS_EXTERNAL=$(echo "$JS_EXTERNAL" | tr -d '[:space:]')
if [ -z "$JS_EXTERNAL" ] || [ "$JS_EXTERNAL" -eq 0 ] 2>/dev/null; then
    assert_pass 22 "No external URLs in dashboard.js"
else
    assert_fail 22 "external JS URLs" "Found $JS_EXTERNAL external URLs in dashboard.js"
fi

# [23] No Google Fonts references
FONTS_COUNT=$(grep -cE 'fonts\.googleapis|fonts\.gstatic' app/web/templates/index.html 2>/dev/null || echo 0)
FONTS_COUNT=$(echo "$FONTS_COUNT" | tr -d '[:space:]')
if [ -z "$FONTS_COUNT" ] || [ "$FONTS_COUNT" -eq 0 ] 2>/dev/null; then
    assert_pass 23 "No Google Fonts references in index.html"
else
    assert_fail 23 "Google Fonts" "Found $FONTS_COUNT Google Fonts references"
fi

# ---------------------------------------------------------------------------
# BLOCK 12: Safe DOM rendering (no innerHTML for untrusted runtime text)
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 12: Safe DOM rendering ==="

# [24] dashboard.js uses textContent predominantly
TEXTCONTENT_COUNT=$(grep -c "textContent" app/web/static/dashboard.js 2>/dev/null || echo 0)
INNERHTML_COUNT=$(grep -v '^\s*\*' app/web/static/dashboard.js 2>/dev/null | grep -v '^\s*//' | grep -c "innerHTML" 2>/dev/null || echo 0)

TEXTCONTENT_COUNT=$(echo "$TEXTCONTENT_COUNT" | tr -d '[:space:]')
INNERHTML_COUNT=$(echo "$INNERHTML_COUNT" | tr -d '[:space:]')
if [ -z "$TEXTCONTENT_COUNT" ]; then TEXTCONTENT_COUNT=0; fi
if [ -z "$INNERHTML_COUNT" ]; then INNERHTML_COUNT=0; fi

if [ "$TEXTCONTENT_COUNT" -gt 0 ] && [ "$INNERHTML_COUNT" -eq 0 ]; then
    assert_pass 24 "dashboard.js uses textContent, no innerHTML for runtime values"
else
    assert_fail 24 "safe DOM" "textContent=$TEXTCONTENT_COUNT, innerHTML=$INNERHTML_COUNT"
fi

# ---------------------------------------------------------------------------
# BLOCK 13: /control/state remains read-only
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 13: /control/state read-only verification ==="

# [25] No POST/PUT/PATCH/DELETE routes for /control/state in code
WRITE_ROUTES=0
for f in app/control/web_ui_read_endpoint.py app/web_host.py app/web_routes.py; do
    c=$(grep -cE '@router\.(post|put|patch|delete).*control.*state|@app\.(post|put|patch|delete).*control.*state' "$f" 2>/dev/null || echo 0)
    c=$(echo "$c" | tr -d '[:space:]')
    [ -n "$c" ] && WRITE_ROUTES=$((WRITE_ROUTES + c))
done
if [ "$WRITE_ROUTES" -eq 0 ]; then
    assert_pass 25 "/control/state has no write routes"
else
    assert_fail 25 "write routes" "Found $WRITE_ROUTES write routes for /control/state"
fi

# [26] web_routes.py or web_host.py does not contain control/command write logic
if grep -q "read-only" app/control/web_ui_read_endpoint.py 2>/dev/null; then
    assert_pass 26 "/control/state endpoint declares read-only"
else
    assert_fail 26 "read-only" "Endpoint does not declare read-only"
fi

# ---------------------------------------------------------------------------
# BLOCK 14: Dark theme presence
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 14: Dark theme ==="

# [27] Dark theme CSS variables in dashboard.css
if grep -qE -- '--bg|--surface|--border|--teal|--text|dark' app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 27 "Dark theme CSS variables present in dashboard.css"
else
    assert_fail 27 "dark theme" "No dark theme CSS variables in dashboard.css"
fi

# ---------------------------------------------------------------------------
# BLOCK 15: Python compilation check
# ---------------------------------------------------------------------------
echo ""
echo "=== BLOCK 15: Python compilation ==="

# [28] All Python files compile
if python3 -m compileall -q app run.py 2>/dev/null; then
    assert_pass 28 "All Python files compile cleanly"
else
    assert_fail 28 "compilation" "Python compilation errors found"
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
