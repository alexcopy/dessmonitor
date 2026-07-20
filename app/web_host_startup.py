"""Standalone read-only web host startup module for dessmonitor.

read-only-web-host-startup manual-startup-only no-run-py-wiring
no-deployment-wiring no-write-api no-execution no-tuya-hardware
uvicorn-lazy-import uvicorn-unavailable
operator-writes-through-control-layer ml-control-deferred

This module provides a standalone, manual/diagnostic entry point for
starting the read-only web host via ``python -m app.web_host_startup``.

It wraps ``app.web_host.create_app()`` with a startup function that
optionally accepts a runtime state provider and provides a uvicorn-based
entry point.

The startup module is NOT wired into ``run.py`` or deployment.
No Docker or deployment changes. No write API. No hardware execution.
No direct device reads. No Tuya/hardware calls.
No command execution. No ML control.
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

WEB_HOST_STARTUP_READ_ONLY_MODE: bool = True
DEFAULT_WEB_HOST: str = "0.0.0.0"
DEFAULT_WEB_PORT: int = 8000

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_startup_app(
    runtime_state_provider: Callable[[], dict[str, Any] | None] | None = None,
):
    """Create the FastAPI application for the web host.

    Delegates to ``app.web_host.create_app()`` with the given
    ``runtime_state_provider``.

    Does NOT:
      - import hardware modules
      - read devices
      - call Tuya or hardware
      - execute commands
      - add write API routes

    Args:
        runtime_state_provider: Optional callable that returns a runtime
            state mapping or None.

    Returns:
        A ``FastAPI`` application instance.

    Raises:
        RuntimeError: If FastAPI cannot be imported (propagated from
            ``app.web_host.create_app``).
    """
    # Lazy import to avoid module-level FastAPI dependency
    from app.web_host import create_app  # noqa: PLC0415

    return create_app(runtime_state_provider=runtime_state_provider)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def run_read_only_web_host(
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
    runtime_state_provider: Callable[[], dict[str, Any] | None] | None = None,
) -> None:
    """Start the read-only web host on the given host and port.

    Lazily imports ``uvicorn`` inside the function. If uvicorn is not
    installed, raises ``RuntimeError("uvicorn-unavailable")``.

    Does NOT:
      - enable reload
      - manage workers
      - start hardware services
      - wire into run.py or deployment

    Args:
        host: Host address to bind (default ``"0.0.0.0"``).
        port: Port to bind (default ``8000``).
        runtime_state_provider: Optional callable that returns a runtime
            state mapping or None.

    Raises:
        RuntimeError: If ``uvicorn`` cannot be imported.
    """
    # Lazy import — uvicorn is not imported at module level
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("uvicorn-unavailable") from None

    app = create_startup_app(runtime_state_provider=runtime_state_provider)
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_read_only_web_host()
