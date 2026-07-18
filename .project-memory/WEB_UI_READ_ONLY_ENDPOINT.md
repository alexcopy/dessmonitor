# Web UI Read-Only Control State Endpoint

This document records the architecture and principles for the web UI read-only
control state endpoint in the dessmonitor control system.

## 1. This PR Adds an Isolated Read-Only Endpoint Module

PR 0027 adds `app/control/web_ui_read_endpoint.py` — an isolated, import-safe
module providing types, constants, and a FastAPI router factory for a future
`GET /control/state` endpoint. The endpoint module is self-contained and does
not depend on runtime services, hardware, or live state.

## 2. It Does Not Wire the Endpoint Into Runtime

`route_wired_now` is `false`. The router factory returns an `APIRouter` but does
not include it in any FastAPI application. No `app.include_router` call exists
in this module. Runtime wiring requires a separate safety-reviewed PR.

## 3. It Does Not Edit api.py or run.py

Neither `api.py` nor `run.py` is modified by this PR. The endpoint module is
isolated and future wiring will be done in a separate PR.

## 4. It Does Not Add Write API

`writes_allowed` is `false`. The endpoint is read-only. No POST, PUT, PATCH, or
DELETE routes are registered. `route-methods` are restricted to `route-write-methods`
forbidden list.

## 5. Future Route Is GET /control/state Only

The planned endpoint is:
- Path: `/control/state`
- Method: `GET`
- Response model: `WebUiControlStateResponse` (from PR 0025)

## 6. The Route Depends on Caller-Provided Read-Only Snapshot Provider

The endpoint receives a `snapshot_provider: Callable[[], ControlStateSnapshot | None]`
from the caller at router-creation time. The endpoint does not create its own
snapshot provider, does not call `build_control_state_snapshot` directly, and does
not call `build_runtime_control_snapshot`.

## 7. The Endpoint Must Not Read Live shared_state Directly

No call is made to `shared_state`, `SharedState`, `get_state`, or any global
state accessor. All state is provided through the caller-provided snapshot provider.

## 8. The Endpoint Must Not Read Devices Directly

The endpoint does not call any device query, status check, or polling function.
Device state is provided through the snapshot pipeline.

## 9. The Endpoint Must Not Call Tuya/Hardware

No Tuya API calls, relay commands, or hardware interactions occur in this module.
The endpoint is purely a read-only state presentation layer.

## 10. The Endpoint Must Not Bypass Safety Gates

Safety gates (PR 0021) are the final authority before any execution. The read-only
endpoint does not execute commands and does not bypass this authority. The
`safety-gates-required` note is present in all responses.

## 11. Operator Writes Must Go Through Control-Layer Intent/Queue

When a future web UI needs to initiate a command (turn load ON/OFF), it must:
1. Write operator intent into the manual control queue (PR 0019)
2. Let command arbitration produce a proposal (PR 0020)
3. Let safety gates evaluate the proposal (PR 0021)
4. Let execution eligibility determine if it can execute (PR 0022)

The web UI must NOT directly call Tuya or hardware.

## 12. Runtime Wiring Requires a Separate Safety-Reviewed PR

All implementation steps for runtime wiring have `requires_separate_pr=true` and
`allowed_in_this_pr=false`. The actual endpoint must be wired in a separate,
safety-reviewed PR.

## 13. execution_allowed_now Remains False

`execution_allowed` is `false` and will remain `false` until a separate
safety-reviewed execution PR. The endpoint module reflects this.

## 14. ML Control Remains Deferred and Requires Separate Safety-Reviewed Approval

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is deferred and is NOT in scope
for any current or near-term PR.
