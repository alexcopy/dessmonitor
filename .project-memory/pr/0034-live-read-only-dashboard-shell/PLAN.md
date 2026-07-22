# PR 0034 — Live Read-Only Dashboard Shell with Locally Vendored Bulma

**Status:** Planning — complete, locked after commit.
**Scope:** Implementation plan for the first useful live read-only dashboard shell.
**Branch:** `pr-0034-live-read-only-dashboard-shell`
**Base:** `origin/master` (PR 0033 authentication is present)

---

## Gate Confirmation

- [x] Repository is `alexcopy/dessmonitor`.
- [x] Current branch is `pr-0034-live-read-only-dashboard-shell`.
- [x] Branch is based on `origin/master`.
- [x] PR 0033 authentication is present and production-validated.
- [x] Working tree contains no unrelated changes.
- [x] PR 0034 `PLAN.md` does not yet exist — fresh creation.

---

## Objective

Create a responsive, authenticated, read-only dashboard shell served from the
existing embedded FastAPI/Uvicorn web host. The dashboard uses **Bulma 1.0.4**
(vendored locally), a small custom CSS layer, and vanilla JavaScript short
polling.

The dashboard renders the current `GET /control/state` read-model. It
establishes connection-state visibility with connecting, online, stale,
degraded, and offline states. It does **not** add runtime-health timestamps
(PR 0035), device-duration or energy metrics (PR 0036), or any write API.

All architectural and dependency decisions are locked in this document. No
decisions remain for the coder.

---

## Current-State Evidence

### Authenticated routes (from PR 0033)

| Method | Path              | Auth | Description                    |
|--------|-------------------|------|--------------------------------|
| GET    | /                 | Yes  | Authenticated landing shell    |
| GET    | /login            | No   | Login form                     |
| POST   | /login            | No*  | Login (CSRF-protected)         |
| POST   | /logout           | Yes* | Logout (CSRF-protected)        |
| GET    | /control/state    | Yes  | Read-only control state JSON   |
| GET    | /healthz          | No   | Minimal public health          |
| GET    | /static/login.css | No   | Login stylesheet (allowlisted) |

*CSRF-protected even for public routes.

### Current index template

The file `app/web/templates/index.html` is a minimal authenticated landing
shell with:
- Product identity ("dessmonitor Operator")
- Authenticated operator username display
- CSRF-protected logout form
- No dashboard, no polling, no device data
- Inline CSS (no dependency on any framework)

### Current static allowlist

At `app/web_host.py:41`:

```python
_STATIC_ALLOWLIST: frozenset[str] = frozenset({"login.css"})
```

All other paths under `/static/` return 404 via `_RestrictedStaticFiles`.

### Current CSP and security headers

The `SecurityHeadersMiddleware` in `app/web_auth.py` applies:

```
Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; form-action 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
```

No `unsafe-inline`, no external origins, no permissive CORS.

### Current /control/state authentication

- Unauthenticated -> 401 JSON `{"detail": "Not authenticated"}`
- Authenticated -> 200 JSON (from `ControlStateAuthMiddleware`)

### Current /control/state JSON shape

FastAPI serialises `WebUiControlStateResponse` as JSON. The dataclass has:

```json
{
  "status": "unavailable",
  "snapshot": null,
  "read_only": true,
  "api_version": "v1",
  "allowed_actions": ["read-control-state"],
  "forbidden_actions": ["direct-hardware-write", "direct-tuya-command",
    "execute-command", "mutate-shared-state", "bypass-control-layer",
    "bypass-safety-gates", "write-api"],
  "warnings": ["control-state-snapshot-unavailable"],
  "notes": ["read-only-api-contract", "future-web-ui-read-model",
    "operator-writes-through-control-layer", "no-execution"]
}
```

When a snapshot **is** available, `snapshot` contains:

```json
{
  "snapshot_id": "...",
  "created_at": "2025-...",
  "status": "ok|degraded|blocked|unknown",
  "loads": [{
    "load_id": "...",
    "display_name": "...",
    "configured_load_watts": 100.0,
    "currently_on": false,
    "controllable": true,
    "is_life_support": false,
    "roles": ["...", "..."],
    "status": "active|idle|unavailable|unknown",
    "notes": ""
  }],
  "pipeline": { ... },
  "mode": { ... },
  "energy_budget": null,
  "battery_window": null,
  "notes": ["read-only-snapshot", "no-execution"],
  "warnings": ["..."]
}
```

The `snapshot.loads` array is the primary data the dashboard renders.

### Existing tests and validation scripts

- `scripts/check-web-authentication.sh` -> 41 tests
- `scripts/check-web-ui-read-only-endpoint.sh` -> endpoint contract tests
- `scripts/check-web-host-startup.sh` -> 25 tests
- `scripts/check-web-control-state-provider.sh` -> 28 tests
- `scripts/check-runtime-read-only-web-host.sh` -> 35 tests
- `.github/workflows/validate.yml` -> full CI pipeline

### Fields deferred to PR 0035+

The dashboard makes no use of:
- `snapshot_created_at` / `runtime_heartbeat_at` / `devices_refreshed_at` /
  `inverter_refreshed_at` / `service_started_at` - PR 0035
- `currently_on_seconds` / `day_on_seconds` / `day_energy_kwh` - PR 0036
- Inverter fields - PR 0035
- Water temperature / ambient temperature - PR 0036

---

## Architectural Constraints

All architectural invariants from ROADMAP.md apply, specifically:

- **W1-W5:** FastAPI host, no Flask, no second container
- **F1-F3:** HTML + CSS + vanilla JS only; no React/Vue/Angular/Node/npm/Vite
- **L1-L4:** Short polling only; one request max; timeout/retry; no WebSockets
- **A1-A12:** Authentication preserved; no localStorage tokens; CSRF required
- **R1-R4:** Read model only; no raw internals; null for unavailable values
- **D1-D5:** No write API; no direct Tuya/hardware calls
- **S1-S6:** Existing automation unchanged; no execution
- **DP1-DP4:** Application-image changes only; no GitOps in this PR

---

## Bulma Dependency Contract

### Version and provenance

| Property | Value |
|----------|-------|
| Library | Bulma (https://github.com/jgthms/bulma) |
| Version | **1.0.4** (pinned, immutable) |
| License | MIT (upstream, included verbatim) |
| Artifact | `bulma.min.css` - official prebuilt minified CSS only |
| Provenance | GitHub tagged release `1.0.4` -> official tarball/asset |
| JavaScript | **None.** Bulma ships no JS for v1; no JS is vendored or loaded |
| Build | **None.** No npm, Node, Sass, Vite, Webpack, or build step |
| Network | **Zero runtime network requests.** No CDN, no external fonts, no icons |

### Vendored paths (mandatory)

```
app/web/static/vendor/bulma/1.0.4/bulma.min.css
app/web/static/vendor/bulma/1.0.4/LICENSE
```

### Integrity verification

- SHA-256 is calculated from the official downloaded file **before** commit.
- The SHA-256 digest is recorded in the validation script.
- The coder and reviewer independently verify the hash matches the official release.
- No manual modification or minification of the upstream artifact.

### Loading order

In `<head>`:

```html
<link rel="stylesheet" href="/static/vendor/bulma/1.0.4/bulma.min.css">
<link rel="stylesheet" href="/static/dashboard.css">
```

Bulma loads first. `dashboard.css` loads second and may intentionally override
Bulma defaults (spacing, colour, dark-mode tweaks) using equal-specificity or
slightly higher-specificity selectors. No `!important` without documented reason.

### Upgrade rule

A Bulma version upgrade is a separate explicit change with its own review,
provenance verification, and regression validation. No coder may change the
version number without explicit planner approval.

---

## Vendor Provenance and License

### Acquisition procedure

1. Download from the official Bulma 1.0.4 tagged release.
2. Extract only `bulma.min.css`.
3. Calculate SHA-256.
4. Copy the upstream MIT `LICENSE` file from the Bulma repository root.
5. Record the computed SHA-256 in `scripts/check-web-dashboard.sh`.

### Files to commit

- `app/web/static/vendor/bulma/1.0.4/bulma.min.css`
- `app/web/static/vendor/bulma/1.0.4/LICENSE`

### What is NOT committed

- No `.zip`, `.tar.gz`, `package.json`, `node_modules`, or build artifacts.
- No developer-only Bulma source files (`.sass`, `.scss`, `.map`).

---

## User Experience Contract

### Page structure (authenticated `GET /`)

| Area | Purpose | Bulma classes |
|------|---------|---------------|
| Application header | Product identity, operator info, logout | `navbar`, `navbar-brand`, `navbar-item`, `navbar-end` |
| Connection-status badge | Current connection state | `tags has-addons`, `tag` + custom state classes |
| Last refresh time | Human-readable "Last updated: X ago" | `is-size-7`, `has-text-grey` |
| Dashboard summary | Load stats: total, ON, OFF, unavailable | `columns`, `column`, `card`, `title`, `subtitle` |
| Loads table | Per-load state table | `table`, `is-fullwidth`, `is-striped`, `is-hoverable` |
| Load state cell | ON/OFF badge per load | `tag is-success` / `tag is-light` |
| Unavailable state | Missing snapshot | `notification is-warning` or `message is-warning` |
| CSRF logout form | Logout button | Existing pattern using `.button` |

### Zero-JS components used

Only Bulma components that require **no JavaScript** are used:
- `section`, `container` - layout
- `level`, `navbar` - header (static, no burger toggle)
- `columns`, `column` - responsive grid
- `card`, `content` - data display
- `notification`, `message`, `tag` - status and alerts
- `table`, `is-fullwidth`, `is-striped`, `is-hoverable` - data table
- `button` - logout (form submit, no JS click handler)
- `title`, `subtitle`, `is-size-*`, `has-text-*` - typography
- `is-flex`, `is-align-items-center` - alignment helpers

No dropdown, modal, tab, navbar-burger, or other JS-triggered components are
used unless explicitly implemented in `dashboard.js`.

---

## Current Read-Model Mapping

All fields are from `response.snapshot` (when available). When
`response.snapshot` is null, the dashboard shows "No data available".

### Top-level snapshot fields

| Source path | Label | Format | Null behaviour | Unavailable behaviour |
|-------------|-------|--------|----------------|----------------------|
| `snapshot.status` | System Status | Text: ok / degraded / blocked / unknown | N/A | Hidden |
| `snapshot.created_at` | Snapshot Timestamp | ISO-8601 datetime | "-" | "-" |
| `response.status` | API Status | Text: ok / degraded / unavailable | N/A | N/A |
| `response.warnings` | Warnings | List of strings | Empty | Empty |

### Load fields (each item in `snapshot.loads[]`)

| Source path | Label | Format | Null behaviour | Unavailable behaviour |
|-------------|-------|--------|----------------|----------------------|
| `display_name` | Device Name | Plain text | "-" | "-" |
| `currently_on` | State | ON/OFF badge | "-" | "-" |
| `configured_load_watts` | Load (W) | Number + "W" | "-" | "-" |
| `controllable` | Controllable | Yes/No text | "-" | "-" |
| `is_life_support` | Life Support | Tag visible only when true | Hidden | Hidden |
| `status` | Status | Text: active/idle/unavailable/unknown | "-" | "-" |
| `roles` | Roles | Comma-separated text | Empty | Empty |

### Safe DOM targets

Every field render uses either:
- `element.textContent = value` (safe)
- `element.setAttribute(...)` (safe)
- Template literals inside a safe text context (safe)

No API value is ever inserted via `innerHTML`. No API value is used to
construct a CSS class name from unvalidated input.

---

## Connection State Machine

### States

| State | Entry condition | Exit condition | Operator message | Bulma class | Snapshot visible? | Marked stale? |
|-------|-----------------|----------------|------------------|-------------|-------------------|---------------|
| **connecting** | Page load; no response yet | First response received | "Connecting..." | `tag is-info` + `.is-connecting` | No | No |
| **online** | Successful response within stale threshold | No response within threshold | Nothing (implied) | `tag is-success` + `.is-online` | Yes | No |
| **stale** | No successful response in 15-60s | Successful response | "Stale data" | `tag is-warning` + `.is-stale` | Yes | Yes (banner/opacity) |
| **degraded** | `response.status === "degraded"` or snapshot degraded/blocked | Response becomes OK | "System degraded" | `tag is-warning` + `.is-degraded` | Yes | No |
| **offline** | No successful response for 60+ seconds | Successful response | "Offline" | `tag is-danger` + `.is-offline` | Last snapshot | Yes |

### Polling delays

| Condition | Delay |
|-----------|-------|
| Normal (visible tab, online) | 5000 ms |
| Stale | 5000 ms |
| Degraded | 5000 ms |
| Offline | 10000 ms |
| Tab hidden (`document.hidden`) | 30000 ms |
| Request timeout | 10000 ms |

### Clarification

These states describe **browser-to-server communication status** and
**read-model availability** only. Runtime-health distinction (HTTP response
vs runtime heartbeat) is deferred to PR 0035.

---

## Polling and Concurrency Algorithm

### Algorithm: recursive setTimeout with AbortController

Guarantees:
1. **One request in flight maximum** - current AbortController cancels
   any prior in-flight fetch before starting a new one.
2. **No overlapping setInterval** - setTimeout is scheduled only after
   the previous request settles (success, error, or abort).
3. **Timeout cleanup** - clearTimeout runs in both success and error paths.
4. **Cancellation on unload** - beforeunload handler aborts the controller
   and clears the timeout.
5. **Ordered responses** - because only one request is in flight at a time,
   stale responses cannot overwrite newer state.

```javascript
// Contract: dashboard.js must implement this pattern
let currentAbortController = null;
let pollTimeoutId = null;

function scheduleNextPoll(delayMs) {
  if (pollTimeoutId) clearTimeout(pollTimeoutId);
  pollTimeoutId = setTimeout(() => executePoll(), delayMs);
}

async function executePoll() {
  if (currentAbortController) currentAbortController.abort();
  currentAbortController = new AbortController();
  const timeoutMs = 10000;
  const timeoutId = setTimeout(() => currentAbortController.abort(), timeoutMs);
  try {
    const response = await fetch('/control/state', {
      method: 'GET', credentials: 'same-origin',
      signal: currentAbortController.signal,
      headers: { 'Accept': 'application/json' }
    });
    clearTimeout(timeoutId);
    if (response.status === 401) { handleUnauthenticated(); return; }
    if (!response.ok) { handleHttpError(response.status); scheduleNextPoll(determineDelay()); return; }
    const data = await response.json();
    handleSuccessfulResponse(data);
    scheduleNextPoll(determineDelay());
  } catch (error) {
    clearTimeout(timeoutId);
    if (error.name !== 'AbortError') handleNetworkError(error);
    scheduleNextPoll(determineDelay());
  } finally { currentAbortController = null; }
}

function determineDelay() {
  if (document.hidden) return 30000;
  if (connectionState === 'offline') return 10000;
  return 5000;
}
```

---

## Retry and Visibility Policy

### Bounded backoff

- consecutiveFailures 0-2: normal delay (5s)
- consecutiveFailures 3+: offline delay (10s)

### Hidden-tab behaviour

- `visibilitychange` event listener detects tab visibility.
- When hidden: poll every 30s.
- When becoming visible: execute an **immediate** poll, then resume 5s.
- consecutiveFailures resets to 0 on any successful HTTP 200 response.

---

## Authentication and Session-Expiry Behaviour

### HTTP 401 handling

When `executePoll()` receives HTTP 401:
1. Abort any in-flight poll.
2. Clear all poll state (timers, abort controllers).
3. Navigate to `/login` using `window.location.href = '/login'`.
4. Do not schedule further polls.

### Session expiry during active session

Same mechanism - next poll returns 401, dashboard navigates to `/login`.

### Logout from dashboard

Existing CSRF-protected `POST /logout` form remains the sole logout mechanism.
No JavaScript logout is added.

---

## Rendering and Security Rules

| Rule | Description |
|------|-------------|
| textContent only | All API values via `.textContent` or safe attribute setters |
| No innerHTML | Never insert API data via `innerHTML` or `insertAdjacentHTML` |
| No eval | No `eval()`, `Function()`, `setTimeout(string)`, or dynamic code |
| No inline handlers | No `onclick="..."`, `onerror="..."`, or HTML event handler attributes |
| No external resources | No external fonts, icon libraries, analytics, scripts, or styles |
| No localStorage | No auth tokens, session data, or secrets in localStorage |
| No console secrets | No API values, credentials, or tokens logged to console |
| Stable null formatting | Missing/null values render as "-", never as "null" or 0 |
| Safe class construction | CSS class names from API values never constructed; explicit mapping |

### CSP compliance

CSP remains `default-src 'self'; frame-ancestors 'none'; form-action 'self'`.
Dashboard adds:
- `<link rel="stylesheet" href="/static/vendor/bulma/1.0.4/bulma.min.css">`
- `<link rel="stylesheet" href="/static/dashboard.css">`
- `<script src="/static/dashboard.js"></script>`

All same-origin. `default-src 'self'` covers all. CSP is **not weakened**.

---

## Responsive and Accessibility Requirements

### Responsive design

| Breakpoint | Behaviour |
|------------|-----------|
| Mobile (<768px) | Single column; table scrolls horizontally; navbar stacks |
| Tablet (768-1024px) | 2-column summary cards; table horizontally scrollable |
| Desktop (>1024px) | 3-4 column summary; full-width table |

### No ordinary horizontal overflow

Table wrapper uses `overflow-x: auto`. Long names use `word-break: break-word`
or `text-overflow: ellipsis`.

### Accessibility

| Requirement | Implementation |
|-------------|----------------|
| Semantic headings | `<h1>`, `<h2>`, `<h3>` |
| Landmarks | `<header>`, `<main>` |
| Keyboard logout | `<button>` in `<form>` - natively accessible |
| Visible focus | Bulma defaults + `:focus-visible` overrides |
| Sufficient contrast | Bulma defaults pass WCAG AA |
| Non-color state | Connection state includes text label |
| aria-live connection | `<div aria-live="polite" aria-atomic="true">` |
| reduced-motion | `prefers-reduced-motion` media query in dashboard.css |

### Dark mode

Bulma 1.0.4 auto-detects OS dark preference via `prefers-color-scheme`.
Custom dark-mode overrides in `dashboard.css`. No theme toggle.

---

## Static Allowlist and CSP Plan

### Updated allowlist

In `app/web_host.py`, expand `_STATIC_ALLOWLIST` from:
```python
_STATIC_ALLOWLIST: frozenset[str] = frozenset({"login.css"})
```
to:
```python
_STATIC_ALLOWLIST: frozenset[str] = frozenset({
    "login.css",
    "vendor/bulma/1.0.4/bulma.min.css",
    "dashboard.css",
    "dashboard.js",
})
```

`vendor/bulma/1.0.4/LICENSE` is committed but NOT allowlisted (never served to
browsers, only for provenance).

### CSP

Remains unchanged. `default-src 'self'` covers all same-origin assets.

---

## Exact File Plan

### Files to create

| File | Responsibility | Must not |
|------|---------------|----------|
| `app/web/static/vendor/bulma/1.0.4/bulma.min.css` | Upstream Bulma 1.0.4 prebuilt CSS | Be modified; be from CDN at runtime; include JS |
| `app/web/static/vendor/bulma/1.0.4/LICENSE` | Upstream MIT license | Be missing or modified |
| `app/web/static/dashboard.css` | Custom dashboard presentation | Duplicate Bulma; reference external resources |
| `app/web/static/dashboard.js` | Polling, rendering, state machine | Use innerHTML for API values; add write endpoints |
| `scripts/check-web-dashboard.sh` | Deterministic validation | Modify application code |

### Files to modify

| File | Change | Why |
|------|--------|-----|
| `app/web/templates/index.html` | Replace landing shell with dashboard template | Serve the dashboard UI |
| `app/web_host.py` | Extend `_STATIC_ALLOWLIST` with new assets | Allow browser to load CSS/JS |
| `.github/workflows/validate.yml` | Add dashboard validation step | CI coverage |

### Coder ALLOWED FILES (minimal)

```
app/web/templates/index.html            (modify)
app/web/static/dashboard.css            (create)
app/web/static/dashboard.js             (create)
app/web/static/vendor/bulma/1.0.4/bulma.min.css  (create)
app/web/static/vendor/bulma/1.0.4/LICENSE         (create)
app/web_host.py                         (modify - allowlist only)
scripts/check-web-dashboard.sh          (create)
.github/workflows/validate.yml          (modify - add CI step)
```

---

## Implementation Sequence

### Step 1: Vendor Bulma
1. Create `app/web/static/vendor/bulma/1.0.4/` directory.
2. Download official `bulma.min.css` from GitHub tagged release 1.0.4.
3. Verify SHA-256.
4. Copy upstream LICENSE.
5. Commit.

### Step 2: Update static allowlist
Extend `_STATIC_ALLOWLIST` in `app/web_host.py`.

### Step 3: Create dashboard.css
Write custom CSS overrides for connection states, responsive layout,
reduced-motion, and dark mode.

### Step 4: Create dashboard.js
Implement state machine, polling with AbortController, safe DOM rendering,
HTTP 401 redirect, error handling.

### Step 5: Update index.html
Replace landing shell with Bulma-based dashboard template. External CSS/JS
only. No inline styles or scripts.

### Step 6: Create validation script
Create `scripts/check-web-dashboard.sh` with behavioural tests.

### Step 7: Add CI step
Add dashboard validation to `.github/workflows/validate.yml` after auth check.

### Step 8: Verify regression suite
Run all existing validation scripts.

---

## Validation Plan

### Test structure

`scripts/check-web-dashboard.sh` starts Uvicorn on a free port and exercises
real HTTP tests using Python's `http.client` and `urllib`.

### Test categories

**AUTHENTICATION AND ACCESS (5 tests)**
- Unauthenticated `GET /` -> 303 redirect to `/login`
- Authenticated `GET /` -> 200, contains Bulma class selectors
- Authenticated `GET /` -> 200, contains dashboard.js script tag
- Authenticated `GET /` -> 200, contains Bulma CSS link
- Authenticated `GET /` -> 200, contains dashboard.css link

**BULMA INTEGRITY (4 tests)**
- `GET /static/vendor/bulma/1.0.4/bulma.min.css` -> 200
- Content-Type is `text/css`
- SHA-256 matches pinned value
- `GET /static/vendor/bulma/1.0.4/LICENSE` -> 404 (not served)

**ALLOWLIST AND CSP (4 tests)**
- `GET /static/dashboard.css` -> 200
- `GET /static/dashboard.js` -> 200
- Path traversal -> 404
- CSP header contains `default-src 'self'` and no `unsafe-inline`

**NO EXTERNAL ASSETS (2 tests)**
- No `http://`, `https://`, `//` external URL in served HTML
- No inline event handlers in served HTML

**ENDPOINT INTEGRITY (3 tests)**
- `GET /control/state` without auth -> 401
- `GET /control/state` with auth -> 200
- No write routes at `/control/state`

**LOGOUT AND CSRF (2 tests)**
- Logout form exists with CSRF token
- POST /logout works (303 to /login)

**POLLING AND STATE MACHINE (6 tests)**
- dashboard.js contains `fetch(`
- dashboard.js contains `AbortController`
- dashboard.js contains `visibilitychange`
- dashboard.js contains `setTimeout`
- dashboard.js does NOT contain `setInterval`
- dashboard.js contains `textContent`; no `innerHTML`

**CONNECTION STATES (2 tests)**
- dashboard.js contains five state strings
- CSS defines `.is-connecting`, `.is-online`, `.is-stale`, `.is-degraded`, `.is-offline`

**ACCESSIBILITY (3 tests)**
- Viewport meta tag present
- `<header>` landmark present
- `<h1>` heading present

### Timer and fetch testability

No real 15-second or 60-second waits. Static analysis verifies state machine
structure and polling primitives. Real HTTP tests cover auth, asset serving,
CSP, endpoint integrity, and logout.

---

## Existing Regression Suite

All existing validation scripts must pass unchanged:

```bash
bash scripts/check-web-ui-read-only-endpoint.sh
bash scripts/check-web-host-startup.sh
bash scripts/check-web-control-state-provider.sh
bash scripts/check-runtime-read-only-web-host.sh
bash scripts/check-web-authentication.sh
python3 -m compileall -q .
git diff --check
```

---

## CI Integration

New step in `.github/workflows/validate.yml` after the auth check:

```yaml
      - name: Web dashboard check
        run: bash scripts/check-web-dashboard.sh
```

No other CI changes.

---

## Deployment Boundary

- No credential or Secret changes.
- No GitOps changes (`app/docker/` manifests unchanged).
- Existing image publication flow (Docker Hub -> ArgoCD) unchanged.
- Production already exposes the full host.
- Rollback is application-image rollback only.
- No database or persistent-state migration.

---

## Risks and Mitigations

| Risk | Impact | Mitigation | Validation |
|------|--------|------------|------------|
| Modified/unverified vendor CSS | Unexpected behaviour | Download from official release; SHA-256 | Hash check in validation; review |
| Missing license | Compliance gap | Include LICENSE in VC | File existence check |
| Accidental CDN dep | CSP violation | Forbidden in contract; grep | Validation checks external URLs |
| Unrestricted static exposure | Security regression | RestrictedStaticFiles unchanged | Path traversal test (404) |
| CSP weakening | XSS risk | CSP unchanged | CSP header verification |
| Conflicting CSS | Visual defects | Loading order; no !important | Visual review |
| Mobile overflow | Broken layout | overflow-x: auto | Viewport meta check |
| Overlapping polling | Race conditions | AbortController pattern | Static analysis |
| Request storms | Server load | Bounded backoff; hidden-tab | Delay constants in code |
| False runtime-health claims | Wrong operator action | Stale/offline visual markers | State machine analysis |
| Stale data appearing current | Wrong operator action | Badge + timestamp visible | Rendered HTML review |
| Session expiry | Data visible | 401 -> redirect to /login | HTTP 401 integration test |
| Sensitive rendering | Data leak | textContent only; no innerHTML | Grep in dashboard.js |
| Login/logout regressions | Cannot auth | Auth validation scripts unchanged | Full regression suite |
| Accidental device controls | Safety violation | No write routes | Write-route density check |

---

## Rollback

Rollback is an application-image rollback only:
1. Revert to previous known-good Docker image tag/digest.
2. GitOps (ArgoCD) points to previous image.
3. No database rollback, Secret change, or config change.

---

## Acceptance Criteria

1. All PR 0033 acceptance criteria remain satisfied.
2. All existing regression scripts exit 0.
3. `scripts/check-web-dashboard.sh` exits 0.
4. `python3 -m compileall -q .` exits 0.
5. `git diff --check` exits 0.
6. Only files in "Exact File Plan" are created or modified.
7. `run.py`, `Dockerfile`, `docker-compose.yml`, `app/docker/`,
   `app/control/`, `app/web_auth.py`, `app/web_routes.py` unchanged.
8. Bulma 1.0.4 is vendored locally with verified SHA-256.
9. No CDN or external asset URL in served pages.
10. CSP not weakened.
11. No write API endpoints.
12. No PR 0035 or PR 0036 fields displayed.
13. `ROADMAP.md` unchanged; only PR 0034 PLAN.md changed in `.project-memory/`.
14. `CODER_REPORT.txt` records Bulma SHA-256 and version.

---

## Explicit Non-Goals

- No PR 0035 runtime-heartbeat fields.
- No PR 0036 device-duration or energy fields.
- No inverter observability (PR 0035).
- No water/ambient temperature (PR 0036).
- No write controls or command buttons.
- No WebSockets, SSE, or long polling.
- No CDN, npm, Node, Sass, Vite, Webpack.
- No Bulma JavaScript.
- No theme toggle (OS preference only).
- No localStorage or sessionStorage for API data.
- No custom fonts, icons, or external resources.
- No real-time clock beyond polling timestamps.
- No page auto-refresh.
- No GitOps, Kubernetes, or deployment changes.
- No changes to `run.py` or entrypoint.
- No changes to auth, session, CSRF, or throttling.
- No new Python dependencies.

---

## Coder Constraints

1. Do not modify `app/control/`, `app/web_auth.py`, `app/web_routes.py`,
   `app/web_control_state_provider.py`, `app/web_runtime_integration.py`,
   `app/web_host_startup.py`, `run.py`.
2. Do not add `unsafe-inline`, nonces, hashes, or external origins to CSP.
3. Do not use CDN or external asset URLs.
4. Do not use npm, Node, Sass, Vite, Webpack, or any build step.
5. Do not modify or minify the upstream Bulma artifact.
6. Do not add Bulma JavaScript or any third-party JavaScript.
7. Do not use `innerHTML` for API values.
8. Do not use `eval()`, `Function()`, or inline event handlers.
9. Do not add POST, PUT, PATCH, or DELETE routes.
10. Do not call Tuya, relays, devices, hardware, or runtime internals.
11. Do not expose raw logs, provider payloads, secrets, or stack traces.
12. Do not add PR 0035 or PR 0036 fields.
13. Do not commit credentials, secrets, tokens, or hashes.
14. Do not modify `ROADMAP.md` or deployment files.

---

## Reviewer Checklist

- [ ] Only `PLAN.md` is changed (no application files)
- [ ] Bulma 1.0.4 pinned; exact vendor paths specified
- [ ] No CDN, npm, Node, Sass, or build tools
- [ ] No Bulma JavaScript
- [ ] Restricted static allowlist preserved
- [ ] CSP preserved without unsafe-inline or external origins
- [ ] Vendor provenance and SHA-256 verification required
- [ ] Exact implementation files listed and minimal
- [ ] Only current API fields mapped
- [ ] All five connection states defined
- [ ] One request in flight guaranteed
- [ ] Timeout, cancellation, backoff, visibility handling defined
- [ ] Communication status separated from runtime health
- [ ] Deterministic behavioural validation planned
- [ ] No application implementation in plan
- [ ] ROADMAP.md and all application files unchanged
