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
JOURNAL_MODES = ("WAL", "DELETE")


def env_flag(name, default):
    """Read a 1/0 environment variable; any other value counts as off."""
    value = os.environ.get(name)
    return default if value is None else value == "1"


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
    # Write-ahead logging lets readers and one writer work at the same time, which
    # the worker processes need. Use DELETE on a network file system, where
    # SQLite's write-ahead log is not supported.
    SQLITE_JOURNAL_MODE = os.environ.get("PHISHAWARE_SQLITE_JOURNAL", "WAL").upper()

    # Content is stored as data, not code, so reviewers can edit scenarios
    # and lessons without touching application logic (NFR-07).
    SCENARIO_FILE = str(BASE_DIR / "data" / "scenarios.json")
    LESSON_FILE = str(BASE_DIR / "data" / "lessons.json")

    CONSENT_VERSION = "1.0"

    # Session-cookie hardening (NFR-08). Secure is on by default in production;
    # PHISHAWARE_COOKIE_SECURE=0 exists only for a plain-HTTP smoke test.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_flag("PHISHAWARE_COOKIE_SECURE", APP_ENV == "production")
    PERMANENT_SESSION_LIFETIME = 2 * 60 * 60  # seconds (two hours)

    # Data protection jobs (M8): records older than the retention period are
    # deleted by "flask purge-expired"; "flask backup-db" writes snapshots that
    # are encrypted with BACKUP_KEY, which is never stored beside the backups.
    RETENTION_DAYS = int(os.environ.get("PHISHAWARE_RETENTION_DAYS") or "90")
    BACKUP_KEY = os.environ.get("PHISHAWARE_BACKUP_KEY")
    BACKUP_DIR = os.environ.get("PHISHAWARE_BACKUP_DIR")

    # Administrator access (M7, NFR-10): idle sign-out and sign-in rate limit.
    ADMIN_IDLE_TIMEOUT = 15 * 60       # seconds without a request before sign-out
    ADMIN_MAX_FAILED_LOGINS = 5        # failed attempts allowed per username ...
    ADMIN_MAX_FAILED_TOTAL = 50        # ... and across all usernames ...
    ADMIN_LOCKOUT_SECONDS = 15 * 60    # ... within this many seconds
