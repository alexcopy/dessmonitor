# Web Host Startup

This document records the architecture and principles for the standalone
read-only web host startup module in the dessmonitor control system.

## 1. Manual Command (PR 0030)

PR 0030 adds a standalone manual startup module.

The web host can be started manually via:

```
python -m app.web_host_startup
```

This starts the read-only FastAPI application on 0.0.0.0:8000 by default.

## 2. Existing Container Startup Is Unchanged

The existing container startup path (`Dockerfile` CMD `["python", "run.py"]`)
is completely unchanged. The container continues to run the existing asyncio
main loop and automation behavior.

## 3. run.py Is Unchanged

`run.py` is not modified. It remains the existing asyncio-based main loop
with no web host awareness.

## 4. api.py Is Unchanged

`app/api.py` (the Flask-based Dess API) is not modified. It remains the
existing Flask application.

## 5. Dockerfile and docker-compose.yml Are Unchanged

No Dockerfile, docker-compose.yml, or container entrypoint changes.
No deployment wiring is added.

## 6. No Deployment Wiring Is Added

This PR does not modify any deployment manifests, CI/CD pipelines,
Kubernetes manifests, or ArgoCD configuration.

## 7. GET /control/state Remains Read-Only

The web host serves the existing `GET /control/state` route from
`app.control.web_ui_read_endpoint`. No write routes are added.

## 8. Runtime State Is Caller-Injected

The `runtime_state_provider` callable is passed through from the startup
module to `app.web_host.create_app()`. The startup module itself does not
read shared state, devices, or hardware.

## 9. Default Startup May Expose UNAVAILABLE

When no `runtime_state_provider` is injected (the default for manual startup),
the `GET /control/state` endpoint returns an `UNAVAILABLE` response. A real
provider must be caller-provided to get meaningful control state data.

## 10. No Write API

No POST, PUT, PATCH, or DELETE routes are registered. The host is strictly
read-only.

## 11. No Command Execution

No command executor, command proposal, or command execution logic exists
in this module.

## 12. No Device Reads

The startup module does not query hardware, read device status, or poll
sensors. Device state must be provided through the injected
`runtime_state_provider`.

## 13. No Tuya or Hardware Calls

No Tuya API calls, relay commands, or hardware interactions occur in this
module. The host is purely a read-only HTTP presentation layer.

## 14. Operator Writes Must Use the Control Layer Intent and Queue Path

When a future web UI needs to initiate a command (turn load ON/OFF), it must
route through the control layer:
1. Write operator intent into the manual control queue (PR 0019)
2. Let command arbitration produce a proposal (PR 0020)
3. Let safety gates evaluate the proposal (PR 0021)
4. Let execution eligibility determine if it can execute (PR 0022)

The web UI must NOT directly call Tuya or hardware.

## 15. ML Control Remains Deferred

Machine learning models must not directly control devices without separate
safety-reviewed approval per ADR-0003. ML control is deferred and is NOT
in scope for any current or near-term PR.
