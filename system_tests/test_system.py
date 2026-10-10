"""System tests: the complete, deployed system, from the outside.

The cases use a running instance the way its users do. Cases ST-01 to ST-10
drive a real browser through whole journeys, including the awkward ones: a
phone, no JavaScript, a closed browser, an impatient double click, two tabs,
the Back button, typed addresses, and a shared computer. Cases ST-11 to ST-15
talk to the server directly and check what travels over the wire. Cases ST-16
to ST-18 look at behaviour under simultaneous use, after a crash, and while
backups are being written.

The cases compare what the system shows with what the scripted answers must
produce (harness.Expected), and they read the administrator's participation
counts before and after an action. They never open the database.
"""

import base64
import json
import re
import statistics
import subprocess  # nosec B404 - runs only the tester's own crash command
import threading
import time
import uuid
from urllib.parse import quote, urlsplit

from system_tests.harness import (
    CUE_LABELS, CUES, PHONE, REDIRECTS, Administrator, Client, Participant, SystemCase,
    results_shown, settings, wait_until_healthy,
)

LINK = re.compile(r'(?:href|src|action)="([^"]+)"')
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
VERSION = re.compile(r"PhishAware prototype ([0-9.]+)")
POLICY = ("default-src 'self'", "script-src 'self'", "style-src 'self'", "object-src 'none'",
          "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'")
HOSTILE = ("", "\x00", "tést", "رمز", "\U0001F600" * 4, "A" * 5000,
           "' OR '1'='1' --", "<script>alert(1)</script>", "../../etc/passwd")
RATINGS = (5, 1, 5, 2, 4, 1, 5, 1, 4, 2)          # scores 90.0
RESTART_SETTLE_SECONDS = 3                        # see case ST-17


def expected_bars(expected):
    return {CUE_LABELS[cue]: (expected.cue_percent("pre", cue), expected.cue_percent("post", cue))
            for cue in CUES}


class JourneyTests(SystemCase):
    """Whole journeys in a real browser (functional and edge cases)."""

    def assert_results(self, user):
        """The results page shows exactly what the scripted answers must produce."""
        user.open("/results")
        scores, bars = user.results_shown()
        expected = user.expected
        self.assertEqual(scores, [expected.percent("pre"), expected.percent("post"), expected.gain])
        self.assertEqual(bars, expected_bars(expected))

    def test_01_complete_journey_on_a_desktop_browser(self):
        """ST-01 | FR-01 to FR-10 |
        A complete journey in a desktop browser reports the right results"""
        administrator = self.administrator()
        before = administrator.counts()
        user = self.browser_user()
        user.journey(pre_wrong={0, 3, 7, 10}, post_wrong={5}, ratings=RATINGS, finish=False)
        self.assert_results(user)
        self.assertEqual(user.expected.gain, 25.0)        # 8 of 12, then 11 of 12
        self.shot(user, "results")
        user.finish()
        self.assertEqual(user.heading(), "You are signed out of this browser")
        after = administrator.counts()
        self.assertEqual(after, {name: count + 1 for name, count in before.items()})
        self.assertGreaterEqual(user.screens, 45)
        self.assert_no_problems(user)
        self.note = f"{user.screens} screens; 66.7% before, 91.7% after, gain +25.0 points"

    def test_02_complete_journey_on_a_phone(self):
        """ST-02 | NFR-04 | The journey works on a 360 px touch screen without sideways scrolling"""
        user = self.browser_user(size=PHONE, touch=True)
        wide = []
        user.checks.append(
            lambda page: user.scrolls_sideways(page) and wide.append(user.path(page)))
        user.journey(pre_wrong={1}, post_wrong=set(), ratings=RATINGS)
        self.assertEqual(wide, [], "these screens scroll sideways at 360 px")
        self.assertEqual(user.heading(), "You are signed out of this browser")
        self.assertGreaterEqual(user.screens, 45)
        self.assert_no_problems(user)

    def test_03_complete_journey_without_javascript(self):
        """ST-03 | NFR-04, NFR-05 | The journey completes with JavaScript switched off"""
        user = self.browser_user(javascript=False)
        user.journey(pre_wrong={2, 4}, post_wrong={2}, ratings=RATINGS, finish=False)
        self.assert_results(user)
        user.finish()
        self.assertEqual(user.heading(), "You are signed out of this browser")

    def test_04_interrupted_journey_resumes_after_the_browser_is_closed(self):
        """ST-04 | NFR-05 |
        An interrupted journey resumes where it stopped after the browser is closed"""
        first = self.browser_user()
        first.consent()
        first.assessment("pre", wrong={0}, count=5)
        storage = first.context.storage_state()      # what the browser keeps on disk
        expected = first.expected
        first.close()
        self._users.remove(first)

        again = self.browser_user(storage=storage)
        again.expected = expected
        again.open("/")
        self.assertEqual(again.path(), "/dashboard")
        self.assertEqual(again.answered(), 5)
        again.follow("a[href$='/assessment/pre']")
        self.assertIn("6 of 12", again.main_text())
        again.assessment("pre", count=7)
        again.open("/dashboard")
        self.assertIn("Baseline 91.7%", again.text())   # eleven of twelve: no answer was lost

    def test_05_a_double_click_stores_one_record(self):
        """ST-05 | NFR-05 |
        A double click on a form stores one record, with and without JavaScript"""
        administrator = self.administrator()
        for javascript in (True, False):
            with self.subTest(javascript=javascript):
                before = administrator.counts()["consented"]
                user = self.browser_user(javascript=javascript)
                user.open("/consent")
                user.page.check("input[name=adult]")
                user.page.check("input[name=agree]")
                user.page.dblclick("form.consent-form button[type=submit]")
                user.page.wait_for_url("**/dashboard")
                user.page.wait_for_timeout(400)      # time for a second request to arrive
                self.assertEqual(administrator.counts()["consented"], before + 1)

                user.open("/assessment/pre")
                user.choose()
                user.page.dblclick("form.decision button[type=submit]")
                user.page.wait_for_load_state("load")
                user.page.wait_for_timeout(400)
                user.open("/dashboard")
                self.assertEqual(user.answered(), 1)

    def test_06_one_item_open_in_two_tabs_is_answered_once(self):
        """ST-06 | NFR-05 |
        An item open in two tabs is answered once, and the first answer counts"""
        user = self.browser_user()
        user.consent()
        user.open("/assessment/pre")
        second = user.new_tab()
        user.open("/assessment/pre", second)
        self.assertEqual(user.item()["id"], user.item(second)["id"])
        first_item = user.answer("pre", correct=True)            # tab 1: the right answer
        user.choose(correct=False, page=second)                   # tab 2: the other answer
        user.follow("form.decision button[type=submit]", second)
        self.assertNotEqual(user.item(second)["id"], first_item["id"])   # moved on, no error
        self.assertIn("2 of 12", user.main_text(second))
        second.close()
        user.assessment("pre", count=11)
        user.open("/dashboard")
        self.assertIn("Baseline 100.0%", user.text())            # the first answer was kept
        self.assert_no_problems(user)

    def test_07_back_and_reload_neither_repeat_nor_lose_an_answer(self):
        """ST-07 | NFR-05 | The Back button and a reload neither repeat nor lose an answer"""
        user = self.browser_user()
        user.consent()
        user.open("/assessment/pre")
        first = user.answer("pre")
        second = user.item()
        user.page.go_back(wait_until="load")
        self.assertEqual(user.item()["id"], second["id"])        # not the item already answered
        user.page.reload(wait_until="load")
        self.assertEqual(user.item()["id"], second["id"])
        self.assertNotEqual(first["id"], second["id"])
        user.answer("pre")
        user.page.reload(wait_until="load")                       # a reload right after an answer
        self.assertIn("3 of 12", user.main_text())
        user.open("/dashboard")
        self.assertEqual(user.answered(), 2)
        self.assert_no_problems(user)

    def test_08_steps_cannot_be_taken_out_of_order(self):
        """ST-08 | FR-03 to FR-09 |
        Typing the address of a later step leads back to the open step"""
        participant = self.participant()
        participant.consent()
        client = participant.client

        def lands(path):
            return client.page(path).path

        for path, target in (
                ("/learn", "/assessment/pre"), ("/practice", "/assessment/pre"),
                ("/practice/complete", "/assessment/pre"), ("/assessment/post", "/dashboard"),
                ("/assessment/pre/complete", "/assessment/pre"), ("/results", "/dashboard"),
                ("/survey", "/dashboard"), ("/survey/complete", "/dashboard")):
            with self.subTest(step="after consent", address=path):
                self.assertEqual(lands(path), target)
        self.assertEqual(client.get("/practice/feedback/P01").status, 404)
        participant.assessment("pre")
        for path, target in (("/practice", "/learn"), ("/assessment/post", "/dashboard"),
                             ("/results", "/dashboard")):
            with self.subTest(step="after the pre-assessment", address=path):
                self.assertEqual(lands(path), target)
        participant.lessons()
        participant.practice(count=3)
        self.assertEqual(lands("/assessment/post"), "/dashboard")     # practice is not finished
        self.assertEqual(client.get("/practice/feedback/P06").status, 404)   # not answered yet
        participant.practice(count=3)
        participant.assessment("post", count=11)
        self.assertEqual(lands("/results"), "/dashboard")             # one answer is missing
        reply = client.post("/finish", {"csrf_token": client.token("/withdraw")})
        self.assertEqual(reply.location, "/dashboard")                # Finish needs the survey
        self.assertEqual(lands("/dashboard"), "/dashboard")           # and did not end the session

    def test_09_a_finished_session_cannot_be_reopened_on_a_shared_computer(self):
        """ST-09 | FR-10, NFR-08 | After Finish, a shared computer shows nothing of the session"""
        earlier = self.participant()
        earlier.journey(pre_wrong={0, 1, 2}, post_wrong=set(), ratings=RATINGS, finish=False)
        user = self.browser_user()
        user.adopt(earlier)
        user.open("/results")
        self.assertIn("75.0%", user.main_text())
        earlier_tag = user.page.locator(".session-tag").inner_text()
        copy = user.copy_of_cookies()                 # a copy of the cookie, taken in time
        self.assertEqual(copy.page("/results").path, "/results")
        user.finish()
        self.assertEqual(user.heading(), "You are signed out of this browser")

        user.page.go_back(wait_until="load")          # the next person presses Back
        self.assertTrue(user.path().startswith("/consent"), user.path())
        self.assertNotIn("75.0%", user.text())
        for address in ("/results", "/dashboard", "/survey/complete", "/withdraw"):
            user.open(address)
            self.assertTrue(user.path().startswith("/consent"), address)
            self.assertEqual(copy.page(address).path, "/consent", address)   # the copy is refused
        user.consent()                                # and takes part themselves
        self.assertNotEqual(user.page.locator(".session-tag").inner_text(), earlier_tag)
        self.assertIn("Start", user.main_text())
        self.assertNotIn("Baseline", user.main_text())

    def test_10_the_researcher_signs_in_reads_the_results_and_downloads_the_export(self):
        """ST-10 | FR-11, NFR-10 |
        The researcher signs in, reads the results, and downloads the export"""
        self.completed_cohort(5)
        complete = self.administrator().counts()["complete"]
        config = settings()
        user = self.browser_user()
        user.open("/admin")
        self.assertEqual(user.path(), "/admin/login")
        user.page.fill("input[name=username]", config.admin_user)
        user.page.fill("input[name=password]", "not-the-password")
        user.follow("form button[type=submit]")
        self.assertIn("The username or password is not correct.", user.main_text())
        user.page.fill("input[name=username]", config.admin_user)
        user.page.fill("input[name=password]", config.admin_password)
        user.follow("form button[type=submit]")
        self.assertEqual(user.heading(), "Pilot results")
        self.assertIn(f"Learning outcome (RQ1), n = {complete}", user.main_text())
        self.assertIsNone(UUID.search(user.page.content()))       # no identifier on the page
        self.shot(user, "dashboard")
        with user.page.expect_download() as download:
            user.page.locator("a[href$='/admin/export.csv']").click()
        lines = open(download.value.path(), encoding="utf-8").read().splitlines()
        self.assertEqual(download.value.suggested_filename, "phishaware-export.csv")
        self.assertEqual(len(lines), complete + 1)
        self.assertEqual(len(lines[0].split(",")), 18)
        self.assertIsNone(UUID.search("\n".join(lines)))
        user.follow("form.topbar__form button")                   # sign out
        self.assertEqual(user.path(), "/admin/login")
        user.page.go_back(wait_until="load")                      # Back shows no results
        self.assertEqual(user.path(), "/admin/login")
        self.assertNotIn("Learning outcome", user.text())


class WireTests(SystemCase):
    """What travels between browser and server (security and privacy on the wire)."""

    def crawl(self, client, start, seen, replies):
        """Follow every link, form address, and file reference reachable with GET."""
        queue = [start]
        while queue:
            path = queue.pop()
            if path in seen:
                continue
            seen.add(path)
            reply = client.get(path)
            replies.append(reply)
            if "text/html" not in reply.header("Content-Type"):
                continue
            for target in LINK.findall(reply.text):
                target = target.replace("&amp;", "&")
                if target.startswith("#"):
                    continue                          # a jump inside the same page
                parts = urlsplit(target)
                self.assertFalse(parts.scheme or parts.netloc,
                                 f"{path} refers to another site: {target}")
                queue.append(target)
            if reply.status in REDIRECTS:
                queue.append(reply.headers["Location"])

    def test_11_every_reachable_address_is_served_with_the_security_headers(self):
        """ST-11 | NFR-08, NFR-11 |
        Every reachable address carries the security headers and no identifier"""
        seen, replies = set(), []
        participant = self.participant()
        self.crawl(participant.client, "/", seen, replies)
        participant.consent()
        for stage in (lambda: None, lambda: participant.assessment("pre"), participant.lessons,
                      participant.practice, lambda: participant.assessment("post"),
                      lambda: participant.survey(RATINGS)):
            stage()
            for path in list(seen):               # everything again in the new state
                if not path.startswith("/static/"):
                    seen.discard(path)
            self.crawl(participant.client, "/dashboard", seen, replies)
        self.completed_cohort(5)
        administrator = Administrator()
        administrator.sign_in()
        self.crawl(administrator.client, "/admin", seen, replies)
        administrator.client.close()

        addresses = {reply.path for reply in replies}
        self.assertGreaterEqual(len(addresses), 15)
        https = settings().https
        for reply in replies:
            with self.subTest(address=reply.path, status=reply.status):
                self.assertLess(reply.status, 500)
                self.assertIsNone(UUID.search(reply.path))          # no identifier in an address
                policy = reply.header("Content-Security-Policy")
                for directive in POLICY:
                    self.assertIn(directive, policy)
                self.assertEqual(reply.header("X-Content-Type-Options"), "nosniff")
                self.assertEqual(reply.header("X-Frame-Options"), "DENY")
                self.assertEqual(reply.header("Referrer-Policy"), "no-referrer")
                kind = reply.header("Content-Type")
                if reply.path.startswith("/static/"):
                    self.assertEqual(reply.header("Cache-Control"),
                                     "public, max-age=31536000, immutable")
                elif "text/html" in kind or "text/csv" in kind:
                    self.assertEqual(reply.header("Cache-Control"), "no-store")
                    self.assertIn("charset=utf-8", kind)
                if https:
                    self.assertEqual(reply.header("Strict-Transport-Security"),
                                     "max-age=31536000")
                for cookie in reply.headers.get_all("Set-Cookie") or []:
                    attributes = [part.strip().lower() for part in cookie.split(";")[1:]]
                    self.assertIn("httponly", attributes)
                    self.assertIn("samesite=lax", attributes)
                    self.assertIn("path=/", attributes)
                    self.assertFalse(any(a.startswith("domain=") for a in attributes))
                    if https:
                        self.assertIn("secure", attributes)
                        self.assertTrue(cookie.startswith("__Host-session="), cookie[:20])
        self.note = f"{len(replies)} replies from {len(addresses)} addresses"

    def test_12_hostile_requests_never_cause_a_server_error(self):
        """ST-12 | NFR-09 | Hostile requests are refused without a server error"""
        participant = self.participant()
        participant.consent()
        client = participant.client
        token = client.token("/assessment/pre")
        item = participant.shown(client.page("/assessment/pre").text)[1]["id"]
        valid = {"scenario_id": item, "answer": "phishing", "csrf_token": token}
        sent = []

        def check(label, reply, *allowed):
            sent.append(label)
            with self.subTest(request=label):
                self.assertLess(reply.status, 500, reply.visible[:120])
                if allowed:
                    self.assertIn(reply.status, allowed)

        for field in valid:
            for value in HOSTILE:
                check(f"{field}={value[:12]!r}",
                      client.post("/assessment/pre", dict(valid, **{field: value})), 400, 413)
        for method in ("PUT", "DELETE", "PATCH"):
            check(method, client.send(method, "/assessment/pre", form=valid), 400, 405)
        check("TRACE", client.send("TRACE", "/consent"), 405)
        check("text/plain body", client.send(
            "POST", "/assessment/pre", body=b"scenario_id=A01&answer=phishing",
            headers={"Content-Type": "text/plain"}), 400, 415)
        check("JSON body", client.send(
            "POST", "/assessment/pre", body=json.dumps(valid).encode(),
            headers={"Content-Type": "application/json"}), 400, 415)
        check("bytes that are not text", client.send(
            "POST", "/assessment/pre", body=b"\xff\xfe\xfd=\xff",
            headers={"Content-Type": "application/x-www-form-urlencoded"}), 400)
        check("token in the address only", client.send(
            "POST", "/assessment/pre?csrf_token=" + quote(token),
            form={"scenario_id": item, "answer": "phishing"}), 400)
        # Larger than any form of the application, and refused for its size alone.
        check("form of 200 kB", client.post(
            "/assessment/pre", dict(valid, answer="p" * 200_000)), 413)
        for path in ("/static/../src/app.py", "/static/%2e%2e/src/config.py",
                     "/static/..%2f..%2fwsgi.py", "/practice/feedback/" + quote("' OR 1=1 --"),
                     "/practice/feedback/" + "P" * 3000, "/consent/", "/admin/export.csv/x",
                     "/.env", "/.git/config", "/instance/phishaware.db"):
            check(path[:40], client.get(path), 400, 404)
        for label, cookie in (("garbage", "not-a-cookie"), ("empty", ""),
                              ("unsigned", base64.urlsafe_b64encode(json.dumps(
                                  {"participant_id": str(uuid.uuid4())}).encode()).decode()),
                              ("oversized", "A" * 3000)):
            name = client.session_cookie[0]
            forged = Client(cookies={name: cookie})
            reply = forged.get("/dashboard")
            forged.close()
            check(f"cookie: {label}", reply, 302)
            self.assertEqual(reply.location, "/consent")
        # Nothing hostile was stored, and the participant's own session still works.
        self.assertIn("<strong>1</strong> of 12", client.page("/assessment/pre").text)
        self.note = f"{len(sent)} hostile requests"

    def test_13_roles_cannot_be_crossed_over_the_wire(self):
        """ST-13 | FR-11, NFR-10 |
        A visitor, a participant, and the researcher cannot cross roles"""
        visitor = self.client()
        participant = self.participant()
        participant.consent()
        administrator = self.administrator().client
        for address in ("/dashboard", "/assessment/pre", "/learn", "/practice", "/results",
                        "/survey", "/withdraw", "/assessment/post/complete"):
            with self.subTest(role="visitor", address=address):
                self.assertEqual(visitor.page(address).path, "/consent")
            with self.subTest(role="researcher", address=address):
                self.assertEqual(administrator.page(address).path, "/consent")
        for address in ("/admin", "/admin/export.csv"):
            for role, client in (("visitor", visitor), ("participant", participant.client)):
                with self.subTest(role=role, address=address):
                    reply = client.get(address)
                    self.assertEqual((reply.status, reply.location), (302, "/admin/login"))
        # A withdrawn participant's cookie opens nothing, and neither does a copy of it.
        copy = participant.client.copy()
        self.assertEqual(participant.withdraw().status, 200)
        self.assertEqual(copy.page("/dashboard").path, "/consent")
        copy.close()
        self.assertEqual(self.administrator().dashboard().path, "/admin")   # still signed in

    def test_14_sign_in_is_rate_limited_and_reveals_no_account(self):
        """ST-14 | NFR-10 | Sign-in is rate-limited and does not reveal which accounts exist"""
        config = settings()
        if not config.admin_user:
            self.skipTest("no administrator: set PHISHAWARE_ADMIN_USER")
        client = self.client()
        token = client.token("/admin/login")

        def attempt(username, password="certainly-not-the-password"):
            return client.post("/admin/login", {
                "username": username, "password": password, "csrf_token": token})

        probe = f"probe-{uuid.uuid4().hex[:10]}"
        unknown = [attempt(probe) for _ in range(5)]
        known = [attempt(config.admin_user) for _ in range(3)]
        for reply in unknown + known:
            self.assertEqual(reply.status, 401)
            self.assertIn("The username or password is not correct.", reply.visible)
        # An unknown name costs the server as much work as a wrong password. Without
        # that, an unknown name would be answered about a hundred times sooner. The
        # medians are compared: on a shared machine a single reply can take twice as
        # long as the others, which says nothing about the application.
        typical_known = statistics.median(reply.seconds for reply in known)
        typical_unknown = statistics.median(reply.seconds for reply in unknown[1:])
        self.assertGreater(typical_unknown, 0.4 * typical_known)
        # The sixth attempt on the probed name is refused; other names are not affected.
        blocked = attempt(probe, password=config.admin_password or "x")
        self.assertEqual(blocked.status, 429)
        self.assertIn("Too many failed sign-in attempts", blocked.visible)
        fresh = Administrator()
        self.assertEqual(fresh.sign_in().path, "/admin")      # which also clears its three failures
        fresh.client.close()
        self.note = (f"wrong password {typical_known * 1000:.0f} ms, "
                     f"unknown name {typical_unknown * 1000:.0f} ms (medians)")

    def test_15_the_health_endpoint_reports_the_release_and_nothing_else(self):
        """ST-15 | NFR-12 | The health endpoint reports status and release, and nothing else"""
        client = self.client()
        reply = client.get("/healthz")
        self.assertEqual(reply.status, 200)
        self.assertIn("application/json", reply.header("Content-Type"))
        self.assertEqual(reply.header("Cache-Control"), "no-store")
        self.assertIsNone(reply.headers.get("Set-Cookie"))
        health = json.loads(reply.text)
        self.assertEqual(sorted(health), ["scenarios", "status", "version"])
        self.assertEqual((health["status"], health["scenarios"]), ("ok", 30))
        shown = VERSION.search(client.page("/consent").text)
        self.assertEqual(shown.group(1), health["version"])    # every page names the release
        self.note = f"release {health['version']}"


class LoadAndRecoveryTests(SystemCase):
    """Simultaneous use, a crash, and backups during use (performance and stability)."""

    @staticmethod
    def percentile(values, share):
        ordered = sorted(values)
        return ordered[max(1, round(share * len(ordered) + 0.5)) - 1] * 1000

    def test_16_twenty_five_simultaneous_journeys_all_report_the_right_results(self):
        """ST-16 | NFR-05, NFR-06 |
        Twenty-five simultaneous journeys all report the right results"""
        administrator = self.administrator()
        before = administrator.counts()
        people = [Participant() for _ in range(25)]
        pages, problems = [None] * 25, []
        barrier = threading.Barrier(25)

        def run(index):
            barrier.wait()
            try:
                wrong = {position for position in range(12) if (position + index) % 5 == 0}
                pages[index] = people[index].journey(
                    pre_wrong=wrong | {index % 12}, post_wrong={index % 7}, ratings=RATINGS)
            except Exception as error:
                problems.append(f"journey {index}: {type(error).__name__}: {error}")

        started = time.perf_counter()
        threads = [threading.Thread(target=run, args=(index,)) for index in range(25)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        seconds = time.perf_counter() - started
        for person in people:
            person.client.close()
        self.assertEqual(problems, [])
        for person, page in zip(people, pages):
            scores, bars = results_shown(page.text)
            expected = person.expected
            self.assertEqual(scores,
                             [expected.percent("pre"), expected.percent("post"), expected.gain])
            self.assertEqual(bars, expected_bars(expected))
        after = administrator.counts()
        self.assertEqual(after, {name: count + 25 for name, count in before.items()})
        # Every participant's row is in the export, and no row belongs to two of them.
        _reply, _header, rows = administrator.export()
        exported = [[row[1]] + [float(value) if value else None for value in row[2:6]]
                    + [int(value) for value in row[6:]] for row in rows]
        for person in people:
            self.assertIn(person.expected.export_row, exported)
        self.note = f"25 journeys in {seconds:.1f} s; every score, gain, and export row correct"

    def test_17_a_crash_of_the_web_service_loses_no_answer(self):
        """ST-17 | NFR-05 |
        The web service is killed during a journey, restarts, and has lost nothing"""
        command = settings().crash_command
        if not command:
            self.skipTest("no crash command: set PHISHAWARE_CRASH_COMMAND")
        user = self.browser_user()
        user.consent()
        user.assessment("pre", wrong={1, 2}, count=5)
        outage = time.perf_counter()
        subprocess.run(command, shell=True, check=False, capture_output=True,   # nosec B602
                       timeout=120)
        waited = wait_until_healthy(120)
        outage = time.perf_counter() - outage
        # The restarted container rejoins the host's network, and for a moment a
        # browser on the same host ends its requests with a network error of its
        # own. A person needs longer than that to notice and to press Reload.
        time.sleep(RESTART_SETTLE_SECONDS)
        reloads = user.reload()                       # the same browser, the same cookie
        self.assertEqual(user.path(), "/assessment/pre")
        self.assertIn("6 of 12", user.main_text())
        user.assessment("pre", count=7)
        user.open("/dashboard")
        self.assertIn("Baseline 83.3%", user.text())  # ten of twelve: the five early answers count
        self.note = (f"service answered again {outage:.1f} s after the kill "
                     f"({waited:.1f} s of polling)"
                     + ("" if reloads == 1 else f"; the browser needed {reloads} reloads"))

    def test_18_backups_written_during_use_do_not_disturb_participants(self):
        """ST-18 | NFR-05, NFR-12 | Backups written while participants answer do not disturb them"""
        if not settings().cli:
            self.skipTest("no command line: set PHISHAWARE_CLI")
        people = [Participant() for _ in range(5)]
        pages, problems, written = [None] * 5, [], []

        def run(index):
            try:
                pages[index] = people[index].journey(pre_wrong={index}, ratings=RATINGS)
            except Exception as error:
                problems.append(f"journey {index}: {type(error).__name__}: {error}")

        threads = [threading.Thread(target=run, args=(index,)) for index in range(5)]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads) or len(written) < 2:
            result = self.cli("backup-db")
            written.append((result.returncode, (result.stdout + result.stderr).strip()))
            time.sleep(1.1)                           # backups are named by the second
        for thread in threads:
            thread.join()
        for person in people:
            person.client.close()
        self.assertEqual(problems, [])
        for code, output in written:
            self.assertEqual(code, 0, output)
            self.assertIn("Encrypted backup written", output)
        for person, page in zip(people, pages):
            scores, _bars = results_shown(page.text)
            self.assertEqual(scores[0], person.expected.percent("pre"))
        dry_run = self.cli("purge-expired", "--dry-run")
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertIn("Would delete 0 participant record(s)", dry_run.stdout)
        self.note = f"{len(written)} backups written during five journeys"
