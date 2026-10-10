-- PhishAware database schema (data tier), based on the Unit 3 ER model.
-- Privacy by design (NFR-11): no table stores a name, email address,
-- student number, password, IP address, or demographic attribute.

PRAGMA foreign_keys = ON;

-- M1: one row per consenting adult. "id" is a random UUID4 and is the only
-- identifier used anywhere else. "seq" is an internal counter used solely to
-- counterbalance test forms (odd = Form A first, even = Form B first).
CREATE TABLE IF NOT EXISTS participant (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT    NOT NULL UNIQUE,
    consent_version TEXT    NOT NULL,
    consented_at    TEXT    NOT NULL,
    form_order      TEXT    NOT NULL CHECK (form_order IN ('AB', 'BA')),
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'completed'))
);

-- Read-only scenario bank, seeded from data/scenarios.json.
-- pool: A or B = parallel assessment forms, P = practice pool (M4).
CREATE TABLE IF NOT EXISTS scenario (
    id           TEXT    PRIMARY KEY,
    pool         TEXT    NOT NULL CHECK (pool IN ('A', 'B', 'P')),
    position     INTEGER NOT NULL,
    channel      TEXT    NOT NULL CHECK (channel IN ('email', 'web')),
    cue          TEXT    NOT NULL,
    difficulty   INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 3),
    label        TEXT    NOT NULL CHECK (label IN ('phishing', 'legitimate')),
    content_json TEXT    NOT NULL
);

-- One attempt per participant per phase (pre-test, practice, post-test).
CREATE TABLE IF NOT EXISTS attempt (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id TEXT    NOT NULL REFERENCES participant(id) ON DELETE CASCADE,
    phase          TEXT    NOT NULL CHECK (phase IN ('pre', 'practice', 'post')),
    form           TEXT    NOT NULL CHECK (form IN ('A', 'B', 'P')),
    started_at     TEXT    NOT NULL,
    completed_at   TEXT,
    score          REAL,
    UNIQUE (participant_id, phase)
);

-- Individual answers. UNIQUE makes re-submission idempotent (NFR-05).
CREATE TABLE IF NOT EXISTS response (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id  INTEGER NOT NULL REFERENCES attempt(id) ON DELETE CASCADE,
    scenario_id TEXT    NOT NULL REFERENCES scenario(id),
    answer      TEXT    NOT NULL CHECK (answer IN ('phishing', 'legitimate')),
    is_correct  INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
    answered_at TEXT    NOT NULL,
    UNIQUE (attempt_id, scenario_id)
);

-- M3: when a participant first opened the lessons (FR-04). The practice phase
-- opens only after this, so everyone who practised was offered the lessons.
CREATE TABLE IF NOT EXISTS lesson_view (
    participant_id TEXT PRIMARY KEY REFERENCES participant(id) ON DELETE CASCADE,
    viewed_at      TEXT NOT NULL
);

-- M6: System Usability Scale ratings (FR-09), one row per participant.
CREATE TABLE IF NOT EXISTS sus_response (
    participant_id TEXT PRIMARY KEY REFERENCES participant(id) ON DELETE CASCADE,
    q1  INTEGER NOT NULL CHECK (q1  BETWEEN 1 AND 5),
    q2  INTEGER NOT NULL CHECK (q2  BETWEEN 1 AND 5),
    q3  INTEGER NOT NULL CHECK (q3  BETWEEN 1 AND 5),
    q4  INTEGER NOT NULL CHECK (q4  BETWEEN 1 AND 5),
    q5  INTEGER NOT NULL CHECK (q5  BETWEEN 1 AND 5),
    q6  INTEGER NOT NULL CHECK (q6  BETWEEN 1 AND 5),
    q7  INTEGER NOT NULL CHECK (q7  BETWEEN 1 AND 5),
    q8  INTEGER NOT NULL CHECK (q8  BETWEEN 1 AND 5),
    q9  INTEGER NOT NULL CHECK (q9  BETWEEN 1 AND 5),
    q10 INTEGER NOT NULL CHECK (q10 BETWEEN 1 AND 5),
    score        REAL NOT NULL,
    submitted_at TEXT NOT NULL
);

-- M1: participants who chose Finish. Their session cookie is refused from then
-- on, even if a copy of it exists (NFR-08). Only the fact is stored, not a time.
CREATE TABLE IF NOT EXISTS session_end (
    participant_id TEXT PRIMARY KEY REFERENCES participant(id) ON DELETE CASCADE
);

-- M7: administrator accounts (FR-11). Deliberately unrelated to participant
-- data so accounts can never be joined to individual learners. "session_stamp"
-- changes whenever the password changes and at every sign-out; a session is
-- valid only while it carries the current stamp.
CREATE TABLE IF NOT EXISTS admin_user (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    session_stamp TEXT NOT NULL
);

-- M7: failed sign-in attempts, kept only long enough to rate-limit guessing
-- (NFR-10). "username" holds a keyed digest of the attempted username
-- (admin.attempt_key): never the text that was typed, which may be a password
-- entered in the wrong field, and never an IP address (NFR-11).
CREATE TABLE IF NOT EXISTS admin_login_attempt (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);

-- Indexes support the NFR-01 response-time target.
CREATE INDEX IF NOT EXISTS idx_attempt_participant ON attempt (participant_id);
CREATE INDEX IF NOT EXISTS idx_response_attempt    ON response (attempt_id);
CREATE INDEX IF NOT EXISTS idx_scenario_pool       ON scenario (pool, position);
CREATE INDEX IF NOT EXISTS idx_login_attempt       ON admin_login_attempt (username, attempted_at);
