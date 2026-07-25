#!/usr/bin/env bash
# check-output-power-out-w-fallback.sh
# Focused validator for PR 0039: Output Power out_w Fallback.
#
# Verifies that _parse_inverter falls back to the out_w field when
# output_power is absent or None.
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

echo "=== BLOCK 1: _parse_inverter output_power fallback ==="

# [1] output_power direct value is preserved
OUT1=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'output_power': 800.0, 'out_w': 736.0}]
result = _parse_inverter(data)
r = result[0]
print('op', r.output_power)
print('op_expected', 800.0)
print('match', r.output_power == 800.0)
" 2>&1) || { assert_fail 1 "direct value" "Python execution failed"; }

if echo "$OUT1" | grep -q "match True"; then
    assert_pass 1 "output_power direct value (800.0) preserved"
else
    assert_fail 1 "output_power direct" "Expected 800.0, got: $OUT1"
fi

# [2] out_w fallback when output_power is None
OUT2=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'output_power': None, 'out_w': 736.0}]
result = _parse_inverter(data)
r = result[0]
print('op', r.output_power)
print('op_is_none', r.output_power is None)
print('expected_736', r.output_power == 736.0)
" 2>&1) || { assert_fail 2 "fallback" "Python execution failed"; }

if echo "$OUT2" | grep -q "expected_736 True"; then
    assert_pass 2 "out_w fallback when output_power is None (736.0)"
else
    assert_fail 2 "out_w fallback" "Expected 736.0, got: $OUT2"
fi

# [3] output_power direct value wins when both present
OUT3=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'output_power': 800.0, 'out_w': 736.0}]
result = _parse_inverter(data)
r = result[0]
print('op', r.output_power)
print('wins', r.output_power == 800.0)
print('not_736', r.output_power != 736.0)
" 2>&1) || { assert_fail 3 "direct wins" "Python execution failed"; }

if echo "$OUT3" | grep -q "wins True" && echo "$OUT3" | grep -q "not_736 True"; then
    assert_pass 3 "output_power direct value wins when both fields present"
else
    assert_fail 3 "direct wins" "Expected 800.0 ≠ 736.0, got: $OUT3"
fi

# [4] both absent remains None
OUT4=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'battery_voltage': 25.7}]
result = _parse_inverter(data)
r = result[0]
print('op_is_none', r.output_power is None)
print('bv', r.battery_voltage)
" 2>&1) || { assert_fail 4 "both absent" "Python execution failed"; }

if echo "$OUT4" | grep -q "op_is_none True"; then
    assert_pass 4 "output_power remains None when both fields absent"
else
    assert_fail 4 "both absent" "Expected None, got: $OUT4"
fi

# [5] out_w absent, output_power None remains None
OUT5=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'output_power': None}]
result = _parse_inverter(data)
r = result[0]
print('op_is_none', r.output_power is None)
" 2>&1) || { assert_fail 5 "op None, ow absent" "Python execution failed"; }

if echo "$OUT5" | grep -q "op_is_none True"; then
    assert_pass 5 "output_power remains None when out_w is absent"
else
    assert_fail 5 "op None, ow absent" "Expected None, got: $OUT5"
fi

# [6] out_w present, output_power absent from dict entirely
OUT6=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'out_w': 736.0}]
result = _parse_inverter(data)
r = result[0]
print('op', r.output_power)
print('expected_736', r.output_power == 736.0)
" 2>&1) || { assert_fail 6 "output_power absent" "Python execution failed"; }

if echo "$OUT6" | grep -q "expected_736 True"; then
    assert_pass 6 "out_w used when output_power key is absent entirely"
else
    assert_fail 6 "output_power absent" "Expected 736.0, got: $OUT6"
fi

# [7] string numeric out_w parsed as float
OUT7=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'output_power': None, 'out_w': '736.5'}]
result = _parse_inverter(data)
r = result[0]
print('op', r.output_power)
print('is_float', isinstance(r.output_power, float))
print('val', r.output_power == 736.5)
" 2>&1) || { assert_fail 7 "string numeric" "Python execution failed"; }

if echo "$OUT7" | grep -q "is_float True" && echo "$OUT7" | grep -q "val True"; then
    assert_pass 7 "string numeric out_w parsed as float (736.5)"
else
    assert_fail 7 "string numeric" "Expected float 736.5, got: $OUT7"
fi

# [8] invalid out_w remains None
OUT8=$(run_python "
from app.control.runtime_snapshot_adapter import _parse_inverter

data = [{'output_power': None, 'out_w': 'bogus'}]
result = _parse_inverter(data)
r = result[0]
print('op_is_none', r.output_power is None)
" 2>&1) || { assert_fail 8 "invalid out_w" "Python execution failed"; }

if echo "$OUT8" | grep -q "op_is_none True"; then
    assert_pass 8 "invalid out_w string remains None (not coerced to 0)"
else
    assert_fail 8 "invalid out_w" "Expected None, got: $OUT8"
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
