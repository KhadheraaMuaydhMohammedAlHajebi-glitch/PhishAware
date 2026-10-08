"""Black-box tests for the dashboard, the lessons page, and the navigation guards.

Statement coverage of release 0.5.0 showed that no test opened the dashboard or
the lessons page, and that several out-of-order requests had never been tried.
"""

from tests.helpers import AppTestCase


class NavigationTests(AppTestCase):
    def dashboard(self):
        return self.client.get("/dashboard").get_data(as_text=True)

    def test_start_page_routes_by_consent_state(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/consent", response.headers["Location"])
        self.consent()
        for url in ("/", "/consent"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/dashboard", response.headers["Location"], url)

    def test_dashboard_tracks_every_step(self):
        participant_id = self.consent()
        page = self.dashboard()
        self.assertIn(f'Anonymous session</span><span class="only-narrow">Session</span> '
                      f"{participant_id[-6:]}", page)
        self.assertIn("You take Form A first and Form B at the end", page)
        self.assertIn("After the pre-assessment", page)   # the lessons are locked
        self.assertIn("After the lessons", page)          # and so is the practice
        self.answer_pattern("/assessment/pre", [True] * 3)
        self.assertIn("3 of 12 answered", self.dashboard())
        self.answer_pattern("/assessment/pre", [True] * 9)
        page = self.dashboard()
        self.assertIn("Baseline 100.0%", page)
        self.assertIn("Open the lessons", page)
        self.assertIn("After the lessons", page)          # practice waits for the lessons
        self.open_lessons()
        page = self.dashboard()
        self.assertIn("Review the lessons", page)
        self.assertNotIn("After the lessons", page)
        self.finish_practice()
        self.assertIn("6 of 6 correct", self.dashboard())
        self.answer_posttest()
        page = self.dashboard()
        self.assertIn("Final 100.0%", page)
        self.assertIn("See your results", page)
        self.assertNotIn("Finish and sign out", page)     # offered only after the last step
        self.client.post("/survey", data=self.survey_data([4, 2] * 5))
        page = self.dashboard()
        self.assertNotIn("See your results", page)
        self.assertIn("Finish and sign out of this browser", page)

    def test_lessons_open_after_the_pre_test(self):
        self.consent()
        response = self.client.get("/learn")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/assessment/pre", response.headers["Location"])
        self.answer_pretest()
        response = self.client.get("/learn")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True).count('<li class="lesson">'), 6)

    def test_withdraw_page_asks_before_deleting_anything(self):
        self.consent()
        response = self.client.get("/withdraw")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Withdraw and delete your data?", response.data)
        self.assertEqual(self.count("participant"), 1)

    def test_practice_rejects_out_of_order_requests(self):
        self.consent()
        data = {"scenario_id": "P01", "answer": "phishing", "csrf_token": self.token()}
        response = self.client.post("/practice", data=data)     # before the pre-test
        self.assertEqual(response.status_code, 302)
        self.assertIn("/assessment/pre", response.headers["Location"])
        self.assertEqual(self.count("response"), 0)
        self.answer_pretest()
        response = self.client.get("/practice/complete")        # nothing practised yet
        self.assertEqual(response.status_code, 302)
        self.open_lessons()
        data["scenario_id"] = "A01"                              # not a practice scenario
        self.assertEqual(self.client.post("/practice", data=data).status_code, 400)
        self.finish_practice()
        response = self.client.get("/practice")                 # nothing left to practise
        self.assertEqual(response.status_code, 302)
        self.assertIn("/practice/complete", response.headers["Location"])

    def test_summary_pages_redirect_until_their_phase_is_complete(self):
        self.consent()
        expected = {
            "/assessment/pre/complete": "/assessment/pre",
            "/assessment/post/complete": "/assessment/post",
            "/survey/complete": "/survey",
        }
        for url, target in expected.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertTrue(response.headers["Location"].endswith(target), url)
