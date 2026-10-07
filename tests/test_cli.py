"""Black-box tests for the command-line tools, run through Flask's test runner."""

from tests.helpers import AppTestCase


class CommandLineTests(AppTestCase):
    def test_init_db_command_reports_the_scenario_count(self):
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertIsNone(result.exception)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Database ready: 30 scenarios loaded.", result.output)

    def test_analytics_command_summarises_the_cohort_without_identifiers(self):
        sessions = (
            (self.client, [True] * 9 + [False] * 3, [True] * 11 + [False]),       # 75.0 -> 91.7
            (self.app.test_client(), [True] * 7 + [False] * 5, [True] * 10 + [False] * 2),
        )
        participant_ids = []
        for client, pre, post in sessions:
            participant_ids.append(self.consent(client))
            self.answer_pattern("/assessment/pre", pre, client)
            self.finish_practice(client)
            self.answer_pattern("/assessment/post", post, client)
        result = self.app.test_cli_runner().invoke(args=["analytics"])
        self.assertIsNone(result.exception)
        output = result.output
        self.assertIn("Participants who consented: 2", output)
        self.assertIn("n = 2: mean pre 66.7%, mean post 87.5%, mean gain +20.8 points", output)
        self.assertIn("SD of gains 5.9, d_z = 3.54, t(1) = 5.00", output)
        self.assertIn("RQ2  Error rate by cue (pre -> post)", output)
        self.assertIn("RQ3  Mean SUS score: no survey responses yet", output)
        for participant_id in participant_ids:
            self.assertNotIn(participant_id, output)
            self.assertNotIn(participant_id[-6:], output)
        # Once both participants answer the survey, RQ3 reports the mean of 85.0 and 75.0.
        for (client, _, _), ratings in zip(sessions, ([4, 2, 5, 1, 4, 2, 5, 2, 4, 1], [4, 2] * 5)):
            client.post("/survey", data=self.survey_data(ratings, client))
        output = self.app.test_cli_runner().invoke(args=["analytics"]).output
        self.assertIn("RQ3  Mean SUS score: 80.0", output)

    def test_analytics_command_waits_for_two_complete_participants(self):
        self.reach_posttest()
        self.answer_posttest()
        output = self.app.test_cli_runner().invoke(args=["analytics"]).output
        self.assertIn("Participants who consented: 1", output)
        self.assertIn("Not enough data yet", output)
