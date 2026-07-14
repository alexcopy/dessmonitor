# PR 0016 — Schedule Profile Model

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `5f54fa84ef653523a4d2aab529a9c0ac6f72be82` |
| Branch | `git branch --show-current` | `0016-schedule-profile-model` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0016-schedule-profile-model` and working tree is clean.

## 2. Purpose

PR 0016 adds a **passive schedule profile model** — frozen dataclasses that represent
allowed control windows by time of day, day-of-week applicability, seasonal applicability,
check interval / evaluation frequency, and per-load schedule profile structure.

The model is **passive data only**. It does not evaluate real-time schedules. It does not
read current time. It does not wire into runtime components. It does not switch devices.
It does not enable automation.

## 3. Product Context

1. The project's core purpose is **energy-aware device control**.
2. Previous PRs introduced readiness evaluation (PR 0014) and health evaluation (PR 0015)
   as pure functions.
3. Schedule profiles define WHEN a device may be considered for switching — which time-of-day
   windows, which days of the week, which seasons.
4. Schedule profiles also define HOW OFTEN evaluation should occur via `check_interval_seconds`.
5. The schedule profile is consumed by a future decision engine (PR 0018) that composes
   readiness, health, schedule, and weather signals.
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
| **Schedule profile model** | `app/control/schedule_profile.py` | **This PR** |

### 4.1 Existing Enum Types (from PR 0012)

The following enums from `app/control/energy_policy.py` are consumed by the schedule profile model:

- `TimeOfDay` — `MORNING`, `DAY`, `EVENING`, `NIGHT`, `UNKNOWN`
- `Season` — `SPRING`, `SUMMER`, `AUTUMN`, `WINTER`, `UNKNOWN`

### 4.2 Grep Evidence Summary

**`TimeOfDay` and `Season` already exist** in `app/control/energy_policy.py` and are
exported via `app/control/__init__.py`.

**No schedule profile module exists** — `app/control/schedule_profile.py` must be created.

**Forbidden hardware calls are absent** from all `app/control/` modules.

**Pump automation remains disabled** (`PUMP_AUTOMATION_ENABLED` defaults to false).

**Manual switch control preserved** (`switch_on_device`, `switch_off_device`, `toggle_device` all
present in runtime code).

**No ML control enabled** — ML code remains advisory/deferred.

## 5. Relationship to PR 0014 (Readiness) and PR 0015 (Health)

| PR | Title | Relationship to PR 0016 |
|---|---|---|
| 0014 | Pure readiness evaluator | Evaluates whether a load may be switched ON based on voltage, time, weather, cooldown, reserve. PR 0016's schedule profile provides time-of-day windows and check intervals that may feed into readiness evaluation in a future decision engine. |
| 0015 | Pure health evaluator | Evaluates whether observed state matches expected state. PR 0016's schedule profile is orthogonal — health does not depend on time windows. |
| 0016 | Schedule profile model | Defines when and how often evaluation may occur. Composable with readiness (PR 0014) and health (PR 0015) in a future decision engine (PR 0018). |

## 6. Schedule Profile Definition

A schedule profile defines:

1. **Time-of-day windows**: Which `TimeOfDay` slot(s) control is allowed in.
2. **Day-of-week applicability**: Which days (0=Monday through 6=Sunday) the window applies to.
3. **Seasonal applicability**: Which `Season` the profile applies to (or `None` for year-round).
4. **Check interval**: How often evaluation should occur in this window, in seconds.
5. **Enabled flag**: Whether this profile is active.

Schedule profiles are passive data. They do not:
- Read current time.
- Evaluate whether "now" is within the window.
- Schedule or execute any loop.
- Switch devices.
- Call hardware or runtime services.

no scheduler execution — no loop, no time evaluation, no scheduler.

## 7. Required Model Types

### Module Location

**Create:** `app/control/schedule_profile.py`

**May update:** `app/control/__init__.py` (export the new types)

### Type 1: `ScheduleWindow`

```python
@dataclass(frozen=True)
class ScheduleWindow:
    """A single time-of-day window within a schedule profile.

    Passive data only — no time evaluation, no scheduling, no hardware calls.
    """
    time_of_day: TimeOfDay
    check_interval_seconds: int = 300
    enabled: bool = True
```

- `time_of_day`: The `TimeOfDay` slot (MORNING, DAY, EVENING, NIGHT).
- `check_interval_seconds`: Recommended evaluation frequency in this window.
- `enabled`: Whether this window is active.

### Type 2: `ScheduleProfile`

```python
@dataclass(frozen=True)
class ScheduleProfile:
    """A named schedule profile with time-of-day windows, day-of-week, and season.

    Passive data only — no time evaluation, no scheduling, no hardware calls.
    """
    profile_id: str
    windows: tuple[ScheduleWindow, ...] = field(default_factory=tuple)
    days_of_week: tuple[int, ...] = field(default_factory=tuple)
    season: Season | None = None
    enabled: bool = True
```

- `profile_id`: Stable identifier for this profile (e.g. "default", "summer", "winter").
- `windows`: Ordered tuple of `ScheduleWindow` entries.
- `days_of_week`: Tuple of ints 0-6 (0=Monday). Empty tuple means every day.
- `season`: The `Season` this profile applies to, or `None` for year-round.
- `enabled`: Whether this profile is active.

### Type 3: `LoadScheduleProfile`

```python
@dataclass(frozen=True)
class LoadScheduleProfile:
    """Associates a device load with a schedule profile.

    Passive data only — no time evaluation, no scheduling, no hardware calls.
    """
    load_id: str
    profile_id: str
```

- `load_id`: Identifies the device load.
- `profile_id`: References a `ScheduleProfile.profile_id`.

### Module Dependencies

The module must use **only**:
- Python standard library (`dataclasses`, `typing`)
- `app.control.energy_policy` types: `TimeOfDay`, `Season`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
- `app.control.readiness`, `app.control.health`
- Any runtime service, config reader, network client, monitoring runtime, or hardware adapter

### Module Requirements

1. Import-safe — no side effects at module import time.
2. No env var reads.
3. No config file reads.
4. No network connections.
5. No hardware calls.
6. No file mutations.
7. No `time.time` or `datetime.now` calls.
8. No device switching.
9. No scheduler execution or loop.
10. No loading of `examples/energy_policy.example.yaml`.
11. No readiness evaluation (PR 0014).
12. No health evaluation (PR 0015).
13. No full policy decision engine (PR 0018).

## 8. Allowed Implementation Files

The following files may be edited by the coder agent:

| File | Action |
|---|---|
| `app/control/schedule_profile.py` | **Create** — passive schedule profile model with frozen dataclasses |
| `app/control/__init__.py` | **Edit** — export `ScheduleWindow`, `ScheduleProfile`, `LoadScheduleProfile` |
| `scripts/check-schedule-profile-model.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step for `scripts/check-schedule-profile-model.sh` |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0016 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0016 in roadmap |
| `.project-memory/pr/0016-schedule-profile-model/CODER_REPORT.txt` | **Create** — coder report |

## 9. Forbidden Implementation Files

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
- `app/control/readiness.py` (frozen from PR 0014)
- `app/control/health.py` (frozen from PR 0015)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 10. Static Validation Script

### File: `scripts/check-schedule-profile-model.sh`

The script must:

1. Use only static local repository files.
2. Not require network access.
3. Not query Docker Hub, GitHub API, Kubernetes, or ArgoCD.
4. Not require Tuya or DESS secrets.
5. Not mutate files.
6. Verify `app/control/schedule_profile.py` exists.
7. Verify `ScheduleWindow` type exists.
8. Verify `ScheduleProfile` type exists.
9. Verify `LoadScheduleProfile` type exists.
10. Verify `TimeOfDay` is used in the module.
11. Verify `Season` is used in the module.
12. Verify `check_interval_seconds` field exists in `ScheduleWindow`.
13. Verify `days_of_week` field exists in `ScheduleProfile`.
14. Verify dataclasses are frozen (check for `@dataclass(frozen=True)`).
15. Verify forbidden imports are absent:
    - `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
    - `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
      `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
    - `app.control.readiness`, `app.control.health`
16. Verify forbidden hardware/action calls absent:
    - `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`, `toggle_device`
    - `set_numeric`, `update_status`, `mark_switched`
    - `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`
17. Verify forbidden impurity calls absent:
    - `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`, `os.getenv`,
      `requests`, `aiohttp`, `subprocess`, `logging`
18. Verify runtime files were not modified (git diff check against known runtime paths).
19. Print clear per-check output.
20. Exit 0 only when all checks pass.
21. Exit 1 if any check fails.

### GitHub Actions Integration

Add one step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Schedule profile model check
        run: bash scripts/check-schedule-profile-model.sh
```

This step must be added **after** the existing health evaluator check.

## 11. CURRENT_STATE.md Update

Add a concise PR 0016 section to `.project-memory/CURRENT_STATE.md`:

```
## PR 0016 — Schedule Profile Model
PR 0016 adds a passive schedule profile model in `app/control/schedule_profile.py`
with frozen dataclasses (ScheduleWindow, ScheduleProfile, LoadScheduleProfile)
defining time-of-day windows, day-of-week applicability, seasonal applicability,
and check interval configuration. The model is not runtime-wired. No schedule
execution is implemented. Runtime automation is not enabled. Manual relay/switch
ON/OFF remains unchanged. Pump automation remains disabled by default from
PR 0008. ML control remains disabled.
```

Do not rewrite unrelated sections.

## 12. ROADMAP.md Update

Mark PR 0016 in `.project-memory/ROADMAP.md`:

```
- [x] PR 0016 — Schedule profile model
```

Update under "Phase 2b: Platform Control Redesign — Staged Backend Refactor".

Do not rewrite unrelated sections.

## 13. Future PR Boundary

PR 0016 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Schedule evaluation (matching "now" against profiles) | 0018 |
| Readiness evaluator changes | 0014 (already done) |
| Health evaluator changes | 0015 (already done) |
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

## 14. Safety Boundaries

1. The model is passive and frozen — no mutation, no side effects.
2. The model must **not** execute hardware calls.
3. The model must **not** switch devices.
4. The model must **not** be read by runtime.
5. The model must **not** read configs.
6. The model must **not** load `examples/energy_policy.example.yaml`.
7. The model must **not** change startup behavior.
8. The model must **not** change pump automation behavior.
9. The model must **not** change manual switch behavior.
10. The model must **not** enable automation, schedule execution, or ML control.
11. ML advisory remains advice only.
12. External GitOps boundary remains unchanged.
13. The model must **not** call `time.time`, `datetime.now`, or any time-reading function.
14. The model must **not** implement any evaluation logic — it is data definitions only.

## 15. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are **LOCKED** after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |

### Artifact Layout

```
.project-memory/pr/0016-schedule-profile-model/
├── PLAN.md              ← This file (locked after approval)
├── PLAN_REVIEW.yaml     ← Plan-review artifact (locked after approval)
├── CODER_REPORT.txt     ← Coder artifacts (created by coder)
└── PRECOMMIT_REVIEW.yaml ← Precommit-review artifact
```

## 16. Boundary Confirmations

- **Passive data model only**: `ScheduleWindow`, `ScheduleProfile`, `LoadScheduleProfile` are
  frozen dataclasses with no evaluation logic.
- **No runtime wiring**: Not connected to any runtime component.
- **No scheduler execution**: No loop, no time evaluation, no scheduler.
- **No hardware execution**: Does not call any switch/device/Tuya method.
- **No config loading**: Does not read YAML, env vars, or files.
- **No system clock**: Does not call `time.time` or `datetime.now`.
- **No automation enabled**: Pump automation disabled per PR 0008. No new automation added.
- **No ML control enabled**: ML advisory is advice-only. ML control deferred per ADR-0003.
- **Manual switch control preserved**: All switch methods unchanged.
- **Pump automation remains disabled**: Per PR 0008.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **Readiness evaluator unchanged**: `app/control/readiness.py` frozen from PR 0014.
- **Health evaluator unchanged**: `app/control/health.py` frozen from PR 0015.
- **Policy decision engine deferred**: PR 0018.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
