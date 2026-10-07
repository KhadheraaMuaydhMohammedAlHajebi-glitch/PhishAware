"""Data tier: SQLite connection management, schema creation, and seeding.

Every query in PhishAware uses "?" placeholders (parameterized queries), so
user input is never concatenated into SQL (NFR-09, OWASP ASVS).
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


# PRAGMA statements cannot take "?" parameters, so each allowed mode has its own
# constant statement and no SQL text is ever assembled from a setting.
_JOURNAL_SQL = {
    "WAL": "PRAGMA journal_mode = WAL",
    "DELETE": "PRAGMA journal_mode = DELETE",
}


def get_db():
    """Return one connection per request, creating it on first use."""
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        # SQLite disables foreign keys by default; they are needed for the
        # ON DELETE CASCADE that implements withdrawal (FR-10).
        connection.execute("PRAGMA foreign_keys = ON")
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


def ensure_schema():
    """Create any table or index that is missing.

    Every statement in schema.sql is IF NOT EXISTS, so running the script again
    is harmless. Running it on each start upgrades a database that an earlier
    release created, for example by adding the tables that M7 introduced.
    """
    get_db().executescript(SCHEMA_FILE.read_text(encoding="utf-8"))


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
    statement = _JOURNAL_SQL[current_app.config["SQLITE_JOURNAL_MODE"]]
    return get_db().execute(statement).fetchone()[0].upper()


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
