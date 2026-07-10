# PR 0011 — Energy-Aware Control Policy Requirements

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `5563213a205ea3fcb2b38348a73bed29f1eaa502` |
| Branch | `git branch --show-current` | `0011-energy-aware-control-policy-requirements` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0011-energy-aware-control-policy-requirements` and working tree is clean.

## 2. Purpose

PR 0011 captures the actual product purpose of this project: **energy-aware device control
for a pond/home energy system**. It documents the policy requirements before any new
automation is implemented. It does NOT implement runtime changes.

## 3. Product Truth

1. The project's core purpose is energy-aware device control, not a generic switch API.
2. The system should switch devices ON when energy conditions are favorable (high battery
   voltage, solar generation, sunny forecast).
3. The system should switch devices OFF when energy conditions are unfavorable (low battery
   voltage, grid/mains operation, cloudy/rainy forecast).
4. Voltage is one primary signal — higher voltage generally means more available energy.
5. Grid/network/mains operation is a signal to reduce or disable discretionary loads.
6. Device readiness must be checked before switching ON (cooldown, voltage threshold,
   time window, current state).
7. Device health must be checked after switching and periodically (observed state matches
   expected state, stale status detection, repeated failure detection).
8. Weather forecast can adjust policy thresholds and allowed windows.
9. ML is useful for prediction and advice but must not directly control hardware yet.
10. An evening battery reserve target (~26.5V after sunset) protects capacity for night use.
11. Sunny days may allow more energy spending (more devices ON, longer windows).
12. Cloudy/rainy days should be more conservative (fewer devices, shorter windows, lower
    voltage thresholds).
13. Temperature may influence device policy (e.g., pump/filter rules based on water/air temp).
14. Some devices may be allowed always-on in good conditions (critical loads).
15. Some devices may be skipped entirely in poor conditions (discretionary loads).

## 4. Current Repository State (relevant to energy-aware control)

### 4.1 Existing Capabilities

| Capability | Where | Status |
|---|---|---|
| Battery voltage telemetry | `app/api.py` → `shared_state["battery_voltage"]` | Active |
| Working mode (grid/solar/battery) | `app/api.py` → `shared_state["working_mode"]` | Active |
| Weather temperature | `app/weather/openweather_service.py` → `shared_state["ambient_temp"]` | Active |
| Weather forecast | `app/weather/openweather_service.py` → `shared_state["forecast_hourly"]` | Active |
| Device ON/OFF state | `RelayChannelDevice.is_device_on()` | Active |
| Device min/max voltage thresholds | `RelayChannelDevice.min_volt`, `max_volt` | Active (legacy switch loop) |
| Device priority | `RelayChannelDevice.priority` | Active |
| Device cooldown | `RelayChannelDevice.can_switch()` / `time_delay` | Active |
| AC-grid detection | `SmartHomeController._switch_loop` checks `mode == "LINE MODE"` | Active |
| Night multiplier | `app/utils/time_utils.night_multiplier()` | Active (scales intervals at night) |
| SwitchableLoad domain type | `app/control/domain.py` | Passive |
| Relay-to-SwitchableLoad mapping | `app/control/relay_mapping.py` | Passive |
| RelayTuyaController switch methods | `app/tuya/relay_tuya_controller.py` | Active (4 methods) |
| Pump automation | gated by `PUMP_AUTOMATION_ENABLED` (default disabled) | Disabled by default |
| Manual switch loop | `SmartHomeController._switch_loop` | Active (voltage-based ON/OFF) |

### 4.2 Existing Gaps (addressed by this policy)

| Gap | Description |
|---|---|
| No explicit policy domain types | Policy decisions, readiness, health, schedule, weather-adjustment not modeled as types |
| No per-device policy configuration | No structured per-device policy beyond `min_volt`/`max_volt` |
| No weather-adjusted thresholds | Forecast not used to influence voltage thresholds or device availability |
| No evening reserve logic | No target reserve voltage after sunset |
| No seasonal profiles | Same behavior summer and winter |
| No configurable check intervals per time-of-day | `night_multiplier()` is hardcoded, not per-time-slot |
| No health check loop | No periodic verification that ON/OFF states match expectations |
| No readiness check separated from switching | `can_switch()` is embedded in `RelayChannelDevice`, not a policy function |
| No manual override tracking | No audit trail of manual switches vs. automated decisions |
| No fail-safe logic | No automatic OFF if a device keeps failing or state is stale |

### 4.3 Grep Evidence Summary

**Voltage/battery/grid/weather signals are available:**
Battery voltage (`battery_voltage`), working mode (`LINE MODE`), ambient temperature,
weather forecast data, cloud cover, rain forecast — all present in `shared_state`.

**Manual switch methods preserved:**
All 4 methods on `RelayTuyaController` + `RelayDeviceManager.toggle_device` +
`SmartHomeController._switch_loop` confirmed present.

**Domain types exist (passive):**
`SwitchableLoad`, `ControlCommand`, `CommandSource` (with `ML_ADVISORY`),
`PolicyDecision`, `TelemetryPoint` — all in `app/control/`.

**Pump automation disabled:**
`PUMP_AUTOMATION_ENABLED` gate confirmed in `run.py` and `SmartHomeController`.

**Time/schedule awareness exists:**
`night_multiplier()` in `app/utils/time_utils.py` scales intervals at night.
`TimescaleDataCollector` uses `sunrise_hour`/`sunset_hour` for power mode detection.
`smart_home_controller.py` has `_sleep()` with `night_multiplier()`.
`app/ml/ml_data_analyzer.py` uses `hour`, `day_of_week`, `month`, `is_daytime`.

## 5. Policy Signal Categories

The future policy engine must consume these signals:

| # | Signal | Source | Required |
|---|---|---|---|
| 1 | Battery voltage (V) | `shared_state["battery_voltage"]` | Yes |
| 2 | Voltage trend (rising/falling/stable) | Computed from voltage history | Nice-to-have |
| 3 | Charging/discharging status | `shared_state["battery_current_chg"]`, `shared_state["battery_current_dis"]` | Nice-to-have |
| 4 | Power source (solar/battery/grid) | `shared_state["working_mode"]` | Yes |
| 5 | Time of day (morning/day/evening/night) | `datetime.now().hour` | Yes |
| 6 | Season (summer/winter profile) | Configurable seasonal profile | Yes |
| 7 | Weather forecast (sun/cloud/rain) | `shared_state["forecast_hourly"]` | Yes |
| 8 | Ambient temperature | `shared_state["ambient_temp"]` | Yes |
| 9 | Device readiness state | Computed by readiness evaluator | Yes |
| 10 | Device observed ON/OFF state | `RelayChannelDevice.is_device_on()` | Yes |
| 11 | Device priority | Per-device policy config | Yes |
| 12 | Critical vs. discretionary classification | Per-device policy config | Yes |
| 13 | Desired evening battery reserve | Policy config | Yes |

## 6. Per-Device Policy Shape (Requirements Only)

Future per-device policy must include these parameters (not yet implemented):

| Parameter | Type | Description |
|---|---|---|
| `load_id` | str | Stable device ID |
| `priority` | int | Higher = switched on first, off last |
| `critical` | bool | Never automatically switched OFF unless critical |
| `discretionary` | bool | Can be skipped in poor conditions |
| `allowed_time_windows` | list | Time-of-day windows when switching is allowed |
| `min_voltage_on` | float | Minimum voltage to allow switch ON |
| `min_voltage_stay_on` | float | Minimum voltage to stay ON once switched |
| `voltage_off` | float | Voltage threshold to trigger switch OFF |
| `cooldown_seconds` | int | Minimum time between state changes |
| `readiness_check_interval` | int | How often to check readiness (seconds) |
| `health_check_interval` | int | How often to check health (seconds) |
| `weather_sensitive` | bool | Whether forecast affects this device |
| `season_profile` | str | Summer/winter policy variant |
| `allow_always_on_when_good` | bool | Keep ON when conditions are favorable |
| `skip_when_cloudy_or_rainy` | bool | Do not switch ON in poor weather |
| `manual_override` | str | `none`, `prefer_manual`, `lock_manual` |
| `fail_safe_off` | bool | Switch OFF if health check fails repeatedly |

## 7. Readiness Check Model

Readiness answers: **"Should this device be switched ON now?"**

Rules:
1. Readiness is computed from policy signals, NOT from hardware calls.
2. Readiness is NOT a hardware action by itself — it produces a boolean or recommendation.
3. Device is ready if:
   - It is currently OFF.
   - Its cooldown period has elapsed (minimum time since last switch).
   - Current time is within its allowed time windows.
   - Battery voltage is at or above `min_voltage_on`.
   - Power source is not grid/mains (unless device is configured grid-allowed).
   - Weather forecast does not indicate sustained poor conditions (if weather_sensitive).
   - Evening reserve target is not at risk (if applicable).
   - Materialized seasonal profile permits it.
4. Device is NOT ready if any of the above conditions fail.
5. Readiness must be cheap to compute (pure data, no I/O).

## 8. Health Check Model

Health answers: **"Is the device behaving as expected?"**

Rules:
1. Health checks compare observed state to expected state.
2. If a device was commanded ON but is observed OFF after a reasonable delay → potential fault.
3. If a device was commanded OFF but is observed ON → potential fault.
4. If device status is stale (last update older than threshold) → potential fault.
5. If repeated switch commands to a device fail consecutively → stop attempting, flag for operator.
6. Health checks must NOT blindly loop switching (escalation limits required).
7. Health check produces a health status: `healthy`, `suspect`, `unhealthy`, `unknown`.
8. Health check must be safe to run frequently (read-only).

## 9. Time-of-Day Scheduling Model

Check frequencies and behavior should vary by time slot:

| Slot | Hours (example) | Behavior |
|---|---|---|
| Morning | 06:00–12:00 | Conservative start: devices may turn ON if solar generation is expected |
| Day | 12:00–18:00 | More permissive: devices can use excess solar energy |
| Evening | 18:00–22:00 | Most sensitive: monitor battery voltage closely, preserve evening reserve |
| Night | 22:00–06:00 | Conservative: minimize switching, preserve battery |
| Dawn/dusk transitions | Configurable | Gradual policy adjustment |

Evening may need more frequent checks (e.g., every 60 seconds) because energy reserve
and load control are more sensitive during this window.

## 10. Seasonal Scheduling Model

| Season | Behavior |
|---|---|
| Summer | More daylight, more solar generation → more permissive |
| Winter | Less daylight, less solar → more conservative |
| Spring/Autumn | Transitional profiles, configurable |

Seasons should be configurable (not hardcoded to hemisphere months).

## 11. Weather Forecast Adjustment Model

| Forecast | Policy Adjustment |
|---|---|
| Sunny | Allow more load usage earlier. Raise voltage thresholds. Allow more devices ON. |
| Cloudy | Reduce device count. Lower voltage thresholds. Conserve more. |
| Rainy | Most conservative. Skip discretionary devices. Protect evening reserve. |
| Mixed / unknown | Use conservative defaults. |

Weather forecast is advisory input, NOT direct hardware action.
Forecast failures (API down, stale data) must fall back to conservative defaults.

## 12. Battery Reserve Model

1. Evening reserve target: ~26.5V after sunset.
2. Policy should avoid spending battery charge below this target in the evening period.
3. If forecast and voltage trend indicate good solar charging, the policy may allow more
   spending earlier in the day.
4. If forecast is poor or voltage is trending down, policy should conserve more aggressively.
5. Reserve threshold must be configurable (not hardcoded to 26.5V) in the future.
6. The reserve model is advisory — it influences readiness, it does not enforce a hard cutoff.

## 13. ML Advisory Model

1. ML may predict energy availability (solar generation forecast).
2. ML may predict consumption patterns.
3. ML may recommend threshold adjustments (voltage targets, allowed devices).
4. ML may recommend load usage windows.
5. ML advisory produces recommendations (PolicyDecision), NOT hardware commands.
6. ML must run in advisory/shadow mode — recommendations are logged and visible but not
   automatically executed.
7. Human-readable deterministic policy and fallback must exist before ML control.
8. ML control remains deferred behind separate safety-reviewed gates (per ADR-0003).
9. PR 0011 does NOT modify any ML code.

## 14. Safety Boundaries

1. Policy decisions must be separate from hardware execution (policy produces
   `PolicyDecision`, a separate executor dispatches commands).
2. Readiness checks must NOT switch devices directly.
3. Health checks must NOT blindly loop switching (escalation limits required).
4. Forecast input must fail safe (stale/missing forecast → conservative defaults).
5. ML advisory must NOT directly control hardware.
6. Manual override must remain possible and respected by policy.
7. Critical loads must be protected (never automatically OFF).
8. Discretionary loads should be shed first in adverse conditions.
9. Grid/mains/network operation should prefer conservation unless explicitly configured
   otherwise for specific loads.
10. Pump-specific automation remains obsolete and disabled by default (per PR 0008).

## 15. Out of Scope for PR 0011

- Implementation of policy domain types, readiness evaluator, health evaluator,
  schedule profiles, weather adjustment, or policy decision engine.
- Any runtime code changes (run.py, app/service/, app/devices/, app/tuya/, etc.).
- Changes to SmartHomeController, RelayTuyaController, DeviceInitializer, monitoring,
  ML, or weather services.
- Changes to Docker, CI/CD, deployment, or external GitOps.

## 16. Staged Implementation Roadmap

| PR | Title | Scope |
|---|---|---|
| 0011 | Energy-aware control policy requirements | This PR — documentation and requirements only |
| 0012 | Passive energy policy domain types | Add PolicyConfig, ReadinessResult, HealthStatus, ScheduleProfile, WeatherAdjustment as dataclasses/enums in `app/control/policy.py` |
| 0013 | Static policy configuration example | Add example YAML/JSON with per-device policy shape (no secrets) |
| 0014 | Readiness evaluator | Pure function readiness check, no hardware execution |
| 0015 | Health evaluator | Pure function health check, no hardware execution |
| 0016 | Schedule profile model | Time-of-day + seasonal interval and behavior profiles |
| 0017 | Weather adjustment evaluator | Forecast → voltage/window adjustment calculator |
| 0018 | Deterministic policy decision engine | Combines readiness + health + schedule + weather → PolicyDecision, no hardware exec |
| 0019 | Manual control API or command queue | Accept and queue ControlCommand from operators |
| 0020 | Wire policy decisions to command proposal | PolicyDecision → proposed ControlCommand (human-in-the-loop) |
| 0021+ | Controlled execution with safety gates | Policy → execution with limits, audit, fail-safe |
| Later | ML advisory integration | ML → PolicyDecision in shadow mode |
| Much later | ML control | ML → PolicyDecision → automatic execution (after safety gates per ADR-0003) |

## 17. Expected Implementation Scope for PR 0011 Coder Phase

The plan allows coder to edit only:

| File | Action |
|---|---|
| `.project-memory/ENERGY_AWARE_CONTROL_POLICY.md` | Create — full policy requirements document (content summarized in this PLAN.md) |
| `.project-memory/CURRENT_STATE.md` | Edit — add PR 0011 section |
| `.project-memory/ROADMAP.md` | Edit — mark PR 0011 in roadmap |
| `scripts/check-energy-aware-control-policy.sh` | Create — static validation script |
| `.github/workflows/validate.yml` | Edit — add one validation step |
| `.project-memory/pr/0011-energy-aware-control-policy-requirements/CODER_REPORT.txt` | Create |

No runtime edits allowed in PR 0011.

## 18. Static Validation Script

`scripts/check-energy-aware-control-policy.sh` must verify:

1. `.project-memory/ENERGY_AWARE_CONTROL_POLICY.md` exists.
2. Required concept phrases exist:
   - `energy-aware control`
   - `voltage`
   - `switch ON`
   - `switch OFF`
   - `readiness`
   - `health`
   - `weather forecast`
   - `26.5` (evening reserve target)
   - `evening reserve`
   - `ML advisory`
   - `ML control`
   - `manual override`
   - `deterministic fallback`
3. The document states runtime automation is not enabled by PR 0011.
4. The document states external GitOps boundary remains unchanged.

Read-only, no network, no secrets, no mutations. Exit 0 on pass, 1 on failure.

## 19. Validation Commands (for coder phase)

```bash
# 1. Verify policy document exists
test -f .project-memory/ENERGY_AWARE_CONTROL_POLICY.md && echo "EXISTS" || echo "MISSING"

# 2. Run the static validation script
bash scripts/check-energy-aware-control-policy.sh
echo "Exit code: $?"

# 3. Run all existing validations
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
bash scripts/check-repo-safety.sh
bash scripts/check-project-memory.sh
python scripts/validate-yaml.py
bash scripts/check-image-publishing-boundary.sh
bash scripts/check-platform-control-redesign.sh
bash scripts/check-pump-automation-disabled.sh
bash scripts/check-generic-control-domain.sh
bash scripts/check-relay-switchable-load-mapping.sh

# 4. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0011-energy-aware-control-policy-requirements/PLAN.md
git diff --name-only HEAD -- .project-memory/pr/0011-energy-aware-control-policy-requirements/PLAN_REVIEW.yaml

# 5. Verify no runtime files modified
git diff --name-only HEAD -- run.py app/service/ app/devices/ app/tuya/ app/monitoring/ app/ml/ app/weather/ Dockerfile docker-compose.yml .github/workflows/build-and-deploy.yml

# 6. Verify coder artifact exists
test -f .project-memory/pr/0011-energy-aware-control-policy-requirements/CODER_REPORT.txt && echo "CODER_REPORT: OK"
```

## 20. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 21. Boundary Confirmations

- **Documentation only**: PR 0011 produces requirements documentation, a validation script,
  and minor project-memory updates. No runtime code is changed.
- **No automation enabled**: Policy evaluation is not wired into any runtime component.
- **No ML control enabled**: Per ADR-0003. Deferred.
- **Manual switch control preserved**: `_switch_loop` and all 4 switch methods unchanged.
- **Pump automation remains disabled**: Per PR 0008.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
