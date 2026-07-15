# PR 0019 — Manual Control Queue Boundary

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `eab27353ef8766c90bef8d00e5d04d2762ccfa00` |
| Branch | `git branch --show-current` | `0019-manual-control-queue-boundary` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0019-manual-control-queue-boundary` and working tree is clean.

## 2. Purpose

PR 0019 introduces a **manual control queue boundary** — a pure, import-safe module that models
and safely stores manual user intent. It provides types and pure functions to enqueue and cancel
manual control commands without executing hardware, creating command proposals, or wiring into
runtime.

This is not hardware execution. This is not command proposal. This is not a runtime API.
This is not automation. The queue is a passive boundary that safely records what a human
operator wants to do — a future PR will execute those intents through the Tuya adapter.

## 3. Product Context

1. PRs 0014–0018C built the policy decision engine (readiness, health, schedule, weather
   adjustment, operating boundaries, passive models, pure decision function).
2. PR 0018D added scenario matrix tests locking the engine's behavior.
3. PR 0019 provides a **manual intent queue** — storing "I want to turn this load ON/OFF"
   before any hardware execution is attempted.
4. PR 0020 will add command proposal before automatic execution.
5. Later PRs will wire the queue to hardware execution (Tuya adapter) with safety gates.
6. Manual relay/switch ON/OFF remains available and unchanged.
7. Pump automation remains obsolete and disabled by default (PR 0008).
8. ML advisory may be used later; ML control remains disabled.

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
| **Manual control queue boundary** | `app/control/manual_control_queue.py` | **This PR** |

### 4.1 Existing Types in `app/control/domain.py`

The existing `ControlCommand`, `CommandSource`, and `DesiredState` types from PR 0009 provide
a foundation. PR 0019's `ManualControlCommand` is distinct: it's a frozen dataclass specific
to the manual control queue domain, with `idempotency_key`, `requested_by`, and `reason`.

### 4.2 Grep Evidence Summary

**`CommandProposal` and `CommandQueue`** (exact class names) do not exist — good.

**`CommandQueue`** appears in future PR roadmap references but is not implemented.

**`ManualControlQueue`** naming is deliberately used instead of `CommandQueue` to maintain clear
separation from future command proposal models (PR 0020).

**All existing validation scripts pass** (14 scripts).

## 5. Required Module Design

### Module Location

**Create:** `app/control/manual_control_queue.py`

**May update:** `app/control/__init__.py` (export all public types and functions)

### Module Dependencies

The module must use **only**:
- Python standard library (`dataclasses`, `typing`, `enum`)
- `app.control.domain` types: `DesiredState`

The module must **not** import:
- `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`
- `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
  `relay_device_manager`, `device_status_logger`, `openweather`, `dess`
- `app.control.policy_models`, `app.control.policy_decision`, `app.control.energy_policy`
- `app.control.readiness`, `app.control.health`, `app.control.schedule_profile`,
  `app.control.weather_adjustment`
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
9. No command proposal creation.
10. No runtime service calls.
11. No loading of `examples/energy_policy.example.yaml`.
12. Pure, deterministic functions.

## 6. Required Types (5 Types)

### Type 1: `ManualControlStatus`

```python
class ManualControlStatus(Enum):
    QUEUED = "queued"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
```

### Type 2: `ManualControlCommand`

```python
@dataclass(frozen=True)
class ManualControlCommand:
    """A manual control intent from a human operator.

    Pure data — no hardware execution, no side effects.
    created_at is a caller-provided string, NOT generated with datetime.now().
    """
    command_id: str
    load_id: str
    desired_state: DesiredState
    source: str = "manual"
    requested_by: str = ""
    reason: str = ""
    idempotency_key: str = ""
    created_at: str = ""
```

### Type 3: `ManualControlQueueItem`

```python
@dataclass(frozen=True)
class ManualControlQueueItem:
    """An item in the manual control queue with status tracking.

    Pure data — no hardware execution, no side effects.
    updated_at is a caller-provided string, NOT generated with datetime.now().
    """
    command: ManualControlCommand
    status: ManualControlStatus = ManualControlStatus.QUEUED
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    safety_notes: str = ""
    updated_at: str = ""
```

### Type 4: `ManualControlQueueSnapshot`

```python
@dataclass(frozen=True)
class ManualControlQueueSnapshot:
    """A snapshot of all items in the manual control queue.

    Pure data — no side effects.
    """
    items: tuple[ManualControlQueueItem, ...] = field(default_factory=tuple)
```

### Type 5: `ManualControlQueueResult`

```python
@dataclass(frozen=True)
class ManualControlQueueResult:
    """Result of a manual control queue operation.

    accepted: whether the operation was accepted.
    snapshot: the new queue snapshot after the operation.
    item: the affected queue item (if any).
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers (if rejected).
    """
    accepted: bool = False
    snapshot: ManualControlQueueSnapshot | None = None
    item: ManualControlQueueItem | None = None
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
```

## 7. Required Pure Functions (2 Functions)

### Function 1: `enqueue_manual_control_command`

```python
def enqueue_manual_control_command(
    snapshot: ManualControlQueueSnapshot,
    command: ManualControlCommand,
) -> ManualControlQueueResult
```

Behavior:
1. If `command.command_id` is empty → rejected.
2. If `command.load_id` is empty → rejected.
3. If a non-terminal item (QUEUED) with the same `command_id` already exists → rejected
   (duplicate command_id).
4. If `command.idempotency_key` is non-empty and a non-terminal item with the same
   `idempotency_key` already exists → rejected (duplicate idempotency).
5. Otherwise, create a new `ManualControlQueueItem` with status `QUEUED`, add it to a new
   `ManualControlQueueSnapshot`, and return `accepted=True`.
6. Pure — no side effects, no I/O, no time reads.
7. Deterministic.

### Function 2: `cancel_manual_control_command`

```python
def cancel_manual_control_command(
    snapshot: ManualControlQueueSnapshot,
    command_id: str,
    reason: str = "cancelled",
) -> ManualControlQueueResult
```

Behavior:
1. If `command_id` is empty → rejected.
2. Find the first non-terminal item (QUEUED) with matching `command_id`.
3. If found, return a new snapshot with that item's status changed to `CANCELLED` and
   `updated_at` set to the caller-provided reason context (the reason string is updated;
   no system time reads).
4. If not found → return `accepted=False`, reason describing the unknown command_id.
5. Pure — no side effects, no I/O, no time reads.
6. Deterministic.

## 8. Safety and Purity Requirements

1. No function executes hardware.
2. No function creates a command proposal (`CommandProposal` type is deferred to PR 0020).
3. No function calls runtime services.
4. No function reads current time (`created_at` and `updated_at` are caller-provided strings).
5. `created_at` and `updated_at` are strings — the module never calls `time.time` or `datetime.now`.
6. The module is import-safe and deterministic.
7. Same input produces same output.

## 9. Naming Boundary

- Use `ManualControlQueue` naming throughout (module, types, functions).
- `CommandQueue` as an exact class/type name must NOT exist.
- `CommandProposal` as a type must NOT exist (deferred to PR 0020).

## 10. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/manual_control_queue.py` | **Create** — manual control queue module with 5 types + 2 functions |
| `app/control/__init__.py` | **Edit** — export all public types and functions |
| `scripts/check-manual-control-queue.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0019 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0019 in roadmap |
| `.project-memory/pr/0019-manual-control-queue-boundary/CODER_REPORT.txt` | **Create** — coder report |

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
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 12. Static Validation Script

### File: `scripts/check-manual-control-queue.sh`

The script must:

1. Check `app/control/manual_control_queue.py` exists.
2. Check all five public types exist:
   `ManualControlStatus`, `ManualControlCommand`, `ManualControlQueueItem`,
   `ManualControlQueueSnapshot`, `ManualControlQueueResult`.
3. Check `enqueue_manual_control_command` function exists.
4. Check `cancel_manual_control_command` function exists.
5. Check `command_id`, `load_id`, `desired_state`, `source`, `requested_by`,
   `idempotency_key` fields exist in `ManualControlCommand`.
6. Check `QUEUED`, `CANCELLED`, `REJECTED`, `EXPIRED` exist in `ManualControlStatus`.
7. Check `@dataclass(frozen=True)` appears.
8. Check `__init__.py` exports all public types and functions.
9. Check `CommandProposal` is absent from the module.
10. Check `CommandQueue` (exact class/type name) is absent.
11. Check forbidden runtime imports absent:
    `app.tuya`, `app.service`, `app.devices`, `app.monitoring`, `app.ml`, `app.weather`,
    `smart_home_controller`, `relay_tuya_controller`, `relay_channel_device`,
    `relay_device_manager`, `device_status_logger`, `openweather`, `dess`.
12. Check no hardware calls exist:
    `switch_on_device`, `switch_off_device`, `switch_binary`, `switch_device`,
    `toggle_device`, `set_numeric`, `update_status`, `mark_switched`,
    `can_switch`, `ready_to_switch_on`, `ready_to_switch_off`, `is_device_on`.
13. Check no file/env/network/weather/ML/logging/current-time calls exist:
    `time.time` (outside docstrings), `datetime.now`, `open(`, `yaml.safe_load`,
    `os.getenv`, `requests`, `aiohttp`, `subprocess`, `logging`.
14. Check runtime files are not modified (git diff check).
15. Print clear per-check output.
16. Exit 0 only when all checks pass.
17. Exit 1 if any check fails.

### GitHub Actions Integration

```yaml
      - name: 🔍 Manual control queue check
        run: bash scripts/check-manual-control-queue.sh
```

Add after the existing policy decision scenarios check.

## 13. CURRENT_STATE.md Update

```
## PR 0019 — Manual Control Queue Boundary
PR 0019 adds a pure manual control queue boundary in
`app/control/manual_control_queue.py` with 5 passive types (ManualControlStatus,
ManualControlCommand, ManualControlQueueItem, ManualControlQueueSnapshot,
ManualControlQueueResult) and 2 pure functions (enqueue_manual_control_command,
cancel_manual_control_command). No hardware execution. No command proposal.
No runtime wiring. No API endpoints. No automation.
```

## 14. ROADMAP.md Update

```
- [x] PR 0019 — Manual control queue boundary
```

## 15. Future PR Boundary

PR 0019 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Command proposal model | 0020 |
| Wire policy decisions to command proposal | 0020 |
| API endpoints for manual control | Later |
| Persistent queue storage | Later |
| Queue executor (Tuya hardware dispatch) | Later |
| Controlled execution with safety gates | 0021+ |
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

- **Manual control queue only**: Types + pure enqueue/cancel functions
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No command proposal**: `CommandProposal` type deferred to PR 0020
- **No `CommandQueue` class name**: Uses `ManualControlQueue` naming
- **No runtime wiring**: Not connected to any runtime component
- **No API endpoints**: Deferred
- **No persistent storage**: Deferred
- **No config loading**: Does not read YAML, env vars, or files
- **No system clock**: Timestamps are caller-provided strings
- **No automation enabled**: Pump automation disabled per PR 0008
- **No ML control**: ML advisory is advice-only; ML control deferred per ADR-0003
- **Manual switch control preserved**: All switch methods unchanged
- **Pump automation remains disabled**: Per PR 0008
- **Docker/GitOps**: `build-and-deploy.yml` untouched; external GitOps boundary respected
- **All existing evaluators, models, and engine unchanged**: PR 0014–0018D frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
