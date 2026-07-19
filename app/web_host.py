"""Minimal read-only FastAPI web host bootstrap for dessmonitor.

read-only-web-host create-app-only no-runtime-wiring no-server-start
no-write-api no-execution placeholder-provider returns-unavailable
real-provider-deferred operator-writes-through-control-layer
safety-gates-required no-tuya-hardware

This module bootstraps a minimal isolated FastAPI web host with the
existing read-only GET /control/state endpoint from
``app.control.web_ui_read_endpoint``. The host uses a placeholder
snapshot provider that returns None, so the endpoint currently returns
UNAVAILABLE.

The host is NOT wired into run.py. It does NOT start a server.
A real runtime snapshot provider is deferred to PR 0029.
"""

from __future__ import annotations

from typing import Callable

# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------

WEB_HOST_READ_ONLY_MODE: bool = True

# ---------------------------------------------------------------------------
# Placeholder snapshot provider
# ---------------------------------------------------------------------------


def create_placeholder_control_state_snapshot_provider() -> Callable[[], None]:
    """Return a placeholder callable snapshot provider that always returns None.

    The returned provider:
    - does not read global state
    - does not read devices
    - does not call runtime
    - does not call hardware
    - does not import any service/tuya/monitoring/ml/weather module

    The endpoint calling this provider will return UNAVAILABLE because
    the provider returns None (no snapshot available).

    Returns:
        A callable that returns None.
    """
    return lambda: None


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_app():
    """Create a minimal FastAPI application with the read-only GET /control/state endpoint.

    FastAPI is imported inside this function so that ``app.web_host``
    remains import-safe even if FastAPI is not installed.

    Behavior:
      1. Creates a placeholder snapshot provider via
         :func:`create_placeholder_control_state_snapshot_provider`.
      2. Imports ``create_control_state_read_router`` from
         ``app.control.web_ui_read_endpoint``.
      3. Creates a ``FastAPI`` instance with a descriptive title.
      4. Includes the read-only router via ``app.include_router``.
      5. Returns the FastAPI application **without** starting a server.

    Does NOT:
      - call ``uvicorn.run``
      - start any server
      - add write API (no POST/PUT/PATCH/DELETE routes)
      - read global state
      - read devices directly
      - call Tuya or hardware
      - execute commands
      - import any service/device/tuya/monitoring/ml/weather module
      - call ``build_runtime_control_snapshot``
      - call ``build_control_state_snapshot``

    Returns:
        A ``FastAPI`` application instance.

    Raises:
        RuntimeError: If FastAPI cannot be imported.
    """
    # Lazy import — FastAPI is not imported at module level
    try:
        from fastapi import FastAPI  # noqa: PLC0415  (lazy import)
    except ImportError:
        raise RuntimeError("fastapi-unavailable") from None

    from app.control.web_ui_read_endpoint import create_control_state_read_router  # noqa: PLC0415

    provider = create_placeholder_control_state_snapshot_provider()
    router = create_control_state_read_router(provider)

    app = FastAPI(
        title="Smart Pond Read API",
        description=(
            "Read-only control state API for dessmonitor. "
            "no-runtime-wiring no-server-start no-write-api no-execution "
            "placeholder-provider returns-unavailable real-provider-deferred"
        ),
    )
    app.include_router(router)
    return app
