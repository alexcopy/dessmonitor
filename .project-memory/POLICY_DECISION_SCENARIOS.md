# Policy Decision Scenario Matrix

This document records 16 real-world operating scenarios for the pure deterministic
policy decision engine (`evaluate_policy_decision` in `app/control/policy_decision.py`).
Each scenario documents the input condition, expected reason string, expected decision
class, safety boundary, and why the scenario matters for live operation.

These scenarios lock the behavior implemented in PR 0018C. They are validated by
`scripts/check-policy-decision-scenarios.sh`.

## Scenario Reference Table

| # | Scenario | Key Input Condition | Expected Reason | Expected Decision |
|---|---|---|---|---|
| 1 | No loads | Empty loads tuple | `no-loads` | `NO_ACTION` |
| 2 | Battery near 24.0–24.5V fallback threshold | Voltage <= `battery_grid_fallback_voltage`, discretionary ON load available | `battery-fallback-protection` | `FORCE_OFF` |
| 3 | Inverter current load at/above 2500W | `current_total_load_watts` >= `max_total_load_watts`, discretionary ON load available | `inverter-load-cap-protection` | `FORCE_OFF` |
| 4 | Cloudy morning, sunny forecast in 2 hours | `morning_strategy_active`=true, `forecast_improves_later_today`=true, voltage <= `battery_morning_minimum_voltage` | `morning-minimum-hold-for-sun` | `PREFER_OFF` or `NO_ACTION` |
| 5 | Cloudy morning, bad all-day forecast | `bad_forecast_all_day`=true, discretionary ON load available | `bad-forecast-conserve` | `PREFER_OFF` |
| 6 | Sunny day, battery at or above 28.5V | Voltage >= `battery_high_voltage_spend_threshold`, healthy/ready OFF load fits budget | `high-voltage-spend` | `ALLOW_ON` |
| 7 | Battery near full at 29.5V, optional loads available | Voltage >= `battery_high_voltage_spend_threshold`, multiple OFF candidates fit budget | `high-voltage-spend` | `ALLOW_ON` |
| 8 | Candidate load would exceed `max_total_load_watts` | Current + candidate watts > `max_total_load_watts` | `high-voltage-spend` or `neutral-no-action` | `NO_ACTION` |
| 9 | Pond temperature 26°C, aeration OFF, budget available | `life_support_active`=true, healthy aeration OFF load fits budget | `pond-life-support-aeration` | `ALLOW_ON` |
| 10 | Pond temperature 26°C, aeration OFF, budget full | `life_support_active`=true, aeration candidate exists but budget full, discretionary ON available | `shed-discretionary-for-aeration` | `FORCE_OFF` |
| 11 | Pond temperature high, discretionary ON, aeration needs budget | `life_support_active`=true, discretionary ON load can be shed to free budget for aeration | `shed-discretionary-for-aeration` | `FORCE_OFF` |
| 12 | Pond aeration device unhealthy/stale/unreachable | `life_support_active`=true, aeration load has health=STALE/MISMATCH/UNREACHABLE | `pond-life-support-aeration` (no candidate) or fallthrough | `NO_ACTION` |
| 13 | Weather conserve adjustment | `weather_adjustment.decision` = `PREFER_OFF` or `FORCE_OFF`, discretionary shed target exists | `weather-conserve` | `PREFER_OFF` |
| 14 | Weather spend adjustment | `weather_adjustment.decision` = `ALLOW_ON`, healthy/ready OFF load fits budget | `weather-spend` | `ALLOW_ON` |
| 15 | Weather unknown / neutral behavior | `weather_adjustment`=None or `decision`=`NO_ACTION`, no other rule triggers | `neutral-no-action` | `NO_ACTION` |
| 16 | Life-support load must not be first shed target | Battery fallback or inverter cap active, life-support ON load plus discretionary ON load present | `battery-fallback-protection` or `inverter-load-cap-protection` | `FORCE_OFF` on discretionary |

---

## Detailed Scenarios

### 1. No Loads

- **Input**: Empty `loads` tuple.
- **Expected Reason**: `no-loads`
- **Expected Decision**: `NO_ACTION`
- **Safety Boundary**: Engine must not crash or return a target when no loads exist.
- **Why This Matters**: The scheduler may call the engine before any loads are registered.
  It must handle the empty case gracefully without side effects.

### 2. Battery Near 24.0–24.5V Fallback Threshold

- **Input**: Battery voltage 24.0V, `battery_grid_fallback_voltage`=24.5V, one discretionary
  load currently ON (e.g. filter pump at 500W).
- **Expected Reason**: `battery-fallback-protection`
- **Expected Decision**: `FORCE_OFF` targeting the discretionary load
- **Safety Boundary**: Critical/life-support loads must not be selected as the first shed
  target.
- **Why This Matters**: The inverter switches to grid/mains when voltage drops below
  fallback. The engine must aggressively shed discretionary loads to avoid grid consumption.

### 3. Inverter Current Load At or Above 2500W

- **Input**: `current_total_load_watts`=2500W, `max_total_load_watts`=2500W, one discretionary
  load currently ON (e.g. heater at 1000W).
- **Expected Reason**: `inverter-load-cap-protection`
- **Expected Decision**: `FORCE_OFF` targeting the discretionary load
- **Safety Boundary**: Engine must never propose total active load exceeding `max_total_load_watts`.
  If no safe shed target exists, return `NO_ACTION`.
- **Why This Matters**: Exceeding the inverter's rated capacity risks tripping breakers
  or damaging hardware. The engine must protect the inverter as the primary safety gate.

### 4. Cloudy Morning, Sunny Forecast in 2 Hours

- **Input**: Battery voltage 25.0V, `battery_morning_minimum_voltage`=25.0V,
  `morning_strategy_active`=true, `forecast_improves_later_today`=true.
- **Expected Reason**: `morning-minimum-hold-for-sun`
- **Expected Decision**: `PREFER_OFF` or `NO_ACTION`
- **Safety Boundary**: Morning minimum must not prevent life-support aeration (handled by
  higher-priority pond check).
- **Why This Matters**: With improving forecast, the engine should preserve battery near
  morning minimum rather than over-conserving. It knows solar charging is coming.

### 5. Cloudy Morning, Bad All-Day Forecast

- **Input**: `bad_forecast_all_day`=true, one discretionary load currently ON.
- **Expected Reason**: `bad-forecast-conserve`
- **Expected Decision**: `PREFER_OFF` targeting discretionary load
- **Safety Boundary**: Life-support aeration remains protected. The engine sheds only
  discretionary loads.
- **Why This Matters**: With no solar generation expected all day, the engine must
  aggressively conserve by shedding discretionary loads early.

### 6. Sunny Day, Battery At or Above 28.5V

- **Input**: Battery voltage 28.8V, `battery_high_voltage_spend_threshold`=28.5V,
  one OFF healthy/ready discretionary load (400W) that fits budget.
- **Expected Reason**: `high-voltage-spend`
- **Expected Decision**: `ALLOW_ON`
- **Safety Boundary**: `projected_total_load_watts` must not exceed `max_total_load_watts`.
  Load must be healthy and ready.
- **Why This Matters**: Excess solar energy should be spent rather than wasted. The
  engine activates discretionary loads when the battery is nearly full.

### 7. Battery Near Full at 29.5V, Optional Loads Available

- **Input**: Battery voltage 29.5V, `battery_high_voltage_spend_threshold`=28.5V,
  multiple OFF healthy/ready candidates available.
- **Expected Reason**: `high-voltage-spend`
- **Expected Decision**: `ALLOW_ON` on the best candidate (life-support first, then
  higher priority, then larger configured_load_watts).
- **Safety Boundary**: Same as scenario 6. Only the single best candidate is selected per
  evaluation cycle.
- **Why This Matters**: At full battery, charging stops and surplus solar must be spent
  or clipped. The engine prioritizes the most useful load.

### 8. Candidate Load Would Exceed max_total_load_watts

- **Input**: Battery voltage 29.0V (above spend threshold), OFF candidate with
  `configured_load_watts`=2000W, `current_total_load_watts`=1500W,
  `max_total_load_watts`=2500W.
- **Expected Reason**: `high-voltage-spend` or `neutral-no-action`
- **Expected Decision**: `NO_ACTION` (no candidate fits)
- **Safety Boundary**: `projected_total_load_watts` must remain within
  `max_total_load_watts`. Engine must never propose an ALLOW_ON that would exceed the cap.
- **Why This Matters**: The inverter cap is a hard limit. Even with surplus energy,
  the engine must not overload the inverter.

### 9. Pond Temperature 26°C, Aeration OFF, Budget Available

- **Input**: `pond_temperature_c`=26.0°C, `pond_hot_water_temperature_c`=26.0°C,
  `life_support_required`=true, one OFF healthy aeration load (250W) that fits budget.
- **Expected Reason**: `pond-life-support-aeration`
- **Expected Decision**: `ALLOW_ON`
- **Safety Boundary**: Aeration is life-support. The engine must activate it when water
  temperature reaches critical levels. Projected load must not exceed inverter cap.
- **Why This Matters**: Warm water holds less dissolved oxygen. Fish and biological
  filtration depend on aeration. This is a safety-critical scenario.

### 10. Pond Temperature 26°C, Aeration OFF, Budget Full

- **Input**: `pond_temperature_c`=26.0°C, `pond_hot_water_temperature_c`=26.0°C,
  aeration OFF, budget is full (`current_total_load_watts`=2500W, `max_total_load_watts`=2500W),
  one discretionary load currently ON.
- **Expected Reason**: `shed-discretionary-for-aeration`
- **Expected Decision**: `FORCE_OFF` targeting discretionary load
- **Safety Boundary**: Discretionary loads are shed before life-support aeration is
  compromised. Aeration must ultimately be activated when conditions demand it.
- **Why This Matters**: When the inverter is at capacity, the engine must make room
  for life-support aeration by shedding lower-priority discretionary loads.

### 11. Pond Temperature High, Discretionary ON, Aeration Needs Budget

- **Input**: Same as scenario 10 but with an explicit discretionary load that can be
  shed. Life-support is active, aeration candidate exists, budget is full.
- **Expected Reason**: `shed-discretionary-for-aeration`
- **Expected Decision**: `FORCE_OFF` targeting discretionary load
- **Safety Boundary**: The engine must shed lower-priority loads before denying aeration.
- **Why This Matters**: This is the common case: aeration is needed but the inverter is
  at capacity. The engine makes room for life-support.

### 12. Pond Aeration Device Unhealthy/Stale/Unreachable

- **Input**: `life_support_active`=true, aeration load has `HealthCheckResult` with
  status=STALE (or MISMATCH, UNREACHABLE).
- **Expected Reason**: Either `pond-life-support-aeration` with no target (no healthy
  candidate to activate) or fallthrough to a lower-priority rule.
- **Expected Decision**: `NO_ACTION` (with explanation)
- **Safety Boundary**: Unhealthy devices must not be blindly relied upon. The engine
  must not trigger repeated switching attempts on a broken device.
- **Why This Matters**: An unhealthy aeration device cannot be trusted to deliver
  oxygen. The engine must flag the situation rather than pretend aeration is working.

### 13. Weather Conserve Adjustment

- **Input**: `weather_adjustment.decision`=`PREFER_OFF` (from cloudy/rainy evaluation),
  one discretionary load currently ON.
- **Expected Reason**: `weather-conserve`
- **Expected Decision**: `PREFER_OFF` targeting discretionary load
- **Safety Boundary**: Weather adjustment is advisory and lower priority than battery
  fallback, inverter cap, and life-support aeration. Those higher-priority rules win first.
- **Why This Matters**: Weather-based conservation complements voltage-based rules.
  It ensures the engine reacts to forecast degradation even when voltage hasn't yet
  dropped.

### 14. Weather Spend Adjustment

- **Input**: `weather_adjustment.decision`=`ALLOW_ON` (from sunny evaluation),
  one OFF healthy/ready load that fits budget. No high-voltage condition present
  (voltage below spend threshold).
- **Expected Reason**: `weather-spend`
- **Expected Decision**: `ALLOW_ON`
- **Safety Boundary**: Same inverter cap constraints apply. Only healthy/ready loads
  are selected.
- **Why This Matters**: Good weather forecasts may justify spending before the battery
  is fully charged. The weather adjustment evaluator can recommend spending before
  the high-voltage threshold is reached.

### 15. Weather Unknown / Neutral Behavior

- **Input**: Normal voltage (26.0V), no life-support active, no forecast flags, no
  weather adjustment (`weather_adjustment`=None).
- **Expected Reason**: `neutral-no-action`
- **Expected Decision**: `NO_ACTION`
- **Safety Boundary**: Default behavior must be conservative — no action unless
  explicitly justified by a higher-priority rule.
- **Why This Matters**: The engine must have a well-defined fallback when no rule
  triggers. This prevents undefined behavior or spurious actions.

### 16. Life-Support Load Must Not Be First Shed Target

- **Input**: Battery at fallback voltage (24.0V, `battery_grid_fallback_voltage`=24.5V),
  one life-support aeration load currently ON, one discretionary load currently ON.
- **Expected Reason**: `battery-fallback-protection`
- **Expected Decision**: `FORCE_OFF` targeting the **discretionary** load, not the
  life-support load.
- **Safety Boundary**: This is the core invariant: life-support loads must never be
  the first shed target. Discretionary loads are shed first, then only if no
  discretionary loads remain might life-support be considered (and only in extreme
  conditions).
- **Why This Matters**: Pond aeration is life-support. Shedding it first could kill
  fish. The engine must always prefer shedding discretionary loads over life-support.

## ALLOW_ON Invariant

Every scenario that produces `ALLOW_ON` must satisfy:

```
projected_total_load_watts <= max_total_load_watts
```

This invariant is enforced programmatically by the scenario test script. The engine
must never propose a total load exceeding the inverter's rated capacity.

## Decision Priority Order

The engine evaluates conditions in this fixed priority order (first match wins):

1. `no-loads` — empty loads
2. `battery-fallback-protection` — battery at or below grid fallback
3. `inverter-load-cap-protection` — load at or above inverter capacity
4. `pond-life-support-aeration` / `shed-discretionary-for-aeration` — hot pond needs aeration
5. `morning-minimum-hold-for-sun` — morning minimum with improving forecast
6. `bad-forecast-conserve` — bad all-day forecast
7. `high-voltage-spend` — battery above spend threshold
8. `weather-conserve` / `weather-spend` — weather adjustment advisory
9. `neutral-no-action` — default fallback

## Safety Boundaries

1. Life-support loads must never be the first shed target.
2. Unhealthy/stale/unreachable loads must never be selected for ON.
3. `projected_total_load_watts` must never exceed `max_total_load_watts` for ALLOW_ON.
4. Empty loads must never crash the engine.
5. Missing context/voltage must be tolerated gracefully.
6. The engine is pure and deterministic — same input always produces the same output.
7. No hardware calls, runtime wiring, command proposal, or execution.
