"""Browser audit of a running PhishAware instance: accessibility, layout, and page load.

    pip install -r evaluation/requirements.txt && python -m playwright install chromium
    python evaluation/browser_audit.py --base-url http://127.0.0.1:5000 \\
        --admin-user evaluator --admin-password "$PASSWORD"

A real browser (Chromium, driven by Playwright) walks through every screen of the
participant journey and the administrator area four times:

1. Inspection. On each screen it runs axe-core (when --axe names the script), the
   checks in page_checks.js at five window widths, and a Tab-key walk that looks
   for a visible focus indicator on every control.
2. Keyboard. It withdraws once and then completes the whole journey with key
   presses only (WCAG 2.1.1 Keyboard and 2.1.2 No Keyboard Trap).
3. Page load without throttling. It reads the browser's own Navigation Timing
   record for every screen.
4. Page load on an emulated slow mobile connection, with the network and
   processor limits that Lighthouse uses for its "slow 4G" profile.

Each journey first consents and withdraws, then consents again, completes every
step, and finishes. A finished session can no longer be withdrawn, so every
journey leaves one scripted record: run the audit against a test instance and
never against the database of a live study. Run it after the load test, because
the administrator dashboard shows its statistics only when five participants
have finished both assessments.
"""

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PAGE_CHECKS = (HERE / "page_checks.js").read_text(encoding="utf-8")
WIDTHS = (320, 360, 768, 1280, 1920)    # 320 px is the WCAG reflow width; NFR-04 covers 360-1920
DESKTOP = {"width": 1280, "height": 800}
TARGET_WIDTH = 360                      # touch-target sizes are checked at phone width
MAX_TABS = 120
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]
# Lighthouse's "slow 4G" throttling as applied through the debugging protocol:
# 562.5 ms added to every request, about 1.47 Mbit/s down and 0.68 Mbit/s up,
# and a processor slowed four times.
SLOW_4G = {
    "offline": False,
    "latency": 562.5,
    "downloadThroughput": 1.6 * 1024 * 1024 / 8 * 0.9,
    "uploadThroughput": 750 * 1024 / 8 * 0.9,
}
CPU_SLOWDOWN = 4
CHECK_LABELS = (
    ("text-contrast", "1.4.3", "Text contrast at least 4.5:1 (3:1 for large text)"),
    ("field-boundary", "1.4.11", "Text-field boundary contrast at least 3:1"),
    ("focus-visible", "2.4.7", "Visible focus indicator on every tab stop"),
    ("focus-contrast", "1.4.11", "Focus indicator contrast at least 3:1"),
    ("keyboard-reach", "2.1.1", "Every control reachable with the Tab key"),
    ("accessible-name", "4.1.2", "Accessible name on every control"),
    ("text-alternative", "1.1.1", "Images and graphics named or hidden"),
    ("group-label", "1.3.1", "Radio buttons grouped under a legend"),
    ("structure", "several", "Language, title, headings, landmarks, skip link"),
    ("reflow", "1.4.10", "No horizontal scrolling at 320 to 1920 px"),
    ("text-spacing", "1.4.12", "No clipped text with wider text spacing"),
    ("target-size", "2.5.8", "Touch targets 24 by 24 px or spaced apart (WCAG 2.2)"),
)
AXE_RUN = """async (tags) => {
  const result = await axe.run(document, {runOnly: {type: "tag", values: tags}});
  const brief = (items) => items.map((item) => ({
    id: item.id, impact: item.impact, help: item.help,
    wcag: item.tags.some((tag) => tag.startsWith("wcag")),
    nodes: item.nodes.map((node) => node.target.join(" ")).slice(0, 5),
  }));
  return {version: axe.version, violations: brief(result.violations),
          incomplete: brief(result.incomplete), passed: result.passes.length};
}"""
TIMING = """() => {
  const nav = performance.getEntriesByType("navigation")[0];
  const resources = performance.getEntriesByType("resource");
  const paint = performance.getEntriesByName("first-contentful-paint")[0];
  return {
    load: nav.loadEventEnd, interactive: nav.domContentLoadedEventEnd,
    paint: paint ? paint.startTime : null, redirects: nav.redirectCount,
    requests: 1 + nav.redirectCount + resources.filter((r) => r.transferSize > 0).length,
    cached: resources.filter((r) => r.transferSize === 0).length,
    bytes: nav.transferSize + resources.reduce((sum, r) => sum + r.transferSize, 0),
  };
}"""


def opposite(label):
    return "legitimate" if label == "phishing" else "phishing"


class Journey:
    """Walks through the application once and reports every screen it reaches.

    `on_screen(name, first, after)` is called when a screen has loaded. `first` is
    True the first time a kind of screen appears, and `after` says how it was
    reached: "open" (a link or an address) or "submit" (a form was sent).
    """

    def __init__(self, page, base_url, bank, on_screen, admin=None):
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.bank = bank
        self.on_screen = on_screen
        self.admin = admin
        self._seen = set()

    def screen(self, name, after="open"):
        self.page.wait_for_function(
            "performance.getEntriesByType('navigation')[0].loadEventEnd > 0")
        self.on_screen(name, name not in self._seen, after)
        self._seen.add(name)

    def open(self, path):
        self.page.goto(self.base_url + path, wait_until="load")

    def follow(self, selector):
        with self.page.expect_navigation(wait_until="load"):
            self.page.locator(selector).first.click()

    def item(self):
        scenario_id = self.page.locator("input[name=scenario_id]").get_attribute("value")
        return self.bank[scenario_id]

    def answer(self, correct):
        """Classify the scenario on the screen (both answer designs are supported)."""
        label = self.item()["label"]
        value = label if correct else opposite(label)
        radio = self.page.locator(f"input[type=radio][name=answer][value={value}]")
        if radio.count():
            radio.check()
            self.follow("form.decision button[type=submit]")
        else:
            self.follow(f"button[name=answer][value={value}]")

    def assessment(self):
        for position in range(12):
            channel = "email" if self.item()["channel"] == "email" else "web page"
            self.screen(f"Assessment item ({channel})", "open" if position == 0 else "submit")
            self.answer(correct=position % 4 != 3)
        self.screen("Assessment complete", "submit")

    def consent(self):
        self.open("/consent")
        self.screen("Consent")
        self.page.check("input[name=adult]")
        self.page.check("input[name=agree]")
        self.follow("form.consent-form button[type=submit]")
        self.screen("Dashboard (start)", "submit")

    def run(self):
        page = self.page
        self.open("/consent/declined")
        self.screen("Consent declined")
        # A participant who changes their mind: consent, then withdraw at once.
        self.consent()
        self.follow("a[href$='/withdraw']")
        self.screen("Withdraw confirmation")
        self.follow("form button.btn--danger")
        self.screen("Withdrawn", "submit")
        # A participant who completes every step and finishes.
        self.consent()
        self.follow("a[href$='/assessment/pre']")
        self.assessment()
        self.follow("a[href$='/learn']")
        self.screen("Lessons")
        self.follow("a[href$='/practice']")
        for position in range(6):
            self.screen("Practice scenario")
            self.answer(correct=position != 0)   # the first answer is wrong on purpose
            self.screen("Feedback (incorrect)" if position == 0 else "Feedback (correct)",
                        "submit")
            self.follow(".panel a.btn--primary")
        self.screen("Practice summary")
        self.follow("a[href$='/assessment/post']")
        self.assessment()
        self.follow("a[href$='/results']")
        self.screen("Results")
        self.follow("a[href$='/survey']")
        self.screen("Usability survey")
        for number in range(1, 11):
            page.check(f"input[name=q{number}][value='4']")
        self.follow("form.sus button[type=submit]")
        self.screen("Survey complete", "submit")
        self.open("/dashboard")
        self.screen("Dashboard (complete)")
        self.open("/no-such-page")
        self.screen("Error page")
        self.open("/dashboard")
        self.follow("form.finish button[type=submit]")
        self.screen("Finished", "submit")
        if self.admin:
            self.open("/admin/login")
            self.screen("Administrator sign-in")
            page.fill("input[name=username]", self.admin[0])
            page.fill("input[name=password]", self.admin[1])
            self.follow("form button[type=submit]")
            self.screen("Administrator dashboard", "submit")
            self.follow("form.topbar__form button")


class Inspector:
    """Journey 1: accessibility and layout checks on the first view of every screen."""

    def __init__(self, page, shots=None, axe=False):
        self.page = page
        self.shots = shots
        self.axe = axe
        self.screens = []
        self.checked = {}
        self.failures = []
        self.advisories = []
        self.axe_result = {"version": None, "violations": {}, "incomplete": {}, "passed": 0}

    def _merge(self, name, result, width=None):
        for check, count in result["checked"].items():
            self.checked[check] = self.checked.get(check, 0) + count
        where = name if width is None else f"{name} at {width} px"
        for kind, target in (("failures", self.failures), ("advisories", self.advisories)):
            for entry in result[kind]:
                target.append(dict(entry, screen=where))

    def _fail(self, check, criterion, name, element, detail):
        self.failures.append({"check": check, "criterion": criterion, "screen": name,
                              "element": element, "detail": detail})

    def __call__(self, name, first, after):
        if not first:
            return
        page = self.page
        self.screens.append(name)
        if self.axe:
            self._run_axe(name)
        self._merge(name, page.evaluate(PAGE_CHECKS, {"spacing": True}))
        self._walk_tab_stops(name)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": DESKTOP["height"]})
            options = {"reflow": True, "targets": width == TARGET_WIDTH}
            result = page.evaluate(PAGE_CHECKS, options)
            # Only the width-dependent checks are counted on these passes.
            result["checked"] = {key: value for key, value in result["checked"].items()
                                 if key in ("reflow", "target-size")}
            for kind in ("failures", "advisories"):
                result[kind] = [entry for entry in result[kind]
                                if entry["check"] in ("reflow", "target-size")]
            self._merge(name, result, width)
            if self.shots and width in (TARGET_WIDTH, DESKTOP["width"]):
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                page.screenshot(path=str(self.shots / f"{slug}-{width}.png"), full_page=True)
        page.set_viewport_size(DESKTOP)

    def _run_axe(self, name):
        result = self.page.evaluate(AXE_RUN, AXE_TAGS)
        self.axe_result["version"] = result["version"]
        self.axe_result["passed"] += result["passed"]
        for kind in ("violations", "incomplete"):
            for item in result[kind]:
                entry = self.axe_result[kind].setdefault(item["id"], dict(item, screens=[]))
                entry["screens"].append(name)

    def _walk_tab_stops(self, name):
        """Press Tab through the page; every control must be reached and show its focus."""
        page = self.page
        expected = page.evaluate(PAGE_CHECKS, {"mode": "expected-stops"})
        page.evaluate("document.activeElement && document.activeElement.blur()")
        reached = {}
        for _ in range(MAX_TABS):
            page.keyboard.press("Tab")
            stop = page.evaluate(PAGE_CHECKS, {"mode": "focus"})
            if stop is None or stop["key"] in reached:
                break
            reached[stop["key"]] = stop
        for stop in reached.values():
            for check in ("focus-visible", "focus-contrast"):
                self.checked[check] = self.checked.get(check, 0) + 1
            if not stop["visible"]:
                self._fail("focus-visible", "2.4.7", name, stop["element"],
                           "no outline of at least 2 px when focused with the keyboard")
            elif stop["contrast"] is not None and stop["contrast"] < 3:
                self._fail("focus-contrast", "1.4.11", name, stop["element"],
                           f"focus ring {stop['ring']} on {stop['around']} is "
                           f"{stop['contrast']}:1; needs 3:1")
        for control in {item["key"]: item for item in expected}.values():
            self.checked["keyboard-reach"] = self.checked.get("keyboard-reach", 0) + 1
            if control["key"] not in reached:
                self._fail("keyboard-reach", "2.1.1", name, control["element"],
                           "the Tab key never reached this control")
        page.evaluate("document.activeElement && document.activeElement.blur()")


class Stopwatch:
    """Journeys 3 and 4: the browser's own timing record for every screen."""

    def __init__(self, page):
        self.page = page
        self.records = []

    def __call__(self, name, first, after):
        record = self.page.evaluate(TIMING)
        record.update(screen=name, after=after, cold=not self.records)
        self.records.append(record)

    def summary(self):
        def stats(records):
            values = sorted(record["load"] for record in records)
            if not values:
                return None
            rank = max(1, math.ceil(0.95 * len(values)))
            return {"count": len(values), "median": round(statistics.median(values)),
                    "p95": round(values[rank - 1]), "max": round(values[-1])}

        warm = [record for record in self.records if not record["cold"]]
        cold = self.records[0]
        return {
            "first_visit": {"load": round(cold["load"]), "requests": cold["requests"],
                            "kilobytes": round(cold["bytes"] / 1000, 1)},
            "open": stats([r for r in warm if r["after"] == "open"]),
            "submit": stats([r for r in warm if r["after"] == "submit"]),
            "all": stats(self.records),
            "requests_per_later_screen": round(
                sum(r["requests"] for r in warm) / max(1, len(warm)), 2),
        }


class Keys:
    """Journey 2: the whole journey with the keyboard only."""

    def __init__(self, page, base_url, bank):
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.bank = bank
        self.presses = 0

    def press(self, key):
        self.page.keyboard.press(key)
        self.presses += 1

    def tab_to(self, selector):
        for _ in range(MAX_TABS):
            self.press("Tab")
            if self.page.evaluate(
                    "s => !!document.activeElement && document.activeElement.matches(s)",
                    selector):
                return
        raise RuntimeError(f"keyboard focus never reached {selector}")

    def activate(self, selector):
        self.tab_to(selector)
        with self.page.expect_navigation(wait_until="load"):
            self.press("Enter")

    def answer(self):
        scenario_id = self.page.locator("input[name=scenario_id]").get_attribute("value")
        label = self.bank[scenario_id]["label"]
        if self.page.locator("input[type=radio][name=answer]").count():
            self.tab_to("input[type=radio][name=answer]")
            self.press("Space")                       # selects the first choice
            for _ in range(2):                        # arrow keys move through the group
                if self.page.evaluate("document.activeElement.value") == label:
                    break
                self.press("ArrowDown")
            self.activate("form.decision button[type=submit]")
        else:
            self.activate(f"button[name=answer][value={label}]")

    def consent(self):
        self.page.goto(self.base_url + "/consent", wait_until="load")
        for box in ("adult", "agree"):
            self.tab_to(f"input[name={box}]")
            self.press("Space")
        self.activate("form.consent-form button[type=submit]")

    def run(self):
        page = self.page
        self.consent()
        self.activate("a[href$='/withdraw']")
        self.activate("form button.btn--danger")
        withdrawn = "withdrawn" in page.locator("h1").inner_text()
        self.consent()
        self.activate("a[href$='/assessment/pre']")
        for _ in range(12):
            self.answer()
        self.activate("a[href$='/learn']")
        self.activate("a[href$='/practice']")
        for _ in range(6):
            self.answer()
            self.activate(".panel a.btn--primary")
        self.activate("a[href$='/assessment/post']")
        for _ in range(12):
            self.answer()
        self.activate("a[href$='/results']")
        self.activate("a[href$='/survey']")
        for number in range(1, 11):
            self.tab_to(f"input[name=q{number}]")
            self.press("Space")                       # "Strongly disagree" ...
            for _ in range(3):
                self.press("ArrowRight")              # ... moved along to "Agree"
        self.activate("form.sus button[type=submit]")
        self.activate("form.finish button[type=submit]")
        finished = "signed out" in page.locator("h1").inner_text()
        return {"completed": withdrawn and finished, "presses": self.presses,
                "last_screen": page.locator("h1").inner_text()}


def timed_journey(browser, args, bank, context_options, throttle):
    context = browser.new_context(viewport=DESKTOP, **context_options)
    page = context.new_page()
    if throttle:
        session = context.new_cdp_session(page)
        session.send("Network.enable")
        session.send("Network.emulateNetworkConditions", SLOW_4G)
        session.send("Emulation.setCPUThrottlingRate", {"rate": CPU_SLOWDOWN})
    watch = Stopwatch(page)
    Journey(page, args.base_url, bank, watch).run()
    context.close()
    return watch.summary()


def audit(args):
    bank = {item["id"]: item for item in
            json.loads(Path(args.bank).read_text(encoding="utf-8"))["scenarios"]}
    admin = (args.admin_user, args.admin_password) if args.admin_user else None
    options = {"ignore_https_errors": args.ignore_https_errors}
    shots = Path(args.screenshots) if args.screenshots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        report = {"base_url": args.base_url, "browser": f"Chromium {browser.version}"}

        context = browser.new_context(viewport=DESKTOP, device_scale_factor=2, **options)
        if args.axe:
            context.add_init_script(path=args.axe)   # defines window.axe before any page script
        page = context.new_page()
        inspector = Inspector(page, shots, axe=bool(args.axe))
        Journey(page, args.base_url, bank, inspector, admin).run()
        context.close()
        report.update(
            screens=inspector.screens, checked=inspector.checked, failures=inspector.failures,
            advisories=inspector.advisories, axe=inspector.axe_result if args.axe else None)

        context = browser.new_context(viewport=DESKTOP, **options)
        try:
            report["keyboard"] = Keys(context.new_page(), args.base_url, bank).run()
        except (RuntimeError, PlaywrightError) as error:
            report["keyboard"] = {"completed": False, "presses": None,
                                  "error": str(error).splitlines()[0]}
        context.close()

        report["load_ms"] = {
            "no throttling": timed_journey(browser, args, bank, options, throttle=False),
            "emulated slow 4G": timed_journey(browser, args, bank, options, throttle=True),
        }
        browser.close()
    report["budget_ms"] = args.budget_load_ms
    return report


def problems(report):
    """Everything that makes the audit fail, as short sentences."""
    found = [f"{f['check']} ({f['criterion']}) on {f['screen']}: {f['element']}: {f['detail']}"
             for f in report["failures"]]
    if report["axe"]:
        found += [f"axe {rule['id']} ({rule['impact']}) on {len(rule['screens'])} screen(s): "
                  f"{rule['help']}; first element: {rule['nodes'][0] if rule['nodes'] else '-'}"
                  for rule in report["axe"]["violations"].values()]
    if not report["keyboard"]["completed"]:
        found.append("keyboard-only journey did not finish: "
                     + report["keyboard"].get("error", "the final screen was not reached"))
    for condition, summary in report["load_ms"].items():
        for key, label in (("open", "open a screen"), ("submit", "submit to next screen")):
            if summary[key]["p95"] > report["budget_ms"]:
                found.append(f"page load, {condition}, {label}: 95th percentile "
                             f"{summary[key]['p95']} ms exceeds {report['budget_ms']:g} ms")
    return found


def format_report(report):
    lines = [f"PhishAware browser audit: {report['base_url']}  ({report['browser']})",
             f"  {len(report['screens'])} screens inspected", ""]
    if report["axe"]:
        axe = report["axe"]
        lines += [f"axe-core {axe['version']}, WCAG 2.1 A and AA rules plus best practices",
                  f"  violations: {len(axe['violations'])} rule(s); needs manual review: "
                  f"{len(axe['incomplete'])} rule(s)"]
        for kind in ("violations", "incomplete"):
            for rule in axe[kind].values():
                lines.append(f"  {kind[:-1] if kind == 'violations' else 'review'}: {rule['id']} "
                             f"({rule['impact']}) on {len(rule['screens'])} screen(s): "
                             f"{rule['help']}")
        lines.append("")
    lines.append(f"  {'Scripted check':<52}{'WCAG':>8}{'checked':>9}{'failed':>8}")
    for check, criterion, label in CHECK_LABELS:
        failed = sum(1 for failure in report["failures"] if failure["check"] == check)
        lines.append(f"  {label:<52}{criterion:>8}{report['checked'].get(check, 0):>9}{failed:>8}")
    keyboard = report["keyboard"]
    lines += ["", "Keyboard only (2.1.1, 2.1.2): "
              + (f"a withdrawal and a complete journey with {keyboard['presses']} key presses"
                 if keyboard["completed"] else "NOT completed")]
    lines += ["", "Page load in the browser, navigation start to load event (ms)",
              f"  {'condition':<18}{'navigation':<22}{'count':>6}{'median':>8}{'p95':>8}{'max':>8}"]
    for condition, summary in report["load_ms"].items():
        for key, label in (("open", "open a screen"), ("submit", "submit to next screen"),
                           ("all", "all navigations")):
            stats = summary[key]
            lines.append(f"  {condition:<18}{label:<22}{stats['count']:>6}{stats['median']:>8}"
                         f"{stats['p95']:>8}{stats['max']:>8}")
            condition = ""
    for condition, summary in report["load_ms"].items():
        first = summary["first_visit"]
        lines.append(f"  first visit, {condition}: {first['load']} ms, {first['requests']} "
                     f"requests, {first['kilobytes']} kB; later screens average "
                     f"{summary['requests_per_later_screen']} requests")
    found = problems(report)
    lines += ["", f"Result: {'PASS' if not found else f'{len(found)} problem(s)'}"]
    lines += [f"  ! {line}" for line in found[:25]]
    if len(found) > 25:
        lines.append(f"  ! ... and {len(found) - 25} more (see the JSON report)")
    if report["advisories"]:
        lines.append(f"  advisory notes: {len(report['advisories'])} (see the JSON report)")
    return lines


def format_markdown(report):
    found = problems(report)
    lines = ["### Browser audit", "",
             f"{len(report['screens'])} screens, {report['browser']}. "
             f"**{'No problems found' if not found else f'{len(found)} problem(s)'}**.", "",
             "| Scripted check | WCAG | Checked | Failed |", "|---|---|---|---|"]
    for check, criterion, label in CHECK_LABELS:
        failed = sum(1 for failure in report["failures"] if failure["check"] == check)
        lines.append(f"| {label} | {criterion} | {report['checked'].get(check, 0)} | {failed} |")
    if report["axe"]:
        lines += ["", f"axe-core {report['axe']['version']}: "
                  f"{len(report['axe']['violations'])} rule(s) violated."]
    lines += ["", "| Page load (ms) | Median | 95th percentile | Maximum |", "|---|---|---|---|"]
    for condition, summary in report["load_ms"].items():
        stats = summary["all"]
        lines.append(f"| {condition} | {stats['median']} | {stats['p95']} | {stats['max']} |")
    return lines + [""]


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--bank", default=str(HERE.parent / "data" / "scenarios.json"))
    parser.add_argument("--axe", help="path to axe.min.js; without it the axe-core scan is skipped")
    parser.add_argument("--admin-user")
    parser.add_argument("--admin-password")
    parser.add_argument("--ignore-https-errors", action="store_true",
                        help="accept a certificate from a local authority (local HTTPS only)")
    parser.add_argument("--budget-load-ms", type=float, default=2000.0,
                        help="NFR-01: the 95th-percentile page load must not exceed this")
    parser.add_argument("--screenshots", help="folder for a screenshot of every screen")
    parser.add_argument("--json", help="write the full result to this file")
    parser.add_argument("--markdown", help="append Markdown tables to this file")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    report = audit(args)
    for line in format_report(report):
        print(line)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as handle:
            handle.write("\n".join(format_markdown(report)) + "\n")
    return 1 if problems(report) else 0


if __name__ == "__main__":
    sys.exit(main())
