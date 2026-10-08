"""Health endpoint for the container platform and the reverse proxy.

    GET /healthz  ->  200 {"status": "ok", ...} when the database answers
                      503 {"status": "unavailable"} when it does not

Docker uses the endpoint to decide whether the container is healthy, and the
deployment steps wait for it before sending traffic to a new version. It reads
no session, sets no cookie, and returns no participant data.
"""

import sqlite3

from flask import Blueprint, current_app, jsonify

from src import __version__, repository

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz():
    try:
        scenarios = repository.scenario_count()
    except sqlite3.Error:
        current_app.logger.error("Health check failed: the database did not answer.")
        response = jsonify(status="unavailable")
        response.status_code = 503
    else:
        response = jsonify(status="ok", version=__version__, scenarios=scenarios)
    response.headers["Cache-Control"] = "no-store"
    return response
