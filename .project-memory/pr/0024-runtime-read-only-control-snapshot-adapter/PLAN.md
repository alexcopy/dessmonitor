# PR 0024 — Runtime Read-Only Control Snapshot Adapter

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `6f5c368b86c47c5e5dbd627a77366af8c836a010` |
| Branch | `git branch --show-current` | `0024-runtime-read-only-control-snapshot-adapter` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0024-runtime-read-only-control-snapshot-adapter` and working tree is clean.

## 2. Purpose

PR 0024 adds a **pure runtime read-only control snapshot adapter** — a pure function
`build_runtime_control_snapshot()` that transforms caller-provided runtime/shared-state-like
data into `ControlStateSnapshotInput` and `ControlStateSnapshot` (from PR 0023).

The adapter is preparation for future runtime integration and future web UI visibility.
It does NOT wire into runtime, import runtime modules, read live `shared_state` globals,
add API endpoints, call Tuya/hardware, or execute any commands.

## 3. Product Context

1. PRs 0014–0023 built the full control layer: policy decision engine, manual queue,
   arbitration, safety gates, execution eligibility, and read-only state snapshot.
2. PR 0024 adds a **thin adapter** that maps caller-provided runtime-like data into
   the snapshot model without importing any runtime or device modules.
3. A future PR may wire the adapter into an API endpoint or runtime read-only integration.
4. This PR itself does not wire anything.

## 4. Required Architecture Document

### File: `.project-memory/RUNTIME_CONTROL_SNAPSHOT_ADAPTER.md`

The document must state:

1. Runtime snapshot adapter is read-only.
2. It accepts caller-provided runtime/shared-state-like data.
3. It does not import runtime modules.
4. It does not read live `shared_state` globals.
5. It does not read devices directly.
6. It does not call Tuya or hardware.
7. It does not execute commands.
8. It does not add API endpoints.
9. It does not recompute policy/arbitration/safety/eligibility.
10. It only adapts already-computed or caller-provided state into `ControlStateSnapshot`.
11. It is preparation for future web UI read visibility.
12. Future web UI writes operator intent through the control layer, not direct hardware calls.
13. ML control remains deferred and requires separate safety-reviewed approval per ADR-0003.

## 5. Required Types (5 Types)

### Module Location

**Create:** `app/control/runtime_snapshot_adapter.py`

**May update:** `app/control/__init__.py`

### Type 1: `RuntimeSnapshotAdapterStatus`

```python
class RuntimeSnapshotAdapterStatus(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
```

### Type 2: `RuntimeLoadState`

```python
@dataclass(frozen=True)
class RuntimeLoadState:
    """A single load's state provided by the runtime caller.

    Pure data — no device reads, no side effects.
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

### Type 3: `RuntimeControlModeState`

```python
@dataclass(frozen=True)
class RuntimeControlModeState:
    """Control mode state provided by the runtime caller.

    Pure data — no side effects.
    """
    autonomous_enabled: bool = True
    operator_overrides_enabled: bool = True
    controlled_execution_enabled: bool = True
    dry_run_only: bool = False
```

### Type 4: `RuntimeControlSnapshotAdapterInput`

```python
@dataclass(frozen=True)
class RuntimeControlSnapshotAdapterInput:
    """All input data for the runtime snapshot adapter.

    Pure data — no side effects.
    snapshot_id and created_at are caller-provided.
    runtime_state is an optional dict of caller-provided keys.
    All pipeline objects are caller-provided.
    """
    snapshot_id: str = ""
    created_at: str = ""
    runtime_state: dict[str, object] = field(default_factory=dict)
    loads: tuple[RuntimeLoadState, ...] = field(default_factory=tuple)
    policy_decision: PolicyDecisionResult | None = None
    command_proposal: CommandProposal | None = None
    safety_gate_result: CommandSafetyGateResult | None = None
    execution_eligibility: ExecutionEligibilityResult | None = None
    manual_queue_snapshot: ManualControlQueueSnapshot | None = None
    energy_budget: EnergyBudget | None = None
    battery_window: BatteryOperatingWindow | None = None
    mode: RuntimeControlModeState | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
```

### Type 5: `RuntimeControlSnapshotAdapterResult`

```python
@dataclass(frozen=True)
class RuntimeControlSnapshotAdapterResult:
    """Result of the runtime snapshot adapter.

    status: overall adapter status.
    snapshot_input: ControlStateSnapshotInput for build_control_state_snapshot.
    snapshot: ControlStateSnapshot from build_control_state_snapshot.
    warnings: tuple of warning strings.
    notes: tuple of note strings.
    """
    status: RuntimeSnapshotAdapterStatus = RuntimeSnapshotAdapterStatus.UNKNOWN
    snapshot_input: ControlStateSnapshotInput | None = None
    snapshot: ControlStateSnapshot | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
```

## 6. Required Pure Function

### `build_runtime_control_snapshot`

```python
def build_runtime_control_snapshot(
    adapter_input: RuntimeControlSnapshotAdapterInput | None,
) -> RuntimeControlSnapshotAdapterResult
```

Behavior:

1. If `adapter_input` is None → return `UNKNOWN` result with `warnings=("no-input",)`.
2. Convert `RuntimeLoadState` objects into `LoadCandidate` objects for `ControlStateSnapshotInput`.
3. Convert `RuntimeControlModeState` into `ControlModeSnapshot`.
4. Optionally parse `runtime_state` dict keys for convenience:
   `loads`, `load_states`, `battery_voltage`, `max_total_load_watts`,
   `current_total_load_watts`, `available_load_budget_watts`, `autonomous_enabled`,
   `operator_overrides_enabled`, `controlled_execution_enabled`, `dry_run_only`.
   This is best-effort and non-failing; missing keys do not cause errors.
5. Preserve all already-computed pipeline objects (policy_decision, command_proposal, etc.).
6. Call `build_control_state_snapshot()` from PR 0023 to produce the final snapshot.
7. Attach `warnings` for any missing state, and `notes` marking read-only/no-execution.

### Required Warning/Reason Strings

| String | When Used |
|---|---|
| `"no-input"` | Input is None |
| `"missing-runtime-state"` | `runtime_state` dict is empty/None |
| `"missing-loads"` | No loads provided |
| `"partial-runtime-state"` | Some expected keys missing |
| `"read-only-adapter"` | This adapter is read-only |
| `"read-only-snapshot"` | The snapshot is read-only |
| `"no-execution"` | This PR does not execute commands |
| `"no-runtime-wiring"` | This PR does not wire to runtime |
| `"caller-provided-state"` | All state is caller-provided |
| `"future-web-ui-read-model"` | This model is for future web UI read visibility |

### Required Product Rules

1. Pure function only.
2. No runtime module imports.
3. No live `shared_state` reads.
4. No device reads.
5. No Tuya/hardware calls.
6. No evaluator/arbitrator/safety/eligibility calls.
7. Does not compute policy decisions.
8. Does not create command proposals.
9. Does not evaluate safety gates.
10. Does not evaluate execution eligibility.
11. Does not execute, queue, propose, persist, fetch, log, or call runtime.
12. Must tolerate missing `runtime_state` keys.
13. Must tolerate `None` fields.
14. Must not mutate inputs.
15. Result must include `read-only-adapter` and `no-execution` notes.
16. If no loads and no pipeline state → `UNKNOWN`.
17. If partial state → `DEGRADED`.
18. If snapshot builds normally → `OK`.

## 7. Allowed Implementation Files

| File | Action |
|---|---|
| `app/control/runtime_snapshot_adapter.py` | **Create** |
| `app/control/__init__.py` | **Edit** |
| `.project-memory/RUNTIME_CONTROL_SNAPSHOT_ADAPTER.md` | **Create** |
| `scripts/check-runtime-snapshot-adapter.sh` | **Create** |
| `.github/workflows/validate.yml` | **Edit** |
| `.project-memory/CURRENT_STATE.md` | **Edit** |
| `.project-memory/ROADMAP.md` | **Edit** |
| `.project-memory/pr/0024-runtime-read-only-control-snapshot-adapter/CODER_REPORT.txt` | **Create** |

## 8. Forbidden Implementation Files

The coder must **not** edit: `run.py`, `app/service/**`, `app/devices/**`, `app/tuya/**`,
`app/monitoring/**`, `app/ml/**`, `app/weather/**`, `app/control/domain.py`,
`app/control/relay_mapping.py`, `app/control/energy_policy.py`, `app/control/readiness.py`,
`app/control/health.py`, `app/control/schedule_profile.py`, `app/control/weather_adjustment.py`,
`app/control/policy_models.py`, `app/control/policy_decision.py`,
`app/control/manual_control_queue.py`, `app/control/command_arbitration.py`,
`app/control/command_safety_gate.py`, `app/control/execution_eligibility.py`,
`app/control/control_state_snapshot.py`, `examples/energy_policy.example.yaml`,
`service/**`, `shared_state/**`, config files, data files, Docker/deployment files,
`.github/workflows/build-and-deploy.yml`, existing validation scripts.

## 9. Static Validation Script

Same requirements as spec: 21 checks for types, functions, fields, reason strings, no executor,
no evaluator calls, no runtime imports, no hardware calls, no impurities, runtime files unchanged.

## 10. CURRENT_STATE.md Update

```
## PR 0024 — Runtime Read-Only Control Snapshot Adapter
PR 0024 adds a pure runtime read-only control snapshot adapter in
`app/control/runtime_snapshot_adapter.py` with 5 types and the pure function
`build_runtime_control_snapshot()`. Transforms caller-provided runtime-like data
into ControlStateSnapshot. No runtime wiring. No API endpoints. No device reads.
No execution.
```

## 11. ROADMAP.md Update

```
- [x] PR 0024 — Runtime read-only control snapshot adapter
```

## 12. Future PR Boundary

Deferred: Web UI API endpoints, controlled executor, persistent storage, ML advisory, ML control.

## 13. Agent Workflow

Standard 4-agent workflow with locked PLAN.md and PLAN_REVIEW.yaml.

## 14. Boundary Confirmations

- **Read-only adapter only**: Transforms caller-provided data into snapshot
- **No runtime module imports**: Does not import `app.service`, `app.tuya`, etc.
- **No live `shared_state` reads**: All state is caller-provided
- **No evaluator/arbitrator calls**: Does not recompute any pipeline stage
- **No executor**: `CommandExecutor` and `execute` function must NOT exist
- **No runtime wiring**: Not connected to any runtime component
- **No API endpoints**: Deferred
- **No device reads**: Does not read hardware directly
- **No hardware execution**: Does not call any switch/device/Tuya method
- **No ML control**: ML control deferred per ADR-0003
- **All existing modules unchanged**: PR 0014–0023 frozen
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml locked after approval
