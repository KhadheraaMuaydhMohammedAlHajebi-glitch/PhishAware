"""Tests for the M8 data-protection jobs: retention and encrypted backups.

Black-box tests run the command-line tools and inspect the database and the
backup folder. Two white-box tests fix the clock to check the retention boundary
and the pruning of old backups.
"""

import os
import re
import stat
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from src.db import get_db
from src.modules import maintenance
from tests.helpers import AppTestCase

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
        self.assertEqual(self.count("scenario"), 30)     # the scenario bank is untouched

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


class BackupTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.key = Fernet.generate_key().decode()
        self.folder = Path(self._tmp.name) / "backups"
        self.app.config["BACKUP_KEY"] = self.key
        self.app.config["BACKUP_DIR"] = str(self.folder)

    def run_command(self, *args, **kwargs):
        return self.app.test_cli_runner().invoke(args=list(args), **kwargs)

    def backup(self):
        result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 0, result.output)
        return next(self.folder.glob("*.enc"))

    def test_backup_file_is_encrypted_and_decrypts_to_a_database(self):
        participant_id = self.complete_session(6, 9, SUS)
        path = self.backup()
        self.assertRegex(path.name, r"^phishaware-\d{8}T\d{6}Z\.db\.enc$")
        stored = path.read_bytes()
        self.assertNotIn(b"SQLite format 3", stored)
        self.assertNotIn(participant_id.encode(), stored)
        plain = Fernet(self.key).decrypt(stored)
        self.assertTrue(plain.startswith(b"SQLite format 3\x00"))
        self.assertIn(participant_id.encode(), plain)

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
        self.app.config["BACKUP_KEY"] = "not-a-fernet-key"
        result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("not a valid Fernet key", result.output)

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
        self.app.config["BACKUP_KEY"] = Fernet.generate_key().decode()
        result = self.run_command("restore-db", str(path), "--yes")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("could not be decrypted", result.output)
        self.assertEqual(self.count("participant"), 1)

    def test_altered_backup_is_rejected_and_changes_nothing(self):
        path = self.backup()
        self.consent()
        stored = bytearray(path.read_bytes())
        stored[len(stored) // 2] ^= 0x01                      # flip one bit
        path.write_bytes(bytes(stored))
        result = self.run_command("restore-db", str(path), "--yes")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("the key is wrong or the file was altered", result.output)
        self.assertEqual(self.count("participant"), 1)

    def test_encrypted_file_that_is_not_a_database_is_rejected(self):
        self.consent()
        truncated = b"SQLite format 3\x00" + b"\x00" * 200    # a header with no valid pages
        for content in (b"not a database at all", b"", truncated):
            impostor = Path(self._tmp.name) / "impostor.enc"
            impostor.write_bytes(Fernet(self.key).encrypt(content))
            result = self.run_command("restore-db", str(impostor), "--yes")
            self.assertEqual(result.exit_code, 1)
            self.assertIn("is not a PhishAware database", result.output)
        self.assertEqual(self.count("participant"), 1)

    def test_missing_backup_file_is_a_usage_error(self):
        result = self.run_command("restore-db", str(self.folder / "absent.enc"), "--yes")
        self.assertEqual(result.exit_code, 2)

    def test_only_the_newest_backups_are_kept(self):
        with self.app.app_context():
            for hour in (9, 10, 11):
                maintenance.create_backup(self.folder, keep=2, now=NOON.replace(hour=hour))
        self.assertEqual(
            sorted(path.name for path in self.folder.glob("*.enc")),
            ["phishaware-20261008T100000Z.db.enc", "phishaware-20261008T110000Z.db.enc"])

    def test_backup_in_the_same_second_is_refused_rather_than_overwritten(self):
        with self.app.app_context():
            first = maintenance.create_backup(self.folder, keep=8, now=NOON)
            original = first.read_bytes()
            with self.assertRaises(FileExistsError):
                maintenance.create_backup(self.folder, keep=8, now=NOON)
        self.assertEqual(first.read_bytes(), original)
        with mock.patch.object(maintenance, "create_backup", side_effect=FileExistsError):
            result = self.run_command("backup-db")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("already exists", result.output)

    def test_keep_must_be_at_least_one(self):
        self.assertEqual(self.run_command("backup-db", "--keep", "0").exit_code, 2)
        self.assertFalse(self.folder.exists())

    def test_backup_name_sorts_by_time(self):
        with self.app.app_context():
            path = maintenance.create_backup(self.folder, keep=8, now=NOON)
        self.assertTrue(re.fullmatch(r"phishaware-20261008T120000Z\.db\.enc", path.name))
