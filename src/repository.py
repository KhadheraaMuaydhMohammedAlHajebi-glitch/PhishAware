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


# Scenarios ------------------------------------------------------------------
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
# None of these queries selects a participant identifier (NFR-11).
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
