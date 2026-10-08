"""M8 data-protection jobs: retention and encrypted backups (NFR-05, NFR-11, NFR-12).

    flask --app src.app purge-expired        delete records and backups past their period
    flask --app src.app backup-db            write an encrypted snapshot of the database
    flask --app src.app restore-db FILE      replace the database with a snapshot
    flask --app src.app run-jobs             repeat the first two, once a day by default
    flask --app src.app jobs-status          did the last pass succeed on time? (health check)

The jobs are command-line tools, so no web route exists that could be abused to
delete or copy data. In the container deployment a second service runs
"run-jobs". The consent page promises a retention period, and that promise then
does not depend on a scheduler that somebody has to remember to configure.

A backup is a consistent snapshot taken through SQLite's online backup API, so it
is safe while participants are using the system. The snapshot is serialized in
memory and encrypted with Fernet (AES-128 in CBC mode with an HMAC-SHA256
integrity check) before anything is written, so the backup folder never holds
readable data. The key comes from PHISHAWARE_BACKUP_KEY and is never stored with
the backups. A backup holds every record that existed when it was written, so
backups expire too, after PHISHAWARE_BACKUP_DAYS.
"""

import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from src import db, repository

BACKUP_PATTERN = "phishaware-*.db.enc"
BACKUP_NAME = re.compile(r"phishaware-(\d{8}T\d{6}Z)\.db\.enc")
BACKUP_STAMP = "%Y%m%dT%H%M%SZ"
# What can go wrong in a maintenance pass without being a programming error:
# a locked or damaged database, a full or read-only disk, a missing key.
JOB_ERRORS = (sqlite3.Error, OSError, click.ClickException)
RETRY_SECONDS = 5 * 60    # a failed pass is tried again this soon, not a day later
HEALTH_GRACE = 15 * 60    # how late a pass may be before the service counts as unhealthy
SQLITE_HEADER = b"SQLite format 3\x00"   # the first 16 bytes of every SQLite database file
REQUIRED_TABLES = {"participant", "scenario", "attempt", "response", "sus_response"}


# Retention (NFR-12) ------------------------------------------------------------
def utc_now():
    return datetime.now(timezone.utc)


def retention_cutoff(days, now=None):
    """ISO-8601 time before which a consent record has outlived the retention period."""
    return ((now or utc_now()) - timedelta(days=days)).isoformat(timespec="seconds")


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


def create_backup(directory, now=None):
    """Write one encrypted snapshot of the database. Returns its path."""
    cipher = _cipher()
    snapshot = sqlite3.connect(":memory:")
    try:
        db.get_db().backup(snapshot)
        token = cipher.encrypt(snapshot.serialize())
    finally:
        snapshot.close()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"phishaware-{(now or utc_now()).strftime(BACKUP_STAMP)}.db.enc"
    # O_EXCL never overwrites an earlier backup; 0o600 lets only the owner read it.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(token)
    return path


def backup_time(path):
    """The UTC time in a backup's file name, or None for any other file."""
    match = BACKUP_NAME.fullmatch(Path(path).name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), BACKUP_STAMP).replace(tzinfo=timezone.utc)


def expire_backups(directory, days, now=None, dry_run=False):
    """Delete backups written more than `days` days ago. Returns the count.

    The age comes from the file name, which this module writes, and not from the
    file system's modification time, which copying a file can change. A file
    with any other name is left alone.
    """
    cutoff = (now or utc_now()) - timedelta(days=days)
    expired = [
        path for path in sorted(Path(directory).glob(BACKUP_PATTERN))
        if (backup_time(path) or cutoff) < cutoff
    ]
    if not dry_run:
        for path in expired:
            path.unlink()
    return len(expired)


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
        healthy, tables = False, set()
        # The header is checked first because deserialize() raises MemoryError,
        # not a database error, when it is given an empty byte string.
        if plaintext.startswith(SQLITE_HEADER):
            # A snapshot of a database in write-ahead-log mode cannot be opened in
            # memory. SQLite documents the remedy: set the two file-format bytes
            # (offsets 18 and 19) to 1, which marks the image as rollback mode.
            image = plaintext[:18] + b"\x01\x01" + plaintext[20:]
            try:
                snapshot.deserialize(image)
                healthy = snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                tables = {row[0] for row in snapshot.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
            except sqlite3.DatabaseError:
                healthy = False
        if not healthy or not REQUIRED_TABLES <= tables:
            raise click.ClickException(
                "The file decrypted correctly but is not a PhishAware database. "
                "The database was not changed."
            )
        live = db.get_db()
        snapshot.backup(live)
    finally:
        snapshot.close()
    db.ensure_schema()        # a snapshot from an older release gains the newer tables
    db.apply_journal_mode()   # the copy arrives in rollback mode; restore the configured mode
    return repository.participant_count()


# One maintenance pass ---------------------------------------------------------------
def describe_purge(count, days, dry_run=False):
    verb = "Would delete" if dry_run else "Deleted"
    return f"{verb} {count} participant record(s) older than {days} days."


def describe_expiry(count, days, dry_run=False):
    verb = "Would delete" if dry_run else "Deleted"
    return f"{verb} {count} backup(s) older than {days} days."


def describe_backup(path):
    return f"Encrypted backup written: {path} ({path.stat().st_size} bytes)"


def run_jobs(now=None):
    """Purge, back up, and expire old backups once. Returns one line per step.

    The order matters. Purging first keeps an expired record out of the new
    snapshot, and expiring backups last removes the older snapshots that still
    hold it.
    """
    config = current_app.config
    lines = [describe_purge(
        purge_expired(config["RETENTION_DAYS"], now), config["RETENTION_DAYS"])]
    if config.get("BACKUP_KEY"):
        lines.append(describe_backup(create_backup(backup_directory(), now)))
    else:
        lines.append("No backup written: PHISHAWARE_BACKUP_KEY is not set.")
    expired = expire_backups(backup_directory(), config["BACKUP_RETENTION_DAYS"], now)
    lines.append(describe_expiry(expired, config["BACKUP_RETENTION_DAYS"]))
    return lines


def heartbeat_path():
    """Where the time of the last successful pass is kept: beside the database."""
    return Path(current_app.config["DATABASE"]).parent / "jobs.heartbeat"


def last_pass():
    """Time of the last successful pass, or None when none is on record."""
    try:
        return datetime.fromisoformat(heartbeat_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


# Commands ------------------------------------------------------------------------
@click.command("purge-expired")
@click.option("--days", type=click.IntRange(min=1), default=None,
              help="Retention period for records, in days (default: "
                   "PHISHAWARE_RETENTION_DAYS, 90).")
@click.option("--dry-run", is_flag=True, help="Report what would be deleted; delete nothing.")
@with_appcontext
def purge_expired_command(days, dry_run):
    """Delete participant records and backups that are past their retention period."""
    days = days or current_app.config["RETENTION_DAYS"]
    count = purge_expired(days, dry_run=dry_run)
    click.echo(describe_purge(count, days, dry_run))
    if count and not dry_run:
        current_app.logger.info("Retention job deleted %d expired record(s).", count)
    backup_days = current_app.config["BACKUP_RETENTION_DAYS"]
    expired = expire_backups(backup_directory(), backup_days, dry_run=dry_run)
    click.echo(describe_expiry(expired, backup_days, dry_run))


@click.command("backup-db")
@click.option("--dir", "directory", type=click.Path(file_okay=False), default=None,
              help="Backup folder (default: PHISHAWARE_BACKUP_DIR, or 'backups' beside "
                   "the database).")
@with_appcontext
def backup_db_command(directory):
    """Write an encrypted snapshot of the database."""
    directory = directory or backup_directory()
    try:
        path = create_backup(directory)
    except FileExistsError as error:
        raise click.ClickException(
            "A backup with this timestamp already exists. Wait a second and run it again."
        ) from error
    click.echo(describe_backup(path))
    backup_days = current_app.config["BACKUP_RETENTION_DAYS"]
    expired = expire_backups(directory, backup_days)
    if expired:
        click.echo(describe_expiry(expired, backup_days))


@click.command("restore-db")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.confirmation_option(prompt="This replaces ALL current data with the backup. Continue?")
@with_appcontext
def restore_db_command(path):
    """Replace the database with an encrypted snapshot (stop the web server first)."""
    count = restore_backup(path)
    click.echo(f"Database restored from {path}: {count} participant record(s).")


@click.command("run-jobs")
@click.option("--every", type=click.IntRange(min=60), default=None,
              help="Seconds between passes (default: PHISHAWARE_JOB_INTERVAL, one day).")
@click.option("--once", is_flag=True, help="Run one pass and exit.")
@with_appcontext
def run_jobs_command(every, once):
    """Purge expired data and write an encrypted backup, then repeat.

    The first pass runs at once, so restarting the service also enforces the
    retention period. A pass that fails is reported and tried again after five
    minutes; the loop itself keeps running. Each successful pass records its
    time, which "flask jobs-status" reads.
    """
    every = every or current_app.config["JOB_INTERVAL"]
    while True:
        started = utc_now()
        click.echo(f"Maintenance pass at {started.isoformat(timespec='seconds')}")
        delay = every
        try:
            for line in run_jobs():
                click.echo(line)
            heartbeat_path().write_text(started.isoformat(timespec="seconds"), encoding="utf-8")
        except JOB_ERRORS as error:
            if once:
                raise click.ClickException(f"The maintenance pass failed: {error}") from error
            delay = min(every, RETRY_SECONDS)
            click.echo(f"The maintenance pass failed and will be tried again in {delay} "
                       f"seconds: {error}", err=True)
        finally:
            db.close_db()   # do not hold the database file open between passes
        if once:
            return
        time.sleep(delay)


@click.command("jobs-status")
@with_appcontext
def jobs_status_command():
    """Report whether the last maintenance pass succeeded on time.

    The "jobs" container uses this as its health check, so "docker compose ps"
    shows at a glance whether the retention job is alive.
    """
    last = last_pass()
    if last is None:
        raise click.ClickException("No maintenance pass has completed yet.")
    limit = current_app.config["JOB_INTERVAL"] + HEALTH_GRACE
    stamp = last.isoformat(timespec="seconds")
    if (utc_now() - last).total_seconds() > limit:
        raise click.ClickException(
            f"The last maintenance pass succeeded at {stamp}, more than {limit} seconds ago.")
    click.echo(f"Last maintenance pass: {stamp}")


def init_app(app):
    for command in (purge_expired_command, backup_db_command, restore_db_command,
                    run_jobs_command, jobs_status_command):
        app.cli.add_command(command)
