# PR 0023 — Runtime Read-Only Control State Snapshot

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `9789249177b1e89034c57724e31324490e9e3141` |
| Branch | `git branch --show-current` | `0023-runtime-read-only-control-state-snapshot` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0023-runtime-read-only-control-state-snapshot` and working tree is clean.

## 2. Purpose

PR 0023 adds a **read-only control state snapshot model** — a pure function
`build_control_state_snapshot()` that packages already-computed control-layer state
(load candidates, policy decision, manual queue, command proposal, safety gate result,
execution eligibility result, battery/load budget summary, autonomous/operator mode flags)
into a stable passive view suitable for future web UI, observability, and debugging.

This PR does NOT execute commands, add API endpoints, wire into runtime, call Tuya/hardware,
read devices directly, fetch weather, or call ML. It only packages what has already been
computed by the control layer.

## 3. Product Context

1. PRs 0014–0018C built the policy decision engine.
2. PR 0018D added scenario matrix tests locking engine behavior.
3. PR 0019 added the manual control queue boundary.
4. PR 0020 added command intent/proposal arbitration (`CommandProposal` introduced).
5. PR 0021 added the command safety gate model.
6. PR 0022 added the controlled execution eligibility model.
7. PR 0023 adds the **read-only control state snapshot** — a packaging function that
   collects all computed state into one stable view for a future web UI.
8. Autonomous operation remains the default path. Operator/web UI remains an
   override/correction layer. Safety gates remain the final authority.
9. `execution_allowed_now` remains `false` — actual execution is deferred.
10. Manual relay/switch ON/OFF remains available and unchanged.
11. Pump automation remains obsolete and disabled by default (PR 0008).
12. ML advisory may be used later; ML control remains disabled.

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
| Controlled execution eligibility model | `app/control/execution_eligibility.py` | Implemented (PR 0022) |
| Controlled execution eligibility doc | `.project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md` | Documented (PR 0022) |
| **Read-only control state snapshot** | `app/control/control_state_snapshot.py` + `.project-memory/CONTROL_STATE_SNAPSHOT.md` | **This PR** |

## 5. Required Architecture Document

### File: `.project-memory/CONTROL_STATE_SNAPSHOT.md`

The document must state:

1. Control state snapshot is read-only.
2. It is for future web UI, observability, debugging, and controller visibility.
3. It does not execute commands.
4. It does not call Tuya or hardware.
5. It does not fetch weather or call ML.
6. It does not recompute policy decisions.
7. It only packages already-computed control-layer state.
8. `execution_allowed_now` remains controlled by execution eligibility and remains `false`
   until a future safety-reviewed execution PR.
9. Web UI may display this snapshot in the future.
10. Future web UI must still write operator intent through the control layer, not direct
    hardware calls.

## 6. Required Module Design

### Module Location

**Create:** `app/control/control_state_snapshot.py`

**May update:** `app/control/__init__.py` (export all public types and functions)

### Module Dependencies

The module must use **only**:
- Python standard library (`dataclasses`, `typing`, `enum`)
- `app.control.policy_models` types: `LoadCandidate`, `EnergyBudget`, `BatteryOperatingWindow`
- `app.control.policy_decision` types: `PolicyDecisionResult`
- `app.control.command_arbitration` types: `CommandProposal`
- `app.control.command_safety_gate` types: `CommandSafetyGateResult`, `SafetyGateStatus`
- `app.control.execution_eligibility` types: `ExecutionEligibilityResult`, `ExecutionEligibilityMode`
- `app.control.manual_control_queue` types: `ManualControlQueueSnapshot`

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
7. No `time.time` or `datetime.now` calls (`created_at` is caller-provided).
8. No device switching.
9. No command execution.
10. No runtime service calls.
11. No loading of `examples/energy_policy.example.yaml`.
12. Pure, deterministic function.

## 7. Required Types (6 Types)

### Type 1: `ControlStateSnapshotStatus`

```python
class ControlStateSnapshotStatus(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
```

### Type 2: `LoadControlSnapshot`

```python
@dataclass(frozen=True)
class LoadControlSnapshot:
    """A single load's control state summary for display and observability.

    Pure data — no hardware calls, no side effects.
    """
    load_id: str = ""
    display_name: str = ""
    configured_load_watts: float = 0.0
    currently_on: bool = False
    controllable: bool = True
    is_life_support: bool = False
    roles: tuple[str, ...] = field(default_factory=tuple)
    status: str = "unknown"
    notes: str = ""
```

### Type 3: `ControlPipelineSnapshot`

```python
@dataclass(frozen=True)
class ControlPipelineSnapshot:
    """Snapshot of the decision pipeline state for a target load.

    Pure data — no recomputation, no side effects.
    """
    policy_decision: PolicyDecisionResult | None = None
    command_proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    execution_eligibility: ExecutionEligibilityResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None
```

### Type 4: `ControlModeSnapshot`

```python
@dataclass(frozen=True)
class ControlModeSnapshot:
    """Snapshot of execution mode flags.

    Pure data — no side effects.
    execution_allowed_now and eligible_for_future_executor are copied
    from execution eligibility if present; this module does not set them.
    """
    autonomous_enabled: bool = True
    operator_overrides_enabled: bool = True
    controlled_execution_enabled: bool = True
    dry_run_only: bool = False
    execution_allowed_now: bool = False
    eligible_for_future_executor: bool = False
```

### Type 5: `ControlStateSnapshotInput`

```python
@dataclass(frozen=True)
class ControlStateSnapshotInput:
    """All input data needed to build a control state snapshot.

    Pure data — no side effects.
    snapshot_id and created_at are caller-provided.
    This function does not generate UUIDs or read system time.
    """
    snapshot_id: str = ""
    created_at: str = ""
    loads: tuple[LoadCandidate, ...] = field(default_factory=tuple)
    policy_decision: PolicyDecisionResult | None = None
    command_proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    execution_eligibility: ExecutionEligibilityResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None
    energy_budget: EnergyBudget | None = None
    battery_window: BatteryOperatingWindow | None = None
    mode: ControlModeSnapshot | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 6: `ControlStateSnapshot`

```python
@dataclass(frozen=True)
class ControlStateSnapshot:
    """Complete read-only control state snapshot.

    Pure data — no side effects, no execution, no recomputation.
    """
    snapshot_id: str = ""
    created_at: str = ""
    status: ControlStateSnapshotStatus = ControlStateSnapshotStatus.UNKNOWN
    loads: tuple[LoadControlSnapshot, ...] = field(default_factory=tuple)
    pipeline: ControlPipelineSnapshot | None = None
    mode: ControlModeSnapshot | None = None
    energy_budget: EnergyBudget | None = None
    battery_window: BatteryOperatingWindow | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
```

## 8. Required Pure Function

### `build_control_state_snapshot`

```python
def build_control_state_snapshot(
    snapshot_input: ControlStateSnapshotInput | None,
) -> ControlStateSnapshot
```

Behavior:

1. If `snapshot_input` is None → return `ControlStateSnapshot` with `status=UNKNOWN`,
   `reason="no-input"` (set via `notes`/`warnings`).
2. Convert each `LoadCandidate` in `snapshot_input.loads` into a `LoadControlSnapshot`.
3. Package `ControlPipelineSnapshot` from the provided pipeline objects (do NOT recompute).
4. If `mode` is provided, use it; otherwise create a `ControlModeSnapshot` with defaults.
5. Copy `energy_budget` and `battery_window` if provided (may be None).
6. Determine overall snapshot `status`:
   - If safety gate result is `BLOCKED` or eligibility is `BLOCKED` → `BLOCKED`.
   - If safety gate or eligibility requires review → `DEGRADED`.
   - If required pipeline pieces are missing → `DEGRADED` or `UNKNOWN`.
   - Otherwise → `OK`.
7. Collect warnings for any missing pipeline pieces.
8. Return the snapshot.

### Required Warning/Reason Strings

| String | When Used |
|---|---|
| `"no-input"` | Input is None |
| `"missing-policy-decision"` | Policy decision is None |
| `"missing-command-proposal"` | Command proposal is None |
| `"missing-safety-gate-result"` | Safety gate result is None |
| `"missing-execution-eligibility"` | Execution eligibility is None |
| `"safety-gate-blocked"` | Safety gate blocked |
| `"execution-eligibility-blocked"` | Execution eligibility blocked |
| `"review-required"` | Review is required by safety gate or eligibility |
| `"read-only-snapshot"` | This snapshot is read-only |
| `"no-execution"` | This PR does not execute commands |

### Required Product Rules

1. Snapshot is read-only/passive.
2. `execution_allowed_now` must be copied from eligibility if present, but this PR must not change it.
3. `eligible_for_future_executor` must be copied from eligibility if present.
4. Snapshot must tolerate `None` fields.
5. Snapshot must not mutate inputs.
6. Snapshot must not execute, queue, propose, fetch, persist, or call runtime.

## 9. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/control_state_snapshot.py` | **Create** — control state snapshot module with 6 types + 1 function |
| `app/control/__init__.py` | **Edit** — export all public types and functions |
| `.project-memory/CONTROL_STATE_SNAPSHOT.md` | **Create** — architecture document |
| `scripts/check-control-state-snapshot.sh` | **Create** — static validation script |
| `.github/workflows/validate.yml` | **Edit** — add one validation step |
| `.project-memory/CURRENT_STATE.md` | **Edit** — add PR 0023 section |
| `.project-memory/ROADMAP.md` | **Edit** — mark PR 0023 in roadmap |
| `.project-memory/pr/0023-runtime-read-only-control-state-snapshot/CODER_REPORT.txt` | **Create** — coder report |

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
- `app/control/execution_eligibility.py` (frozen from PR 0022)
- `examples/energy_policy.example.yaml`
- `service/**`
- `shared_state/**`
- Config files, data files
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `.github/workflows/build-and-deploy.yml`
- Existing validation scripts (other than adding the new step to `validate.yml`)

## 11. Static Validation Script

### File: `scripts/check-control-state-snapshot.sh`

The script must:

1. Check `app/control/control_state_snapshot.py` exists.
2. Check all six public types exist:
   `ControlStateSnapshotStatus`, `LoadControlSnapshot`, `ControlPipelineSnapshot`,
   `ControlModeSnapshot`, `ControlStateSnapshotInput`, `ControlStateSnapshot`.
3. Check `build_control_state_snapshot` function exists.
4. Check `OK`, `DEGRADED`, `BLOCKED`, `UNKNOWN` exist in `ControlStateSnapshotStatus`.
5. Check `LoadControlSnapshot` exists.
6. Check `ControlPipelineSnapshot` exists.
7. Check `ControlModeSnapshot` exists.
8. Check `execution_allowed_now` field exists in `ControlModeSnapshot`.
9. Check `eligible_for_future_executor` field exists in `ControlModeSnapshot`.
10. Check all required warning/reason strings exist:
    `no-input`, `missing-policy-decision`, `missing-command-proposal`,
    `missing-safety-gate-result`, `missing-execution-eligibility`,
    `safety-gate-blocked`, `execution-eligibility-blocked`, `review-required`,
    `read-only-snapshot`, `no-execution`.
11. Check `__init__.py` exports all public types and functions.
12. Check `.project-memory/CONTROL_STATE_SNAPSHOT.md` exists.
13. Check read-only/no-execution language exists in the doc.
14. Check no executor was introduced (no `CommandExecutor`, no `execute` function).
15. Check forbidden runtime imports absent.
16. Check no hardware calls exist.
17. Check no file/env/network/weather/ML/logging/current-time calls exist.
18. Check runtime files are not modified (git diff check).
19. Print clear per-check output.
20. Exit 0 only when all checks pass.
21. Exit 1 if any check fails.

### GitHub Actions Integration

```yaml
      - name: 🔍 Control state snapshot check
        run: bash scripts/check-control-state-snapshot.sh
```

Add after the existing execution eligibility check.

## 12. CURRENT_STATE.md Update

```
## PR 0023 — Runtime Read-Only Control State Snapshot
PR 0023 adds a read-only control state snapshot model in
`app/control/control_state_snapshot.py` with 6 types (ControlStateSnapshotStatus,
LoadControlSnapshot, ControlPipelineSnapshot, ControlModeSnapshot,
ControlStateSnapshotInput, ControlStateSnapshot) and the pure function
`build_control_state_snapshot()`. Packages already-computed control-layer state
for future web UI/observability. No executor. No runtime wiring. No API endpoints.
No hardware execution.
```

## 13. ROADMAP.md Update

```
- [x] PR 0023 — Runtime read-only control state snapshot
```

## 14. Future PR Boundary

PR 0023 explicitly defers:

| Deferred Work | Target PR |
|---|---|
| Web UI API endpoints | Later |
| Controlled executor (Tuya hardware dispatch) | Later |
| Persistent storage (queue, proposal, safety logs, snapshots) | Later |
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

- **Read-only snapshot packaging only**: Pure function packages already-computed state
- **No executor**: `CommandExecutor` and `execute` function must NOT exist
- **No runtime wiring**: Not connected to any runtime component
- **No API endpoints**: Deferred
- **No persistent storage**: Deferred
- **No device reads**: Does not read hardware directly
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No config loading**: Does not read YAML, env vars, or files
- **No system clock**: `created_at` is caller-provided
- **No automation enabled**: Pump automation disabled per PR 0008
- **No ML control**: ML advisory is advice-only; ML control deferred per ADR-0003
- **Autonomous default preserved**: Operator overrides are optional corrections
- **Safety gates are final authority**: Documented in architecture docs
- **Manual switch control preserved**: All switch methods unchanged
- **Pump automation remains disabled**: Per PR 0008
- **Docker/GitOps**: `build-and-deploy.yml` untouched; external GitOps boundary respected
- **All existing modules unchanged**: PR 0014–0022 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
