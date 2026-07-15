# PR 0021 — Command Safety Gate Model

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `9eecba2a2860d87622e39094825d144e7cb8b174` |
| Branch | `git branch --show-current` | `0021-command-safety-gate-model` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0021-command-safety-gate-model` and working tree is clean.

## 2. Purpose

PR 0021 introduces a **pure command safety gate model** — a deterministic function
`evaluate_command_safety_gate()` that decides whether a `CommandProposal` (from PR 0020)
is eligible for future execution. Safety gates are the final authority before any
hardware action is attempted.

This PR does NOT execute commands, wire into runtime, add API endpoints, or call Tuya/hardware.
Execution remains deferred to future controlled-execution PRs.

## 3. Product Context

1. PRs 0014–0018C built the policy decision engine (readiness, health, schedule, weather
   adjustment, operating boundaries, models, pure decision function).
2. PR 0018D added scenario matrix tests locking engine behavior.
3. PR 0019 added the manual control queue boundary (pure enqueue/cancel).
4. PR 0020 added command intent/proposal arbitration (`CommandProposal` type introduced).
5. PR 0021 adds the **safety gate** — a pure check that validates a proposal against battery
   voltage, inverter load cap, readiness, health, cooldown, kill switch, and maintenance mode
   before future execution.
6. Autonomous operation remains the default path. Operator input is override/correction.
   Safety gates are the final authority. Life-support constraints remain higher priority.
7. Manual relay/switch ON/OFF remains available and unchanged.
8. Pump automation remains obsolete and disabled by default (PR 0008).
9. ML advisory may be used later; ML control remains disabled.

## 4. Current Repository State

| Capability | File | Status |
|---|---|---|
| Readiness evaluator (pure function) | `app/control/readiness.py` | Implemented (PR 0014) |
| Health evaluator (pure function) | `app/control/health.py` | Implemented (PR 0015) |
| Schedule profile model | `app/control/schedule_profile.py` | Implemented (PR 0016) |
| Weather adjustment evaluator | `app/control/weather_adjustment.py` | Implemented (PR 0017) |
| Policy engine operating boundaries | `.project-memory/POLICY_DECISION_ENGINE.md` | Documented (PR 0018A) |
| Passive policy engine models (7 types) | `app/control/policy_models.py` | Implemented (PR 0018B) |
| Policy decision engine (pure function) | `app/control/policy_decision.py` | Implemented (PR 0018C) |
| Scenario matrix tests | `.project-memory/POLICY_DECISION_SCENARIOS.md` | Implemented (PR 0018D) |
| Manual control queue boundary | `app/control/manual_control_queue.py` | Implemented (PR 0019) |
| Command intent/proposal arbitration | `app/control/command_arbitration.py` | Implemented (PR 0020) |
| Autonomous control architecture doc | `.project-memory/AUTONOMOUS_CONTROL_AND_OPERATOR_OVERRIDE.md` | Documented (PR 0020) |
| **Command safety gate model** | `app/control/command_safety_gate.py` + `.project-memory/COMMAND_SAFETY_GATES.md` | **This PR** |

### 4.1 Grep Evidence Summary

**`CommandProposal` exists** in `app/control/command_arbitration.py` (PR 0020).

**`SafetyGate`, `SafetyGateStatus`, `SafetyGateCheck`, `CommandSafetyContext`,
`CommandSafetyGateInput`, `CommandSafetyGateResult`, `evaluate_command_safety_gate`**
do not exist anywhere — this PR introduces them.

**No executor, no runtime wiring, no Tuya calls** exist in `app/control/`.

**All 16 validation scripts pass** including policy engine, scenarios, manual queue, and arbitration checks.

## 5. Required Architecture Document

### File: `.project-memory/COMMAND_SAFETY_GATES.md`

The document must state:

1. CommandProposal is not execution — it is an advisory intent that requires safety gate approval.
2. Safety gate approval is required before any future execution.
3. Autonomous execution remains future and separate from this PR.
4. Web UI/operator override still passes through safety gates — no bypass.
5. Safety gates have final authority over execution eligibility.
6. Kill switch and maintenance mode are hard blocks (always refuse execution).
7. Battery fallback and inverter cap protect hardware from unsafe operating conditions.
8. Readiness/health/cooldown protect devices from premature or unsafe switching.
9. Pond/aeration/life-support remains special priority — even in low-battery edge cases,
   life-support loads may require operator review instead of hard block.
10. ML control remains deferred and requires separate safety-reviewed approval per ADR-0003.

## 6. Required Module Design

### Module Location

**Create:** `app/control/command_safety_gate.py`

**May update:** `app/control/__init__.py` (export all public types and functions)

### Module Dependencies

The module must use **only**:
- Python standard library (`dataclasses`, `typing`, `enum`)
- `app.control.command_arbitration` types: `CommandProposal`, `CommandIntent`, `CommandIntentSource`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
- Any runtime service, config reader, network client, or hardware adapter

### Module Requirements

1. Import-safe — no side effects at module import time.
2. No env var reads.
3. No config file reads.
4. No network connections.
5. No hardware calls.
6. No file mutations.
7. No `time.time` or `datetime.now` calls (timestamps are caller-provided).
8. No device switching.
9. No command execution.
10. No runtime service calls.
11. No loading of `examples/energy_policy.example.yaml`.
12. Pure, deterministic functions.

## 7. Required Types (5 Types)

### Type 1: `SafetyGateStatus`

```python
class SafetyGateStatus(Enum):
    PASSED = "passed"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    NO_PROPOSAL = "no_proposal"
```

### Type 2: `SafetyGateCheck`

```python
@dataclass(frozen=True)
class SafetyGateCheck:
    """Result of a single safety gate check.

    name: identifier for this check (e.g. "battery-fallback").
    passed: whether this check passed.
    reason: human-readable reason.
    severity: advisory severity string (e.g. "critical", "warning", "info").
    """
    name: str = ""
    passed: bool = False
    reason: str = ""
    severity: str = "info"
```

### Type 3: `CommandSafetyContext`

```python
@dataclass(frozen=True)
class CommandSafetyContext:
    """Runtime safety context for evaluating a command proposal.

    Pure data — no hardware calls, no side effects.
    All values are caller-provided (from telemetry, state, or config).
    """
    battery_voltage: float | None = None
    battery_grid_fallback_voltage: float = 24.5
    max_total_load_watts: float = 2500.0
    projected_total_load_watts: float | None = None
    readiness_passed: bool = True
    health_passed: bool = True
    cooldown_passed: bool = True
    operator_override_allowed: bool = True
    life_support_load_ids: tuple[str, ...] = field(default_factory=tuple)
    manual_review_required: bool = False
    kill_switch_active: bool = False
    maintenance_mode: bool = False
```

### Type 4: `CommandSafetyGateInput`

```python
@dataclass(frozen=True)
class CommandSafetyGateInput:
    """Input to the command safety gate evaluation.

    Pure data — no side effects.
    """
    proposal: CommandProposal | None = None
    context: CommandSafetyContext = field(default_factory=CommandSafetyContext)
```

### Type 5: `CommandSafetyGateResult`

```python
@dataclass(frozen=True)
class CommandSafetyGateResult:
    """Result of a command safety gate evaluation.

    status: overall safety gate status.
    execution_allowed: whether execution is allowed.
    requires_operator_review: whether operator review is needed before execution.
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers.
    checks: detailed list of individual safety check results.
    safety_notes: advisory notes for operator visibility.
    """
    status: SafetyGateStatus = SafetyGateStatus.NO_PROPOSAL
    execution_allowed: bool = False
    requires_operator_review: bool = False
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    checks: tuple[SafetyGateCheck, ...] = field(default_factory=tuple)
    safety_notes: str = ""
```

## 8. Required Pure Function

### `evaluate_command_safety_gate`

```python
def evaluate_command_safety_gate(
    gate_input: CommandSafetyGateInput,
) -> CommandSafetyGateResult
```

Behavior (evaluated in order, first match wins):

1. **no-proposal**: If `proposal` is None → `NO_PROPOSAL`, `execution_allowed=False`,
   `reason="no-proposal"`.

2. **proposal-not-executable**: If `proposal.execution_eligible=False` → `BLOCKED`,
   `execution_allowed=False`, `reason="proposal-not-executable"`.

3. **kill-switch-active**: If `context.kill_switch_active=True` → `BLOCKED`,
   `execution_allowed=False`, `reason="kill-switch-active"`.

4. **maintenance-mode**: If `context.maintenance_mode=True` → `BLOCKED`,
   `execution_allowed=False`, `reason="maintenance-mode"`.

5. **battery-fallback-block**: If proposal desires "on" and `battery_voltage` is not None
   and `battery_voltage <= battery_grid_fallback_voltage`:
   - If the target load_id is in `life_support_load_ids` → `REVIEW_REQUIRED`,
     `execution_allowed=False`, `requires_operator_review=True`,
     `reason="battery-fallback-block"`, `safety_notes` mentioning life-support exception.
   - Otherwise → `BLOCKED`, `execution_allowed=False`, `reason="battery-fallback-block"`.

6. **inverter-load-cap-block**: If proposal desires "on" and `projected_total_load_watts`
   is not None and `projected_total_load_watts > max_total_load_watts`:
   → `BLOCKED`, `execution_allowed=False`, `reason="inverter-load-cap-block"`.

7. **readiness-block**: If `readiness_passed=False` → `BLOCKED`,
   `execution_allowed=False`, `reason="readiness-block"`.

8. **health-block**: If `health_passed=False` → `BLOCKED`,
   `execution_allowed=False`, `reason="health-block"`.

9. **cooldown-block**: If `cooldown_passed=False` → `BLOCKED`,
   `execution_allowed=False`, `reason="cooldown-block"`.

10. **manual-review-required**: If `proposal.requires_operator_review=True` or
    `context.manual_review_required=True`:
    → `REVIEW_REQUIRED`, `execution_allowed=False`, `requires_operator_review=True`,
    `reason="manual-review-required"`.

11. **manual-override-not-allowed**: If `proposal.intent` source is `MANUAL_OPERATOR`
    and `context.operator_override_allowed=False`:
    → `BLOCKED`, `execution_allowed=False`, `reason="manual-override-not-allowed"`.

12. **passed**: Otherwise → `PASSED`, `execution_allowed=True`,
    `requires_operator_review=False`, `reason="passed"`.

### Required Reason Strings

| Reason String | When Used |
|---|---|
| `"no-proposal"` | Input proposal is None |
| `"proposal-not-executable"` | Proposal.execution_eligible is false |
| `"kill-switch-active"` | Kill switch is active |
| `"maintenance-mode"` | Maintenance mode is active |
| `"battery-fallback-block"` | Battery voltage at/below fallback threshold |
| `"inverter-load-cap-block"` | Projected load would exceed inverter cap |
| `"readiness-block"` | Readiness check failed |
| `"health-block"` | Health check failed |
| `"cooldown-block"` | Cooldown check failed |
| `"manual-review-required"` | Operator review is required |
| `"manual-override-not-allowed"` | Operator override is not allowed |
| `"passed"` | All checks passed |

### Required Product Rules

1. Safety gates are the final authority before future execution.
2. Autonomous proposals can pass without operator review if safe.
3. Operator overrides can pass without mandatory review if safe and allowed.
4. Operator overrides are blocked if `operator_override_allowed` is false.
5. Kill switch always blocks.
6. Maintenance mode blocks automatic/manual execution eligibility.
7. ON proposals must respect battery fallback threshold.
8. ON proposals must respect max_total_load_watts.
9. Readiness/health/cooldown can block execution eligibility.
10. Life-support loads may require review instead of hard block in low-battery edge cases.
11. This module must be pure, deterministic, import-safe, and side-effect-free.

## 9. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/command_safety_gate.py` | **Create** — command safety gate module with 5 types + 1 function |
| `app/control/__init__.py` | **Edit** — export all public types and functions |
| `.project-memory/COMMAND_SAFETY_GATES.md` | **Create** — safety gate architecture document |
| `scripts/check-command-safety-gate.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0021 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0021 in roadmap |
| `.project-memory/pr/0021-command-safety-gate-model/CODER_REPORT.txt` | **Create** — coder report |

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
- `app/control/policy_models.py` (frozen from PR 0018B)
- `app/control/policy_decision.py` (frozen from PR 0018C)
- `app/control/manual_control_queue.py` (frozen from PR 0019)
- `app/control/command_arbitration.py` (frozen from PR 0020)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 11. Static Validation Script

### File: `scripts/check-command-safety-gate.sh`

The script must:

1. Check `app/control/command_safety_gate.py` exists.
2. Check all five public types exist:
   `SafetyGateStatus`, `SafetyGateCheck`, `CommandSafetyContext`,
   `CommandSafetyGateInput`, `CommandSafetyGateResult`.
3. Check `evaluate_command_safety_gate` function exists.
4. Check `PASSED`, `BLOCKED`, `REVIEW_REQUIRED`, `NO_PROPOSAL` exist in `SafetyGateStatus`.
5. Check `execution_allowed` field exists in `CommandSafetyGateResult`.
6. Check `requires_operator_review` field exists in `CommandSafetyGateResult`.
7. Check `kill_switch_active` field exists in `CommandSafetyContext`.
8. Check `maintenance_mode` field exists in `CommandSafetyContext`.
9. Check `battery_voltage` and `battery_grid_fallback_voltage` exist in `CommandSafetyContext`.
10. Check `max_total_load_watts` and `projected_total_load_watts` exist in `CommandSafetyContext`.
11. Check `readiness_passed`, `health_passed`, `cooldown_passed` exist in `CommandSafetyContext`.
12. Check all required reason strings exist:
    `no-proposal`, `proposal-not-executable`, `kill-switch-active`, `maintenance-mode`,
    `battery-fallback-block`, `inverter-load-cap-block`, `readiness-block`, `health-block`,
    `cooldown-block`, `manual-review-required`, `manual-override-not-allowed`, `passed`.
13. Check `__init__.py` exports all public types and functions.
14. Check `.project-memory/COMMAND_SAFETY_GATES.md` exists.
15. Check no executor was introduced (no `CommandExecutor`, no `execute` function).
16. Check forbidden runtime imports absent:
    `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`,
    `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
    `relay_device_manager`, `device_status_logger`, `openweather`, `dess`.
17. Check no hardware calls exist:
    `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`,
    `toggle_device`, `set_numeric`, `update_status`, `mark_switched`,
    `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`.
18. Check no file/env/network/weather/ML/logging/current-time calls exist:
    `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`,
    `os.getenv`, `requests`, `aiohttp`, `subprocess`, `logging`.
19. Check runtime files are not modified (git diff check).
20. Print clear per-check output.
21. Exit 0 only when all checks pass.
22. Exit 1 if any check fails.

### GitHub Actions Integration

```yaml
      - name: 🔍 Command safety gate check
        run: bash scripts/check-command-safety-gate.sh
```

Add after the existing command arbitration check.

## 12. CURRENT_STATE.md Update

```
## PR 0021 — Command Safety Gate Model
PR 0021 adds a pure command safety gate model in
`app/control/command_safety_gate.py` with 5 types (SafetyGateStatus,
SafetyGateCheck, CommandSafetyContext, CommandSafetyGateInput,
CommandSafetyGateResult) and the pure function
`evaluate_command_safety_gate()`. Documents safety gate architecture in
`.project-memory/COMMAND_SAFETY_GATES.md`. No executor. No runtime wiring.
No API endpoints. No hardware execution.
```

## 13. ROADMAP.md Update

```
- [x] PR 0021 — Command safety gate model
```

## 14. Future PR Boundary

PR 0021 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Controlled executor (Tuya hardware dispatch with safety gate integration) | Later |
| API endpoints for web UI / operator review | Later |
| Persistent queue, proposal, and safety log storage | Later |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |
| Runtime wiring | Deferred |

## 15. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation |

## 16. Boundary Confirmations

- **Command safety gate model only**: Pure types + single safety gate function
- **No executor**: `CommandExecutor` and `execute` function must NOT exist
- **No runtime wiring**: Not connected to any runtime component
- **No API endpoints**: Deferred
- **No persistent storage**: Deferred
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No config loading**: Does not read YAML, env vars, or files
- **No system clock**: Does not call `time.time` or `datetime.now`
- **No automation enabled**: Pump automation disabled per PR 0008
- **No ML control**: ML advisory is advice-only; ML control deferred per ADR-0003
- **Autonomous default preserved**: Operator overrides are optional corrections
- **Safety gates are final authority**: Documented in architecture doc
- **Manual switch control preserved**: All switch methods unchanged
- **Pump automation remains disabled**: Per PR 0008
- **Docker/GitOps**: `build-and-deploy.yml` untouched; external GitOps boundary respected
- **All existing modules unchanged**: PR 0014–0020 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
