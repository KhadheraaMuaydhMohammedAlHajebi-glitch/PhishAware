"""Black-box tests for the data tier: persistence across restarts and the reset command."""

from src.app import create_app
from src.db import get_db
from tests.helpers import AppTestCase


class DatabaseTests(AppTestCase):
    def test_existing_database_survives_an_application_restart(self):
        self.consent()
        restarted = create_app({
            "TESTING": True,
            "DATABASE": self.app.config["DATABASE"],
            "SECRET_KEY": "test-secret-key",
        })
        with restarted.app_context():
            counts = get_db().execute(
                "SELECT (SELECT COUNT(*) FROM participant) AS participants, "
                "(SELECT COUNT(*) FROM scenario) AS scenarios"
            ).fetchone()
        self.assertEqual(counts["participants"], 1)   # the record is still there
        self.assertEqual(counts["scenarios"], 30)     # and the bank was not seeded twice

    def test_reset_db_command_deletes_participant_data(self):
        self.consent()
        result = self.app.test_cli_runner().invoke(args=["reset-db", "--yes"])
        self.assertIsNone(result.exception)
        self.assertIn("Database rebuilt: 30 scenarios loaded, no participant data.", result.output)
        self.assertEqual(self.count("participant"), 0)
        self.assertEqual(self.count("scenario"), 30)

    def test_database_from_an_earlier_release_gains_the_new_tables(self):
        self.consent()
        with self.app.app_context():                # recreate the 0.5.1 schema
            get_db().execute("DROP TABLE admin_login_attempt")
            get_db().commit()
        upgraded = create_app({
            "TESTING": True,
            "DATABASE": self.app.config["DATABASE"],
            "SECRET_KEY": "test-secret-key",
        })
        with upgraded.app_context():
            tables = {row["name"] for row in get_db().execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            participants = get_db().execute("SELECT COUNT(*) AS n FROM participant").fetchone()
        self.assertIn("admin_login_attempt", tables)
        self.assertEqual(participants["n"], 1)      # upgrading keeps existing records
