"""Black-box tests for M1 Consent & Session (FR-01, FR-02, FR-10).

The consent page makes promises about storage, retention, and time limits. Each
promise is tested against the mechanism that keeps it, so the wording cannot
drift away from what the software does.
"""

import time
from pathlib import Path
from unittest import mock

from itsdangerous import TimestampSigner

from tests.helpers import AppTestCase

SUS = [4, 2, 5, 1, 4, 2, 5, 2, 4, 1]
UUID4_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


class ConsentTests(AppTestCase):
    def test_consent_page_loads_and_stores_nothing(self):
        response = self.client.get("/consent")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Before you begin", response.data)
        self.assertEqual(self.count("participant"), 0)

    def test_protected_pages_redirect_to_consent(self):
        for url in ("/dashboard", "/assessment/pre", "/learn", "/practice", "/withdraw"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/consent", response.headers["Location"], url)

    def test_missing_age_confirmation_stores_nothing(self):
        response = self.client.post("/consent", data={"agree": "yes", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count("participant"), 0)

    def test_declining_stores_nothing(self):
        self.assertEqual(self.client.get("/consent/declined").status_code, 200)
        self.assertEqual(self.count("participant"), 0)

    def test_consent_creates_random_pseudonymous_id(self):
        participant_id = self.consent()
        self.assertRegex(participant_id, UUID4_PATTERN)
        self.assertEqual(self.count("participant"), 1)
        columns = {row["name"] for row in self.query("PRAGMA table_info(participant)")}
        self.assertEqual(
            columns, {"seq", "id", "consent_version", "consented_at", "form_order", "status"}
        )

    def test_withdrawal_deletes_all_linked_records(self):
        self.complete_session(6, 9, SUS)                 # another participant, who stays
        self.consent()
        self.answer_pretest()
        self.open_lessons()
        self.assertEqual(self.count("response"), 30 + 12)
        response = self.client.post("/withdraw", data={"csrf_token": self.token()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.count("participant"), 1)
        self.assertEqual(self.count("attempt"), 3)
        self.assertEqual(self.count("response"), 30)
        self.assertEqual(self.count("lesson_view"), 1)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_withdrawn_identifier_is_erased_from_the_database_file(self):
        participant_id = self.consent()
        self.answer_pretest()
        database = Path(self.app.config["DATABASE"])
        self.assertIn(participant_id.encode(), database.read_bytes())
        self.client.post("/withdraw", data={"csrf_token": self.token()})
        # Deleted rows are overwritten with zeros, not just marked as free space.
        self.assertEqual(self.query("PRAGMA secure_delete")[0][0], 1)
        self.assertNotIn(participant_id.encode(), database.read_bytes())


class ConsentWordingTests(AppTestCase):
    """Version 1.1 of the consent form states how long data and sessions last."""

    def page(self):
        return self.client.get("/consent").get_data(as_text=True)

    def test_form_version_is_shown_and_stored_with_the_consent_record(self):
        self.assertIn("consent form version 1.1", self.page())
        self.assertIn("your answers and the time of each", self.page())
        self.consent()
        self.assertEqual(
            self.query("SELECT consent_version FROM participant")[0]["consent_version"], "1.1")

    def test_retention_periods_come_from_the_configuration(self):
        self.assertIn("Every record is deleted 90 days after you agree", self.page())
        self.assertIn("Encrypted backup copies exist for up to 7 days longer", self.page())
        self.app.config.update(RETENTION_DAYS=30, BACKUP_RETENTION_DAYS=3)
        self.assertIn("Every record is deleted 30 days after you agree", self.page())
        self.assertIn("Encrypted backup copies exist for up to 3 days longer", self.page())

    def test_session_limit_is_stated(self):
        self.assertIn("or after 2 hours without activity", self.page())

    def test_contact_line_appears_only_when_configured(self):
        self.assertNotIn("Contact the researcher", self.page())
        self.app.config["CONTACT"] = "A. Researcher <researcher@example.edu>"
        page = self.page()
        self.assertIn("Contact the researcher: A. Researcher", page)
        self.assertIn("&lt;researcher@example.edu&gt;", page)   # escaped, never markup

    def test_missing_confirmation_shows_the_same_information_again(self):
        response = self.client.post("/consent", data={"adult": "yes", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 400)
        page = response.get_data(as_text=True)
        self.assertIn("Confirm that you are 18 or older", page)
        self.assertIn("Every record is deleted 90 days after you agree", page)

    def test_withdrawal_pages_state_the_backup_period(self):
        self.consent()
        self.assertIn(
            "Encrypted backup copies follow within 7 days",
            self.client.get("/withdraw").get_data(as_text=True))
        response = self.client.post("/withdraw", data={"csrf_token": self.token()})
        self.assertIn("will be gone within 7 days", response.get_data(as_text=True))


class SessionLimitTests(AppTestCase):
    """The session ends after two hours without activity, as the consent page says."""

    def request_at(self, seconds_from_now, url="/dashboard"):
        moment = int(time.time()) + seconds_from_now
        with mock.patch.object(TimestampSigner, "get_timestamp", return_value=moment):
            return self.client.get(url)

    def test_session_is_accepted_just_inside_two_hours(self):
        self.consent()
        self.assertEqual(self.request_at(2 * 3600 - 5).status_code, 200)

    def test_session_is_refused_after_two_hours_without_activity(self):
        self.consent()
        response = self.request_at(2 * 3600 + 5)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consent", response.headers["Location"])
        self.assertEqual(self.count("participant"), 1)   # the stored record is untouched

    def test_activity_extends_the_session(self):
        self.consent()
        self.assertEqual(self.request_at(90 * 60).status_code, 200)    # 1.5 hours in
        self.assertEqual(self.request_at(180 * 60).status_code, 200)   # 1.5 hours after that


class FinishTests(AppTestCase):
    """Finishing ends the session on a shared computer and keeps the records."""

    def finish(self, client=None):
        client = client or self.client
        return client.post("/finish", data={"csrf_token": self.token(client)})

    def complete_in_this_browser(self):
        participant_id = self.reach_posttest()
        self.answer_posttest()
        self.client.post("/survey", data=self.survey_data(SUS))
        return participant_id

    def test_finish_is_not_available_before_the_survey(self):
        self.reach_posttest()
        self.answer_posttest()
        response = self.finish()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        self.assertEqual(self.client.get("/dashboard").status_code, 200)   # still signed in

    def test_finish_ends_the_session_and_keeps_every_record(self):
        self.complete_in_this_browser()
        self.assertIn(
            b"Finish and sign out of this browser", self.client.get("/survey/complete").data)
        response = self.finish()
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("You are signed out of this browser", page)
        self.assertIn("deleted 90 days after you agreed", page)
        for url in ("/dashboard", "/results", "/withdraw"):
            self.assertEqual(self.client.get(url).status_code, 302, url)
        self.assertEqual(self.count("participant"), 1)
        self.assertEqual(self.count("response"), 30)
        self.assertEqual(self.count("sus_response"), 1)

    def test_copy_of_the_cookie_is_refused_after_finishing(self):
        self.complete_in_this_browser()
        copy = self.copy_session()                       # taken while the session was open
        self.assertEqual(copy.get("/results").status_code, 200)
        response = self.finish()
        self.assertEqual(response.headers["Clear-Site-Data"], '"cache", "storage"')
        for url in ("/dashboard", "/results", "/learn", "/withdraw"):
            response = copy.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/consent", response.headers["Location"], url)
        self.assertEqual(self.count("session_end"), 1)
        self.assertEqual(
            [row["name"] for row in self.query("PRAGMA table_info(session_end)")],
            ["participant_id"])                          # the fact only, no time

    def test_finish_requires_the_security_token(self):
        self.complete_in_this_browser()
        self.assertEqual(self.client.post("/finish").status_code, 400)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_finish_without_a_session_goes_to_the_consent_page(self):
        response = self.finish()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consent", response.headers["Location"])
