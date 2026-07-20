# Web Host Startup

This document records the architecture and principles for the standalone
read-only web host startup module in the dessmonitor control system.

## 1. PR 0030 Introduces a Standalone Read-Only Startup Module

PR 0030 creates `app/web_host_startup.py` — a standalone, manual/diagnostic
entry point for starting the read-only web host via
`python -m app.web_host_startup`. The module wraps
`app.web_host.create_app()` with `create_startup_app()` and provides
`run_read_only_web_host()` for uvicorn-based server start.

## 2. The Startup Module Is Not Wired Into run.py

`run.py` is not modified. The startup module is a manual/diagnostic entry
point only. Production deployment wiring requires a separate safety-reviewed PR.

## 3. The Startup Module Is Not Wired Into Deployment

No Dockerfile, docker-compose, or deployment manifest is modified. This PR
is purely a Python module addition.

## 4. GET /control/state Remains Read-Only

The web host continues to serve the existing `GET /control/state` route from
`app.control.web_ui_read_endpoint`. No write routes are added.

## 5. No Write API

No POST, PUT, PATCH, or DELETE routes are registered. The host is strictly
read-only.

## 6. No Hardware/Tuya

No Tuya API calls, relay commands, or hardware interactions occur in this
module. The host is purely a read-only HTTP presentation layer.

## 7. No Command Execution

No command executor, command proposal, or command execution logic exists
in this module.

## 8. runtime_state_provider Remains Injected

The `runtime_state_provider` callable is passed through to
`app.web_host.create_app()`, consistent with PR 0029. When no provider is
given, the endpoint returns UNAVAILABLE.

## 9. Operator Writes Must Go Through the Control Layer Only

When a future web UI needs to initiate a command (turn load ON/OFF), it must:
1. Write operator intent into the manual control queue (PR 0019)
2. Let command arbitration produce a proposal (PR 0020)
3. Let safety gates evaluate the proposal (PR 0021)
4. Let execution eligibility determine if it can execute (PR 0022)

The web UI must NOT directly call Tuya or hardware.

## 10. ML Control Is Deferred

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is deferred and is NOT in scope
for any current or near-term PR.

## 11. Manual Startup Only

The module is invoked manually via `python -m app.web_host_startup`. It is
intended for diagnostic and development use. It is not part of the production
deployment topology.
