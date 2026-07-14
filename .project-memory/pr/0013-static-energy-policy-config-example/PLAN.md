# PR 0013 — Static Energy Policy Config Example

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `2c83bff624c216dc52f383a9b037c1ccce647e09` |
| Branch | `git branch --show-current` | `0013-static-energy-policy-config-example` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0013-static-energy-policy-config-example` and working tree is clean.

## 2. Purpose

PR 0013 adds a **static, non-runtime, no-secret YAML example** showing how an energy-aware
control policy configuration might look for this system. It follows the passive policy
domain types introduced in PR 0012 and the requirements documented in PR 0011.

The example is documentation only. It is NOT loaded by any runtime code. It does NOT
contain real device IDs, Tuya credentials, API keys, secrets, or production values.

## 3. Product Context

1. Core purpose: energy-aware device control based on voltage, power source, time of day,
   season, weather forecast, and device policy.
2. Example config illustrates per-device thresholds, readiness/health defaults, schedule
   profiles, weather adjustments, battery reserve, and ML advisory boundaries.
3. Manual relay/switch ON/OFF remains available (per PR 0008).
4. Pump automation remains obsolete and disabled by default (per PR 0008).
5. ML control remains disabled. ML advisory is explicitly documented as `false` in the example.

## 4. Relationship to PR 0011 and PR 0012

- PR 0011 documented energy-aware control policy requirements (voltage-based control,
  readiness, health, scheduling, weather, reserve, ML boundaries).
- PR 0012 introduced 17 passive energy policy domain types (PowerSource, TimeOfDay, Season,
  WeatherCondition, LoadClass, DevicePriority, VoltageSnapshot, WeatherForecastSignal,
  BatteryReservePolicy, DeviceEnergyPolicy, ReadinessInput, ReadinessResult, HealthInput,
  HealthStatus, HealthCheckResult, EnergyPolicyContext, EnergyPolicyDecision).
- PR 0013 adds a static YAML example that could be parsed into those types in a future PR
  (but does NOT implement config parsing).

## 5. Static Config Example Goals

1. Provide a concrete illustration of what an energy policy config looks like.
2. Demonstrate per-device policy parameters matching `DeviceEnergyPolicy` fields.
3. Demonstrate global battery reserve, schedule profiles, weather adjustment, readiness
   defaults, health defaults, manual/ML override levers.
4. Use only fake placeholder load IDs (`example-light`, `example-filter`, `example-fan`).
5. Contain zero secrets.
6. NOT be loaded or consumed by any runtime component in PR 0013.

## 6. Example File Path

**Create**: `examples/energy_policy.example.yaml`

**Optionally create**: `.project-memory/ENERGY_POLICY_CONFIG_EXAMPLE.md` as companion
documentation describing the example structure.

## 7. Required YAML Content

### 7.1 Metadata
```yaml
energy_aware_policy_version: "0.1.0"
runtime_loaded: false
example_only: true
description: "Example energy-aware device control policy config — no secrets, no real device IDs"
```

### 7.2 Global Policy
```yaml
global_policy:
  default_power_source_behavior: "conserve_on_grid"
  grid_or_mains_conservation: true
  default_min_voltage_on: 24.0
  default_min_voltage_stay_on: 23.5
  default_voltage_off: 22.0
```

### 7.3 Battery Reserve
```yaml
battery_reserve:
  evening_reserve_voltage: 26.5
  reserve_priority: 10
  enabled: true
```

### 7.4 Schedule Profiles
```yaml
schedule_profiles:
  default:
    morning: { start: "06:00", end: "12:00", check_interval_seconds: 300 }
    day: { start: "12:00", end: "18:00", check_interval_seconds: 300 }
    evening: { start: "18:00", end: "22:00", check_interval_seconds: 60 }
    night: { start: "22:00", end: "06:00", check_interval_seconds: 600 }
  summer:
    day: { start: "06:00", end: "20:00", check_interval_seconds: 180 }
  winter:
    day: { start: "09:00", end: "16:00", check_interval_seconds: 600 }
```

### 7.5 Weather Adjustment
```yaml
weather_adjustment:
  sunny:
    voltage_multiplier: 1.0
    allow_additional_loads: true
  cloudy:
    voltage_multiplier: 0.95
    allow_additional_loads: false
  rainy:
    voltage_multiplier: 0.9
    skip_discretionary: true
  unknown:
    voltage_multiplier: 0.95
    allow_additional_loads: false
```

### 7.6 Readiness Defaults
```yaml
readiness_defaults:
  default_check_interval_seconds: 180
  default_cooldown_seconds: 120
```

### 7.7 Health Defaults
```yaml
health_defaults:
  default_check_interval_seconds: 300
  consecutive_failure_limit: 3
  stale_status_threshold_seconds: 600
  auto_force_off_after_failures: true
```

### 7.8 Manual Override
```yaml
manual_override:
  allowed: true
  prefer_manual: false
  lock_manual: false
```

### 7.9 ML Advisory
```yaml
ml_advisory:
  ml_advisory_enabled: false
  ml_control_enabled: false
  deterministic_fallback: true
  description: "ML advisory is not enabled in this example. ML control remains deferred per safety policy."
```

### 7.10 Loads (Fake Examples)
```yaml
loads:
  - load_id: example-light
    display_name: "Example Light"
    load_class: discretionary
    priority: low
    allowed_time_windows:
      - [evening, night]
    min_voltage_on: 25.0
    min_voltage_stay_on: 24.5
    voltage_off: 23.0
    cooldown_seconds: 30
    weather_sensitive: false
    allow_always_on_when_good: false
    skip_when_cloudy_or_rainy: false
    fail_safe_off: true

  - load_id: example-filter
    display_name: "Example Filter"
    load_class: critical
    priority: high
    allowed_time_windows: []
    min_voltage_on: 24.0
    min_voltage_stay_on: 23.0
    voltage_off: 21.0
    cooldown_seconds: 300
    weather_sensitive: false
    allow_always_on_when_good: true
    skip_when_cloudy_or_rainy: false
    fail_safe_off: false

  - load_id: example-fan
    display_name: "Example Fan"
    load_class: discretionary
    priority: normal
    allowed_time_windows:
      - [day, evening]
    min_voltage_on: 26.0
    min_voltage_stay_on: 25.5
    voltage_off: 24.0
    cooldown_seconds: 60
    weather_sensitive: true
    allow_always_on_when_good: false
    skip_when_cloudy_or_rainy: true
    fail_safe_off: true
```

## 8. No-Secret Requirements

The example must NOT contain any of these patterns:
- `api_key`, `token`, `secret`, `password`, `credential`
- `tuya_device_id`, `device_id` (only `load_id` with fake prefixes)
- `local_ip`, `kubeconfig`, `bearer`, `private_key`
- Any values from real `devices.yaml`, `devices_prod.yaml`, or `config.json`

All load IDs use the `example-` prefix. No real device identifiers are used.

## 9. Runtime Isolation Requirements

1. The example YAML is NOT loaded by any runtime code.
2. The example is NOT loaded by any runtime code (not runtime-loaded).
3. No config loader is added.
4. No evaluator consumes this file.
5. No scheduler consumes this file.
6. No Tuya call consumes this file.
7. No ML code consumes this file.
8. No environment variable points to this file.
9. No automation is enabled.
10. No device switching behavior changes.

## 10. Files Coder May Edit

| File | Action |
|---|---|
| `examples/energy_policy.example.yaml` | Create — static no-secret example |
| `scripts/check-energy-policy-config-example.sh` | Create — static validation script |
| `.github/workflows/validate.yml` | Edit — add one validation step |
| `.project-memory/CURRENT_STATE.md` | Edit — add PR 0013 section |
| `.project-memory/ROADMAP.md` | Edit — mark PR 0013 |
| `.project-memory/pr/0013-static-energy-policy-config-example/CODER_REPORT.txt` | Create |

The plan also allows `.project-memory/ENERGY_POLICY_CONFIG_EXAMPLE.md` as a companion doc
if the coder determines it adds value, but it is not required by default.

## 11. Files Coder Must NOT Edit

- `run.py`, `app/**`, `service/**`, `shared_state/**`
- Real config files: `devices.yaml`, `devices_prod.yaml`, `config.json`,
  `config_cache.json`, `fallback_data.json`, `web_fallback_url.txt`, `.env`
- `app/control/domain.py`, `app/control/energy_policy.py`, `app/control/relay_mapping.py`
- Existing validation scripts
- `.github/workflows/build-and-deploy.yml`
- Docker/deployment files

## 12. Static Validation Script

`scripts/check-energy-policy-config-example.sh` must verify:

1. `examples/energy_policy.example.yaml` exists.
2. Required phrases/keys exist in the example:
   - `energy_aware_policy_version`
   - `runtime_loaded: false`
   - `example_only: true`
   - `evening_reserve_voltage: 26.5`
   - `grid_or_mains_conservation`
   - `weather_adjustment` (section header)
   - `readiness` (section header or key)
   - `health` (section header or key)
   - `manual_override_allowed` or `allowed: true` within `manual_override`
   - `deterministic_fallback`
   - `ml_advisory_enabled: false`
   - `ml_control_enabled: false`
   - `minimum_voltage_to_switch_on` or `min_voltage_on`
   - `voltage_to_switch_off` or `voltage_off`
   - `fail_safe_off`
3. No secret-like terms exist in the example file:
   `api_key`, `token`, `secret`, `password`, `credential`, `tuya_device_id`,
   `device_id`, `local_ip`, `kubeconfig`, `bearer`, `private_key`.
4. The file is valid YAML (`python -c "import yaml; yaml.safe_load(...)"`).
5. Runtime files not modified (git diff check).
6. Exit 0 on pass, 1 on failure. Read-only, no network, no secrets, no mutations.

## 13. Validation Commands (for coder phase)

```bash
# 1. Verify example exists
test -f examples/energy_policy.example.yaml && echo "EXISTS" || echo "MISSING"

# 2. Verify example is valid YAML
python -c "import yaml; yaml.safe_load(open('examples/energy_policy.example.yaml')); print('YAML: OK')"

# 3. Run the static validation script
bash scripts/check-energy-policy-config-example.sh
echo "Exit code: $?"

# 4. Run all existing validations
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
bash scripts/check-repo-safety.sh
bash scripts/check-project-memory.sh
python scripts/validate-yaml.py
bash scripts/check-image-publishing-boundary.sh
bash scripts/check-platform-control-redesign.sh
bash scripts/check-pump-automation-disabled.sh
bash scripts/check-generic-control-domain.sh
bash scripts/check-relay-switchable-load-mapping.sh
bash scripts/check-energy-aware-control-policy.sh
bash scripts/check-energy-policy-domain-types.sh

# 5. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0013-.../PLAN.md
git diff --name-only HEAD -- .project-memory/pr/0013-.../PLAN_REVIEW.yaml

# 6. Verify no runtime files modified
git diff --name-only HEAD -- run.py app/ service/ shared_state/ Dockerfile .github/workflows/build-and-deploy.yml

# 7. Verify coder artifact exists
test -f .project-memory/pr/0013-static-energy-policy-config-example/CODER_REPORT.txt && echo "CODER_REPORT: OK"
```

## 14. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 15. Future PR Boundary

| Work | Target PR |
|---|---|
| Runtime config loader | Deferred |
| Config schema enforcement | Deferred |
| Readiness evaluator | 0014 |
| Health evaluator | 0015 |
| Schedule/season profile evaluator | 0016 |
| Weather adjustment evaluator | 0017 |
| Deterministic policy decision engine | 0018 |
| Command proposal / manual control API | 0019+ |
| Controlled execution with safety gates | 0021+ |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |

## 16. Boundary Confirmations

- **Documentation/example only**: The example YAML is not loaded at runtime.
- **No secrets**: Fake placeholder load IDs (`example-*`), no `api_key`, `token`, `secret`.
- **No runtime code changed**: `run.py`, `app/`, `service/`, `shared_state/` untouched.
- **No config readers added**: No new Python modules that read YAML configs.
- **No pump code changed**: Pump already gated by PR 0008.
- **Manual switch control preserved**: All 4 methods + `toggle_device` + `_switch_loop`.
- **No ML control enabled**: `ml_control_enabled: false` explicitly in example.
- **No Docker/image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
