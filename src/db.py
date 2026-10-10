"""Data tier: SQLite connection management, schema creation and upgrade, and seeding.

Every query in PhishAware uses "?" placeholders (parameterized queries), so
user input is never concatenated into SQL (NFR-09, OWASP ASVS).

A database outlives the release that created it. schema.sql creates whatever
is missing, but CREATE TABLE IF NOT EXISTS leaves an existing table as it is,
so a change to an existing table needs a migration step (see migrate). The
database file records the schema version it has reached.

Connections. A web request takes its connection from a pool and gives it back
when the request ends; the web service never closes a connection while it is
running (see ConnectionPool for the reason, defect D-7). A command and the
start-up open a connection of their own and close it when they are done. That
is safe there, because such a process has a single thread.
"""

import json
import os
import sqlite3
import threading
import weakref
from pathlib import Path

import click
from flask import current_app, g, has_request_context
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


POOL = "phishaware.connections"   # key of the pool in app.extensions

# How long a statement waits for a lock that another connection holds, before
# it fails with "database is locked". The sqlite3 module waits 5 seconds unless
# told otherwise. In the contention test (evaluation/contention.py), where eight
# threads write without a pause, the longest wait was 2.4 seconds: too close to
# that limit. A participant is better served by a late answer than by an error
# page, and 15 seconds is still well inside the web server's limit of 30.
BUSY_TIMEOUT_SECONDS = 15.0


class Connection(sqlite3.Connection):
    """A connection that can end the statements a request has left unfinished.

    A SELECT that still has rows to give keeps its read lock on the database
    file for as long as its cursor exists. While a request closed its
    connection, that ended with the request. On a connection that is kept, a
    cursor that outlives its request would keep the lock (the traceback of an
    error can hold one for minutes), and no other process could write. The
    connection therefore remembers its cursors, and the pool closes them when
    the request ends.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cursors = weakref.WeakSet()

    def _remember(self, cursor):
        self._cursors.add(cursor)
        return cursor

    # execute() and its relatives create their cursor inside the sqlite3 module,
    # without calling cursor(), so each of them is extended here.
    def cursor(self, *args, **kwargs):
        return self._remember(super().cursor(*args, **kwargs))

    def execute(self, *args, **kwargs):
        return self._remember(super().execute(*args, **kwargs))

    def executemany(self, *args, **kwargs):
        return self._remember(super().executemany(*args, **kwargs))

    def executescript(self, *args, **kwargs):
        return self._remember(super().executescript(*args, **kwargs))

    def finish_statements(self):
        """Close every cursor that is still open; an unfinished SELECT loses its lock."""
        for cursor in list(self._cursors):
            cursor.close()
        self._cursors.clear()


def connect(path, abandon=None):
    """Open a connection to the database file and prepare it for use.

    If the preparation fails, the connection is closed, or handed to `abandon`
    when that is given. The pool gives its own function: Python would close an
    abandoned connection when it collects it, and the web service closes none.
    """
    # check_same_thread is off because a pooled connection serves one request
    # at a time, but not always in the same thread. SQLite itself is built
    # thread-safe; the module's check would only forbid the hand-over.
    connection = sqlite3.connect(
        path, timeout=BUSY_TIMEOUT_SECONDS, check_same_thread=False, factory=Connection)
    try:
        connection.row_factory = sqlite3.Row
        # SQLite disables foreign keys by default; they are needed for the
        # ON DELETE CASCADE that implements withdrawal (FR-10).
        connection.execute("PRAGMA foreign_keys = ON")
        # Overwrite deleted rows with zeros, so that a withdrawn or expired
        # record does not linger in the file's unused pages (FR-10, NFR-12).
        connection.execute("PRAGMA secure_delete = ON")
    except sqlite3.Error:
        if abandon is None:
            connection.close()
        else:
            abandon(connection)
        raise
    return connection


def file_identity(path):
    """(device, inode) of the database file, or None while there is no such file."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_dev, status.st_ino


class ConnectionPool:
    """The database connections of the web service in one process, kept open.

    Why they are kept open (defect D-7). SQLite locks the database file with
    POSIX advisory locks. Such a lock belongs to the process, not to the
    descriptor: when a process closes any descriptor of a file, the operating
    system releases every lock the process holds on that file. SQLite knows
    this and postpones a close while another connection of the process holds a
    lock. The check and the close are two steps, however, and another thread
    can take a lock between them. The close then releases that lock without
    SQLite or the thread noticing.

    Releases up to 0.6.0 opened a connection for every request and closed it
    afterwards, in eight threads of two processes. System testing showed the
    result: a worker lost its write lock in the middle of a transaction, a
    second worker began to write, and both used the same rollback journal. One
    commit failed with "disk I/O error" (SQLITE_IOERR_DELETE_NOENT), answers
    that other participants had already stored were overwritten, and with the
    close delayed by 0.3 ms the database file was damaged within seconds.

    A web process therefore never closes a connection while it serves requests.
    A request borrows one, and returns it at its end, after a rollback if it
    left a transaction open. The pool holds at most as many connections as the
    process has had requests in progress at the same moment: the number of
    worker threads.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._idle = []          # (connection, identity of the file it was opened on)
        self._kept = []          # never used again, and never closed while the process runs
        self._owner = os.getpid()
        self.opened = 0          # connections opened by this process, for diagnosis

    def acquire(self, path):
        """An idle connection to the file that is at `path` now, or a new one."""
        identity = file_identity(path)
        stale = []
        connection = None
        with self._lock:
            if self._owner != os.getpid():
                # This process is a copy of the one that filled the pool. SQLite
                # forbids using a connection across fork(), so the copies are
                # put aside. They are not closed: nothing may be assumed about them.
                self._kept.extend(entry[0] for entry in self._idle)
                self._idle, self._owner = [], os.getpid()
            while self._idle and connection is None:
                candidate, opened_on = self._idle.pop()
                if opened_on == identity:
                    connection = candidate
                else:
                    stale.append(candidate)
        for candidate in stale:
            # The file was replaced or deleted (reset-db, or an operator's copy).
            # This connection belongs to the earlier file. Closing it can release
            # locks on that earlier file only, which nothing should use any more.
            candidate.close()
        if connection is None:
            # The identity was read before the file is opened. Should the file be
            # replaced in between, this connection counts as stale at its next
            # use; read afterwards, a stale connection could pass for a current one.
            connection = connect(path, abandon=self._keep)
            with self._lock:
                self.opened += 1
        return connection, identity

    def _keep(self, connection):
        """Put a connection aside: it is not used again, and it is not closed."""
        with self._lock:
            self._kept.append(connection)

    def release(self, connection, identity):
        """Take a connection back at the end of a request."""
        try:
            connection.finish_statements()
            if connection.in_transaction:
                # The request ended between a statement and its commit, for
                # example with an error. Nothing of it may reach the next request.
                connection.rollback()
        except sqlite3.Error:
            self._keep(connection)
            return
        with self._lock:
            self._idle.append((connection, identity))

    def close_idle(self):
        """Close the idle connections. Returns how many were closed.

        Only for a process in which no other thread is using the database at
        that moment: a command, or a test that ends an application.
        """
        with self._lock:
            idle, self._idle = self._idle, []
        for connection, _identity in idle:
            connection.close()
        return len(idle)

    @property
    def idle(self):
        with self._lock:
            return len(self._idle)


def pool_of(app):
    """The connection pool of an application."""
    return app.extensions[POOL]


def get_db():
    """Return this context's connection, creating or borrowing it on first use."""
    if "db" not in g:
        path = current_app.config["DATABASE"]
        if has_request_context():
            g.db, g.db_identity = pool_of(current_app).acquire(path)
        else:
            g.db = connect(path)
    return g.db


def close_db(_error=None):
    """End the context's use of its connection (registered as a teardown handler).

    A request returns its connection to the pool. A command or the start-up
    closes the connection it opened.
    """
    connection = g.pop("db", None)
    if connection is None:
        return
    if "db_identity" in g:
        pool_of(current_app).release(connection, g.pop("db_identity"))
    else:
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
    pool_of(current_app).close_idle()    # a command is the only user of its process
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
    app.extensions[POOL] = ConnectionPool()
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(reset_db_command)
