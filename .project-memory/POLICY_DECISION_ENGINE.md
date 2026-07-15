# Policy Decision Engine Operating Boundaries

## Purpose

This document records the operating boundaries, product invariants, and scenario matrix
for the energy-aware policy decision engine **before any implementation begins**. It is
the reference contract for sub-PRs 0018B (models), 0018C (engine), and 0018D (tests).

The policy decision engine is the "brain" of the energy-aware control system. It composes
readiness (PR 0014), health (PR 0015), schedule (PR 0016), and weather adjustment (PR 0017)
signals into a unified decision for each device load - respecting battery extrema, inverter
limits, and pond/fish aeration life-support invariants.

## Relationship to PR 0014-0017

The engine ingests results from all four preceding evaluators:

| PR | Evaluator | What It Provides |
|---|---|---|
| 0014 | Readiness evaluator | `ReadinessResult` - is this load allowed to switch ON? |
| 0015 | Health evaluator | `HealthCheckResult` - is the observed device state healthy? |
| 0016 | Schedule profile model | `ScheduleProfile` / `LoadScheduleProfile` - what windows/intervals apply? |
| 0017 | Weather adjustment evaluator | `WeatherAdjustmentResult` - what adjustment factor and weather decision? |

Plus runtime data provided as input (not fetched directly by the engine):
- Battery voltage and trend from `VoltageSnapshot`
- Power source from `VoltageSnapshot.power_source`
- Active load watts from runtime state
- Pond temperature from telemetry

## Why 0018 Is Split into 0018A/0018B/0018C/0018D

PR 0018 is split into four safe sub-PRs to maintain reviewability and safety boundaries:

| Sub-PR | Title | Scope |
|---|---|---|
| **0018A** | Policy engine operating boundaries | **This PR** - documentation and validation of product invariants, battery/inverter extrema, pond life-support rules, forecast strategy, scenario matrix. No code. |
| 0018B | Passive policy engine models | Add `PolicyDecisionInput`, `PolicyDecisionResult`, `LoadCandidate`, `EnergyBudget`, `BatteryOperatingWindow`, `PondSafetyContext`, `ForecastStrategyContext` as frozen dataclasses. |
| 0018C | Pure deterministic decision engine | Implement `evaluate_policy_decision()` as a pure function with no execution. |
| 0018D | Scenario matrix and regression tests | Add scenario-based tests covering all 12 scenarios from this document. |

## Battery Operating Extrema

The engine must respect these configurable battery voltage boundaries:

### `battery_grid_fallback_voltage`
- **Typical range:** 24.0-24.5V
- **Configurable:** Yes
- Below this threshold the inverter falls back to grid/mains power. The engine must
  treat grid fallback as a strong conservation signal and minimize all discretionary
  load activation. The engine must not assume grid power means unlimited energy.

### `battery_morning_minimum_voltage`
- **Configurable:** Yes (defined by operator per season)
- Voltage floor for morning hours, accounting for forecast later in the day.
- Prevents over-discharge before solar generation starts.
- The engine uses this floor when evaluating whether to activate loads in the morning.

### `battery_evening_reserve_voltage`
- **Typical:** 26.5V
- **Configurable:** Yes
- Must protect this voltage after sunset to preserve overnight capacity.
- The engine should shed discretionary loads when voltage approaches this reserve.
- Evening check frequency should be higher (e.g., every 60s vs 300s during day).

### `battery_high_voltage_spend_threshold`
- **Typical:** 28.5-29.0V
- **Configurable:** Yes
- Above this threshold the battery is nearly full and surplus solar should be
  spent on discretionary loads rather than wasted.
- The engine should activate additional loads (within inverter capacity) when
  voltage exceeds this threshold.

### `battery_full_voltage`
- **Typical:** 29.5V
- **Configurable:** Yes (hardware limit)
- When battery reaches this voltage, charging stops; any remaining solar must
  be spent or clipped.
- The engine should maximize discretionary load activation at this voltage.

## Morning Minimum Strategy

The engine must be forecast-aware when making morning decisions:

### Overcast morning, sunny forecast in 2 hours
- Preserve battery near the **morning minimum** - do not over-conserve blindly, do not
  over-spend early.
- Delay discretionary load activation until solar charging begins or forecast improves.
- The engine should hold loads OFF unless critical, knowing recharging is coming soon.

### Cloudy morning, bad all-day forecast
- Conserve energy more aggressively.
- Minimize discretionary load activation.
- Protect evening reserve from morning draw-down.
- The engine should keep discretionary loads OFF and prioritize only life-support loads.

### General morning rule
- Morning strategy must consider forecast later in the day as a primary input.
- The engine must not make morning decisions in isolation from the afternoon forecast.

## Daytime High-Voltage Spend Strategy

The high-voltage spend strategy activates when surplus solar is available.
When the battery is rising and is well above the reserve threshold:

### Sunny day, battery rising toward 29.5V
- Spend surplus energy on discretionary loads to avoid wasting solar production.
- The engine should activate additional loads (within inverter capacity) when voltage
  exceeds `battery_high_voltage_spend_threshold` (28.5-29.0V).
- Priorities: active aeration loads first, then other discretionary loads by priority.

### High PV generation, multiple loads competing
- Aeration wins over low-priority discretionary loads.
- If load cap is tight, shed lower-priority discretionary loads first.
- Spend surplus on aeration first, then priority-ranked discretionary loads.

## Evening Reserve Strategy

After sunset, the engine must protect `battery_evening_reserve_voltage` (~26.5V):

- Discretionary loads should be shed if voltage is at or approaching the reserve threshold.
- Critical loads (pond aeration) may remain ON if voltage is above a lower safety floor,
  but aeration priority is weighed against reserve protection.
- Evening check frequency should be higher (e.g., every 60s vs 300s during day).
- The engine should apply tighter voltage thresholds for switch-ON in the evening.

## Grid Fallback Protection

When voltage falls to `battery_grid_fallback_voltage` (24.0-24.5V):

- The inverter switches to grid/mains power.
- During grid fallback, the engine must minimize discretionary load usage as a
  conservation signal.
- The engine must not assume grid power means unlimited energy - grid operation is
  a signal to conserve and minimize expense.
- Only critical/life-support loads (pond aeration) may remain active.

## Inverter Max Load Protection

The engine must respect the inverter's maximum load capacity:

### `max_total_load_watts`
- **Typical:** 2500W
- **Configurable:** Yes
- The engine must **never** propose or suggest a total active load exceeding this limit.
- The engine must calculate total active load watts from known load consumption data
  and include proposed new loads in the cap calculation.
- If adding a proposed load would exceed the cap, the engine must either reject it
  or propose shedding a lower-priority load first. This discretionary load shedding preserves inverter headroom.

## Load Classification and Priority

### Load Classes
- `CRITICAL` - loads that must not be automatically switched OFF unless safety requires.
- `DISCRETIONARY` - loads that can be shed when energy is scarce.

### Pond/Fish Aeration
- Pond aeration loads are a special sub-class of `CRITICAL` - see life-support invariant below.

### Shedding Order
1. LOW priority discretionary loads first
2. NORMAL priority discretionary loads next
3. HIGH priority discretionary loads last
4. CRITICAL loads must never be automatically switched OFF unless safety conditions require it

## Pond / Fish / Aeration Life-Support Invariant

**Pond aeration is life-support, not ordinary discretionary load.** Aeration devices
(aerators, air pumps, venturis, waterfalls) maintain dissolved oxygen for fish and
biological filtration.

The engine must enforce this invariant:

1. Even in poor weather / conserve mode, daytime aeration should be protected when possible.
2. The engine must treat pond aeration as a protected class above standard discretionary loads.
3. In summer or at water temperature >= 25-26°C, additional aeration priority increases.
4. The engine should prefer/propose 2-4 additional aeration loads when available and safe
   (subject to inverter cap and health/readiness).
5. Discretionary loads should be shed before life-support aeration.
6. Unhealthy/stale/unreachable aeration devices must not be blindly relied upon - flag
   for operator; do not trigger repeated switching attempts.

## Summer and High Water Temperature Rule

When **pond water temperature** equals or exceeds `pond_hot_water_temperature_c`
(configurable, typical 25-26°C):

- Aeration becomes **safety-critical** because warm water holds less dissolved oxygen.
- The engine should preserve or propose 2-4 aeration loads when available, subject to
  inverter cap and health/readiness.
- If load cap is tight, shed lower-priority discretionary loads before pond aeration.
- If only one aeration device is available, it should receive maximum protection.
- If an aeration device is unhealthy, do not rely on it - flag for operator, attempt to
  use a healthy alternative if available.

## Forecast-Aware Strategy

The engine must consider forecast across all time windows:

### Morning Decisions
- **Sunny forecast in 2 hours** → preserve battery near morning minimum, do not over-conserve.
- **Bad all-day forecast** → conserve energy aggressively, restrict discretionary loads.
- **Mixed or unknown forecast** → use conservative defaults (no adjustment factor, factor = 1.0).

### Daytime Spending
- **Good afternoon forecast** → may spend more in morning, knowing solar will recharge.
- **Poor afternoon forecast** → conserve more in morning, prepare for limited generation.

### Weather Unknown
- When weather forecast is missing, stale, or UNKNOWN, the engine must fall back to
  conservative defaults (no adjustment factor = 1.0, no assumed sunny spending).
- The weather adjustment evaluator (PR 0017) already produces `"unknown-weather"` with
  `NO_ACTION` and factor `1.0` - the decision engine must respect that result.

## Health / Readiness / Schedule / Weather Inputs

The engine ingests results from all four evaluators:

| Input | From | Purpose |
|---|---|---|
| `ReadinessResult` | `evaluate_readiness()` | Is the load allowed to switch ON now? |
| `HealthCheckResult` | `evaluate_health()` | Is the load's observed state healthy? |
| `ScheduleProfile` / `LoadScheduleProfile` | PR 0016 model | What windows and check intervals apply? |
| `WeatherAdjustmentResult` | `evaluate_weather_adjustment()` | What adjustment factor and weather decision? |
| `VoltageSnapshot` | Runtime input | Battery voltage, trend, power source |
| Active load watts | Runtime input | Current total active load in watts |
| Pond temperature | Telemetry input | Current pond water temperature (°C) |

The engine does **not** fetch any of this data directly - all inputs are provided as
function arguments.

## What the Policy Engine May Decide

For each load, the engine may produce:

| Decision | Meaning |
|---|---|
| `ALLOW_ON` | Conditions are favorable, load may be switched ON |
| `PREFER_OFF` | Conditions marginal, load should prefer OFF |
| `FORCE_OFF` | Immediate switch OFF recommended (safety/conservation) |
| `HOLD` | Keep current state, no change |
| `NO_ACTION` | No policy input, no recommendation |

Additionally, the engine may produce:
- A ranked proposal list of loads to switch ON or OFF.
- A total active load budget allocation across loads.
- An advisory follow-up string for operator visibility.

## What the Policy Engine Must Not Execute

The engine must **never**:

- Call hardware switch methods (`switch_on_device`, `switch_off_device`, etc.).
- Call Tuya, DESS, OpenWeather, or ML runtime services.
- Read config files, env vars, or network resources.
- Call `time.time()` or `datetime.now()` directly (time inputs are provided as data).
- Switch devices.
- Enable pump automation.
- Enable ML control.
- Send commands to devices.

All engine output is **advisory/proposal** - execution is deferred to later PRs (0019+).

## Scenario Matrix

The following 12 scenarios describe expected engine behavior across diverse conditions.

### 1. Cloudy morning, sunny forecast in 2 hours
Preserve battery near morning minimum. Do not over-conserve blindly. Delay discretionary
loads until solar charging begins or forecast improves. Critical loads may remain ON.

### 2. Cloudy morning, bad forecast all day
Conserve energy more aggressively. Minimize discretionary load activation. Protect evening
reserve from morning draw-down. Only life-support aeration and critical loads may activate.

### 3. Sunny day, battery rising toward 29.5V
Spend surplus energy on discretionary loads. Activate additional loads within inverter
capacity. Aeration loads activated first, then priority-ranked discretionary loads.

### 4. Sunny day, active load near 2500W
Do not propose additional loads. If aeration needs exist, can any lower-priority load
be shed? Shed lowest-priority discretionary loads before aeration.

### 5. Battery near 24.0-24.5V fallback threshold
Minimize all discretionary loads. Prepare for grid fallback. Protect critical aeration only.
All discretionary loads should be switched OFF.

### 6. Evening reserve around 26.5V
Shed discretionary loads. Preserve reserve for overnight. Higher check frequency (60s).
Critical aeration may remain if voltage is well above absolute floor.

### 7. Weather unknown
Conservative defaults. No assumed sunny spending. Adjustment factor = 1.0. Use neutral
thresholds. Discretionary loads restricted to conservative limits.

### 8. Summer, pond temperature 26°C, bad weather
Aeration is safety-critical. Preserve/propose additional aeration if possible. Shed
discretionary loads first. Even in poor weather, pond aeration must be protected.

### 9. Pond temperature 26°C, active load near inverter cap
Protect aeration. Cannot add more aeration without shedding. Shed lowest-priority
discretionary loads. Preserve as many aeration loads as cap allows.

### 10. Pond temperature high, one aeration device unhealthy
Do not rely on unhealthy aeration. Flag for operator. Attempt to activate healthy
aeration if available. If no healthy aeration is available, escalate warning.

### 11. Multiple optional aerators available; choose 2-4
If energy/load budget allows, activate up to 4 aerators. Prioritize healthiest. Respect
cooldown/readiness. Shed discretionary loads if budget is tight.

### 12. High PV generation but low-priority load competes with aeration
Aeration wins. Shed low-priority load if cap is tight. Spend surplus on aeration first.
Do not allow a low-priority load to displace life-support aeration.

## Future Implementation Sequence

| PR | Title | Scope |
|---|---|---|
| **0018A** | Policy engine operating boundaries | **This document** - boundaries, invariants, scenario matrix. No code. |
| 0018B | Passive policy engine models | Frozen dataclasses for input/output/budget/context. |
| 0018C | Pure deterministic policy decision engine | `evaluate_policy_decision()` pure function, no execution. |
| 0018D | Scenario matrix and regression tests | Tests covering all 12 scenarios. |
| 0019 | Manual control API or command queue | API or queue for manual operator actions. |
| 0020 | command proposal before automatic execution | Proposed commands before automatic execution (dry-run mode). |
| Later | Controlled execution with safety gates | Execute proposed commands with safety gates. |
| Later | ML advisory | ML suggests thresholds/windows; engine retains final authority. |
| Much later | ML control | ML directly influences decisions - only after separate safety-reviewed approval per ADR-0003. |

## Safety Boundaries

1. All 0018 series output is advisory/proposal, not execution.
2. The engine must never call hardware or runtime services.
3. The engine must never switch devices.
4. The engine must never read current time from system clock.
5. The engine must never read config files or env vars.
6. The engine must never enable automation or ML control.
7. Manual switch control must remain available and unchanged.
8. Pump automation remains disabled by default per PR 0008.
9. ML control remains disabled per ADR-0003.
10. Pond aeration is life-support - the engine must protect it.
11. The engine must not violate the inverter max load cap.
12. The engine must respect battery operating extrema at all times.
