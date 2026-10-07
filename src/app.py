"""Application factory: wires the presentation, application, and data tiers."""

import logging
from pathlib import Path

from flask import Flask, render_template

from src import __version__, db
from src.config import Config
from src.modules import assessment, consent, learning, practice, results
from src.modules.scoring import CUE_LABELS
from src.modules.security import (
    apply_security_headers, current_participant, get_csrf_token, verify_csrf,
)

ERROR_TITLES = {
    400: "Request not accepted",
    404: "Page not found",
    405: "Action not allowed",
    500: "Something went wrong",
}


def create_app(test_config=None):
    """Create and configure a PhishAware application instance."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    for module in (consent, assessment, learning, practice, results):
        app.register_blueprint(module.bp)

    # M8 is cross-cutting: it runs before and after every request.
    app.before_request(verify_csrf)
    app.after_request(apply_security_headers)

    @app.context_processor
    def inject_template_globals():
        return {
            "csrf_token": get_csrf_token,
            "app_version": __version__,
            "cue_labels": CUE_LABELS,
            "session_participant": current_participant(),
        }

    _register_error_handlers(app)
    _configure_logging(app)

    with app.app_context():
        if not db.database_ready():
            db.init_db()
    return app


def _register_error_handlers(app):
    """Friendly error pages that never expose stack traces or internals."""
    def handle_error(error):
        code = getattr(error, "code", None) or 500
        description = getattr(error, "description", "") if code != 500 else ""
        return render_template(
            "error.html",
            code=code,
            title=ERROR_TITLES.get(code, "Error"),
            description=description,
        ), code

    for code in ERROR_TITLES:
        app.register_error_handler(code, handle_error)


def _configure_logging(app):
    """Log application events only: request logs would record IP addresses (NFR-11)."""
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.logger.setLevel(logging.INFO)
