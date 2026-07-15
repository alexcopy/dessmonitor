# PR 0018B — Passive Policy Engine Models

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `d202898c3ef425c70bce3f025d1fcfe1dd96762a` |
| Branch | `git branch --show-current` | `0018b-passive-policy-engine-models` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0018b-passive-policy-engine-models` and working tree is clean.

## 2. Purpose

PR 0018B adds **passive policy engine input/output model types** — frozen dataclasses that
represent the data structures the future deterministic policy decision engine (PR 0018C)
will consume and produce. These types define the vocabulary for battery operating windows,
energy budgets, pond safety context, forecast strategy, load candidates, and the combined
policy decision input/output.

The models are **passive data only**. They do not implement `evaluate_policy_decision()`.
They do not choose loads. They do not execute commands. They do not wire into runtime.

This PR follows PR 0018A (which documented operating boundaries) and precedes PR 0018C
(pure decision function), PR 0018D (scenario tests), and later execution PRs.

## 3. Product Context

1. The policy decision engine is the "brain" of the energy-aware control system.
2. PR 0018A documented operating boundaries: battery extrema, inverter max load, pond
   life-support invariants, forecast-aware strategy, and a scenario matrix.
3. PR 0018B defines the **data model** the brain will use — its inputs and outputs.
4. The models are frozen dataclasses: immutable, pure, no side effects.
5. `configured_load_watts` comes from config-derived device metadata (such as `load_in_wt`),
   not live telemetry.
6. `current_total_load_watts` is the estimated sum of currently-on configured loads.
7. `projected_total_load_watts` is the estimated load after a candidate action.
8. Manual relay/switch ON/OFF remains available and unchanged.
9. Pump automation remains obsolete and disabled by default (PR 0008).
10. ML advisory may be used later; ML control remains disabled.

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
| Policy engine operating boundaries | `.project-memory/POLICY_DECISION_ENGINE.md` | Documented (PR 0018A) |
| **Passive policy engine models** | `app/control/policy_models.py` | **This PR** |

### 4.1 Grep Evidence Summary

**No `PolicyDecisionInput`, `PolicyDecisionResult`, `LoadCandidate`, `EnergyBudget`,
`BatteryOperatingWindow`, `PondSafetyContext`, or `ForecastStrategyContext` exist**
in any `app/control/` module — these types must be created.

**`configured_load_watts` does not exist** anywhere yet.

**`load_in_wt` exists** in legacy device metadata (PR 0010 mapping) but is excluded from
`SwitchableLoad` metadata as a raw config value. `configured_load_watts` in policy models
is the future structured form of this value.

**Battery boundary values** (24.5, 26.5, 28.5, 29.5, 2500) are documented in
`POLICY_DECISION_ENGINE.md` (PR 0018A) but not yet in any dataclass.

**All evaluator modules (PR 0014–0017) are implemented and pass validation.**

## 5. Relationship to PR 0014–0018A

| PR | Title | Relationship to PR 0018B |
|---|---|---|
| 0014 | Pure readiness evaluator | `ReadinessResult` is consumed by `PolicyDecisionInput` via `LoadCandidate.readiness`. |
| 0015 | Pure health evaluator | `HealthCheckResult` is consumed by `PolicyDecisionInput` via `LoadCandidate.health`. |
| 0016 | Schedule profile model | `ScheduleProfile`/`LoadScheduleProfile` is consumed by `LoadCandidate.schedule_profile`. |
| 0017 | Weather adjustment evaluator | `WeatherAdjustmentResult` is consumed by `PolicyDecisionInput.weather_adjustment`. |
| 0018A | Policy engine operating boundaries | Documents the numeric thresholds and invariants that the model types represent (e.g. `battery_evening_reserve_voltage`, `max_total_load_watts`). |
| 0018B | Passive policy engine models | **This PR** — defines frozen dataclasses for the decision engine's input/output. |
| 0018C | Pure deterministic decision engine | Will consume `PolicyDecisionInput` and produce `PolicyDecisionResult`. |
| 0018D | Scenario matrix and regression tests | Will test the decision engine with constructed `PolicyDecisionInput` instances. |

## 6. Required Passive Model Types (7 Types)

### Module Location

**Create:** `app/control/policy_models.py`

**May update:** `app/control/__init__.py` (export all seven model types)

### Module Dependencies

The module must use **only**:
- Python standard library (`dataclasses`, `typing`, `enum`)
- `app.control.energy_policy` types as needed: `LoadClass`, `DevicePriority`, `HealthStatus`,
  `EnergyPolicyDecision`, `EnergyPolicyContext`, `HealthCheckResult`, `ReadinessResult`
- `app.control.weather_adjustment` types: `WeatherAdjustmentResult`
- `app.control.schedule_profile` types: `ScheduleProfile`, `LoadScheduleProfile`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
- `app.control.readiness` (types only from `energy_policy` + `schedule_profile`)
- `app.control.health` (types only from `energy_policy`)

### Type 1: `BatteryOperatingWindow`

```python
@dataclass(frozen=True)
class BatteryOperatingWindow:
    """Battery voltage operating boundaries for the policy decision engine.

    All values are configurable defaults; the decision engine (0018C) may
    receive overridden values from runtime input.

    Pure data — no voltage monitoring, no hardware calls, no side effects.
    """
    grid_fallback_voltage: float = 24.5
    morning_minimum_voltage: float = 24.0
    evening_reserve_voltage: float = 26.5
    high_voltage_spend_threshold: float = 28.5
    full_voltage: float = 29.5
```

### Type 2: `EnergyBudget`

```python
@dataclass(frozen=True)
class EnergyBudget:
    """Energy budget tracking for inverter max load protection.

    configured_load_watts is an estimate from config-derived device metadata.
    current_total_load_watts is the estimated sum of currently-on configured loads.
    projected_total_load_watts is the estimated load after a candidate action.

    Pure data — no load monitoring, no hardware calls, no side effects.
    """
    max_total_load_watts: float = 2500.0
    current_total_load_watts: float = 0.0
    projected_total_load_watts: float = 0.0
```

### Type 3: `PondSafetyContext`

```python
@dataclass(frozen=True)
class PondSafetyContext:
    """Pond/fish/aeration safety context for the policy decision engine.

    Pure data — no temperature sensing, no hardware calls, no side effects.
    """
    pond_temperature_c: float | None = None
    pond_hot_water_temperature_c: float = 26.0
    is_summer: bool = False
    aeration_load_ids: tuple[str, ...] = field(default_factory=tuple)
    minimum_aeration_count: int = 1
    preferred_aeration_count: int = 2
    maximum_extra_aeration_count: int = 4
    life_support_required: bool = False
```

### Type 4: `ForecastStrategyContext`

```python
@dataclass(frozen=True)
class ForecastStrategyContext:
    """Forecast-aware strategy context for the policy decision engine.

    Pure data — no forecast fetching, no weather API calls, no side effects.
    """
    forecast_improves_later_today: bool = False
    bad_forecast_all_day: bool = False
    sunny_window_expected_hours: float = 0.0
    morning_strategy_active: bool = False
```

### Type 5: `LoadCandidate`

```python
@dataclass(frozen=True)
class LoadCandidate:
    """A single device load being evaluated by the policy decision engine.

    Pure data — no device queries, no hardware calls, no side effects.

    configured_load_watts is an estimate from config-derived device metadata
    (e.g. load_in_wt). It is NOT live telemetry.
    """
    load_id: str
    display_name: str = ""
    load_class: LoadClass = LoadClass.DISCRETIONARY
    priority: DevicePriority = DevicePriority.NORMAL
    configured_load_watts: float = 0.0
    currently_on: bool = False
    controllable: bool = True
    is_life_support: bool = False
    roles: tuple[str, ...] = field(default_factory=tuple)
    readiness: ReadinessResult | None = None
    health: HealthCheckResult | None = None
    schedule_profile: LoadScheduleProfile | None = None
```

### Type 6: `PolicyDecisionInput`

```python
@dataclass(frozen=True)
class PolicyDecisionInput:
    """Complete input for a policy decision evaluation.

    Aggregates battery state, energy budget, pond safety, forecast strategy,
    weather adjustment, and a list of load candidates for a single evaluation.

    Pure data — no data fetching, no hardware calls, no side effects.
    """
    context: EnergyPolicyContext
    loads: tuple[LoadCandidate, ...] = field(default_factory=tuple)
    energy_budget: EnergyBudget = field(default_factory=EnergyBudget)
    battery_window: BatteryOperatingWindow = field(default_factory=BatteryOperatingWindow)
    pond_safety: PondSafetyContext = field(default_factory=PondSafetyContext)
    forecast_strategy: ForecastStrategyContext = field(default_factory=ForecastStrategyContext)
    weather_adjustment: WeatherAdjustmentResult | None = None
```

### Type 7: `PolicyDecisionResult`

```python
@dataclass(frozen=True)
class PolicyDecisionResult:
    """Result of a policy decision evaluation for a single load candidate.

    Pure data — no execution commands, no hardware calls, no side effects.

    decision: what the engine recommends for this load.
    projected_total_load_watts: estimated total load if this decision is applied.
    blocked_by: list of blocking condition identifiers (e.g. "evening-reserve-protected").
    recommended_follow_up: advisory text for operator visibility.
    """
    decision: EnergyPolicyDecision = EnergyPolicyDecision.NO_ACTION
    target_load_id: str = ""
    projected_total_load_watts: float = 0.0
    reason: str = ""
    explanation: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    recommended_follow_up: str = "none"
```

### Module Requirements

1. All seven types must be frozen dataclasses (`@dataclass(frozen=True)`).
2. Import-safe — no side effects at module import time.
3. No env var reads.
4. No config file reads.
5. No network connections.
6. No hardware calls.
7. No file mutations.
8. No `time.time` or `datetime.now` calls.
9. No device switching.
10. No command proposal model (deferred to PR 0020).
11. No `evaluate_policy_decision` implementation (deferred to PR 0018C).
12. No loading of `examples/energy_policy.example.yaml`.
13. No decision engine logic of any kind.

## 7. Product Contract for Load Wattage

1. `configured_load_watts` comes from config-derived device metadata such as `load_in_wt`.
2. `configured_load_watts` is an estimate, not live telemetry.
3. `current_total_load_watts` is the estimated sum of currently-on configured loads.
4. `projected_total_load_watts` is the estimated load after a candidate action.
5. `max_total_load_watts` is configurable, typical 2500W.
6. 0018B only models these values; 0018C will calculate and decide.
7. No model in 0018B may execute, fetch, switch, queue, or propose commands.

## 8. Required Default Values

| Parameter | Default |
|---|---|
| `BatteryOperatingWindow.grid_fallback_voltage` | 24.5 |
| `BatteryOperatingWindow.evening_reserve_voltage` | 26.5 |
| `BatteryOperatingWindow.high_voltage_spend_threshold` | 28.5 |
| `BatteryOperatingWindow.full_voltage` | 29.5 |
| `EnergyBudget.max_total_load_watts` | 2500.0 |
| `PondSafetyContext.pond_hot_water_temperature_c` | 26.0 |

## 9. Allowed Implementation Files

The following files may be edited by the coder agent:

| File | Action |
|---|---|
| `app/control/policy_models.py` | **Create** — seven passive policy engine model types |
| `app/control/__init__.py` | **Edit** — export all seven model types |
| `scripts/check-policy-engine-models.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0018B section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0018B in roadmap |
| `.project-memory/pr/0018b-passive-policy-engine-models/CODER_REPORT.txt` | **Create** — coder report |

## 10. Forbidden Implementation Files

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
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 11. Static Validation Script

### File: `scripts/check-policy-engine-models.sh`

The script must:

1. Check `app/control/policy_models.py` exists.
2. Check all seven model types exist:
   `BatteryOperatingWindow`, `EnergyBudget`, `PondSafetyContext`,
   `ForecastStrategyContext`, `LoadCandidate`, `PolicyDecisionInput`, `PolicyDecisionResult`.
3. Check `configured_load_watts` field exists in `LoadCandidate`.
4. Check `projected_total_load_watts` field exists in `PolicyDecisionResult`.
5. Check `max_total_load_watts` field exists in `EnergyBudget`.
6. Check battery boundary fields exist in `BatteryOperatingWindow`:
   `grid_fallback_voltage`, `morning_minimum_voltage`, `evening_reserve_voltage`,
   `high_voltage_spend_threshold`, `full_voltage`.
7. Check pond safety fields exist in `PondSafetyContext`:
   `pond_temperature_c`, `pond_hot_water_temperature_c`, `is_summer`,
   `aeration_load_ids`, `life_support_required`.
8. Check forecast strategy fields exist in `ForecastStrategyContext`:
   `forecast_improves_later_today`, `bad_forecast_all_day`,
   `sunny_window_expected_hours`, `morning_strategy_active`.
9. Check dataclasses are frozen (check for `@dataclass(frozen=True)`).
10. Check `__init__.py` exports all seven model types.
11. Check no `evaluate_policy_decision` implementation exists.
12. Check no command proposal model is introduced (no `CommandProposal` or `ProposedAction` type).
13. Check no forbidden runtime imports exist:
    - `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
    - `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
      `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
14. Check no hardware calls exist:
    - `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`, `toggle_device`
    - `set_numeric`, `update_status`, `mark_switched`
    - `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`
15. Check no file/env/network/weather/ML calls exist:
    - `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`, `os.getenv`,
      `requests`, `aiohttp`, `subprocess`, `logging`
16. Check runtime files are not modified (git diff check).
17. Print clear per-check output.
18. Exit 0 only when all checks pass.
19. Exit 1 if any check fails.

### GitHub Actions Integration

Add one step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Policy engine models check
        run: bash scripts/check-policy-engine-models.sh
```

This step must be added **after** the existing policy engine operating boundaries check.

## 12. CURRENT_STATE.md Update

Add a concise PR 0018B section to `.project-memory/CURRENT_STATE.md`:

```
## PR 0018B — Passive Policy Engine Models
PR 0018B adds seven passive policy engine model types in
`app/control/policy_models.py`: BatteryOperatingWindow, EnergyBudget,
PondSafetyContext, ForecastStrategyContext, LoadCandidate, PolicyDecisionInput,
PolicyDecisionResult. All types are frozen dataclasses with no evaluation logic.
No evaluate_policy_decision implementation. No command proposal. No runtime wiring.
No automation enabled. Pond aeration life-support term recorded. Battery/inverter
boundaries recorded.
```

Do not rewrite unrelated sections.

## 13. ROADMAP.md Update

Mark PR 0018B:

```
- [x] PR 0018B — Passive policy engine models
```

Update under "Phase 2b: Platform Control Redesign — Staged Backend Refactor".

Do not rewrite unrelated sections.

## 14. Future PR Boundary

PR 0018B explicitly defers:

| Deferred Work | Target PR |
|---|---|
| `evaluate_policy_decision()` implementation | 0018C |
| Scenario matrix and regression tests | 0018D |
| Command proposal model | 0020 |
| Command queue / manual control API | 0019 |
| Wire policy decisions to command proposal | 0020 |
| Controlled execution with safety gates | 0021+ |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |
| Runtime wiring | Deferred |

## 15. Safety Boundaries

1. All seven model types are frozen dataclasses — no mutation, no side effects.
2. No `evaluate_policy_decision()` implementation in 0018B.
3. No command proposal or queue model.
4. No hardware execution.
5. No config loading or YAML reads.
6. No system clock calls.
7. No network or weather fetching.
8. No automation enabled.
9. No ML control enabled.
10. Manual switch control preserved.
11. Pump automation remains disabled per PR 0008.
12. All existing evaluators unchanged (PR 0014–0017).

## 16. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are **LOCKED** after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |

### Artifact Layout

```
.project-memory/pr/0018b-passive-policy-engine-models/
├── PLAN.md              ← This file (locked after approval)
├── PLAN_REVIEW.yaml     ← Plan-review artifact (locked after approval)
├── CODER_REPORT.txt     ← Coder artifacts (created by coder)
└── PRECOMMIT_REVIEW.yaml ← Precommit-review artifact
```

## 17. Boundary Confirmations

- **Passive data model only**: Seven frozen dataclasses with no evaluation logic.
- **No `evaluate_policy_decision()`**: Deferred to 0018C.
- **No command proposal**: Deferred to 0020.
- **No runtime wiring**: Not connected to any runtime component.
- **No hardware execution**: Does not call any switch/device/Tuya method.
- **No config loading**: Does not read YAML, env vars, or files.
- **No system clock**: Does not call `time.time` or `datetime.now`.
- **No automation enabled**: Pump automation disabled per PR 0008.
- **No ML control enabled**: ML advisory is advice-only. ML control deferred per ADR-0003.
- **Manual switch control preserved**: All switch methods unchanged.
- **Pump automation remains disabled**: Per PR 0008.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **All existing evaluators unchanged**: PR 0014–0017 frozen.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
