"""Black-box tests for the counterbalanced post-assessment (FR-07)."""

from tests.helpers import AppTestCase


class PostAssessmentTests(AppTestCase):
    def post_attempts(self):
        return self.query("SELECT form, score, completed_at FROM attempt WHERE phase = 'post'")

    def test_post_test_is_locked_until_practice_is_complete(self):
        self.consent()
        self.answer_pretest()
        response = self.client.get("/assessment/post")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])
        # A hand-crafted POST must not bypass the lock either.
        response = self.client.post("/assessment/post", data={
            "scenario_id": "B01", "answer": "phishing", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.post_attempts(), [])

    def test_post_test_uses_the_second_form_of_the_order(self):
        odd, even = self.client, self.app.test_client()
        self.reach_posttest(odd)    # session 1: order AB, so the post-test is Form B
        self.reach_posttest(even)   # session 2: order BA, so the post-test is Form A
        odd_page = odd.get("/assessment/post").get_data(as_text=True)
        even_page = even.get("/assessment/post").get_data(as_text=True)
        self.assertIn("Post-assessment, Form B", odd_page)
        self.assertIn("Post-assessment, Form A", even_page)
        forms = [row["form"] for row in self.query(
            "SELECT a.form FROM attempt a JOIN participant p ON p.id = a.participant_id "
            "WHERE a.phase = 'post' ORDER BY p.seq")]
        self.assertEqual(forms, ["B", "A"])

    def test_completed_post_test_is_scored_without_changing_the_pre_test(self):
        self.reach_posttest()
        self.answer_pattern("/assessment/post", [True] * 10 + [False] * 2)
        response = self.client.get("/assessment/post")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/assessment/post/complete", response.headers["Location"])
        attempt = self.post_attempts()[0]
        self.assertEqual(attempt["score"], 83.3)
        self.assertIsNotNone(attempt["completed_at"])
        pre = self.query("SELECT score FROM attempt WHERE phase = 'pre'")[0]
        self.assertEqual(pre["score"], 100.0)
        self.assertIn(b"83.3", self.client.get("/assessment/post/complete").data)

    def test_pre_and_post_items_never_overlap(self):
        self.reach_posttest()
        self.answer_posttest()
        ids = {}
        for phase in ("pre", "post"):
            ids[phase] = {row["scenario_id"] for row in self.query(
                "SELECT r.scenario_id FROM response r JOIN attempt a ON a.id = r.attempt_id "
                "WHERE a.phase = ?", (phase,))}
        self.assertEqual(len(ids["pre"]), 12)
        self.assertEqual(len(ids["post"]), 12)
        self.assertEqual(ids["pre"] & ids["post"], set())

    def test_item_from_the_pre_test_form_is_rejected(self):
        self.reach_posttest()  # first session: pre-test on Form A, post-test on Form B
        response = self.client.post("/assessment/post", data={
            "scenario_id": "A01", "answer": "phishing", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 400)
        stored = self.query(
            "SELECT COUNT(*) AS n FROM response r JOIN attempt a ON a.id = r.attempt_id "
            "WHERE a.phase = 'post'")[0]["n"]
        self.assertEqual(stored, 0)
