"""Tests for the way the web service holds its database connections (defect D-7).

SQLite locks the database file with POSIX advisory locks, and a process that
closes any descriptor of a file loses every lock it holds on that file. A web
process that closed a connection after each request could cancel, in that
instant, a lock that another of its threads had just taken. The web service
therefore keeps its connections open and lends them to requests.

A lost lock cannot be produced on purpose from Python: the window is inside
SQLite. These tests check the rule that removes it, which can be observed:
while requests are served, no connection is closed. evaluation/contention.py
tests the consequence, with real processes and a delayed close().
"""

import os
import shutil
import sqlite3
import threading
from unittest import mock

from src import db, repository
from src.db import get_db
from tests.helpers import AppTestCase, watch_connections


class ConnectionTestCase(AppTestCase):
    watching = staticmethod(watch_connections)

    def consent_form(self, client):
        return {"adult": "yes", "agree": "yes", "csrf_token": self.token(client)}


class Unprepared(sqlite3.Connection):
    """A connection on which the first statement fails, as on a failing disk."""

    made = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False
        Unprepared.made.append(self)

    def execute(self, *_args):
        raise sqlite3.OperationalError("disk I/O error")

    def close(self):
        self.closed = True
        super().close()


CONNECT = sqlite3.connect    # the real function, before any test replaces it


def connect_unprepared(*args, **kwargs):
    kwargs["factory"] = Unprepared
    return CONNECT(*args, **kwargs)


class RequestTests(ConnectionTestCase):
    """What a request does with its connection."""

    def test_a_series_of_requests_is_served_by_one_connection_that_stays_open(self):
        form = self.consent_form(self.client)
        with self.watching() as watch:
            self.assertEqual(self.client.post("/consent", data=form).status_code, 302)
            for _ in range(5):
                self.assertEqual(self.client.get("/dashboard").status_code, 200)
                self.assertEqual(self.client.get("/assessment/pre").status_code, 200)
            self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(len(watch.opened), 1)      # releases up to 0.6.0: twelve
        self.assertEqual(watch.closed, [])          # and each of them closed again
        self.assertEqual(db.pool_of(self.app).idle, 1)

    def test_no_connection_is_closed_while_eight_threads_are_served(self):
        browsers = [self.app.test_client() for _ in range(8)]
        forms = [self.consent_form(browser) for browser in browsers]
        barrier = threading.Barrier(8)
        problems = []

        def session(index):
            browser = browsers[index]
            barrier.wait()
            try:
                statuses = [browser.post("/consent", data=forms[index]).status_code]
                for _ in range(15):
                    statuses.append(browser.get("/dashboard").status_code)
                    statuses.append(browser.get("/assessment/pre").status_code)
                if statuses != [302] + [200] * 30:
                    problems.append(statuses)
            except Exception as error:      # reported by the assertion below
                problems.append(repr(error))

        with self.watching() as watch:
            threads = [threading.Thread(target=session, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(problems, [])
        self.assertEqual(watch.closed, [])
        # Never more connections than requests in progress at one moment.
        self.assertLessEqual(len(watch.opened), 8)
        self.assertEqual(db.pool_of(self.app).idle, len(watch.opened))
        self.assertEqual(db.pool_of(self.app).opened, len(watch.opened))
        self.assertEqual(self.count("participant"), 8)

    def test_a_transaction_that_a_failed_request_left_open_is_rolled_back(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        participant_id = self.consent()
        self.answer_pretest()

        def store_and_fail(_participant_id):
            get_db().execute(
                "INSERT INTO lesson_view (participant_id, viewed_at) VALUES (?, 'now')",
                (participant_id,))
            raise RuntimeError("the request fails between the statement and its commit")

        with mock.patch.object(repository, "record_lesson_view", side_effect=store_and_fail):
            with self.assertLogs("src.app", level="ERROR"):
                self.assertEqual(self.client.get("/learn").status_code, 500)
        self.assertEqual(self.count("lesson_view"), 0)
        # The connection went back without the transaction: it holds no lock, so
        # another connection can write at once ...
        other = sqlite3.connect(self.app.config["DATABASE"], timeout=0.2)
        try:
            other.execute("UPDATE participant SET status = status")
            other.commit()
        finally:
            other.close()
        # ... and the next request on it stores what it should, and nothing more.
        self.assertEqual(self.client.get("/learn").status_code, 200)
        self.assertEqual(self.count("lesson_view"), 1)
        self.assertEqual(self.client.get("/practice").status_code, 200)

    def test_a_statement_that_a_request_left_unfinished_keeps_no_lock(self):
        self.consent()
        left_open = []

        def read_one_row_of_many(_participant_id):
            cursor = get_db().execute("SELECT id FROM scenario ORDER BY id")
            cursor.fetchone()               # 29 more rows wait: the read lock stays
            left_open.append(cursor)        # and the cursor outlives the request
            return None

        with mock.patch.object(repository, "record_lesson_view",
                               side_effect=read_one_row_of_many):
            self.answer_pretest()
            self.assertEqual(self.client.get("/learn").status_code, 200)
        self.assertEqual(len(left_open), 1)
        # Another process can write at once. With the lock still held it would
        # wait, and fail with "database is locked".
        other = sqlite3.connect(self.app.config["DATABASE"], timeout=0.2)
        try:
            other.execute("UPDATE participant SET status = status")
            other.commit()
        finally:
            other.close()
        with self.assertRaises(sqlite3.ProgrammingError):   # the cursor was closed
            left_open[0].fetchone()
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_every_way_of_running_a_statement_is_remembered_until_the_request_ends(self):
        with self.app.test_request_context("/"):
            connection = get_db()
            cursors = [
                connection.cursor(),
                connection.execute("SELECT id FROM scenario"),
                connection.executemany("UPDATE scenario SET cue = cue WHERE id = ?", [("A01",)]),
                connection.executescript("SELECT 1;"),
            ]
            cursors[0].execute("SELECT id FROM scenario")
            self.assertEqual(len(connection._cursors), 4)
        # Leaving the request returned the connection, and closed all four.
        for cursor in cursors:
            with self.assertRaises(sqlite3.ProgrammingError):
                cursor.execute("SELECT 1")
        self.assertEqual(len(connection._cursors), 0)
        self.assertFalse(connection.in_transaction)

    def test_a_connection_that_cannot_be_rolled_back_is_not_used_again(self):
        self.consent()
        pool = db.pool_of(self.app)
        self.assertEqual(pool.idle, 1)

        class Broken:
            in_transaction = True

            def finish_statements(self):
                pass

            def rollback(self):
                raise sqlite3.OperationalError("disk I/O error")

        pool.release(Broken(), None)
        self.assertEqual(pool.idle, 1)                      # it did not join the idle ones
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_a_connection_that_cannot_be_prepared_is_put_aside_and_not_closed(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.consent()
        pool = db.pool_of(self.app)
        pool.close_idle()                                   # the next request must open one
        Unprepared.made.clear()
        self.addCleanup(Unprepared.made.clear)
        with mock.patch.object(db.sqlite3, "connect", connect_unprepared):
            with self.assertLogs("src.app", level="ERROR"):
                self.assertEqual(self.client.get("/dashboard").status_code, 500)
        self.assertGreaterEqual(len(Unprepared.made), 1)
        for connection in Unprepared.made:
            # Python would have closed it on collecting it; the pool holds it instead.
            self.assertFalse(connection.closed)
            self.assertIn(connection, pool._kept)
        self.assertEqual(pool.idle, 0)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)   # a sound one
        self.assertEqual(pool.idle, 1)
        for connection in Unprepared.made:      # the test is the end of this "process"
            connection.close()


class FileTests(ConnectionTestCase):
    """The database file is replaced, or the process is a copy of another."""

    def test_a_database_file_that_was_replaced_is_opened_again(self):
        self.consent()
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        with self.watching() as watch:
            # "flask reset-db" deletes the file and builds a new one. A connection
            # to the earlier file would keep showing the deleted records.
            result = self.app.test_cli_runner().invoke(args=["reset-db", "--yes"])
            self.assertEqual(result.exit_code, 0, result.output)
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)          # the participant is gone
        self.assertIn("/consent", response.headers["Location"])
        self.assertEqual(self.count("participant"), 0)
        self.consent()
        self.assertEqual(self.count("participant"), 1)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        self.assertGreaterEqual(len(watch.opened), 1)

    def test_a_file_that_another_process_put_in_place_is_noticed_at_the_next_request(self):
        form = self.consent_form(self.client)
        path = self.app.config["DATABASE"]
        with self.watching() as watch:
            self.assertEqual(self.client.post("/consent", data=form).status_code, 302)
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
            earlier = db.file_identity(path)
            # An operator puts a copy of the database in place of the file: the
            # same records, but another file for the operating system.
            shutil.copy(path, path + ".copy")
            os.replace(path + ".copy", path)
            self.assertNotEqual(db.file_identity(path), earlier)
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
            self.assertEqual(len(watch.opened), 2)       # a connection to the new file
            self.assertEqual(len(watch.closed), 1)       # and the stale one is closed
            self.assertIs(watch.closed[0], watch.opened[0])
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
            self.assertEqual(len(watch.opened), 2)       # which is then kept, as before

    def test_a_file_is_identified_by_device_and_inode_and_a_missing_file_by_nothing(self):
        path = self.app.config["DATABASE"]
        status = os.stat(path)
        self.assertEqual(db.file_identity(path), (status.st_dev, status.st_ino))
        self.assertIsNone(db.file_identity(path + ".absent"))

    def test_a_copy_of_the_process_does_not_use_the_connections_it_inherited(self):
        self.consent()
        pool = db.pool_of(self.app)
        self.assertEqual(pool.idle, 1)
        with self.watching() as watch:
            with mock.patch.object(db.os, "getpid", return_value=os.getpid() + 1):
                self.assertEqual(self.client.get("/dashboard").status_code, 200)
                self.assertEqual(len(watch.opened), 1)   # a connection of its own
                self.assertEqual(watch.closed, [])       # the inherited one is left alone
                self.assertEqual(pool.idle, 1)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        # Here both "processes" are this test, so what was put aside can be closed.
        for connection in pool._kept:
            connection.close()


class CommandTests(ConnectionTestCase):
    """Outside a request: a command, the start-up, a test's own query."""

    def test_a_statement_waits_fifteen_seconds_for_a_lock_before_it_gives_up(self):
        with self.app.app_context():
            # PRAGMA busy_timeout reports the waiting time in milliseconds.
            self.assertEqual(get_db().execute("PRAGMA busy_timeout").fetchone()[0], 15000)

    def test_a_connection_opened_outside_a_request_is_closed_with_its_context(self):
        with self.watching() as watch:
            with self.app.app_context():
                connection = get_db()
                self.assertIs(get_db(), connection)
                self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual((len(watch.opened), len(watch.closed)), (1, 1))
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        self.assertEqual(db.pool_of(self.app).idle, 0)    # it never entered the pool

    def test_a_command_closes_a_connection_that_it_could_not_prepare(self):
        Unprepared.made.clear()
        self.addCleanup(Unprepared.made.clear)
        with mock.patch.object(db.sqlite3, "connect", connect_unprepared):
            with self.app.app_context():
                with self.assertRaises(sqlite3.OperationalError):
                    get_db()
        self.assertEqual([connection.closed for connection in Unprepared.made], [True])
        self.assertEqual(db.pool_of(self.app)._kept, [])

    def test_a_command_leaves_the_connections_of_requests_alone(self):
        self.consent()
        with self.watching() as watch:
            result = self.app.test_cli_runner().invoke(args=["analytics"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual((len(watch.opened), len(watch.closed)), (1, 1))
        self.assertEqual(db.pool_of(self.app).idle, 1)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_closing_the_idle_connections_ends_what_a_process_kept_open(self):
        self.consent()
        self.consent(self.app.test_client())
        pool = db.pool_of(self.app)
        self.assertEqual(pool.idle, 1)                      # one thread, one connection
        self.assertEqual(pool.close_idle(), 1)
        self.assertEqual(pool.idle, 0)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)   # opens a new one
        self.assertEqual(pool.opened, 2)
