# PR 0033 — Authentication Foundation and Login Page

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `bce927547ea89aff6435db4ab243fa999704fc14` |
| Branch | `git branch --show-current` | `pr-0033-authentication-login-page` |
| Working tree | `git status --short` | clean (no local changes) |
| Roadmap gate | PR 0032 after-merge | ROADMAP.md exists at root; PR 0032 is the governance-only preceding milestone |
| `app/web_host.py` | `test -f app/web_host.py` | EXISTS |
| `app/web_runtime_integration.py` | `test -f app/web_runtime_integration.py` | EXISTS |
| `app/web_control_state_provider.py` | `test -f app/web_control_state_provider.py` | EXISTS |
| `app/web_host_startup.py` | `test -f app/web_host_startup.py` | EXISTS |
| `run.py` | `test -f run.py` | EXISTS |
| `requirements.txt` | `test -f requirements.txt` | EXISTS |
| FastAPI installed | `python3 -c "import fastapi"` | NOT INSTALLED (pinned in requirements.txt via PR 0031) |
| Argon2 installed | `python3 -c "import argon2"` | NOT INSTALLED (not yet in requirements) |
| jinja2 installed | `python3 -c "import jinja2"` | NOT INSTALLED (not yet in requirements) |
| python-multipart installed | `python3 -c "import python_multipart"` | NOT INSTALLED (not yet in requirements) |
| itsdangerous installed | `python3 -c "import itsdangerous"` | NOT INSTALLED (transitive from Starlette when FastAPI installed; pinned explicitly) |

The precondition passes. Branch is `pr-0033-authentication-login-page`, working tree is clean, and all required prerequisite files exist. The roadmap gate (after PR 0032 merge) is satisfied — the canonical ROADMAP.md exists at root and PR 0033 is the first incomplete Current Milestone.

**Dependency discovery:** Neither `argon2-cffi`, `jinja2`, `python-multipart`, nor `itsdangerous` is present locally or in `requirements.txt`. All four must be added. Resolution is locked in Section 9.

## 2. Purpose

PR 0033 introduces authentication before the currently exposed read-only FastAPI
operator endpoint is expanded into a full frontend. It provides one operator account,
a login page, secure session handling, password hash verification, login throttling,
CSRF protection, a minimal authenticated landing page, a public non-sensitive
`/healthz` endpoint, and protection of the existing `GET /control/state`.

The PR does NOT implement the live dashboard (PR 0034), polling, inverter cards,
device metrics, control buttons, manual command queue, hardware execution,
configuration editing, raw log access, or ML control.

## 3. Scope

### In Scope

- One operator account with credentials via environment variables
- GET /login — public, renders login form
- POST /login — public, CSRF-protected, verifies password, creates session
- POST /logout — authenticated, CSRF-protected, clears session
- GET / — authenticated, minimal landing shell (no dashboard)
- GET /control/state — authenticated (existing read-only contract preserved)
- GET /healthz — public, minimal non-sensitive response
- Argon2 password hash verification
- Secure signed session cookies (Secure, HttpOnly, SameSite=Lax, time-limited)
- Session-fixation prevention (session renewal on login)
- CSRF synchronizer token (stored in session, validated on POST)
- In-memory login throttling (single-process, deterministic)
- Security headers (Cache-Control, X-Content-Type-Options, Referrer-Policy, CSP frame-ancestors, no permissive CORS)
- FastAPI /docs, /redoc, /openapi.json disabled
- Static assets served under a narrow public allowlist
- Fail-closed configuration: missing credentials → web host refuses to start protected routes
- Validation script `scripts/check-web-authentication.sh` with executable HTTP tests
- CI integration in `.github/workflows/validate.yml`
- GitOps rollout contract documentation (no implementation in this repo)

### Explicitly Out of Scope

- Live dashboard with short polling (PR 0034)
- Inverter observability cards (PR 0035)
- Device runtime duration or daily energy metrics (PR 0036)
- Structured event feed (PR 0037)
- Manual ON/OFF command queue (PR 0038)
- Hardware execution (PR 0039)
- Runtime configuration overrides (PR 0040)
- Historical metrics and charts (PR 0041+)
- Direct Tuya, relay, or inverter calls
- Raw log file exposure
- ML control (deferred per ADR-0003)
- Multi-user accounts, password reset, external identity providers
- Dockerfile, docker-compose.yml, or Kubernetes manifest changes in this repository
- run.py modification (no required change identified)

## 4. Security Model

### 4.1 Operator Identity

One operator account. Username is validated against the `WEB_AUTH_USERNAME` environment
variable (case-sensitive comparison). Any mismatch produces the same generic error as a
wrong password. The username is NOT logged on failure.

### 4.2 Password Hashing

**Dependency:** `argon2-cffi` (`argon2` package on PyPI).

`argon2.PasswordHasher` is used for verification. No custom hash implementation.
The `WEB_AUTH_PASSWORD_HASH` environment variable holds the full Argon2 hash string
(produced externally via `argon2` CLI or equivalent — this PR does not generate hashes).

Verification uses `PasswordHasher.verify(hash, password)`. The plaintext password is
never persisted, logged, or stored beyond the verification call. No timing attack
mitigation beyond what Argon2 provides.

### 4.3 Session Design

**Dependency:** Starlette `SessionMiddleware` (built into FastAPI).

Starlette's `SessionMiddleware` uses `itsdangerous.URLSafeTimedSerializer` with the
`WEB_AUTH_SESSION_SECRET` as the signing key. Sessions are server-signed cookies; the
payload lives in the cookie (no server-side store). The session payload contains only:

- `user` — the authenticated username (string, always the configured operator)
- `csrf_token` — the current CSRF synchronizer token (random string)
- `created_at` — epoch seconds of session creation

No password hash, session secret, device data, Tuya data, or sensitive runtime content
is stored in the session payload.

### 4.4 Cookie Attributes

| Attribute | Value |
|---|---|
| `Secure` | `True` (except in test harness where it may be relaxed for HTTP — see Section 9) |
| `HttpOnly` | `True` |
| `SameSite` | `Lax` |
| `Path` | `/` |
| `Max-Age` / expiration | `WEB_AUTH_SESSION_TTL_SECONDS` (default 3600) |
| Cookie name | `dessmonitor_session` |
| Deletion on logout | Cookie is deleted via `response.delete_cookie()` |

The test harness must allow testing Secure cookies over HTTP by configuring the
session middleware with `session_cookie_secure=False` only when a test-mode
environment variable is set. CI tests use `session_cookie_secure=False`; production
always uses `True`.

### 4.5 CSRF

Synchronizer token pattern:

1. **Token creation:** A 32-byte random hex token is generated on first session
   creation and stored in the session under `csrf_token`.
2. **Token delivery:** The token is rendered as a hidden `<input name="csrf_token">`
   in every form (login, logout).
3. **Token validation:** Every POST/PUT/PATCH/DELETE handler reads `csrf_token`
   from the form body and compares it (constant-time string comparison) against
   the session's `csrf_token`. Mismatch or absence returns 403 with a generic
   error message.
4. **Token rotation:** The token is rotated on every successful login. Logout
   also rotates the token (though the session is cleared immediately after).
5. **Failure response:** 403 with generic message. No session data is leaked.
6. **No CSRF token logging:** The token value must never appear in logs.

### 4.6 Throttling

In-memory deterministic throttling using `time.monotonic()`:

- **Key:** `(username, source_ip)` tuple. Source IP is read from
  `request.client.host` only; no `X-Forwarded-For` parsing.
- **Window:** `WEB_AUTH_ATTEMPT_WINDOW_SECONDS` (default 300).
- **Max attempts:** `WEB_AUTH_MAX_ATTEMPTS` (default 5).
- **Lockout:** `WEB_AUTH_LOCKOUT_SECONDS` (default 900).
- **Cleanup:** On every attempt, expired entries (older than window + lockout)
  are pruned from the in-memory store. Bounded to the number of distinct keys
  seen in the current window.
- **After success:** The failed-attempt counter for that key is cleared.
- **Throttled response:** Same generic message as invalid credentials. No
  indication that throttling is active.
- **Memory lifetime:** Process-local only. Restart clears state. Documented
  that horizontal scaling or multi-user auth would require replacement.
- Tests exercise lockout expiry and counter reset.

### 4.7 Fail-Closed Behavior

`create_app()` reads `WEB_AUTH_USERNAME` from `os.environ`. If the variable is
absent, empty, or whitespace-only, `create_app()` raises `WebAuthConfigError("auth-not-configured")`.
The web host startup fails; `run.py` catches the exception and continues the
automation process without any HTTP listener. No route, including `/healthz` and
`/control/state`, is exposed.

`WEB_AUTH_SESSION_SECRET` must be at least 32 bytes (64 hex characters) after
stripping whitespace. Shorter values raise `WebAuthConfigError("session-secret-too-short")`.

`WEB_AUTH_PASSWORD_HASH` must be non-empty. Absent or empty raises
`WebAuthConfigError("password-hash-missing")`.

No fallback leaves `/control/state` publicly readable.

### 4.8 Security Headers

Applied via FastAPI middleware or per-route `Response` objects:

| Header | Value | Routes |
|---|---|---|
| `Cache-Control` | `no-store` | /login, / (authenticated pages) |
| `X-Content-Type-Options` | `nosniff` | All HTML and JSON responses |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | All responses |
| `Content-Security-Policy` | `default-src 'self'; frame-ancestors 'none'; form-action 'self'` | All HTML responses |
| `Access-Control-Allow-Origin` | NOT SET | All routes |

No permissive CORS headers are added.

### 4.9 Secret Handling

- No credential, password hash, session secret, CSRF token, or cookie value
  is written to any log at any level.
- `repr()` and `str()` of the auth config dataclass redact the password hash
  and session secret (display `"<redacted>"`).
- Exception messages from auth failures are generic and contain no secret values.
- Test-only credentials use visibly fake values (e.g., `test-user`,
  `$argon2id$v=19$m=65536,t=3,p=4$fake$fakehash`), never real production hashes.

## 5. Route Contract

| Route | Method | Auth | Behavior |
|---|---|---|---|
| `/healthz` | GET | Public | Returns `{"status":"ok","web_api":"available"}`. No device/inverter/config data. |
| `/login` | GET | Public | Renders `login.html`. Authenticated users redirected (303) to `/`. `Cache-Control: no-store`. CSRF token form field. |
| `/login` | POST | Public + CSRF | Validates username (env), Argon2 hash. Generic error on any failure. Creates session on success, redirect 303 to `/`. Throttling applied. |
| `/logout` | POST | Auth + CSRF | Clears session, deletes cookie, redirect 303 to `/login`. |
| `/` | GET | Auth | Renders `index.html` minimal landing shell. No dashboard, no polling. Shows operator username. |
| `/control/state` | GET | Auth | Existing read-only contract. JSON 401 for unauthenticated (no redirect). No write methods. |
| `/docs` | GET | — | Returns 404 (disabled). |
| `/redoc` | GET | — | Returns 404 (disabled). |
| `/openapi.json` | GET | — | Returns 404 (disabled). |
| `/static/{path}` | GET | Public (allow-list) | Serves only `login.css`. 404 for any other path. No directory listing. |

**Static asset allowlist:** Only `/static/login.css` is public. Any request for
`/static/{path}` where `path` is not exactly `login.css` returns 404.

**GET /logout:** Returns 405 Method Not Allowed.

**Alternate HTTP methods on protected routes:** Return 405. No protected route
becomes accessible through an alternate HTTP verb.

## 6. Environment Contract

| Variable | Required | Validation | Default | Failure mode |
|---|---|---|---|---|
| `WEB_AUTH_USERNAME` | Yes | Non-empty, stripped | — | `create_app()` raises `WebAuthConfigError("auth-not-configured")` |
| `WEB_AUTH_PASSWORD_HASH` | Yes | Non-empty | — | `create_app()` raises `WebAuthConfigError("password-hash-missing")` |
| `WEB_AUTH_SESSION_SECRET` | Yes | ≥ 64 hex chars (32 bytes) stripped | — | `create_app()` raises `WebAuthConfigError("session-secret-too-short")` |
| `WEB_AUTH_SESSION_TTL_SECONDS` | No | Integer > 0 | `3600` | Invalid → default |
| `WEB_AUTH_MAX_ATTEMPTS` | No | Integer ≥ 1 | `5` | Invalid → default |
| `WEB_AUTH_ATTEMPT_WINDOW_SECONDS` | No | Integer ≥ 1 | `300` | Invalid → default |
| `WEB_AUTH_LOCKOUT_SECONDS` | No | Integer ≥ 1 | `900` | Invalid → default |

All credential variables are read once at `create_app()` time and held in a
frozen dataclass. No `os.getenv()` call occurs at request time for credentials.

Optional tuning variables default silently on invalid values (non-integer, zero,
or negative). They never weaken authentication — defaults are always applied
conservatively.

## 7. Required Implementation Files

| Exact path | Action | Responsibility | Why required |
|---|---|---|---|
| `app/web_auth.py` | **Create** | Auth config, password verification, session helpers, CSRF helpers, throttling, security headers middleware | Centralizes all auth logic; avoids polluting web_host.py |
| `app/web_routes.py` | **Create** | Route handlers: `/login` GET/POST, `/logout` POST, `/` GET, `/healthz` GET | Separates route logic from app construction |
| `app/web/templates/login.html` | **Create** | Login form (username, password, CSRF token, submit) | Server-served HTML; no SPA |
| `app/web/templates/index.html` | **Create** | Minimal authenticated landing shell | Proves authentication without dashboard |
| `app/web/static/login.css` | **Create** | Minimal login page styling | Narrow public asset allowlist |
| `app/web/__init__.py` | **Create** | Package marker | Python package requirement |
| `app/web_host.py` | **Edit** | Extend `create_app()` with auth middleware, route inclusion, docs disable, static mount | App factory wiring |
| `requirements.txt` | **Edit** | Add `argon2-cffi`, `jinja2`, `python-multipart`, `itsdangerous` | New auth dependencies |
| `scripts/check-web-authentication.sh` | **Create** | Executable HTTP authentication tests | CI validation; no grep-only checks |
| `.github/workflows/validate.yml` | **Edit** | Add one validation step | CI integration |
| `.project-memory/pr/0033-authentication-foundation-and-login-page/CODER_REPORT.txt` | **Create** | Coder completion report | Standard PR artifact |

### Forbidden Changes (unchanged files)

- `run.py` — unchanged (auth integration is through `create_app()` and env vars; no edit needed)
- `api.py` — unchanged
- `Dockerfile` — unchanged
- `docker-compose.yml` — unchanged
- `app/docker/` — unchanged (legacy manifests; GitOps deployment is external)
- `app/web_runtime_integration.py` — unchanged (`create_app()` signature extended backward-compatibly)
- `app/web_control_state_provider.py` — unchanged
- `app/web_host_startup.py` — unchanged
- `app/control/` — all modules unchanged
- `ROADMAP.md` — unchanged (planner discipline)
- Agent configuration files — unchanged

## 8. Detailed Implementation Steps

### Step 1: Add dependencies to requirements.txt

Add exact version pins for:
- `argon2-cffi>=23.1.0` — Argon2 password verification
- `jinja2>=3.1.0` — HTML template rendering
- `python-multipart>=0.0.18` — form data parsing
- `itsdangerous>=2.2.0` — signed session serialization (explicit pin; also transitive from Starlette)

### Step 2: Create `app/web/` directory and static assets

Create `app/web/__init__.py` (empty package marker).

Create `app/web/templates/login.html`:
- HTML5 doctype, `<meta charset="utf-8">`, viewport meta
- Form with `method="post" action="/login"`, `autocomplete="off"`
- Hidden `csrf_token` input
- Username input (`name="username"`, `autocomplete="username"`)
- Password input (`name="password"`, `autocomplete="current-password"`)
- Submit button
- Generic error placeholder (populated by server via template context)
- References `/static/login.css`
- Link to `login.css` only
- No JavaScript requirements (vanilla JS for form UX optional, not required)

Create `app/web/templates/index.html`:
- HTML5 doctype, basic structure
- Displays "Authenticated as {username}" text
- Logout form with hidden CSRF token, POST to `/logout`
- No dashboard, no polling, no device data, no control widgets
- References no external resources

Create `app/web/static/login.css`:
- Minimal responsive styling for the login form
- Dark/light compatible base colors
- No external font or image dependencies
- Under ~200 lines

### Step 3: Create `app/web_auth.py`

Module responsibilities:

**3a. Auth configuration dataclass:**
```python
@dataclass(frozen=True)
class WebAuthConfig:
    username: str
    password_hash: str   # __repr__ redacts to "<redacted>"
    session_secret: str   # __repr__ redacts to "<redacted>"
    session_ttl_seconds: int
    max_attempts: int
    attempt_window_seconds: int
    lockout_seconds: int
```

**3b. Config loader function:**
```python
def load_auth_config(environ: dict[str, str] | None = None) -> WebAuthConfig | None:
```
- Reads from `environ` or `os.environ`
- Returns `None` if `WEB_AUTH_USERNAME` is missing/empty (caller decides fail-closed)
- Validates `WEB_AUTH_SESSION_SECRET` length ≥ 64 hex chars
- Validates `WEB_AUTH_PASSWORD_HASH` non-empty
- Raises `WebAuthConfigError` on validation failures
- Parses optional tuning variables with defaults on invalid

**3c. Password verification:**
```python
def verify_password(password_hash: str, password: str) -> bool:
```
- Uses `argon2.PasswordHasher().verify(password_hash, password)`
- Returns `True` on match, `False` on `argon2.exceptions.VerifyMismatchError`
- Re-raises unexpected argon2 exceptions (not a verification failure — indicates config problem)
- Never logs the password or hash

**3d. Session helpers (using itsdangerous):**
```python
def create_session_data(username: str, ttl_seconds: int) -> dict:
    # Returns dict with user, csrf_token, created_at
    # csrf_token is secrets.token_hex(32)

def validate_session(session: dict) -> tuple[bool, str | None]:
    # Returns (valid, username_or_None)
    # Checks user key exists
    # Does NOT check expiry (handled by itsdangerous signing)
```

The actual signing/unsigning is handled by Starlette's `SessionMiddleware`, which
uses `itsdangerous.URLSafeTimedSerializer`. `create_session_data()` builds the payload
dictionary that `request.session.update()` will write.

**3e. CSRF helpers:**
```python
def generate_csrf_token(session: dict) -> str:
    # Creates token if not present, returns it

def validate_csrf_token(session: dict, form_token: str | None) -> bool:
    # Constant-time comparison against session['csrf_token']
    # Returns False on missing/mismatch
    # Uses secrets.compare_digest for comparison
```

**3f. Throttling:**
```python
class LoginThrottle:
    def __init__(self, max_attempts: int, window_seconds: int, lockout_seconds: int): ...
    def check(self, username: str, source_ip: str) -> bool:
        # Returns True if attempt allowed, False if throttled
        # Prunes expired entries
    def record_failure(self, username: str, source_ip: str) -> None: ...
    def clear(self, username: str, source_ip: str) -> None: ...
```
- Thread-safe not required (single-threaded asyncio)
- Uses `time.monotonic()` for all timing
- Stores per-key `(failures: int, first_failure: float, locked_until: float | None)`

**3g. Security headers middleware:**
```python
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next): ...
```
- Adds headers as defined in Section 4.8 based on response content-type
- Does not modify JSON responses' existing headers beyond nosniff

### Step 4: Create `app/web_routes.py`

Module responsibilities:

**4a. `/healthz` GET:**
```python
# Returns PlainTextResponse or JSONResponse with {"status":"ok","web_api":"available"}
# No auth, no template, no session access
# No device/inverter/config/topology data
```

**4b. `/login` GET:**
```python
# If session contains valid user → RedirectResponse("/", status_code=303)
# Otherwise renders login.html with csrf_token in context
# Cache-Control: no-store header
```

**4c. `/login` POST:**
```python
# 1. CSRF check: validate_csrf_token(session, form.get("csrf_token"))
# 2. Throttle check: throttler.check(username, request.client.host)
# 3. Username comparison against config.username (case-sensitive)
# 4. Password verification via verify_password(config.password_hash, password)
# 5. On failure: same generic error for wrong username, wrong password, throttled
# 6. On failure: throttler.record_failure(...)
# 7. On success: throttler.clear(...); create_session_data(); write session;
#    rotate csrf_token; RedirectResponse("/", status_code=303)
# 8. Never log username, password, or hash
```

**4d. `/logout` POST:**
```python
# 1. CSRF check
# 2. request.session.clear()
# 3. response.delete_cookie("dessmonitor_session")
# 4. RedirectResponse("/login", status_code=303)
```

**4e. `/` GET:**
```python
# Renders index.html with {"username": session["user"]}
# 401 redirect to /login if unauthenticated
```

**4f. Docs disable:**
```python
# Mount a router that returns 404 for /docs, /redoc, /openapi.json
# Done in web_host.py via overriding FastAPI's default docs routes
```

### Step 5: Modify `app/web_host.py`

Extend `create_app()`:

```python
def create_app(
    runtime_state_provider: Callable[[], dict[str, Any] | None] | None = None,
):
```

Changes:

1. **Fail-closed check:** At function start, call `load_auth_config()`. If it returns
   `None` (username missing), raise `WebAuthConfigError("auth-not-configured")`.
   This prevents the app from being created without auth config.

2. **Auth module imports:** Lazy imports of `app.web_auth` (config, throttle, etc.)
   inside the function body. Auth deps (argon2, itsdangerous) are imported only
   when auth is configured.

3. **Session middleware:** Add `SessionMiddleware` with `secret_key=config.session_secret`,
   `session_cookie="dessmonitor_session"`, `max_age=config.session_ttl_seconds`,
   `same_site="lax"`, `https_only=True` (relaxable for testing).

4. **Security headers middleware:** Add `SecurityHeadersMiddleware` (from `app.web_auth`).

5. **Static files:** Mount `/static` with `app.mount("/static", StaticFiles(directory="app/web/static"), name="static")`.
   Use a custom `StaticFiles` subclass or middleware that restricts to the allowed file
   list (`login.css` only). Any path not in the allowlist returns 404.

6. **Route registration:** Include routes from `app.web_routes` (login, logout, index,
   healthz). Include the existing `create_control_state_read_router(provider)`.

7. **Docs disable:** Set `app.docs_url = None`, `app.redoc_url = None`, `app.openapi_url = None`
   on the FastAPI instance.

8. **Backward compatibility:** The `runtime_state_provider` parameter is unchanged.
   Default `create_app()` without `runtime_state_provider` still works (placeholder provider).
   The only breaking change is that auth env vars are now required — which is the intended
   fail-closed behavior.

### Step 6: Create validation script `scripts/check-web-authentication.sh`

Follows the existing `scripts/check-*.sh` pattern. Implements executable HTTP tests
using `python3` to start a test server, send requests, and assert responses.

See Section 10 for the complete test contract.

### Step 7: Update `.github/workflows/validate.yml`

Add one step after the existing `check-runtime-read-only-web-host.sh`:

```yaml
      - name: 🔍 Web authentication check
        run: bash scripts/check-web-authentication.sh
```

### Step 8: Write CODER_REPORT.txt

Standard completion report with:
- Files changed list
- Dependencies added with resolved versions
- Validation results
- Boundary confirmations

## 9. Dependency Resolution

| Dependency | Minimum version | Rationale |
|---|---|---|
| `argon2-cffi` | `>=23.1.0` | Argon2 password verification. `PasswordHasher` API is stable. 23.1.0 is the current stable series. |
| `jinja2` | `>=3.1.0` | HTML template rendering for login and landing pages. 3.1+ is the current stable major version. |
| `python-multipart` | `>=0.0.18` | Required by FastAPI/Starlette for `request.form()` parsing of POST bodies. Not a core FastAPI dependency; must be explicit. |
| `itsdangerous` | `>=2.2.0` | Signed session serialization used by `Starlette.SessionMiddleware`. While transitive through Starlette in some configurations, pinning explicitly ensures CI reproducibility. |

These are the **only** new dependencies. No frontend package manager, no database driver,
no Redis client, no additional crypto library.

The coder must resolve exact installed versions and record them in CODER_REPORT.txt.

**Test-mode cookie relaxation:** The validation script sets `WEB_AUTH_TEST_HTTP=1` which
causes `create_app()` to set `session_cookie_secure=False` on the session middleware
and `https_only=False`. This allows testing over HTTP in CI. The variable is read only
when all three credential variables are present.

## 10. Validation Contract

All commands must be runnable by coder and precommit-review. No command uses SKIP, `|| true`,
or a fallback that hides failure.

### 10.1 Bootstrap step

```bash
pip install -r requirements.txt
```

### 10.2 Validation script: `scripts/check-web-authentication.sh`

The script must execute each test case below using Python subprocess or inline Python blocks.
Pattern follows `scripts/check-runtime-read-only-web-host.sh`.

**Test server setup:** Start a temporary Uvicorn server on a free port with test credentials:

```
WEB_AUTH_USERNAME=test-operator
WEB_AUTH_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$dGVzdC1zYWx0LXRlc3Q$test-hash-value-for-validation-only
WEB_AUTH_SESSION_SECRET=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2
WEB_AUTH_TEST_HTTP=1
WEB_HOST_ENABLED=1
WEB_HOST_BIND=127.0.0.1
```

The password hash above is a structurally valid Argon2id hash string with known-weak
test values (16-byte salt, 4-byte hash). The coder must generate a real verify-able test
hash during implementation by pre-computing it with `argon2` CLI.

**Test cases:**

**CONFIGURATION (5 tests)**
- `[1]` Missing `WEB_AUTH_USERNAME` → app factory raises `WebAuthConfigError("auth-not-configured")`
- `[2]` Missing `WEB_AUTH_PASSWORD_HASH` → raises `WebAuthConfigError("password-hash-missing")`
- `[3]` Missing `WEB_AUTH_SESSION_SECRET` → raises `WebAuthConfigError("session-secret-too-short")`
- `[4]` `WEB_AUTH_SESSION_SECRET` length < 64 hex chars → raises `WebAuthConfigError`
- `[5]` Exception text contains no secret value (grep the stringified exception)

**PUBLIC ROUTES (4 tests)**
- `[6]` `GET /login` → 200, HTML content, contains `<form`, does NOT contain password hash
- `[7]` `GET /healthz` → 200, JSON, contains `"status":"ok"` or equivalent
- `[8]` `/healthz` response contains NONE of: `device`, `inverter`, `voltage`, `tuya`, `config`
- `[9]` `GET /docs` → 404

**UNAUTHENTICATED ACCESS (4 tests)**
- `[10]` `GET /` → 303 redirect to `/login`
- `[11]` `GET /control/state` → 401, JSON, no device data in body
- `[12]` No protected route accessible via alternate HTTP method (e.g., `PUT /` returns 405)
- `[13]` `GET /static/login.css` → 200; `GET /static/../../secrets` → 404

**LOGIN (7 tests)**
- `[14]` Valid credentials → 303 redirect to `/`, session cookie set
- `[15]` Session cookie has `Secure` flag (when over HTTPS or test-mode relaxed — check
  the Set-Cookie header for attributes)
- `[16]` Session cookie has `HttpOnly` flag
- `[17]` Session cookie has `SameSite=Lax` (or stricter)
- `[18]` Invalid username returns same error as invalid password (compare response bodies)
- `[19]` Valid username + invalid password → same generic error
- `[20]` Successful login replaces prior session (session_id or csrf_token changes)

**CSRF (5 tests)**
- `[21]` `POST /login` without CSRF token → 403
- `[22]` `POST /login` with wrong CSRF token → 403
- `[23]` `POST /login` with correct CSRF token → 303 (token extracted from GET /login form)
- `[24]` `POST /logout` without CSRF token → 403
- `[25]` `POST /logout` with correct CSRF token → 303 redirect to /login

**AUTHENTICATED ACCESS (3 tests)**
- `[26]` Authenticated `GET /` → 200, HTML, contains "test-operator"
- `[27]` Authenticated `GET /control/state` → 200, JSON
- `[28]` `/control/state` response does not contain `tuya_device_id`, `control_key`, `api_key`

**LOGOUT (2 tests)**
- `[29]` `POST /logout` (authenticated) → redirect to `/login`
- `[30]` Old session cookie after logout → cannot access `/control/state` (401 or redirect)

**THROTTLING (4 tests)**
- `[31]` Repeated failures (≥ max_attempts) → subsequent attempt returns generic error (not 200, not device data)
- `[32]` Throttled response does not reveal throttling state
- `[33]` Lockout expires → login works again after `lockout_seconds`
- `[34]` Successful login resets throttle counter

**BOUNDARIES (6 tests)**
- `[35]` `app/web_auth.py` contains no import of `app.tuya`, `app.service`, `app.devices`,
  `app.monitoring`, `app.ml`, `app.weather`, `relay_tuya_controller`, `smart_home_controller`
- `[36]` `app/web_routes.py` contains no import of the above
- `[37]` No `POST`, `PUT`, `PATCH`, `DELETE` route accessible at `/control/state` or `/devices`
- `[38]` No route returns raw log content
- `[39]` No polling JavaScript or dashboard code exists in any served page
- `[40]` `app/web_host.py` modified; `run.py` NOT modified

### 10.3 Regression checks

```bash
bash scripts/check-web-ui-read-only-endpoint.sh
bash scripts/check-web-host-startup.sh
bash scripts/check-web-control-state-provider.sh
bash scripts/check-runtime-read-only-web-host.sh
bash scripts/check-web-authentication.sh
python3 -m compileall -q .
git diff --check
```

### 10.4 CI integration

The new step in `.github/workflows/validate.yml`:
```yaml
      - name: 🔍 Web authentication check
        run: bash scripts/check-web-authentication.sh
```

Placed after `check-runtime-read-only-web-host.sh`. CI already runs `pip install -r requirements.txt`,
so all new dependencies are installed before the auth check runs.

## 11. GitOps Rollout Contract

This section documents the external GitOps requirement. No implementation in this
repository.

### Production Secret Contract

The external GitOps repository (Kubernetes manifests) must define a Secret:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: dessmonitor-web-auth
type: Opaque
stringData:
  web-auth-username: "<operator-username>"
  web-auth-password-hash: "<argon2-hash>"
  web-auth-session-secret: "<random-64-hex-chars>"
```

The Deployment/StatefulSet must reference:
```yaml
env:
  - name: WEB_AUTH_USERNAME
    valueFrom:
      secretKeyRef:
        name: dessmonitor-web-auth
        key: web-auth-username
  - name: WEB_AUTH_PASSWORD_HASH
    valueFrom:
      secretKeyRef:
        name: dessmonitor-web-auth
        key: web-auth-password-hash
  - name: WEB_AUTH_SESSION_SECRET
    valueFrom:
      secretKeyRef:
        name: dessmonitor-web-auth
        key: web-auth-session-secret
```

Optional tuning variables (`WEB_AUTH_SESSION_TTL_SECONDS`, `WEB_AUTH_MAX_ATTEMPTS`,
`WEB_AUTH_ATTEMPT_WINDOW_SECONDS`, `WEB_AUTH_LOCKOUT_SECONDS`) may be set as plain
env vars if non-default values are desired.

### Safe Rollout Order

1. Application PR 0033 passes review and CI.
2. Fixed application image is built; an immutable digest is available.
3. GitOps: create `dessmonitor-web-auth` Secret with real credentials.
4. GitOps: add `secretKeyRef` environment references to Deployment.
5. GitOps: update the Deployment image to the new immutable digest.
6. Deploy Secret + Deployment atomically (or in sequence with Secret first,
   followed by image update within the same maintenance window). The window
   between Secret creation and image update must not expose `/control/state`
   without authentication — the existing `web_host` startup does not read
   auth env vars, so it remains unchanged until the image is updated.
7. Verify: `curl /healthz` → 200; `curl /control/state` → 401; browser login → 303;
   authenticated `/control/state` → 200; logout → invalidates; pod logs contain
   no secrets; existing automation continues.
8. Declare production-validated before PR 0034 begins.

### What This Repository Does NOT Do

- Does not create or modify `app/docker/` Kubernetes manifests.
- Does not create Kubernetes Secrets.
- Does not change Dockerfile, docker-compose.yml, or container entrypoint.
- Does not commit any password hash, session secret, or credential value.
- Does not reference external GitOps repository URLs or specific cluster details.

## 12. Security and Safety Boundaries

All of the following must be true after implementation.

| # | Boundary | Verification |
|---|---|---|
| 1 | No Tuya, relay, or hardware call from any auth/web module | grep check in validation script |
| 2 | No device write (no switch_on, switch_off, set_numeric) | grep check; no write routes |
| 3 | No dashboard implementation | index.html is a minimal landing shell only |
| 4 | No polling JavaScript | grep for `setInterval`, `setTimeout`, `fetch` in templates |
| 5 | No raw log file exposure | grep for `/logs`, `file` routes |
| 6 | No ML control | no `ml_` imports |
| 7 | No secrets committed to Git | no `WEB_AUTH_*` values in committed files except validation script test values |
| 8 | No authentication bypass | `create_app()` fails closed; all protected routes require valid session |
| 9 | Existing automation unchanged | `run.py` not modified; `PUMP_AUTOMATION_ENABLED` behavior preserved |
| 10 | Existing control pipeline unchanged | `app/control/` modules not modified |

## 13. Acceptance Criteria

The implementation is complete when:

1. All 40 validation script checks pass (`scripts/check-web-authentication.sh` exit 0).
2. All existing regression checks pass (Section 10.3).
3. `python3 -m compileall -q .` exits 0.
4. `git diff --check` exits 0.
5. Only the files listed in Section 7 are created or modified.
6. `run.py`, `Dockerfile`, `docker-compose.yml`, `app/docker/`, `app/control/` are unchanged.
7. No secret values appear in any committed file (test-only visibly fake values are acceptable).
8. PR 0034 dashboard work has NOT been started — no polling, no device cards, no ON/OFF controls.
9. CODER_REPORT.txt records exact dependency versions resolved during implementation.

## 14. Blockers and Open Decisions

**No unresolved architectural decisions remain for the coder.** All design choices
are locked in this PLAN.md: dependency versions, file paths, route contracts,
session design, CSRF pattern, throttling algorithm, fail-closed behavior,
security headers, and validation test cases.

**No blockers.** All prerequisite modules exist. The roadmap gate is satisfied.
The coder has a complete, unambiguous implementation contract.
