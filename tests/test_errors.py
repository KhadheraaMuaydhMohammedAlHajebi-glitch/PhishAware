"""Tests for the error pages (M8, NFR-02, NFR-09).

Every error is answered by a page of the application, in its own words. The
framework's pages must never reach a participant: they have no layout and no
way back, and they speak of "the requested URL" and "the server".
"""

import sqlite3
from unittest import mock

from flask import abort

from src import repository
from tests.helpers import AppTestCase

FRAMEWORK_WORDING = (
    "The requested URL was not found on the server",
    "The method is not allowed for the requested URL",
    "The data value transmitted exceeds the capacity limit",
    "The browser (or proxy) sent a request that this server could not understand",
    "The server encountered an internal error",
    "This server is a teapot",
    "Internal Server Error",
    "Traceback (most recent call last)",
)


class ErrorPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        # A deployed server answers an unexpected failure itself; the test client
        # would otherwise pass the exception on to the test.
        self.app.config["PROPAGATE_EXCEPTIONS"] = False

    def raising(self, code, **details):
        """An address whose only job is to raise the given HTTP error."""
        self.app.add_url_rule(f"/raise-{code}", f"raise_{code}", lambda: abort(code, **details))
        return f"/raise-{code}"

    def assert_own_page(self, response, status, title):
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, status)
        self.assertIn(f"<title>{title} | PhishAware</title>", page)
        self.assertIn(f"<h1>{title}</h1>", page)
        self.assertIn('<main id="main"', page)
        self.assertIn("Go to the start page", page)
        self.assertIn(f"mention status {status}.", page)
        for sentence in FRAMEWORK_WORDING:
            self.assertNotIn(sentence, page)
        # The page is protected like every other one.
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return page

    def test_unknown_address_is_answered_in_plain_words(self):
        page = self.assert_own_page(self.client.get("/no-such-page"), 404, "Page not found")
        self.assertIn("There is no page at this address.", page)
        self.assertNotIn("Error 404", page)          # the code is not the headline (finding U-4)
        self.assertNotIn('class="kicker"', page)

    def test_wrong_method_is_explained_and_the_allowed_methods_are_named(self):
        response = self.client.get("/finish")
        page = self.assert_own_page(response, 405, "Action not allowed")
        self.assertIn("Use the buttons and links on the pages instead.", page)
        self.assertEqual(response.headers["Allow"], "OPTIONS, POST")

    def test_a_reason_given_by_the_application_is_shown(self):
        self.consent()
        response = self.client.post("/assessment/pre", data={
            "scenario_id": "A01", "answer": "maybe", "csrf_token": self.token()})
        page = self.assert_own_page(response, 400, "Request not accepted")
        self.assertIn("That answer is not one of the allowed choices.", page)

    def test_a_form_that_cannot_be_read_gets_the_general_reason(self):
        page = self.assert_own_page(self.client.get(self.raising(400)), 400,
                                    "Request not accepted")
        self.assertIn("The form could not be accepted, so nothing was changed.", page)

    def test_an_oversized_request_is_answered_on_the_same_page(self):
        page = self.assert_own_page(self.client.get(self.raising(413)), 413, "Too much data")
        self.assertIn("larger than any form of PhishAware", page)

    def test_a_status_that_has_no_text_of_its_own_still_gets_the_page(self):
        # Before, only five codes had a page; any other got the framework's.
        page = self.assert_own_page(self.client.get(self.raising(418)), 418,
                                    "Request not accepted")
        self.assertIn("The request could not be carried out, and nothing was changed.", page)

    def test_an_unexpected_failure_shows_no_detail(self):
        self.consent()
        with mock.patch.object(repository, "scenarios_for_pool",
                               side_effect=RuntimeError("table scenario, row 7")):
            response = self.client.get("/assessment/pre")
        page = self.assert_own_page(response, 500, "Something went wrong")
        self.assertIn("Your answers so far are saved.", page)
        self.assertNotIn("row 7", page)
        self.assertNotIn("RuntimeError", page)

    def test_a_server_error_never_shows_the_description_it_was_raised_with(self):
        address = self.raising(503, description="backup host db-2 is unreachable")
        page = self.assert_own_page(self.client.get(address), 503, "Something went wrong")
        self.assertNotIn("db-2", page)

    def test_the_error_page_does_not_depend_on_the_database(self):
        # The layout shows the session's identifier, which it reads from the
        # database. When the database is the problem, the page must still arrive.
        participant_id = self.consent()
        with mock.patch.object(repository, "get_participant",
                               side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertLogs(self.app.logger, level="ERROR") as logs:
                response = self.client.get("/dashboard")
        page = self.assert_own_page(response, 500, "Something went wrong")
        self.assertNotIn("database is locked", page)
        self.assertNotIn(participant_id[-6:], page)
        self.assertTrue(any("a plain page was sent" in line for line in logs.output))
        # The session was not harmed: the next request works again.
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_text_on_the_plain_page_is_escaped(self):
        address = self.raising(400, description='<script>alert("x")</script>')
        with mock.patch("src.modules.errors.render_template", side_effect=RuntimeError("layout")):
            with self.assertLogs(self.app.logger, level="ERROR"):
                page = self.client.get(address).get_data(as_text=True)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;", page)
