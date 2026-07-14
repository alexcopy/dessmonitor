# PR 0017 — Weather Adjustment Evaluator, Pure Function

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `c1e501c10cf1d43bfe5c9f5ab5ea4774411257cc` |
| Branch | `git branch --show-current` | `0017-weather-adjustment-evaluator` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0017-weather-adjustment-evaluator` and working tree is clean.

## 2. Purpose

PR 0017 adds a **pure weather adjustment evaluator** — a deterministic, side-effect-free
function that translates a passive `WeatherForecastSignal` into an advisory energy adjustment
result with a suggested decision, an adjustment factor, a reason string, and a recommended
follow-up.

The evaluator is **pure data-in / data-out**. It does not fetch weather. It does not import
OpenWeather or any weather runtime service. It does not execute control. It does not switch
devices. It does not wire into runtime components.

## 3. Product Context

1. The project's core purpose is **energy-aware device control**.
2. Previous PRs introduced readiness evaluation (PR 0014), health evaluation (PR 0015), and
   schedule profile model (PR 0016).
3. Weather adjustment translates forecast conditions into advisory energy spending adjustments:
   - **Sunny / good forecast**: may recommend spending more energy (higher voltage thresholds relaxed).
   - **Cloudy / rainy forecast**: may recommend conserving energy.
   - **Storm / snow / safety weather**: may recommend stronger conservation or protection.
   - **Unknown weather**: must remain safe and non-executing.
4. The weather adjustment result is consumed by a future decision engine (PR 0018) that
   composes readiness, health, schedule, and weather signals.
5. Manual relay/switch ON/OFF remains available and unchanged.
6. Pump automation remains obsolete and disabled by default (PR 0008).
7. ML advisory may be used later; ML control remains disabled.

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
| **Weather adjustment evaluator** | `app/control/weather_adjustment.py` | **This PR** |

### 4.1 Existing Types (from PR 0012)

The following types from `app/control/energy_policy.py` are consumed by the weather
adjustment evaluator:

- `WeatherForecastSignal` — frozen dataclass with `condition: WeatherCondition`,
  `temperature`, `confidence`, `expected_solar_opportunity`, `timestamp`, `metadata`
- `WeatherCondition` — enum: `SUNNY`, `CLOUDY`, `RAINY`, `SNOWY`, `STORM`, `UNKNOWN`
- `EnergyPolicyDecision` — enum: `ALLOW_ON`, `PREFER_OFF`, `FORCE_OFF`, `HOLD`, `NO_ACTION`

### 4.2 Grep Evidence Summary

**`WeatherCondition`, `WeatherForecastSignal`, `EnergyPolicyDecision`** all exist in
`app/control/energy_policy.py` and are exported via `app/control/__init__.py`.

**No weather adjustment module exists** — `app/control/weather_adjustment.py` must be created.

**`openweather` and `aiohttp`** are used only in `app/weather/openweather_service.py` (runtime),
which will not be imported by the pure evaluator.

**Forbidden hardware calls are absent** from all `app/control/` modules.

**Pump automation remains disabled** (`PUMP_AUTOMATION_ENABLED` defaults to false).

**No ML control enabled** — ML code remains advisory/deferred.

## 5. Relationship to PR 0014, PR 0015, and PR 0016

| PR | Title | Relationship to PR 0017 |
|---|---|---|
| 0014 | Pure readiness evaluator | Evaluates whether a load may be switched ON. The weather adjustment factor produced by PR 0017 may future-advise the readiness evaluator on threshold relaxation or tightening. |
| 0015 | Pure health evaluator | Evaluates whether observed state matches expected. Weather adjustment is orthogonal — health does not depend on weather. |
| 0016 | Schedule profile model | Defines when evaluation may occur. Weather adjustment is orthogonal — both will be composed in the future decision engine (PR 0018). |
| 0017 | Weather adjustment evaluator | Translates weather forecast into an advisory energy adjustment. Consumes `WeatherForecastSignal`; produces `WeatherAdjustmentResult`. |

## 6. Weather Adjustment Definition

Weather adjustment translates a `WeatherForecastSignal` into an advisory result that a future
decision engine may use to adjust voltage thresholds, time windows, or conservation behavior.

The evaluator:
- Consumes only `WeatherForecastSignal` (or `None`).
- Produces `WeatherAdjustmentResult` with decision, factor, reason, and follow-up.
- Is robust to enum names and enum values (matches on `.value` and `.name`).
- Does not require all `WeatherCondition` enum values to exist.
- Does not fetch weather or inspect external services.
- Does not mutate input.
- Does not read current time.
- Does not switch devices.

## 7. Required Module Design

### Module Location

**Create:** `app/control/weather_adjustment.py`

**May update:** `app/control/__init__.py` (export `WeatherAdjustmentResult`, `evaluate_weather_adjustment`)

### Required Result Model

```python
@dataclass(frozen=True)
class WeatherAdjustmentResult:
    """Advisory result of a weather-based energy adjustment evaluation.

    Pure data — no hardware calls, no weather fetch, no side effects.
    """
    decision: EnergyPolicyDecision
    adjustment_factor: float = 1.0
    reason: str = ""
    recommended_follow_up: str = "none"
```

- `decision`: The advisory recommendation (`ALLOW_ON`, `PREFER_OFF`, `FORCE_OFF`, or `NO_ACTION`).
- `adjustment_factor`: A multiplier that may be applied to voltage thresholds
  (1.0 = no adjustment, >1.0 = more permissive, <1.0 = more conservative).
- `reason`: A stable reason string identifying the weather condition and resulting action.
- `recommended_follow_up`: Advisory text such as "none", "monitor-weather", or "check-forecast".

### Required Pure Function

```python
def evaluate_weather_adjustment(
    weather: WeatherForecastSignal | None,
) -> WeatherAdjustmentResult
```

### Module Dependencies

The module must use **only**:
- Python standard library (`typing`, `dataclasses`)
- `app.control.energy_policy` types: `WeatherForecastSignal`, `WeatherCondition`, `EnergyPolicyDecision`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
- `app.control.readiness`, `app.control.health`, `app.control.schedule_profile`
- `aiohttp`, `requests`, any network or weather API client

### Module Requirements

1. Import-safe — no side effects at module import time.
2. No env var reads.
3. No config file reads.
4. No network connections.
5. No hardware calls.
6. No file mutations.
7. No `time.time` or `datetime.now` calls.
8. No device switching.
9. No weather fetching.
10. No OpenWeather runtime import.
11. No loading of `examples/energy_policy.example.yaml`.
12. No readiness evaluation (PR 0014).
13. No health evaluation (PR 0015).
14. No schedule evaluation (PR 0018).
15. No full policy decision engine (PR 0018).

## 8. Required Evaluation Semantics

The function must translate `WeatherForecastSignal.condition` (a `WeatherCondition` enum)
into an advisory result. It must match on both `.value` and `.name` to handle enum
representation differences.

### Mapping Rules

| Condition | Decision | Adjustment Factor | Reason | Follow-Up |
|---|---|---|---|---|
| `SUNNY` | `ALLOW_ON` | 1.15 | `"sunny-spend"` | `"none"` |
| `CLOUDY` | `PREFER_OFF` | 0.75 | `"cloudy-conserve"` | `"monitor-weather"` |
| `RAINY` | `PREFER_OFF` | 0.60 | `"rainy-conserve"` | `"monitor-weather"` |
| `STORM` | `FORCE_OFF` | 0.30 | `"storm-protect"` | `"check-forecast"` |
| `SNOWY` | `FORCE_OFF` | 0.30 | `"snowy-protect"` | `"check-forecast"` |
| Known but not explicitly mapped (e.g. matching no specific risky/good pattern) | `NO_ACTION` | 1.0 | `"neutral-weather"` | `"none"` |
| `UNKNOWN` or `None` input | `NO_ACTION` | 1.0 | `"unknown-weather"` | `"check-forecast"` |

### Implementation Approach

The function should use a mapping dictionary keyed by `WeatherCondition` enum values (and
optionally names) so that it is robust to enum member identity regardless of how the enum
was imported or constructed. For example:

```python
_ADJUSTMENT_MAP: dict[WeatherCondition, tuple[EnergyPolicyDecision, float, str, str]] = {
    WeatherCondition.SUNNY:  (EnergyPolicyDecision.ALLOW_ON,   1.15, "sunny-spend",       "none"),
    WeatherCondition.CLOUDY: (EnergyPolicyDecision.PREFER_OFF, 0.75, "cloudy-conserve",   "monitor-weather"),
    WeatherCondition.RAINY:  (EnergyPolicyDecision.PREFER_OFF, 0.60, "rainy-conserve",    "monitor-weather"),
    WeatherCondition.STORM:  (EnergyPolicyDecision.FORCE_OFF,  0.30, "storm-protect",     "check-forecast"),
    WeatherCondition.SNOWY:  (EnergyPolicyDecision.FORCE_OFF,  0.30, "snowy-protect",     "check-forecast"),
}
```

A fallback for known but unmapped conditions should return `neutral-weather`.
`None` input or `WeatherCondition.UNKNOWN` should return `unknown-weather`.

### Evaluation Flow

```
evaluate_weather_adjustment(weather):
  1. If weather is None
     → NO_ACTION, 1.0, "unknown-weather", "check-forecast"

  2. If weather.condition is UNKNOWN
     → NO_ACTION, 1.0, "unknown-weather", "check-forecast"

  3. Look up weather.condition in map using .value and .name
     If found (via dict lookup or explicit value/name match)
     → return mapped decision, factor, reason, follow_up

  4. If not found in map (known but unmapped weather condition)
     → NO_ACTION, 1.0, "neutral-weather", "none"
```

## 9. Required Reason Strings

| Reason String | Meaning | Decision | Factor |
|---|---|---|---|
| `"sunny-spend"` | Sunny conditions suggest energy spending is favorable | `ALLOW_ON` | 1.15 |
| `"cloudy-conserve"` | Cloudy conditions suggest conservation | `PREFER_OFF` | 0.75 |
| `"rainy-conserve"` | Rainy conditions suggest stronger conservation | `PREFER_OFF` | 0.60 |
| `"storm-protect"` | Storm conditions suggest protection mode | `FORCE_OFF` | 0.30 |
| `"snowy-protect"` | Snowy conditions suggest protection mode | `FORCE_OFF` | 0.30 |
| `"neutral-weather"` | Known weather condition with no explicit adjustment | `NO_ACTION` | 1.0 |
| `"unknown-weather"` | Missing or unknown weather forecast | `NO_ACTION` | 1.0 |

The exact name strings are stable and must be checked by the validation script.

## 10. Determinism and Purity Requirements

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
11. It does **not** depend on global mutable state.
12. It does **not** fetch weather or call external APIs.
13. **Same input produces same output** — always.

## 11. Safety Boundaries

1. The evaluator is pure and deterministic.
2. The evaluator must **not** execute hardware calls.
3. The evaluator must **not** switch devices.
4. The evaluator must **not** fetch weather.
5. The evaluator must **not** import OpenWeather or weather runtime.
6. The evaluator must **not** be read by runtime.
7. The evaluator must **not** read configs.
8. The evaluator must **not** load `examples/energy_policy.example.yaml`.
9. The evaluator must **not** change startup behavior.
10. The evaluator must **not** change pump automation behavior.
11. The evaluator must **not** change manual switch behavior.
12. The evaluator must **not** enable automation or ML control.
13. ML advisory remains advice only.
14. External GitOps boundary remains unchanged.
15. The evaluator uses only the `WeatherForecastSignal` input — no system clock, no mutable state.

## 12. Allowed Implementation Files

The following files may be edited by the coder agent:

| File | Action |
|---|---|
| `app/control/weather_adjustment.py` | **Create** — pure weather adjustment evaluator module |
| `app/control/__init__.py` | **Edit** — export `WeatherAdjustmentResult`, `evaluate_weather_adjustment` |
| `scripts/check-weather-adjustment-evaluator.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step for `scripts/check-weather-adjustment-evaluator.sh` |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0017 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0017 in roadmap |
| `.project-memory/pr/0017-weather-adjustment-evaluator/CODER_REPORT.txt` | **Create** — coder report |

## 13. Forbidden Implementation Files

The coder must **not** edit these files:

- `run.py`
- `app/service/**`
- `app/devices/**`
- `app/tuya/**`
- `app/monitoring/**`
- `app/ml/**`
- `app/weather/**` (including `openweather_service.py`)
- `app/control/domain.py` (frozen from PR 0009)
- `app/control/relay_mapping.py` (frozen from PR 0010)
- `app/control/energy_policy.py` (frozen from PR 0012)
- `app/control/readiness.py` (frozen from PR 0014)
- `app/control/health.py` (frozen from PR 0015)
- `app/control/schedule_profile.py` (frozen from PR 0016)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 14. Static Validation Script

### File: `scripts/check-weather-adjustment-evaluator.sh`

The script must:

1. Use only static local repository files.
2. Not require network access.
3. Not query Docker Hub, GitHub API, Kubernetes, or ArgoCD.
4. Not require Tuya or DESS secrets.
5. Not mutate files.
6. Verify `app/control/weather_adjustment.py` exists.
7. Verify `WeatherAdjustmentResult` type exists.
8. Verify `evaluate_weather_adjustment` function exists.
9. Verify `WeatherForecastSignal` is used in the module.
10. Verify `WeatherCondition` is used in the module.
11. Verify `EnergyPolicyDecision` is used in the module.
12. Verify `adjustment_factor` field exists in `WeatherAdjustmentResult`.
13. Verify dataclass is frozen (`@dataclass(frozen=True)`).
14. Verify all required reason strings exist:
    - `"sunny-spend"` or `sunny-spend`
    - `"cloudy-conserve"` or `cloudy-conserve`
    - `"rainy-conserve"` or `rainy-conserve`
    - `"storm-protect"` or `storm-protect`
    - `"snowy-protect"` or `snowy-protect`
    - `"neutral-weather"` or `neutral-weather`
    - `"unknown-weather"` or `unknown-weather`
15. Verify forbidden imports are absent:
    - `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
    - `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
      `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
    - `app.control.readiness`, `app.control.health`, `app.control.schedule_profile`
16. Verify forbidden hardware/action calls absent:
    - `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`, `toggle_device`
    - `set_numeric`, `update_status`, `mark_switched`
    - `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`
17. Verify forbidden impurity calls absent:
    - `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`, `os.getenv`,
      `requests`, `aiohttp`, `subprocess`, `logging`
18. Verify no weather API/client imports or URLs (e.g. `openweather`, `weathermap`, `api.openweather`).
19. Verify runtime files were not modified (git diff check against known runtime paths).
20. Print clear per-check output.
21. Exit 0 only when all checks pass.
22. Exit 1 if any check fails.

### GitHub Actions Integration

Add one step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Weather adjustment evaluator check
        run: bash scripts/check-weather-adjustment-evaluator.sh
```

This step must be added **after** the existing schedule profile model check.

## 15. CURRENT_STATE.md Update

Add a concise PR 0017 section to `.project-memory/CURRENT_STATE.md`:

```
## PR 0017 — Weather Adjustment Evaluator
PR 0017 adds a pure deterministic weather adjustment evaluator in
`app/control/weather_adjustment.py`. The evaluator translates a passive
WeatherForecastSignal into an advisory energy adjustment result (decision,
factor, reason, follow-up). It does not fetch weather, import OpenWeather,
execute control, or switch devices. It is not runtime-wired. Runtime
automation is not enabled. Manual relay/switch ON/OFF remains unchanged.
Pump automation remains disabled by default from PR 0008. ML control
remains disabled.
```

Do not rewrite unrelated sections.

## 16. ROADMAP.md Update

Mark PR 0017 in `.project-memory/ROADMAP.md`:

```
- [x] PR 0017 — Weather adjustment evaluator
```

Update under "Phase 2b: Platform Control Redesign — Staged Backend Refactor".

Do not rewrite unrelated sections.

## 17. Future PR Boundary

PR 0017 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Deterministic policy decision engine (no hardware exec) | 0018 |
| Runtime config loader | 0018+ |
| Policy config schema enforcement | 0018+ |
| Schedule evaluation (matching "now" against profiles) | 0018 |
| Command proposal layer | 0019 |
| Manual control API or command queue | 0019 |
| Wire policy decisions to command proposal | 0020 |
| Controlled execution with safety gates | 0021+ |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |
| External GitOps changes | Never (separate repo) |
| Runtime wiring | Deferred |
| Readiness evaluator changes | 0014 (already done) |
| Health evaluator changes | 0015 (already done) |
| Schedule profile model changes | 0016 (already done) |

## 18. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are **LOCKED** after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |

### Artifact Layout

```
.project-memory/pr/0017-weather-adjustment-evaluator/
├── PLAN.md              ← This file (locked after approval)
├── PLAN_REVIEW.yaml     ← Plan-review artifact (locked after approval)
├── CODER_REPORT.txt     ← Coder artifacts (created by coder)
└── PRECOMMIT_REVIEW.yaml ← Precommit-review artifact
```

## 19. Boundary Confirmations

- **Pure function only**: `evaluate_weather_adjustment` is deterministic, side-effect-free.
- **No weather fetch**: Does not call OpenWeather, any HTTP client, or external API.
- **No OpenWeather import**: Does not import `app.weather` or any weather runtime.
- **No runtime wiring**: Not connected to any runtime component.
- **No hardware execution**: Does not call any switch/device/Tuya method.
- **No config loading**: Does not read YAML, env vars, or files.
- **No system clock**: Does not call `time.time` or `datetime.now`.
- **No automation enabled**: Pump automation disabled per PR 0008. No new automation added.
- **No ML control enabled**: ML advisory is advice-only. ML control deferred per ADR-0003.
- **Manual switch control preserved**: All switch methods unchanged.
- **Pump automation remains disabled**: Per PR 0008.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **Readiness/health/schedule evaluators unchanged**: Frozen from previous PRs.
- **Policy decision engine deferred**: PR 0018.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
