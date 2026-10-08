"""Repository: every data-access function used by the modules.

Keeping SQL out of the route handlers isolates the data tier, so SQLite can be
replaced later (NFR-06), and makes it easy to verify that every query is
parameterized (NFR-09).
"""

import json
from datetime import datetime, timezone

from src.db import get_db


def utc_now():
    """Current UTC time as an ISO-8601 string with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Participants (M1) -----------------------------------------------------------
def create_participant(participant_id, consent_version, choose_order):
    """Store a consenting participant and return the counterbalanced order."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO participant (id, consent_version, consented_at, form_order) "
        "VALUES (?, ?, ?, 'AB')",
        (participant_id, consent_version, utc_now()),
    )
    order = choose_order(cursor.lastrowid)
    db.execute(
        "UPDATE participant SET form_order = ? WHERE seq = ?", (order, cursor.lastrowid)
    )
    db.commit()
    return order


def get_participant(participant_id):
    return get_db().execute(
        "SELECT seq, id, consent_version, consented_at, form_order, status "
        "FROM participant WHERE id = ?",
        (participant_id,),
    ).fetchone()


def delete_participant(participant_id):
    """Withdrawal (FR-10): ON DELETE CASCADE removes attempts and responses."""
    db = get_db()
    db.execute("DELETE FROM participant WHERE id = ?", (participant_id,))
    db.commit()


def participants_before(cutoff):
    """Number of participants whose consent is older than the cutoff time (NFR-12)."""
    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM participant WHERE consented_at < ?", (cutoff,)
    ).fetchone()
    return row["n"]


def delete_participants_before(cutoff):
    """Retention job: ON DELETE CASCADE removes every linked record, as in withdrawal."""
    db = get_db()
    db.execute("DELETE FROM participant WHERE consented_at < ?", (cutoff,))
    db.commit()


# Scenarios ------------------------------------------------------------------
def scenario_count():
    return get_db().execute("SELECT COUNT(*) AS n FROM scenario").fetchone()["n"]


def scenarios_for_pool(pool):
    return get_db().execute(
        "SELECT id, pool, position, channel, cue, difficulty, label, content_json "
        "FROM scenario WHERE pool = ? ORDER BY position",
        (pool,),
    ).fetchall()


def get_scenario(scenario_id, pool):
    return get_db().execute(
        "SELECT id, pool, position, channel, cue, difficulty, label, content_json "
        "FROM scenario WHERE id = ? AND pool = ?",
        (scenario_id, pool),
    ).fetchone()


def scenario_content(row):
    """Decode the JSON content stored with a scenario row."""
    return json.loads(row["content_json"])


# Attempts and responses ------------------------------------------------------
def get_attempt(participant_id, phase):
    return get_db().execute(
        "SELECT id, participant_id, phase, form, started_at, completed_at, score "
        "FROM attempt WHERE participant_id = ? AND phase = ?",
        (participant_id, phase),
    ).fetchone()


def get_or_create_attempt(participant_id, phase, form):
    attempt = get_attempt(participant_id, phase)
    if attempt is None:
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO attempt (participant_id, phase, form, started_at) "
            "VALUES (?, ?, ?, ?)",
            (participant_id, phase, form, utc_now()),
        )
        db.commit()
        attempt = get_attempt(participant_id, phase)
    return attempt


def answered_ids(attempt_id):
    rows = get_db().execute(
        "SELECT scenario_id FROM response WHERE attempt_id = ?", (attempt_id,)
    ).fetchall()
    return {row["scenario_id"] for row in rows}


def get_response(attempt_id, scenario_id):
    return get_db().execute(
        "SELECT answer, is_correct, answered_at FROM response "
        "WHERE attempt_id = ? AND scenario_id = ?",
        (attempt_id, scenario_id),
    ).fetchone()


def record_response(attempt_id, scenario_id, answer, is_correct):
    """Insert an answer once; repeated submissions are ignored (NFR-05)."""
    db = get_db()
    cursor = db.execute(
        "INSERT OR IGNORE INTO response "
        "(attempt_id, scenario_id, answer, is_correct, answered_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (attempt_id, scenario_id, answer, int(is_correct), utc_now()),
    )
    db.commit()
    return cursor.rowcount == 1


def correct_count(attempt_id):
    row = get_db().execute(
        "SELECT COALESCE(SUM(is_correct), 0) AS n FROM response WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    return row["n"]


def complete_attempt(attempt_id, score):
    db = get_db()
    db.execute(
        "UPDATE attempt SET completed_at = ?, score = ? "
        "WHERE id = ? AND completed_at IS NULL",
        (utc_now(), score, attempt_id),
    )
    db.commit()


def responses_with_cues(attempt_id):
    """(cue, is_correct) pairs in answer order, for cue-level analysis (RQ2)."""
    rows = get_db().execute(
        "SELECT s.cue AS cue, r.is_correct AS is_correct FROM response r "
        "JOIN scenario s ON s.id = r.scenario_id WHERE r.attempt_id = ? ORDER BY r.id",
        (attempt_id,),
    ).fetchall()
    return [(row["cue"], bool(row["is_correct"])) for row in rows]


# Lessons (M3) -------------------------------------------------------------------
def record_lesson_view(participant_id):
    """Remember that the participant opened the lessons; later visits change nothing."""
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO lesson_view (participant_id, viewed_at) VALUES (?, ?)",
        (participant_id, utc_now()),
    )
    db.commit()


def lessons_viewed(participant_id):
    row = get_db().execute(
        "SELECT 1 FROM lesson_view WHERE participant_id = ?", (participant_id,)
    ).fetchone()
    return row is not None


# Usability survey (M6) ---------------------------------------------------------
def save_sus(participant_id, ratings, score):
    """Store the ten ratings once and mark the participant as completed.

    A repeated submission is ignored (NFR-05). Returns True when a row was stored.
    """
    db = get_db()
    cursor = db.execute(
        "INSERT OR IGNORE INTO sus_response "
        "(participant_id, q1, q2, q3, q4, q5, q6, q7, q8, q9, q10, score, submitted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (participant_id, *ratings, score, utc_now()),
    )
    stored = cursor.rowcount == 1
    if stored:
        db.execute("UPDATE participant SET status = 'completed' WHERE id = ?", (participant_id,))
    db.commit()
    return stored


def get_sus(participant_id):
    return get_db().execute(
        "SELECT score, submitted_at FROM sus_response WHERE participant_id = ?",
        (participant_id,),
    ).fetchone()


# Cohort analytics (M5) --------------------------------------------------------
# None of these queries returns a participant identifier (NFR-11).
def participant_count():
    return get_db().execute("SELECT COUNT(*) AS n FROM participant").fetchone()["n"]


# Exact percentage correct per completed phase, recomputed from the stored answers
# so that cohort means are not distorted by the rounding of attempt.score.
_COHORT_SQL = (
    "SELECT "
    "(SELECT 100.0 * SUM(r.is_correct) / COUNT(*) FROM attempt a "
    "JOIN response r ON r.attempt_id = a.id WHERE a.participant_id = p.id "
    "AND a.phase = 'pre' AND a.completed_at IS NOT NULL) AS pre, "
    "(SELECT 100.0 * SUM(r.is_correct) / COUNT(*) FROM attempt a "
    "JOIN response r ON r.attempt_id = a.id WHERE a.participant_id = p.id "
    "AND a.phase = 'post' AND a.completed_at IS NOT NULL) AS post, "
    "(SELECT s.score FROM sus_response s WHERE s.participant_id = p.id) AS sus "
    "FROM participant p ORDER BY p.seq"
)


def cohort_records():
    """One record per participant: pre and post percentages and the SUS score.

    A phase that is not complete is None, as is a missing survey.
    """
    rows = get_db().execute(_COHORT_SQL).fetchall()
    return [{"pre": row["pre"], "post": row["post"], "sus": row["sus"]} for row in rows]


def cohort_responses(phase):
    """(cue, is_correct) pairs for one phase, from participants who finished both tests."""
    rows = get_db().execute(
        "SELECT s.cue AS cue, r.is_correct AS is_correct FROM response r "
        "JOIN attempt a ON a.id = r.attempt_id "
        "JOIN scenario s ON s.id = r.scenario_id "
        "WHERE a.phase = ? AND a.participant_id IN ("
        "SELECT participant_id FROM attempt "
        "WHERE phase IN ('pre', 'post') AND completed_at IS NOT NULL "
        "GROUP BY participant_id HAVING COUNT(*) = 2) "
        "ORDER BY r.id",
        (phase,),
    ).fetchall()
    return [(row["cue"], bool(row["is_correct"])) for row in rows]


def funnel_counts():
    """How many participants reached each step (counts only, no identifiers)."""
    row = get_db().execute(
        "SELECT "
        "(SELECT COUNT(*) FROM participant) AS consented, "
        "(SELECT COUNT(*) FROM attempt WHERE phase = 'pre' "
        "AND completed_at IS NOT NULL) AS pre_done, "
        "(SELECT COUNT(*) FROM lesson_view) AS lessons_opened, "
        "(SELECT COUNT(*) FROM attempt WHERE phase = 'practice' "
        "AND completed_at IS NOT NULL) AS practice_done, "
        "(SELECT COUNT(*) FROM attempt WHERE phase = 'post' "
        "AND completed_at IS NOT NULL) AS post_done, "
        "(SELECT COUNT(*) FROM sus_response) AS survey_done"
    ).fetchone()
    return dict(row)


# De-identified export (M7) ----------------------------------------------------
def export_records():
    """De-identified records for participants who finished both assessments (FR-11).

    The random ID is used only inside this function, to gather each participant's
    rows, and is never returned. Each record holds the form order, the number of
    correct answers per phase and cue, the number of items, and the SUS score.
    """
    rows = get_db().execute(
        "SELECT a.participant_id AS pid, p.form_order AS form_order, a.phase AS phase, "
        "s.cue AS cue, SUM(r.is_correct) AS correct, COUNT(*) AS total, "
        "(SELECT u.score FROM sus_response u WHERE u.participant_id = a.participant_id) AS sus "
        "FROM attempt a "
        "JOIN participant p ON p.id = a.participant_id "
        "JOIN response r ON r.attempt_id = a.id "
        "JOIN scenario s ON s.id = r.scenario_id "
        "WHERE a.phase IN ('pre', 'post') AND a.participant_id IN ("
        "SELECT participant_id FROM attempt "
        "WHERE phase IN ('pre', 'post') AND completed_at IS NOT NULL "
        "GROUP BY participant_id HAVING COUNT(*) = 2) "
        "GROUP BY a.participant_id, a.phase, s.cue ORDER BY p.seq"
    ).fetchall()
    grouped = {}
    for row in rows:
        record = grouped.setdefault(row["pid"], {
            "form_order": row["form_order"], "sus": row["sus"],
            "pre": {}, "post": {}, "pre_total": 0, "post_total": 0,
        })
        record[row["phase"]][row["cue"]] = row["correct"]
        record[f"{row['phase']}_total"] += row["total"]
    return list(grouped.values())


# Administrators (M7) ----------------------------------------------------------
def get_admin(username):
    return get_db().execute(
        "SELECT username, password_hash, created_at FROM admin_user WHERE username = ?",
        (username,),
    ).fetchone()


def save_admin(username, password_hash):
    """Create the account or replace its password. Returns True when it is new.

    A new "created_at" on every change lets require_admin sign out sessions that
    were opened with the previous password.
    """
    db = get_db()
    is_new = get_admin(username) is None
    db.execute(
        "INSERT INTO admin_user (username, password_hash, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash, "
        "created_at = excluded.created_at",
        (username, password_hash, datetime.now(timezone.utc).isoformat(timespec="microseconds")),
    )
    db.commit()
    return is_new


def record_failed_login(username):
    db = get_db()
    db.execute(
        "INSERT INTO admin_login_attempt (username, attempted_at) VALUES (?, ?)",
        (username, utc_now()),
    )
    db.commit()


def failed_login_count(since, username=None):
    """Failed sign-in attempts since a time: for one username, or for all of them."""
    if username is None:
        row = get_db().execute(
            "SELECT COUNT(*) AS n FROM admin_login_attempt WHERE attempted_at >= ?", (since,)
        ).fetchone()
    else:
        row = get_db().execute(
            "SELECT COUNT(*) AS n FROM admin_login_attempt "
            "WHERE username = ? AND attempted_at >= ?",
            (username, since),
        ).fetchone()
    return row["n"]


def clear_failed_logins(username=None, before=None):
    """Forget failed attempts: all of one username's, or everything older than a time."""
    db = get_db()
    if username is not None:
        db.execute("DELETE FROM admin_login_attempt WHERE username = ?", (username,))
    if before is not None:
        db.execute("DELETE FROM admin_login_attempt WHERE attempted_at < ?", (before,))
    db.commit()
