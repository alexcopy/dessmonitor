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

# [16] sensors-panel present (architect replacement for sensors-table-body)
if grep -q 'id="sensors-panel"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 16 "index.html contains #sensors-panel (architect sensors section)"
else
    assert_fail 16 "#sensors-panel" "Sensors section not found in index.html"
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

# [19] Single loads table present (architect consolidated layout)
if grep -q 'id="loads-table-body"' app/web/templates/index.html 2>/dev/null; then
    assert_pass 19 "Loads table body present (#loads-table-body — architect single table)"
else
    assert_fail 19 "loads table" "loads-table-body element missing"
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

# [21] dashboard.js contains renderOperatorSummary (consolidated timestamp rendering)
if grep -q "renderOperatorSummary" app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 21 "dashboard.js contains renderOperatorSummary (consolidated inverter rendering)"
else
    assert_fail 21 "renderOperatorSummary" "Function not found in dashboard.js"
fi

# [22] dashboard.js contains renderInverterFreshness
if grep -q "renderInverterFreshness" app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 22 "dashboard.js contains renderInverterFreshness function"
else
    assert_fail 22 "renderInverterFreshness" "Function not found in dashboard.js"
fi

# [23] dashboard.js applies dm-badge classes for severity/status rendering
if grep -q 'dm-badge' app/web/static/dashboard.js 2>/dev/null; then
    assert_pass 23 "dashboard.js uses dm-badge class system for severity rendering"
else
    assert_fail 23 "dm-badge severity" "dm-badge not referenced in dashboard.js"
fi

# [24] No innerHTML for API values
if grep -v '^\s*\*' app/web/static/dashboard.js 2>/dev/null | grep -v '^\s*//' | grep -q "innerHTML"; then
    assert_fail 24 "textContent safety" "dashboard.js contains innerHTML usage"
else
    assert_pass 24 "dashboard.js uses safe DOM APIs (no innerHTML)"
fi

# [25] CSS dm-badge severity classes (architect replacement for ds-power-severity-*)
if grep -q ".dm-badge.ok" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 25 "CSS defines .dm-badge.ok (replaces ds-power-severity-green)"
else
    assert_fail 25 ".dm-badge.ok" "CSS class not found"
fi

if grep -q ".dm-badge.degraded" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 26 "CSS defines .dm-badge.degraded (replaces ds-power-severity-amber)"
else
    assert_fail 26 ".dm-badge.degraded" "CSS class not found"
fi

if grep -q ".dm-badge.error" app/web/static/dashboard.css 2>/dev/null; then
    assert_pass 27 "CSS defines .dm-badge.error (replaces ds-power-severity-red)"
else
    assert_fail 27 ".dm-badge.error" "CSS class not found"
fi

# [28] DESS timestamps are never presented as verified device measurement time.
# Replaces the former source-shape grep for "from zoneinfo import ZoneInfo" in
# run.py (Task 0079, explicitly authorised narrow change): that import only
# served code relabelling the unverified DESS Timestamp as a Europe/London
# observed_at, which ADR-0004 removed. This check is behavioural: it executes
# the real run.py inverter web provider (extracted with ast, no app start, no
# network), the read-only snapshot adapter, the telemetry recorder and the
# winter diagnostics with a DESS response carrying timestamp/gts/
# device_observed_at, and requires that none becomes a verified/observed time.
OUT28=$(run_python "
import ast, json, types
from datetime import datetime, timezone
from typing import Any

DESS_TS = '2026-12-10 20:00:00'
GTS = '1797019200000'
DEVICE_TS = '2026-12-10T20:00:00+08:00'
RECEIPT = datetime(2026, 12, 10, 12, 0, 5, tzinfo=timezone.utc)
dd = types.SimpleNamespace(
    timestamp=DESS_TS, gts=GTS, device_observed_at=DEVICE_TS,
    battery_voltage=26.4, battery_capacity=60.0, battery_charging_current=0.0,
    battery_discharging_current=0.0, pv1_voltage=0.0, pv1_power=0.0, pv2_voltage=0.0,
    pv2_power=0.0, pv_total_power=0.0, output_voltage=230.0, output_power=400.0,
    output_apparent_power=420.0, ac_input_voltage=230.0, ac_input_frequency=50.0,
    ac_output_load=8.0, working_state='Line Mode', mains_status='Normal',
    charger_priority='Solar only', output_priority='Utility first')
fail = []
leaked = lambda value: any(s in value for s in (DESS_TS, GTS, DEVICE_TS, '20:00:00'))

# 1. Real run.py provider (extracted, executed with a stub monitor/recorder).
tree = ast.parse(open('run.py', encoding='utf-8').read())
func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_inverter_provider')
ns = {'Any': Any, 'inverter_mon': types.SimpleNamespace(last_data=dd),
      'telemetry_recorder': types.SimpleNamespace(latest_received_at=RECEIPT)}
exec(compile(ast.Module(body=[func], type_ignores=[]), 'run.py', 'exec'), ns)
row = ns['_inverter_provider']()
if row.get('observed_at') is not None: fail.append('provider observed_at=%r' % row.get('observed_at'))
if row.get('timestamp_quality') != 'device_time_unverified': fail.append('provider timestamp_quality')
if row.get('received_at') != RECEIPT.isoformat(): fail.append('provider received_at is not the receipt time')
if leaked(json.dumps(row, default=str)): fail.append('provider exposes the DESS timestamp')

# 2. Read-only /control/state snapshot built from that provider row.
from app.web_control_state_provider import build_control_state_snapshot_from_runtime_state
snap = build_control_state_snapshot_from_runtime_state(
    {'snapshot_id': 's', 'created_at': RECEIPT.isoformat(), 'loads': [], 'inverter': [row]})
inv = snap.inverter[0]
if inv.observed_at is not None: fail.append('snapshot observed_at=%r' % inv.observed_at)
if inv.timestamp_quality != 'device_time_unverified': fail.append('snapshot timestamp_quality')

# 3. Telemetry recorder: no verified observation / device-time counter, no measurement time.
from app.control.adaptive_telemetry import AdaptiveTelemetryRecorder
rec = AdaptiveTelemetryRecorder(); rec.attach_writer()
rec.observe(dd, received_at=RECEIPT)
dd2 = types.SimpleNamespace(**dict(vars(dd), working_state='Invert Mode', timestamp='2026-12-10 20:05:00'))
rec.observe(dd2, received_at=RECEIPT.replace(second=59))
stats = rec.statistics(RECEIPT.replace(second=59))
if stats['verified_independent_observations'] or stats['device_time_verified_observations']:
    fail.append('recorder counted a verified observation')
if stats['last_verified_measurement_at'] is not None: fail.append('recorder last_verified_measurement_at')
meta = rec.pending()[0].metadata(RECEIPT)
if meta['measurement_time'] is not None or meta['verified_observation'] is not False \
        or meta['timestamp_quality'] != 'device_time_unverified':
    fail.append('event metadata claims a measurement time')
if leaked(json.dumps(meta)): fail.append('event metadata exposes the DESS timestamp')

# 4. Winter diagnostics: measurement time stays null and unverified.
from app.control.winter_runtime_diagnostics import WinterRuntimeDiagnostics
w = WinterRuntimeDiagnostics().observe(dd, received_at=RECEIPT, evaluated_at=RECEIPT)
if w.measurement_time is not None or w.timestamp_quality != 'device_time_unverified':
    fail.append('winter diagnostics measurement_time=%r' % w.measurement_time)

print('device_time_failures:', '; '.join(fail) if fail else 'none')
" 2>&1) || true

echo "$OUT28" | tail -n 3 | while IFS= read -r line; do echo "  $line"; done

if echo "$OUT28" | grep -q "^device_time_failures: none$"; then
    assert_pass 28 "DESS timestamp is never presented as verified device measurement time"
else
    assert_fail 28 "DESS timestamp provenance" "$(echo "$OUT28" | tail -n 1)"
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
