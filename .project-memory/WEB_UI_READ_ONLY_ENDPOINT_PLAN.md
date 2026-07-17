# Web UI Read-Only Endpoint Implementation Plan

This document records the implementation plan for the future `GET /control/state`
read-only endpoint in the dessmonitor control system.

## 1. This PR Defines a Future Endpoint Implementation Plan Only

The `WebUiReadEndpointPlan` documents the boundaries, data sources, and
implementation steps for the future endpoint. No FastAPI route, Flask endpoint,
or HTTP server is added by this PR.

## 2. No FastAPI Route Is Added

`route_added_now` is `false`. The endpoint exists only as a plan. A separate
PR will add the actual route.

## 3. No Real API Endpoint Is Added

The `build_web_ui_read_endpoint_plan()` function is pure and deterministic.
It produces a `WebUiReadEndpointPlan` data object — not an HTTP response.

## 4. Future Endpoint Should Be GET /control/state

The planned endpoint is:
- Path: `/control/state`
- Method: `GET`
- Response model: `WebUiControlStateResponse` (from PR 0025)

## 5. Future Endpoint Must Be Read-Only

`writes_allowed` is `false`. `allowed_actions` only includes `read-control-state`.
No write actions are permitted through this endpoint.

## 6. Future Endpoint Must Use Control-Layer Read Model

The endpoint will use the runtime snapshot adapter (PR 0024) and web UI read
contract (PR 0025) to produce responses. It will not read shared_state directly.

## 7. Future Endpoint Must Not Call Tuya/Hardware

The endpoint will not call any Tuya API method, relay controller, or hardware
adapter. All state is provided through the control-layer pipeline.

## 8. Future Endpoint Must Not Bypass Safety Gates

Safety gates (PR 0021) remain the final authority before any execution. The
read-only endpoint does not execute commands and does not bypass this authority.

## 9. Future Endpoint Must Not Mutate shared_state

The endpoint is read-only. It does not modify any global state, shared_state,
or runtime variable.

## 10. Future Write Actions Must Go Through Control-Layer Intent/Queue

When a future web UI needs to initiate a command, it must use the manual control
queue (PR 0019) and command arbitration (PR 0020) — not direct hardware calls.

## 11. Adding the Actual Endpoint Requires a Separate PR

All implementation steps have `requires_separate_pr=true` and
`allowed_in_this_pr=false`. The actual endpoint must be implemented in a
separate, safety-reviewed PR.

## 12. execution_allowed_now Remains False

`execution_allowed` is `false` in the endpoint boundary. It will remain `false`
until a separate safety-reviewed execution PR.

## 13. ML Control Remains Deferred

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is deferred.
