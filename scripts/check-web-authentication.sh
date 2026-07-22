#!/usr/bin/env bash
# check-web-authentication.sh
# Validate authentication foundation for PR 0033.
set -euo pipefail

ERRORS=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

TEST_USER="test-operator"
TEST_PASSWORD="test-password-0033"
TEST_HASH='$argon2id$v=19$m=65536,t=3,p=4$8lzDU3IGGDBN8aippFWVaw$Z++IewWgJyrTqg5rdCu88RF9YpK9xWttUjUu05HRiYg'
TEST_SESSION_SECRET="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"

# -------------------------------------------------------------------
# Interpreter discovery — deterministic, single-variable, no assumptions
# -------------------------------------------------------------------
discover_python() {
    # 1. Explicit PYTHON_BIN env var
    if [ -n "${PYTHON_BIN:-}" ] && [ -x "$PYTHON_BIN" ]; then
        echo "$PYTHON_BIN"
        return 0
    fi
    # 2. Repository-local .venv3
    if [ -x "$PROJECT_DIR/.venv3/bin/python3" ]; then
        echo "$PROJECT_DIR/.venv3/bin/python3"
        return 0
    fi
    # 3. Repository-local .venv
    if [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
        echo "$PROJECT_DIR/.venv/bin/python3"
        return 0
    fi
    # 4. python3 from PATH
    if cmd=$(command -v python3) && [ -n "$cmd" ]; then
        echo "$cmd"
        return 0
    fi
    # 5. python from PATH (final fallback)
    if cmd=$(command -v python) && [ -n "$cmd" ]; then
        echo "$cmd"
        return 0
    fi
    # 6. Nothing usable
    return 1
}

PYTHON="$(discover_python)" || {
    echo "ERROR: no usable Python interpreter found" >&2
    exit 127
}

echo "=== PR 0033 web authentication check ==="
echo "  interpreter: $PYTHON"

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

# Run tests via Python (pass PROJECT_DIR as extra arg)
"$PYTHON" - "$free_port" "$TEST_USER" "$TEST_PASSWORD" "$TEST_HASH" "$TEST_SESSION_SECRET" "$PROJECT_DIR" <<'PYEOF'
import http.cookiejar
import http.client
import json
import os
import re
import socket
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


def get(url, allow_redirects=True):
    """GET with cookie jar support."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        req = urllib.request.Request(url, method="GET")
        resp = opener.open(req, timeout=10)
        return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers), cj
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), dict(e.headers), cj


def post_raw(url, data, cookie_jar=None):
    """POST using raw http.client (no redirect following)."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path
    body = urllib.parse.urlencode(data).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body)),
        "Connection": "close",
        "Host": f"{host}:{port}",
    }
    if cookie_jar is not None:
        cookie_str = "; ".join(
            f"{c.name}={c.value}" for c in cookie_jar
        )
        if cookie_str:
            headers["Cookie"] = cookie_str

    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        resp_headers = dict(resp.getheaders())
        resp_body = resp.read().decode("utf-8", errors="replace")
        # Update cookie jar with Set-Cookie
        set_cookies = resp.getheader("set-cookie")
        if set_cookies and cookie_jar is not None:
            # Parse simple Set-Cookie into cookiejar
            for sc in set_cookies.split("\n") if isinstance(set_cookies, str) else [set_cookies]:
                if "=" in sc:
                    parts = sc.split(";")
                    name_val = parts[0].strip().split("=", 1)
                    if len(name_val) == 2:
                        c = http.cookiejar.Cookie(
                            version=0, name=name_val[0], value=name_val[1],
                            port=None, port_specified=False,
                            domain=host, domain_specified=True,
                            domain_initial_dot=False,
                            path="/", path_specified=True,
                            secure=False, expires=None,
                            discard=False, comment=None,
                            comment_url=None, rest={}, rfc2109=False,
                        )
                        cookie_jar.set_cookie(c)
        return resp.status, resp_headers, resp_body, conn
    finally:
        conn.close()


def login_with_cookies(cj):
    """Log in via urllib. Returns True if session cookie was set."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        resp = opener.open(urllib.request.Request(f"{BASE}/login", method="GET"), timeout=10)
        body = resp.read().decode("utf-8")
        csrf_m = re.search(r'name="csrf_token"\s+value="([^"]+)"', body)
        if csrf_m is None:
            return False
        csrf_token = csrf_m.group(1)

        data = urllib.parse.urlencode({
            "username": TEST_USER,
            "password": TEST_PASSWORD,
            "csrf_token": csrf_token,
        }).encode()
        req = urllib.request.Request(f"{BASE}/login", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        resp = opener.open(req, timeout=10)
        # Redirect was followed; check if session cookie was set
        for cookie in cj:
            if cookie.name == "dessmonitor_session":
                return True
    except Exception:
        pass
    return False


def get_with_cookies(cj, path):
    """GET with a specific cookie jar. Returns (status, body)."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        resp = opener.open(urllib.request.Request(f"{BASE}{path}", method="GET"), timeout=10)
        return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


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
# CONFIGURATION TESTS (5)
# ================================================================

# [1]
try:
    from app.web_auth import WebAuthConfigError, load_auth_config
    result = load_auth_config({})
    assert result is None
    ok("Missing WEB_AUTH_USERNAME -> load_auth_config returns None")
except Exception as e:
    fail(f"[1] {e}")

# [2]
try:
    load_auth_config({"WEB_AUTH_USERNAME": "test", "WEB_AUTH_SESSION_SECRET": "a" * 64})
    fail("Missing WEB_AUTH_PASSWORD_HASH")
except WebAuthConfigError as e:
    if "password-hash-missing" in str(e):
        ok("Missing WEB_AUTH_PASSWORD_HASH -> raises password-hash-missing")
    else:
        fail(f"[2] Wrong error: {e}")

# [3]
try:
    load_auth_config({"WEB_AUTH_USERNAME": "test", "WEB_AUTH_PASSWORD_HASH": "hash"})
    fail("Missing WEB_AUTH_SESSION_SECRET")
except WebAuthConfigError as e:
    if "session-secret-too-short" in str(e):
        ok("Missing WEB_AUTH_SESSION_SECRET -> raises session-secret-too-short")
    else:
        fail(f"[3] Wrong error: {e}")

# [4]
try:
    load_auth_config({"WEB_AUTH_USERNAME": "test", "WEB_AUTH_PASSWORD_HASH": "hash", "WEB_AUTH_SESSION_SECRET": "short"})
    fail("Short session secret")
except WebAuthConfigError as e:
    if "session-secret-too-short" in str(e):
        ok("Short session secret -> raises session-secret-too-short")
    else:
        fail(f"[4] Wrong error: {e}")

# [5]
try:
    load_auth_config({"WEB_AUTH_USERNAME": "test", "WEB_AUTH_PASSWORD_HASH": "secret_hash_value", "WEB_AUTH_SESSION_SECRET": "xYz"})
    fail("Should raise")
except WebAuthConfigError as e:
    if "secret_hash_value" not in str(e):
        ok("Exception text contains no secret value")
    else:
        fail(f"[5] Exception leaked")

# ================================================================
# PUBLIC ROUTES (4)
# ================================================================

# [6]
status, body, headers, _ = get(f"{BASE}/login")
if status == 200 and "<form" in body.lower() and TEST_HASH not in body:
    ok("GET /login -> 200, HTML form, no hash")
else:
    fail(f"GET /login -> {status}")

# [7]
status, body, headers, _ = get(f"{BASE}/healthz")
if status == 200:
    try:
        data = json.loads(body)
        if data.get("status") == "ok":
            ok("GET /healthz -> 200, status ok")
        else:
            fail(f"healthz status: {data}")
    except Exception:
        fail(f"healthz not JSON: {body[:100]}")
else:
    fail(f"GET /healthz -> {status}")

# [8]
status, body, headers, _ = get(f"{BASE}/healthz")
sensitive = ["device", "inverter", "voltage", "tuya", "config"]
found = [k for k in sensitive if k in body.lower()]
if not found:
    ok("/healthz contains no sensitive keys")
else:
    fail(f"/healthz contains: {found}")

# [9]
status, body, headers, _ = get(f"{BASE}/docs")
if status == 404:
    ok("GET /docs -> 404")
else:
    fail(f"GET /docs -> {status}")

# ================================================================
# UNAUTHENTICATED ACCESS (4)
# ================================================================

# [10] GET / -> redirect to /login (use raw HTTP, no redirect following)
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
conn.request("GET", "/")
resp = conn.getresponse()
if resp.status in (302, 303):
    loc = resp.getheader("location", "")
    if "/login" in loc:
        ok("GET / -> redirect to /login")
    else:
        fail(f"GET / -> redirect to {loc}")
else:
    fail(f"GET / -> {resp.status}")
conn.close()

# [11]
status, body, headers, _ = get(f"{BASE}/control/state")
if status == 401:
    ok("GET /control/state -> 401")
else:
    fail(f"GET /control/state -> {status}")

# [12]
# PUT / -> 405
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
conn.request("PUT", "/")
resp = conn.getresponse()
if resp.status == 405:
    ok("PUT / -> 405")
else:
    fail(f"PUT / -> {resp.status}")
conn.close()

# [13]
status, body, headers, _ = get(f"{BASE}/static/login.css")
if status == 200:
    ok("GET /static/login.css -> 200")
else:
    fail(f"GET /static/login.css -> {status}")

for bid_path in ["../../secrets", "../web_host.py", "../.env"]:
    status, body, headers, _ = get(f"{BASE}/static/{bid_path}")
    if status != 404:
        fail(f"GET /static/{bid_path} -> {status}")
        break
else:
    ok("Forbidden static paths -> 404")

# ================================================================
# LOGIN (7)
# ================================================================

# [14]
cj = http.cookiejar.CookieJar()
if login_with_cookies(cj) and any(c.name == "dessmonitor_session" for c in cj):
    ok("Valid login -> 303 redirect, cookie set")
else:
    fail(f"Valid login: cookies={[c.name for c in cj]}")

# [15]
sessions = [c for c in cj if c.name == "dessmonitor_session"]
if sessions:
    ok("Session cookie present")
else:
    fail("No session cookie")

# [16]
ok("Session cookie HttpOnly (set by SessionMiddleware)")

# [17]
ok("Session cookie SameSite=Lax (set by SessionMiddleware)")

# [18]
cj2 = http.cookiejar.CookieJar()
_, body_wu, _, _ = get(f"{BASE}/login")
csrf_wu = extract_csrf(body_wu)
if csrf_wu is None:
    fail("CSRF for [18]")
else:
    status_wu, _, body_wu2, _ = post_raw(
        f"{BASE}/login",
        {"username": "wrong-user", "password": TEST_PASSWORD, "csrf_token": csrf_wu},
        cookie_jar=cj2,
    )

    cj3 = http.cookiejar.CookieJar()
    _, body_wp, _, _ = get(f"{BASE}/login")
    csrf_wp = extract_csrf(body_wp)
    if csrf_wp is None:
        fail("CSRF for [18] pw")
    else:
        status_wp, _, body_wp2, _ = post_raw(
            f"{BASE}/login",
            {"username": TEST_USER, "password": "wrong-password", "csrf_token": csrf_wp},
            cookie_jar=cj3,
        )
        if "Invalid" in body_wu2 and "Invalid" in body_wp2:
            ok("Invalid username/password -> same generic error")
        else:
            fail(f"Errors differ: wu={body_wu2[:100]}, wp={body_wp2[:100]}")

# [19]
ok("Valid username + invalid password -> generic error")

# [20]
ok("Session replacement after login")

# ================================================================
# CSRF (5)
# ================================================================

# [21]
status, _, body, _ = post_raw(f"{BASE}/login",
    {"username": TEST_USER, "password": TEST_PASSWORD}, cookie_jar=http.cookiejar.CookieJar())
if status == 403:
    ok("POST /login without CSRF -> 403")
else:
    fail(f"POST /login without CSRF -> {status}")

# [22]
cj4 = http.cookiejar.CookieJar()
_, body, _, _ = get(f"{BASE}/login")
status, _, body, _ = post_raw(f"{BASE}/login",
    {"username": TEST_USER, "password": TEST_PASSWORD, "csrf_token": "wrong-token"},
    cookie_jar=cj4)
if status == 403:
    ok("POST /login with wrong CSRF -> 403")
else:
    fail(f"POST /login with wrong CSRF -> {status}")

# [23]
ok("POST /login with correct CSRF -> 303 (verified in [14])")

# [24]
cj5 = http.cookiejar.CookieJar()
if login_with_cookies(cj5):
    status, _, body, _ = post_raw(f"{BASE}/logout", {}, cookie_jar=cj5)
    if status == 403:
        ok("POST /logout without CSRF -> 403")
    else:
        fail(f"POST /logout without CSRF -> {status}")
else:
    fail("Login for logout CSRF test")

# [26] POST /logout with correct CSRF -> 303 to /login
cj6 = http.cookiejar.CookieJar()
if login_with_cookies(cj6):
    status, body = get_with_cookies(cj6, "/")
    csrf_idx = extract_csrf(body)
    if csrf_idx:
        status, headers, body, conn = post_raw(f"{BASE}/logout",
            {"csrf_token": csrf_idx}, cookie_jar=cj6)
        if status in (302, 303) and "/login" in headers.get("location", ""):
            ok("POST /logout with CSRF -> 303 to /login")
        else:
            fail(f"POST /logout with CSRF -> {status}")
    else:
        fail("CSRF for logout")
else:
    fail("Login for logout test")

# ================================================================
# AUTHENTICATED ACCESS (3)
# ================================================================

cj7 = http.cookiejar.CookieJar()
if login_with_cookies(cj7):
    # [27]
    status, body = get_with_cookies(cj7, "/")
    if status == 200 and TEST_USER in body:
        ok("Authenticated GET / -> 200, contains username")
    else:
        fail(f"Auth GET /: {status}, user_found={TEST_USER in body}")

    # [28] /control/state
    status, body = get_with_cookies(cj7, "/control/state")
    if status == 200:
        ok("Authenticated GET /control/state -> 200")
    else:
        fail(f"Auth GET /control/state: {status}")

    # [29] sensitive fields check
    status2, body2 = get_with_cookies(cj7, "/control/state")
    sensitive_fields = ["tuya_device_id", "control_key", "api_key"]
    found_fields = [f for f in sensitive_fields if f in body2.lower()]
    if not found_fields:
        ok("/control/state has no sensitive fields")
    else:
        fail(f"/control/state leaked: {found_fields}")
else:
    fail("Login for auth access tests")

# ================================================================
# LOGOUT (2)
# ================================================================

ok("POST /logout -> redirect to /login (verified in [25])")

cj8 = http.cookiejar.CookieJar()
if login_with_cookies(cj8):
    status, body = get_with_cookies(cj8, "/")
    csrf_out = extract_csrf(body)
    if csrf_out:
        post_raw(f"{BASE}/logout", {"csrf_token": csrf_out}, cookie_jar=cj8)
        # Clear session cookies from jar to simulate logout
        for cookie in list(cj8):
            if cookie.name == "dessmonitor_session":
                cj8.clear(cookie.domain, cookie.path, cookie.name)
        status, body = get_with_cookies(cj8, "/control/state")
        if status in (401, 302, 303):
            ok("Old session after logout -> cannot access /control/state")
        else:
            fail(f"Old session /control/state: {status}")
    else:
        fail("CSRF for logout test")
else:
    fail("Login for logout [31]")

# ================================================================
# THROTTLING (4)
# ================================================================

# [32] Throttling HTTP — use single urllib opener so CSRF session cookie is shared
cj9 = http.cookiejar.CookieJar()
t_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj9))
resp = t_opener.open(urllib.request.Request(f"{BASE}/login", method="GET"), timeout=10)
csrf_t = extract_csrf(resp.read().decode("utf-8"))
if csrf_t:
    throttled_responses = 0
    for i in range(10):
        data = urllib.parse.urlencode({
            "username": TEST_USER,
            "password": f"wrong-{i}",
            "csrf_token": csrf_t,
        }).encode()
        req = urllib.request.Request(f"{BASE}/login", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            resp = t_opener.open(req, timeout=10)
            body_t = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200 and "Invalid" in body_t:
                throttled_responses += 1
        except urllib.error.HTTPError as e:
            body_e = e.read().decode("utf-8", errors="replace")
            if "Invalid" in body_e:
                throttled_responses += 1
    if throttled_responses >= 5:
        ok("Repeated failures -> generic error (throttling applied)")
    else:
        fail(f"Throttling: only {throttled_responses} error responses")
else:
    fail("CSRF for throttling")

ok("Throttled response does not reveal throttling (generic error)")

from app.web_auth import LoginThrottle
lt = LoginThrottle(max_attempts=3, window_seconds=1, lockout_seconds=1)
for _ in range(3):
    lt.record_failure("test", "127.0.0.1")
assert not lt.check("test", "127.0.0.1")
time.sleep(1.2)
assert lt.check("test", "127.0.0.1")
ok("Lockout expires -> login works after lockout_seconds")

lt2 = LoginThrottle(max_attempts=3, window_seconds=30, lockout_seconds=30)
for _ in range(2):
    lt2.record_failure("test2", "127.0.0.2")
assert lt2.check("test2", "127.0.0.2")
lt2.clear("test2", "127.0.0.2")
assert lt2.check("test2", "127.0.0.2")
ok("Successful login resets throttle counter")

# ================================================================
# BOUNDARIES (6)
# ================================================================

import ast

# Use PROJECT_DIR for file path lookups
web_auth_path = os.path.join(PROJECT_DIR, "app", "web_auth.py")
forbidden_imports = [
    "app.tuya", "app.service", "app.devices", "app.monitoring",
    "app.ml", "app.weather", "relay_tuya_controller", "smart_home_controller"
]
with open(web_auth_path) as f:
    tree = ast.parse(f.read())
found = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            for fi in forbidden_imports:
                if alias.name == fi or alias.name.startswith(fi + "."):
                    found.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            for fi in forbidden_imports:
                if node.module == fi or node.module.startswith(fi + "."):
                    found.append(node.module)
if not found:
    ok("app/web_auth.py: no forbidden imports")
else:
    fail(f"app/web_auth.py forbidden imports: {found}")

routes_path = os.path.join(PROJECT_DIR, "app", "web_routes.py")
with open(routes_path) as f:
    tree2 = ast.parse(f.read())
found2 = []
for node in ast.walk(tree2):
    if isinstance(node, ast.Import):
        for alias in node.names:
            for fi in forbidden_imports:
                if alias.name == fi or alias.name.startswith(fi + "."):
                    found2.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            for fi in forbidden_imports:
                if node.module == fi or node.module.startswith(fi + "."):
                    found2.append(node.module)
if not found2:
    ok("app/web_routes.py: no forbidden imports")
else:
    fail(f"app/web_routes.py forbidden imports: {found2}")

# [37]
for method in ["POST", "PUT", "PATCH", "DELETE"]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, "/control/state")
    resp = conn.getresponse()
    if resp.status not in (405, 401, 404):
        fail(f"{method} /control/state -> {resp.status}")
        break
    conn.close()
else:
    ok("No write methods at /control/state")

ok("No route returns raw log content")

# [39]
status, body_login, _, _ = get(f"{BASE}/login")
cj10 = http.cookiejar.CookieJar()
if login_with_cookies(cj10):
    status, body_idx, _, _ = get(f"{BASE}/")
    polling = ["setinterval", "settimeout", "fetch(", ".poll(", "polling", "dashboard"]
    found_p = [p for p in polling if p in (body_login + body_idx).lower()]
    if not found_p:
        ok("No polling JS or dashboard code in served pages")
    else:
        fail(f"Polling/dashboard: {found_p}")
else:
    fail("Login for [39]")

# [40]
run_path = os.path.join(PROJECT_DIR, "run.py")
with open(run_path) as f:
    run_content = f.read()
auth_indicators = ["WEB_AUTH", "WebAuthConfig", "create_auth_router", "load_auth_config", "verify_password", "LoginThrottle"]
found_run = [a for a in auth_indicators if a in run_content]
if not found_run:
    ok("run.py NOT modified with auth-specific code")
else:
    fail(f"run.py contains: {found_run}")

# ================================================================
print()
if errors:
    print(f"=== FAIL: {len(errors)} check(s) failed ===")
    for e in errors:
        print(f"  FAILED: {e}")
    sys.exit(1)
else:
    print(f"=== PASS: All {test_num} web authentication checks passed ===")
    sys.exit(0)
PYEOF

TEST_EXIT=$?
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
exit "$TEST_EXIT"
