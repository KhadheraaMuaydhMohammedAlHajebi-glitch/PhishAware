"""M8 Security & Data Protection: the cross-cutting layer used by every module.

Implements the Unit 3 safeguards informed by OWASP ASVS: anti-forgery tokens,
allowlist input validation, consent gating, hardened HTTP headers, and safe
handling of the pseudonymous session identifier.
"""

import hmac
import re
import secrets
from functools import wraps

from flask import abort, g, redirect, request, session, url_for

from src import repository

ALLOWED_ANSWERS = frozenset({"phishing", "legitimate"})
SCENARIO_ID = re.compile(r"[ABP][0-9]{2}")
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_MESSAGE = (
    "Your security token is missing or has expired. "
    "Go back, reload the page, and try again."
)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
)


def get_csrf_token():
    """Return this session's anti-forgery token, creating one if needed."""
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def verify_csrf():
    """before_request hook: reject state-changing requests without a valid token."""
    if request.method not in STATE_CHANGING_METHODS:
        return
    sent = request.form.get("csrf_token", "")
    expected = session.get("_csrf_token", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description=CSRF_MESSAGE)


def validate_answer(value):
    """Allowlist validation for a classification answer."""
    if value not in ALLOWED_ANSWERS:
        abort(400, description="That answer is not one of the allowed choices.")
    return value


def validate_scenario_id(value):
    """Pattern validation for scenario identifiers such as A01 or P06."""
    if not isinstance(value, str) or not SCENARIO_ID.fullmatch(value):
        abort(400, description="That scenario identifier is not valid.")
    return value


def is_valid_uuid4(value):
    """True for a canonical lowercase UUID4 string."""
    return isinstance(value, str) and UUID4.fullmatch(value) is not None


def current_participant():
    """The consenting participant linked to this session, or None."""
    participant_id = session.get("participant_id")
    if not is_valid_uuid4(participant_id):
        return None
    return repository.get_participant(participant_id)


def require_consent(view):
    """Decorator: the wrapped route is reachable only after consent (FR-01)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        participant = current_participant()
        if participant is None:
            session.pop("participant_id", None)
            return redirect(url_for("consent.consent_form"))
        g.participant = participant
        return view(*args, **kwargs)
    return wrapped


def apply_security_headers(response):
    """after_request hook: defense-in-depth headers on every response."""
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response
