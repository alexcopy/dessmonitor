# PR 0018C — Pure Deterministic Policy Decision Engine

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `2ef2692618134cc7fa9a50bf3fe98c98bd8e1724` |
| Branch | `git branch --show-current` | `0018c-pure-deterministic-policy-decision-engine` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0018c-pure-deterministic-policy-decision-engine` and working tree is clean.

## 2. Purpose

PR 0018C implements the **first pure deterministic policy decision engine** — a pure function
`evaluate_policy_decision(policy_input: PolicyDecisionInput) -> PolicyDecisionResult` that
combines battery operating window, energy budget, load wattage, readiness, health, schedule
profile, weather adjustment, forecast strategy, and pond/fish/aeration life-support context
into a single advisory decision.

The engine is **pure and deterministic**: same input always produces the same output. It does
not execute commands. It does not create command proposals. It does not wire into runtime.
It does not fetch weather. It does not switch devices.

This PR follows PR 0018A (operating boundaries), PR 0018B (passive models), and precedes
PR 0018D (scenario matrix tests), PR 0019+ (command proposal and execution).

## 3. Product Context

1. The policy decision engine is the "brain" of the energy-aware control system.
2. It composes readiness (PR 0014), health (PR 0015), schedule (PR 0016), weather adjustment
   (PR 0017), battery/inverter extrema (PR 0018A), and passive model types (PR 0018B).
3. The engine produces advisory `PolicyDecisionResult` only — no hardware execution.
4. All inputs are passive data; the engine is pure and deterministic.
5. The engine respects pond/fish aeration life-support invariants, inverter max load caps,
   battery fallback thresholds, forecast-aware strategy, and weather adjustment.
6. Manual relay/switch ON/OFF remains available and unchanged.
7. Pump automation remains obsolete and disabled by default (PR 0008).
8. ML advisory may be used later; ML control remains disabled.

## 4. Current Repository State

| Capability | File | Status |
|---|---|---|
| Readiness evaluator (pure function) | `app/control/readiness.py` | Implemented (PR 0014) |
| Health evaluator (pure function) | `app/control/health.py` | Implemented (PR 0015) |
| Schedule profile model | `app/control/schedule_profile.py` | Implemented (PR 0016) |
| Weather adjustment evaluator | `app/control/weather_adjustment.py` | Implemented (PR 0017) |
| Policy engine operating boundaries | `.project-memory/POLICY_DECISION_ENGINE.md` | Documented (PR 0018A) |
| Passive policy engine models (7 types) | `app/control/policy_models.py` | Implemented (PR 0018B) |
| **Policy decision engine (pure function)** | `app/control/policy_decision.py` | **This PR** |

### 4.1 Grep Evidence Summary

**All seven model types from PR 0018B** (`PolicyDecisionInput`, `PolicyDecisionResult`,
`LoadCandidate`, `EnergyBudget`, `BatteryOperatingWindow`, `PondSafetyContext`,
`ForecastStrategyContext`) exist in `app/control/policy_models.py` and are exported.

**`evaluate_policy_decision` does not exist** anywhere — no module `app/control/policy_decision.py`
exists.

**`CommandProposal` and `CommandQueue`** do not exist in `app/control/` — good (deferred).

**All evaluator modules (PR 0014–0017) pass validation.** All policy engine boundaries
(PR 0018A) and models (PR 0018B) pass validation.

## 5. Relationship to PR 0014–0018B

| PR | Title | Input to 0018C |
|---|---|---|
| 0014 | Pure readiness evaluator | `ReadinessResult` via `LoadCandidate.readiness` |
| 0015 | Pure health evaluator | `HealthCheckResult` via `LoadCandidate.health` |
| 0016 | Schedule profile model | `LoadScheduleProfile` via `LoadCandidate.schedule_profile` |
| 0017 | Weather adjustment evaluator | `WeatherAdjustmentResult` via `PolicyDecisionInput.weather_adjustment` |
| 0018A | Policy engine operating boundaries | Numeric thresholds and invariants encoded in model defaults |
| 0018B | Passive policy engine models | `PolicyDecisionInput`, `PolicyDecisionResult`, `LoadCandidate`, etc. |
| 0018C | **Pure decision engine** | `evaluate_policy_decision()` implementation consuming the above |
| 0018D | Scenario matrix tests | Will test 0018C with constructed inputs |

## 6. Required Module Design

### Module Location

**Create:** `app/control/policy_decision.py`

**May update:** `app/control/__init__.py` (export `evaluate_policy_decision`)

### Required Function

```python
def evaluate_policy_decision(
    policy_input: PolicyDecisionInput,
) -> PolicyDecisionResult
```

### Module Dependencies

The module must use **only**:
- Python standard library (`typing`)
- `app.control.energy_policy` types: `EnergyPolicyDecision`, `EnergyPolicyContext`, `ReadinessResult`,
  `HealthCheckResult`, `LoadClass`, `DevicePriority`, `HealthStatus`, `VoltageSnapshot`
- `app.control.policy_models` types: `PolicyDecisionInput`, `PolicyDecisionResult`, `LoadCandidate`,
  `EnergyBudget`, `BatteryOperatingWindow`, `PondSafetyContext`, `ForecastStrategyContext`
- `app.control.weather_adjustment` types: `WeatherAdjustmentResult`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
- `app.control.readiness` (readiness types are accessed via `energy_policy` types already)
- `app.control.health` (health types are accessed via `energy_policy` types already)
- `app.control.schedule_profile` (schedule types accessed via `policy_models` imports)
- Any runtime service, config reader, network client, or hardware adapter

### Module Requirements

1. Pure, deterministic, side-effect-free function.
2. No `time.time` or `datetime.now` calls.
3. No file/env/network reads.
4. No hardware calls.
5. No device switching.
6. No command proposal or command queue.
7. No logging.
8. No ML integration.
9. No config loading.
10. Import-safe — no side effects at module import time.

## 7. Required Evaluation Semantics

The function must evaluate the input in the following order of priority. Earlier checks
take precedence over later ones. A check applies only if its conditions are met.

### 7.1 Invalid / No Loads

If `policy_input.loads` is empty or the specified target load does not exist:
- Return `PolicyDecisionResult(decision=NO_ACTION, reason="no-loads", ...)`

### 7.2 Battery Fallback Protection

If battery voltage is at or below `battery_window.battery_grid_fallback_voltage`:
- Check if a non-life-support, currently-on, discretionary load exists that can be shed.
- If yes → `FORCE_OFF` on the lowest-priority such load, reason `"battery-fallback-protection"`.
- Never pick a life-support aeration load as the first shed target.
- If no safe shed target exists → `PREFER_OFF` on any discretionary load, reason `"battery-fallback-protection"`.

### 7.3 Inverter Max Load Protection

If `energy_budget.current_total_load_watts >= energy_budget.max_total_load_watts`:
- Check if a non-life-support, currently-on, discretionary load exists that can be shed.
- If yes → `FORCE_OFF` on the lowest-priority such load, reason `"inverter-load-cap-protection"`.
- If no safe shed target exists → `PREFER_OFF`, reason `"inverter-load-cap-protection"`.

### 7.4 Pond/Fish/Aeration Life-Support

If life-support conditions are active (any of):
- `pond_safety.life_support_required` is `True`, OR
- `pond_safety.pond_temperature_c` >= `pond_safety.pond_hot_water_temperature_c`, OR
- pond is in summer hot context (high temperature + summer season)

Then:
- Find a life-support aeration load that is currently OFF and healthy (health status is HEALTHY
  or not STALE/MISMATCH/UNREACHABLE).
- Check if projected total load (current + configured_load_watts) fits under `max_total_load_watts`.
- If fits → `ALLOW_ON` on that aeration load, reason `"pond-life-support-aeration"`.
- If does not fit → check if a lower-priority non-life-support discretionary load can be shed
  first. If so → return result suggesting shed first; if no → `NO_ACTION`, reason `"shed-discretionary-for-aeration"`.
- Do not blindly rely on unhealthy/stale/unreachable aeration loads.

### 7.5 Morning Minimum Forecast Strategy

If `forecast_strategy.morning_strategy_active` is `True` AND
`forecast_strategy.forecast_improves_later_today` is `True` AND
battery voltage is at or below `battery_window.battery_morning_minimum_voltage`:
- Avoid new discretionary loads — prefer `NO_ACTION` or `PREFER_OFF` for discretionary targets.
- Reason: `"morning-minimum-hold-for-sun"`.
- Pond life-support remains higher priority (already handled by 7.4).

### 7.6 Bad All-Day Forecast

If `forecast_strategy.bad_forecast_all_day` is `True`:
- Conserve discretionary loads.
- If a safe shed target exists (non-life-support discretionary currently-on) → `PREFER_OFF`.
- Otherwise → `NO_ACTION`.
- Reason: `"bad-forecast-conserve"`.
- Pond life-support remains higher priority.

### 7.7 High-Voltage Daytime Spend

If battery voltage is at or above `battery_window.battery_high_voltage_spend_threshold`:
- Find a load that is: currently OFF, healthy (not STALE/MISMATCH/UNREACHABLE), and ready
  (readiness result is `ready`).
- Check if projected total load (current + configured_load_watts) fits under `max_total_load_watts`.
- If fits → `ALLOW_ON` on the best candidate.
  - Preference order: life-support/critical loads first, then higher priority, then larger
    configured_load_watts (more useful energy spending).
- Reason: `"high-voltage-spend"`.

### 7.8 Weather Adjustment

If `weather_adjustment` is not None:
- If `weather_adjustment.decision` is `PREFER_OFF` or `FORCE_OFF` and a safe discretionary
  shed target exists:
  → `PREFER_OFF`, reason `"weather-conserve"`.
- If `weather_adjustment.decision` is `ALLOW_ON` and a healthy/ready load fits the budget:
  → `ALLOW_ON`, reason `"weather-spend"`.
- If `weather_adjustment.decision` is `NO_ACTION` or `HOLD`:
  → Fall through to default.

### 7.9 Default

If no rule above triggered a decision:
- Return `PolicyDecisionResult(decision=NO_ACTION, reason="neutral-no-action")`.

## 8. Required Reason Strings

| Reason String | When Used |
|---|---|
| `"no-loads"` | Input has no loads or target load is invalid |
| `"battery-fallback-protection"` | Battery at/below grid fallback voltage, shed needed |
| `"inverter-load-cap-protection"` | Current load at/above inverter max capacity |
| `"pond-life-support-aeration"` | Aeration load allowed ON for life-support |
| `"shed-discretionary-for-aeration"` | Budget full, need to shed discretionary before aeration |
| `"morning-minimum-hold-for-sun"` | Morning active, forecast later improves, hold at minimum |
| `"bad-forecast-conserve"` | Bad all-day forecast, conserve discretionary loads |
| `"high-voltage-spend"` | Battery near full, spend surplus on discretionary load |
| `"weather-conserve"` | Weather adjustment recommends conservation |
| `"weather-spend"` | Weather adjustment recommends spending |
| `"neutral-no-action"` | No rule triggered a decision |

## 9. Required Product Rules

1. Use `configured_load_watts` for `projected_total_load_watts` calculations.
2. `configured_load_watts` is an estimate, not live telemetry.
3. Never allow ON if `projected_total_load_watts > max_total_load_watts`.
4. Preserve pond/aeration life-support above discretionary loads.
5. Do not blindly rely on unhealthy/stale/unreachable aeration devices.
6. Do not turn off life-support loads as the first shed target.
7. If no safe candidate exists, return `NO_ACTION` with explanation.
8. `PolicyDecisionResult.projected_total_load_watts` must be set whenever a target load
   is selected (either for ON or OFF).

## 10. Implementation Robustness Requirements

1. Must tolerate `policy_input.context` being `None`.
2. Must tolerate missing voltage fields (use safe getattr with defaults).
3. Must support voltage under common names: `battery_voltage`, `voltage`, `value`,
   `battery_voltage_v`.
4. Must tolerate enum and string values for `load_class`, `priority`, decision/status names.
5. Must not require all enum members to exist.
6. Must use deterministic ordering only (no random, no unstable sort).

## 11. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/policy_decision.py` | **Create** — pure deterministic policy decision engine |
| `app/control/__init__.py` | **Edit** — export `evaluate_policy_decision` |
| `scripts/check-policy-decision-engine.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0018C section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0018C in roadmap |
| `.project-memory/pr/0018c-pure-deterministic-policy-decision-engine/CODER_REPORT.txt` | **Create** — coder report |

## 12. Forbidden Implementation Files

The coder must **not** edit:
- `run.py`
- `app/service/**`
- `app/devices/**`
- `app/tuya/**`
- `app/monitoring/**`
- `app/ml/**`
- `app/weather/**`
- `app/control/domain.py` (frozen from PR 0009)
- `app/control/relay_mapping.py` (frozen from PR 0010)
- `app/control/energy_policy.py` (frozen from PR 0012)
- `app/control/readiness.py` (frozen from PR 0014)
- `app/control/health.py` (frozen from PR 0015)
- `app/control/schedule_profile.py` (frozen from PR 0016)
- `app/control/weather_adjustment.py` (frozen from PR 0017)
- `app/control/policy_models.py` (frozen from PR 0018B)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 13. Static Validation Script

### File: `scripts/check-policy-decision-engine.sh`

The script must:

1. Check `app/control/policy_decision.py` exists.
2. Check `evaluate_policy_decision` function exists.
3. Check `PolicyDecisionInput` is used in function signature.
4. Check `PolicyDecisionResult` is the return type.
5. Check `LoadCandidate`, `EnergyBudget`, `BatteryOperatingWindow`, `PondSafetyContext`,
   `ForecastStrategyContext` are used in the module.
6. Check `EnergyPolicyDecision` is used.
7. Check `configured_load_watts`, `projected_total_load_watts`, `max_total_load_watts`
   are referenced.
8. Check all required reason strings exist (or partial match):
   `no-loads`, `battery-fallback-protection`, `inverter-load-cap-protection`,
   `pond-life-support-aeration`, `shed-discretionary-for-aeration`,
   `morning-minimum-hold-for-sun`, `bad-forecast-conserve`, `high-voltage-spend`,
   `weather-conserve`, `weather-spend`, `neutral-no-action`.
9. Check no `CommandProposal` or `CommandQueue` exists.
10. Check no forbidden runtime imports exist:
    `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`,
    `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
    `relay_device_manager`, `device_status_logger`, `openweather`, `dess`.
11. Check no hardware calls exist:
    `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`,
    `toggle_device`, `set_numeric`, `update_status`, `mark_switched`,
    `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`.
12. Check no weather fetch/file/env/network/ML/logging calls exist:
    `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`,
    `os.getenv`, `requests`, `aiohttp`, `subprocess`, `logging`.
13. Check `__init__.py` exports `evaluate_policy_decision`.
14. Check runtime files are not modified (git diff check).
15. Print clear per-check output.
16. Exit 0 only when all checks pass.
17. Exit 1 if any check fails.

### GitHub Actions Integration

```yaml
      - name: 🔍 Policy decision engine check
        run: bash scripts/check-policy-decision-engine.sh
```

Add after the existing policy engine models check.

## 14. CURRENT_STATE.md Update

```
## PR 0018C — Pure Deterministic Policy Decision Engine
PR 0018C implements `evaluate_policy_decision()` in `app/control/policy_decision.py`.
The engine combines battery window, energy budget, load wattage, readiness, health,
schedule, weather adjustment, forecast strategy, and pond life-support context into
a single advisory PolicyDecisionResult. The engine is pure, deterministic, and
side-effect-free. It does not execute commands, propose actions, or wire into runtime.
No automation enabled. No ML control.
```

## 15. ROADMAP.md Update

```
- [x] PR 0018C — Pure deterministic policy decision engine
```

## 16. Future PR Boundary

PR 0018C explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Scenario matrix and regression tests | 0018D |
| Command proposal model | 0020 |
| Command queue / manual control API | 0019 |
| Wire policy decisions to command proposal | 0020 |
| Controlled execution with safety gates | 0021+ |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |
| Runtime wiring | Deferred |

## 17. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation |

## 18. Boundary Confirmations

- **Pure function only**: `evaluate_policy_decision` is deterministic, side-effect-free
- **No command proposal**: Deferred to 0020
- **No runtime wiring**: Not connected to any runtime component
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No config loading**: Does not read YAML, env vars, or files
- **No system clock**: Does not call `time.time` or `datetime.now`
- **No automation enabled**: Pump automation disabled per PR 0008
- **No ML control**: ML advisory is advice-only; ML control deferred per ADR-0003
- **Manual switch control preserved**: All switch methods unchanged
- **Pump automation remains disabled**: Per PR 0008
- **Docker/GitOps**: `build-and-deploy.yml` untouched; external GitOps boundary respected
- **All existing evaluators and models unchanged**: PR 0014–0018B frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
