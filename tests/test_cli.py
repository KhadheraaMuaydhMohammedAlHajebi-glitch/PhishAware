"""Black-box tests for the command-line tools, run through Flask's test runner."""

from tests.helpers import AppTestCase


class CommandLineTests(AppTestCase):
    def test_init_db_command_reports_the_scenario_count(self):
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertIsNone(result.exception)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Database ready: 30 scenarios loaded.", result.output)
