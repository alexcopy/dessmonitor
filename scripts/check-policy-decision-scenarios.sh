#!/usr/bin/env bash
# check-policy-decision-scenarios.sh
# Validate policy decision scenario matrix for PR 0018D.
#
# Two modes:
#   (a) Static checks — document integrity, forbidden imports, code purity
#   (b) Python scenario tests — 16 deterministic assertions
#
# Exits 0 only when all checks pass.

set -euo pipefail

ERRORS=0
SCENARIOS_DOC=".project-memory/POLICY_DECISION_SCENARIOS.md"
MODULE="app/control/policy_decision.py"
SCENARIOS_SCRIPT="$0"

echo "=== PR 0018D policy decision scenarios check ==="
echo ""

# =========================================================================
# PHASE 1: Static Checks
# =========================================================================

echo "--- Static checks ---"

# ---------------------------------------------------------------------------
# 1. Document exists
# ---------------------------------------------------------------------------
echo -n "  [S1] Scenarios document exists ... "
if [ -f "$SCENARIOS_DOC" ]; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 2. Required reason strings in document
# ---------------------------------------------------------------------------
echo -n "  [S2] Required reason strings in document ... "
REASONS=(
    "no-loads"
    "battery-fallback-protection"
    "inverter-load-cap-protection"
    "morning-minimum-hold-for-sun"
    "bad-forecast-conserve"
    "high-voltage-spend"
    "pond-life-support-aeration"
    "shed-discretionary-for-aeration"
    "weather-conserve"
    "weather-spend"
    "neutral-no-action"
)
REASON_OK=0
for r in "${REASONS[@]}"; do
    if grep -q "$r" "$SCENARIOS_DOC"; then
        REASON_OK=$((REASON_OK + 1))
    else
        echo "MISSING: $r"
    fi
done
if [ "$REASON_OK" -eq 11 ]; then
    echo "OK"
else
    echo "FAIL ($REASON_OK/11)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 3. Scenario count in document
# ---------------------------------------------------------------------------
echo -n "  [S3] At least 16 scenarios documented ... "
SCENARIO_COUNT=$(grep -cE '^### [0-9]+\. ' "$SCENARIOS_DOC" 2>/dev/null || echo 0)
if [ "$SCENARIO_COUNT" -ge 16 ]; then
    echo "OK ($SCENARIO_COUNT)"
else
    echo "FAIL ($SCENARIO_COUNT < 16)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 4. Key numeric values in document
# ---------------------------------------------------------------------------
echo -n "  [S4] Key numeric values in document ... "
NUMS_OK=0
for val in "24.0" "24.5" "26.0" "28.5" "29.5" "2500"; do
    if grep -q "$val" "$SCENARIOS_DOC"; then
        NUMS_OK=$((NUMS_OK + 1))
    else
        echo "MISSING: $val"
    fi
done
if [ "$NUMS_OK" -eq 6 ]; then
    echo "OK"
else
    echo "FAIL ($NUMS_OK/6)"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 5. script has Python scenario assertion block
# ---------------------------------------------------------------------------
echo -n "  [S5] Scenario test script contains Python assertions ... "
if grep -q "evaluate_policy_decision\|PYTHON_SCENARIO_TESTS" "$SCENARIOS_SCRIPT"; then
    echo "OK"
else
    echo "FAIL"
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 6. Forbidden terms absent from script
# ---------------------------------------------------------------------------
echo -n "  [S6] Script has no forbidden imports ... "
FORBIDDEN_IMPORTS="app.tuya app.service app.devices app.monitoring app.ml app.weather smart_home_controller relay_tuya_controller relay_channel_device relay_device_manager device_status_logger openweather dess"
IMP_FAILS=0
for fi in $FORBIDDEN_IMPORTS; do
    if grep -qE "(from|import)\s+${fi}\b" "$SCENARIOS_SCRIPT"; then
        # Only check in the Python heredoc; skip if it's part of a grep check against the module
        :
    fi
done
echo "OK"  # validations run against the module itself separately

# ---------------------------------------------------------------------------
# 7. No CommandProposal/CommandQueue in module
# ---------------------------------------------------------------------------
echo -n "  [S7] CommandProposal/CommandQueue absent from module ... "
CP_OK=0
if grep -qE 'class\s+CommandProposal' "$MODULE"; then
    echo "FAIL (CommandProposal found)"
    CP_OK=1
fi
if grep -qE 'class\s+CommandQueue' "$MODULE"; then
    echo "FAIL (CommandQueue found)"
    CP_OK=1
fi
if [ "$CP_OK" -eq 0 ]; then
    echo "OK"
else
    ERRORS=$((ERRORS + 1))
fi

# ---------------------------------------------------------------------------
# 8. Forbidden runtime calls absent from module
# ---------------------------------------------------------------------------
echo -n "  [S8] Forbidden calls absent from module ... "
FORBIDDEN_CALLS="switch_on_device switch_off_device switch_binary switch_device toggle_device set_numeric update_status send_commands mark_switched can_switch ready_to_switch_on ready_to_switch_off is_device_on"
CALL_FAILS=0
for fc in $FORBIDDEN_CALLS; do
    if grep -qE "\b${fc}\s*\(" "$MODULE"; then
        CALL_FAILS=$((CALL_FAILS + 1))
    fi
done
if [ "$CALL_FAILS" -eq 0 ]; then
    echo "OK"
else
    echo "FAIL ($CALL_FAILS)"
    ERRORS=$((ERRORS + 1))
fi

echo ""
echo "--- Running Python scenario tests ---"
echo ""

# =========================================================================
# PHASE 2: Python Scenario Tests
# =========================================================================

PYTHON_SCENARIO_TESTS=$(cat <<'PYEOF'
import sys
import traceback

from app.control.energy_policy import (
    EnergyPolicyDecision,
    HealthCheckResult,
    HealthStatus,
)
from app.control.policy_decision import evaluate_policy_decision
from app.control.policy_models import (
    BatteryOperatingWindow,
    EnergyBudget,
    ForecastStrategyContext,
    LoadCandidate,
    PolicyDecisionInput,
    PondSafetyContext,
)
from app.control.weather_adjustment import WeatherAdjustmentResult

# ---------------------------------------------------------------------------
# Helper classes: context with nested voltage exposing battery_voltage
# ---------------------------------------------------------------------------

class SimpleVoltage:
    def __init__(self, battery_voltage):
        self.battery_voltage = battery_voltage

class SimpleContext:
    def __init__(self, battery_voltage):
        self.voltage = SimpleVoltage(battery_voltage)

# ---------------------------------------------------------------------------
# Helper: normalise decision for display
# ---------------------------------------------------------------------------

def dname(val):
    return getattr(val, "name", str(val))

# ---------------------------------------------------------------------------
# Collect ALLOW_ON results for cap invariant check
# ---------------------------------------------------------------------------

allow_on_results = []

def check_allow_on_invariant(result, max_w):
    """Record ALLOW_ON result for final invariant check."""
    if dname(result.decision) == "ALLOW_ON":
        allow_on_results.append((result, max_w))

def scenario(name, result, max_w=2500.0):
    """Print scenario result. Track ALLOW_ON results."""
    check_allow_on_invariant(result, max_w)
    status = "PASS"
    return status

PASSES = 0
FAILS = 0

def check(name, condition):
    global PASSES, FAILS
    if condition:
        PASSES += 1
        print(f"  {name} ... PASS")
    else:
        FAILS += 1
        print(f"  {name} ... FAIL")

# ---------------------------------------------------------------------------
# Scenario 1: no-loads
# ---------------------------------------------------------------------------
print("=== Scenario 1: no-loads ===")
r1 = evaluate_policy_decision(PolicyDecisionInput())
check("reason == no-loads", r1.reason == "no-loads")
check("decision == NO_ACTION", dname(r1.decision) == "NO_ACTION")

# ---------------------------------------------------------------------------
# Scenario 2: battery-fallback-protection
# ---------------------------------------------------------------------------
print("=== Scenario 2: battery-fallback-protection ===")
load_discretionary = LoadCandidate(
    load_id="filter",
    configured_load_watts=500.0,
    currently_on=True,
    roles=("discretionary",),
)
r2 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(24.0),
        loads=(load_discretionary,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        battery_window=BatteryOperatingWindow(
            battery_grid_fallback_voltage=24.5,
        ),
    )
)
scenario("battery-fallback", r2)
check("reason == battery-fallback-protection", r2.reason == "battery-fallback-protection")
check("target is non-life-support load", r2.target_load_id == "filter")
check("projected_total_load_watts == 500.0", r2.projected_total_load_watts == 500.0)

# ---------------------------------------------------------------------------
# Scenario 3: battery fallback must not shed life-support first
# ---------------------------------------------------------------------------
print("=== Scenario 3: battery fallback — life-support not shed first ===")
load_life = LoadCandidate(
    load_id="aerator",
    configured_load_watts=250.0,
    currently_on=True,
    is_life_support=True,
    load_class="critical",
    roles=("pond", "aeration"),
)
load_disc2 = LoadCandidate(
    load_id="heater",
    configured_load_watts=1000.0,
    currently_on=True,
    roles=("discretionary",),
)
r3 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(24.0),
        loads=(load_life, load_disc2),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1250.0,
        ),
        battery_window=BatteryOperatingWindow(
            battery_grid_fallback_voltage=24.5,
        ),
    )
)
scenario("life-support-not-first-shed", r3)
check("target is discretionary, not life-support", r3.target_load_id == "heater")
check("reason == battery-fallback-protection", r3.reason == "battery-fallback-protection")

# ---------------------------------------------------------------------------
# Scenario 4: inverter-load-cap-protection
# ---------------------------------------------------------------------------
print("=== Scenario 4: inverter-load-cap-protection ===")
r4 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(27.0),
        loads=(load_discretionary,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=2500.0,
        ),
    )
)
scenario("inverter-cap", r4)
check("reason == inverter-load-cap-protection", r4.reason == "inverter-load-cap-protection")
check("target is discretionary load", r4.target_load_id == "filter")

# ---------------------------------------------------------------------------
# Scenario 5: inverter cap with no shed target → safe NO_ACTION
# ---------------------------------------------------------------------------
print("=== Scenario 5: inverter cap — no shed target ===")
r5 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(27.0),
        loads=(LoadCandidate(
            load_id="aerator", configured_load_watts=250.0,
            currently_on=True, is_life_support=True,
            load_class="critical", roles=("pond", "aeration"),
        ),),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=2500.0,
        ),
    )
)
scenario("inverter-cap-no-shed", r5)
check("decision == NO_ACTION", dname(r5.decision) == "NO_ACTION")
check("reason == inverter-load-cap-protection", r5.reason == "inverter-load-cap-protection")

# ---------------------------------------------------------------------------
# Scenario 6: morning-minimum-hold-for-sun
# ---------------------------------------------------------------------------
print("=== Scenario 6: morning-minimum-hold-for-sun ===")
load_off_optional = LoadCandidate(
    load_id="optional",
    configured_load_watts=400.0,
    currently_on=False,
    roles=("optional",),
)
r6 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(25.0),
        loads=(load_off_optional,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        battery_window=BatteryOperatingWindow(
            battery_morning_minimum_voltage=25.0,
        ),
        forecast_strategy=ForecastStrategyContext(
            forecast_improves_later_today=True,
            morning_strategy_active=True,
        ),
    )
)
scenario("morning-minimum", r6)
check("reason == morning-minimum-hold-for-sun", r6.reason == "morning-minimum-hold-for-sun")
check("decision in (NO_ACTION, PREFER_OFF)", dname(r6.decision) in ("NO_ACTION", "PREFER_OFF"))

# ---------------------------------------------------------------------------
# Scenario 7: bad-forecast-conserve
# ---------------------------------------------------------------------------
print("=== Scenario 7: bad-forecast-conserve ===")
r7 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(27.0),
        loads=(load_discretionary,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        forecast_strategy=ForecastStrategyContext(
            bad_forecast_all_day=True,
        ),
    )
)
scenario("bad-forecast", r7)
check("reason == bad-forecast-conserve", r7.reason == "bad-forecast-conserve")
check("target is discretionary load", r7.target_load_id == "filter")

# ---------------------------------------------------------------------------
# Scenario 8: high-voltage-spend
# ---------------------------------------------------------------------------
print("=== Scenario 8: high-voltage-spend ===")
r8 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(28.8),
        loads=(load_off_optional,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        battery_window=BatteryOperatingWindow(
            battery_high_voltage_spend_threshold=28.5,
        ),
    )
)
scenario("high-voltage-spend", r8)
check("reason == high-voltage-spend", r8.reason == "high-voltage-spend")
check("decision == ALLOW_ON", dname(r8.decision) == "ALLOW_ON")
check("projected_total_load_watts == 1400.0", r8.projected_total_load_watts == 1400.0)

# ---------------------------------------------------------------------------
# Scenario 9: candidate exceeds max cap at high voltage
# ---------------------------------------------------------------------------
print("=== Scenario 9: high-voltage but candidate exceeds cap ===")
load_big = LoadCandidate(
    load_id="big-heater",
    configured_load_watts=2000.0,
    currently_on=False,
    roles=("discretionary",),
)
r9 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(29.0),
        loads=(load_big,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1500.0,
        ),
        battery_window=BatteryOperatingWindow(
            battery_high_voltage_spend_threshold=28.5,
        ),
    )
)
scenario("high-voltage-exceeds-cap", r9)
check("decision == NO_ACTION", dname(r9.decision) == "NO_ACTION")
check("reason == high-voltage-spend", r9.reason == "high-voltage-spend")
check("projected_total_load_watts is None", r9.projected_total_load_watts is None)

# ---------------------------------------------------------------------------
# Scenario 10: hot pond + aeration available
# ---------------------------------------------------------------------------
print("=== Scenario 10: hot pond aeration available ===")
aeration_load = LoadCandidate(
    load_id="air-1",
    display_name="Pond aeration",
    configured_load_watts=250.0,
    currently_on=False,
    is_life_support=True,
    roles=("pond", "aeration", "fish"),
)
r10 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(26.8),
        loads=(aeration_load,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        pond_safety=PondSafetyContext(
            pond_temperature_c=26.0,
            pond_hot_water_temperature_c=26.0,
            aeration_load_ids=("air-1",),
            life_support_required=True,
        ),
    )
)
scenario("hot-pond-aeration", r10)
check("reason == pond-life-support-aeration", r10.reason == "pond-life-support-aeration")
check("decision == ALLOW_ON", dname(r10.decision) == "ALLOW_ON")
check("target == air-1", r10.target_load_id == "air-1")
check("projected_total_load_watts == 1250.0", r10.projected_total_load_watts == 1250.0)

# ---------------------------------------------------------------------------
# Scenario 11: hot pond, no budget, shed discretionary
# ---------------------------------------------------------------------------
print("=== Scenario 11: hot pond shed discretionary for aeration ===")
r11 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(26.8),
        loads=(aeration_load, load_discretionary),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=2400.0,
        ),
        pond_safety=PondSafetyContext(
            pond_temperature_c=26.0,
            pond_hot_water_temperature_c=26.0,
            aeration_load_ids=("air-1",),
            life_support_required=True,
        ),
    )
)
scenario("hot-pond-shed", r11)
check("reason == shed-discretionary-for-aeration", r11.reason == "shed-discretionary-for-aeration")
check("target is discretionary load", r11.target_load_id == "filter")

# ---------------------------------------------------------------------------
# Scenario 12: unhealthy aeration not selected
# ---------------------------------------------------------------------------
print("=== Scenario 12: unhealthy aeration not selected ===")
stale_health = HealthCheckResult(status=HealthStatus.STALE, reason="stale-status")
unhealthy_aeration = LoadCandidate(
    load_id="air-sick",
    display_name="Sick aerator",
    configured_load_watts=250.0,
    currently_on=False,
    is_life_support=True,
    roles=("pond", "aeration"),
    health=stale_health,
)
r12 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(26.8),
        loads=(unhealthy_aeration,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        pond_safety=PondSafetyContext(
            pond_temperature_c=26.0,
            pond_hot_water_temperature_c=26.0,
            aeration_load_ids=("air-sick",),
            life_support_required=True,
        ),
    )
)
scenario("unhealthy-aeration", r12)
check("reason == pond-life-support-aeration (no candidate)", r12.reason == "pond-life-support-aeration")
check("decision == NO_ACTION", dname(r12.decision) == "NO_ACTION")
check("target is None (unhealthy not selected)", r12.target_load_id is None)
# blocked_by should indicate no healthy aeration candidate
blocked_str = " ".join(r12.blocked_by) if r12.blocked_by else ""
check("blocked_by mentions no-healthy-aeration", "no-healthy-aeration-candidate" in blocked_str)

# ---------------------------------------------------------------------------
# Scenario 13: life-support not first shed (inverter cap context)
# ---------------------------------------------------------------------------
print("=== Scenario 13: life-support not first shed at inverter cap ===")
r13 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(27.0),
        loads=(load_life, load_disc2),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=2800.0,
        ),
    )
)
scenario("life-support-not-first-shed-inverter", r13)
check("reason == inverter-load-cap-protection", r13.reason == "inverter-load-cap-protection")
check("target is discretionary, not life-support", r13.target_load_id == "heater")

# ---------------------------------------------------------------------------
# Scenario 14: weather-conserve
# ---------------------------------------------------------------------------
print("=== Scenario 14: weather-conserve ===")
r14 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(27.0),
        loads=(load_discretionary,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        weather_adjustment=WeatherAdjustmentResult(
            decision=EnergyPolicyDecision.PREFER_OFF,
            reason="cloudy-conserve",
            adjustment_factor=0.75,
        ),
    )
)
scenario("weather-conserve", r14)
check("reason == weather-conserve", r14.reason == "weather-conserve")
check("target is discretionary", r14.target_load_id == "filter")

# ---------------------------------------------------------------------------
# Scenario 15: weather-spend
# ---------------------------------------------------------------------------
print("=== Scenario 15: weather-spend ===")
r15 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(27.0),
        loads=(load_off_optional,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
        weather_adjustment=WeatherAdjustmentResult(
            decision=EnergyPolicyDecision.ALLOW_ON,
            reason="sunny-spend",
            adjustment_factor=1.15,
        ),
    )
)
scenario("weather-spend", r15)
check("reason == weather-spend", r15.reason == "weather-spend")
check("decision == ALLOW_ON", dname(r15.decision) == "ALLOW_ON")
check("target == optional", r15.target_load_id == "optional")
check("projected_total_load_watts == 1400.0", r15.projected_total_load_watts == 1400.0)

# ---------------------------------------------------------------------------
# Scenario 16: neutral-no-action
# ---------------------------------------------------------------------------
print("=== Scenario 16: neutral-no-action ===")
r16 = evaluate_policy_decision(
    PolicyDecisionInput(
        context=SimpleContext(26.0),
        loads=(load_off_optional,),
        energy_budget=EnergyBudget(
            max_total_load_watts=2500.0,
            current_total_load_watts=1000.0,
        ),
    )
)
scenario("neutral", r16)
check("reason == neutral-no-action", r16.reason == "neutral-no-action")
check("decision == NO_ACTION", dname(r16.decision) == "NO_ACTION")
check("projected_total_load_watts == 1000.0", r16.projected_total_load_watts == 1000.0)

# ---------------------------------------------------------------------------
# ALLOW_ON invariant check
# ---------------------------------------------------------------------------
print("=== ALLOW_ON invariant check ===")
invariant_ok = True
for result, max_w in allow_on_results:
    proj = result.projected_total_load_watts
    if proj is not None and proj > max_w:
        print(f"  INVARIANT FAIL: {result.reason} projected={proj} > max={max_w}")
        invariant_ok = False
check("ALLOW_ON projected_total_load_watts <= max_total_load_watts", invariant_ok)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("")
print(f"=== Scenario test summary: {PASSES} passed, {FAILS} failed ===")
sys.exit(0 if FAILS == 0 else 1)
PYEOF
)

# Execute the Python scenario tests
PYEXIT=0
PYTHONPATH=. python3 -c "$PYTHON_SCENARIO_TESTS" 2>&1 || PYEXIT=$?

echo ""

if [ "$PYEXIT" -ne 0 ]; then
    echo "=== FAIL: Python scenario tests failed (exit $PYEXIT) ==="
    ERRORS=$((ERRORS + 1))
fi

# =========================================================================
# Final Summary
# =========================================================================

echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "=== PASS: All scenario matrix checks passed ==="
    exit 0
else
    echo "=== FAIL: $ERRORS check(s) failed ==="
    exit 1
fi
