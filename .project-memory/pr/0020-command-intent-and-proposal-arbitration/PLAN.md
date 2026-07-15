# PR 0020 — Command Intent and Proposal Arbitration

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `b499079ad2d7ebd7370ebb042d6436b4b47e3b71` |
| Branch | `git branch --show-current` | `0020-command-intent-and-proposal-arbitration` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0020-command-intent-and-proposal-arbitration` and working tree is clean.

## 2. Purpose

PR 0020 adds **command intent and proposal arbitration** — a pure, deterministic arbitration
function that decides between autonomous policy intents and manual operator overrides, producing
a single `CommandProposal` that future PRs may execute.

Key product direction:
- **Autonomous operation is the default path.**
- Manual/operator input is an **override and correction layer**, not a mandatory approval step.
- Safety gates and life-support constraints remain the final authority.

This PR introduces `CommandProposal` (the first time this name appears in implementation code).
It does NOT execute proposals, wire into runtime, add API endpoints, or call hardware.

## 3. Product Context

1. PRs 0014–0018C built the policy decision engine.
2. PR 0018D added scenario matrix tests locking engine behavior.
3. PR 0019 added the manual control queue boundary (pure enqueue/cancel).
4. PR 0020 adds the **arbitration layer** between the policy engine and the manual queue,
   producing a single command proposal.
5. Future PRs will add safety gate validation, then a controlled executor.
6. The architecture ensures autonomous regulation works by default; operator input overrides
   when explicitly provided.
7. Safety gates and life-support constraints are higher priority than operator convenience.
8. Manual relay/switch ON/OFF remains available and unchanged.
9. Pump automation remains obsolete and disabled by default (PR 0008).
10. ML advisory may be used later; ML control remains disabled.

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
| Scenario matrix tests | `.project-memory/POLICY_DECISION_SCENARIOS.md` + `scripts/check-policy-decision-scenarios.sh` | Implemented (PR 0018D) |
| Manual control queue boundary | `app/control/manual_control_queue.py` | Implemented (PR 0019) |
| **Command intent and proposal arbitration** | `app/control/command_arbitration.py` + `.project-memory/AUTONOMOUS_CONTROL_AND_OPERATOR_OVERRIDE.md` | **This PR** |

### 4.1 Grep Evidence Summary

**`CommandProposal` does not exist** in any `app/control/` implementation — this PR introduces it.

**`CommandQueue`** (exact class/function name) does not exist and must not be introduced.

**`CommandIntentSource` and `CommandProposalStatus`** do not exist — this PR introduces them.

**`AUTO_POLICY` and `MANUAL_OPERATOR`** do not exist — this PR introduces them.

**All existing validation scripts pass** (15 scripts including all policy, scenarios, and manual queue checks).

## 5. Required Architecture Document

### File: `.project-memory/AUTONOMOUS_CONTROL_AND_OPERATOR_OVERRIDE.md`

The document must state these key architecture principles:

1. **Autonomous operation is the default path.** If the operator does nothing, the system
   regulates itself automatically using the policy decision engine.
2. **Operator/web UI input is override/correction**, not required approval for every command.
   A future web UI writes operator intent into the control layer (`ManualControlQueue`),
   not directly to Tuya/hardware.
3. **Safety gates remain the final authority** before any future execution.
4. **Life-support constraints remain higher priority** than discretionary operator convenience.
5. **Future execution is allowed only** after separate controlled-execution safety gates (PR 0020+).
6. **ML advisory/control remains deferred.** ML control requires separate safety-reviewed
   approval per ADR-0003.
7. **This document does not require engineering changes** — it records the architecture direction.

## 6. Required Module Design

### Module Location

**Create:** `app/control/command_arbitration.py`

**May update:** `app/control/__init__.py` (export all public types and functions)

### Module Dependencies

The module must use **only**:
- Python standard library (`dataclasses`, `typing`, `enum`)
- `app.control.domain` types: `DesiredState`
- `app.control.policy_models` types: `PolicyDecisionResult`
- `app.control.manual_control_queue` types: `ManualControlQueueSnapshot`,
  `ManualControlQueueItem`, `ManualControlStatus`

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

## 7. Required Types (6 Types)

### Type 1: `CommandIntentSource`

```python
class CommandIntentSource(Enum):
    AUTO_POLICY = "auto_policy"
    MANUAL_OPERATOR = "manual_operator"
    SCHEDULE = "schedule"
    SAFETY_SYSTEM = "safety_system"
    MAINTENANCE = "maintenance"
```

### Type 2: `CommandProposalStatus`

```python
class CommandProposalStatus(Enum):
    PROPOSED = "proposed"
    BLOCKED = "blocked"
    NO_ACTION = "no_action"
```

### Type 3: `CommandIntent`

```python
@dataclass(frozen=True)
class CommandIntent:
    """A specific intent to change a load's state.

    Pure data — no hardware execution, no side effects.
    created_at is a caller-provided string.
    """
    intent_id: str
    load_id: str
    desired_state: DesiredState
    source: CommandIntentSource
    reason: str = ""
    priority: int = 0
    created_at: str = ""
    ttl_seconds: float = 0.0
```

### Type 4: `CommandProposal`

```python
@dataclass(frozen=True)
class CommandProposal:
    """A proposed command after arbitration between policy and operator intents.

    execution_eligible: true if the proposal may be executed by a future executor.
    requires_operator_review: true if the proposal should wait for human approval.
    blocked_by: list of blocking condition identifiers.
    safety_notes: advisory notes for operator visibility.

    Pure data — no hardware execution, no side effects.
    """
    proposal_id: str = ""
    intent: CommandIntent | None = None
    status: CommandProposalStatus = CommandProposalStatus.NO_ACTION
    execution_eligible: bool = False
    requires_operator_review: bool = False
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    safety_notes: str = ""
    created_at: str = ""
```

### Type 5: `CommandArbitrationInput`

```python
@dataclass(frozen=True)
class CommandArbitrationInput:
    """Input to the arbitration function.

    policy_decision: the latest decision from evaluate_policy_decision.
    manual_queue_snapshot: current state of the manual control queue.
    autonomous_enabled: whether autonomous policy decisions are allowed.
    operator_overrides_enabled: whether manual operator commands override policy.
    safety_blocked_by: active safety gate blockers (e.g. "life-support-at-risk").

    Pure data — no side effects.
    """
    policy_decision: PolicyDecisionResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None
    autonomous_enabled: bool = True
    operator_overrides_enabled: bool = True
    safety_blocked_by: tuple[str, ...] = field(default_factory=tuple)
```

### Type 6: `CommandArbitrationResult`

```python
@dataclass(frozen=True)
class CommandArbitrationResult:
    """Result of command intent arbitration.

    accepted: whether a proposal was created.
    proposal: the resulting CommandProposal (if any).
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers.
    """
    accepted: bool = False
    proposal: CommandProposal | None = None
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
```

## 8. Required Pure Function

### `arbitrate_command_intent`

```python
def arbitrate_command_intent(
    arbitration_input: CommandArbitrationInput,
) -> CommandArbitrationResult
```

Behavior (evaluated in order, first match wins):

1. **Safety blocked**: If `safety_blocked_by` is non-empty → return `BLOCKED` proposal with
   `accepted=False`, `reason="safety-blocked"`, `blocked_by=safety_blocked_by`.

2. **Manual operator override**: If `operator_overrides_enabled=True` and there exists a
   non-terminal `QUEUED` item in `manual_queue_snapshot.items`:
   - Create `CommandIntent` with `source=MANUAL_OPERATOR`, `load_id`, `desired_state`
     from the manual command.
   - Create `CommandProposal` with `status=PROPOSED`, `execution_eligible=True`,
     `requires_operator_review=False`, `intent` set.
   - Return `accepted=True`, `reason="manual-operator-override"`.

3. **Autonomous policy intent**: If `autonomous_enabled=True` and `policy_decision` has an
   actionable decision (`ALLOW_ON`, `FORCE_OFF`, `PREFER_OFF`) with a non-empty `target_load_id`:
   - Map ALLOW_ON → desired_state "on", FORCE_OFF/PREFER_OFF → desired_state "off".
   - Create `CommandIntent` with `source=AUTO_POLICY`.
   - Create `CommandProposal` with `status=PROPOSED`, `execution_eligible=True`,
     `requires_operator_review=False`.
   - Return `accepted=True`, `reason="auto-policy-intent"`.

4. **Autonomous disabled**: If `autonomous_enabled=False` and no manual command matches
   → return `NO_ACTION`, `reason="autonomous-disabled"`.

5. **No target load**: If `policy_decision` has an actionable decision but no `target_load_id`
   → return `NO_ACTION`, `reason="no-target-load"`.

6. **No actionable intent**: If no manual command and no actionable policy decision
   → return `NO_ACTION`, `reason="no-actionable-intent"`.

### Required Reason Strings

| Reason String | When Used |
|---|---|
| `"safety-blocked"` | Safety blocker is active |
| `"manual-operator-override"` | Manual operator command overrides policy |
| `"auto-policy-intent"` | Autonomous policy proposes an action |
| `"autonomous-disabled"` | Autonomous mode is disabled and no manual override |
| `"no-target-load"` | Policy decision has decision but no target_load_id |
| `"no-actionable-intent"` | No actionable intent from any source |

## 9. Naming Boundary

- `CommandProposal` is the correct type name for PR 0020 — this is the first PR introducing it.
- `CommandQueue` as an exact class/function name must NOT exist.
- `CommandExecutor` as a class/function must NOT exist.

## 10. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/command_arbitration.py` | **Create** — command arbitration module with 6 types + 1 function |
| `app/control/__init__.py` | **Edit** — export all public types and functions |
| `.project-memory/AUTONOMOUS_CONTROL_AND_OPERATOR_OVERRIDE.md` | **Create** — architecture document |
| `scripts/check-command-arbitration.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0020 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0020 in roadmap |
| `.project-memory/pr/0020-command-intent-and-proposal-arbitration/CODER_REPORT.txt` | **Create** — coder report |

## 11. Forbidden Implementation Files

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
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 12. Static Validation Script

### File: `scripts/check-command-arbitration.sh`

The script must:

1. Check `app/control/command_arbitration.py` exists.
2. Check all six public types exist:
   `CommandIntentSource`, `CommandProposalStatus`, `CommandIntent`, `CommandProposal`,
   `CommandArbitrationInput`, `CommandArbitrationResult`.
3. Check `arbitrate_command_intent` function exists.
4. Check `AUTO_POLICY` and `MANUAL_OPERATOR` exist in `CommandIntentSource`.
5. Check `CommandProposal` exists.
6. Check `execution_eligible` field exists in `CommandProposal`.
7. Check `requires_operator_review` field exists in `CommandProposal`.
8. Check `safety_blocked_by` field exists in `CommandArbitrationInput`.
9. Check all required reason strings exist:
   `safety-blocked`, `manual-operator-override`, `auto-policy-intent`,
   `autonomous-disabled`, `no-target-load`, `no-actionable-intent`.
10. Check `__init__.py` exports all public types and functions.
11. Check `.project-memory/AUTONOMOUS_CONTROL_AND_OPERATOR_OVERRIDE.md` exists.
12. Check autonomous-default language exists in the doc.
13. Check no exact class/function `CommandQueue` was introduced.
14. Check no executor was introduced (no `CommandExecutor` class/function, no `execute` function).
15. Check forbidden runtime imports absent:
    `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`,
    `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
    `relay_device_manager`, `device_status_logger`, `openweather`, `dess`.
16. Check no hardware calls exist:
    `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`,
    `toggle_device`, `set_numeric`, `update_status`, `mark_switched`,
    `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`.
17. Check no file/env/network/weather/ML/logging/current-time calls exist:
    `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`,
    `os.getenv`, `requests`, `aiohttp`, `subprocess`, `logging`.
18. Check runtime files are not modified (git diff check).
19. Print clear per-check output.
20. Exit 0 only when all checks pass.
21. Exit 1 if any check fails.

### GitHub Actions Integration

```yaml
      - name: 🔍 Command arbitration check
        run: bash scripts/check-command-arbitration.sh
```

Add after the existing manual control queue check.

## 13. CURRENT_STATE.md Update

```
## PR 0020 — Command Intent and Proposal Arbitration
PR 0020 adds command intent/proposal arbitration in
`app/control/command_arbitration.py` with 6 types (CommandIntentSource,
CommandProposalStatus, CommandIntent, CommandProposal, CommandArbitrationInput,
CommandArbitrationResult) and the pure function
`arbitrate_command_intent()`. Introduces CommandProposal type.
Documents autonomous-default architecture in
`.project-memory/AUTONOMOUS_CONTROL_AND_OPERATOR_OVERRIDE.md`.
No executor. No runtime wiring. No API endpoints. No hardware execution.
```

## 14. ROADMAP.md Update

```
- [x] PR 0020 — Command intent and proposal arbitration
```

## 15. Future PR Boundary

PR 0020 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Safety gate model and validation | Later |
| Controlled executor (Tuya hardware dispatch) | Later |
| API endpoints for web UI | Later |
| Persistent queue and proposal storage | Later |
| ML advisory integration | Later |
| ML control (safety gates per ADR-0003) | Much later |
| Runtime wiring | Deferred |

## 16. Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes this plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation |

## 17. Boundary Confirmations

- **Command proposal arbitration only**: Pure types + single arbitration function
- **`CommandProposal` introduced**: First time this type exists in code
- **No executor**: `CommandExecutor` and `execute` function must NOT exist
- **No `CommandQueue` class**: Correct name boundary maintained
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No runtime wiring**: Not connected to any runtime component
- **No API endpoints**: Deferred
- **No persistent storage**: Deferred
- **No config loading**: Does not read YAML, env vars, or files
- **No system clock**: Timestamps are caller-provided strings
- **No automation enabled**: Pump automation disabled per PR 0008
- **No ML control**: ML advisory is advice-only; ML control deferred per ADR-0003
- **Autonomous default architecture documented**: Operator overrides are optional corrections
- **Manual switch control preserved**: All switch methods unchanged
- **Pump automation remains disabled**: Per PR 0008
- **Docker/GitOps**: `build-and-deploy.yml` untouched; external GitOps boundary respected
- **All existing modules unchanged**: PR 0014–0019 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
