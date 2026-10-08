"""M8 Security & Data Protection: the cross-cutting layer used by every module.

Implements the Unit 3 safeguards informed by OWASP ASVS: anti-forgery tokens,
allowlist input validation, role-based access (participant and administrator),
hardened HTTP headers, and safe handling of the pseudonymous session identifier.
"""

import hashlib
import hmac
import re
import secrets
import time
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for
from flask.sessions import SecureCookieSessionInterface

from src import repository

ALLOWED_ANSWERS = frozenset({"phishing", "legitimate"})
SCENARIO_ID = re.compile(r"[ABP][0-9]{2}")
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
ADMIN_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}")
ADMIN_SESSION_KEYS = ("admin_user", "admin_seen", "admin_stamp")
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_MESSAGE = (
    "Your security token is missing or has expired. "
    "Go back, reload the page, and try again."
)
# OWASP ASVS 5.0 requirement 3.4.3 asks for object-src 'none' and base-uri 'none'
# as the minimum; everything else may load from this origin only.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


# Sent when a session ends, so that the browser drops anything it kept for this
# site (OWASP ASVS 5.0 requirement 14.3.1). Pages are never cached anyway
# (Cache-Control: no-store), and the application uses no browser storage.
CLEAR_SITE_DATA = '"cache", "storage"'


class Sha256SessionInterface(SecureCookieSessionInterface):
    """Sign the session cookie with HMAC-SHA-256.

    Flask's default is HMAC-SHA-1, which OWASP ASVS 5.0 lists as a legacy
    algorithm. Everything else about the signed cookie is unchanged.
    """

    digest_method = staticmethod(hashlib.sha256)


def forget_client_data(response):
    """Ask the browser to clear what it holds for this site; returns the response."""
    response.headers["Clear-Site-Data"] = CLEAR_SITE_DATA
    return response


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
    """The consenting participant linked to this session, or None.

    A session that the participant has finished is refused here, on the server.
    Clearing the cookie alone would leave any copy of it usable until it expires.
    """
    participant_id = session.get("participant_id")
    if not is_valid_uuid4(participant_id):
        return None
    participant = repository.get_participant(participant_id)
    if participant is None or participant["session_ended"]:
        return None
    return participant


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


def is_valid_admin_username(value):
    """True for 3 to 32 lowercase letters, digits, dots, underscores, or hyphens."""
    return isinstance(value, str) and ADMIN_USERNAME.fullmatch(value) is not None


def start_admin_session(account):
    """Open an administrator session in a fresh cookie (prevents session fixation)."""
    session.clear()
    session.permanent = True
    session["admin_user"] = account["username"]
    session["admin_seen"] = int(time.time())
    session["admin_stamp"] = account["session_stamp"]
    get_csrf_token()


def end_admin_session():
    """Remove the administrator's details from this browser's cookie."""
    for key in ADMIN_SESSION_KEYS:
        session.pop(key, None)


def sign_out_admin():
    """End the administrator's session here and on the server.

    The account's stamp changes, so every cookie issued for it stops working,
    including a copy taken before the sign-out.
    """
    username = session.get("admin_user")
    if isinstance(username, str):
        repository.renew_admin_stamp(username)
    end_admin_session()


def current_admin():
    """The signed-in administrator's account, or None.

    The session is valid only while the account still exists, its stamp has
    not changed since sign-in (it changes with the password and at sign-out),
    and the last request was within the idle limit (15 minutes by default,
    NFR-10).
    """
    username = session.get("admin_user")
    seen = session.get("admin_seen")
    if not isinstance(username, str) or not isinstance(seen, int):
        return None
    if time.time() - seen > current_app.config["ADMIN_IDLE_TIMEOUT"]:
        return None
    account = repository.get_admin(username)
    if account is None or account["session_stamp"] != session.get("admin_stamp"):
        return None
    return account


def require_admin(view):
    """Decorator: the wrapped route needs the administrator role (FR-11, NFR-10)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_admin()
        if account is None:
            had_session = "admin_user" in session
            end_admin_session()
            return redirect(url_for("admin.login_form", expired=1 if had_session else None))
        session["admin_seen"] = int(time.time())  # sliding idle window
        g.admin = account
        return view(*args, **kwargs)
    return wrapped


def apply_security_headers(response):
    """after_request hook: defense-in-depth headers on every response."""
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if current_app.config["SESSION_COOKIE_SECURE"]:
        # Served over HTTPS: tell browsers never to fall back to plain HTTP (NFR-08).
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response
