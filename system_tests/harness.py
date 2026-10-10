"""Tools shared by the system and acceptance tests.

Three kinds of test user are modelled, each from the outside only:

* Client        a cookie jar and one HTTPS connection: what any HTTP client can do.
* Participant   a scripted participant over HTTP, with an independent oracle that
                works out what the system must report for the answers it sent.
* BrowserUser   a real browser (Playwright) with its own cookies, for everything
                that depends on rendering, scripts, the Back button, or the screen.

The instance under test is named by environment variables, which run.py sets
from its command line:

    PHISHAWARE_BASE_URL             https://localhost
    PHISHAWARE_CACERT               extra certificate authority to trust (PEM), optional
    PHISHAWARE_IGNORE_HTTPS_ERRORS  "1" lets the browser accept a local authority
    PHISHAWARE_ADMIN_USER, PHISHAWARE_ADMIN_PASSWORD
                                    an administrator, for cases that read the dashboard
    PHISHAWARE_BROWSER              chromium (default), firefox, or webkit
    PHISHAWARE_CLI                  how to run "flask" on the instance, for operator cases,
                                    for example: docker compose exec -T app flask
    PHISHAWARE_CRASH_COMMAND        a command that kills the web service without warning
    PHISHAWARE_ARTIFACTS            folder for screenshots, optional

The module uses the standard library, and Playwright only when a browser is asked for.
"""

import atexit
import csv
import functools
import http.client
import io
import json
import os
import re
import shlex
import ssl
import subprocess  # nosec B404 - runs only the operator commands named in the settings
import time
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
SCENARIO = re.compile(r'name="scenario_id" value="([ABP][0-9]{2})"')
STAT = re.compile(r'<p class="stat__value[^"]*">([+\-]?[0-9.]+)')
CUE_BARS = re.compile(
    r'aria-label="([^":]+): ([0-9]+)% correct before training, ([0-9]+)% after training"')
HEADING = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
TAGS = re.compile(r"<[^>]+>")
REDIRECTS = (301, 302, 303, 307, 308)
CUE_LABELS = {
    "sender_spoofing": "Sender spoofing",
    "deceptive_links": "Deceptive links",
    "urgency_threats": "Urgency or threats",
    "unexpected_attachments": "Unexpected attachments",
    "credential_requests": "Credential requests",
    "lookalike_websites": "Look-alike websites",
}
CUES = tuple(CUE_LABELS)
DASHBOARD_COUNTS = ("consented", "pre_done", "lessons_opened", "complete", "survey_done")
NEUTRAL = (3,) * 10          # ten ratings that score 50.0
DESKTOP = (1280, 800)
PHONE = (360, 740)           # the narrowest supported window (NFR-04)
# How WebKit's console begins its report of a refused style element (see SystemCase.shot).
STYLE_REFUSED = "Refused to apply a stylesheet"
# A browser's own report that a request got no answer (Chromium, Firefox); see BrowserUser.reload.
NETWORK_ERROR = re.compile(r"net::ERR_|NS_ERROR_(NET|CONNECTION|OFFLINE)")


class Settings:
    """Where the instance under test is and how to reach it."""

    def __init__(self, env):
        self.base_url = (env.get("PHISHAWARE_BASE_URL") or "").rstrip("/")
        self.cacert = env.get("PHISHAWARE_CACERT") or None
        self.ignore_https_errors = env.get("PHISHAWARE_IGNORE_HTTPS_ERRORS") == "1"
        self.admin_user = env.get("PHISHAWARE_ADMIN_USER") or None
        self.admin_password = env.get("PHISHAWARE_ADMIN_PASSWORD") or None
        self.engine = env.get("PHISHAWARE_BROWSER") or "chromium"
        self.cli = shlex.split(env.get("PHISHAWARE_CLI") or "")
        self.crash_command = env.get("PHISHAWARE_CRASH_COMMAND") or None
        artifacts = env.get("PHISHAWARE_ARTIFACTS")
        self.artifacts = Path(artifacts) if artifacts else None
        self.https = self.base_url.startswith("https://")


@functools.lru_cache(maxsize=1)
def settings():
    return Settings(os.environ)


@functools.lru_cache(maxsize=1)
def bank():
    """The scenario bank: the answer key that lets a script answer rightly or wrongly."""
    with open(ROOT / "data" / "scenarios.json", encoding="utf-8") as handle:
        return {item["id"]: item for item in json.load(handle)["scenarios"]}


def opposite(label):
    return "legitimate" if label == "phishing" else "phishing"


def text_of(html):
    """The visible text of a page, with tags removed and white space collapsed."""
    import html as html_module
    return " ".join(html_module.unescape(TAGS.sub(" ", html)).split())


# HTTP --------------------------------------------------------------------------------
class Reply:
    """One HTTP response."""

    def __init__(self, status, headers, body, seconds, address):
        parts = urllib.parse.urlsplit(address)
        self.status = status
        self.headers = headers
        self.body = body
        self.seconds = seconds
        self.path = parts.path    # the path that produced this reply (after redirects)
        self.query = parts.query
        self.hops = []            # the replies that redirected here, oldest first

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    @property
    def location(self):
        """The path a redirect points to, without its query string."""
        value = self.headers.get("Location")
        return urllib.parse.urlsplit(value).path if value else None

    @property
    def target(self):
        """Where a redirect points to, as a browser would request it."""
        parts = urllib.parse.urlsplit(self.headers.get("Location") or "")
        return parts.path + (f"?{parts.query}" if parts.query else "")

    def header(self, name):
        return self.headers.get(name) or ""

    @property
    def heading(self):
        match = HEADING.search(self.text)
        return text_of(match.group(1)) if match else ""

    @property
    def visible(self):
        return text_of(self.text)

    def __repr__(self):
        return f"<Reply {self.status} {self.path} {self.heading!r}>"


class Client:
    """A small HTTP client with a cookie jar: one person's browser, without the rendering."""

    def __init__(self, cookies=None, timeout=30):
        config = settings()
        parts = urllib.parse.urlsplit(config.base_url)
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port or (443 if self._https else 80)
        # The extra authority is added to the default ones; verification stays on.
        self._context = ssl.create_default_context(cafile=config.cacert) if self._https else None
        self._timeout = timeout
        self._connection = None
        self.cookies = dict(cookies or {})

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _connect(self):
        if self._https:
            return http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout, context=self._context)
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    def send(self, method, path, form=None, body=None, headers=None):
        """One request, exactly as given. Redirects are not followed."""
        if form is not None:
            body = urllib.parse.urlencode(form).encode()
        send_headers = {"Accept": "text/html,*/*", "User-Agent": "phishaware-system-test/1.0"}
        if form is not None:
            send_headers["Content-Type"] = "application/x-www-form-urlencoded"
        if self.cookies:
            send_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        send_headers.update(headers or {})
        started = time.perf_counter()
        for attempt in (1, 2):   # one more try if the server had closed an idle connection
            try:
                if self._connection is None:
                    self._connection = self._connect()
                self._connection.request(method, path, body=body, headers=send_headers)
                response = self._connection.getresponse()
                payload = response.read()
                break
            except (http.client.HTTPException, OSError):
                self.close()
                if attempt == 2:
                    raise
        seconds = time.perf_counter() - started
        for value in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = value.partition("=")
            content, _, attributes = rest.partition(";")
            expired = "max-age=0" in attributes.lower() or "01 jan 1970" in attributes.lower()
            if expired or not content:
                self.cookies.pop(name.strip(), None)
            else:
                self.cookies[name.strip()] = content
        if (response.headers.get("Connection") or "").lower() == "close":
            self.close()
        return Reply(response.status, response.headers, payload, seconds, path)

    def get(self, path, **kwargs):
        return self.send("GET", path, **kwargs)

    def post(self, path, form, **kwargs):
        return self.send("POST", path, form=form, **kwargs)

    def page(self, path):
        """GET a screen and follow redirects, as a browser does."""
        hops = []
        for _ in range(8):
            reply = self.get(path)
            if reply.status not in REDIRECTS:
                reply.hops = hops
                return reply
            hops.append(reply)
            path = reply.target
        raise AssertionError(f"too many redirects from {hops[0].path}")

    def submit(self, path, form):
        """POST a form and load the screen it redirects to."""
        reply = self.post(path, form)
        if reply.status not in REDIRECTS:
            return reply
        landed = self.page(reply.target)
        landed.hops.insert(0, reply)
        return landed

    def token(self, path="/consent"):
        """The anti-forgery token of this session, read from a page that has a form."""
        match = CSRF.search(self.page(path).text)
        if match is None:
            raise AssertionError(f"{path} shows no form with an anti-forgery token")
        return match.group(1)

    def copy(self):
        """A second client that holds a copy of this one's cookies."""
        return Client(cookies=self.cookies)

    def forget(self):
        """Drop every cookie, as a browser does when a session cookie expires."""
        self.cookies.clear()

    @property
    def session_cookie(self):
        for name, value in self.cookies.items():
            if name.endswith("session"):
                return name, value
        return None, None


def wait_until_healthy(seconds=90):
    """Poll the health endpoint until the instance answers; returns the time it took."""
    started = time.perf_counter()
    last = None
    while time.perf_counter() - started < seconds:
        client = Client(timeout=3)
        try:
            reply = client.get("/healthz")
            if reply.status == 200 and b'"status":"ok"' in reply.body.replace(b" ", b""):
                return time.perf_counter() - started
            last = reply.status
        except (http.client.HTTPException, OSError) as error:
            last = type(error).__name__
        finally:
            client.close()
        time.sleep(0.25)
    raise AssertionError(f"the instance was not healthy after {seconds} s (last: {last})")


# The oracle ---------------------------------------------------------------------------
class Expected:
    """What the system must report for one scripted participant.

    The arithmetic is written here independently of the application: the score is
    the share of correct answers, the gain is the difference, and the usability
    score follows the published rule (Brooke, 1996).
    """

    def __init__(self):
        self.correct = {"pre": dict.fromkeys(CUES, 0), "post": dict.fromkeys(CUES, 0)}
        self.answered = {"pre": [], "practice": [], "post": []}
        self.forms = {}
        self.ratings = None

    def record(self, phase, item, correct):
        self.answered[phase].append(item["id"])
        if phase in self.correct:
            self.forms[phase] = item["pool"]
            if correct:
                self.correct[phase][item["cue"]] += 1

    def percent(self, phase):
        return round(sum(self.correct[phase].values()) / 12 * 100, 1)

    @property
    def gain(self):
        return round(self.percent("post") - self.percent("pre"), 1)

    def cue_percent(self, phase, cue):
        return round(self.correct[phase][cue] / 2 * 100)

    @property
    def sus(self):
        if self.ratings is None:
            return None
        odd = sum(self.ratings[i] - 1 for i in range(0, 10, 2))
        even = sum(5 - self.ratings[i] for i in range(1, 10, 2))
        return (odd + even) * 2.5

    @property
    def export_row(self):
        """This participant's row of the de-identified export, without its number."""
        return ([self.forms["pre"] + self.forms["post"], self.percent("pre"),
                 self.percent("post"), self.gain, self.sus]
                + [self.correct["pre"][cue] for cue in CUES]
                + [self.correct["post"][cue] for cue in CUES])


def results_shown(html):
    """The figures on a results page: (pre, post, gain) and {cue label: (before, after)}."""
    scores = [float(value) for value in STAT.findall(html)[:3]]
    bars = {name: (int(before), int(after)) for name, before, after in CUE_BARS.findall(html)}
    return scores, bars


# A scripted participant over HTTP -------------------------------------------------------
class Participant:
    """One participant's journey over plain HTTP: fast, and exact about every request."""

    def __init__(self, client=None):
        self.client = client or Client()
        self.expected = Expected()

    def consent(self):
        return self.client.submit("/consent", {
            "adult": "yes", "agree": "yes", "csrf_token": self.client.token("/consent")})

    def shown(self, html):
        """(token, scenario) of the item on a page, or (None, None)."""
        token, scenario = CSRF.search(html), SCENARIO.search(html)
        if token is None or scenario is None:
            return None, None
        return token.group(1), bank()[scenario.group(1)]

    def answer(self, url, phase, correct=True, html=None):
        """Answer the item now shown at url; returns the reply to the POST (not followed)."""
        token, item = self.shown(html if html is not None else self.client.page(url).text)
        if item is None:
            raise AssertionError(f"{url} shows no item to answer")
        reply = self.client.post(url, {
            "scenario_id": item["id"], "csrf_token": token,
            "answer": item["label"] if correct else opposite(item["label"])})
        self.expected.record(phase, item, correct)
        return reply

    def assessment(self, phase, wrong=(), count=12):
        """Answer `count` items of an assessment; positions in `wrong` are answered wrongly."""
        url = f"/assessment/{phase}"
        start = len(self.expected.answered[phase])
        for position in range(start, start + count):
            reply = self.answer(url, phase, correct=position not in wrong)
            if reply.status not in REDIRECTS:
                raise AssertionError(f"{url}: answer {position + 1} was answered with {reply}")

    def lessons(self):
        return self.client.page("/learn")

    def practice(self, wrong=(), count=6):
        start = len(self.expected.answered["practice"])
        for position in range(start, start + count):
            reply = self.answer("/practice", "practice", correct=position not in wrong)
            if reply.status not in REDIRECTS:
                raise AssertionError(f"/practice: answer {position + 1} was answered with {reply}")

    def survey(self, ratings=NEUTRAL):
        form = {f"q{number}": str(rating) for number, rating in enumerate(ratings, start=1)}
        form["csrf_token"] = self.client.token("/survey")
        self.expected.ratings = list(ratings)
        return self.client.submit("/survey", form)

    def results(self):
        return self.client.page("/results")

    def finish(self):
        return self.client.post("/finish", {"csrf_token": self.client.token("/dashboard")})

    def withdraw(self):
        return self.client.post("/withdraw", {"csrf_token": self.client.token("/withdraw")})

    def journey(self, pre_wrong=(), post_wrong=(), ratings=NEUTRAL, finish=True):
        """The whole journey; returns the results page as the participant saw it."""
        self.consent()
        self.assessment("pre", pre_wrong)
        self.lessons()
        self.practice()
        self.assessment("post", post_wrong)
        results = self.results()
        if ratings is not None:
            self.survey(ratings)
            if finish:
                self.finish()
        return results


class Administrator:
    """The researcher's view over HTTP: sign-in, the dashboard's counts, and the export."""

    def __init__(self):
        self.client = Client()

    def sign_in(self, username=None, password=None):
        config = settings()
        return self.client.submit("/admin/login", {
            "username": username or config.admin_user,
            "password": password or config.admin_password,
            "csrf_token": self.client.token("/admin/login")})

    def dashboard(self):
        reply = self.client.page("/admin")
        if reply.path != "/admin":       # the 15-minute idle limit has passed: sign in again
            reply = self.sign_in()
        if reply.path != "/admin" or reply.status != 200:
            raise AssertionError(f"the administrator could not open the dashboard: {reply}")
        return reply

    def counts(self):
        """The participation counts, which the dashboard shows at any cohort size."""
        values = [int(float(value)) for value in STAT.findall(self.dashboard().text)]
        if len(values) < len(DASHBOARD_COUNTS):
            raise AssertionError("the dashboard does not show its five participation counts")
        return dict(zip(DASHBOARD_COUNTS, values))

    def export(self):
        """(reply, header, rows) of the de-identified export."""
        reply = self.client.get("/admin/export.csv")
        rows = list(csv.reader(io.StringIO(reply.text))) if reply.status == 200 else [[]]
        return reply, rows[0], rows[1:]

    def sign_out(self):
        return self.client.post("/admin/logout", {"csrf_token": self.client.token("/admin")})


# A real browser ------------------------------------------------------------------------
_playwright = None
_browser = None


def browser():
    """The browser engine named in the settings, started on first use."""
    global _playwright, _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = getattr(_playwright, settings().engine).launch()
        atexit.register(close_browser)
    return _browser


def close_browser():
    global _playwright, _browser
    if _browser is not None:
        _browser.close()
        _playwright.stop()
        _playwright = _browser = None


def browser_version():
    names = {"chromium": "Chromium", "firefox": "Firefox", "webkit": "WebKit"}
    return f"{names.get(settings().engine, settings().engine)} {browser().version}"


class BrowserUser:
    """One person at a real browser: a context with its own cookies.

    Everything that goes wrong on the way is collected in `problems`: errors on
    the browser's console (which include violations of the content security
    policy), uncaught script errors, a missing style sheet or script, and any
    reply with a server error.
    """

    def __init__(self, size=DESKTOP, javascript=True, touch=False, storage=None):
        config = settings()
        self.base_url = config.base_url
        self.context = browser().new_context(
            viewport={"width": size[0], "height": size[1]},
            ignore_https_errors=config.ignore_https_errors,
            java_script_enabled=javascript, has_touch=touch, storage_state=storage)
        self.problems = []
        self.checks = []          # called with the page after every navigation
        self.screens = 0
        self.expected = Expected()
        self.page = self.new_tab()

    def new_tab(self):
        page = self.context.new_page()
        page.on("console", self._on_console)
        page.on("pageerror", lambda error: self.problems.append(f"script error: {error}"))
        page.on("response", self._on_response)
        return page

    def _on_console(self, message):
        # A reply with an error status is reported by _on_response; the console's
        # one-line echo of it would count the same event twice.
        if message.type == "error" and "Failed to load resource" not in message.text:
            self.problems.append(f"console: {message.text}")

    def _on_response(self, response):
        kind = response.request.resource_type
        if response.status >= 500:
            self.problems.append(f"{response.status} from {response.url}")
        elif response.status >= 400 and kind in ("stylesheet", "script", "image", "font"):
            self.problems.append(f"{response.status} for the {kind} {response.url}")

    def close(self):
        self.context.close()

    # Moving a session between the browser and plain HTTP
    def adopt(self, participant):
        """Continue in this browser a journey that a Participant began over HTTP."""
        self.context.add_cookies([
            {"name": name, "value": value, "url": self.base_url, "httpOnly": True,
             "secure": settings().https, "sameSite": "Lax"}
            for name, value in participant.client.cookies.items()])
        self.expected = participant.expected

    def copy_of_cookies(self):
        """An HTTP client that holds a copy of this browser's cookies."""
        return Client(cookies={cookie["name"]: cookie["value"]
                               for cookie in self.context.cookies()})

    # Navigation
    def _arrived(self, page):
        self.screens += 1
        for check in self.checks:
            check(page)

    def open(self, path, page=None):
        page = page or self.page
        response = page.goto(self.base_url + path, wait_until="load")
        self._arrived(page)
        return response

    def reload(self, page=None, attempts=5):
        """Press Reload; press it again if the browser itself reports a network error.

        After the container of the web service has been restarted, a browser on
        the same host can end its next request with an error of its own
        (Chromium: net::ERR_NETWORK_CHANGED, because the container rejoined the
        host's network). The application has given no answer in that case. A
        person would press Reload again, and so does this method. Every answer
        of the application, and every other failure, is passed on unchanged.
        Returns the number of attempts that were needed.
        """
        page = page or self.page
        attempt = 1
        while True:
            try:
                page.reload(wait_until="load")
                return attempt
            except Exception as error:   # Playwright is imported only when a browser starts
                if attempt == attempts or not NETWORK_ERROR.search(str(error)):
                    raise
            attempt += 1
            time.sleep(1)

    def follow(self, selector, page=None):
        """Click something that loads a new screen, and wait for that screen."""
        page = page or self.page
        with page.expect_navigation(wait_until="load"):
            page.locator(selector).first.click()
        self._arrived(page)

    def path(self, page=None):
        parts = urllib.parse.urlsplit((page or self.page).url)
        return parts.path + (f"?{parts.query}" if parts.query else "")

    def heading(self, page=None):
        return " ".join((page or self.page).locator("h1").first.inner_text().split())

    def text(self, page=None):
        return " ".join((page or self.page).locator("body").inner_text().split())

    def main_text(self, page=None):
        return " ".join((page or self.page).locator("main").inner_text().split())

    # The participant's actions
    def consent(self):
        self.open("/consent")
        self.page.check("input[name=adult]")
        self.page.check("input[name=agree]")
        self.follow("form.consent-form button[type=submit]")

    def item(self, page=None):
        page = page or self.page
        field = page.locator("input[name=scenario_id]")
        return bank()[field.get_attribute("value")] if field.count() else None

    def choose(self, correct=True, page=None):
        """Select an answer without sending it; returns the scenario."""
        page = page or self.page
        item = self.item(page)
        value = item["label"] if correct else opposite(item["label"])
        page.check(f"input[type=radio][name=answer][value={value}]")
        return item

    def answer(self, phase, correct=True, page=None):
        item = self.choose(correct, page)
        self.follow("form.decision button[type=submit]", page)
        self.expected.record(phase, item, correct)
        return item

    def assessment(self, phase, wrong=(), count=12):
        if self.path() != f"/assessment/{phase}":
            self.open(f"/assessment/{phase}")
        start = len(self.expected.answered[phase])
        for position in range(start, start + count):
            self.answer(phase, correct=position not in wrong)

    def practice(self, wrong=()):
        if self.path() != "/practice":
            self.open("/practice")
        for position in range(6):
            self.answer("practice", correct=position not in wrong)   # lands on the feedback
            self.follow(".panel a.btn--primary")                      # next scenario or summary

    def survey(self, ratings=NEUTRAL):
        if self.path() != "/survey":
            self.open("/survey")
        for number, rating in enumerate(ratings, start=1):
            self.page.check(f"input[name=q{number}][value='{rating}']")
        self.follow("form.sus button[type=submit]")
        self.expected.ratings = list(ratings)

    def finish(self):
        self.open("/dashboard")
        self.follow("form.finish button[type=submit]")

    def journey(self, pre_wrong=(), post_wrong=(), ratings=NEUTRAL, finish=True):
        """The whole journey through the links a participant would use."""
        self.consent()
        self.follow("a[href$='/assessment/pre']")
        self.assessment("pre", pre_wrong)
        self.follow("a[href$='/learn']")
        self.follow("a[href$='/practice']")
        self.practice()
        self.follow("a[href$='/assessment/post']")
        self.assessment("post", post_wrong)
        self.follow("a[href$='/results']")
        if ratings is not None:
            self.follow("a[href$='/survey']")
            self.survey(ratings)
            if finish:
                self.finish()

    def results_shown(self):
        return results_shown(self.page.content())

    def answered(self, page=None):
        """How many items the progress bar on the screen reports as answered, or None.

        The bar's wording ("5 of 12 answered") is its fallback text and is not
        drawn, so the number is read from the bar itself.
        """
        bar = (page or self.page).locator("main progress")
        return int(bar.first.get_attribute("value")) if bar.count() else None

    def scrolls_sideways(self, page=None):
        return (page or self.page).evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")


# The base class of every case ------------------------------------------------------------
class SystemCase(unittest.TestCase):
    """A case that needs a running instance; without one it is reported as not run."""

    maxDiff = None
    _administrator = None

    @classmethod
    def setUpClass(cls):
        if not settings().base_url:
            raise unittest.SkipTest("no instance to test: set PHISHAWARE_BASE_URL")

    def setUp(self):
        self._users = []

    def tearDown(self):
        for user in self._users:
            user.close()

    # Evidence: a screenshot of every open tab when a case fails.
    def _callTestMethod(self, method):
        try:
            super()._callTestMethod(method)
        except unittest.SkipTest:
            raise
        except Exception:
            for number, user in enumerate(self._users, start=1):
                if isinstance(user, BrowserUser):
                    self.shot(user, f"failed-{number}")
            raise

    @property
    def case_id(self):
        return (self._testMethodDoc or self._testMethodName).split("|")[0].strip()

    def shot(self, user, name, full_page=True):
        """Save a screenshot as evidence when an artifacts folder is configured."""
        folder = settings().artifacts
        if folder is None:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.case_id}-{settings().engine}-{name}.png"
        known = len(user.problems)
        try:
            user.page.screenshot(path=str(path), full_page=full_page)
            user.page.evaluate("0")   # one round trip, so that the console has been read
        except Exception:   # evidence is best effort and must not hide the verdict
            path = None
        # In WebKit, Playwright adds an empty style element to the page before every
        # screenshot and removes it again, to bring animations into step. The
        # application's content security policy refuses the element, as it should,
        # and WebKit reports the refusal in the console. That message belongs to the
        # screenshot and not to the page, so it is not kept as a problem of the page.
        user.problems[known:] = [problem for problem in user.problems[known:]
                                 if STYLE_REFUSED not in problem]
        return path

    # The three kinds of test user
    def client(self):
        client = Client()
        self._users.append(client)
        return client

    def participant(self):
        participant = Participant(self.client())
        return participant

    def browser_user(self, **options):
        user = BrowserUser(**options)
        self._users.append(user)
        return user

    def administrator(self):
        """The signed-in administrator shared by the cases of a run."""
        config = settings()
        if not (config.admin_user and config.admin_password):
            self.skipTest("no administrator: set PHISHAWARE_ADMIN_USER and "
                          "PHISHAWARE_ADMIN_PASSWORD")
        if SystemCase._administrator is None:
            administrator = Administrator()
            reply = administrator.sign_in()
            if reply.path != "/admin":
                self.fail(f"the administrator could not sign in: {reply}")
            SystemCase._administrator = administrator
        return SystemCase._administrator

    def completed_cohort(self, size=5):
        """Make sure at least `size` participants have finished both assessments."""
        missing = size - self.administrator().counts()["complete"]
        for number in range(max(0, missing)):
            self.participant().journey(pre_wrong=range(number + 2), post_wrong=range(number))

    # The operator's command line
    def cli(self, *arguments, text=None):
        """Run "flask <arguments>" on the instance; returns the completed process."""
        command = settings().cli
        if not command:
            self.skipTest("no command line: set PHISHAWARE_CLI")
        return subprocess.run(   # nosec B603 - the command comes from the tester's settings
            [*command, *arguments], input=text, capture_output=True, text=True, timeout=180)

    # Assertions used by many cases
    def assert_no_problems(self, user):
        self.assertEqual(user.problems, [], "the browser reported problems")

    def assert_status(self, reply, *allowed):
        self.assertIn(reply.status, allowed, f"{reply!r} {reply.visible[:200]}")
