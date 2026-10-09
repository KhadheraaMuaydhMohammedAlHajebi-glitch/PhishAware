"""Tests for the M8 data-protection jobs: retention and encrypted backups.

Black-box tests run the command-line tools and inspect the database and the
backup folder. White-box tests fix the clock to check both retention boundaries
(records and backups) and replace the timer to check the repeating job.
"""

import base64
import os
import re
import sqlite3
import stat
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.db import get_db
from src.modules import maintenance
from tests.helpers import BACKUP_MARKER, AppTestCase, new_backup_key, open_backup, seal_backup

SUS = [4, 2, 5, 1, 4, 2, 5, 2, 4, 1]
NOON = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


class RetentionTests(AppTestCase):
    def set_consent_time(self, participant_id, when):
        with self.app.app_context():
            get_db().execute(
                "UPDATE participant SET consented_at = ? WHERE id = ?",
                (when.isoformat(timespec="seconds"), participant_id))
            get_db().commit()

    def age(self, participant_id, days):
        self.set_consent_time(participant_id, datetime.now(timezone.utc) - timedelta(days=days))

    def purge(self, *args):
        return self.app.test_cli_runner().invoke(args=["purge-expired", *args])

    def test_expired_participant_and_every_linked_record_are_deleted(self):
        expired = self.complete_session(6, 9, SUS)
        recent = self.complete_session(7, 10, SUS)
        self.age(expired, 91)
        result = self.purge()
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Deleted 1 participant record(s) older than 90 days.", result.output)
        self.assertEqual(
            [row["id"] for row in self.query("SELECT id FROM participant")], [recent])
        self.assertEqual(self.count("attempt"), 3)       # pre, practice, and post of one person
        self.assertEqual(self.count("response"), 30)     # 12 + 6 + 12 answers
        self.assertEqual(self.count("sus_response"), 1)
        self.assertEqual(self.count("lesson_view"), 1)
        self.assertEqual(self.count("scenario"), 30)     # the scenario bank is untouched

    def test_expired_participant_who_finished_leaves_no_trace(self):
        client = self.app.test_client()
        participant_id = self.reach_posttest(client)
        self.answer_posttest(client=client)
        client.post("/survey", data=self.survey_data(SUS, client))
        client.post("/finish", data={"csrf_token": self.token(client)})
        self.assertEqual(self.count("session_end"), 1)
        self.age(participant_id, 91)
        self.purge()
        for table in ("participant", "attempt", "response", "sus_response", "lesson_view",
                      "session_end"):
            self.assertEqual(self.count(table), 0, table)

    def test_record_exactly_at_the_cutoff_is_kept_and_one_second_older_is_deleted(self):
        at_cutoff = self.consent()
        just_past = self.consent(self.app.test_client())
        self.set_consent_time(at_cutoff, NOON - timedelta(days=90))
        self.set_consent_time(just_past, NOON - timedelta(days=90, seconds=1))
        with self.app.app_context():
            self.assertEqual(maintenance.purge_expired(90, now=NOON), 1)
        self.assertEqual(
            [row["id"] for row in self.query("SELECT id FROM participant")], [at_cutoff])

    def test_dry_run_reports_the_count_and_deletes_nothing(self):
        self.age(self.consent(), 120)
        result = self.purge("--dry-run")
        self.assertIn("Would delete 1 participant record(s) older than 90 days.", result.output)
        self.assertEqual(self.count("participant"), 1)

    def test_days_option_overrides_the_configured_period(self):
        self.age(self.consent(), 31)
        self.assertIn("Deleted 0 participant record(s) older than 90 days.", self.purge().output)
        self.assertIn(
            "Deleted 1 participant record(s) older than 30 days.",
            self.purge("--days", "30").output)
        self.assertEqual(self.count("participant"), 0)

    def test_configured_period_is_used_by_default(self):
        self.app.config["RETENTION_DAYS"] = 7
        self.age(self.consent(), 8)
        self.assertIn("older than 7 days", self.purge().output)
        self.assertEqual(self.count("participant"), 0)

    def test_a_period_shorter_than_one_day_is_rejected(self):
        self.consent()
        self.assertEqual(self.purge("--days", "0").exit_code, 2)
        self.assertEqual(self.count("participant"), 1)


class BackupCase(AppTestCase):
    """A fresh application with a backup key and an empty backup folder."""

    def setUp(self):
        super().setUp()
        self.key = new_backup_key()
        self.folder = Path(self._tmp.name) / "backups"
        self.app.config["BACKUP_KEY"] = self.key
        self.app.config["BACKUP_DIR"] = str(self.folder)

    def run_command(self, *args, **kwargs):
        return self.app.test_cli_runner().invoke(args=list(args), **kwargs)

    def backup(self):
        result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 0, result.output)
        return next(self.folder.glob("*.enc"))

    def write_backup(self, when):
        with self.app.app_context():
            return maintenance.create_backup(self.folder, now=when)

    def names(self):
        return sorted(path.name for path in self.folder.iterdir())


class BackupTests(BackupCase):
    def test_backup_file_is_encrypted_and_decrypts_to_a_database(self):
        participant_id = self.complete_session(6, 9, SUS)
        path = self.backup()
        self.assertRegex(path.name, r"^phishaware-\d{8}T\d{6}Z\.db\.enc$")
        stored = path.read_bytes()
        self.assertTrue(stored.startswith(BACKUP_MARKER))
        self.assertNotIn(b"SQLite format 3", stored)
        self.assertNotIn(participant_id.encode(), stored)
        plain = open_backup(self.key, stored)
        self.assertTrue(plain.startswith(b"SQLite format 3\x00"))
        self.assertIn(participant_id.encode(), plain)
        # Marker, 12-byte nonce, and 16-byte tag are the only overhead.
        self.assertEqual(len(stored), len(BACKUP_MARKER) + 12 + len(plain) + 16)

    def test_two_backups_of_the_same_data_share_no_nonce_and_no_ciphertext(self):
        first = self.write_backup(NOON).read_bytes()
        second = self.write_backup(NOON + timedelta(seconds=1)).read_bytes()
        start = len(BACKUP_MARKER)
        self.assertNotEqual(first[start:start + 12], second[start:start + 12])
        self.assertNotEqual(first[start + 12:start + 76], second[start + 12:start + 76])
        self.assertEqual(open_backup(self.key, first), open_backup(self.key, second))

    @unittest.skipIf(os.name == "nt", "POSIX file permissions")
    def test_backup_file_is_readable_by_its_owner_only(self):
        path = self.backup()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_backup_refuses_to_write_without_a_key(self):
        self.app.config["BACKUP_KEY"] = None
        result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("PHISHAWARE_BACKUP_KEY is not set, so no backup was written", result.output)
        self.assertFalse(self.folder.exists())

    def test_malformed_key_is_reported(self):
        too_short = new_backup_key()[:22] + "=="               # 16 bytes: an AES-128 key
        not_base64 = "!" + new_backup_key()[1:]
        for key in ("not-a-key", too_short, not_base64, "clé-secrète"):
            self.app.config["BACKUP_KEY"] = key
            result = self.run_command("backup-db")
            self.assertEqual(result.exit_code, 1, key)
            self.assertIn("it must be 32 random bytes in URL-safe base64", result.output)
        self.assertFalse(self.folder.exists())

    def test_default_folder_is_beside_the_database(self):
        self.app.config["BACKUP_DIR"] = None
        result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 0, result.output)
        beside = Path(self.app.config["DATABASE"]).parent / "backups"
        self.assertEqual(len(list(beside.glob("phishaware-*.db.enc"))), 1)

    def test_restore_brings_back_participants_answers_and_accounts(self):
        participant_id = self.complete_session(6, 9, SUS)
        self.create_admin()
        path = self.backup()
        self.run_command("reset-db", "--yes")                 # simulate losing the data
        self.assertEqual(self.count("participant"), 0)
        result = self.run_command("restore-db", str(path), "--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("1 participant record(s)", result.output)
        self.assertEqual(self.query("SELECT id FROM participant")[0]["id"], participant_id)
        self.assertEqual(self.count("response"), 30)
        self.assertEqual(self.count("sus_response"), 1)
        self.assertEqual(self.count("admin_user"), 1)
        self.assertEqual(self.admin_sign_in().status_code, 302)   # the restored account works

    def test_restore_asks_for_confirmation(self):
        path = self.backup()
        self.consent()
        result = self.run_command("restore-db", str(path), input="n\n")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(self.count("participant"), 1)        # declined: nothing replaced

    def test_wrong_key_changes_nothing(self):
        path = self.backup()
        self.consent()
        self.app.config["BACKUP_KEY"] = new_backup_key()
        result = self.run_command("restore-db", str(path), "--yes")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("could not be decrypted", result.output)
        self.assertEqual(self.count("participant"), 1)

    def test_altered_backup_is_rejected_and_changes_nothing(self):
        path = self.backup()
        self.consent()
        original = path.read_bytes()
        flipped = bytearray(original)
        flipped[len(flipped) // 2] ^= 0x01                    # one bit in the ciphertext
        damaged = {
            "one bit flipped": bytes(flipped),
            "last byte missing": original[:-1],
            "cut to the header": original[:len(BACKUP_MARKER) + 12],
            "marker of another version": original.replace(b"BACKUP-1", b"BACKUP-2", 1),
            "no marker": original[len(BACKUP_MARKER):],
            "empty": b"",
        }
        for name, content in damaged.items():
            path.write_bytes(content)
            result = self.run_command("restore-db", str(path), "--yes")
            self.assertEqual(result.exit_code, 1, name)
            self.assertIn("the key is wrong or the file was altered", result.output, name)
        self.assertEqual(self.count("participant"), 1)

    def test_marker_is_authenticated_with_the_data(self):
        blob = self.write_backup(NOON).read_bytes()
        nonce, sealed = blob[len(BACKUP_MARKER):][:12], blob[len(BACKUP_MARKER):][12:]
        cipher = AESGCM(base64.urlsafe_b64decode(self.key))
        self.assertTrue(
            cipher.decrypt(nonce, sealed, BACKUP_MARKER).startswith(b"SQLite format 3"))
        with self.assertRaises(InvalidTag):   # the right key and nonce, another marker
            cipher.decrypt(nonce, sealed, b"PHISHAWARE-BACKUP-2\n")

    def test_encrypted_file_that_is_not_a_database_is_rejected(self):
        self.consent()
        truncated = b"SQLite format 3\x00" + b"\x00" * 200    # a header with no valid pages
        for content in (b"not a database at all", b"", truncated):
            impostor = Path(self._tmp.name) / "impostor.enc"
            impostor.write_bytes(seal_backup(self.key, content))
            result = self.run_command("restore-db", str(impostor), "--yes")
            self.assertEqual(result.exit_code, 1)
            self.assertIn("is not a PhishAware database", result.output)
        self.assertEqual(self.count("participant"), 1)

    def test_missing_backup_file_is_a_usage_error(self):
        result = self.run_command("restore-db", str(self.folder / "absent.enc"), "--yes")
        self.assertEqual(result.exit_code, 2)

    def test_backup_in_the_same_second_is_refused_rather_than_overwritten(self):
        with self.app.app_context():
            first = maintenance.create_backup(self.folder, now=NOON)
            original = first.read_bytes()
            with self.assertRaises(FileExistsError):
                maintenance.create_backup(self.folder, now=NOON)
        self.assertEqual(first.read_bytes(), original)
        with mock.patch.object(maintenance, "create_backup", side_effect=FileExistsError):
            result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("already exists", result.output)

    def test_backup_name_carries_its_time(self):
        with self.app.app_context():
            path = maintenance.create_backup(self.folder, now=NOON)
        self.assertTrue(re.fullmatch(r"phishaware-20261008T120000Z\.db\.enc", path.name))
        self.assertEqual(maintenance.backup_time(path), NOON)
        self.assertIsNone(maintenance.backup_time(self.folder / "phishaware-notes.db.enc"))


class BackupExpiryTests(BackupCase):
    """Backups hold participant records, so they expire as well (NFR-12)."""

    def test_backup_exactly_at_the_cutoff_is_kept_and_one_second_older_is_deleted(self):
        self.write_backup(NOON - timedelta(days=7, seconds=1))
        at_cutoff = self.write_backup(NOON - timedelta(days=7))
        newest = self.write_backup(NOON)
        self.assertEqual(maintenance.expire_backups(self.folder, 7, now=NOON), 1)
        self.assertEqual(self.names(), [at_cutoff.name, newest.name])

    def test_expiry_leaves_other_files_alone(self):
        self.write_backup(NOON - timedelta(days=30))
        (self.folder / "phishaware-notes.db.enc").write_bytes(b"not written by the job")
        (self.folder / "README.txt").write_text("keys are kept elsewhere")
        self.assertEqual(maintenance.expire_backups(self.folder, 7, now=NOON), 1)
        self.assertEqual(self.names(), ["README.txt", "phishaware-notes.db.enc"])

    def test_dry_run_counts_expired_backups_and_deletes_nothing(self):
        old = self.write_backup(NOON - timedelta(days=8))
        self.assertEqual(maintenance.expire_backups(self.folder, 7, now=NOON, dry_run=True), 1)
        self.assertTrue(old.exists())

    def test_purge_command_deletes_expired_backups_as_well(self):
        old = self.write_backup(datetime.now(timezone.utc) - timedelta(days=8))
        result = self.run_command("purge-expired", "--dry-run")
        self.assertIn("Would delete 1 backup(s) older than 7 days.", result.output)
        self.assertTrue(old.exists())
        result = self.run_command("purge-expired")
        self.assertIn("Deleted 1 backup(s) older than 7 days.", result.output)
        self.assertFalse(old.exists())

    def test_backup_command_removes_expired_backups(self):
        self.write_backup(datetime.now(timezone.utc) - timedelta(days=8))
        result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Deleted 1 backup(s) older than 7 days.", result.output)
        self.assertEqual(len(self.names()), 1)

    def test_configured_backup_period_is_used(self):
        self.app.config["BACKUP_RETENTION_DAYS"] = 2
        self.write_backup(datetime.now(timezone.utc) - timedelta(days=3))
        result = self.run_command("purge-expired")
        self.assertIn("Deleted 1 backup(s) older than 2 days.", result.output)
        self.assertEqual(self.names(), [])


class StopLoop(Exception):
    """Raised by the replaced timer to end the otherwise endless job loop."""


class JobTests(BackupCase):
    """The repeating job that the "jobs" service runs (flask run-jobs)."""

    def test_one_pass_purges_then_backs_up_then_expires(self):
        expired = self.complete_session(6, 9, SUS)
        recent = self.complete_session(7, 10, SUS)
        now = datetime.now(timezone.utc)
        with self.app.app_context():
            get_db().execute(
                "UPDATE participant SET consented_at = ? WHERE id = ?",
                ((now - timedelta(days=91)).isoformat(timespec="seconds"), expired))
            get_db().commit()
        old = self.write_backup(now - timedelta(days=8))
        result = self.run_command("run-jobs", "--once")
        self.assertEqual(result.exit_code, 0, result.output)
        lines = result.output.splitlines()
        self.assertTrue(lines[0].startswith("Maintenance pass at 20"))
        self.assertEqual(lines[1], "Deleted 1 participant record(s) older than 90 days.")
        self.assertEqual(lines[2], "Deleted 0 failed sign-in record(s) older than 15 minutes.")
        self.assertTrue(lines[3].startswith("Encrypted backup written: "))
        self.assertEqual(lines[4], "Deleted 1 backup(s) older than 7 days.")
        self.assertFalse(old.exists())
        # The purge ran first, so the new snapshot no longer holds the expired record.
        (snapshot,) = self.folder.glob("*.enc")
        plain = open_backup(self.key, snapshot.read_bytes())
        self.assertNotIn(expired.encode(), plain)
        self.assertIn(recent.encode(), plain)

    def test_pass_forgets_failed_sign_ins_that_are_older_than_the_rate_limit_window(self):
        # Defect D-4: only the sign-in form removed them, so without a sign-in
        # they stayed in the database and went into every backup.
        self.create_admin()
        for name in ("guess-one", "guess-two", "guess-three"):
            self.assertEqual(self.admin_sign_in(username=name).status_code, 401)
        just_inside = datetime.now(timezone.utc) - timedelta(minutes=14)
        with self.app.app_context():
            get_db().execute(
                "UPDATE admin_login_attempt SET attempted_at = '2026-01-01T00:00:00+00:00' "
                "WHERE id < 3")
            get_db().execute("UPDATE admin_login_attempt SET attempted_at = ? WHERE id = 3",
                             (just_inside.isoformat(timespec="seconds"),))
            get_db().commit()
        result = self.run_command("run-jobs", "--once")
        self.assertIn("Deleted 2 failed sign-in record(s) older than 15 minutes.", result.output)
        self.assertEqual(self.count("admin_login_attempt"), 1)    # the recent one still counts
        # The snapshot was written after the records were removed.
        (snapshot,) = self.folder.glob("*.enc")
        copy = sqlite3.connect(":memory:")
        copy.deserialize(open_backup(self.key, snapshot.read_bytes()))
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM admin_login_attempt").fetchone()[0], 1)
        copy.close()

    def test_pass_without_a_key_still_purges_and_reports_the_missing_backup(self):
        self.app.config["BACKUP_KEY"] = None
        result = self.run_command("run-jobs", "--once")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Deleted 0 participant record(s) older than 90 days.", result.output)
        self.assertIn("No backup written: PHISHAWARE_BACKUP_KEY is not set.", result.output)
        self.assertFalse(self.folder.exists())

    def test_loop_waits_for_the_configured_interval_between_passes(self):
        self.app.config["JOB_INTERVAL"] = 3600
        # The replaced timer does not wait, so both passes fall in the same second.
        # Without a key no backup is written, and the two cannot collide.
        self.app.config["BACKUP_KEY"] = None
        with mock.patch.object(maintenance.time, "sleep", side_effect=[None, StopLoop]) as sleep:
            result = self.run_command("run-jobs")
        self.assertIsInstance(result.exception, StopLoop)
        self.assertEqual(sleep.call_args_list, [mock.call(3600), mock.call(3600)])
        self.assertEqual(result.output.count("Maintenance pass at"), 2)

    def test_failed_pass_is_reported_and_the_loop_carries_on(self):
        outcomes = [sqlite3.OperationalError("database is locked"), ["Second pass succeeded."]]
        with mock.patch.object(maintenance, "run_jobs", side_effect=outcomes), \
                mock.patch.object(maintenance.time, "sleep", side_effect=[None, StopLoop]) as sleep:
            result = self.run_command("run-jobs", "--every", "60")
        self.assertIsInstance(result.exception, StopLoop)
        self.assertEqual(sleep.call_args_list, [mock.call(60), mock.call(60)])
        self.assertIn(
            "The maintenance pass failed and will be tried again in 60 seconds: "
            "database is locked", result.output)
        self.assertIn("Second pass succeeded.", result.output)

    def test_failed_pass_is_tried_again_after_five_minutes_not_a_day(self):
        outcomes = [OSError("disk full"), ["Second pass succeeded."]]
        with mock.patch.object(maintenance, "run_jobs", side_effect=outcomes), \
                mock.patch.object(maintenance.time, "sleep", side_effect=[None, StopLoop]) as sleep:
            result = self.run_command("run-jobs", "--every", "86400")
        self.assertEqual(sleep.call_args_list, [mock.call(300), mock.call(86400)])
        self.assertIn("will be tried again in 300 seconds: disk full", result.output)

    def test_failed_single_pass_exits_with_an_error(self):
        with mock.patch.object(maintenance, "run_jobs", side_effect=OSError("disk full")):
            result = self.run_command("run-jobs", "--once")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("The maintenance pass failed: disk full", result.output)

    def test_interval_shorter_than_a_minute_is_rejected(self):
        with mock.patch.object(maintenance.time, "sleep", side_effect=StopLoop) as sleep:
            result = self.run_command("run-jobs", "--every", "59")
        self.assertEqual(result.exit_code, 2)
        sleep.assert_not_called()


class JobStatusTests(BackupCase):
    """The health check of the jobs service (flask jobs-status)."""

    def heartbeat(self):
        return Path(self.app.config["DATABASE"]).parent / "jobs.heartbeat"

    def set_last_pass(self, seconds_ago):
        moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
        self.heartbeat().write_text(moment.isoformat(timespec="seconds"), encoding="utf-8")

    def test_no_pass_on_record_is_unhealthy(self):
        result = self.run_command("jobs-status")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("No maintenance pass has completed yet.", result.output)

    def test_successful_pass_is_recorded_and_reported(self):
        self.assertEqual(self.run_command("run-jobs", "--once").exit_code, 0)
        result = self.run_command("jobs-status")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.output, r"Last maintenance pass: 20\d\d-\d\d-\d\dT")

    def test_failed_pass_leaves_no_record(self):
        with mock.patch.object(maintenance, "run_jobs", side_effect=OSError("disk full")):
            self.run_command("run-jobs", "--once")
        self.assertFalse(self.heartbeat().exists())
        self.assertEqual(self.run_command("jobs-status").exit_code, 1)

    def test_pass_may_be_fifteen_minutes_late_and_no_later(self):
        self.app.config["JOB_INTERVAL"] = 3600
        self.set_last_pass(3600 + 14 * 60)
        self.assertEqual(self.run_command("jobs-status").exit_code, 0)
        self.set_last_pass(3600 + 16 * 60)
        result = self.run_command("jobs-status")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("more than 4500 seconds ago", result.output)

    def test_unreadable_record_counts_as_no_pass(self):
        self.heartbeat().write_text("not a time", encoding="utf-8")
        self.assertEqual(self.run_command("jobs-status").exit_code, 1)
