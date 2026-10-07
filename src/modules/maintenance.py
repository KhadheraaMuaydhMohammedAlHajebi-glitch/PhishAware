"""M8 data-protection jobs: retention and encrypted backups (NFR-05, NFR-11, NFR-12).

    flask --app src.app purge-expired        delete records older than the retention period
    flask --app src.app backup-db            write an encrypted snapshot of the database
    flask --app src.app restore-db FILE      replace the database with a snapshot

The jobs are command-line tools so that the host's scheduler can run them (a
weekly backup, a daily purge) without any web route that could be abused.

A backup is a consistent snapshot taken through SQLite's online backup API, so it
is safe while participants are using the system. The snapshot is serialized in
memory and encrypted with Fernet (AES-128 in CBC mode with an HMAC-SHA256
integrity check) before anything is written, so the backup folder never holds
readable data. The key comes from PHISHAWARE_BACKUP_KEY and is never stored with
the backups.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from src import db, repository

BACKUP_PATTERN = "phishaware-*.db.enc"
REQUIRED_TABLES = {"participant", "scenario", "attempt", "response", "sus_response"}


# Retention (NFR-12) ------------------------------------------------------------
def retention_cutoff(days, now=None):
    """ISO-8601 time before which a consent record has outlived the retention period."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).isoformat(timespec="seconds")


def purge_expired(days, now=None, dry_run=False):
    """Delete participants who consented more than `days` days ago. Returns the count.

    Deleting the participant row removes the attempts, answers, and survey
    ratings through ON DELETE CASCADE, exactly as a withdrawal does (FR-10).
    """
    cutoff = retention_cutoff(days, now)
    count = repository.participants_before(cutoff)
    if count and not dry_run:
        repository.delete_participants_before(cutoff)
    return count


# Encrypted backups (NFR-05, NFR-11) ---------------------------------------------
def _cipher():
    """Build the Fernet cipher from the configured key, or stop with a clear message."""
    # Imported here so that web workers, which never encrypt, do not load the library.
    from cryptography.fernet import Fernet

    key = current_app.config.get("BACKUP_KEY")
    if not key:
        raise click.ClickException(
            "PHISHAWARE_BACKUP_KEY is not set, so no backup was written. Generate a key with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key)
    except (ValueError, TypeError) as error:
        raise click.ClickException(
            "PHISHAWARE_BACKUP_KEY is not a valid Fernet key."
        ) from error


def backup_directory():
    """Configured backup folder; by default a 'backups' folder beside the database."""
    configured = current_app.config.get("BACKUP_DIR")
    if configured:
        return Path(configured)
    return Path(current_app.config["DATABASE"]).parent / "backups"


def create_backup(directory, keep, now=None):
    """Write one encrypted snapshot, then keep only the newest `keep`. Returns its path."""
    cipher = _cipher()
    snapshot = sqlite3.connect(":memory:")
    try:
        db.get_db().backup(snapshot)
        token = cipher.encrypt(snapshot.serialize())
    finally:
        snapshot.close()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"phishaware-{stamp}.db.enc"
    # O_EXCL never overwrites an earlier backup; 0o600 lets only the owner read it.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(token)
    for old in sorted(directory.glob(BACKUP_PATTERN))[:-keep]:
        old.unlink()
    return path


def restore_backup(path):
    """Decrypt a snapshot, verify it, and copy it over the live database.

    The live database is replaced only after the snapshot has passed the
    authenticity check (Fernet), SQLite's integrity check, and a schema check.
    Returns the number of participant records restored.
    """
    from cryptography.fernet import InvalidToken

    cipher = _cipher()
    try:
        plaintext = cipher.decrypt(Path(path).read_bytes())
    except InvalidToken as error:
        raise click.ClickException(
            "The backup could not be decrypted: the key is wrong or the file was altered. "
            "The database was not changed."
        ) from error
    snapshot = sqlite3.connect(":memory:")
    try:
        try:
            snapshot.deserialize(plaintext)
            healthy = snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            tables = {row[0] for row in snapshot.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError:
            healthy, tables = False, set()
        if not healthy or not REQUIRED_TABLES <= tables:
            raise click.ClickException(
                "The file decrypted correctly but is not a PhishAware database. "
                "The database was not changed."
            )
        live = db.get_db()
        snapshot.backup(live)
    finally:
        snapshot.close()
    db.ensure_schema()   # a snapshot from an older release gains the newer tables
    return repository.participant_count()


# Commands ------------------------------------------------------------------------
@click.command("purge-expired")
@click.option("--days", type=click.IntRange(min=1), default=None,
              help="Retention period in days (default: PHISHAWARE_RETENTION_DAYS, 90).")
@click.option("--dry-run", is_flag=True, help="Report what would be deleted; delete nothing.")
@with_appcontext
def purge_expired_command(days, dry_run):
    """Delete participant records that are older than the retention period."""
    days = days or current_app.config["RETENTION_DAYS"]
    count = purge_expired(days, dry_run=dry_run)
    verb = "Would delete" if dry_run else "Deleted"
    click.echo(f"{verb} {count} participant record(s) older than {days} days.")
    if count and not dry_run:
        current_app.logger.info("Retention job deleted %d expired record(s).", count)


@click.command("backup-db")
@click.option("--dir", "directory", type=click.Path(file_okay=False), default=None,
              help="Backup folder (default: PHISHAWARE_BACKUP_DIR, or 'backups' beside "
                   "the database).")
@click.option("--keep", type=click.IntRange(min=1), default=8, show_default=True,
              help="Number of newest backups to keep.")
@with_appcontext
def backup_db_command(directory, keep):
    """Write an encrypted snapshot of the database."""
    try:
        path = create_backup(directory or backup_directory(), keep)
    except FileExistsError as error:
        raise click.ClickException(
            "A backup with this timestamp already exists. Wait a second and run it again."
        ) from error
    click.echo(f"Encrypted backup written: {path} ({path.stat().st_size} bytes)")


@click.command("restore-db")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.confirmation_option(prompt="This replaces ALL current data with the backup. Continue?")
@with_appcontext
def restore_db_command(path):
    """Replace the database with an encrypted snapshot (stop the web server first)."""
    count = restore_backup(path)
    click.echo(f"Database restored from {path}: {count} participant record(s).")


def init_app(app):
    for command in (purge_expired_command, backup_db_command, restore_db_command):
        app.cli.add_command(command)
