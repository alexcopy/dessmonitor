"""Web host bootstrap for dessmonitor.

read-only-web-host create-app-only no-runtime-wiring no-server-start
no-write-api no-execution placeholder-provider returns-unavailable
real-provider-deferred operator-writes-through-control-layer
safety-gates-required no-tuya-hardware fail-closed-authentication
argon2-password-verification csrf-protected secure-session-cookies
login-throttling security-headers

This module bootstraps a FastAPI web host with:
- the existing read-only GET /control/state endpoint
- authentication (login/logout, session management, CSRF, throttling)
- security headers and docs disable

The host uses a placeholder snapshot provider that returns None, so the
endpoint returns UNAVAILABLE when no real provider is injected.

PR 0029 added an injectable ``runtime_state_provider`` parameter.
PR 0033 adds mandatory authentication — ``create_app()`` requires
``WEB_AUTH_*`` environment variables and raises ``WebAuthConfigError``
if they are missing or invalid.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from starlette.middleware.sessions import SessionMiddleware
    from starlette.responses import Response
    from starlette.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------

WEB_HOST_READ_ONLY_MODE: bool = True

# Static files allowlist — only login.css is public
_STATIC_ALLOWLIST: frozenset[str] = frozenset({"login.css"})

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


def create_app(
    runtime_state_provider: Callable[[], dict[str, Any] | None] | None = None,
    environ: dict[str, str] | None = None,
):
    """Create a FastAPI application with auth and the read-only GET /control/state endpoint.

    FastAPI is imported inside this function so that ``app.web_host``
    remains import-safe even if FastAPI is not installed.

    Behavior:
      1. Loads auth config via ``load_auth_config(environ)``.  If config is
         ``None`` (username missing), raises ``WebAuthConfigError("auth-not-configured")``.
         This is fail-closed — no routes are exposed without credentials.
      2. Adds Starlette ``SessionMiddleware`` for signed session cookies.
      3. Adds ``SecurityHeadersMiddleware`` for all responses.
      4. Mounts ``/static`` with a restricted allowlist (``login.css`` only).
      5. Includes auth routes (login, logout, index, healthz).
      6. Includes the existing ``/control/state`` read-only router.
      7. Disables ``/docs``, ``/redoc``, ``/openapi.json``.
      8. Returns the FastAPI application **without** starting a server.

    Does NOT:
      - call ``uvicorn.run``
      - start any server
      - add device write API
      - read global state
      - read devices directly
      - call Tuya or hardware
      - execute commands
      - import any service/device/tuya/monitoring/ml/weather module

    Args:
        runtime_state_provider: Optional callable that returns a runtime
            state mapping or None. When provided, real snapshots are built.
        environ: Optional env mapping (defaults to ``os.environ``).

    Returns:
        A ``FastAPI`` application instance.

    Raises:
        WebAuthConfigError: If auth configuration is missing or invalid.
        RuntimeError: If FastAPI cannot be imported.
    """
    env = environ if environ is not None else os.environ

    # ------------------------------------------------------------------
    # 1. Load auth config (fail-closed)
    # ------------------------------------------------------------------
    from app.web_auth import (  # noqa: PLC0415
        LoginThrottle,
        SecurityHeadersMiddleware,
        WebAuthConfigError,
        load_auth_config,
    )

    auth_config = load_auth_config(env)
    if auth_config is None:
        raise WebAuthConfigError("auth-not-configured")

    # ------------------------------------------------------------------
    # 2. Lazy FastAPI import
    # ------------------------------------------------------------------
    try:
        from fastapi import FastAPI  # noqa: PLC0415  (lazy import)
    except ImportError:
        raise RuntimeError("fastapi-unavailable") from None

    from app.control.web_ui_read_endpoint import create_control_state_read_router  # noqa: PLC0415

    if runtime_state_provider is not None:
        from app.web_control_state_provider import create_runtime_control_state_snapshot_provider  # noqa: PLC0415
        provider = create_runtime_control_state_snapshot_provider(runtime_state_provider)
    else:
        provider = create_placeholder_control_state_snapshot_provider()

    # ------------------------------------------------------------------
    # 3. Create FastAPI app
    # ------------------------------------------------------------------
    app = FastAPI(
        title="dessmonitor Operator",
        description=(
            "Authenticated operator interface for dessmonitor. "
            "no-runtime-wiring no-server-start no-write-api no-execution "
            "fail-closed-authentication argon2-password-verification "
            "csrf-protected secure-session-cookies login-throttling"
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # ------------------------------------------------------------------
    # 4. Control state auth middleware (innermost — runs after session)
    # ------------------------------------------------------------------

    _PROTECTED_PATHS: frozenset[str] = frozenset({"/control/state"})

    class ControlStateAuthMiddleware:
        """ASGI middleware that requires authentication for /control/state.

        Must be added as innermost middleware (before SessionMiddleware in
        ``add_middleware`` order) so the session is already populated.
        """

        def __init__(self, asgi_app: Any) -> None:
            self.app = asgi_app

        async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope["type"] != "http" or scope.get("path") not in _PROTECTED_PATHS:
                await self.app(scope, receive, send)
                return

            session = scope.get("session", {})
            from app.web_auth import validate_session  # noqa: PLC0415

            valid, _ = validate_session(session)
            if not valid:
                from starlette.responses import JSONResponse  # noqa: PLC0415
                response = JSONResponse(
                    content={"detail": "Not authenticated"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return

            await self.app(scope, receive, send)

    app.add_middleware(ControlStateAuthMiddleware)

    # ------------------------------------------------------------------
    # 5. Session middleware (outer — populates scope["session"])
    # ------------------------------------------------------------------
    from starlette.middleware.sessions import SessionMiddleware  # noqa: PLC0415

    app.add_middleware(
        SessionMiddleware,
        secret_key=auth_config.session_secret,
        session_cookie="dessmonitor_session",
        max_age=auth_config.session_ttl_seconds,
        same_site="lax",
        https_only=not auth_config.test_http,
    )

    # ------------------------------------------------------------------
    # 6. Security headers middleware (outermost)
    # ------------------------------------------------------------------
    app.add_middleware(SecurityHeadersMiddleware)

    # ------------------------------------------------------------------
    # 7. Include control state router
    # ------------------------------------------------------------------
    control_state_router = create_control_state_read_router(provider)
    app.include_router(control_state_router)

    # ------------------------------------------------------------------
    # 8. Auth routes (login, logout, index, healthz)
    # ------------------------------------------------------------------
    from app.web_routes import create_auth_router  # noqa: PLC0415

    throttler = LoginThrottle(
        max_attempts=auth_config.max_attempts,
        window_seconds=auth_config.attempt_window_seconds,
        lockout_seconds=auth_config.lockout_seconds,
    )
    auth_router = create_auth_router(auth_config, throttler)
    app.include_router(auth_router)

    # ------------------------------------------------------------------
    # 9. Static files with allowlist
    # ------------------------------------------------------------------
    from starlette.responses import Response  # noqa: PLC0415
    from starlette.staticfiles import StaticFiles  # noqa: PLC0415

    class _RestrictedStaticFiles(StaticFiles):
        """StaticFiles subclass that only serves files from an allowlist."""

        def __init__(self, *args: Any, allowed: frozenset[str] | None = None, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._allowed: frozenset[str] = allowed if allowed is not None else frozenset()

        async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
            if path not in self._allowed:
                return Response("Not Found", status_code=404)
            return await super().get_response(path, scope)

    app.mount(
        "/static",
        _RestrictedStaticFiles(directory="app/web/static", allowed=_STATIC_ALLOWLIST),
        name="static",
    )

    return app
