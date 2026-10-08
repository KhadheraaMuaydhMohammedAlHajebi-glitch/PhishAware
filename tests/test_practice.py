"""Black-box tests for M4 Scenario Practice & Feedback (FR-05, FR-06)."""

import html
import json
import re

from tests.helpers import AppTestCase

TAGS = re.compile(r"<[^>]+>")


class PracticeTests(AppTestCase):
    def test_practice_requires_completed_pretest(self):
        self.consent()
        response = self.client.get("/practice")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/assessment/pre", response.headers["Location"])

    def test_practice_opens_only_after_the_lessons(self):
        self.consent()
        self.answer_pretest()
        data = {"scenario_id": "P01", "answer": "phishing", "csrf_token": self.token()}
        for response in (self.client.get("/practice"), self.client.post("/practice", data=data)):
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/learn"))
        self.assertEqual(self.count("response"), 12)       # the practice answer was not stored
        self.assertEqual(self.count("lesson_view"), 0)
        self.assertEqual(self.open_lessons().status_code, 200)
        self.open_lessons()                                 # a second visit changes nothing
        self.assertEqual(self.count("lesson_view"), 1)
        self.assertEqual(self.client.get("/practice").status_code, 200)

    def test_wrong_answer_gets_immediate_cue_explanation(self):
        self.consent()
        self.answer_pretest()
        self.open_lessons()
        page = self.client.get("/practice").get_data(as_text=True)
        scenario_id = self.scenario_id_from(page)
        response = self.answer_current("/practice", correct=False)
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        # Compare what the participant reads: domains in the text carry markup
        # that keeps them from breaking across lines, so the tags are removed.
        feedback = html.unescape(TAGS.sub("", self.client.get(location).get_data(as_text=True)))
        content = json.loads(self.query(
            "SELECT content_json FROM scenario WHERE id = ?", (scenario_id,))[0]["content_json"])
        self.assertIn("Not quite", feedback)
        self.assertIn(content["feedback"]["explanation"], feedback)
        self.assertIn(content["feedback"]["safe_action"], feedback)

    def test_feedback_is_hidden_for_unanswered_scenarios(self):
        self.consent()
        self.answer_pretest()
        self.open_lessons()
        self.client.get("/practice")
        self.assertEqual(self.client.get("/practice/feedback/P06").status_code, 404)

    def test_completing_practice_shows_summary(self):
        self.consent()
        self.answer_pretest()
        self.open_lessons()
        for _ in range(6):
            self.answer_current("/practice", correct=True)
        summary = self.client.get("/practice/complete")
        self.assertEqual(summary.status_code, 200)
        self.assertIn(b"6 of 6", summary.data)
