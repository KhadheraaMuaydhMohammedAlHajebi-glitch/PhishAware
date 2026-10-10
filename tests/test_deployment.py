"""Tests for the deployment-facing behaviour: start-up checks, health, and storage mode.

These settings decide whether a deployment is safe, so each rule is tested at
its boundary: a 31- and a 32-character secret key, each accepted and each
rejected environment name, and a database that does and does not answer.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from src import __version__, db, repository
from src.config import env_flag, env_int
from src.db import get_db
from tests.helpers import AppTestCase, end_apps, new_backup_key, restart_app, start_app

SUS = [4, 2, 5, 1, 4, 2, 5, 2, 4, 1]


class StartupCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(end_apps)

    def start(self, **settings):
        config = {"TESTING": True, "DATABASE": os.path.join(self._tmp.name, "start.db")}
        config.update(settings)
        return start_app(config)

    def test_production_refuses_to_start_without_a_secret_key(self):
        with self.assertRaises(RuntimeError) as raised:
            self.start(APP_ENV="production", SECRET_KEY=None)
        self.assertIn("PHISHAWARE_SECRET_KEY must be set to at least 32", str(raised.exception))
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "start.db")))

    def test_production_secret_key_boundary_is_thirty_two_characters(self):
        with self.assertRaises(RuntimeError):
            self.start(APP_ENV="production", SECRET_KEY="k" * 31)
        app = self.start(APP_ENV="production", SECRET_KEY="k" * 32)
        self.assertEqual(app.config["SECRET_KEY"], "k" * 32)

    def test_production_refuses_debug_mode(self):
        with self.assertRaises(RuntimeError) as raised:
            self.start(APP_ENV="production", SECRET_KEY="k" * 32, DEBUG=True)
        self.assertIn("debug mode must be off", str(raised.exception))

    def test_misspelled_settings_are_reported_together(self):
        with self.assertRaises(RuntimeError) as raised:
            self.start(APP_ENV="staging", SQLITE_JOURNAL_MODE="FAST")
        message = str(raised.exception)
        self.assertIn("PHISHAWARE_ENV must be one of development, production", message)
        self.assertIn("PHISHAWARE_SQLITE_JOURNAL must be one of AUTO, WAL, DELETE", message)

    def test_periods_shorter_than_their_minimum_are_reported(self):
        with self.assertRaises(RuntimeError) as raised:
            self.start(RETENTION_DAYS=0, BACKUP_RETENTION_DAYS=0, JOB_INTERVAL=59)
        message = str(raised.exception)
        self.assertIn("PHISHAWARE_RETENTION_DAYS must be at least 1", message)
        self.assertIn("PHISHAWARE_BACKUP_DAYS must be at least 1", message)
        self.assertIn("PHISHAWARE_JOB_INTERVAL must be at least 60", message)
        app = self.start(RETENTION_DAYS=1, BACKUP_RETENTION_DAYS=1, JOB_INTERVAL=60)
        self.assertEqual(app.config["JOB_INTERVAL"], 60)

    def test_env_int_reads_whole_numbers_and_names_an_unreadable_setting(self):
        with mock.patch.dict(os.environ, {"DAYS": "30", "EMPTY": "", "WORD": "ninety"}):
            self.assertEqual(env_int("DAYS", 90), 30)
            self.assertEqual(env_int("EMPTY", 90), 90)
            self.assertEqual(env_int("UNSET_NUMBER", 90), 90)
            with self.assertRaises(RuntimeError) as raised:
                env_int("WORD", 90)
        self.assertIn("WORD must be a whole number, not 'ninety'", str(raised.exception))

    def test_development_generates_a_different_key_on_every_start(self):
        first = self.start(SECRET_KEY=None).config["SECRET_KEY"]
        second = self.start(SECRET_KEY=None).config["SECRET_KEY"]
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)

    def test_env_flag_reads_one_as_on_and_anything_else_as_off(self):
        with mock.patch.dict(os.environ, {"FLAG_ON": "1", "FLAG_OFF": "0", "FLAG_ODD": "yes"}):
            self.assertTrue(env_flag("FLAG_ON", False))
            self.assertFalse(env_flag("FLAG_OFF", True))
            self.assertFalse(env_flag("FLAG_ODD", True))
            self.assertTrue(env_flag("FLAG_UNSET", True))
            self.assertFalse(env_flag("FLAG_UNSET", False))


class HealthTests(AppTestCase):
    def test_healthy_instance_reports_its_version_and_scenario_count(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(), {"status": "ok", "version": __version__, "scenarios": 30})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", response.headers)   # the probe opens no session

    def test_unreachable_database_is_reported_as_unavailable(self):
        failure = sqlite3.OperationalError("unable to open database file")
        with mock.patch.object(repository, "scenario_count", side_effect=failure):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"status": "unavailable"})

    def test_probe_reveals_no_participant_data(self):
        participant_id = self.consent()
        body = self.app.test_client().get("/healthz").get_data(as_text=True)
        self.assertNotIn(participant_id, body)
        self.assertNotIn("participant", body)


class TransportSecurityTests(AppTestCase):
    def https_app(self):
        return start_app({
            "TESTING": True, "SECRET_KEY": "k" * 32, "SESSION_COOKIE_SECURE": True,
            "DATABASE": os.path.join(self._tmp.name, "https.db"),
        })

    def test_https_settings_add_hsts_and_a_host_bound_secure_cookie(self):
        response = self.https_app().test_client().get("/consent")
        self.assertEqual(response.headers["Strict-Transport-Security"], "max-age=31536000")
        cookie = response.headers["Set-Cookie"]
        # The "__Host-" prefix is honoured only with Secure, Path=/, and no Domain.
        self.assertTrue(cookie.startswith("__Host-session="), cookie)
        attributes = [part.strip() for part in cookie.split(";")[1:]]
        self.assertIn("Secure", attributes)
        self.assertIn("HttpOnly", attributes)
        self.assertIn("Path=/", attributes)
        self.assertFalse([part for part in attributes if part.lower().startswith("domain")])

    def test_a_whole_session_works_with_the_host_bound_cookie(self):
        app = self.https_app()
        client = app.test_client()
        page = client.get("/consent", base_url="https://localhost")
        token = page.get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
        response = client.post("/consent", base_url="https://localhost", data={
            "adult": "yes", "agree": "yes", "csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            client.get("/dashboard", base_url="https://localhost").status_code, 200)

    def test_plain_http_development_sends_neither(self):
        response = self.client.get("/consent")
        self.assertNotIn("Strict-Transport-Security", response.headers)
        cookie = response.headers["Set-Cookie"]
        self.assertTrue(cookie.startswith("session="), cookie)
        self.assertNotIn("Secure", cookie)


class JournalModeTests(AppTestCase):
    def journal_mode(self, app=None):
        with (app or self.app).app_context():
            return get_db().execute("PRAGMA journal_mode").fetchone()[0]

    def restart(self, **settings):
        config = {"TESTING": True, "DATABASE": self.app.config["DATABASE"], "SECRET_KEY": "k"}
        config.update(settings)
        return restart_app(config)

    def test_wal_is_trusted_only_on_releases_with_the_fix(self):
        # Boundary values around 3.51.3 and the patch releases 3.44.6 and 3.50.7.
        fixed = ((3, 51, 3), (3, 51, 4), (3, 52, 0), (4, 0, 0),
                 (3, 50, 7), (3, 50, 8), (3, 44, 6), (3, 44, 7))
        affected = ((3, 7, 0), (3, 44, 5), (3, 45, 0), (3, 46, 1), (3, 50, 6),
                    (3, 51, 0), (3, 51, 2))
        for version in fixed:
            self.assertTrue(db.wal_is_safe(version), version)
        for version in affected:
            self.assertFalse(db.wal_is_safe(version), version)

    def test_auto_chooses_the_rollback_journal_on_an_affected_release(self):
        self.assertEqual(db.journal_mode_for("AUTO", (3, 46, 1)), "DELETE")
        self.assertEqual(db.journal_mode_for("AUTO", (3, 51, 3)), "WAL")
        self.assertEqual(db.journal_mode_for("WAL", (3, 46, 1)), "WAL")       # explicit choice wins
        self.assertEqual(db.journal_mode_for("DELETE", (3, 51, 3)), "DELETE")

    def test_default_mode_matches_this_sqlite_release(self):
        expected = "wal" if db.wal_is_safe() else "delete"
        self.assertEqual(self.app.config["SQLITE_JOURNAL_MODE"], "AUTO")
        self.assertEqual(self.journal_mode(), expected)

    def test_mode_can_be_switched_in_both_directions_without_losing_data(self):
        self.consent()
        self.assertEqual(self.journal_mode(self.restart(SQLITE_JOURNAL_MODE="WAL")), "wal")
        self.assertEqual(self.journal_mode(self.restart(SQLITE_JOURNAL_MODE="DELETE")), "delete")
        self.assertEqual(self.count("participant"), 1)

    def test_wal_reader_is_not_blocked_while_another_connection_is_writing(self):
        app = self.restart(SQLITE_JOURNAL_MODE="WAL")
        participant = app.test_client()
        self.consent(participant)
        writer = sqlite3.connect(app.config["DATABASE"], timeout=0.2)
        writer.execute("BEGIN EXCLUSIVE")   # hold the write lock, as a slow request would
        writer.execute("UPDATE participant SET status = 'completed'")
        try:
            self.assertEqual(app.test_client().get("/healthz").status_code, 200)
            self.assertEqual(participant.get("/dashboard").status_code, 200)
        finally:
            writer.rollback()
            writer.close()

    def test_reset_keeps_the_configured_mode_and_leaves_no_stale_log(self):
        app = self.restart(SQLITE_JOURNAL_MODE="WAL")
        self.consent(app.test_client())
        app.test_cli_runner().invoke(args=["reset-db", "--yes"])
        self.assertEqual(self.journal_mode(app), "wal")
        self.assertEqual(self.count("participant"), 0)
        self.assertEqual(self.count("scenario"), 30)

    def test_backup_and_restore_work_in_both_modes(self):
        for mode in ("WAL", "DELETE"):
            with self.subTest(mode=mode):
                app = self.restart(SQLITE_JOURNAL_MODE=mode)
                app.config["BACKUP_KEY"] = new_backup_key()
                app.config["BACKUP_DIR"] = os.path.join(self._tmp.name, f"backups-{mode}")
                runner = app.test_cli_runner()
                self.assertEqual(runner.invoke(args=["backup-db"]).exit_code, 0)
                snapshot = os.path.join(
                    app.config["BACKUP_DIR"], os.listdir(app.config["BACKUP_DIR"])[0])
                self.consent(app.test_client())               # a record made after the backup
                result = runner.invoke(args=["restore-db", snapshot, "--yes"])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("0 participant record(s)", result.output)
                self.assertEqual(self.journal_mode(app), mode.lower())
