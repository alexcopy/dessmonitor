# Web UI Read-Only API Contract

This document records the architecture and principles for the future web UI
read-only API contract in the dessmonitor control system.

## 1. This PR Defines Contract Only

The `WebUiReadContract`, `WebUiReadEndpointContract`, and `WebUiControlStateResponse`
types define the expected shape of a future read-only API. No FastAPI route, Flask
endpoint, or HTTP server is added by this PR.

## 2. Future Web UI May Read Control State Snapshot

A future web UI may display:
- Load state (which loads are on/off, configured wattage, roles)
- Policy decision (what the autonomous engine recommends)
- Command proposal (what arbitration produced)
- Safety gate result (what safety checks passed/failed)
- Execution eligibility (when/if execution might happen)
- Manual queue state (what operators have enqueued)
- Mode flags (autonomous/manual/dry-run status)

## 3. Future Web UI Write Actions Are Forbidden in This Contract

The `forbidden_actions` tuple lists actions that are explicitly disallowed:
- `direct-hardware-write` — writing directly to relays/loads
- `direct-tuya-command` — calling Tuya API directly
- `execute-command` — dispatching hardware commands
- `mutate-shared-state` — modifying global state
- `bypass-control-layer` — skipping the policy/arbitration/safety gates
- `bypass-safety-gates` — skipping safety gate evaluation
- `write-api` — any write operation on the API

## 4. Future Operator Writes Must Go Through Control-Layer Intent/Queue

When a future web UI needs to initiate a command (turn load ON/OFF), it must:
1. Write operator intent into the manual control queue (PR 0019)
2. Let command arbitration produce a proposal (PR 0020)
3. Let safety gates evaluate the proposal (PR 0021)
4. Let execution eligibility determine if it can execute (PR 0022)

The web UI must NOT directly call Tuya or hardware.

## 5. Web UI Must Not Bypass Safety Gates

Safety gates (PR 0021) are the final authority before any execution. The web UI
cannot skip, override, or disable safety gate evaluation.

## 6. Web UI Must Not Directly Call Tuya/Hardware

Under no circumstances will a web UI component directly call Tuya API methods,
relay controllers, or hardware adapters. All commands flow through the control layer.

## 7. execution_allowed_now Remains False

`execution_allowed_now` is always `false` now and will remain `false` until a
separate safety-reviewed execution PR. The web UI contract reflects this — the
response is read-only, and `allowed_actions` only includes `read-control-state`.

## 8. ML Control Remains Deferred

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is deferred and is NOT in scope
for any current or near-term PR.
