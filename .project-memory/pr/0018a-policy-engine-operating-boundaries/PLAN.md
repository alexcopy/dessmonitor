# PR 0018A — Policy Engine Operating Boundaries

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `28b38c9b7a381ce5cfd474a8120f67d545602f6a` |
| Branch | `git branch --show-current` | `0018a-policy-engine-operating-boundaries` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0018a-policy-engine-operating-boundaries` and working tree is clean.

## 2. Purpose

PR 0018A is the first sub-PR of the 0018 policy decision engine series. It is a
**documentation and validation-only PR** that captures the policy engine's operating
boundaries, battery/inverter extrema, pond/fish life-support invariants, forecast-aware
strategy rules, and a scenario matrix — **before any implementation code is written**.

This PR does NOT implement the decision engine. It does NOT add domain models (those are
0018B). It does NOT add the pure decision function (0018C). It does NOT add scenario tests
(0018D). It only records **what the engine must respect** and ensures future PRs build on
a shared understanding of the product's safety-critical invariants.

## 3. Product Context

1. The policy decision engine is the "brain" of the energy-aware control system.
2. It must compose readiness (PR 0014), health (PR 0015), schedule (PR 0016), and weather
   adjustment (PR 0017) signals into a unified decision for each device load.
3. It must respect battery operating extrema, inverter load limits, and pond/fish life-support
   invariants.
4. It must not be a naive combiner — it must understand forecast-aware morning strategy,
   daytime surplus spending, evening reserve protection, grid fallback, and pond aeration
   priority.
5. This PR records those boundaries as a shared reference document before any implementation.
6. Manual relay/switch ON/OFF remains available and unchanged.
7. Pump automation remains obsolete and disabled by default (PR 0008).
8. ML advisory may be used later; ML control remains disabled.

## 4. Current Repository State

| Capability | File | Status |
|---|---|---|
| Generic control domain types | `app/control/domain.py` | Passive (PR 0009) |
| Relay-to-SwitchableLoad mapping | `app/control/relay_mapping.py` | Passive (PR 0010) |
| Energy-aware control policy requirements | `.project-memory/ENERGY_AWARE_CONTROL_POLICY.md` | Documented (PR 0011) |
| Energy policy domain types (17) | `app/control/energy_policy.py` | Passive (PR 0012) |
| Static energy policy config example | `examples/energy_policy.example.yaml` | Documented (PR 0013) |
| Readiness evaluator (pure function) | `app/control/readiness.py` | Implemented (PR 0014) |
| Health evaluator (pure function) | `app/control/health.py` | Implemented (PR 0015) |
| Schedule profile model | `app/control/schedule_profile.py` | Implemented (PR 0016) |
| Weather adjustment evaluator | `app/control/weather_adjustment.py` | Implemented (PR 0017) |
| **Policy engine operating boundaries** | `.project-memory/POLICY_DECISION_ENGINE.md` | **This PR** |

### 4.1 Grep Evidence Summary

**Battery extrema references exist** in policy documents: `26.5V` (evening reserve),
`24.0–24.5V` (grid fallback range). No structured `battery_grid_fallback_voltage` or
`battery_high_voltage_spend_threshold` terms exist yet — they will be defined in this PR.

**Pond/fish/aeration references exist** only in the high-level project description
("pond energy monitoring system") and in ADR-0003 mentioning "pond pumps". No structured
invariant for pond aeration life-support exists yet.

**Inverter max load** is not documented anywhere. `2500W` and `load_watts` are absent
from project memory. The example config references `load_in_wt` at the device level
but not a global inverter cap.

**All evaluator modules (PR 0014–0017) are implemented and pass validation.**

## 5. Why PR 0018 Is Split

PR 0018 is split into four safe sub-PRs to maintain reviewability and safety boundaries:

| Sub-PR | Title | Scope |
|---|---|---|
| 0018A | Policy engine operating boundaries | **This PR** — documentation and validation of product invariants, battery/inverter extrema, pond life-support rules, forecast strategy, scenario matrix. No code. |
| 0018B | Passive policy engine models | Add `PolicyDecisionInput`, `PolicyDecisionResult`, `LoadCandidate`, `EnergyBudget`, `BatteryOperatingWindow`, `PondSafetyContext`, `ForecastStrategyContext` as frozen dataclasses in `app/control/decision_models.py`. |
| 0018C | Pure deterministic decision engine | Implement `evaluate_policy_decision(policy_input: PolicyDecisionInput) -> PolicyDecisionResult` as a pure function with no execution. |
| 0018D | Scenario matrix and regression tests | Add scenario-based tests covering morning/cloud/sun/load/pond/aeration cases. |

## 6. Required Document

### File: `.project-memory/POLICY_DECISION_ENGINE.md`

The document must include these sections:

### 6.1 Purpose
State that this document records the operating boundaries, product invariants, and scenario
matrix for the energy-aware policy decision engine before any implementation begins.

### 6.2 Relationship to PR 0014–0017
Explain that the engine composes readiness (0014), health (0015), schedule (0016), and
weather adjustment (0017) outputs, plus battery, inverter, and pond data.

### 6.3 Why 0018 Is Split into 0018A/0018B/0018C/0018D
Briefly describe the four sub-PRs and their boundaries.

### 6.4 Battery Operating Extrema
Document these configurable parameters:

- `battery_grid_fallback_voltage`: Configurable, typical range 24.0–24.5V. Below this threshold
  the inverter falls back to grid power and discretionary loads should be minimized.
- `battery_morning_minimum_voltage`: Configurable voltage floor for morning hours, accounting
  for forecast later in the day. Prevents over-discharge before solar generation starts.
- `battery_evening_reserve_voltage`: Configurable, typical 26.5V. Must protect this voltage
  after sunset to preserve overnight capacity.
- `battery_high_voltage_spend_threshold`: Configurable, typical 28.5–29.0V. Above this
  threshold the battery is nearly full and surplus solar should be spent on discretionary
  loads rather than wasted.
- `battery_full_voltage`: Typical 29.5V. When battery reaches this voltage, charging stops;
  any remaining solar must be spent or clipped.

### 6.5 Morning Minimum Strategy
- Overcast morning with sunny forecast in 2 hours: preserve battery near morning minimum;
  do not over-conserve blindly, do not over-spend early. The engine should delay discretionary
  load activation until solar charging begins or forecast improves.
- Cloudy morning with bad all-day forecast: conserve energy more aggressively; minimize
  discretionary load activation; protect evening reserve from morning draw-down.
- Morning strategy must consider forecast later in the day as a primary input.

### 6.6 Daytime High-Voltage Spend Strategy
- Sunny daytime with battery rising toward 29.5V: spend surplus energy on discretionary
  loads to avoid wasting solar production.
- The engine should activate additional loads (within inverter capacity) when voltage
  exceeds `battery_high_voltage_spend_threshold`.
- Priorities: active aeration loads first, then other discretionary loads by priority.

### 6.7 Evening Reserve Strategy
- After sunset, the engine must protect `battery_evening_reserve_voltage` (~26.5V).
- Discretionary loads should be shed if voltage is at or approaching the reserve threshold.
- Critical loads (pond aeration) may remain ON if voltage is above a lower safety floor,
  but aeration priority is weighed against reserve protection.
- Evening check frequency should be higher (e.g., every 60s vs 300s during day).

### 6.8 Grid Fallback Protection
- When voltage falls to `battery_grid_fallback_voltage` (24.0–24.5V), the inverter switches
  to grid/mains power.
- During grid fallback, the engine should minimize discretionary load usage as a conservation
  signal.
- The engine must not assume grid power means unlimited energy — grid operation is a signal
  to conserve and minimize expense.

### 6.9 Inverter Max Load Protection
- `max_total_load_watts`: Configurable, typical 2500W. The engine must never propose or
  suggest a total active load exceeding this limit.
- The engine must calculate total active load watts from known load consumption data and
  include proposed new loads in the cap calculation.
- If adding a proposed load would exceed the cap, the engine must either reject it or
  propose shedding a lower-priority load first.

### 6.10 Load Classification and Priority
- Loads are classified as `CRITICAL` or `DISCRETIONARY` per `DeviceEnergyPolicy.load_class`.
- Pond/fish aeration loads are a special sub-class of CRITICAL (see Section 6.11).
- Within DISCRETIONARY, loads have `DevicePriority` (HIGH, NORMAL, LOW).
- Shedding order: LOW priority discretionary first, then NORMAL, then HIGH.
- CRITICAL loads must never be automatically switched OFF unless safety conditions require it.

### 6.11 Pond/Fish/Aeration Life-Support Invariant
- Pond aeration is life-support, not ordinary discretionary load. Aeration devices
  (aerators, air pumps, venturis, waterfalls) maintain dissolved oxygen for fish and
  biological filtration.
- The engine must treat pond aeration as a protected class above standard discretionary loads.
- Even in poor weather or conserve mode, daytime pond aeration should be protected as much
  as possible (subject to inverter cap and health/readiness).
- If aeration load is unhealthy/stale/unreachable, do not blindly rely on it — flag for operator.
- An unhealthy aeration device should not trigger repeated switching attempts.

### 6.12 Summer and High Water Temperature Rule
- When pond water temperature equals or exceeds `pond_hot_water_temperature_c`
  (configurable, typical 25–26°C), aeration becomes safety-critical because warm water
  holds less dissolved oxygen.
- At high pond temperature, the engine should preserve or propose 2–4 aeration loads
  when available, subject to inverter cap and health/readiness.
- If load cap is tight, shed lower-priority discretionary loads before pond aeration.
- If only one aeration device is available, it should receive maximum protection.

### 6.13 Forecast-Aware Strategy
- The engine must consider forecast when making morning decisions:
  - Sunny forecast in 2 hours → preserve battery near morning minimum, do not over-conserve.
  - Bad all-day forecast → conserve energy aggressively, restrict discretionary loads.
  - Mixed or unknown forecast → use conservative defaults.
- The engine must consider forecast when planning daytime spending:
  - Good afternoon forecast → may spend more in morning, knowing solar will recharge.
  - Poor afternoon forecast → conserve more in morning, prepare for limited generation.

### 6.14 Weather Unknown Safe Behavior
- When weather forecast is missing, stale, or UNKNOWN, the engine must fall back to
  conservative defaults (no adjustment factor = 1.0, no assumed sunny spending).
- The weather adjustment evaluator (PR 0017) already produces `"unknown-weather"` with
  NO_ACTION and factor 1.0 — the decision engine must respect that result.

### 6.15 Health/Readiness/Schedule/Weather Inputs
The engine ingests results from all four evaluators/models:
- `ReadinessResult` from `evaluate_readiness()` — is the load allowed to switch ON?
- `HealthCheckResult` from `evaluate_health()` — is the load's observed state healthy?
- `ScheduleProfile` / `LoadScheduleProfile` — what windows and intervals apply?
- `WeatherAdjustmentResult` from `evaluate_weather_adjustment()` — what adjustment factor
  and decision apply?
- Battery voltage and trend from `VoltageSnapshot`.
- Power source from `VoltageSnapshot.power_source`.
- Active load watts from runtime state (provided as input, not fetched directly).
- Pond temperature from telemetry (provided as input, not fetched directly).

### 6.16 What the Policy Engine May Decide
For each load, the engine may produce:
- `ALLOW_ON` — Conditions are favorable, load may be switched ON.
- `PREFER_OFF` — Conditions marginal, load should prefer OFF but may remain ON.
- `FORCE_OFF` — Immediate switch OFF recommended (safety/conservation).
- `HOLD` — Keep current state, no change.
- `NO_ACTION` — No policy input, no recommendation.

Additionally, the engine may produce:
- A ranked proposal list of loads to switch ON or OFF.
- A total active load budget allocation across loads.
- An advisory follow-up string for operator visibility.

### 6.17 What the Policy Engine Must Not Execute
The engine must **never**:
- Call hardware switch methods (`switch_on_device`, `switch_off_device`, etc.).
- Call Tuya, DESS, OpenWeather, or ML runtime services.
- Read config files, env vars, or network resources.
- Call `time.time()` or `datetime.now()` directly (time inputs are provided as data).
- Switch devices.
- Enable pump automation.
- Enable ML control.
- Send commands to devices.

All engine output is advisory/proposal — execution is deferred to later PRs (0019+).

### 6.18 Scenario Matrix
The document must describe these 12 scenarios:

1. **Cloudy morning, sunny forecast in 2 hours**: Preserve battery near morning minimum;
   do not over-conserve blindly; delay discretionary loads until solar charging begins.
2. **Cloudy morning, bad all-day forecast**: Conserve energy more aggressively; minimize
   discretionary load activation; protect evening reserve from morning draw-down.
3. **Sunny day, battery rising toward 29.5V**: Spend surplus energy on discretionary loads;
   activate additional loads within inverter capacity.
4. **Sunny day, active load already near 2500W**: Do not propose additional loads;
   if aeration needs exist, can any lower-priority load be shed?
5. **Battery near 24.0–24.5V fallback threshold**: Minimize all discretionary loads;
   prepare for grid fallback; protect critical aeration only.
6. **Evening reserve protection around 26.5V**: Shed discretionary loads; preserve reserve
   for overnight; higher check frequency.
7. **Weather unknown**: Conservative defaults; no assumed sunny spending; factor = 1.0.
8. **Pond temperature 26°C in summer, bad weather**: Aeration is safety-critical;
   preserve/propose additional aeration if possible; shed discretionary loads first.
9. **Pond temperature 26°C, active load near inverter cap**: Protect aeration;
   cannot add more aeration without shedding; shed lowest-priority discretionary loads.
10. **Pond temperature high, one aeration device unhealthy**: Do not rely on unhealthy
    aeration; flag for operator; attempt to activate healthy aeration if available.
11. **Multiple optional aerators available, choose 2–4**: If energy/load budget allows,
    activate up to 4 aerators; prioritize healthiest; respect cooldown/readiness.
12. **High PV generation but low-priority load competes with aeration**: Aeration wins;
    shed low-priority load if cap is tight; spend surplus on aeration first.

### 6.19 Future Implementation Sequence
- 0018B: Passive policy engine models (dataclasses for input/output/budget/context).
- 0018C: Pure `evaluate_policy_decision()` implementation.
- 0018D: Scenario matrix tests.
- 0019: Manual control API or command queue.
- 0020: Command proposal before automatic execution.
- Later: Controlled execution with safety gates.
- Later: ML advisory.
- Much later: ML control only after separate safety-reviewed approval.

### 6.20 Safety Boundaries
1. All 0018 series output is advisory/proposal, not execution.
2. The engine must never call hardware or runtime services.
3. The engine must never switch devices.
4. The engine must never read current time from system clock.
5. The engine must never read config files or env vars.
6. The engine must never enable automation or ML control.
7. Manual switch control must remain available and unchanged.
8. Pump automation remains disabled by default per PR 0008.
9. ML control remains disabled per ADR-0003.

## 7. Required Numeric Boundaries

The `POLICY_DECISION_ENGINE.md` document must define or reference:

| Parameter | Typical Value | Configurable |
|---|---|---|
| `battery_grid_fallback_voltage` | 24.0–24.5V | Yes |
| `battery_morning_minimum_voltage` | (to be defined by operator) | Yes |
| `battery_evening_reserve_voltage` | 26.5V | Yes |
| `battery_high_voltage_spend_threshold` | 28.5–29.0V | Yes |
| `battery_full_voltage` | 29.5V | Yes (hardware limit) |
| `max_total_load_watts` | 2500W | Yes |
| `pond_hot_water_temperature_c` | 25–26°C | Yes |

## 8. Required Product Invariants

1. Avoid low-voltage collapse — protect battery from over-discharge.
2. Avoid wasting daytime solar when battery approaches full voltage (29.5V).
3. Never exceed inverter max load (2500W).
4. Morning decisions must account for forecast later in the day.
5. Poor all-day forecast should conserve energy aggressively.
6. Sunny forecast later may justify holding morning minimum rather than aggressive shutdown.
7. Pond aeration is life-support or critical, not normal discretionary load.
8. Summer/high pond temperature raises aeration priority.
9. At pond temperature >= 25–26°C, the brain should preserve/propose additional aeration if possible.
10. Shed lower-priority discretionary loads before life-support aeration.
11. Unhealthy/stale/unreachable aeration device must not be blindly relied upon.
12. All 0018A output is documentation and validation only — no decision engine implementation.

## 9. Allowed Implementation Files

The following files may be edited by the coder agent:

| File | Action |
|---|---|
| `.project-memory/POLICY_DECISION_ENGINE.md` | **Create** — policy engine operating boundaries document |
| `scripts/check-policy-engine-operating-boundaries.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0018A section |
| `.project-memory/ROADMAP.md` | **Edit** — update roadmap with 0018A/0018B/0018C/0018D |
| `.project-memory/pr/0018a-policy-engine-operating-boundaries/CODER_REPORT.txt` | **Create** — coder report |

## 10. Forbidden Implementation Files

The coder must **not** edit:

- `run.py`
- `app/service/**`
- `app/devices/**`
- `app/tuya/**`
- `app/monitoring/**`
- `app/ml/**`
- `app/weather/**`
- `app/control/*.py` (all existing modules frozen)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 11. Static Validation Script

### File: `scripts/check-policy-engine-operating-boundaries.sh`

The script must:

1. Check `.project-memory/POLICY_DECISION_ENGINE.md` exists.
2. Check `battery_grid_fallback_voltage` exists.
3. Check `battery_morning_minimum_voltage` exists.
4. Check `battery_evening_reserve_voltage` exists.
5. Check `battery_high_voltage_spend_threshold` exists.
6. Check `battery_full_voltage` exists.
7. Check `max_total_load_watts` exists.
8. Check `pond_hot_water_temperature_c` exists.
9. Check numeric values appear: `24.0`, `24.5`, `26.5`, `29.5`, `2500`, `25`, `26`.
10. Check pond/fish/aeration/life-support language exists.
11. Check morning/cloudy/sunny forecast scenario exists.
12. Check high-voltage spend strategy exists.
13. Check inverter max load protection exists.
14. Check discretionary load shedding before aeration exists.
15. Check no execution/hardware/runtime wiring language exists.
16. Check 0018B, 0018C, 0018D are documented.
17. Check no forbidden runtime files modified if feasible (git diff check).
18. Print clear per-check output.
19. Exit 0 only when all checks pass.
20. Exit 1 if any check fails.

### GitHub Actions Integration

Add one step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Policy engine operating boundaries check
        run: bash scripts/check-policy-engine-operating-boundaries.sh
```

This step must be added **after** the existing weather adjustment evaluator check.

## 12. CURRENT_STATE.md Update

Add a concise PR 0018A section to `.project-memory/CURRENT_STATE.md`:

```
## PR 0018A — Policy Engine Operating Boundaries
PR 0018A records policy engine operating boundaries, battery/inverter extrema, pond/fish
life-support invariants, forecast-aware strategy rules, and a scenario matrix in
`.project-memory/POLICY_DECISION_ENGINE.md`. No decision engine implementation yet.
No runtime wiring. No execution. No automation. Pond/fish aeration life-support invariant
recorded. Battery/inverter extrema recorded. Future sub-PRs: 0018B (models), 0018C
(decision function), 0018D (scenario tests).
```

Do not rewrite unrelated sections.

## 13. ROADMAP.md Update

Replace the single `PR 0018` line with:

```
- [x] PR 0018A — Policy engine operating boundaries (documentation only)
- [ ] PR 0018B — Passive policy engine models
- [ ] PR 0018C — Pure deterministic policy decision engine
- [ ] PR 0018D — Scenario matrix and regression tests
- [ ] PR 0019 — Manual control API or command queue
- [ ] PR 0020 — Command proposal before automatic execution
- [ ] Later — Controlled execution with safety gates
- [ ] Later — ML advisory
- [ ] Much later — ML control (only after separate safety-reviewed approval)
```

Update under "Phase 2b: Platform Control Redesign — Staged Backend Refactor".

Do not rewrite unrelated sections.

## 14. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are **LOCKED** after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |

### Artifact Layout

```
.project-memory/pr/0018a-policy-engine-operating-boundaries/
├── PLAN.md              ← This file (locked after approval)
├── PLAN_REVIEW.yaml     ← Plan-review artifact (locked after approval)
├── CODER_REPORT.txt     ← Coder artifacts (created by coder)
└── PRECOMMIT_REVIEW.yaml ← Precommit-review artifact
```

## 15. Boundary Confirmations

- **Documentation and validation only**: No code implementation in 0018A.
- **No decision engine implementation**: Deferred to 0018B/0018C/0018D.
- **No policy model implementation**: Deferred to 0018B.
- **No runtime wiring**: Not connected to any runtime component.
- **No hardware execution**: Does not call any switch/device/Tuya method.
- **No config loading**: Does not read YAML, env vars, or files.
- **No system clock**: Not applicable — documentation only.
- **No automation enabled**: Pump automation disabled per PR 0008.
- **No ML control enabled**: ML advisory is advice-only. ML control deferred per ADR-0003.
- **Manual switch control preserved**: All switch methods unchanged.
- **Pump automation remains disabled**: Per PR 0008.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **All existing evaluators unchanged**: PR 0014–0017 frozen.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
