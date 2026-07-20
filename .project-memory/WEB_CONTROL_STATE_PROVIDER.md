# Web Control State Provider

This document records the architecture and principles for the runtime read-only
control state provider module in the dessmonitor control system.

## 1. This PR Replaces the Placeholder Provider

PR 0029 creates `app/web_control_state_provider.py` — an injectable read-only
runtime state provider that adapts caller-provided runtime state mappings into
`ControlStateSnapshot` via the existing runtime snapshot adapter (PR 0024).

## 2. Provider Receives Runtime Data Only Through Caller-Provided Callable

The provider never reads `shared_state` globals, devices, Tuya, or hardware.
All data arrives through a caller-provided `runtime_state_provider` callable
injected into `create_app()`.

## 3. Default Behavior Is Backward-Compatible

When no `runtime_state_provider` is injected, `create_app()` uses the existing
placeholder provider from PR 0028b, and the endpoint returns `UNAVAILABLE`.

## 4. Two Public Functions

- `create_runtime_control_state_snapshot_provider(runtime_state_provider)` —
  returns a callable provider suitable for `create_control_state_read_router`.
- `build_control_state_snapshot_from_runtime_state(runtime_state)` —
  builds a `ControlStateSnapshot` from a caller-provided mapping.

## 5. Provider Errors Are Hidden

If the injected `runtime_state_provider` raises an exception, the returned
provider catches it silently and returns `None` — no exception text is leaked
through the API.

## 6. No shared_state Reads

The provider does not import or call any `shared_state` module or global.
All state is injected through the caller's callable.

## 7. No Direct Device Reads

The provider does not query hardware, read device status, or poll sensors.
Device state must be provided through the runtime state mapping.

## 8. No Tuya/Hardware Calls

No Tuya API calls, relay commands, or hardware interactions occur in this
module. The provider is purely a data transformation layer.

## 9. No Command Execution

No command executor, command proposal, or command execution logic exists
in this module.

## 10. No Write API

No POST, PUT, PATCH, or DELETE routes are added. The provider is strictly
read-only.

## 11. Uses Only build_runtime_control_snapshot (not build_control_state_snapshot)

The provider delegates to `build_runtime_control_snapshot` from the runtime
snapshot adapter (PR 0024), which internally calls `build_control_state_snapshot`.
The provider itself never calls `build_control_state_snapshot` directly.

## 12. Partial/Missing Mappings Are Tolerated

`build_control_state_snapshot_from_runtime_state` parses recognized keys
best-effort and skips invalid entries without crashing.

## 13. Operator Writes Must Go Through Control-Layer Intent/Queue

When a future web UI needs to initiate a command, it must route through
the manual control queue (PR 0019), command arbitration (PR 0020),
safety gates (PR 0021), and execution eligibility (PR 0022).

## 14. Hardware Execution Remains Behind Later Safety-Reviewed PR

No command execution is enabled by this PR.

## 15. ML Control Remains Deferred and Requires Separate Safety-Reviewed Approval

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003.
