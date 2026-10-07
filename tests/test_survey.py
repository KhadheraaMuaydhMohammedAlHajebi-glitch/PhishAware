"""Black-box tests for the System Usability Scale survey (FR-09, NFR-05).

Ratings are chosen with equivalence partitioning and boundary-value analysis:
1 and 5 are the valid boundaries; 0, 6, 3.5, and a blank answer lie just outside.
"""

from tests.helpers import AppTestCase

TYPICAL = [4, 2, 5, 1, 4, 2, 5, 2, 4, 1]   # SUS score 85.0


class SurveyTests(AppTestCase):
    def finish_posttest(self):
        self.reach_posttest()
        self.answer_posttest()

    def stored(self):
        return self.query("SELECT score FROM sus_response")

    def status(self):
        return self.query("SELECT status FROM participant")[0]["status"]

    def test_survey_is_locked_until_the_post_test_is_complete(self):
        self.reach_posttest()
        for response in (
            self.client.get("/survey"),
            self.client.post("/survey", data=self.survey_data(TYPICAL)),
        ):
            self.assertEqual(response.status_code, 302)
            self.assertIn("/dashboard", response.headers["Location"])
        self.assertEqual(self.count("sus_response"), 0)

    def test_valid_ratings_are_scored_and_stored(self):
        self.finish_posttest()
        self.assertEqual(self.client.get("/survey").status_code, 200)
        response = self.client.post("/survey", data=self.survey_data(TYPICAL))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/survey/complete", response.headers["Location"])
        self.assertEqual(self.stored()[0]["score"], 85.0)
        self.assertEqual(self.status(), "completed")
        self.assertIn(b"Thank you for taking part", self.client.get("/survey/complete").data)

    def test_values_just_outside_the_scale_are_rejected(self):
        self.finish_posttest()
        for invalid in ("0", "6", "3.5", ""):
            with self.subTest(rating=invalid):
                data = self.survey_data(TYPICAL)
                data["q1"] = invalid
                response = self.client.post("/survey", data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Choose one answer for every statement", response.data)
        self.assertEqual(self.count("sus_response"), 0)
        self.assertEqual(self.status(), "active")

    def test_lowest_and_highest_ratings_are_accepted(self):
        self.finish_posttest()
        response = self.client.post("/survey", data=self.survey_data([5, 1] * 5))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored()[0]["score"], 100.0)

    def test_resubmitting_the_survey_changes_nothing(self):
        self.finish_posttest()
        self.client.post("/survey", data=self.survey_data(TYPICAL))
        self.client.post("/survey", data=self.survey_data([1, 5] * 5))
        self.assertEqual([row["score"] for row in self.stored()], [85.0])
        response = self.client.get("/survey")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/survey/complete", response.headers["Location"])

    def test_withdrawal_also_deletes_the_survey_ratings(self):
        self.finish_posttest()
        self.client.post("/survey", data=self.survey_data(TYPICAL))
        self.assertEqual(self.count("sus_response"), 1)
        self.client.post("/withdraw", data={"csrf_token": self.token()})
        self.assertEqual(self.count("sus_response"), 0)
