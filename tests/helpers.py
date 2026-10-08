"""Shared test fixture: a fresh application and a temporary database per test."""

import base64
import os
import re
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.app import create_app
from src.db import get_db

SCENARIO_FIELD = re.compile(r'name="scenario_id" value="([ABP][0-9]{2})"')
ADMIN_USER = "researcher"
ADMIN_PASSWORD = "correct-horse-battery-staple"
BACKUP_MARKER = b"PHISHAWARE-BACKUP-1\n"


# The backup format, written out again here so that the tests check the files
# against the documented format and not against the code that produced them:
# marker, 12-byte nonce, AES-256-GCM ciphertext with the marker as associated data.
def new_backup_key():
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def seal_backup(key, plaintext):
    nonce = os.urandom(12)
    cipher = AESGCM(base64.urlsafe_b64decode(key))
    return BACKUP_MARKER + nonce + cipher.encrypt(nonce, plaintext, BACKUP_MARKER)


def open_backup(key, blob):
    body = blob[len(BACKUP_MARKER):]
    return AESGCM(base64.urlsafe_b64decode(key)).decrypt(body[:12], body[12:], BACKUP_MARKER)


class AppTestCase(unittest.TestCase):
    """Creates an isolated app so tests never touch the development database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATABASE": os.path.join(self._tmp.name, "test.db"),
            "SECRET_KEY": "test-secret-key",
            # A cheap hash keeps the suite fast; test_admin checks the real setting.
            "ADMIN_PASSWORD_METHOD": "scrypt:1024:8:1",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self._tmp.cleanup()

    def query(self, sql, params=()):
        with self.app.app_context():
            return get_db().execute(sql, params).fetchall()

    def count(self, table):
        allowed = {"participant", "attempt", "response", "scenario", "sus_response",
                   "lesson_view", "session_end", "admin_user", "admin_login_attempt"}
        if table not in allowed:
            raise ValueError(table)
        return self.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]  # nosec B608

    def token(self, client=None):
        """Return the client's CSRF token, visiting a form page if necessary."""
        client = client or self.client
        with client.session_transaction() as session:
            token = session.get("_csrf_token")
        if token is None:
            client.get("/consent")
            with client.session_transaction() as session:
                token = session.get("_csrf_token")
        return token

    def consent(self, client=None):
        """Give consent and return the new participant's random ID."""
        client = client or self.client
        response = client.post(
            "/consent", data={"adult": "yes", "agree": "yes", "csrf_token": self.token(client)}
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            return session["participant_id"]

    @staticmethod
    def scenario_id_from(html):
        return SCENARIO_FIELD.search(html).group(1)

    def label_of(self, scenario_id):
        return self.query("SELECT label FROM scenario WHERE id = ?", (scenario_id,))[0]["label"]

    def answer_current(self, url, correct=True, client=None):
        """Answer the item currently shown at url; returns the POST response."""
        client = client or self.client
        scenario_id = self.scenario_id_from(client.get(url).get_data(as_text=True))
        label = self.label_of(scenario_id)
        answer = label if correct else ("legitimate" if label == "phishing" else "phishing")
        return client.post(
            url,
            data={"scenario_id": scenario_id, "answer": answer, "csrf_token": self.token(client)},
        )

    def answer_pattern(self, url, pattern, client=None):
        """Answer one item per entry in pattern (True = correct, False = incorrect)."""
        for correct in pattern:
            self.answer_current(url, correct, client)

    def answer_pretest(self, correct=True, client=None):
        self.answer_pattern("/assessment/pre", [correct] * 12, client)

    def open_lessons(self, client=None):
        """Open the lessons page, which unlocks the practice phase."""
        return (client or self.client).get("/learn")

    def finish_practice(self, client=None):
        self.open_lessons(client)
        self.answer_pattern("/practice", [True] * 6, client)

    def answer_posttest(self, correct=True, client=None):
        self.answer_pattern("/assessment/post", [correct] * 12, client)

    def survey_data(self, ratings, client=None):
        """Form data for the SUS survey: q1..q10 plus the CSRF token."""
        data = {f"q{number}": str(rating) for number, rating in enumerate(ratings, start=1)}
        data["csrf_token"] = self.token(client)
        return data

    def reach_posttest(self, client=None):
        """Consent, finish the pre-test and the practice phase; returns the random ID."""
        participant_id = self.consent(client)
        self.answer_pretest(client=client)
        self.finish_practice(client)
        return participant_id

    def complete_session(self, pre_correct, post_correct, ratings=None):
        """Run one whole participant journey in a new browser session.

        pre_correct and post_correct are the numbers of correct answers out of 12.
        Returns the participant's random ID.
        """
        client = self.app.test_client()
        participant_id = self.consent(client)
        self.answer_pattern(
            "/assessment/pre", [True] * pre_correct + [False] * (12 - pre_correct), client)
        self.finish_practice(client)
        self.answer_pattern(
            "/assessment/post", [True] * post_correct + [False] * (12 - post_correct), client)
        if ratings is not None:
            client.post("/survey", data=self.survey_data(ratings, client))
        return participant_id

    def copy_session(self, source=None):
        """A second browser that holds a copy of the first one's session cookie."""
        source = source or self.client
        name = self.app.config["SESSION_COOKIE_NAME"]
        thief = self.app.test_client()
        thief.set_cookie(name, source.get_cookie(name).value)
        return thief

    def create_admin(self, username=ADMIN_USER, password=ADMIN_PASSWORD):
        """Create (or update) an administrator through the command-line tool."""
        return self.app.test_cli_runner().invoke(
            args=["create-admin", "--username", username, "--password", password])

    def admin_sign_in(self, username=ADMIN_USER, password=ADMIN_PASSWORD, client=None):
        client = client or self.client
        return client.post("/admin/login", data={
            "username": username, "password": password, "csrf_token": self.token(client)})
