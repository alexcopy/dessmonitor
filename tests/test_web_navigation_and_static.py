import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from app import web_routes
from app.web_host import _STATIC_ALLOWLIST, create_app


AUTH_ENV = {
    "WEB_AUTH_USERNAME": "synthetic-operator",
    "WEB_AUTH_PASSWORD_HASH": "not-used-by-this-test",
    "WEB_AUTH_SESSION_SECRET": "s" * 64,
    "WEB_AUTH_TEST_HTTP": "true",
}


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_links = False
        self.in_anchor = False
        self.current = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("id") == "navLinks":
            self.in_links = True
        elif self.in_links and tag == "a":
            self.in_anchor = True
            self.current = {"href": attrs.get("href"), "class": attrs.get("class", ""),
                            "aria-current": attrs.get("aria-current"), "text": ""}

    def handle_data(self, data):
        if self.in_anchor and self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.in_anchor:
            self.links.append(self.current)
            self.current = None
            self.in_anchor = False
        elif tag == "div" and self.in_links:
            self.in_links = False


async def asgi_get(app, path):
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": [], "extensions": {"http.response.pathsend": {}},
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    messages = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.pathsend":
            messages.append({"type": "http.response.body", "body": Path(message["path"]).read_bytes(),
                             "more_body": False})
            return
        messages.append(message)

    await app(scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    headers = {key.decode().lower(): value.decode() for key, value in start.get("headers", [])}
    body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return start["status"], headers, body


class NavigationAndStaticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = create_app(environ=AUTH_ENV)

    async def test_log_javascript_is_allowlisted_with_javascript_mime_and_unknown_files_stay_blocked(self):
        self.assertIn("adaptive_decisions.js", _STATIC_ALLOWLIST)
        status, headers, body = await asgi_get(self.app, "/static/adaptive_decisions.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/javascript"), headers["content-type"])
        self.assertGreater(int(headers.get("content-length", "0")), 0)
        self.assertIn(b"decision-export", body)
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")

        for path in ("/static/not-allowlisted.js", "/static/adaptive_decisions.js.map"):
            unlisted_status, _, _ = await asgi_get(self.app, path)
            self.assertEqual(unlisted_status, 404)
        nav_status, nav_headers, _ = await asgi_get(self.app, "/static/global_nav.js")
        self.assertEqual(nav_status, 200)
        self.assertTrue(nav_headers["content-type"].startswith("text/javascript"))

    async def test_log_is_authenticated_and_global_navigation_is_consistent(self):
        paths = ("/", "/analytics", "/alarms", "/controls", "/controls/decisions",
                 "/settings", "/settings/devices")
        expected_active = {
            "/": "/", "/analytics": "/analytics", "/alarms": "/alarms",
            "/controls": "/controls", "/controls/decisions": "/controls/decisions",
            "/settings": "/settings", "/settings/devices": "/settings",
        }
        expected_order = ["/", "/analytics", "/alarms", "/controls", "/controls/decisions", "/settings"]
        with patch.object(web_routes, "_check_auth", return_value=(True, "synthetic-operator")):
            for path in paths:
                with self.subTest(path=path):
                    status, headers, body = await asgi_get(self.app, path)
                    self.assertEqual(status, 200)
                    parser = NavigationParser()
                    parser.feed(body.decode())
                    self.assertEqual([item["href"] for item in parser.links], expected_order)
                    active = [item for item in parser.links if "active" in item["class"].split()]
                    self.assertEqual([item["href"] for item in active], [expected_active[path]])
                    self.assertIn("Log", next(item["text"] for item in parser.links
                                               if item["href"] == "/controls/decisions"))
                    self.assertIn("Sign Out", body.decode())
                    self.assertIn(b'name="csrf_token"', body)
                    self.assertIn("/static/global_nav.js", body.decode())
                    self.assertIn("default-src 'self'", headers.get("content-security-policy", ""))
                    if path == "/controls/decisions":
                        self.assertIn("DRY-RUN — NO HARDWARE WRITE", body.decode())
                        self.assertEqual(active[0]["aria-current"], "page")

        with patch.object(web_routes, "_check_auth", return_value=(False, None)):
            status, headers, _ = await asgi_get(self.app, "/controls/decisions")
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()
