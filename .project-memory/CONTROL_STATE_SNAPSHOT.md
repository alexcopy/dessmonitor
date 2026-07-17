# Control State Snapshot

This document records the architecture, principles, and invariants for the
control state snapshot model in the dessmonitor control system. The snapshot
packages already-computed state for future visibility — it does not execute.

## 1. Control State Snapshot Is Read-Only

The `ControlStateSnapshot` is a passive, frozen data structure. It packages
already-computed control-layer state into a stable view. It does not recompute,
reevaluate, or modify any pipeline stage. The snapshot is for display and
observability, not for control.

## 2. For Future Web UI, Observability, Debugging, and Controller Visibility

The snapshot is designed to be consumed by:
- Future web UI dashboards showing current system state
- Observability tools tracking pipeline health
- Debugging sessions inspecting why a decision was made
- Future controller components that need a stable view of control state

## 3. It Does Not Execute Commands

Under no circumstances does the snapshot module execute, enqueue, or dispatch
commands. It reads from already-computed objects — it never calls:
- `switch_on_device` or `switch_off_device`
- Any Tuya API method
- Any hardware adapter or relay controller

## 4. It Does Not Call Tuya or Hardware

The module's dependencies are limited to other `app.control.*` modules that are
already pure and deterministic. It does not import or call any Tuya, hardware,
or runtime service module.

## 5. It Does Not Fetch Weather or Call ML

The snapshot does not fetch weather data, call OpenWeather, or invoke ML models.
All data in the snapshot was computed by upstream pipeline stages before the
snapshot was built.

## 6. It Does Not Recompute Policy Decisions

The snapshot uses `PolicyDecisionResult` as provided in the input. It does not
call `evaluate_policy_decision()`, `arbitrate_command_intent()`,
`evaluate_command_safety_gate()`, or `evaluate_execution_eligibility()`.
The entire pipeline is pre-computed before the snapshot function is called.

## 7. It Only Packages Already-Computed Control-Layer State

The pipeline is:
```
evaluate_policy_decision → arbitrate_command_intent → evaluate_command_safety_gate → evaluate_execution_eligibility → build_control_state_snapshot
```
Each stage is evaluated independently. The snapshot function is the final
read-only packaging step.

## 8. execution_allowed_now Remains Controlled by Execution Eligibility

`execution_allowed_now` is copied from the execution eligibility result if
provided. It remains `false` until a future safety-reviewed execution PR.
The snapshot module does not change this value.

## 9. Web UI May Display This Snapshot in the Future

A future web UI may display the snapshot to show operators:
- Current load states
- Pipeline decisions (what the system wants to do)
- Safety gate results (what's blocking)
- Execution eligibility (when it might execute)
- Manual queue state (what operators have enqueued)

## 10. Future Web UI Must Still Write Operator Intent Through the Control Layer

Even when displaying this snapshot, a future web UI must write operator intent
through the manual control queue and control arbitration layer — not through
direct hardware calls. The control layer is the single source of truth for
all command execution.
