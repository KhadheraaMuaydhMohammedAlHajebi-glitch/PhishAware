"""Security tests for M8 (NFR-08 to NFR-11)."""

from tests.helpers import AppTestCase


class SecurityTests(AppTestCase):
    def test_security_headers_are_sent(self):
        response = self.client.get("/consent")
        for header in ("Content-Security-Policy", "X-Content-Type-Options",
                       "X-Frame-Options", "Referrer-Policy"):
            self.assertIn(header, response.headers)
        policy = response.headers["Content-Security-Policy"]
        for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                          "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
                          "form-action 'self'"):
            self.assertIn(directive, policy)
        self.assertNotIn("unsafe", policy)   # no inline scripts or styles, no eval

    def test_post_without_csrf_token_is_rejected(self):
        response = self.client.post("/consent", data={"adult": "yes", "agree": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count("participant"), 0)

    def test_injection_style_scenario_id_is_rejected(self):
        self.consent()
        response = self.client.post("/assessment/pre", data={
            "scenario_id": "A01' OR '1'='1", "answer": "phishing", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count("scenario"), 30)

    def test_session_cookie_is_hardened(self):
        response = self.client.post("/consent", data={
            "adult": "yes", "agree": "yes", "csrf_token": self.token()})
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_unknown_page_returns_safe_404(self):
        response = self.client.get("/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"Traceback", response.data)
