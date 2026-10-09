"""Tests for the data tier: persistence, the reset command, and upgrades.

The upgrade tests open databases that were created with the schema files of
earlier releases (tests/fixtures, copied from the release tags). An earlier
version of these tests imitated release 0.5.1 by dropping one table from a
current database, which hid the fact that 0.5.1 had defined another table
differently (defect D-5).
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

from src import db
from src.app import create_app
from src.db import get_db
from tests.helpers import ADMIN_PASSWORD, ADMIN_USER, AppTestCase

FIXTURES = Path(__file__).with_name("fixtures")
EARLY_HASH = generate_password_hash(ADMIN_PASSWORD, method="scrypt:1024:8:1")
ADMIN_COLUMNS = ["id", "username", "password_hash", "session_stamp"]


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


class UpgradeTests(AppTestCase):
    """A database that an earlier release created, opened by this release."""

    def written_by(self, release, populate=None):
        """The path of a new database with the schema of an earlier release."""
        path = str(Path(self._tmp.name) / f"written-by-{release}.db")
        connection = sqlite3.connect(path)
        connection.executescript(
            (FIXTURES / f"schema-{release}.sql").read_text(encoding="utf-8"))
        db.seed_scenarios(connection, self.app.config["SCENARIO_FILE"])
        connection.execute(
            "INSERT INTO participant (id, consent_version, consented_at, form_order) "
            "VALUES ('6f1f0f5e-3f0b-4c57-9d53-0c1f0a8f2b11', '1.0', ?, 'AB')",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
        if populate:
            populate(connection)
        connection.commit()
        connection.close()
        return path

    def open_with_this_release(self, path):
        self.app = create_app({
            "TESTING": True, "DATABASE": path, "SECRET_KEY": "test-secret-key",
            "ADMIN_PASSWORD_METHOD": "scrypt:1024:8:1"})
        self.client = self.app.test_client()
        return self.app

    def columns(self, table):
        return [row["name"] for row in self.query(f"PRAGMA table_info({table})")]   # nosec B608

    def tables(self):
        return {row["name"] for row in self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

    def version(self):
        return self.query("PRAGMA user_version")[0][0]

    def test_new_database_records_the_schema_version(self):
        self.assertEqual(self.version(), db.SCHEMA_VERSION)
        self.assertEqual(self.columns("admin_user"), ADMIN_COLUMNS)

    def test_database_of_release_0_5_1_can_hold_an_administrator_after_the_upgrade(self):
        # Defect D-5, found by case IT-13: "no such column: session_stamp".
        path = self.written_by("0.5.1")
        with sqlite3.connect(path) as before:
            self.assertEqual([row[1] for row in before.execute("PRAGMA table_info(admin_user)")],
                             ["id", "username", "password_hash", "created_at"])
        with self.assertLogs("src.app", level="INFO") as logs:
            self.open_with_this_release(path)
        self.assertIn("Database schema brought to version 2.", "\n".join(logs.output))
        self.assertEqual(self.columns("admin_user"), ADMIN_COLUMNS)
        self.assertLessEqual({"lesson_view", "session_end", "admin_login_attempt"}, self.tables())
        self.assertNotIn("admin_user_before_0_6", self.tables())
        self.assertEqual(self.version(), 2)
        self.assertEqual(self.count("participant"), 1)       # upgrading keeps existing records
        self.assertEqual(self.count("scenario"), 30)
        result = self.create_admin()
        self.assertEqual(result.exit_code, 0, result.output or repr(result.exception))
        self.assertEqual(self.admin_sign_in().status_code, 302)
        self.assertEqual(self.query("PRAGMA integrity_check")[0][0], "ok")

    def test_a_row_in_the_older_administrator_table_is_kept(self):
        def populate(connection):
            connection.execute(
                "INSERT INTO admin_user (username, password_hash, created_at) "
                "VALUES (?, ?, '2026-09-01T08:00:00+00:00')", (ADMIN_USER, EARLY_HASH))

        self.open_with_this_release(self.written_by("0.5.1", populate))
        row = self.query("SELECT username, password_hash, session_stamp FROM admin_user")[0]
        self.assertEqual((row["username"], row["password_hash"]), (ADMIN_USER, EARLY_HASH))
        self.assertTrue(row["session_stamp"])
        self.assertEqual(self.admin_sign_in().status_code, 302)
        # A further account gets the next number, as in a table that was never rebuilt.
        self.create_admin(username="second-admin")
        numbers = [row["id"] for row in self.query("SELECT id FROM admin_user ORDER BY id")]
        self.assertEqual(numbers, [1, 2])

    def test_database_of_release_0_6_0_keeps_its_administrator_and_loses_the_typed_names(self):
        typed = "Tr0ub4dor-typed-into-the-wrong-box"

        def populate(connection):
            connection.execute(
                "INSERT INTO admin_user (username, password_hash, session_stamp) "
                "VALUES (?, ?, 'stamp-of-release-0.6.0')", (ADMIN_USER, EARLY_HASH))
            connection.executemany(
                "INSERT INTO admin_login_attempt (username, attempted_at) VALUES (?, ?)",
                [(typed, datetime.now(timezone.utc).isoformat(timespec="seconds"))] * 3)

        path = self.written_by("0.6.0", populate)
        self.assertIn(typed.encode(), Path(path).read_bytes())
        self.open_with_this_release(path)
        self.assertEqual(self.version(), 2)
        self.assertEqual(self.count("admin_login_attempt"), 0)
        self.assertNotIn(typed.encode(), Path(path).read_bytes())    # overwritten in the file
        # The table already had its present form, so it was not rebuilt: the stamp is the same.
        self.assertEqual(self.query("SELECT session_stamp FROM admin_user")[0][0],
                         "stamp-of-release-0.6.0")
        self.assertEqual(self.admin_sign_in().status_code, 302)

    def test_a_step_is_applied_once(self):
        path = self.written_by("0.6.0")
        self.open_with_this_release(path)
        self.create_admin()
        self.assertEqual(self.admin_sign_in(password="a wrong one").status_code, 401)
        self.assertEqual(self.count("admin_login_attempt"), 1)
        with self.assertNoLogs("src.app", level="INFO"):
            self.open_with_this_release(path)                  # a restart
        with self.app.app_context():
            self.assertEqual(db.migrate(), [])
        # The rate limit's memory survives a restart: step 2 did not run again.
        self.assertEqual(self.count("admin_login_attempt"), 1)

    def test_a_step_that_fails_leaves_the_database_exactly_as_it_was(self):
        def interrupted(connection):
            connection.execute("ALTER TABLE admin_user RENAME TO admin_user_before_0_6")
            connection.execute("DELETE FROM participant")
            raise sqlite3.OperationalError("disk I/O error")

        path = self.written_by("0.5.1")
        with mock.patch.object(db, "_MIGRATIONS", ((2, interrupted),)):
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk I/O error"):
                self.open_with_this_release(path)
        with sqlite3.connect(path) as after:
            self.assertEqual(after.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual([row[1] for row in after.execute("PRAGMA table_info(admin_user)")],
                             ["id", "username", "password_hash", "created_at"])
            self.assertEqual(after.execute("SELECT COUNT(*) FROM participant").fetchone()[0], 1)
        # The next start, without the fault, completes the upgrade.
        self.open_with_this_release(path)
        self.assertEqual((self.version(), self.columns("admin_user")), (2, ADMIN_COLUMNS))

    def test_processes_that_start_at_the_same_moment_migrate_once(self):
        # Two web workers and the jobs service open the database together after an
        # upgrade. The first one is held in the middle of the step, and the second
        # must wait outside it and then find the work done.
        path = self.written_by("0.5.1")
        inside, go_on = threading.Event(), threading.Event()
        entered, failures = [], []

        def slow_step(connection):
            entered.append(threading.current_thread().name)
            inside.set()
            go_on.wait(timeout=10)
            db._to_version_2(connection)

        def start():
            try:
                create_app({"TESTING": True, "DATABASE": path, "SECRET_KEY": "test-secret-key"})
            except Exception as error:      # reported by the assertion below
                failures.append(repr(error))

        with mock.patch.object(db, "_MIGRATIONS", ((2, slow_step),)):
            first = threading.Thread(target=start, name="first")
            first.start()
            self.assertTrue(inside.wait(timeout=10))
            second = threading.Thread(target=start, name="second")
            second.start()
            second.join(timeout=0.5)
            self.assertTrue(second.is_alive(), "the second process did not wait for the first")
            self.assertEqual(entered, ["first"])
            go_on.set()
            first.join(timeout=10)
            second.join(timeout=10)
        self.assertEqual(failures, [])
        self.assertEqual(entered, ["first"])                    # the step ran once
        self.open_with_this_release(path)
        self.assertEqual((self.version(), self.columns("admin_user")), (2, ADMIN_COLUMNS))
        self.assertEqual(self.count("participant"), 1)
        self.assertEqual(self.query("PRAGMA integrity_check")[0][0], "ok")

    def test_database_of_a_newer_release_is_opened_with_a_warning(self):
        path = self.app.config["DATABASE"]
        self.consent()
        with self.app.app_context():
            get_db().execute("PRAGMA user_version = 3")
        with self.assertLogs("src.app", level="WARNING") as logs:
            self.open_with_this_release(path)
        self.assertIn("a newer release has written to it", logs.output[0])
        self.assertIn("schema version 3", logs.output[0])
        self.assertEqual(self.version(), 3)                    # left as it is
        self.assertEqual(self.count("participant"), 1)
        self.assertEqual(self.client.get("/consent").status_code, 200)
