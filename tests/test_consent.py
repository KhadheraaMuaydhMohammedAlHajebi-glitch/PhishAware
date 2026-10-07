"""Black-box tests for M1 Consent & Session (FR-01, FR-02, FR-10)."""

from tests.helpers import AppTestCase

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
        self.consent()
        self.answer_pretest()
        self.assertEqual(self.count("response"), 12)
        response = self.client.post("/withdraw", data={"csrf_token": self.token()})
        self.assertEqual(response.status_code, 200)
        for table in ("participant", "attempt", "response"):
            self.assertEqual(self.count(table), 0, table)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
