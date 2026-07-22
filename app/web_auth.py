"""Authentication foundation for the dessmonitor web operator surface.

Provides:
- WebAuthConfig: frozen configuration dataclass
- WebAuthConfigError: configuration error exception
- load_auth_config(): env-var config loader with fail-closed validation
- verify_password(): Argon2 password verification
- create_session_data(): session payload builder
- validate_session(): session payload validator
- generate_csrf_token(): CSRF synchronizer token generator
- validate_csrf_token(): constant-time CSRF token comparison
- LoginThrottle: in-memory deterministic login throttling
- SecurityHeadersMiddleware: ASGI middleware for security headers

No credentials, hashes, tokens, or secrets are ever logged.
No imports of hardware, Tuya, relay, device, monitoring, ML, or weather modules.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# WebAuthConfigError
# ---------------------------------------------------------------------------


class WebAuthConfigError(Exception):
    """Raised when authentication configuration is missing or invalid.

    The message is a short machine-readable code; it never contains
    secret values.
    """
    pass


# ---------------------------------------------------------------------------
# WebAuthConfig
# ---------------------------------------------------------------------------


def _redacted() -> str:
    return "<redacted>"


@dataclass(frozen=True)
class WebAuthConfig:
    """Immutable authentication configuration.

    All credential and secret fields use a custom ``__repr__`` that
    replaces the actual value with ``<redacted>``.  This prevents
    accidental exposure in logs, tracebacks, and debug output.

    Attributes:
        username: The single operator username (from ``WEB_AUTH_USERNAME``).
        password_hash: Full Argon2 hash string (redacted in repr).
        session_secret: Session signing key (≥ 64 hex chars; redacted in repr).
        session_ttl_seconds: Session cookie Max-Age in seconds.
        max_attempts: Max failed attempts before lockout.
        attempt_window_seconds: Rolling window for counting failures.
        lockout_seconds: Lockout duration after threshold is reached.
        test_http: Whether to allow non-HTTPS session cookies (test only).
    """

    username: str
    password_hash: str = field(repr=False)
    session_secret: str = field(repr=False)
    session_ttl_seconds: int = 3600
    max_attempts: int = 5
    attempt_window_seconds: int = 300
    lockout_seconds: int = 900
    test_http: bool = False

    def __repr__(self) -> str:
        return (
            f"WebAuthConfig(username={self.username!r}, "
            f"password_hash={_redacted()!r}, "
            f"session_secret={_redacted()!r}, "
            f"session_ttl_seconds={self.session_ttl_seconds}, "
            f"max_attempts={self.max_attempts}, "
            f"attempt_window_seconds={self.attempt_window_seconds}, "
            f"lockout_seconds={self.lockout_seconds}, "
            f"test_http={self.test_http})"
        )


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_auth_config(
    environ: dict[str, str] | None = None,
) -> WebAuthConfig | None:
    """Load authentication configuration from environment variables.

    Args:
        environ: Optional environment mapping (defaults to ``os.environ``).

    Returns:
        ``WebAuthConfig`` on success, or ``None`` when ``WEB_AUTH_USERNAME``
        is absent, empty, or whitespace-only (caller decides fail-closed).

    Raises:
        WebAuthConfigError: For invalid ``WEB_AUTH_SESSION_SECRET`` length
            or missing ``WEB_AUTH_PASSWORD_HASH``.
    """
    env = environ if environ is not None else os.environ

    # --- WEB_AUTH_USERNAME ---
    username = env.get("WEB_AUTH_USERNAME", "").strip()
    if not username:
        return None

    # --- WEB_AUTH_PASSWORD_HASH ---
    password_hash = env.get("WEB_AUTH_PASSWORD_HASH", "").strip()
    if not password_hash:
        raise WebAuthConfigError("password-hash-missing")

    # --- WEB_AUTH_SESSION_SECRET ---
    session_secret = env.get("WEB_AUTH_SESSION_SECRET", "").strip()
    if len(session_secret) < 64:
        raise WebAuthConfigError("session-secret-too-short")

    # --- Optional tuning ---
    session_ttl = _parse_int_env(env, "WEB_AUTH_SESSION_TTL_SECONDS", 3600)
    max_attempts = _parse_int_env(env, "WEB_AUTH_MAX_ATTEMPTS", 5)
    attempt_window = _parse_int_env(env, "WEB_AUTH_ATTEMPT_WINDOW_SECONDS", 300)
    lockout = _parse_int_env(env, "WEB_AUTH_LOCKOUT_SECONDS", 900)

    # --- Test mode (HTTP cookie relaxation) ---
    test_http_raw = env.get("WEB_AUTH_TEST_HTTP", "").strip().lower()
    test_http = test_http_raw in ("1", "true", "yes", "on")

    return WebAuthConfig(
        username=username,
        password_hash=password_hash,
        session_secret=session_secret,
        session_ttl_seconds=session_ttl,
        max_attempts=max_attempts,
        attempt_window_seconds=attempt_window,
        lockout_seconds=lockout,
        test_http=test_http,
    )


def _parse_int_env(
    env: dict[str, str],
    key: str,
    default: int,
    min_value: int = 1,
) -> int:
    """Parse an integer from env, returning *default* on invalid values."""
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return default
    if value < min_value:
        return default
    return value


# ---------------------------------------------------------------------------
# Password verification
# ---------------------------------------------------------------------------


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a plaintext password against an Argon2 hash.

    Args:
        password_hash: Full Argon2 hash string (from config).
        password: Plaintext password to verify.

    Returns:
        ``True`` on match, ``False`` on mismatch.

    The plaintext password is never persisted, logged, or stored beyond
    this call.  Unexpected Argon2 exceptions (not ``VerifyMismatchError``)
    are re-raised — they indicate a configuration problem, not a failed
    authentication attempt.
    """
    import argon2.exceptions  # noqa: PLC0415  (lazy import)

    try:
        ph = argon2.PasswordHasher()
        return ph.verify(password_hash, password.encode("utf-8"))
    except argon2.exceptions.VerifyMismatchError:
        return False


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def create_session_data(username: str) -> dict[str, Any]:
    """Build a session payload dictionary.

    The payload contains the authenticated username, a fresh CSRF token,
    and the creation timestamp.

    Args:
        username: The authenticated operator username.

    Returns:
        A dict suitable for ``request.session.update()``.
    """
    return {
        "user": username,
        "csrf_token": secrets.token_hex(32),
        "created_at": int(time.time()),
    }


def validate_session(session: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate a session payload.

    Checks that the ``user`` key exists and is a non-empty string.

    Expiry is handled by Starlette's ``SessionMiddleware`` / itsdangerous
    timed serializer — this function only performs structural validation.

    Args:
        session: The session dict from ``request.session``.

    Returns:
        A ``(valid, username_or_None)`` tuple.
    """
    user = session.get("user")
    if not user or not isinstance(user, str) or not user.strip():
        return False, None
    return True, user


# ---------------------------------------------------------------------------
# CSRF helpers
# ---------------------------------------------------------------------------


def generate_csrf_token(session: dict[str, Any]) -> str:
    """Ensure a CSRF token exists in the session and return it.

    If the session already contains a ``csrf_token``, it is returned.
    Otherwise a new 32-byte hex token is generated, stored, and returned.

    Args:
        session: The mutable session dict.

    Returns:
        The current CSRF token string.
    """
    token = session.get("csrf_token")
    if not token or not isinstance(token, str):
        token = secrets.token_hex(32)
        session["csrf_token"] = token
        # Ensure session is marked as modified
        session.modified = True
    return token


def validate_csrf_token(session: dict[str, Any], form_token: str | None) -> bool:
    """Validate a CSRF form token against the session token.

    Uses :func:`secrets.compare_digest` for constant-time comparison.

    Args:
        session: The session dict.
        form_token: The CSRF token from the form body (may be ``None``).

    Returns:
        ``True`` if the tokens match, ``False`` otherwise.
    """
    session_token = session.get("csrf_token")
    if not session_token or not isinstance(session_token, str):
        return False
    if not form_token or not isinstance(form_token, str):
        return False
    return secrets.compare_digest(session_token, form_token)


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------


@dataclass
class _ThrottleEntry:
    """Internal per-key throttle state."""

    failures: int = 0
    first_failure: float = 0.0
    locked_until: float | None = None


class LoginThrottle:
    """In-memory deterministic login throttling.

    Uses ``time.monotonic()`` for all timing.  Not thread-safe (single-
    threaded asyncio is assumed).  Process-local — restart clears state.

    Args:
        max_attempts: Max failed attempts before lockout.
        window_seconds: Rolling window for counting failures.
        lockout_seconds: Lockout duration after threshold is reached.
    """

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._lockout_seconds = lockout_seconds
        self._store: dict[tuple[str, str], _ThrottleEntry] = {}

    def check(self, username: str, source_ip: str) -> bool:
        """Return ``True`` if the attempt is allowed, ``False`` if throttled.

        Prunes expired entries before checking.

        Args:
            username: The attempted username.
            source_ip: The client IP address (from ``request.client.host``).

        Returns:
            ``True`` if allowed, ``False`` if throttled.
        """
        self._prune()
        key = (username, source_ip)
        entry = self._store.get(key)
        if entry is None:
            return True
        now = time.monotonic()
        if entry.locked_until is not None:
            if now < entry.locked_until:
                return False
            # Lockout expired — reset
            del self._store[key]
            return True
        # Check if window has expired
        if now - entry.first_failure > self._window_seconds:
            del self._store[key]
            return True
        return entry.failures < self._max_attempts

    def record_failure(self, username: str, source_ip: str) -> None:
        """Record a failed login attempt.

        Args:
            username: The attempted username.
            source_ip: The client IP address.
        """
        key = (username, source_ip)
        now = time.monotonic()
        entry = self._store.get(key)
        if entry is None:
            entry = _ThrottleEntry(failures=1, first_failure=now)
            self._store[key] = entry
        else:
            entry.failures += 1
        # Check if threshold reached
        if entry.failures >= self._max_attempts:
            entry.locked_until = now + self._lockout_seconds

    def clear(self, username: str, source_ip: str) -> None:
        """Clear the throttle state for a key (called on successful login).

        Args:
            username: The authenticated username.
            source_ip: The client IP address.
        """
        key = (username, source_ip)
        self._store.pop(key, None)

    def _prune(self) -> None:
        """Remove expired entries from the store."""
        now = time.monotonic()
        max_age = self._window_seconds + self._lockout_seconds
        expired = [
            key
            for key, entry in self._store.items()
            if now - entry.first_failure > max_age
            and (entry.locked_until is None or now > entry.locked_until)
        ]
        for key in expired:
            del self._store[key]


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware:
    """ASGI middleware that adds security headers to all responses.

    Headers applied:
    - ``X-Content-Type-Options: nosniff`` (all HTML and JSON responses).
    - ``Referrer-Policy: strict-origin-when-cross-origin`` (all responses).
    - ``Content-Security-Policy`` (all HTML responses).
    - No ``Access-Control-Allow-Origin`` header is added.

    This is a pure ASGI middleware — no Starlette-specific base class
    is used, so the module is import-safe without Starlette installed.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                # Convert headers to a mutable dict for easier manipulation
                header_dict = {}
                for h in headers:
                    key = h[0].decode("latin-1").lower()
                    value = h[1].decode("latin-1")
                    header_dict[key] = value

                # X-Content-Type-Options
                if "x-content-type-options" not in header_dict:
                    headers.append(
                        (b"x-content-type-options", b"nosniff")
                    )

                # Referrer-Policy
                if "referrer-policy" not in header_dict:
                    headers.append(
                        (b"referrer-policy", b"strict-origin-when-cross-origin")
                    )

                # CSP for HTML responses
                content_type = header_dict.get("content-type", "")
                if "text/html" in content_type:
                    if "content-security-policy" not in header_dict:
                        headers.append(
                            (
                                b"content-security-policy",
                                b"default-src 'self'; frame-ancestors 'none'; form-action 'self'",
                            )
                        )

            await send(message)

        await self.app(scope, receive, send_wrapper)
