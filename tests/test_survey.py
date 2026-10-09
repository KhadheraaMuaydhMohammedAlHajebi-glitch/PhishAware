"""Black-box tests for the System Usability Scale survey (FR-09, NFR-05).

Ratings are chosen with equivalence partitioning and boundary-value analysis:
1 and 5 are the valid boundaries; 0, 6, 3.5, and a blank answer lie just outside.
"""

import re

from tests.helpers import AppTestCase

TYPICAL = [4, 2, 5, 1, 4, 2, 5, 2, 4, 1]   # SUS score 85.0
CHOICE = re.compile(
    r'<label class="sus__choice">\s*<input type="radio" name="q(\d+)" value="(\d)" required>\s*'
    r'<span class="sus__value" aria-hidden="true">(\d)</span>\s*'
    r'<span class="sus__label">([^<]+)</span>\s*</label>')


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

    def test_every_choice_shows_its_number_and_keeps_its_label(self):
        # Finding U-3: on a phone the five choices of each statement were stacked.
        # They now stay in one row and show a number; the label is kept for
        # screen readers, and a line above each row names the two ends.
        self.finish_posttest()
        page = self.client.get("/survey").get_data(as_text=True)
        choices = CHOICE.findall(page)
        self.assertEqual(len(choices), 50)
        self.assertEqual(
            [(value, shown, label) for number, value, shown, label in choices if number == "7"],
            [("1", "1", "Strongly disagree"), ("2", "2", "Disagree"), ("3", "3", "Neutral"),
             ("4", "4", "Agree"), ("5", "5", "Strongly agree")])
        self.assertEqual(page.count(
            '<p class="sus__ends" aria-hidden="true"><span>Strongly disagree</span>'
            '<span>Strongly agree</span></p>'), 10)

    def test_the_style_sheet_keeps_the_scale_in_one_row_at_every_width(self):
        css = self.client.get("/static/css/style.css").get_data(as_text=True)
        self.assertIn(".sus__scale { display: grid; grid-template-columns: repeat(5,", css)
        self.assertNotIn(".sus__scale { grid-template-columns: 1fr; }", css)   # the stacked layout
        phone = css.split("@media (max-width: 600px)", 1)[1].split("@media", 1)[0]
        self.assertIn(".sus__ends { display: flex; }", phone)
        self.assertIn(".sus__label { position: absolute; width: 1px; height: 1px;", phone)

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
