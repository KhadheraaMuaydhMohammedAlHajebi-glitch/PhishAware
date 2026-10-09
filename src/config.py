"""Application configuration.

Every deployment-specific value is read from an environment variable, so the
same code and the same container image run unchanged in development, in the CI
pipeline, and in the pilot (NFR-08, NFR-12). Secrets never have to be committed.
Development defaults allow the prototype to run with zero setup; production
settings are validated when the application starts (see app.finalise_config).
"""

import os
from pathlib import Path

# Project root: the folder that contains src/, data/, and tests/.
BASE_DIR = Path(__file__).resolve().parent.parent
ENVIRONMENTS = ("development", "production")
JOURNAL_MODES = ("AUTO", "WAL", "DELETE")


def env_flag(name, default):
    """Read a 1/0 environment variable; any other value counts as off."""
    value = os.environ.get(name)
    return default if value is None else value == "1"


def env_int(name, default):
    """Read a whole-number environment variable; an unreadable value stops the start-up."""
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(
            f"PhishAware cannot start: {name} must be a whole number, not {value!r}."
        ) from None


class Config:
    """Settings shared by every environment, with development defaults."""

    # "production" switches on the start-up checks and Secure cookies.
    APP_ENV = os.environ.get("PHISHAWARE_ENV", "development")

    # Signs the session cookie. Required in production. In development a random
    # key is generated at start-up, so sessions reset when the server restarts.
    SECRET_KEY = os.environ.get("PHISHAWARE_SECRET_KEY")

    # Data tier (SQLite). The database file is git-ignored.
    DATABASE = os.environ.get(
        "PHISHAWARE_DB", str(BASE_DIR / "instance" / "phishaware.db")
    )
    # Journal mode. Write-ahead logging (WAL) lets readers and the single writer
    # work at the same time; the rollback journal (DELETE) makes them take turns.
    # AUTO chooses WAL only when the SQLite library contains the fix for the
    # "WAL-reset bug" (see db.wal_is_safe) and the rollback journal otherwise.
    # Choose DELETE on a network file system, where WAL is not supported.
    SQLITE_JOURNAL_MODE = os.environ.get("PHISHAWARE_SQLITE_JOURNAL", "AUTO").upper()

    # Content is stored as data, not code, so reviewers can edit scenarios
    # and lessons without touching application logic (NFR-07).
    SCENARIO_FILE = str(BASE_DIR / "data" / "scenarios.json")
    LESSON_FILE = str(BASE_DIR / "data" / "lessons.json")

    # The version is stored with every consent record. 1.1 added the retention
    # period, the session time limit, and the optional contact line.
    CONSENT_VERSION = "1.1"
    # Whom participants can ask about the study, shown on the consent page when
    # set, for example "Researcher name, name@example.edu".
    CONTACT = os.environ.get("PHISHAWARE_CONTACT")

    # Session-cookie hardening (NFR-08). Secure is on by default in production;
    # PHISHAWARE_COOKIE_SECURE=0 exists only for a plain-HTTP smoke test.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_flag("PHISHAWARE_COOKIE_SECURE", APP_ENV == "production")
    PERMANENT_SESSION_LIFETIME = 2 * 60 * 60  # seconds (two hours)

    # Data protection jobs (M8). "flask purge-expired" deletes records that are
    # older than RETENTION_DAYS and backups that are older than
    # BACKUP_RETENTION_DAYS; the consent page states both periods. "flask
    # backup-db" writes snapshots encrypted with BACKUP_KEY, which is never
    # stored beside the backups. "flask run-jobs" repeats both every JOB_INTERVAL
    # seconds.
    RETENTION_DAYS = env_int("PHISHAWARE_RETENTION_DAYS", 90)
    BACKUP_RETENTION_DAYS = env_int("PHISHAWARE_BACKUP_DAYS", 7)
    BACKUP_KEY = os.environ.get("PHISHAWARE_BACKUP_KEY")
    BACKUP_DIR = os.environ.get("PHISHAWARE_BACKUP_DIR")
    JOB_INTERVAL = env_int("PHISHAWARE_JOB_INTERVAL", 24 * 60 * 60)

    # Administrator passwords are hashed with scrypt at N = 2^15, r = 8, p = 3,
    # one of the settings in the OWASP Password Storage Cheat Sheet. Werkzeug's
    # own default (p = 1) is weaker than any setting on that list.
    ADMIN_PASSWORD_METHOD = "scrypt:32768:8:3"
    # Digests of commonly used passwords of at least twelve characters. A new
    # administrator password is refused when it is on the list (OWASP ASVS 5.0
    # requirement 6.2.4). scripts/build_common_passwords.py builds the file.
    COMMON_PASSWORD_FILE = str(BASE_DIR / "data" / "common-passwords.sha256")

    # Administrator access (M7, NFR-10): idle sign-out, a longest session, and
    # the sign-in rate limit.
    ADMIN_IDLE_TIMEOUT = 15 * 60       # seconds without a request before sign-out
    ADMIN_MAX_SESSION = 8 * 60 * 60    # seconds after sign-in, however active
    ADMIN_MAX_FAILED_LOGINS = 5        # failed attempts allowed per username ...
    ADMIN_MAX_FAILED_TOTAL = 50        # ... and across all usernames ...
    ADMIN_LOCKOUT_SECONDS = 15 * 60    # ... within this many seconds
