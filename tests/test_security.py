"""Security tests for M8 (NFR-08 to NFR-11)."""

import base64
from unittest import mock

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

    def test_token_outside_ascii_is_refused_like_any_other_wrong_token(self):
        # Found by case IT-04: such a token ended in an unhandled TypeError (status 500).
        self.consent()
        for token in ("t\u00e9st", "\u0631\u0645\u0632", "\U0001F600" * 8):
            response = self.client.post("/assessment/pre", data={
                "scenario_id": "A01", "answer": "phishing", "csrf_token": token})
            self.assertEqual(response.status_code, 400, token)
            self.assertIn(b"Your security token is missing or has expired", response.data)
        self.assertEqual(self.count("response"), 0)

    def test_request_larger_than_any_form_is_refused_before_it_is_read(self):
        # Defect D-6, found by cases ST-12 and IT-12: there was no limit, and the
        # framework release in use parses a form of any size.
        self.assertEqual(self.app.config["MAX_CONTENT_LENGTH"], 64 * 1024)
        self.consent()
        token = self.token()
        with mock.patch("werkzeug.formparser.FormDataParser.parse") as parser:
            response = self.client.post("/assessment/pre", data={
                "scenario_id": "A01", "answer": "p" * 200_000, "csrf_token": token})
        parser.assert_not_called()                      # the body was not parsed at all
        self.assertEqual(response.status_code, 413)
        page = response.get_data(as_text=True)
        self.assertIn("<h1>Too much data</h1>", page)
        self.assertIn("Go to the start page", page)
        self.assertNotIn("The data value transmitted exceeds the capacity limit", page)
        self.assertEqual(self.count("response"), 0)
        self.assertEqual(self.client.get("/assessment/pre").status_code, 200)   # session intact

    def test_the_limit_is_exact_and_ordinary_forms_are_far_below_it(self):
        self.consent()
        limit = self.app.config["MAX_CONTENT_LENGTH"]
        prefix = b"csrf_token=" + self.token().encode() + b"&scenario_id=A01&answer="

        def post(length):
            body = prefix + b"x" * (length - len(prefix))
            return self.client.post("/assessment/pre", data=body,
                                    content_type="application/x-www-form-urlencoded")

        self.assertEqual(post(limit + 1).status_code, 413)
        self.assertEqual(post(limit).status_code, 400)       # read, and refused as an answer
        self.assertLess(len(prefix) + len("legitimate"), 300)

    def test_every_form_of_the_application_is_refused_alike_when_it_is_too_large(self):
        self.create_admin()
        for address in ("/consent", "/assessment/pre", "/practice", "/assessment/post",
                        "/survey", "/withdraw", "/finish", "/admin/login", "/admin/logout"):
            response = self.client.post(address, data={"csrf_token": "t" * 70_000})
            self.assertEqual(response.status_code, 413, address)
        self.assertEqual(self.count("participant"), 0)
        self.assertEqual(self.count("admin_login_attempt"), 0)

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
