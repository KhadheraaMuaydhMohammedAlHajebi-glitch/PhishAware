"""Security tests for M8 (NFR-08 to NFR-11)."""

import base64

from flask.sessions import SecureCookieSessionInterface

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

    def test_session_cookie_is_signed_with_hmac_sha256(self):
        self.consent()
        signature = self.client.get_cookie("session").value.rsplit(".", 1)[1]
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        self.assertEqual(len(raw), 32)   # SHA-256 gives 32 bytes; Flask's default SHA-1 gives 20

    def test_cookie_signed_with_the_old_sha1_default_is_refused(self):
        participant_id = self.consent()
        legacy = SecureCookieSessionInterface().get_signing_serializer(self.app)
        forged = self.app.test_client()
        forged.set_cookie("session", legacy.dumps({"participant_id": participant_id}))
        response = forged.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consent", response.headers["Location"])

    def test_browser_data_is_cleared_only_when_a_session_ends(self):
        self.assertNotIn("Clear-Site-Data", self.client.get("/consent").headers)
        self.consent()
        self.assertNotIn("Clear-Site-Data", self.client.get("/dashboard").headers)
        response = self.client.post("/withdraw", data={"csrf_token": self.token()})
        self.assertEqual(response.headers["Clear-Site-Data"], '"cache", "storage"')

    def test_unknown_page_returns_safe_404(self):
        response = self.client.get("/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"Traceback", response.data)
