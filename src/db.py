"""Data tier: SQLite connection management, schema creation and upgrade, and seeding.

Every query in PhishAware uses "?" placeholders (parameterized queries), so
user input is never concatenated into SQL (NFR-09, OWASP ASVS).

A database outlives the release that created it. schema.sql creates whatever
is missing, but CREATE TABLE IF NOT EXISTS leaves an existing table as it is,
so a change to an existing table needs a migration step (see migrate). The
database file records the schema version it has reached.
"""

import json
import sqlite3
from pathlib import Path

import click
from flask import current_app, g
from flask.cli import with_appcontext

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

# Upsert keeps seeding idempotent: re-running it updates edited scenarios.
_SEED_SQL = (
    "INSERT INTO scenario (id, pool, position, channel, cue, difficulty, label, content_json) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET pool = excluded.pool, position = excluded.position, "
    "channel = excluded.channel, cue = excluded.cue, difficulty = excluded.difficulty, "
    "label = excluded.label, content_json = excluded.content_json"
)


# The schema version this release writes into the database file (PRAGMA
# user_version). Releases 0.4.0 to 0.6.0 recorded no version, so their
# databases read 0; their schema counts as version 1.
SCHEMA_VERSION = 2

# The administrator table as version 2 defines it. A migration step carries its
# own copy of a definition, so that a later edit of schema.sql cannot change
# what an earlier step did.
_ADMIN_USER_V2 = (
    "CREATE TABLE admin_user ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "username TEXT NOT NULL UNIQUE, "
    "password_hash TEXT NOT NULL, "
    "session_stamp TEXT NOT NULL)"
)

# PRAGMA statements cannot take "?" parameters, so each allowed mode has its own
# constant statement and no SQL text is ever assembled from a setting.
_JOURNAL_SQL = {
    "WAL": "PRAGMA journal_mode = WAL",
    "DELETE": "PRAGMA journal_mode = DELETE",
}


def wal_is_safe(version=sqlite3.sqlite_version_info):
    """True when this SQLite release contains the fix for the "WAL-reset bug".

    SQLite's documentation (sqlite.org/wal.html, section 11) describes a rare race
    between connections that write or checkpoint at the same instant; it can lose
    committed changes from a database in WAL mode. Releases 3.7.0 to 3.51.2 are
    affected. The fix is in 3.51.3 and later and in the patch releases 3.44.6
    and 3.50.7.
    """
    version = tuple(version[:3])
    return (
        version >= (3, 51, 3)
        or (3, 50, 7) <= version < (3, 51, 0)
        or (3, 44, 6) <= version < (3, 45, 0)
    )


def journal_mode_for(setting, version=sqlite3.sqlite_version_info):
    """Resolve the configured journal mode; AUTO depends on the SQLite release."""
    if setting == "AUTO":
        return "WAL" if wal_is_safe(version) else "DELETE"
    return setting


def get_db():
    """Return one connection per request, creating it on first use."""
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        # SQLite disables foreign keys by default; they are needed for the
        # ON DELETE CASCADE that implements withdrawal (FR-10).
        connection.execute("PRAGMA foreign_keys = ON")
        # Overwrite deleted rows with zeros, so that a withdrawn or expired
        # record does not linger in the file's unused pages (FR-10, NFR-12).
        connection.execute("PRAGMA secure_delete = ON")
        g.db = connection
    return g.db


def close_db(_error=None):
    """Close the request's connection (registered as a teardown handler)."""
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def load_scenario_bank(path):
    """Read the fictional scenario bank from JSON."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def seed_scenarios(connection, path):
    """Insert or update every scenario from the JSON bank. Returns the count."""
    bank = load_scenario_bank(path)
    for item in bank["scenarios"]:
        connection.execute(
            _SEED_SQL,
            (
                item["id"], item["pool"], item["position"], item["channel"],
                item["cue"], item["difficulty"], item["label"], json.dumps(item),
            ),
        )
    return len(bank["scenarios"])


def _to_version_2(connection):
    """Release 0.7.0.

    Releases 0.4.0 to 0.5.1 created the table admin_user for a module that was
    still planned, with a column "created_at" and without "session_stamp".
    Release 0.6.0 added the module but could not store an administrator in such
    a database ("no such column: session_stamp"), because CREATE TABLE IF NOT
    EXISTS had left the older table in place. The table is rebuilt in its
    present form, and any row it holds is kept.

    Release 0.6.0 stored the user name of a failed sign-in as it was typed. This
    release stores a keyed digest in its place (admin.attempt_key), so the older
    rows can never match again. They are deleted; at most the last 15 minutes
    of the sign-in rate limit start again from zero.
    """
    columns = {row[1] for row in connection.execute("PRAGMA table_info(admin_user)")}
    if "session_stamp" not in columns:
        connection.execute("ALTER TABLE admin_user RENAME TO admin_user_before_0_6")
        connection.execute(_ADMIN_USER_V2)
        # No cookie carries this stamp, so no session of an earlier release continues.
        connection.execute(
            "INSERT INTO admin_user (id, username, password_hash, session_stamp) "
            "SELECT id, username, password_hash, 'set by the upgrade to schema version 2' "
            "FROM admin_user_before_0_6")
        connection.execute("DROP TABLE admin_user_before_0_6")
    connection.execute("DELETE FROM admin_login_attempt")
    connection.execute("PRAGMA user_version = 2")


# (version reached, step), oldest first. A step changes what schema.sql cannot
# change in an existing database, and records its version as its last statement.
_MIGRATIONS = ((2, _to_version_2),)


def schema_version():
    """The schema version recorded in the database file."""
    return get_db().execute("PRAGMA user_version").fetchone()[0]


def migrate():
    """Apply the migration steps that this database has not seen. Returns their versions.

    All steps run in one transaction that takes the write lock before it reads
    the version. When several processes start at once (two web workers and the
    jobs service do), one of them migrates, and the others wait and then find
    nothing to do. If a step fails, the transaction is rolled back, the database
    is exactly as it was, and the start-up stops with the error.
    """
    connection = get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        pending = [(target, step) for target, step in _MIGRATIONS if target > version]
        for _, step in pending:
            step(connection)
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    return [target for target, _ in pending]


def ensure_schema():
    """Bring the database to this release's schema. Returns the migration steps applied.

    schema.sql creates any table or index that is missing; every statement in it
    is IF NOT EXISTS, so running it again is harmless. migrate() then changes
    what that cannot change. Both run on every start and after a restore, so a
    database or a snapshot of an earlier release is upgraded when it is opened.
    """
    get_db().executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    applied = migrate()
    if applied:
        current_app.logger.info("Database schema brought to version %d.", applied[-1])
    version = schema_version()
    if version > SCHEMA_VERSION:
        current_app.logger.warning(
            "The database has schema version %d, and this release knows version %d: a newer "
            "release has written to it. If this release fails on it, restore the snapshot "
            "taken before the upgrade (docs/deployment.md, section 8).",
            version, SCHEMA_VERSION)
    return applied


def init_db():
    """Create tables (if missing) and seed the scenario bank."""
    connection = get_db()
    ensure_schema()
    count = seed_scenarios(connection, current_app.config["SCENARIO_FILE"])
    connection.commit()
    return count


def apply_journal_mode():
    """Set the journal mode chosen in the configuration; returns the mode in effect.

    The mode is stored in the database file, so setting it once at start-up is
    enough for every later connection.
    """
    mode = journal_mode_for(current_app.config["SQLITE_JOURNAL_MODE"])
    return get_db().execute(_JOURNAL_SQL[mode]).fetchone()[0].upper()


def database_ready():
    """True when the schema exists and the scenario bank has been seeded."""
    try:
        row = get_db().execute("SELECT COUNT(*) AS n FROM scenario").fetchone()
        return row["n"] > 0
    except sqlite3.OperationalError:
        return False


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create the database schema and load the scenario bank."""
    count = init_db()
    click.echo(f"Database ready: {count} scenarios loaded.")
    click.echo(f"Location: {current_app.config['DATABASE']}")


@click.command("reset-db")
@click.confirmation_option(prompt="This deletes ALL participant data. Continue?")
@with_appcontext
def reset_db_command():
    """Development only: delete the database file and rebuild it."""
    close_db()
    database = current_app.config["DATABASE"]
    for suffix in ("", "-wal", "-shm"):  # the write-ahead log lives beside the database
        Path(database + suffix).unlink(missing_ok=True)
    count = init_db()
    apply_journal_mode()
    click.echo(f"Database rebuilt: {count} scenarios loaded, no participant data.")


def init_app(app):
    """Register teardown and CLI commands with the application.

    Each command carries @with_appcontext. The flask launcher pushes an
    application context by itself, but Flask's test runner does not, so a
    command without the decorator works in a terminal and fails under test.
    """
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(reset_db_command)
