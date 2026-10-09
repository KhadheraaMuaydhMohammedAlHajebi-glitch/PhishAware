"""M7 Administrator Reporting (FR-11, NFR-10, NFR-11).

The researcher signs in to see aggregate pilot results and to download a
de-identified export. Three rules shape the module:

* Authentication: passwords are stored only as salted scrypt hashes, a commonly
  used password is refused, sign-in is rate-limited without recording IP
  addresses, and a session ends after 15 idle minutes.
* Aggregation: statistics appear only when at least five participants have
  finished both assessments, so a mean can never expose one person's score.
* De-identification: the export has no random ID and no timestamp, and its rows
  are shuffled so that their order says nothing about when someone took part.
"""

import csv
import hashlib
import io
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import click
from flask import (
    Blueprint, abort, current_app, redirect, render_template, request, url_for,
)
from flask.cli import with_appcontext
from werkzeug.security import check_password_hash, generate_password_hash

from src import repository
from src.modules.results import BAR_FULL_WIDTH, CHART_LABEL_SPACE, chart_rows
from src.modules.scoring import (
    CUE_CATEGORIES, cohort_summary, cue_comparison, describe, score_attempt,
)
from src.modules.security import (
    current_admin, forget_client_data, is_valid_admin_username, require_admin,
    sign_out_admin, start_admin_session,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

MIN_REPORTABLE = 5       # complete participants needed before any statistic is shown
MIN_PASSWORD_LENGTH = 12
SUS_TARGET = 68          # objective O5: mean System Usability Scale score
GAIN_TARGET = 15         # objective O4: mean gain in percentage points
COMPLETION_TARGET = 80   # objective O3: percentage of consenting participants who finish
FAILED_MESSAGE = "The username or password is not correct."
LOCKED_MESSAGE = "Too many failed sign-in attempts. Wait 15 minutes and try again."
EXPIRED_MESSAGE = "You were signed out. Sign in again to continue."
EXPORT_COLUMNS = (
    ["record", "form_order", "pre_percent", "post_percent", "gain_points", "sus_score"]
    + [f"pre_{cue}" for cue in CUE_CATEGORIES]
    + [f"post_{cue}" for cue in CUE_CATEGORIES]
)


@lru_cache(maxsize=1)
def _decoy_hash():
    """A hash that no password matches, checked when the username is unknown.

    Verifying it takes as long as verifying a real hash, so response time does
    not reveal whether an account exists.
    """
    return hash_password(secrets.token_urlsafe(32))


def hash_password(password):
    """Salted scrypt hash with the configured cost parameters (NFR-10)."""
    return generate_password_hash(password, method=current_app.config["ADMIN_PASSWORD_METHOD"])


@lru_cache(maxsize=2)
def _common_passwords(path):
    """The digests in the file of commonly used passwords, read once."""
    with open(path, encoding="ascii") as handle:
        lines = (line.strip() for line in handle)
        return frozenset(line for line in lines if line and not line.startswith("#"))


def is_common_password(password):
    """True when the password, in any capitalisation, is on the list of common ones.

    The list holds the first 16 hex digits of the SHA-256 digest of each password
    in lower case (scripts/build_common_passwords.py).
    """
    digest = hashlib.sha256(password.lower().encode("utf-8", "replace")).hexdigest()[:16]
    return digest in _common_passwords(current_app.config["COMMON_PASSWORD_FILE"])


def _window_start():
    """Start of the rate-limit window as an ISO-8601 string."""
    seconds = current_app.config["ADMIN_LOCKOUT_SECONDS"]
    start = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return start.isoformat(timespec="seconds")


def _login_page(error=None, notice=None):
    return render_template("admin_login.html", error=error, notice=notice)


def percent_of(part, whole):
    """Whole-number percentage, or None when there is nothing to divide by."""
    return None if not whole else round(100 * part / whole)


def build_dashboard():
    """Everything the dashboard shows, computed from pseudonymous records only."""
    funnel = repository.funnel_counts()
    records = repository.cohort_records()
    complete = [r for r in records if r["pre"] is not None and r["post"] is not None]
    data = {
        "funnel": funnel,
        "completion_rate": percent_of(funnel["survey_done"], funnel["consented"]),
        "complete": len(complete),
        "minimum": MIN_REPORTABLE,
        "reportable": len(complete) >= MIN_REPORTABLE,
        "targets": {"gain": GAIN_TARGET, "sus": SUS_TARGET, "completion": COMPLETION_TARGET},
    }
    if data["reportable"]:
        sus_scores = [r["sus"] for r in complete if r["sus"] is not None]
        data.update(
            summary=cohort_summary(records),
            table=[
                ("Pre-assessment (%)", describe(r["pre"] for r in complete)),
                ("Post-assessment (%)", describe(r["post"] for r in complete)),
                ("Gain (points)", describe(r["post"] - r["pre"] for r in complete)),
            ],
            sus=describe(sus_scores) if sus_scores else None,
            sus_count=len(sus_scores),
            rows=chart_rows(cue_comparison(
                repository.cohort_responses("pre"), repository.cohort_responses("post")
            )),
            chart_width=BAR_FULL_WIDTH + CHART_LABEL_SPACE,
        )
    return data


def export_rows(records):
    """Turn de-identified records into shuffled CSV rows (lists of plain values)."""
    records = list(records)
    secrets.SystemRandom().shuffle(records)  # row order must not reveal enrolment order
    rows = []
    for number, record in enumerate(records, start=1):
        pre = score_attempt(sum(record["pre"].values()), record["pre_total"])
        post = score_attempt(sum(record["post"].values()), record["post_total"])
        rows.append(
            [number, record["form_order"], pre, post, round(post - pre, 1),
             "" if record["sus"] is None else record["sus"]]
            + [record["pre"].get(cue, 0) for cue in CUE_CATEGORIES]
            + [record["post"].get(cue, 0) for cue in CUE_CATEGORIES]
        )
    return rows


@bp.get("/login")
def login_form():
    if current_admin() is not None:
        return redirect(url_for("admin.dashboard"))
    notice = EXPIRED_MESSAGE if request.args.get("expired") == "1" else None
    return _login_page(notice=notice)


@bp.post("/login")
def login():
    username = request.form.get("username", "").strip().lower()[:64]
    password = request.form.get("password", "")
    window_start = _window_start()
    repository.clear_failed_logins(before=window_start)
    config = current_app.config
    # Two limits: one per username against guessing a password, and one across all
    # usernames, because every attempt costs a deliberately slow hash check.
    if (repository.failed_login_count(window_start, username) >= config["ADMIN_MAX_FAILED_LOGINS"]
            or repository.failed_login_count(window_start) >= config["ADMIN_MAX_FAILED_TOTAL"]):
        current_app.logger.warning("Administrator sign-in blocked by the rate limit.")
        return _login_page(error=LOCKED_MESSAGE), 429
    account = repository.get_admin(username)
    stored_hash = account["password_hash"] if account is not None else _decoy_hash()
    if not check_password_hash(stored_hash, password) or account is None:
        repository.record_failed_login(username)
        current_app.logger.warning("Administrator sign-in failed.")
        return _login_page(error=FAILED_MESSAGE), 401
    repository.clear_failed_logins(username=username)
    start_admin_session(account)
    current_app.logger.info("Administrator signed in.")
    return redirect(url_for("admin.dashboard"))


@bp.post("/logout")
def logout():
    sign_out_admin()
    return forget_client_data(redirect(url_for("admin.login_form")))


@bp.get("")
@require_admin
def dashboard():
    return render_template("admin_dashboard.html", **build_dashboard())


@bp.get("/export.csv")
@require_admin
def export_csv():
    records = repository.export_records()
    if len(records) < MIN_REPORTABLE:
        abort(403, description=(
            f"The export is available once {MIN_REPORTABLE} participants have "
            "finished both assessments."
        ))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    writer.writerows(export_rows(records))
    current_app.logger.info("De-identified export downloaded (%d records).", len(records))
    response = current_app.response_class(buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = 'attachment; filename="phishaware-export.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@click.command("create-admin")
@click.option("--username", prompt=True, help="3-32 lowercase letters, digits, . _ or -")
@click.password_option(help="At least 12 characters; typed twice, never echoed.")
@with_appcontext
def create_admin_command(username, password):
    """Create an administrator account, or replace its password."""
    username = username.strip().lower()
    if not is_valid_admin_username(username):
        raise click.BadParameter(
            "use 3 to 32 lowercase letters, digits, dots, underscores, or hyphens",
            param_hint="--username",
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise click.BadParameter(
            f"use at least {MIN_PASSWORD_LENGTH} characters", param_hint="--password"
        )
    try:
        common = is_common_password(password)
    except OSError as error:
        # Without the list the check cannot be made, and an unchecked password
        # is not accepted in its place.
        raise click.ClickException(
            "The list of commonly used passwords could not be read "
            f"({current_app.config['COMMON_PASSWORD_FILE']}), so the password was not "
            "checked and the account was not changed.") from error
    if common:
        raise click.BadParameter(
            "this is a commonly used password, which an attacker would try first; choose "
            "another, for example four unrelated words", param_hint="--password")
    created = repository.save_admin(username, hash_password(password))
    action = "created" if created else "updated; earlier sessions are signed out"
    click.echo(f"Administrator '{username}' {action}.")
