"""Route handlers for the authenticated web operator surface.

Provides request handlers for:
- GET /healthz (public, minimal)
- GET /login (public, renders login form)
- POST /login (public + CSRF, authenticates)
- POST /logout (auth + CSRF, clears session)
- GET / (auth, renders landing shell)

Does NOT implement dashboard, polling, device writes, or hardware access.
No imports of hardware, Tuya, relay, device, monitoring, ML, or weather modules.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from app.web_auth import (
    LoginThrottle,
    WebAuthConfig,
    create_session_data,
    generate_csrf_token,
    validate_csrf_token,
    validate_session,
    verify_password,
)

# ---------------------------------------------------------------------------
# Templates — use absolute path so working-directory changes are safe
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "web", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------


def create_auth_router(
    config: WebAuthConfig,
    throttler: LoginThrottle,
) -> APIRouter:
    """Create a FastAPI APIRouter with auth routes.

    Injects the authentication configuration and throttler into all
    handlers via closure.

    Args:
        config: The loaded authentication configuration.
        throttler: The login throttler instance.

    Returns:
        An ``APIRouter`` with auth-related routes.
    """
    router = APIRouter()

    # -- /healthz -------------------------------------------------------

    @router.get("/healthz")
    async def healthz() -> JSONResponse:
        """Public health endpoint — minimal, non-sensitive."""
        return JSONResponse(
            content={"status": "ok", "web_api": "available"},
            status_code=200,
        )

    # -- /login GET -----------------------------------------------------

    @router.get("/login")
    async def login_get(request: Request) -> Any:
        """Render the login form.

        Authenticated users are redirected to ``/``.
        Unauthenticated users receive a CSRF token in the form.
        """
        valid, _user = _check_auth(request)
        if valid:
            return RedirectResponse("/", status_code=303)

        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # -- /login POST ----------------------------------------------------

    @router.post("/login")
    async def login_post(request: Request) -> Any:
        """Authenticate the operator.

        CSRF token must match.  Throttling applies.
        Generic error on any failure (no credential leakage).
        Successful login creates a fresh session and redirects to ``/``.
        """
        # CSRF check
        form = await request.form()
        form_csrf = form.get("csrf_token")
        if isinstance(form_csrf, str):
            form_csrf = form_csrf.strip()
        else:
            form_csrf = None

        if not validate_csrf_token(request.session, form_csrf):
            return JSONResponse(
                content={"detail": "Invalid request."},
                status_code=403,
            )

        # Extract credentials (never log them)
        username_raw = form.get("username")
        username = username_raw.strip() if isinstance(username_raw, str) else ""
        password_raw = form.get("password")
        password = password_raw if isinstance(password_raw, str) else ""

        # Source IP from request
        source_ip = request.client.host if request.client else "127.0.0.1"

        # Throttle check
        if not throttler.check(username if username else "__empty__", source_ip):
            throttler.record_failure(username if username else "__empty__", source_ip)
            return _login_error(request, "Invalid credentials.")

        # Username check (case-sensitive)
        if username != config.username:
            throttler.record_failure(username, source_ip)
            return _login_error(request, "Invalid credentials.")

        # Password check
        try:
            pw_ok = verify_password(config.password_hash, password)
        except Exception:
            # Unexpected Argon2 error — config problem, not credential failure
            throttler.record_failure(username, source_ip)
            return _login_error(request, "Invalid credentials.")

        if not pw_ok:
            throttler.record_failure(username, source_ip)
            return _login_error(request, "Invalid credentials.")

        # Success — clear throttle, create session
        throttler.clear(username, source_ip)
        request.session.clear()
        session_data = create_session_data(username)
        request.session.update(session_data)

        response = RedirectResponse("/", status_code=303)
        return response

    # -- /logout POST ---------------------------------------------------

    @router.post("/logout")
    async def logout_post(request: Request) -> Any:
        """Log out the current operator.

        Requires authentication and CSRF token.
        Clears the session and deletes the cookie.
        """
        # Authentication required
        valid, _user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)

        # CSRF check
        form = await request.form()
        form_csrf = form.get("csrf_token")
        if isinstance(form_csrf, str):
            form_csrf = form_csrf.strip()
        else:
            form_csrf = None

        if not validate_csrf_token(request.session, form_csrf):
            return JSONResponse(
                content={"detail": "Invalid request."},
                status_code=403,
            )

        # Clear session
        request.session.clear()

        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("dessmonitor_session")
        return response

    # -- / GET ----------------------------------------------------------

    @router.get("/")
    async def index_get(request: Request) -> Any:
        """Render the authenticated landing shell.

        Unauthenticated users are redirected to ``/login``.
        """
        valid, user = _check_auth(request)
        if not valid:
            return RedirectResponse("/login", status_code=303)

        csrf = generate_csrf_token(request.session)
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"username": user, "csrf_token": csrf},
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_auth(request: Request) -> tuple[bool, str | None]:
    """Check if the current request has a valid authenticated session.

    Args:
        request: The incoming request.

    Returns:
        A ``(valid, username_or_None)`` tuple.
    """
    session = getattr(request, "session", None)
    if session is None:
        return False, None
    return validate_session(session)


def _login_error(request: Request, message: str) -> Any:
    """Render the login form with a generic error message.

    The error message is deliberately generic — no indication of whether
    the username, password, or throttling was the cause.
    """
    csrf = generate_csrf_token(request.session)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": csrf, "error": message},
    )
    response.headers["Cache-Control"] = "no-store"
    return response
