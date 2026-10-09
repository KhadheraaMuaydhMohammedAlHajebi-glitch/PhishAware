"""M1 Consent & Session (FR-01, FR-02, FR-10).

No data is written until an adult participant explicitly consents. Consent
creates a random UUID4 identifier; no name, email address, student number,
or IP address is ever requested or stored. Withdrawal, offered on every page,
permanently deletes all linked records through ON DELETE CASCADE. Finishing
ends the session in the browser and keeps the pseudonymous records.
"""

import uuid

from flask import (
    Blueprint, current_app, g, make_response, redirect, render_template, request, session,
    url_for,
)

from src import repository
from src.modules.progress import build_progress
from src.modules.scoring import assign_form_order
from src.modules.security import (
    current_participant, forget_client_data, get_csrf_token, require_consent,
)

bp = Blueprint("consent", __name__)


@bp.get("/")
def index():
    if current_participant() is not None:
        return redirect(url_for("consent.dashboard"))
    return redirect(url_for("consent.consent_form"))


def _consent_page(error=None, ended=False):
    return render_template(
        "consent.html",
        version=current_app.config["CONSENT_VERSION"],
        contact=current_app.config["CONTACT"],
        error=error,
        ended=ended,
    )


@bp.get("/consent")
def consent_form():
    if current_participant() is not None:
        return redirect(url_for("consent.dashboard"))
    # A step that was opened without a session sends the reader here with
    # "ended" (security.require_consent), and the page then explains why.
    return _consent_page(ended=request.args.get("ended") == "1")


@bp.post("/consent")
def give_consent():
    if current_participant() is not None:
        # A consent form sent in a live session, for example twice in a row: the
        # participant has a record already. A second one would leave the first
        # where nobody could continue or withdraw it.
        return redirect(url_for("consent.dashboard"))
    adult = request.form.get("adult") == "yes"
    agreed = request.form.get("agree") == "yes"
    if not (adult and agreed):
        # FR-01: without both confirmations nothing is stored.
        return _consent_page(
            "Confirm that you are 18 or older and that you agree to take part."), 400
    participant_id = str(uuid.uuid4())  # FR-02: random and non-identifying
    order = repository.create_participant(
        participant_id, current_app.config["CONSENT_VERSION"], assign_form_order
    )
    session.clear()  # a fresh session on consent prevents session fixation
    session.permanent = True
    session["participant_id"] = participant_id
    get_csrf_token()
    current_app.logger.info("Consent recorded; counterbalanced form order %s.", order)
    return redirect(url_for("consent.dashboard"))


@bp.get("/consent/declined")
def declined():
    return render_template("declined.html")


@bp.get("/dashboard")
@require_consent
def dashboard():
    return render_template("dashboard.html", progress=build_progress(g.participant))


@bp.get("/withdraw")
@require_consent
def withdraw_confirm():
    return render_template("withdraw.html")


@bp.post("/withdraw")
@require_consent
def withdraw():
    repository.delete_participant(g.participant["id"])  # FR-10
    session.clear()
    current_app.logger.info("Participant withdrew; all linked records deleted.")
    return forget_client_data(make_response(render_template("withdrawn.html")))


@bp.post("/finish")
@require_consent
def finish():
    """End the session; the pseudonymous records are kept.

    On a shared computer the session cookie would otherwise let the next person
    open this participant's results for up to two hours. The session is ended
    on the server as well, so a copy of the cookie is refused too. Finishing is
    offered after the last step, because it also ends the chance to resume or
    withdraw.
    """
    if repository.get_sus(g.participant["id"]) is None:
        return redirect(url_for("consent.dashboard"))
    repository.end_session(g.participant["id"])
    session.clear()
    current_app.logger.info("Participant finished; the session was ended.")
    return forget_client_data(make_response(render_template("finished.html")))
