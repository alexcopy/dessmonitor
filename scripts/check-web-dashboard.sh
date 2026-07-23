#!/usr/bin/env bash
# check-web-dashboard.sh
# Validate PR 0034 live read-only dashboard shell.
set -euo pipefail

ERRORS=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ---------------------------------------------------------------------------
# Python interpreter discovery
# ---------------------------------------------------------------------------
if [ -n "${PYTHON_BIN:-}" ]; then
    if [ -x "$PYTHON_BIN" ]; then
        PYTHON="$PYTHON_BIN"
    else
        echo "ERROR: PYTHON_BIN is set but not executable: $PYTHON_BIN" >&2
        exit 127
    fi
elif [ -x "$PROJECT_DIR/.venv3/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv3/bin/python3"
elif [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
else
    echo "ERROR: No executable Python interpreter found" >&2
    exit 127
fi

echo "Using Python interpreter: $PYTHON"

# ---------------------------------------------------------------------------
# Pinned Bulma 1.0.4 SHA-256
# ---------------------------------------------------------------------------
EXPECTED_BULMA_SHA256="67fa26df1ca9e95d8f2adc7c04fa1b15fa3d24257470ebc10cc68b9aab914bee"

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------
TEST_USER="test-operator"
TEST_PASSWORD="test-password-0033"
TEST_HASH='$argon2id$v=19$m=65536,t=3,p=4$8lzDU3IGGDBN8aippFWVaw$Z++IewWgJyrTqg5rdCu88RF9YpK9xWttUjUu05HRiYg'
TEST_SESSION_SECRET="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"

echo "=== PR 0034 dashboard validation ==="

find_free_port() {
    "$PYTHON" -c "
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(('127.0.0.1', 0))
    print(s.getsockname()[1])
"
}

free_port=$(find_free_port)
echo ""
echo "Starting test server on 127.0.0.1:${free_port}..."

WEB_AUTH_USERNAME="$TEST_USER" \
WEB_AUTH_PASSWORD_HASH="$TEST_HASH" \
WEB_AUTH_SESSION_SECRET="$TEST_SESSION_SECRET" \
WEB_AUTH_TEST_HTTP=1 "$PYTHON" -c "
import asyncio
from app.web_host import create_app
import uvicorn
app = create_app()
config = uvicorn.Config(app, host='127.0.0.1', port=$free_port, log_level='error')
config.reload = False; config.workers = 1
server = uvicorn.Server(config=config)
asyncio.run(server.serve())
" &
SERVER_PID=$!
sleep 2

# Run tests via Python
"$PYTHON" - "$free_port" "$TEST_USER" "$TEST_PASSWORD" "$TEST_HASH" "$TEST_SESSION_SECRET" "$PROJECT_DIR" "$EXPECTED_BULMA_SHA256" <<'PYEOF'
import hashlib
import http.cookiejar
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

port = int(sys.argv[1])
TEST_USER = sys.argv[2]
TEST_PASSWORD = sys.argv[3]
TEST_HASH = sys.argv[4]
TEST_SESSION_SECRET = sys.argv[5]
PROJECT_DIR = sys.argv[6]
EXPECTED_BULMA_SHA256 = sys.argv[7]

BASE = f"http://127.0.0.1:{port}"
errors = []
test_num = 0

def ok(msg=""):
    global test_num
    test_num += 1
    print(f"  [{test_num}] {msg} ... OK")
    return True

def fail(msg=""):
    global test_num, errors
    test_num += 1
    print(f"  [{test_num}] {msg} ... FAIL")
    errors.append(f"  [{test_num}] {msg}")
    return False

def get(url):
    """GET with cookie jar support."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        req = urllib.request.Request(url, method="GET")
        resp = opener.open(req, timeout=10)
        body = resp.read()
        return resp.status, body, dict(resp.headers), cj
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers), cj

def login(cj):
    """Log in via urllib. Returns True on success."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        resp = opener.open(urllib.request.Request(f"{BASE}/login", method="GET"), timeout=10)
        body = resp.read().decode("utf-8")
        csrf_m = re.search(r'name="csrf_token"\s+value="([^"]+)"', body)
        if csrf_m is None:
            return False
        csrf_token = csrf_m.group(1)
        data = urllib.parse.urlencode({
            "username": TEST_USER, "password": TEST_PASSWORD, "csrf_token": csrf_token,
        }).encode()
        req = urllib.request.Request(f"{BASE}/login", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        resp = opener.open(req, timeout=10)
        for cookie in cj:
            if cookie.name == "dessmonitor_session":
                return True
    except Exception:
        pass
    return False

def get_with_cookies(cj, path, raw=False):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        resp = opener.open(urllib.request.Request(f"{BASE}{path}", method="GET"), timeout=10)
        body = resp.read()
        return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)

def post_raw(url, data, cookie_jar=None):
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port_num = parsed.port or 80
    path = parsed.path
    body = urllib.parse.urlencode(data).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body)),
        "Connection": "close",
        "Host": f"{host}:{port_num}",
    }
    if cookie_jar is not None:
        cookie_str = "; ".join(f"{c.name}={c.value}" for c in cookie_jar)
        if cookie_str:
            headers["Cookie"] = cookie_str
    conn = http.client.HTTPConnection(host, port_num, timeout=10)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        resp_headers = dict(resp.getheaders())
        resp_body = resp.read().decode("utf-8", errors="replace")
        return resp.status, resp_headers, resp_body
    finally:
        conn.close()

def extract_csrf(html):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    return m.group(1) if m else None

# Wait for server
deadline = time.time() + 10
while time.time() < deadline:
    try:
        status, _, _, _ = get(f"{BASE}/healthz")
        if status == 200:
            break
    except Exception:
        pass
    time.sleep(0.3)
else:
    print("FATAL: test server did not start")
    sys.exit(1)

# ================================================================
# AUTHENTICATION AND ACCESS (5 tests)
# ================================================================

# [1] Unauthenticated GET / -> 303 redirect to /login
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
conn.request("GET", "/")
resp = conn.getresponse()
if resp.status in (302, 303) and "/login" in (resp.getheader("location") or ""):
    ok("Unauthenticated GET / -> redirect to /login")
else:
    fail(f"Unauth GET / -> {resp.status}")
conn.close()

# [2] Authenticated GET / -> 200, contains Bulma class selectors
cj = http.cookiejar.CookieJar()
if login(cj):
    status, body, headers = get_with_cookies(cj, "/")
    html = body.decode("utf-8", errors="replace")
    # Save HTML headers for later CSP check
    dashboard_headers = headers
    if status == 200 and "navbar" in html:
        ok("Auth GET / -> 200, Bulma classes present")
    else:
        fail(f"Auth GET / -> {status}, has-navbar={'navbar' in html}")
else:
    fail("Login for [2]")

# [3] Authenticated GET / -> contains dashboard.js script tag
if 'src="/static/dashboard.js"' in html:
    ok("Auth GET / -> dashboard.js script tag present")
else:
    fail("dashboard.js script tag missing")

# [4] Authenticated GET / -> contains Bulma CSS link
if 'bulma.min.css' in html:
    ok("Auth GET / -> Bulma CSS link present")
else:
    fail("Bulma CSS link missing")

# [5] Authenticated GET / -> contains dashboard.css link
if 'dashboard.css' in html:
    ok("Auth GET / -> dashboard.css link present")
else:
    fail("dashboard.css link missing")

# ================================================================
# BULMA INTEGRITY (4 tests)
# ================================================================

# [6] Bulma CSS served with 200
status, body, headers = get_with_cookies(cj, "/static/vendor/bulma/1.0.4/bulma.min.css")
if status == 200:
    ok("GET /static/vendor/bulma/1.0.4/bulma.min.css -> 200")
else:
    fail(f"Bulma CSS -> {status}")

# [7] Content-Type is text/css
ct = headers.get("content-type") or headers.get("Content-Type") or ""
if "text/css" in ct:
    ok("Bulma CSS Content-Type is text/css")
else:
    fail(f"Bulma CSS Content-Type: {ct}")

# [8] SHA-256 matches pinned value
actual_sha256 = hashlib.sha256(body).hexdigest()
if actual_sha256 == EXPECTED_BULMA_SHA256:
    ok(f"Bulma SHA-256 matches: {actual_sha256[:16]}...")
else:
    fail(f"Bulma SHA-256 mismatch: expected {EXPECTED_BULMA_SHA256[:16]}..., got {actual_sha256[:16]}...")

# [9] LICENSE is NOT served (404)
status, _, _ = get_with_cookies(cj, "/static/vendor/bulma/1.0.4/LICENSE")
if status == 404:
    ok("Bulma LICENSE -> 404 (not served)")
else:
    fail(f"Bulma LICENSE -> {status}")

# ================================================================
# ALLOWLIST AND CSP (4 tests)
# ================================================================

# [10] dashboard.css served
status, _, _ = get_with_cookies(cj, "/static/dashboard.css")
if status == 200:
    ok("GET /static/dashboard.css -> 200")
else:
    fail(f"dashboard.css -> {status}")

# [11] dashboard.js served
status, _, headers_js = get_with_cookies(cj, "/static/dashboard.js")
if status == 200:
    ok("GET /static/dashboard.js -> 200")
else:
    fail(f"dashboard.js -> {status}")

# [12] Path traversal -> 404
for bad_path in ["../web_host.py", "../../secrets", "../.env"]:
    status, _, _ = get_with_cookies(cj, f"/static/{bad_path}")
    if status != 404:
        fail(f"GET /static/{bad_path} -> {status}")
        break
else:
    ok("Static path traversal -> 404")

# [13] CSP header presence (from HTML response, not CSS)
csp = dashboard_headers.get("content-security-policy") or dashboard_headers.get("Content-Security-Policy") or ""
if "default-src 'self'" in csp and "unsafe-inline" not in csp:
    ok("CSP: default-src 'self', no unsafe-inline")
else:
    fail(f"CSP check failed: {csp[:120]}")

# ================================================================
# NO EXTERNAL ASSETS (2 tests)
# ================================================================

# [14] No external URLs in served HTML
# All CSS/JS should be same-origin paths starting with /static/
external_patterns = [r'https?://', r'src="//', r'href="//']
found_external = False
for pat in external_patterns:
    if re.search(pat, html):
        found_external = True
        fail(f"External URL found: {pat}")
        break
if not found_external:
    ok("No external URLs in served HTML")

# [15] No inline event handlers
if not re.search(r'\bon\w+\s*=', html):
    ok("No inline event handlers in HTML")
else:
    fail("Inline event handler found")

# ================================================================
# ENDPOINT INTEGRITY (3 tests)
# ================================================================

# [16] /control/state without auth -> 401
conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
conn2.request("GET", "/control/state")
resp2 = conn2.getresponse()
if resp2.status == 401:
    ok("Unauthenticated /control/state -> 401")
else:
    fail(f"Unauth /control/state -> {resp2.status}")
conn2.close()

# [17] /control/state with auth -> 200
status_cs, body_cs, _ = get_with_cookies(cj, "/control/state")
if status_cs == 200:
    ok("Authenticated /control/state -> 200")
else:
    fail(f"Auth /control/state -> {status_cs}")

# [18] No write routes at /control/state
for method in ["POST", "PUT", "PATCH", "DELETE"]:
    conn3 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn3.request(method, "/control/state")
    resp3 = conn3.getresponse()
    if resp3.status not in (405, 401, 404):
        fail(f"{method} /control/state -> {resp3.status}")
        break
    conn3.close()
else:
    ok("No write methods at /control/state")

# ================================================================
# LOGOUT AND CSRF (2 tests)
# ================================================================

# [19] Logout form exists with CSRF token
if '<form method="post" action="/logout"' in html and 'csrf_token' in html:
    ok("Logout form with CSRF token present")
else:
    fail("Logout form/CSRF missing")

# [20] POST /logout works (303 to /login)
csrf_idx = extract_csrf(html)
if csrf_idx:
    status_lo, headers_lo, _ = post_raw(f"{BASE}/logout", {"csrf_token": csrf_idx}, cookie_jar=cj)
    if status_lo in (302, 303) and "/login" in headers_lo.get("location", ""):
        ok("POST /logout -> 303 to /login")
    else:
        fail(f"POST /logout -> {status_lo}")
else:
    fail("CSRF for logout test")

# ================================================================
# POLLING AND STATE MACHINE (6 tests) — static analysis of dashboard.js
# ================================================================

ds_path = os.path.join(PROJECT_DIR, "app", "web", "static", "dashboard.js")
with open(ds_path) as f:
    ds_content = f.read()

# [21] dashboard.js contains fetch(
if "fetch(" in ds_content:
    ok("dashboard.js contains fetch(")
else:
    fail("dashboard.js missing fetch(")

# [22] dashboard.js contains AbortController
if "AbortController" in ds_content:
    ok("dashboard.js contains AbortController")
else:
    fail("dashboard.js missing AbortController")

# [23] dashboard.js contains visibilitychange
if "visibilitychange" in ds_content:
    ok("dashboard.js contains visibilitychange")
else:
    fail("dashboard.js missing visibilitychange")

# [24] dashboard.js contains setTimeout
if "setTimeout" in ds_content:
    ok("dashboard.js contains setTimeout")
else:
    fail("dashboard.js missing setTimeout")

# [25] dashboard.js does NOT contain setInterval as function call
# (comment mentions are ok)
setinterval_lines = [
    ln for ln in ds_content.split("\n")
    if "setInterval" in ln and "no setInterval" not in ln
]
if not setinterval_lines:
    ok("dashboard.js does NOT use setInterval")
else:
    fail(f"dashboard.js uses setInterval: {setinterval_lines}")

# [26] textContent present, no innerHTML for API values
# innerHTML is allowed only with escaped text or hardcoded strings
# Check that textContent is used
if "textContent" in ds_content:
    ok("dashboard.js uses textContent")
else:
    fail("dashboard.js missing textContent")

# ================================================================
# CONNECTION STATES (2 tests)
# ================================================================

# [27] Five state strings
states = ["connecting", "online", "stale", "degraded", "offline"]
found_states = [s for s in states if s in ds_content.lower()]
if len(found_states) >= 5:
    ok(f"All 5 connection states present: {found_states}")
else:
    fail(f"Missing states: {set(states) - set(found_states)}")

# [28] CSS defines connection state classes
css_path = os.path.join(PROJECT_DIR, "app", "web", "static", "dashboard.css")
with open(css_path) as f:
    css_content = f.read()
css_states = ["is-connecting", "is-online", "is-stale", "is-degraded", "is-offline"]
found_css = [s for s in css_states if s in css_content]
if len(found_css) >= 5:
    ok(f"All 5 CSS state classes present: {found_css}")
else:
    fail(f"Missing CSS states: {set(css_states) - set(found_css)}")

# ================================================================
# ACCESSIBILITY (3 tests)
# ================================================================

# [29] Viewport meta tag
if 'name="viewport"' in html:
    ok("Viewport meta tag present")
else:
    fail("Viewport meta tag missing")

# [30] <header> landmark
if "<header" in html:
    ok("<header> landmark present")
else:
    fail("<header> landmark missing")

# [31] <h1> heading
if "<h1" in html:
    ok("<h1> heading present")
else:
    fail("<h1> heading missing")

# ================================================================
# ADDITIONAL CHECKS (no eval, no localStorage)
# ================================================================

# [32] No eval in dashboard.js
if "eval(" not in ds_content:
    ok("dashboard.js: no eval")
else:
    fail("dashboard.js uses eval")

# [33] No localStorage usage in dashboard.js (check for actual property access)
if re.search(r'\blocalStorage\.', ds_content):
    fail("dashboard.js uses localStorage")
else:
    ok("dashboard.js: no localStorage")

# ================================================================
# DASHBOARD STRUCTURE (3 tests)
# ================================================================

# [34] Username present in authenticated page
if TEST_USER in html:
    ok("Authenticated page contains username")
else:
    fail("Username not found in authenticated page")

# [35] Connection state badge present
if "connection-state-badge" in html or "is-connecting" in html:
    ok("Connection state badge present")
else:
    fail("Connection state badge missing")

# [36] Unavailable state element present
if "dashboard-unavailable" in html:
    ok("Unavailable state element present")
else:
    fail("Unavailable state missing")

# ================================================================
# Results
# ================================================================

print()
if errors:
    print(f"=== FAIL: {len(errors)} check(s) failed ===")
    for e in errors:
        print(f"  FAILED: {e}")
    sys.exit(1)
else:
    print(f"=== PASS: All {test_num} dashboard checks passed ===")
    sys.exit(0)
PYEOF

TEST_EXIT=$?
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
exit "$TEST_EXIT"
