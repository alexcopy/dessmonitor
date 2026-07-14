# PR 0015 — Health Evaluator, Pure Function Only

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `f33b7faefa5cd4c858dd38030fbcf1b5c671b4ec` |
| Branch | `git branch --show-current` | `0015-health-evaluator-pure-function` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0015-health-evaluator-pure-function` and working tree is clean.

## 2. Purpose

PR 0015 adds a **pure health evaluator** — a deterministic, side-effect-free function that
assesses whether a device load's observed state matches its expected state and whether the
status data is usable, using only data already present in `HealthInput`.

The evaluator is **not wired into runtime behavior**. It does not switch devices. It does not
retry switching. It does not call Tuya, DESS, OpenWeather, ML, monitoring, or any runtime
service. It does not read config files, environment variables, or system time. It is pure
data-in / data-out.

## 3. Product Context

1. The project's core purpose is **energy-aware device control**.
2. **Health** answers: does observed load state/status match expectation and is the status usable?
3. Health is **not** readiness. A device can be ready but unhealthy, or healthy but not ready.
4. Health is **not** hardware execution. Health must not switch devices.
5. Health must **not** retry switching — escalation limits are the decision engine's concern.
6. Health must **not** call runtime services or read live device state by itself.
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
| Static energy policy config example | `examples/energy_policy.example.yaml` | Documented (PR 0013) |
| Readiness evaluator (pure function) | `app/control/readiness.py` | Implemented (PR 0014) |
| **Health evaluator (pure function)** | `app/control/health.py` | **This PR** |

### 4.1 Relevant Domain Types (from PR 0012)

The following types from `app/control/energy_policy.py` are consumed by the health evaluator:

- `HealthInput` — context for health evaluation: `load_id`, `expected_state`, `observed_state`,
  `status_age_seconds`, `failure_count`
- `HealthStatus` — enum: `HEALTHY`, `STALE`, `MISMATCH`, `UNREACHABLE`, `UNKNOWN`
- `HealthCheckResult` — output with `status`, `reason`, `recommended_follow_up`

### 4.2 Existing HealthInput Type (from `app/control/energy_policy.py`)

```python
@dataclass(frozen=True)
class HealthInput:
    load_id: str
    expected_state: Optional[object] = None
    observed_state: Optional[object] = None
    status_age_seconds: Optional[float] = None
    failure_count: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class HealthCheckResult:
    status: HealthStatus = HealthStatus.UNKNOWN
    reason: str = ""
    recommended_follow_up: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)
```

### 4.3 Grep Evidence Summary

**HealthInput/HealthStatus/HealthCheckResult already exist** in `app/control/energy_policy.py` (passive data). All required fields (`expected_state`, `observed_state`, `status_age_seconds`, `failure_count`) are present.

**`evaluate_health` does not yet exist** — no module `app/control/health.py` exists.

**Forbidden hardware calls are absent** from all `app/control/` modules.

**Pump automation remains disabled** (`PUMP_AUTOMATION_ENABLED` defaults to false).

**Manual switch control preserved** (`switch_on_device`, `switch_off_device`, `toggle_device` all
present in runtime code).

**No ML control enabled** — ML code remains advisory/deferred.

## 5. Relationship to PR 0011, PR 0012, PR 0013, and PR 0014

| PR | Title | Relationship to PR 0015 |
|---|---|---|
| 0011 | Energy-aware control policy requirements | Defines health check model: compare observed to expected state, detect stale status, detect repeated failures, recommend escalation without looping. |
| 0012 | Passive energy policy domain types | Provides `HealthInput`, `HealthStatus`, `HealthCheckResult` — the vocabulary the health evaluator consumes and produces. |
| 0013 | Static energy policy config example | Defines example health defaults (`consecutive_failure_limit: 3`, `stale_status_threshold_seconds: 600`). PR 0015 may reference comparable default constants. |
| 0014 | Pure readiness evaluator | Establishes the pattern for pure evaluator modules in `app/control/`. PR 0015 follows the same pattern. Readiness and health remain separate. |

## 6. Health Evaluator Goals

1. **Pure function** — deterministic, no side effects, same input produces same output.
2. **No hardware execution** — must never call switch methods or Tuya/DESS.
3. **No switch retry** — must never call `can_switch` or attempt recovery switching.
4. **No runtime wiring** — must not be called by `SmartHomeController`, `RelayTuyaController`,
   `DeviceInitializer`, monitoring runtime, weather service, or ML code in PR 0015.
5. **No config loading** — must not read files, env vars, or network resources.
6. **No system clock calls** — uses only data from `HealthInput`.
7. **Stable, testable reason strings** — deterministic classification of health conditions.
8. **Advisory follow-up only** — `recommended_follow_up` is text, not executable commands.
9. **Clear separation from readiness** — health does not evaluate voltage, cooldown, or time windows.

## 7. Health Definition

A device load's health is classified based on the following checks, evaluated in order:

1. **Invalid load ID**: `load_id` is empty or whitespace-only → `UNKNOWN` or equivalent.
2. **Unreachable / unknown state**: `observed_state` is missing/None and `expected_state` is present
   → `UNREACHABLE` (no status data to compare).
3. **Stale status**: `status_age_seconds` is above a threshold (default ~300s)
   → `STALE` (status data is too old to trust).
4. **Repeated failures**: `failure_count` is above a threshold (default ~3)
   → `STALE` or `UNREACHABLE` (escalation needed).
5. **State mismatch**: `expected_state` and `observed_state` are both present and differ
   → `MISMATCH`.
6. **Healthy**: `expected_state` and `observed_state` are both present and match, no stale/failure
   condition applies → `HEALTHY`.
7. **Unknown**: insufficient data (both `expected_state` and `observed_state` are absent/None)
   → `UNKNOWN`.

The evaluator must **not** automatically escalate to switching. It produces an advisory
`recommended_follow_up` string only.

## 8. Difference Between Readiness and Health

| Dimension | Readiness (PR 0014) | Health (PR 0015) |
|---|---|---|
| Question | Is this load allowed to be switched ON now? | Does observed state match expectation? |
| Consumes | `ReadinessInput` (voltage, time, weather, policy, cooldown) | `HealthInput` (expected state, observed state, age, failures) |
| Blocks on | Voltage, power source, time windows, cooldown, weather, reserve | N/A — health does not block |
| Produces | `ready: bool` + reason + `EnergyPolicyDecision` | `HealthStatus` + reason + `recommended_follow_up` |
| Hardware action | None | None (no switching, no retry) |
| System clock | Uses `context.voltage.timestamp` | Uses only data from `HealthInput` |
| Decision vocabulary | `ALLOW_ON`, `PREFER_OFF`, `FORCE_OFF`, `HOLD`, `NO_ACTION` | `HEALTHY`, `STALE`, `MISMATCH`, `UNREACHABLE`, `UNKNOWN` |

The two evaluators are independent. A future decision engine (PR 0018) may combine both
results.

## 9. Required Module Design

### Module Location

**Create:** `app/control/health.py`

**May update:** `app/control/__init__.py` (export `evaluate_health`)

### Module Dependencies

The module must use **only**:
- Python standard library (`typing`)
- `app.control.energy_policy` types: `HealthInput`, `HealthCheckResult`, `HealthStatus`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
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
9. No switch retry.
10. No scheduling.
11. No loading of `examples/energy_policy.example.yaml`.
12. No readiness evaluation (deferred to PR 0014, already implemented there).
13. No full policy decision engine (deferred to PR 0018).

## 10. Required Function Design

### Public API

```python
def evaluate_health(health_input: HealthInput) -> HealthCheckResult
```

### Semantics

1. Accept only `HealthInput`.
2. Return `HealthCheckResult`.
3. Use only data already present in `HealthInput`.
4. Be deterministic: same input returns same output.
5. Have no side effects.
6. Never switch a device.
7. Never retry switching.
8. Never call hardware.
9. Never call runtime services.
10. Never call ML.
11. Never load YAML/config files.
12. Never read current system time.

### Default Threshold Constants

The module may define module-level constants for health thresholds:

```python
DEFAULT_STALE_STATUS_SECONDS: float = 300.0
DEFAULT_FAILURE_COUNT_THRESHOLD: int = 3
```

These constants must be pure numeric values, not read from env vars or config files.

### Private Helpers

The module may define small private helper functions inside `app/control/health.py` if they
are pure and local. Suggested private helpers:
- `_check_load_id(load_id: str) -> tuple[bool, str]` — returns `(invalid, reason)`
- `_check_unreachable(expected_state, observed_state) -> tuple[bool, str]`
- `_check_stale(status_age_seconds: float | None) -> tuple[bool, str]`
- `_check_repeated_failures(failure_count: int) -> tuple[bool, str]`
- `_check_state_mismatch(expected_state, observed_state) -> tuple[bool, str]`

Each helper must:
- Accept only data already in the input (no system calls, no globals)
- Return a `tuple[bool, str]` where `True` means "condition detected" and the string is the reason
- Be pure and deterministic

### Evaluation Flow

```
evaluate_health(health_input):
  1. If load_id is empty/whitespace
     → HealthStatus.UNKNOWN, reason="invalid-load-id", follow_up="none"

  2. If expected_state is not None and observed_state is None
     → HealthStatus.UNREACHABLE, reason="unreachable", follow_up="inspect-device"

  3. If status_age_seconds is not None and > DEFAULT_STALE_STATUS_SECONDS
     → HealthStatus.STALE, reason="stale-status", follow_up="refresh-status"

  4. If failure_count >= DEFAULT_FAILURE_COUNT_THRESHOLD
     → HealthStatus.UNREACHABLE or HealthStatus.STALE, reason="repeated-failures",
        follow_up="investigate-failures"

  5. If expected_state is not None and observed_state is not None
     and expected_state != observed_state
     → HealthStatus.MISMATCH, reason="state-mismatch", follow_up="inspect-device"

  6. If expected_state is not None and observed_state is not None
     and expected_state == observed_state
     and no stale/failure condition above applied
     → HealthStatus.HEALTHY, reason="healthy", follow_up="none"

  7. Otherwise (insufficient data)
     → HealthStatus.UNKNOWN, reason="unknown-state", follow_up="none"
```

Note: steps 3 and 4 are checked before 5, so a device with stale status or repeated failures
is flagged as `STALE`/`UNREACHABLE` even if expected and observed appear to match — because
the observed data may be unreliable.

## 11. Required Reason Strings

The evaluator must produce stable, testable reason strings:

| Reason String | Meaning | HealthStatus |
|---|---|---|
| `"healthy"` | Expected and observed states match; no stale/failure conditions | `HEALTHY` |
| `"invalid-load-id"` | Load ID is empty, None, or whitespace-only | `UNKNOWN` |
| `"unreachable"` | Expected state present but observed state is None/missing | `UNREACHABLE` |
| `"stale-status"` | `status_age_seconds` above threshold | `STALE` |
| `"repeated-failures"` | `failure_count` at or above threshold | `UNREACHABLE` or `STALE` |
| `"state-mismatch"` | Expected and observed states differ | `MISMATCH` |
| `"unknown-state"` | Insufficient data to evaluate health | `UNKNOWN` |

The exact name strings are stable and must be checked by the validation script.

## 12. Recommended Follow-Up Semantics

`HealthCheckResult.recommended_follow_up` must contain advisory text only:

| Follow-Up String | Meaning |
|---|---|
| `"none"` | No action needed |
| `"refresh-status"` | Re-query status to get fresh data |
| `"inspect-device"` | Observed state differs from expected or device unreachable |
| `"investigate-failures"` | Repeated failures detected; operator attention needed |

The follow-up is advisory text only and must **not** be treated as a hardware command.
Future decision engine (PR 0018) may interpret these as escalation hints.

## 13. Determinism and Purity Requirements

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
12. **Same input produces same output** — always.

## 14. Safety Boundaries

1. The evaluator is pure and deterministic.
2. The evaluator must **not** execute hardware calls.
3. The evaluator must **not** switch devices.
4. The evaluator must **not** retry switching.
5. The evaluator must **not** be read by runtime.
6. The evaluator must **not** read configs.
7. The evaluator must **not** load `examples/energy_policy.example.yaml`.
8. The evaluator must **not** change startup behavior.
9. The evaluator must **not** change pump automation behavior.
10. The evaluator must **not** change manual switch behavior.
11. The evaluator must **not** enable automation or ML control.
12. ML advisory remains advice only.
13. External GitOps boundary remains unchanged.
14. The evaluator uses only data from `HealthInput` — no system clock, no mutable state.

## 15. Out of Scope for PR 0015

PR 0015 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Readiness evaluator changes | 0014 (already done) |
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

## 16. Allowed Implementation Files

The following files may be edited by the coder agent:

| File | Action |
|---|---|
| `app/control/health.py` | **Create** — pure health evaluator module |
| `app/control/__init__.py` | **Edit** — export `evaluate_health` function |
| `scripts/check-health-evaluator.sh` | **Create** — static validation script (see Section 18) |
| `.github/workflows/validate.yml` | **Edit** — add one validation step for `scripts/check-health-evaluator.sh` |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0015 section (see Section 19) |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0015 in roadmap (see Section 20) |
| `.project-memory/pr/0015-health-evaluator-pure-function/CODER_REPORT.txt` | **Create** — coder report |

## 17. Forbidden Implementation Files

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
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 18. Static Validation Script

### File: `scripts/check-health-evaluator.sh`

The script must:

1. Use only static local repository files.
2. Not require network access.
3. Not query Docker Hub, GitHub API, Kubernetes, or ArgoCD.
4. Not require Tuya or DESS secrets.
5. Not mutate files.
6. Verify `app/control/health.py` exists.
7. Verify `evaluate_health` function exists.
8. Verify `HealthInput` type is used in function signature.
9. Verify `HealthCheckResult` is the return type.
10. Verify `HealthStatus` is used in the module.
11. Verify required reason strings exist:
    - `"healthy"` or `healthy`
    - `"invalid-load-id"` or `invalid-load-id`
    - `"unreachable"` or `unreachable`
    - `"stale-status"` or `stale-status`
    - `"repeated-failures"` or `repeated-failures`
    - `"state-mismatch"` or `state-mismatch`
    - `"unknown-state"` or `unknown-state`
12. Verify forbidden imports are absent from `app/control/health.py`:
    - `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
    - `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
      `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
13. Verify forbidden hardware/action calls absent:
    - `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`, `toggle_device`
    - `set_numeric`, `update_status`, `mark_switched`
    - `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`
14. Verify forbidden impurity calls absent:
    - `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`, `os.getenv`,
      `requests`, `aiohttp`, `subprocess`, `logging`
15. Verify runtime files were not modified (git diff check against known runtime paths).
16. Print clear per-check output.
17. Exit 0 only when all checks pass.
18. Exit 1 if any check fails.

### GitHub Actions Integration

Add one step to `.github/workflows/validate.yml`:

```yaml
      - name: 🔍 Health evaluator check
        run: bash scripts/check-health-evaluator.sh
```

This step must be added **after** the existing readiness evaluator check.

## 19. CURRENT_STATE.md Update

Add a concise PR 0015 section to `.project-memory/CURRENT_STATE.md`:

```
## PR 0015 — Pure Health Evaluator
PR 0015 adds a pure deterministic health evaluator in `app/control/health.py`.
The evaluator is not runtime-wired. It does not switch devices or retry switching.
Runtime automation is not enabled. Manual relay/switch ON/OFF remains unchanged.
Pump automation remains disabled by default from PR 0008. ML control remains disabled.
```

Do not rewrite unrelated sections.

## 20. ROADMAP.md Update

Mark PR 0015 in `.project-memory/ROADMAP.md`:

```
- [x] PR 0015 — Health evaluator, pure function only
```

Update under "Phase 2b: Platform Control Redesign — Staged Backend Refactor".

Do not rewrite unrelated sections.

## 21. Validation Commands (for coder phase)

```bash
# 1. Verify module exists
test -f app/control/health.py && echo "EXISTS" || echo "MISSING"

# 2. Run the static validation script
bash scripts/check-health-evaluator.sh
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
bash scripts/check-energy-policy-config-example.sh
bash scripts/check-readiness-evaluator.sh

# 4. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0015-health-evaluator-pure-function/PLAN.md
git diff --name-only HEAD -- .project-memory/pr/0015-health-evaluator-pure-function/PLAN_REVIEW.yaml

# 5. Verify no runtime files modified
git diff --name-only HEAD -- run.py app/service/ app/devices/ app/tuya/ app/monitoring/ app/ml/ app/weather/ Dockerfile docker-compose.yml .github/workflows/build-and-deploy.yml

# 6. Verify coder artifact exists
test -f .project-memory/pr/0015-health-evaluator-pure-function/CODER_REPORT.txt && echo "CODER_REPORT: OK"
```

## 22. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are **LOCKED** after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must **NOT** edit PLAN.md or PLAN_REVIEW.yaml |

### Artifact Layout

```
.project-memory/pr/0015-health-evaluator-pure-function/
├── PLAN.md              ← This file (locked after approval)
├── PLAN_REVIEW.yaml     ← Plan-review artifact (locked after approval)
├── CODER_REPORT.txt     ← Coder artifacts (created by coder)
└── PRECOMMIT_REVIEW.yaml ← Precommit-review artifact
```

## 23. Boundary Confirmations

- **Pure function only**: `evaluate_health` is deterministic, side-effect-free.
- **No runtime wiring**: Not connected to `SmartHomeController`, `RelayTuyaController`,
  `DeviceInitializer`, monitoring, weather service, or ML code.
- **No hardware execution**: Does not call any switch/device/Tuya method.
- **No switch retry**: Does not call `can_switch`, `ready_to_switch_on`, or attempt recovery.
- **No config loading**: Does not read YAML, env vars, or files.
- **No system clock**: Uses only data from `HealthInput`.
- **Advisory follow-up only**: `recommended_follow_up` is text, not executable commands.
- **No automation enabled**: Pump automation disabled per PR 0008. No new automation added.
- **No ML control enabled**: ML advisory is advice-only. ML control deferred per ADR-0003.
- **Manual switch control preserved**: `_switch_loop`, `toggle_device`, all 4 switch methods unchanged.
- **Pump automation remains disabled**: Per PR 0008.
- **No Docker image publishing change**: `build-and-deploy.yml` untouched.
- **No external GitOps/ArgoCD change**: Publishing boundary respected.
- **Readiness evaluator unchanged**: `app/control/readiness.py` frozen from PR 0014.
- **Policy decision engine deferred**: PR 0018.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval.
