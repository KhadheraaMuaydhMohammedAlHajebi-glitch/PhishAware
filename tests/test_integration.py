"""Integration tests (Unit 7): the modules working together through their real interfaces.

The unit tests check one module at a time. These cases check the seams between
modules: the security layer (M8) in front of every route, the path of an answer
from the form through scoring into the four places that report it, the
data-protection jobs acting on records that open sessions are using, the content
files feeding the templates, and a database that an earlier release wrote.
Nothing is replaced by a stand-in. Every case runs the real application against
a real SQLite file; the cases about concurrency use real threads.

Each case is listed in docs/test-plan.md. The first line of its docstring gives
the case ID, the requirement it verifies, and its title, which the report of
"python -m system_tests.run --level integration" prints.
"""

import csv
import html
import io
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlsplit

from flask import url_for
from werkzeug.security import generate_password_hash

from src import db, repository
from tests.helpers import (
    ADMIN_PASSWORD, ADMIN_USER, AppTestCase, new_backup_key, restart_app, watch_connections)

FIXTURES = Path(__file__).with_name("fixtures")
TAGS = re.compile(r"<[^>]+>")
STAT = re.compile(r'<p class="stat__value[^"]*">([+\-]?[0-9.]+)')
CUE_BARS = re.compile(
    r'aria-label="([^":]+): ([0-9]+)% correct before training, ([0-9]+)% after training"')
CUE_LABELS = {
    "sender_spoofing": "Sender spoofing",
    "deceptive_links": "Deceptive links",
    "urgency_threats": "Urgency or threats",
    "unexpected_attachments": "Unexpected attachments",
    "credential_requests": "Credential requests",
    "lookalike_websites": "Look-alike websites",
}
CUES = tuple(CUE_LABELS)

# Every route of the application, by the role that may use it. A route that is
# added later belongs to none of the three sets, and IT-02 fails until somebody
# has decided who may use it.
PUBLIC = {
    "static", "health.healthz", "consent.index", "consent.consent_form",
    "consent.give_consent", "consent.declined", "admin.login_form", "admin.login",
    "admin.logout",
}
PARTICIPANT = {
    "consent.dashboard", "consent.withdraw_confirm", "consent.withdraw", "consent.finish",
    "assessment.pre_item", "assessment.pre_answer", "assessment.pre_complete",
    "assessment.post_item", "assessment.post_answer", "assessment.post_complete",
    "learning.lessons", "practice.practice_item", "practice.practice_answer",
    "practice.feedback", "practice.practice_complete", "results.results",
    "survey.survey_form", "survey.survey_submit", "survey.survey_complete",
}
ADMINISTRATOR = {"admin.dashboard", "admin.export_csv"}
ROUTE_ARGUMENTS = {"practice.feedback": {"scenario_id": "P01"}}

# Sentences that Flask and Werkzeug put on their own error pages. The application
# answers every error in its own words, so none of them may reach a participant.
FRAMEWORK_WORDING = (
    "The requested URL was not found on the server",
    "The method is not allowed for the requested URL",
    "The data value transmitted exceeds the capacity limit",
    "The browser (or proxy) sent a request that this server could not understand",
    "The server encountered an internal error",
    "Traceback (most recent call last)",
)
HOSTILE = (
    "", " ", "\x00", "tést", "رمز الأمان",
    "\U0001F600" * 8, "A" * 5000, "' OR '1'='1' --", "<script>alert(1)</script>",
    "../../etc/passwd", "%00%0d%0a", "phishing\r\nSet-Cookie: stolen=1", "None", "-1", "1e9",
)
# Ten ratings each, chosen so that the four scores (100, 62.5, 87.5, and 50) have a
# mean with one decimal place: 75.0.
SUS_100 = [5, 1, 5, 1, 5, 1, 5, 1, 5, 1]
SUS_62_5 = [4, 2, 3, 3, 4, 2, 3, 3, 4, 3]
SUS_87_5 = [5, 2, 4, 1, 5, 2, 4, 2, 5, 1]
SUS_50 = [3] * 10
# (wrong positions before training, wrong positions after, ratings) for six people.
# The fifth answers no survey, and the sixth stops after the pre-assessment.
COHORT = (
    ({0, 1, 2, 3, 4, 5}, {0, 6}, SUS_100),
    ({1, 3, 5, 7}, {2}, SUS_62_5),
    ({2, 4, 6, 8, 10}, {3, 4, 5}, SUS_87_5),
    ({11}, {11}, SUS_50),
    ({0, 2, 4, 6, 8, 10, 11}, set(), None),
)


def visible(page):
    """The text of a page: tags removed, entities decoded, white space collapsed.

    Tags are removed without leaving a space, because a domain is rendered as
    several elements ("northbridge-admin" and ".example") that read as one word.
    """
    return " ".join(html.unescape(TAGS.sub("", page)).split())


def path_of(response):
    return urlsplit(response.headers.get("Location", "")).path


class IntegrationCase(AppTestCase):
    """Helpers that several integration cases share."""

    TABLES = ("participant", "attempt", "response", "lesson_view", "sus_response",
              "session_end", "admin_user")

    def snapshot(self):
        """How many rows each table holds: a cheap way to show that nothing was stored."""
        return {table: self.count(table) for table in self.TABLES}

    def text(self, url, client=None):
        return (client or self.client).get(url).get_data(as_text=True)

    def answer_positions(self, url, wrong, client):
        """Answer twelve items; the positions in `wrong` (0 to 11) are answered wrongly."""
        self.answer_pattern(url, [position not in wrong for position in range(12)], client)

    def journey(self, pre_wrong, post_wrong, ratings):
        """One whole journey in its own browser. Returns (client, random ID)."""
        client = self.app.test_client()
        participant_id = self.consent(client)
        self.answer_positions("/assessment/pre", pre_wrong, client)
        self.finish_practice(client)
        self.answer_positions("/assessment/post", post_wrong, client)
        if ratings is not None:
            client.post("/survey", data=self.survey_data(ratings, client))
        return client, participant_id

    def stored_answers(self, participant_id):
        """{phase: {cue: correct answers}} read straight from the response table."""
        rows = self.query(
            "SELECT a.phase AS phase, s.cue AS cue, SUM(r.is_correct) AS correct "
            "FROM attempt a JOIN response r ON r.attempt_id = a.id "
            "JOIN scenario s ON s.id = r.scenario_id "
            "WHERE a.participant_id = ? AND a.phase IN ('pre', 'post') "
            "GROUP BY a.phase, s.cue", (participant_id,))
        counts = {"pre": dict.fromkeys(CUES, 0), "post": dict.fromkeys(CUES, 0)}
        for row in rows:
            counts[row["phase"]][row["cue"]] = row["correct"]
        return counts

    def together(self, count, action):
        """Run action(0..count-1) in `count` threads that all start at the same moment."""
        barrier = threading.Barrier(count)
        results = [None] * count

        def run(index):
            barrier.wait()
            try:
                results[index] = action(index)
            except Exception as error:   # reported by the assertion that follows
                results[index] = error

        threads = [threading.Thread(target=run, args=(index,)) for index in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def handle_errors_as_in_production(self):
        """Let the application answer an unexpected error itself, as a deployed server does."""
        self.app.config["PROPAGATE_EXCEPTIONS"] = False


class SecurityLayerTests(IntegrationCase):
    """M8 in front of every other module."""

    VALID_FIELDS = {
        "adult": "yes", "agree": "yes", "scenario_id": "A01", "answer": "phishing",
        "username": ADMIN_USER, "password": ADMIN_PASSWORD,
        **{f"q{number}": "3" for number in range(1, 11)},
    }

    def test_01_every_post_route_refuses_a_request_without_a_valid_token(self):
        """IT-01 | NFR-09 | Every state-changing route refuses a request without a valid token"""
        self.create_admin()
        self.reach_posttest()
        self.answer_posttest()                      # every participant route is now open
        routes = sorted(rule.rule for rule in self.app.url_map.iter_rules()
                        if "POST" in rule.methods)
        self.assertGreaterEqual(len(routes), 9)
        before = self.snapshot()
        for route in routes:
            for label, token in (("no token", None), ("another session's token", "x" * 43)):
                with self.subTest(route=route, token=label):
                    data = dict(self.VALID_FIELDS)
                    if token is not None:
                        data["csrf_token"] = token
                    self.assertEqual(self.client.post(route, data=data).status_code, 400)
        self.assertEqual(self.snapshot(), before)   # nothing was stored, deleted, or ended
        self.assertEqual(self.client.get("/results").status_code, 200)   # still signed in

    def test_02_every_route_is_restricted_to_the_role_it_belongs_to(self):
        """IT-02 | FR-01, NFR-10 | Every route is restricted to the role it belongs to"""
        endpoints = {rule.endpoint for rule in self.app.url_map.iter_rules()}
        self.assertEqual(endpoints, PUBLIC | PARTICIPANT | ADMINISTRATOR)
        self.assertFalse(PUBLIC & PARTICIPANT or PUBLIC & ADMINISTRATOR
                         or PARTICIPANT & ADMINISTRATOR)

        self.create_admin()
        visitor = self.app.test_client()
        administrator = self.app.test_client()
        self.admin_sign_in(client=administrator)
        participant = self.app.test_client()
        self.consent(participant)

        def requests(endpoint_names):
            for rule in self.app.url_map.iter_rules():
                if rule.endpoint in endpoint_names:
                    with self.app.test_request_context():
                        url = url_for(rule.endpoint, **ROUTE_ARGUMENTS.get(rule.endpoint, {}))
                    for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                        yield method, url

        def send(client, method, url):
            data = {"csrf_token": self.token(client)} if method == "POST" else None
            return client.open(url, method=method, data=data)

        before = self.snapshot()
        checked = 0
        for method, url in requests(PARTICIPANT):
            for role, client in (("visitor", visitor), ("administrator", administrator)):
                with self.subTest(route=f"{method} {url}", role=role):
                    response = send(client, method, url)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(path_of(response), "/consent")
                    checked += 1
        for method, url in requests(ADMINISTRATOR):
            for role, client in (("visitor", visitor), ("participant", participant)):
                with self.subTest(route=f"{method} {url}", role=role):
                    response = send(client, method, url)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(path_of(response), "/admin/login")
                    checked += 1
        self.assertEqual(checked, 2 * (len(PARTICIPANT) + len(ADMINISTRATOR)))
        self.assertEqual(self.snapshot(), before)

    def test_03_every_page_of_every_role_carries_the_security_headers(self):
        """IT-03 | NFR-08, NFR-09 | Every page of every role carries the security headers"""
        pages = []

        def keep(label, response):
            pages.append((label, response))
            return response

        client = self.client
        keep("start page", client.get("/"))
        keep("consent", client.get("/consent"))
        keep("declined", client.get("/consent/declined"))
        keep("page not found", client.get("/no-such-page"))
        keep("refused form", client.post("/consent", data={"adult": "yes", "agree": "yes"}))
        self.consent()
        keep("dashboard", client.get("/dashboard"))
        keep("withdrawal question", client.get("/withdraw"))
        keep("assessment item", client.get("/assessment/pre"))
        keep("redirect after an answer", self.answer_current("/assessment/pre"))
        self.answer_pattern("/assessment/pre", [True] * 11)
        keep("pre-assessment complete", client.get("/assessment/pre/complete"))
        keep("lessons", client.get("/learn"))
        keep("practice item", client.get("/practice"))
        answered = self.answer_current("/practice", correct=False)
        keep("feedback", client.get(answered.headers["Location"]))
        self.answer_pattern("/practice", [True] * 5)
        keep("practice summary", client.get("/practice/complete"))
        keep("post-assessment item", client.get("/assessment/post"))
        self.answer_posttest()
        keep("post-assessment complete", client.get("/assessment/post/complete"))
        keep("results", client.get("/results"))
        keep("survey", client.get("/survey"))
        keep("refused survey", client.post("/survey", data={"csrf_token": self.token()}))
        client.post("/survey", data=self.survey_data(SUS_50))
        keep("survey complete", client.get("/survey/complete"))
        keep("wrong method", client.get("/finish"))
        keep("finished", client.post("/finish", data={"csrf_token": self.token()}))
        self.consent()
        keep("withdrawn", client.post("/withdraw", data={"csrf_token": self.token()}))

        self.create_admin()
        keep("sign-in form", client.get("/admin/login"))
        keep("refused sign-in", self.admin_sign_in(password="not-the-password"))
        self.admin_sign_in()
        keep("dashboard below the threshold", client.get("/admin"))
        keep("export refused", client.get("/admin/export.csv"))
        for journey in COHORT:
            self.journey(*journey)
        keep("dashboard with statistics", client.get("/admin"))
        export = keep("export", client.get("/admin/export.csv"))
        self.assertEqual(export.mimetype, "text/csv")
        keep("sign-out", client.post("/admin/logout", data={"csrf_token": self.token()}))

        self.assertGreaterEqual(len(pages), 30)
        for label, response in pages:
            with self.subTest(page=label):
                headers = response.headers
                policy = headers.get("Content-Security-Policy", "")
                for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                                  "object-src 'none'", "base-uri 'none'", "form-action 'self'",
                                  "frame-ancestors 'none'"):
                    self.assertIn(directive, policy)
                self.assertNotIn("unsafe", policy)
                self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
                self.assertEqual(headers.get("X-Frame-Options"), "DENY")
                self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
                self.assertIn("geolocation=()", headers.get("Permissions-Policy", ""))
                self.assertEqual(headers.get("Cache-Control"), "no-store")
                for cookie in headers.getlist("Set-Cookie"):
                    self.assertIn("HttpOnly", cookie)
                    self.assertIn("SameSite=Lax", cookie)

    def test_04_hostile_input_in_any_field_is_refused_without_a_server_error(self):
        """IT-04 | NFR-09 | Hostile input in any field is refused without a server error"""
        self.handle_errors_as_in_production()
        self.app.config.update(ADMIN_MAX_FAILED_LOGINS=10_000, ADMIN_MAX_FAILED_TOTAL=10_000)
        self.create_admin()
        visitor = self.app.test_client()
        early = self.app.test_client()               # in the pre-assessment
        self.consent(early)
        practising = self.app.test_client()          # in the practice phase
        self.consent(practising)
        self.answer_pretest(client=practising)
        self.open_lessons(practising)
        surveyed = self.app.test_client()            # at the survey
        self.reach_posttest(surveyed)
        self.answer_posttest(client=surveyed)
        forms = (
            (visitor, "/consent", {"adult": "yes", "agree": "yes"}),
            (early, "/assessment/pre", {"scenario_id": "A01", "answer": "phishing"}),
            (practising, "/practice", {"scenario_id": "P01", "answer": "phishing"}),
            (surveyed, "/survey", {f"q{number}": "3" for number in range(1, 11)}),
            (surveyed, "/finish", {}),
            (early, "/withdraw", {}),
            (visitor, "/admin/login", {"username": ADMIN_USER, "password": ADMIN_PASSWORD}),
            (visitor, "/admin/logout", {}),
        )
        before = self.snapshot()
        sent = 0
        for client, url, fields in forms:
            valid = dict(fields, csrf_token=self.token(client))
            for field in valid:
                for value in HOSTILE:
                    with self.subTest(form=url, field=field, value=value[:20]):
                        response = client.post(url, data=dict(valid, **{field: value}))
                        self.assertLess(response.status_code, 500)
                        self.assertNotIn(b"Traceback", response.data)
                        sent += 1
        for value in HOSTILE[1:]:
            with self.subTest(url="/practice/feedback/", value=value[:20]):
                response = practising.get("/practice/feedback/" + quote(value, safe=""))
                self.assertIn(response.status_code, (400, 404))
        self.assertEqual(sent, sum(len(fields) + 1 for _c, _u, fields in forms) * len(HOSTILE))
        self.assertEqual(self.snapshot(), before)    # no hostile value was stored as a record
        self.assertEqual(early.get("/dashboard").status_code, 200)   # and nobody was signed out


class ReportingPathTests(IntegrationCase):
    """One answer, four reports: M2 and M4 store, M5 scores, M7 and the command line report."""

    def test_05_four_views_of_one_cohort_agree_with_the_stored_answers(self):
        """IT-05 | FR-08, FR-11 | Results page, dashboard, export, and command line agree"""
        self.create_admin()
        people = [self.journey(*journey) for journey in COHORT]
        unfinished = self.app.test_client()
        self.consent(unfinished)
        self.answer_pretest(correct=False, client=unfinished)     # 0%, and no post-assessment
        truth = [self.stored_answers(participant_id) for _client, participant_id in people]
        percent = [{phase: sum(stored[phase].values()) / 12 * 100 for phase in ("pre", "post")}
                   for stored in truth]

        # 1. Each participant's own results page.
        for (client, _id), stored, exact in zip(people, truth, percent):
            page = self.text("/results", client)
            shown = [float(value) for value in STAT.findall(page)[:3]]
            self.assertEqual(shown, [round(exact["pre"], 1), round(exact["post"], 1),
                                     round(round(exact["post"], 1) - round(exact["pre"], 1), 1)])
            bars = {name: (int(a), int(b)) for name, a, b in CUE_BARS.findall(page)}
            self.assertEqual(bars, {
                CUE_LABELS[cue]: (stored["pre"][cue] * 50, stored["post"][cue] * 50)
                for cue in CUES})

        # 2. The de-identified export: the same people, in an order that says nothing.
        self.admin_sign_in()
        rows = list(csv.reader(io.StringIO(self.text("/admin/export.csv"))))[1:]
        scores = [100.0, 62.5, 87.5, 50.0, None]
        expected_rows = sorted(
            [f"{round(exact['pre'], 1)}", f"{round(exact['post'], 1)}",
             "" if score is None else f"{score}"]
            + [str(stored["pre"][cue]) for cue in CUES]
            + [str(stored["post"][cue]) for cue in CUES]
            for stored, exact, score in zip(truth, percent, scores))
        self.assertEqual(sorted(row[2:4] + row[5:] for row in rows), expected_rows)

        # 3. The dashboard: complete cases only, the unfinished participant counted apart.
        mean_pre = round(sum(exact["pre"] for exact in percent) / 5, 1)
        mean_post = round(sum(exact["post"] for exact in percent) / 5, 1)
        mean_gain = round(sum(exact["post"] - exact["pre"] for exact in percent) / 5, 1)
        dashboard = self.text("/admin")
        figures = [float(value) for value in STAT.findall(dashboard)]
        self.assertEqual(figures[:5], [6, 6, 5, 5, 4])   # consented ... finished the survey
        self.assertEqual(figures[5:8], [mean_pre, mean_post, mean_gain])
        self.assertIn("Learning outcome (RQ1), n = 5", dashboard)
        pooled = {CUE_LABELS[cue]: tuple(
            round(sum(stored[phase][cue] for stored in truth) / 10 * 100)
            for phase in ("pre", "post")) for cue in CUES}
        self.assertEqual(
            {name: (int(a), int(b)) for name, a, b in CUE_BARS.findall(dashboard)}, pooled)
        self.assertIn("<strong>75.0</strong> (median 75.0) from 4 survey responses", dashboard)

        # 4. The command-line report an operator reads in a terminal.
        output = self.app.test_cli_runner().invoke(args=["analytics"]).output
        self.assertIn("Participants who consented: 6", output)
        self.assertIn(f"RQ1  n = 5: mean pre {mean_pre:.1f}%, mean post {mean_post:.1f}%, "
                      f"mean gain {mean_gain:+.1f} points", output)
        for cue in CUES:
            before, after = pooled[CUE_LABELS[cue]]
            self.assertRegex(
                output, rf"{re.escape(CUE_LABELS[cue])}\s+{100 - before}% ->\s+{100 - after}%")
        self.assertIn("RQ3  Mean SUS score: 75.0", output)

    def test_06_a_second_consent_in_a_live_session_creates_no_second_record(self):
        """IT-06 | FR-02, FR-10 | A second consent in a live session creates no second record"""
        participant_id = self.consent()
        self.answer_pattern("/assessment/pre", [True] * 3)
        response = self.client.post(
            "/consent", data={"adult": "yes", "agree": "yes", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(path_of(response), "/dashboard")
        self.assertEqual(self.count("participant"), 1)   # no abandoned record that nobody
        with self.client.session_transaction() as session:   # could withdraw any more
            self.assertEqual(session["participant_id"], participant_id)
        self.assertIn("3 of 12 answered", self.text("/dashboard"))


class ConcurrencyTests(IntegrationCase):
    """Several requests at the same instant: real threads, one SQLite file (NFR-05)."""

    def test_07_simultaneous_requests_store_each_answer_and_each_consent_once(self):
        """IT-07 | NFR-05 | Simultaneous requests store each answer and each consent once"""
        # Twelve copies of one answer, as from an impatient double click in two tabs.
        participant_id = self.consent()
        item = self.scenario_id_from(self.text("/assessment/pre"))
        form = {"scenario_id": item, "answer": self.label_of(item), "csrf_token": self.token()}
        tabs = [self.copy_session() for _ in range(12)]
        replies = self.together(12, lambda index: tabs[index].post("/assessment/pre", data=form))
        self.assertEqual([getattr(reply, "status_code", reply) for reply in replies], [302] * 12)
        self.assertEqual(self.count("response"), 1)

        # The twelfth answer of an assessment, eight times at once: scored exactly once.
        self.answer_pattern("/assessment/pre", [True] * 10)
        item = self.scenario_id_from(self.text("/assessment/pre"))
        form.update(scenario_id=item, answer=self.label_of(item))
        replies = self.together(8, lambda index: tabs[index].post("/assessment/pre", data=form))
        self.assertEqual([getattr(reply, "status_code", reply) for reply in replies], [302] * 8)
        attempts = self.query("SELECT phase, score FROM attempt WHERE participant_id = ?",
                              (participant_id,))
        self.assertEqual([(row["phase"], row["score"]) for row in attempts], [("pre", 100.0)])
        self.assertEqual(self.count("response"), 12)

        # Twenty people agree at the same moment: twenty records, forms still balanced.
        browsers = [self.app.test_client() for _ in range(20)]
        tokens = [self.token(browser) for browser in browsers]
        replies = self.together(20, lambda index: browsers[index].post(
            "/consent", data={"adult": "yes", "agree": "yes", "csrf_token": tokens[index]}))
        self.assertEqual([getattr(reply, "status_code", reply) for reply in replies], [302] * 20)
        rows = self.query("SELECT seq, id, form_order FROM participant WHERE seq > 1")
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row["id"] for row in rows}), 20)
        for row in rows:
            self.assertEqual(row["form_order"], "AB" if row["seq"] % 2 else "BA")
        self.assertEqual(sum(1 for row in rows if row["form_order"] == "AB"), 10)

    def test_15_the_web_service_closes_no_connection_while_it_serves_requests(self):
        """IT-15 | NFR-05 | The web service closes no database connection while it serves"""
        # Closing a connection in one thread can cancel the file lock that another
        # thread of the process has just taken (defect D-7, src/db.py). Eight
        # people answer their pre-assessment at the same moment, as the eight
        # threads of the deployed service would serve them.
        labels = {row["id"]: row["label"] for row in self.query("SELECT id, label FROM scenario")}
        browsers = [self.app.test_client() for _ in range(8)]
        tokens = [self.token(browser) for browser in browsers]

        def session(index):
            browser = browsers[index]
            statuses = [browser.post("/consent", data={
                "adult": "yes", "agree": "yes", "csrf_token": tokens[index]}).status_code]
            token = self.token(browser)     # consent starts a new session, with a new token
            for _ in range(12):
                item = self.scenario_id_from(browser.get("/assessment/pre").get_data(as_text=True))
                statuses.append(browser.post("/assessment/pre", data={
                    "scenario_id": item, "answer": labels[item],
                    "csrf_token": token}).status_code)
            return statuses + [browser.get("/dashboard").status_code]

        with watch_connections() as watch:
            replies = self.together(8, session)
            self.assertEqual(replies, [[302] * 13 + [200]] * 8)
            self.assertEqual(watch.closed, [])
            # One connection for each request in progress at the same moment, at most.
            self.assertLessEqual(len(watch.opened), 8)
            in_service = len(watch.opened)

            # The jobs run in a process of their own. Each command there opens one
            # connection and closes it; the connections of the web service stay.
            key = new_backup_key()
            self.app.config["BACKUP_KEY"] = key
            for command in (["purge-expired"], ["backup-db"], ["analytics"]):
                result = self.app.test_cli_runner().invoke(args=command)
                self.assertEqual(result.exit_code, 0, result.output)
            by_commands = watch.opened[in_service:]   # the backup also opens one in memory
            self.assertGreaterEqual(len(by_commands), 3)
            self.assertCountEqual(watch.closed, by_commands)
            self.assertEqual(db.pool_of(self.app).idle, in_service)

            # The next requests are served by the connections that were kept.
            self.assertEqual([browser.get("/dashboard").status_code for browser in browsers],
                             [200] * 8)
            self.assertEqual(len(watch.opened), in_service + len(by_commands))
            self.assertCountEqual(watch.closed, by_commands)

        attempts = self.query("SELECT phase, score FROM attempt")
        self.assertEqual([(row["phase"], row["score"]) for row in attempts], [("pre", 100.0)] * 8)
        self.assertEqual(self.count("response"), 96)
        self.assertEqual(self.query("PRAGMA integrity_check")[0][0], "ok")


class DataProtectionJobTests(IntegrationCase):
    """The jobs of M8 acting on records that open sessions are using."""

    def setUp(self):
        super().setUp()
        self.app.config["BACKUP_KEY"] = new_backup_key()
        self.app.config["BACKUP_DIR"] = str(Path(self._tmp.name) / "backups")

    def command(self, *arguments):
        result = self.app.test_cli_runner().invoke(args=list(arguments))
        self.assertEqual(result.exit_code, 0, result.output)
        return result.output

    def test_08_the_retention_job_ends_an_expired_session_and_leaves_the_others(self):
        """IT-08 | NFR-12 | The retention job ends an expired session and leaves the others"""
        self.handle_errors_as_in_production()
        expired = self.app.test_client()
        expired_id = self.consent(expired)
        self.answer_pattern("/assessment/pre", [True] * 3, expired)
        current = self.app.test_client()
        self.consent(current)
        self.answer_pattern("/assessment/pre", [True] * 5, current)
        long_ago = datetime.now(timezone.utc) - timedelta(days=91)
        with self.app.app_context():
            db.get_db().execute("UPDATE participant SET consented_at = ? WHERE id = ?",
                                (long_ago.isoformat(timespec="seconds"), expired_id))
            db.get_db().commit()
        item = self.scenario_id_from(self.text("/assessment/pre", expired))   # a form left open
        form = {"scenario_id": item, "answer": "phishing", "csrf_token": self.token(expired)}

        self.assertIn("Deleted 1 participant record(s) older than 90 days.",
                      self.command("run-jobs", "--once"))

        for response in (expired.post("/assessment/pre", data=form), expired.get("/dashboard"),
                         expired.get("/assessment/pre")):
            self.assertEqual(response.status_code, 302)     # sent back to the start, no error
            self.assertEqual(path_of(response), "/consent")
        self.assertEqual(self.count("participant"), 1)
        self.assertEqual(self.count("response"), 5)         # the expired answers are gone
        self.assertIn("5 of 12 answered", self.text("/dashboard", current))
        self.assertEqual(self.answer_current("/assessment/pre", client=current).status_code, 302)
        self.assertEqual(self.count("response"), 6)

    def test_09_a_session_continues_from_a_backup_taken_in_the_middle_of_it(self):
        """IT-09 | NFR-05 | A session continues from a backup taken in the middle of it"""
        early = self.app.test_client()
        self.consent(early)
        self.answer_pattern("/assessment/pre", [True] * 5, early)
        self.assertIn("Encrypted backup written", self.command("backup-db"))
        backup = next(Path(self.app.config["BACKUP_DIR"]).glob("*.db.enc"))
        # After the backup: seven more answers, the lessons, and a newcomer.
        self.answer_pattern("/assessment/pre", [False] * 7, early)
        self.open_lessons(early)
        late = self.app.test_client()
        self.consent(late)

        self.assertIn("1 participant record(s)", self.command("restore-db", str(backup), "--yes"))

        self.assertIn("5 of 12 answered", self.text("/dashboard", early))
        self.assertIn("<strong>6</strong> of 12", self.text("/assessment/pre", early))
        self.assertEqual(path_of(early.get("/learn")), "/assessment/pre")   # not unlocked yet
        self.answer_pattern("/assessment/pre", [True] * 7, early)
        self.assertIn("Baseline 100.0%", self.text("/dashboard", early))
        # The newcomer consented after the snapshot, so the restored data does not hold them.
        response = late.get("/dashboard")
        self.assertEqual((response.status_code, path_of(response)), (302, "/consent"))
        self.assertEqual(self.query("PRAGMA integrity_check")[0][0], "ok")

    def test_10_a_failed_sign_in_stores_nothing_that_was_typed(self):
        """IT-10 | NFR-11 | A failed sign-in stores nothing that was typed into the form"""
        self.create_admin()
        typed = "Tr0ub4dor-typed-into-the-wrong-box"     # a password in the user-name field
        self.assertEqual(self.admin_sign_in(username=typed, password="x").status_code, 401)
        database = Path(self.app.config["DATABASE"])
        self.assertEqual(self.count("admin_login_attempt"), 1)
        self.assertFalse(typed.lower().encode() in database.read_bytes().lower(),
                         "the database file holds the text that was typed as the user name")
        # The limit of five attempts still applies to that name, and to no other.
        for _ in range(4):
            self.assertEqual(self.admin_sign_in(username=typed, password="x").status_code, 401)
        self.assertEqual(self.admin_sign_in(username=typed, password="x").status_code, 429)
        self.assertEqual(self.admin_sign_in().status_code, 302)
        # Nobody signs in for a day: the daily job removes what is left of the attempts.
        self.client.post("/admin/logout", data={"csrf_token": self.token()})
        with self.app.app_context():
            db.get_db().execute(
                "UPDATE admin_login_attempt SET attempted_at = '2026-01-01T00:00:00+00:00'")
            db.get_db().commit()
        self.assertEqual(self.count("admin_login_attempt"), 5)
        self.command("run-jobs", "--once")
        self.assertEqual(self.count("admin_login_attempt"), 0)


class ContentTests(IntegrationCase):
    """The content files (data tier) through the templates (presentation tier)."""

    def walk(self, client):
        """One participant's whole journey; returns the IDs served in each phase."""
        served = {"pre": [], "practice": [], "post": []}
        bank = {row["id"]: repository.scenario_content(row) for row in self.rows}
        self.consent(client)

        def assessment(phase):
            url = f"/assessment/{phase}"
            for _ in range(12):
                page = self.text(url, client)
                scenario = bank[self.scenario_id_from(page)]
                served[phase].append(scenario["id"])
                shown = visible(page)
                source = scenario["email"] if scenario["channel"] == "email" else scenario["web"]
                self.assertIn(source["subject" if scenario["channel"] == "email" else "heading"],
                              shown, scenario["id"])
                # An assessment item shows the specimen and nothing that gives the answer away.
                feedback = scenario["feedback"]
                for answer_text in [feedback["explanation"], feedback["safe_action"],
                                    *feedback["cues"]]:
                    self.assertNotIn(answer_text, shown, scenario["id"])
                self.answer_current(url, client=client)

        assessment("pre")
        lessons = visible(self.text("/learn", client))
        for cue in {scenario["cue"] for scenario in bank.values()}:
            self.assertIn(cue, self.lesson_cues)
        for lesson in self.lessons:
            for part in (lesson["title"], lesson["summary"], lesson["example"],
                         lesson["safe_action"], *lesson["look_for"]):
                self.assertIn(part, lessons, lesson["cue"])
        for _ in range(6):
            page = self.text("/practice", client)
            scenario = bank[self.scenario_id_from(page)]
            served["practice"].append(scenario["id"])
            answered = self.answer_current("/practice", correct=False, client=client)
            shown = visible(self.text(answered.headers["Location"], client))
            feedback = scenario["feedback"]
            for part in (feedback["explanation"], feedback["safe_action"], *feedback["cues"]):
                self.assertIn(part, shown, scenario["id"])       # immediate, cue by cue (FR-06)
            self.assertIn(CUE_LABELS[scenario["cue"]].lower(), shown)
        assessment("post")
        return served

    def test_11_every_scenario_and_lesson_reaches_the_screen_and_no_item_leaks_its_answer(self):
        """IT-11 | FR-04 to FR-06 | Every scenario and lesson is shown; no item leaks its answer"""
        from src.modules.learning import load_lessons
        with self.app.app_context():
            self.rows = [row for pool in "ABP" for row in repository.scenarios_for_pool(pool)]
            self.lessons = load_lessons()
        self.lesson_cues = {lesson["cue"] for lesson in self.lessons}
        self.assertEqual(self.lesson_cues, set(CUES))
        first = self.walk(self.app.test_client())       # Form A, then Form B
        second = self.walk(self.app.test_client())      # Form B, then Form A
        self.assertEqual(first["pre"], second["post"])
        self.assertEqual(first["post"], second["pre"])
        self.assertEqual(first["practice"], second["practice"])
        for served in (first, second):
            everything = served["pre"] + served["practice"] + served["post"]
            self.assertEqual(len(everything), 30)
            self.assertEqual(len(set(everything)), 30)   # nobody meets an item twice
        self.assertEqual(sorted(first["pre"] + first["practice"] + first["post"]),
                         sorted(row["id"] for row in self.rows))


class ErrorPageTests(IntegrationCase):
    """Whatever goes wrong, the answer is the application's own page (M8)."""

    def test_12_every_error_is_answered_in_the_applications_own_words(self):
        """IT-12 | NFR-02, NFR-09 | Every error is answered in the application's own words"""
        self.handle_errors_as_in_production()
        self.create_admin()
        self.consent()
        administrator = self.app.test_client()
        self.admin_sign_in(client=administrator)
        with mock.patch.object(repository, "scenarios_for_pool", side_effect=RuntimeError("x")):
            failed = self.client.get("/assessment/pre")
        cases = (
            (404, "unknown address", self.client.get("/no-such-page")),
            (405, "wrong method", self.client.get("/finish")),
            (400, "form without a token", self.client.post("/withdraw")),
            (403, "export below the threshold", administrator.get("/admin/export.csv")),
            (413, "oversized form", self.client.post(
                "/consent", data={"csrf_token": self.token(), "adult": "y" * 600_000})),
            (500, "unexpected failure", failed),
        )
        for status, label, response in cases:
            with self.subTest(error=label):
                page = response.get_data(as_text=True)
                self.assertEqual(response.status_code, status)
                self.assertTrue(   # the application's layout, with a way back
                    '<main id="main"' in page and "Go to the start page" in page,
                    f"not the application's own page: {visible(page)[:90]!r}")
                self.assertIn("Content-Security-Policy", response.headers)
                for sentence in FRAMEWORK_WORDING:
                    self.assertFalse(sentence in page, f"the framework's wording: {sentence!r}")
        self.assertEqual(self.count("participant"), 1)            # nothing was withdrawn


class UpgradeTests(IntegrationCase):
    """A database written by an earlier release, opened by this one.

    tests/fixtures holds the schema files of releases 0.5.1 and 0.6.0, copied
    from their tags, so the older database is the real one and not an imitation.
    """

    def database_of(self, release, populate):
        path = str(Path(self._tmp.name) / f"written-by-{release}.db")
        connection = sqlite3.connect(path)
        connection.executescript(
            (FIXTURES / f"schema-{release}.sql").read_text(encoding="utf-8"))
        db.seed_scenarios(connection, self.app.config["SCENARIO_FILE"])
        populate(connection)
        connection.commit()
        connection.close()
        return path

    def start_this_release_on(self, path):
        self.app = restart_app({
            "TESTING": True, "DATABASE": path, "SECRET_KEY": "test-secret-key",
            "ADMIN_PASSWORD_METHOD": "scrypt:1024:8:1"})
        self.client = self.app.test_client()

    @staticmethod
    def participant_after_the_pre_assessment(connection):
        """A participant of the older release who has finished the pre-assessment."""
        participant_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO participant (id, consent_version, consented_at, form_order) "
            "VALUES (?, '1.0', ?, 'AB')", (participant_id, now))
        attempt = connection.execute(
            "INSERT INTO attempt (participant_id, phase, form, started_at, completed_at, score) "
            "VALUES (?, 'pre', 'A', ?, ?, 75.0)", (participant_id, now, now)).lastrowid
        items = connection.execute(
            "SELECT id, label FROM scenario WHERE pool = 'A' ORDER BY position").fetchall()
        for position, (scenario_id, label) in enumerate(items):
            correct = position < 9
            other = "legitimate" if label == "phishing" else "phishing"
            connection.execute(
                "INSERT INTO response (attempt_id, scenario_id, answer, is_correct, answered_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (attempt, scenario_id, label if correct else other, int(correct), now))
        return participant_id

    def resume(self, participant_id):
        """The participant's browser still holds the session cookie of the older release."""
        with self.client.session_transaction() as session:
            session["participant_id"] = participant_id
            session["_csrf_token"] = "token-from-before-the-upgrade"

    def finish_the_journey(self):
        self.assertIn("Baseline 75.0%", self.text("/dashboard"))
        self.finish_practice()
        self.answer_posttest()
        self.assertIn("+25.0 points", self.text("/results"))
        self.client.post("/survey", data=self.survey_data(SUS_100))
        finished = self.client.post("/finish", data={"csrf_token": self.token()})
        self.assertEqual(finished.status_code, 200)
        self.assertIn(b"signed out of this browser", finished.data)

    def test_13_a_database_of_release_0_5_1_serves_participants_and_administrators(self):
        """IT-13 | NFR-05, NFR-07 | A database written by release 0.5.1 works after the upgrade"""
        created = []
        path = self.database_of(
            "0.5.1", lambda connection: created.append(
                self.participant_after_the_pre_assessment(connection)))
        self.start_this_release_on(path)
        self.resume(created[0])
        self.finish_the_journey()
        # Release 0.5.1 had no administrator; the first one is created after the upgrade.
        result = self.create_admin()
        self.assertEqual(result.exit_code, 0, result.output or repr(result.exception))
        self.assertEqual(self.admin_sign_in().status_code, 302)
        page = self.text("/admin")
        self.assertIn("1 of 5 participants have finished both assessments", page)
        self.assertEqual(self.query("PRAGMA integrity_check")[0][0], "ok")

    def test_14_a_database_of_release_0_6_0_keeps_its_records_and_its_administrator(self):
        """IT-14 | NFR-05, NFR-07 | A database written by release 0.6.0 works after the upgrade"""
        created = []

        def populate(connection):
            created.append(self.participant_after_the_pre_assessment(connection))
            connection.execute(
                "INSERT INTO admin_user (username, password_hash, session_stamp) "
                "VALUES (?, ?, 'stamp-of-release-0.6.0')",
                (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD, method="scrypt:1024:8:1")))
            connection.execute(
                "INSERT INTO admin_login_attempt (username, attempted_at) VALUES (?, ?)",
                ("somebody-else", datetime.now(timezone.utc).isoformat(timespec="seconds")))

        self.start_this_release_on(self.database_of("0.6.0", populate))
        self.resume(created[0])
        self.finish_the_journey()
        administrator = self.app.test_client()
        self.assertEqual(self.admin_sign_in(password="not-it", client=administrator).status_code,
                         401)
        self.assertEqual(self.admin_sign_in(client=administrator).status_code, 302)
        self.assertIn("1 of 5 participants have finished both assessments",
                      self.text("/admin", administrator))
        self.assertEqual(self.count("participant"), 1)
        self.assertEqual(self.count("response"), 30)
        self.assertEqual(self.query("PRAGMA integrity_check")[0][0], "ok")
