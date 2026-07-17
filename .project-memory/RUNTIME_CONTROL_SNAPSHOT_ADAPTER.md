# Runtime Control Snapshot Adapter

This document records the architecture and principles for the runtime control
snapshot adapter in the dessmonitor control system.

## 1. Runtime Snapshot Adapter Is Read-Only

The `build_runtime_control_snapshot()` function is a pure, deterministic adapter.
It transforms caller-provided data into `ControlStateSnapshot` without executing
commands, wiring runtime, or calling hardware.

## 2. It Accepts Caller-Provided Runtime/Shared-State-Like Data

All data is passed as function arguments — `runtime_state` is an optional
`dict[str, object]` provided by the caller. The adapter does not read globals
or poll live state.

## 3. It Does Not Import Runtime Modules

The adapter's dependencies are limited to other `app.control.*` modules. It
does not import `app.service`, `app.tuya`, `app.devices`, `app.monitoring`,
`app.ml`, `app.weather`, `shared_state`, or any runtime module.

## 4. It Does Not Read Live shared_state Globals

No call is made to `shared_state`, `SharedState`, `get_state`, or any global
state accessor. All state is caller-provided.

## 5. It Does Not Read Devices Directly

The adapter does not call any device query, status check, or polling function.
Device state is provided by the caller through `RuntimeLoadState` objects.

## 6. It Does Not Call Tuya or Hardware

No Tuya API calls, relay commands, or hardware interactions occur in the adapter.

## 7. It Does Not Execute Commands

`execution_allowed_now` is always `false`. The adapter does not execute,
enqueue, or dispatch any command.

## 8. It Does Not Add API Endpoints

This is a pure Python module with no HTTP routes, serialization, or endpoint
registration.

## 9. It Does Not Recompute Policy/Arbitration/Safety/Eligibility

The adapter does not call:
- `evaluate_policy_decision()`
- `arbitrate_command_intent()`
- `evaluate_command_safety_gate()`
- `evaluate_execution_eligibility()`

All pipeline objects are provided by the caller as already-computed values.

## 10. It Only Adapts Already-Computed or Caller-Provided State

The adapter's sole job is to convert `RuntimeLoadState` → `LoadCandidate` and
`RuntimeControlModeState` → `ControlModeSnapshot`, then call
`build_control_state_snapshot()` from PR 0023.

## 11. It Is Preparation for Future Web UI Read Visibility

The adapter provides a bridge between runtime callers and the control state
snapshot. A future web UI or API endpoint can call this adapter with
already-computed pipeline data to get a display-ready snapshot.

## 12. Future Web UI Writes Operator Intent Through the Control Layer

Even when a future web UI displays this snapshot, it must write operator
intent through the manual control queue and control arbitration layer —
not through direct hardware calls. The control layer is the single source
of truth for all command execution.

## 13. ML Control Remains Deferred

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is deferred and is NOT
in scope for any current or near-term PR.
