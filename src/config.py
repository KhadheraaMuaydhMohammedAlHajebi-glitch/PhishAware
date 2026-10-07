"""Application configuration.

Secrets and deployment-specific values are read from environment variables so
that they never have to be committed to the repository (NFR-08, NFR-12).
Development defaults allow the prototype to run with zero setup.
"""

import os
import secrets
from pathlib import Path

# Project root: the folder that contains src/, data/, and tests/.
BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    """Default configuration used for local development."""

    # If no key is supplied, a random one is generated at start-up. Sessions
    # then reset whenever the server restarts, which is acceptable locally.
    SECRET_KEY = os.environ.get("PHISHAWARE_SECRET_KEY") or secrets.token_hex(32)

    # Data tier (SQLite). The database file is git-ignored.
    DATABASE = os.environ.get(
        "PHISHAWARE_DB", str(BASE_DIR / "instance" / "phishaware.db")
    )

    # Content is stored as data, not code, so reviewers can edit scenarios
    # and lessons without touching application logic (NFR-07).
    SCENARIO_FILE = str(BASE_DIR / "data" / "scenarios.json")
    LESSON_FILE = str(BASE_DIR / "data" / "lessons.json")

    CONSENT_VERSION = "1.0"

    # Session-cookie hardening (NFR-08). SECURE must be enabled wherever the
    # application is served over HTTPS (staging and pilot environments).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("PHISHAWARE_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 2 * 60 * 60  # seconds (two hours)
