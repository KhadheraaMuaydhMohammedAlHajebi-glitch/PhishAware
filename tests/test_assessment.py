"""Black-box tests for M2 Assessment Engine with M5 scoring (FR-03, NFR-05)."""

from tests.helpers import AppTestCase


class AssessmentTests(AppTestCase):
    def test_sessions_are_counterbalanced(self):
        self.consent()
        self.consent(self.app.test_client())
        orders = [row["form_order"] for row in
                  self.query("SELECT form_order FROM participant ORDER BY seq")]
        self.assertEqual(orders, ["AB", "BA"])

    def test_completed_pretest_is_scored(self):
        self.consent()
        self.answer_pretest(correct=True)
        response = self.client.get("/assessment/pre")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/assessment/pre/complete", response.headers["Location"])
        attempt = self.query("SELECT score, completed_at FROM attempt WHERE phase = 'pre'")[0]
        self.assertEqual(attempt["score"], 100.0)
        self.assertIsNotNone(attempt["completed_at"])
        self.assertIn(b"100.0", self.client.get("/assessment/pre/complete").data)

    def test_resubmitting_an_answer_is_idempotent(self):
        self.consent()
        html = self.client.get("/assessment/pre").get_data(as_text=True)
        data = {"scenario_id": self.scenario_id_from(html), "answer": "phishing",
                "csrf_token": self.token()}
        self.client.post("/assessment/pre", data=data)
        self.client.post("/assessment/pre", data=data)
        self.assertEqual(self.count("response"), 1)

    def test_answer_outside_allowlist_is_rejected(self):
        self.consent()
        html = self.client.get("/assessment/pre").get_data(as_text=True)
        response = self.client.post("/assessment/pre", data={
            "scenario_id": self.scenario_id_from(html), "answer": "maybe",
            "csrf_token": self.token()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count("response"), 0)

    def test_item_from_the_other_form_is_rejected(self):
        self.consent()  # first session takes Form A
        response = self.client.post("/assessment/pre", data={
            "scenario_id": "B01", "answer": "phishing", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 400)
