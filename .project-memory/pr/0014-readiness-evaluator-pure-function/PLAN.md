# PR 0014 — Readiness Evaluator, Pure Function Only

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `2c83bff624c216dc52f383a9b037c1ccce647e09` |
| Branch | `git branch --show-current` | `0014-readiness-evaluator-pure-function` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0014-readiness-evaluator-pure-function` and working tree is clean.

## 2. Purpose

PR 0014 adds a **pure readiness evaluator** — a deterministic, side-effect-free function that
determines whether a specific device load is allowed to be switched ON at the current moment,
using only data already present in `ReadinessInput`.

The evaluator is **not wired into runtime behavior**. It does not switch devices. It does not
call Tuya, DESS, OpenWeather, ML, or any runtime service. It does not read config files,
environment variables, or system time. It is pure data-in / data-out.

## 3. Product Context

1. The project's core purpose is **energy-aware device control**.
2. The system should decide when selected loads are ready to be switched ON based on voltage,
   power source, time of day, season, weather forecast, cooldown, reserve policy, and
   per-device policy.
3. **Readiness** means "is this load allowed to be switched ON now?"
4. Readiness is **not** health. Readiness is **not** hardware execution.
5. Readiness must **not** switch devices. Readiness must **not** call runtime services.
6. Evening reserve around 26.5V after sunset is important.
7. Manual relay/switch ON/OFF remains available and unchanged.
8. Pump automation remains obsolete and disabled by default (PR 0008).
9. ML advisory may be used later; ML control remains disabled.

## 4. Current Repository State

| Capability | File | Status |
|---|---|---|
| Generic control domain types | `app/control/domain.py` | Passive (PR 0009) |
| Relay-to-SwitchableLoad mapping | `app/control/relay_mapping.py` | Passive (PR 0010) |
| Energy-aware control policy requirements | `.project-memory/ENERGY_AWARE_CONTROL_POLICY.md` | Documented (PR 0011) |
| Energy policy domain types (17) | `app/control/energy_policy.py` | Passive (PR 0012) |
| Static energy policy config example | *(pending PR 0013)* | Not yet created |
| **Readiness evaluator (pure function)** | `app/control/readiness.py` | **This PR** |

### 4.1 Relevant Domain Types (from PR 0012)

The following types from `app/control/energy_policy.py` are consumed by the readiness evaluator:

- `ReadinessInput` — combined context for readiness evaluation
- `ReadinessResult` — output with `ready: bool`, `reason: str`, `decision: EnergyPolicyDecision`
- `DeviceEnergyPolicy` — per-device policy fields (voltage thresholds, cooldown, weather skip, etc.)
- `EnergyPolicyContext` — aggregated context (voltage, weather, time-of-day, season, reserve)
- `VoltageSnapshot` — current voltage with timestamp and power source
- `WeatherForecastSignal` — forecast condition (SUNNY, CLOUDY, RAINY, etc.)
- `BatteryReservePolicy` — evening reserve target (~26.5V)
- `EnergyPolicyDecision` — vocabulary: `ALLOW_ON`, `PREFER_OFF`, `FORCE_OFF`, `HOLD`, `NO_ACTION`
- `TimeOfDay` — `MORNING`, `DAY`, `EVENING`, `NIGHT`
- `PowerSource` — `SOLAR`, `BATTERY`, `GRID`, `MAINS`, `NETWORK`
- `WeatherCondition` — `SUNNY`, `CLOUDY`, `RAINY`, `SNOWY`, `STORM`
- `LoadClass` — `CRITICAL`, `DISCRETIONARY`

### 4.2 Grep Evidence Summary

**ReadinessInput/ReadinessResult already exist** in `app/control/energy_policy.py` (passive data).

**DeviceEnergyPolicy fields available:**
- `minimum_voltage_to_switch_on`, `cooldown_after_switch_seconds`, `skip_when_cloudy_or_rainy`,
  `allow_always_on_when_good_conditions`, `manual_override_allowed`, `fail_safe_off` — all defined.

**Forbidden hardware calls are absent** from all `app/control/` modules.

**Pump automation remains disabled** (`PUMP_AUTOMATION_ENABLED` defaults to false).

**Manual switch control preserved** (`switch_on_device`, `switch_off_device`, `toggle_device` all
present in runtime code).

**No ML control enabled** — ML code remains advisory/deferred.

## 5. Relationship to PR 0011, PR 0012, and PR 0013

| PR | Title | Relationship to PR 0014 |
|---|---|---|
| 0011 | Energy-aware control policy requirements | Defines readiness rules, voltage thresholds, cooldown, weather sensitivity, evening reserve, fail-safe, and per-device policy shape. PR 0014 implements the readiness evaluation logic. |
| 0012 | Passive energy policy domain types | Provides `ReadinessInput`, `ReadinessResult`, `DeviceEnergyPolicy`, `EnergyPolicyContext`, `VoltageSnapshot`, `WeatherForecastSignal`, `BatteryReservePolicy`, `EnergyPolicyDecision`, `TimeOfDay`, `PowerSource`, `WeatherCondition`, `LoadClass` — the vocabulary the readiness evaluator consumes and produces. |
| 0013 | Static energy policy config example | Defines example per-device policy values. PR 0014 does **not** load config files. The evaluator accepts `DeviceEnergyPolicy` as a parameter; the config loader is deferred. |

## 6. Readiness Evaluator Goals

1. **Pure function** — deterministic, no side effects, same input produces same output.
2. **No hardware execution** — must never call switch methods or Tuya/DESS.
3. **No runtime wiring** — must not be called by `SmartHomeController`, `RelayTuyaController`,
   `DeviceInitializer`, weather service, or ML code in PR 0014.
4. **No config loading** — must not read files, env vars, or network resources.
5. **No system clock calls** — uses `context.voltage.timestamp` for cooldown calculations.
6. **Stable, testable reason strings** — deterministric classification of blocking conditions.
7. **Clear safety boundaries** — provides a decision vocabulary but does not execute it.
8. **Composable** — stands alone; future PRs will compose it into a decision pipeline.

## 7. Readiness Definition

A device load is **ready to be switched ON** when **none** of these blocking conditions apply:

1. `load_id` is empty or invalid.
2. `fail_safe_off` is true and required context is missing or invalid.
3. Current voltage is below `minimum_voltage_to_switch_on` when that threshold is set.
4. Power source is `GRID`, `MAINS`, or `NETWORK` and load class is `DISCRETIONARY`, unless
   future policy explicitly allows it.
5. `context.time_of_day` is not in `allowed_time_windows` when `allowed_time_windows` is non-empty.
6. `cooldown_after_switch_seconds` is active based on `context.voltage.timestamp` minus
   `last_switch_timestamp`.
7. `skip_when_cloudy_or_rainy` is true and weather condition is `CLOUDY`, `RAINY`, `STORM`,
   or `SNOWY`.
8. `context.time_of_day` is `EVENING` or `NIGHT`, load class is `DISCRETIONARY`, and voltage is
   at or below `reserve.evening_reserve_voltage`.

A device load is **ready** when none of these block and required input is valid.

## 8. Required Module Design

### Module Location

**Create:** `app/control/readiness.py`

**May update:** `app/control/__init__.py` (export `evaluate_readiness`)

### Module Dependencies

The module must use **only**:
- Python standard library (`typing`)
- `app.control.energy_policy` types: `ReadinessInput`, `ReadinessResult`, `DeviceEnergyPolicy`,
  `EnergyPolicyContext`, `VoltageSnapshot`, `WeatherForecastSignal`, `BatteryReservePolicy`,
  `EnergyPolicyDecision`, `TimeOfDay`, `PowerSource`, `WeatherCondition`, `LoadClass`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `openweather`, `dess`
- Any runtime service, config reader, network client, or hardware adapter

### Module Requirements

1. Import-safe — no side effects at module import time.
2. No env var reads.
3. No config file reads.
4. No network connections.
5. No hardware calls.
6. No file mutations.
7. No `time.time` or `datetime.now` calls.
8. No device switching.
9. No scheduling.
10. No loading of `examples/energy_policy.example.yaml`.
11. No health evaluation (deferred to PR 0015).
12. No full policy decision engine (deferred to PR 0018).

## 9. Required Function Design

### Public API

```python
def evaluate_readiness(readiness_input: ReadinessInput) -> ReadinessResult
```

### Semantics

1. Accept only `ReadinessInput`.
2. Return `ReadinessResult`.
3. Use only data already present in `ReadinessInput`, `DeviceEnergyPolicy`, `EnergyPolicyContext`,
   `VoltageSnapshot`, `WeatherForecastSignal`, and `BatteryReservePolicy`.
4. Not read current time from system clock.
5. Use `context.voltage.timestamp` as the evaluation timestamp for cooldown calculations.
6. Be deterministic: same input returns same output.
7. Have no side effects.
8. Never switch a device.
9. Never call hardware.
10. Never call runtime services.
11. Never call ML.
12. Never load YAML/config files.

### Private Helpers

The module may define small private helper functions inside `app/control/readiness.py` if they
are pure and local. Suggested private helpers:
- `_check_voltage_threshold(...)` → returns `(blocked: bool, reason: str)`
- `_check_power_source_conservation(...)` → returns `(blocked: bool, reason: str)`
- `_check_time_window(...)` → returns `(blocked: bool, reason: str)`
- `_check_cooldown(...)` → returns `(blocked: bool, reason: str)`
- `_check_weather_skip(...)` → returns `(blocked: bool, reason: str)`
- `_check_evening_reserve(...)` → returns `(blocked: bool, reason: str)`
- `_check_fail_safe_off(...)` → returns `(blocked: bool, reason: str)`

Each helper must:
- Accept only data already in the input (no system calls, no globals)
- Return a `tuple[bool, str]` where `True` means "blocked (not ready)" and the string is the reason
- Be pure and deterministic

### Evaluation Flow

```
evaluate_readiness(readiness_input):
  1. If load_id is empty/missing → NOT READY, "invalid-load-id", NO_ACTION
  2. If fail_safe_off is True and context is missing/invalid → NOT READY, "context-fail-safe", NO_ACTION
  3. If voltage < minimum_voltage_to_switch_on → NOT READY, "below-switch-on-voltage", PREFER_OFF|FORCE_OFF
  4. If power_source in (GRID, MAINS, NETWORK) and load_class is DISCRETIONARY
     → NOT READY, "grid-or-mains-conservation", PREFER_OFF
  5. If allowed_time_windows non-empty and time_of_day not in them
     → NOT READY, "outside-allowed-time-window", PREFER_OFF
  6. If cooldown is active (timestamp - last_switch < cooldown_seconds)
     → NOT READY, "cooldown-active", PREFER_OFF
  7. If skip_when_cloudy_or_rainy and weather condition in (CLOUDY, RAINY, STORM, SNOWY)
     → NOT READY, "weather-skip", PREFER_OFF
  8. If time_of_day in (EVENING, NIGHT) and load_class is DISCRETIONARY
     and voltage <= reserve.evening_reserve_voltage
     → NOT READY, "evening-reserve-protected", FORCE_OFF
  9. Otherwise → READY, "ready", ALLOW_ON
```

## 10. Required Reason Strings

The evaluator must produce stable, testable reason strings:

| Reason String | Meaning | Decision |
|---|---|---|
| `"ready"` | No blocking conditions; load may be switched ON | `ALLOW_ON` |
| `"invalid-load-id"` | Load ID is empty, None, or whitespace-only | `NO_ACTION` |
| `"context-fail-safe"` | `fail_safe_off` is True but required context is missing/invalid | `NO_ACTION` |
| `"below-switch-on-voltage"` | Voltage is below `minimum_voltage_to_switch_on` | `PREFER_OFF` or `FORCE_OFF` |
| `"grid-or-mains-conservation"` | Discretionary load during grid/mains/network power | `PREFER_OFF` |
| `"outside-allowed-time-window"` | Current time period not in `allowed_time_windows` | `PREFER_OFF` |
| `"cooldown-active"` | Cooldown period has not yet elapsed since last switch | `PREFER_OFF` |
| `"weather-skip"` | Weather condition is poor and device is configured to skip | `PREFER_OFF` |
| `"evening-reserve-protected"` | Voltage at/below evening reserve during evening/night | `FORCE_OFF` |

The exact name strings are stable and must be checked by the validation script.

## 11. Determinism and Purity Requirements

1. The evaluator is a **pure function**.
2. It reads **no files**.
3. It reads **no env vars**.
4. It makes **no network calls**.
5. It imports **no runtime services**.
6. It calls **no hardware methods**.
7. It mutates **no input objects**.
8. It does **not** call `time.time` or `datetime.now`.
9. It does **not** generate UUIDs.
10. It does **not** log.
11. It does **not** depend on global state.
12. **Same input produces same output** — always.

## 12. Safety Boundaries

1. The evaluator is pure and deterministic.
2. The evaluator must **not** execute hardware calls.
3. The evaluator must **not** switch devices.
4. The evaluator must **not** be read by runtime.
5. The evaluator must **not** read configs.
6. The evaluator must **not** load `examples/energy_policy.example.yaml`.
7. The evaluator must **not** change startup behavior.
8. The evaluator must **not** change pump automation behavior.
9. The evaluator must **not** change manual switch behavior.
10. The evaluator must **not** enable automation or ML control.
11. ML advisory remains advice only.
12. External GitOps boundary remains unchanged.
13. The evaluator uses `context.voltage.timestamp` for cooldown — it does not call the system clock.

## 13. Out of Scope for PR 0014

PR 0014 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Health evaluator (pure function) | 0015 |
| Schedule/season profile model | 0016 |
| Weather adjustment evaluator | 0017 |
| Deterministic policy decision engine (no hardware exec) | 0018 |
| Runtime config loader | 0018+ |
| Policy config schema enforcement | 0018+ |
| Command proposal layer | 0019 |
| Manual control API or command queue | 0019 |
| Wire policy decisions to command proposal | 0020 |
| Controlled execution with safety gates | 0021+ |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |
| External GitOps changes | Never (separate repo) |
| Runtime wiring | Deferred |

## 14. Allowed Implementation Files

The following files may be edited by the coder agent:

| File | Action |
|---|---|
| `app/control/readiness.py` | **Create** — pure readiness evaluator module |
| `app/control/__init__.py` | **Edit** — export `evaluate_readiness` function |
| `scripts/check-readiness-evaluator.sh` | **Create** — static validation script (see Section 16) |
| `.github/workflows/validate.yml` | **Edit** — add one validation step for `scripts/check-readiness-evaluator.sh` |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0014 section (see Section 17) |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0014 in roadmap (see Section 18) |
| `.project-memory/pr/0014-readiness-evaluator-pure-function/CODER_REPORT.txt` | **Create** — coder report |

## 15. Forbidden Implementation Files

The coder must **not** edit these files:

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
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 16. Static Validation Script

### File: `scripts/check-readiness-evaluator.sh`

The script must:

1. Use only static local repository files.
2. Not require network access.
3. Not query Docker Hub.
4. Not query GitHub API.
5. Not query Kubernetes.
6. Not query ArgoCD.
7. Not require Tuya or DESS.
8. Not require secrets.
9. Not mutate files.
10. Verify `app/control/readiness.py` exists.
11. Verify `evaluate_readiness` function exists.
12. Verify `ReadinessInput` type is used in function signature.
13. Verify `ReadinessResult` is the return type.
14. Verify `EnergyPolicyDecision` is used in the module.
15. Verify required reason strings exist:
    - `"ready"` or `ready`
    - `"invalid-load-id"` or `invalid-load-id`
    - `"below-switch-on-voltage"` or `below-switch-on-voltage`
    - `"grid-or-mains-conservation"` or `grid-or-mains-conservation`
    - `"outside-allowed-time-window"` or `outside-allowed-time-window`
    - `"cooldown-active"` or `cooldown-active`
    - `"weather-skip"` or `weather-skip`
    - `"evening-reserve-protected"` or `evening-reserve-protected`
    - An empty-load-id or context-fail-safe variant (e.g. `"context-fail-safe"`, `"invalid-load-id"`)
16. Verify forbidden imports are absent from `app/control/readiness.py`:
    - `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
    - `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`, `relay_device_manager`
    - `openweather`, `dess`
17. Verify forbidden hardware/action calls absent:
    - `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`, `toggle_device`
    - `set_numeric`, `update_status`, `mark_switched`
    - `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`
18. Verify forbidden impurity calls absent:
    - `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`, `os.getenv`,
      `requests`, `aiohttp`, `subprocess`
19. Verify runtime files were not modified (git diff check against known runtime paths).
20. Print clear per-check output.
21. Exit 0 only when all checks pass.
22. Exit 1 if any check fails.

### GitHub Actions Integration

Add one step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Readiness evaluator check
        run: bash scripts/check-readiness-evaluator.sh
```

This step must be added **after** the existing energy policy domain types check.

## 17. CURRENT_STATE.md Update

Add a concise PR 0014 section to `.project-memory/CURRENT_STATE.md`:

```
## PR 0014 — Pure Readiness Evaluator
PR 0014 adds a pure deterministic readiness evaluator in `app/control/readiness.py`.
The evaluator is not runtime-wired. It does not switch devices. Runtime automation
is not enabled. Manual relay/switch ON/OFF remains unchanged. Pump automation remains
disabled by default from PR 0008. ML control remains disabled.
```

Do not rewrite unrelated sections.

## 18. ROADMAP.md Update

Mark PR 0014 in `.project-memory/ROADMAP.md`:

```
- [x] PR 0014 — Readiness evaluator, pure function only
```

Update under "Phase 2b: Platform Control Redesign — Staged Backend Refactor".

Do not rewrite unrelated sections.

## 19. Validation Commands (for coder phase)

```bash
# 1. Verify module exists
test -f app/control/readiness.py && echo "EXISTS" || echo "MISSING"

# 2. Run the static validation script
bash scripts/check-readiness-evaluator.sh
echo "Exit code: $?"

# 3. Run all existing validations
python3 -m compileall -q .
bash scripts/check-repo-safety.sh
bash scripts/check-project-memory.sh
python3 scripts/validate-yaml.py
bash scripts/check-image-publishing-boundary.sh
bash scripts/check-platform-control-redesign.sh
bash scripts/check-pump-automation-disabled.sh
bash scripts/check-generic-control-domain.sh
bash scripts/check-relay-switchable-load-mapping.sh
bash scripts/check-energy-aware-control-policy.sh
bash scripts/check-energy-policy-domain-types.sh

# 4. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0014-readiness-evaluator-pure-function/PLAN.md
git diff --name-only HEAD -- .project-memory/pr/0014-readiness-evaluator-pure-function/PLAN_REVIEW.yaml

# 5. Verify no runtime files modified
git diff --name-only HEAD -- run.py app/service/ app/devices/ app/tuya/ app/monitoring/ app/ml/ app/weather/ Dockerfile docker-compose.yml .github/workflows/build-and-deploy.yml

# 6. Verify coder artifact exists
test -f .project-memory/pr/0014-readiness-evaluator-pure-function/CODER_REPORT.txt && echo "CODER_REPORT: OK"
```

## 20. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are **LOCKED** after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |

### Artifact Layout

```
.project-memory/pr/0014-readiness-evaluator-pure-function/
├── PLAN.md              ← This file (locked after approval)
├── PLAN_REVIEW.yaml     ← Plan-review artifact (locked after approval)
├── CODER_REPORT.txt     ← Coder artifacts (created by coder)
└── PRECOMMIT_REVIEW.yaml ← Precommit-review artifact
```

## 21. Boundary Confirmations

- **Pure function only**: `evaluate_readiness` is deterministic, side-effect-free.
- **No runtime wiring**: Not connected to `SmartHomeController`, `RelayTuyaController`,
  `DeviceInitializer`, weather service, or ML code.
- **No hardware execution**: Does not call any switch/device/Tuya method.
- **No config loading**: Does not read YAML, env vars, or files.
- **No system clock**: Uses `context.voltage.timestamp` for cooldown.
- **No automation enabled**: Pump automation disabled per PR 0008. No new automation added.
- **No ML control enabled**: ML advisory is advice-only. ML control deferred per ADR-0003.
- **Manual switch control preserved**: `_switch_loop`, `toggle_device`, all 4 switch methods unchanged.
- **Pump automation remains disabled**: Per PR 0008.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **Health evaluation deferred**: PR 0015.
- **Policy decision engine deferred**: PR 0018.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
