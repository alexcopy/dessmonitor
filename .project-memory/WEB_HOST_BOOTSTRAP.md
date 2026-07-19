# Web Host Bootstrap

This document records the architecture and principles for the minimal read-only
FastAPI web host bootstrap module in the dessmonitor control system.

## 1. This PR Introduces a Minimal Isolated FastAPI Web Host Module

PR 0028b creates `app/web_host.py` — a minimal, isolated FastAPI web host
module providing `create_app()`, `create_placeholder_control_state_snapshot_provider()`,
and the `WEB_HOST_READ_ONLY_MODE` constant. The module is self-contained and
does not depend on runtime services, hardware, or live state.

## 2. The Host Is Not Wired Into run.py

`run.py` is not modified. The host module is importable but not integrated
into the runtime process. Future wiring requires a separate safety-reviewed PR.

## 3. The Host Is Not Started by Runtime

No `uvicorn.run()` call exists anywhere in this PR. The `create_app()` function
returns a FastAPI application instance without starting any server.

## 4. No Docker/Deployment Changes Are Included

No Dockerfile, docker-compose, or deployment manifest is modified. This PR is
purely a Python module addition.

## 5. The Host Includes Read-Only GET /control/state

The host includes the existing `create_control_state_read_router` from
`app.control.web_ui_read_endpoint`, which registers a single `GET /control/state`
route. No other routes are added.

## 6. Current Provider Returns None and Endpoint Reports UNAVAILABLE

The placeholder snapshot provider (`create_placeholder_control_state_snapshot_provider`)
returns a callable that always returns `None`. Since `None` means "no snapshot
available", the `GET /control/state` endpoint returns a response with status
`UNAVAILABLE`.

## 7. Real Read-Only Runtime Snapshot Provider Is Deferred to PR 0029

A real runtime snapshot provider that returns actual `ControlStateSnapshot`
data will be implemented in PR 0029. The placeholder provider in this PR
exists solely to unblock the web host bootstrap.

## 8. No Write API Is Added

No POST, PUT, PATCH, or DELETE routes are registered. The host is strictly
read-only.

## 9. No Command Execution Is Added

No command executor, command proposal, or command execution logic exists
in this module.

## 10. No shared_state Reads Are Added

The host does not import or read any shared state. All snapshot data is
provided through the caller-provided snapshot provider.

## 11. No Direct Device Reads Are Added

The host does not call any device query, status check, or polling function.
Device state must be provided through the snapshot pipeline (future PR 0029).

## 12. No Tuya/Hardware Calls Are Added

No Tuya API calls, relay commands, or hardware interactions occur in this
module. The host is purely a read-only HTTP presentation layer.

## 13. Operator Writes Must Go Through Control-Layer Intent/Queue

When a future web UI needs to initiate a command (turn load ON/OFF), it must:
1. Write operator intent into the manual control queue (PR 0019)
2. Let command arbitration produce a proposal (PR 0020)
3. Let safety gates evaluate the proposal (PR 0021)
4. Let execution eligibility determine if it can execute (PR 0022)

The web UI must NOT directly call Tuya or hardware.

## 14. Hardware Execution Remains Behind Later Safety-Reviewed PR

No command execution is enabled by this PR. Hardware execution (relay control,
switch toggling) requires a separate safety-reviewed PR that integrates the
command safety gates (PR 0021) and execution eligibility (PR 0022).

## 15. ML Control Remains Deferred and Requires Separate Safety-Reviewed Approval

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is deferred and is NOT in scope
for any current or near-term PR.
