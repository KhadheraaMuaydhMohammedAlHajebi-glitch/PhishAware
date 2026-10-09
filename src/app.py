"""Application factory: wires the presentation, application, and data tiers."""

import logging
import secrets
from pathlib import Path

from flask import Flask

from src import __version__, db
from src.config import ENVIRONMENTS, JOURNAL_MODES, Config
from src.modules import (
    admin, analytics, assessment, assets, consent, display, errors, health, learning,
    maintenance, practice, results, survey,
)
from src.modules.scoring import CUE_LABELS
from src.modules.security import (
    Sha256SessionInterface, apply_security_headers, current_admin, current_participant,
    get_csrf_token, verify_csrf,
)

MIN_SECRET_LENGTH = 32
# Periods that the consent page states or the jobs rely on, with the smallest
# value each accepts: zero days would delete at once, and a job interval under
# a minute would keep the database busy.
PERIOD_SETTINGS = {
    "RETENTION_DAYS": ("PHISHAWARE_RETENTION_DAYS", 1),
    "BACKUP_RETENTION_DAYS": ("PHISHAWARE_BACKUP_DAYS", 1),
    "JOB_INTERVAL": ("PHISHAWARE_JOB_INTERVAL", 60),
}


def create_app(test_config=None):
    """Create and configure a PhishAware application instance."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    finalise_config(app)
    app.session_interface = Sha256SessionInterface()
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    maintenance.init_app(app)
    app.cli.add_command(analytics.analytics_command)
    app.cli.add_command(admin.create_admin_command)
    for module in (consent, assessment, learning, practice, results, survey, admin, health):
        app.register_blueprint(module.bp)

    # M8 is cross-cutting: it runs before and after every request.
    app.before_request(verify_csrf)
    app.after_request(apply_security_headers)
    assets.init_app(app)
    display.init_app(app)

    @app.context_processor
    def inject_template_globals():
        return {
            "csrf_token": get_csrf_token,
            "app_version": __version__,
            "cue_labels": CUE_LABELS,
            "session_participant": current_participant(),
            "session_admin": current_admin(),
            # The consent page and the closing pages state these limits, so the
            # promise shown to participants cannot drift from the configuration.
            "retention_days": app.config["RETENTION_DAYS"],
            "backup_days": app.config["BACKUP_RETENTION_DAYS"],
            "session_hours": int(app.permanent_session_lifetime.total_seconds() // 3600),
        }

    errors.init_app(app)
    _configure_logging(app)

    with app.app_context():
        if db.database_ready():
            db.ensure_schema()  # adds any table a newer release introduced
        else:
            db.init_db()
        db.apply_journal_mode()
    return app


def finalise_config(app):
    """Refuse to start with an unsafe or misspelled setting (fail fast, NFR-12).

    A wrong setting is cheapest to find when the process starts. Without this
    check, a production server with no secret key would still start: each worker
    process would sign cookies with its own random key, and participants would
    lose their session whenever a different worker answered.
    """
    config = app.config
    problems = []
    if config["APP_ENV"] not in ENVIRONMENTS:
        problems.append(f"PHISHAWARE_ENV must be one of {', '.join(ENVIRONMENTS)}")
    if config["SQLITE_JOURNAL_MODE"] not in JOURNAL_MODES:
        problems.append(f"PHISHAWARE_SQLITE_JOURNAL must be one of {', '.join(JOURNAL_MODES)}")
    for setting, (name, minimum) in PERIOD_SETTINGS.items():
        if config[setting] < minimum:
            problems.append(f"{name} must be at least {minimum}")
    if config["APP_ENV"] == "production":
        if len(config.get("SECRET_KEY") or "") < MIN_SECRET_LENGTH:
            problems.append(
                f"PHISHAWARE_SECRET_KEY must be set to at least {MIN_SECRET_LENGTH} characters")
        if app.debug:
            problems.append("debug mode must be off in production (unset FLASK_DEBUG)")
    if problems:
        raise RuntimeError("PhishAware cannot start: " + "; ".join(problems) + ".")
    if not config.get("SECRET_KEY"):
        config["SECRET_KEY"] = secrets.token_hex(32)  # development convenience only
    # Browsers accept a "__Host-" cookie only over HTTPS, for the whole site, and
    # from this exact host, so a neighbouring subdomain cannot plant or overwrite
    # it (OWASP ASVS 5.0 requirements 3.3.1 and 3.3.3). The prefix needs the
    # Secure attribute, so plain-HTTP development keeps the ordinary name.
    config["SESSION_COOKIE_NAME"] = (
        "__Host-session" if config["SESSION_COOKIE_SECURE"] else "session")


def _configure_logging(app):
    """Log application events only: request logs would record IP addresses (NFR-11)."""
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.logger.setLevel(logging.INFO)
