"""M3 Learning Content: six short lessons, one per phishing-cue category (FR-04).

Lessons live in data/lessons.json so they can be edited without code changes.
"""

import json

from flask import Blueprint, current_app, g, redirect, render_template, url_for

from src.modules.progress import pretest_done
from src.modules.security import require_consent

bp = Blueprint("learning", __name__)


def load_lessons():
    with open(current_app.config["LESSON_FILE"], encoding="utf-8") as handle:
        return json.load(handle)["lessons"]


@bp.get("/learn")
@require_consent
def lessons():
    if not pretest_done(g.participant):
        return redirect(url_for("assessment.pre_item"))
    return render_template("learn.html", lessons=load_lessons())
