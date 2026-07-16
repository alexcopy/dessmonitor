# PR 0022 — Controlled Execution Eligibility Model

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `166b2a47a6be67407d717e99c5d4ef3135e7382e` |
| Branch | `git branch --show-current` | `0022-controlled-execution-eligibility-model` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0022-controlled-execution-eligibility-model` and working tree is clean.

## 2. Purpose

PR 0022 introduces a **pure controlled execution eligibility model** — a deterministic function
`evaluate_execution_eligibility()` that determines whether a `CommandProposal` that has passed
safety gates is eligible for a future executor.

This PR answers: **CommandProposal + PASSED safety gate → eligible_for_future_executor**.

This PR does NOT execute anything, create a runtime executor, wire into runtime, add API
endpoints, or call Tuya/hardware. Execution remains deferred to future controlled-execution PRs.

## 3. Product Context

1. PRs 0014–0018C built the policy decision engine.
2. PR 0018D added scenario matrix tests locking engine behavior.
3. PR 0019 added the manual control queue boundary.
4. PR 0020 added command intent/proposal arbitration (`CommandProposal` introduced).
5. PR 0021 added the command safety gate model (validates proposals against battery,
   inverter, readiness, health, cooldown, kill switch, maintenance mode).
6. PR 0022 adds **execution eligibility** — the final gate before a future executor.
   It checks mode flags (autonomous/manual/disabled), disabled loads, dry-run mode, and
   operator review requirements.
7. Autonomous operation remains the default future path. Operator/web UI remains an
   override/correction layer. Safety gates remain the final authority.
8. `execution_allowed_now` must always be `false` in this PR — actual execution is deferred.
9. Manual relay/switch ON/OFF remains available and unchanged.
10. Pump automation remains obsolete and disabled by default (PR 0008).
11. ML advisory may be used later; ML control remains disabled.

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
| Command safety gate model | `app/control/command_safety_gate.py` | Implemented (PR 0021) |
| Safety gate architecture doc | `.project-memory/COMMAND_SAFETY_GATES.md` | Documented (PR 0021) |
| **Controlled execution eligibility model** | `app/control/execution_eligibility.py` + `.project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md` | **This PR** |

## 5. Required Architecture Document

### File: `.project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md`

The document must state:

1. Execution eligibility is not hardware execution.
2. CommandProposal must pass safety gates before eligibility.
3. `eligible_for_future_executor` means a future executor may consider it.
4. `execution_allowed_now` remains `false` until a separate safety-reviewed execution PR.
5. Autonomous operation remains the default future path.
6. Operator/web UI remains override/correction, not mandatory approval for every autonomous command.
7. Disabled loads and safety gates block eligibility.
8. Dry-run mode never executes.
9. ML control remains deferred and requires separate safety-reviewed approval per ADR-0003.

## 6. Required Module Design

### Module Location

**Create:** `app/control/execution_eligibility.py`

**May update:** `app/control/__init__.py` (export all public types and functions)

### Module Dependencies

The module must use **only**:
- Python standard library (`dataclasses`, `typing`, `enum`)
- `app.control.command_arbitration` types: `CommandProposal`, `CommandIntentSource`
- `app.control.command_safety_gate` types: `CommandSafetyGateResult`, `SafetyGateStatus`

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
13. `execution_allowed_now` must always be `false`.

## 7. Required Types (5 Types)

### Type 1: `ExecutionEligibilityStatus`

```python
class ExecutionEligibilityStatus(Enum):
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    REVIEW_REQUIRED = "review_required"
    NO_PROPOSAL = "no_proposal"
```

### Type 2: `ExecutionEligibilityMode`

```python
class ExecutionEligibilityMode(Enum):
    AUTONOMOUS = "autonomous"
    MANUAL_OPERATOR = "manual_operator"
    DISABLED = "disabled"
```

### Type 3: `ExecutionEligibilityContext`

```python
@dataclass(frozen=True)
class ExecutionEligibilityContext:
    """Execution eligibility mode and policy flags.

    Pure data — no side effects.
    """
    controlled_execution_enabled: bool = False
    autonomous_execution_enabled: bool = True
    manual_operator_execution_enabled: bool = True
    require_operator_review_for_autonomous: bool = False
    require_operator_review_for_manual: bool = False
    disabled_load_ids: tuple[str, ...] = field(default_factory=tuple)
    dry_run_only: bool = False
```

### Type 4: `ExecutionEligibilityInput`

```python
@dataclass(frozen=True)
class ExecutionEligibilityInput:
    """Input to the execution eligibility evaluation.

    Pure data — no side effects.
    """
    proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    context: ExecutionEligibilityContext = field(default_factory=ExecutionEligibilityContext)
```

### Type 5: `ExecutionEligibilityResult`

```python
@dataclass(frozen=True)
class ExecutionEligibilityResult:
    """Result of an execution eligibility evaluation.

    status: overall eligibility status.
    eligible_for_future_executor: whether a future executor may consider this.
    execution_allowed_now: always false in this PR.
    requires_operator_review: whether operator review is needed.
    mode: which execution mode applies.
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers.
    safety_notes: advisory notes for operator visibility.
    """
    status: ExecutionEligibilityStatus = ExecutionEligibilityStatus.NO_PROPOSAL
    eligible_for_future_executor: bool = False
    execution_allowed_now: bool = False
    requires_operator_review: bool = False
    mode: ExecutionEligibilityMode = ExecutionEligibilityMode.DISABLED
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    safety_notes: str = ""
```

## 8. Required Pure Function

### `evaluate_execution_eligibility`

```python
def evaluate_execution_eligibility(
    eligibility_input: ExecutionEligibilityInput,
) -> ExecutionEligibilityResult
```

Behavior (evaluated in order, first match wins):

1. **no-proposal**: If `proposal` is None → `NO_PROPOSAL`, `eligible_for_future_executor=False`,
   `reason="no-proposal"`.

2. **no-safety-gate**: If `safety_gate_result` is None → `BLOCKED`,
   `eligible_for_future_executor=False`, `reason="no-safety-gate"`.

3. **safety-gate-blocked**: If `safety_gate_result.status` is `BLOCKED` or
   `safety_gate_result.execution_allowed` is `false` and
   `safety_gate_result.requires_operator_review` is `false`:
   → `BLOCKED`, `eligible_for_future_executor=False`, `reason="safety-gate-blocked"`.

4. **safety-review-required**: If `safety_gate_result.status` is `REVIEW_REQUIRED` or
   `safety_gate_result.requires_operator_review` is `true`:
   → `REVIEW_REQUIRED`, `eligible_for_future_executor=False`,
   `requires_operator_review=True`, `reason="safety-review-required"`.

5. **controlled-execution-disabled**: If `context.controlled_execution_enabled` is `false`:
   → `BLOCKED`, `eligible_for_future_executor=False`, `reason="controlled-execution-disabled"`.

6. **disabled-load**: If proposal intent `load_id` is in `context.disabled_load_ids`:
   → `BLOCKED`, `eligible_for_future_executor=False`, `reason="disabled-load"`.

7. **autonomous-disabled**: If proposal intent source is `AUTO_POLICY` and
   `context.autonomous_execution_enabled` is `false`:
   → `BLOCKED`, `eligible_for_future_executor=False`, `reason="autonomous-disabled"`.

8. **manual-operator-disabled**: If proposal intent source is `MANUAL_OPERATOR` and
   `context.manual_operator_execution_enabled` is `false`:
   → `BLOCKED`, `eligible_for_future_executor=False`, `reason="manual-operator-disabled"`.

9. **autonomous-review-required**: If proposal intent source is `AUTO_POLICY` and
   `context.require_operator_review_for_autonomous` is `true`:
   → `REVIEW_REQUIRED`, `eligible_for_future_executor=False`,
   `requires_operator_review=True`, `reason="autonomous-review-required"`.

10. **manual-review-required**: If proposal intent source is `MANUAL_OPERATOR` and
    `context.require_operator_review_for_manual` is `true`:
    → `REVIEW_REQUIRED`, `eligible_for_future_executor=False`,
    `requires_operator_review=True`, `reason="manual-review-required"`.

11. **dry-run-only**: If `context.dry_run_only` is `true`:
    → `ELIGIBLE`, `eligible_for_future_executor=True`, `execution_allowed_now=False`,
    `reason="dry-run-only"`, `safety_notes` must say this PR does not execute commands.

12. **eligible**: Otherwise:
    → `ELIGIBLE`, `eligible_for_future_executor=True`, `execution_allowed_now=False`,
    `reason="eligible"`, `safety_notes` must say this PR does not execute commands.

### Required Reason Strings

| Reason String | When Used |
|---|---|
| `"no-proposal"` | Input proposal is None |
| `"no-safety-gate"` | Safety gate result is None |
| `"safety-gate-blocked"` | Safety gate blocked the proposal |
| `"safety-review-required"` | Safety gate requires operator review |
| `"controlled-execution-disabled"` | Controlled execution is disabled |
| `"disabled-load"` | Target load is in disabled list |
| `"autonomous-disabled"` | Autonomous execution is disabled |
| `"manual-operator-disabled"` | Manual operator execution is disabled |
| `"autonomous-review-required"` | Operator review required for autonomous proposals |
| `"manual-review-required"` | Operator review required for manual proposals |
| `"dry-run-only"` | System is in dry-run mode |
| `"eligible"` | All checks passed |

### Required Product Rules

1. Eligibility is not execution.
2. `execution_allowed_now` must always be `false` in this PR.
3. `eligible_for_future_executor` can be `true` only when safety gate passed.
4. Autonomous operation remains default when enabled.
5. Operator/manual proposals can be eligible when safe and enabled.
6. Operator/manual proposals are not mandatory approval for autonomous proposals.
7. Disabled loads are blocked.
8. Dry-run mode can mark future eligibility but still cannot execute now.
9. No downstream layer may bypass safety gates.
10. This module must be pure, deterministic, import-safe, and side-effect-free.

## 9. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/execution_eligibility.py` | **Create** — execution eligibility module with 5 types + 1 function |
| `app/control/__init__.py` | **Edit** — export all public types and functions |
| `.project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md` | **Create** — architecture document |
| `scripts/check-execution-eligibility.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0022 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0022 in roadmap |
| `.project-memory/pr/0022-controlled-execution-eligibility-model/CODER_REPORT.txt` | **Create** — coder report |

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
- `app/control/command_safety_gate.py` (frozen from PR 0021)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 11. Static Validation Script

### File: `scripts/check-execution-eligibility.sh`

The script must:

1. Check `app/control/execution_eligibility.py` exists.
2. Check all five public types exist:
   `ExecutionEligibilityStatus`, `ExecutionEligibilityMode`, `ExecutionEligibilityContext`,
   `ExecutionEligibilityInput`, `ExecutionEligibilityResult`.
3. Check `evaluate_execution_eligibility` function exists.
4. Check `ELIGIBLE`, `BLOCKED`, `REVIEW_REQUIRED`, `NO_PROPOSAL` exist in `ExecutionEligibilityStatus`.
5. Check `AUTONOMOUS`, `MANUAL_OPERATOR`, `DISABLED` exist in `ExecutionEligibilityMode`.
6. Check `eligible_for_future_executor` field exists in `ExecutionEligibilityResult`.
7. Check `execution_allowed_now` field exists in `ExecutionEligibilityResult`.
8. Check `controlled_execution_enabled` field exists in `ExecutionEligibilityContext`.
9. Check `dry_run_only` field exists in `ExecutionEligibilityContext`.
10. Check all required reason strings exist:
    `no-proposal`, `no-safety-gate`, `safety-gate-blocked`, `safety-review-required`,
    `controlled-execution-disabled`, `disabled-load`, `autonomous-disabled`,
    `manual-operator-disabled`, `autonomous-review-required`, `manual-review-required`,
    `dry-run-only`, `eligible`.
11. Check `__init__.py` exports all public types and functions.
12. Check `.project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md` exists.
13. Check no executor was introduced (no `CommandExecutor`, no `execute` function).
14. Check forbidden runtime imports absent:
    `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`,
    `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
    `relay_device_manager`, `device_status_logger`, `openweather`, `dess`.
15. Check no hardware calls exist:
    `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`,
    `toggle_device`, `set_numeric`, `update_status`, `mark_switched`,
    `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`.
16. Check no file/env/network/weather/ML/logging/current-time calls exist:
    `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`,
    `os.getenv`, `requests`, `aiohttp`, `subprocess`, `logging`.
17. Check `execution_allowed_now` is not set to `true` (verify the value is never `True`).
18. Check runtime files are not modified (git diff check).
19. Print clear per-check output.
20. Exit 0 only when all checks pass.
21. Exit 1 if any check fails.

### GitHub Actions Integration

```yaml
      - name: 🔍 Execution eligibility check
        run: bash scripts/check-execution-eligibility.sh
```

Add after the existing command safety gate check.

## 12. CURRENT_STATE.md Update

```
## PR 0022 — Controlled Execution Eligibility Model
PR 0022 adds a pure controlled execution eligibility model in
`app/control/execution_eligibility.py` with 5 types (ExecutionEligibilityStatus,
ExecutionEligibilityMode, ExecutionEligibilityContext, ExecutionEligibilityInput,
ExecutionEligibilityResult) and the pure function
`evaluate_execution_eligibility()`. Documents controlled execution eligibility
architecture in `.project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md`.
execution_allowed_now is always false. No executor. No runtime wiring.
No API endpoints. No hardware execution.
```

## 13. ROADMAP.md Update

```
- [x] PR 0022 — Controlled execution eligibility model
```

## 14. Future PR Boundary

PR 0022 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Controlled executor (Tuya hardware dispatch) | Later |
| API endpoints for web UI / operator review | Later |
| Persistent storage (queue, proposal, safety logs) | Later |
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

- **Execution eligibility model only**: Pure types + single eligibility function
- **No executor**: `CommandExecutor` and `execute` function must NOT exist
- **`execution_allowed_now` is always false**: Verified by validation script
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
- **All existing modules unchanged**: PR 0014–0021 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
