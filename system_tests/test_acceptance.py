"""Acceptance tests: does the system do what was required of it?

Each case states one requirement as its users would judge it, in the form
"given, when, then", and checks it on the deployed system through a real
browser. Cases AT-01 to AT-11 cover the eleven functional requirements of the
Unit 3 baseline, one case for each; they are the critical acceptance cases
that objective O2 requires to pass without exception (marked "critical").
Cases AT-12 to AT-17 are the acceptance criteria of the six findings that
release 0.6.0 left open: five from the usability inspection (U-1 to U-5) and
one from the security review (S-8). Case AT-18 is the operator's acceptance of
the command-line tools.

These are requirement-based acceptance tests, run by the developer. They are
not user acceptance testing: no participant has judged the system yet. That
judgement is the pilot, with its usability survey.
"""

import base64
import html
import json
import re
import uuid
import zlib

from system_tests.harness import (
    CUE_LABELS, CUES, PHONE, ROOT, Administrator, SystemCase, bank, settings,
)

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
RATINGS = (4, 2, 4, 1, 5, 2, 4, 2, 4, 1)          # scores 82.5
SUS_STATEMENTS = (        # Brooke (1996), as listed in Appendix B of the design report
    "I think that I would like to use this system frequently.",
    "I found the system unnecessarily complex.",
    "I thought the system was easy to use.",
    "I think that I would need the support of a technical person to be able to use this system.",
    "I found the various functions in this system were well integrated.",
    "I thought there was too much inconsistency in this system.",
    "I would imagine that most people would learn to use this system very quickly.",
    "I found the system very cumbersome to use.",
    "I felt very confident using the system.",
    "I needed to learn a lot of things before I could get going with this system.",
)
SCALE = ("Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree")
EXPORT_COLUMNS = (
    ["record", "form_order", "pre_percent", "post_percent", "gain_points", "sus_score"]
    + [f"pre_{cue}" for cue in CUES] + [f"post_{cue}" for cue in CUES])
FEEDBACK_WORDS = ("Well spotted", "Not quite", "Red flags", "Trust signals", "Safe action")
# What a browser may contain inside a specimen: nothing that can be followed,
# typed into, or submitted (FR-05: scenarios are illustrations).
LIVE_CONTROLS = "a[href], form, input, button, textarea, select, iframe, object"
# Where a bracket stands on a line, for AT-16. Returns the sentences in which an
# opening bracket ends a line or a closing bracket begins one.
STRANDED_BRACKETS = r"""() => {
  const found = [];
  const top = (node, index) => {
    const range = document.createRange();
    range.setStart(node, index);
    range.setEnd(node, index + 1);
    const box = range.getClientRects()[0];
    return box ? box.top : null;
  };
  const texts = [];
  const walker = document.createTreeWalker(
    document.querySelector("main"), NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.textContent.trim() && node.parentElement.getClientRects().length) texts.push(node);
  }
  const neighbour = (list, at, index, step) => {
    // The next (or previous) visible character, in this text node or a later one.
    let node = list[at], position = index + step;
    for (;;) {
      if (position >= 0 && position < node.textContent.length) {
        if (node.textContent[position].trim()) return [node, position];
        position += step;
        continue;
      }
      at += step;
      if (at < 0 || at >= list.length) return null;
      node = list[at];
      position = step > 0 ? 0 : node.textContent.length - 1;
    }
  };
  texts.forEach((node, at) => {
    [...node.textContent].forEach((character, index) => {
      const step = "([".includes(character) ? 1 : (")]".includes(character) ? -1 : 0);
      if (!step) return;
      const other = neighbour(texts, at, index, step);
      const here = top(node, index);
      if (!other || here === null) return;
      const there = top(other[0], other[1]);
      if (there !== null && Math.abs(there - here) > 4) {
        const sentence = node.parentElement.closest("li, p, dd, h1, h2, h3") || node.parentElement;
        found.push(sentence.textContent.trim().split(/[ \n\t]+/).join(" ").slice(0, 70));
      }
    });
  });
  return found;
}"""


def lessons():
    with open(ROOT / "data" / "lessons.json", encoding="utf-8") as handle:
        return json.load(handle)["lessons"]


def session_content(cookie):
    """What a session cookie holds. It is signed, not encrypted, so anyone can read it."""
    compressed = cookie.startswith(".")
    payload = cookie.lstrip(".").split(".")[0]
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    return json.loads(zlib.decompress(raw) if compressed else raw)


class AcceptanceCase(SystemCase):
    """Checks that several acceptance cases share."""

    def assert_specimen_is_fictional_and_inert(self, user, page=None):
        """Every address is on a reserved domain, and nothing in the specimen can be used."""
        page = page or user.page
        specimen = page.locator("article.scenario")
        self.assertEqual(specimen.count(), 1)
        self.assertEqual(specimen.locator(LIVE_CONTROLS).count(), 0)
        # text_content also reads the link destination, which stays folded away
        # until the participant asks where the link really goes.
        hosts = [re.sub(r"[^a-z]+$", "", host.lower())
                 for host in specimen.locator(".host").all_text_contents()]
        self.assertTrue(hosts)
        for host in hosts:
            self.assertRegex(host, r"\.(example|test)$")
        return user.item(page)

    def assert_balanced_form(self, items):
        """Twelve items: two for each cue, and half of them phishing."""
        self.assertEqual(len(items), 12)
        for cue in CUES:
            self.assertEqual(sum(1 for item in items if item["cue"] == cue), 2, cue)
        self.assertEqual(sum(1 for item in items if item["label"] == "phishing"), 6)
        self.assertEqual({item["channel"] for item in items}, {"email", "web"})

    def participant_at(self, stage):
        """A scripted participant who has come as far as `stage`, over HTTP."""
        participant = self.participant()
        steps = (("consent", participant.consent),
                 ("pre", lambda: participant.assessment("pre", wrong={0, 5})),
                 ("lessons", participant.lessons),
                 ("practice", participant.practice),
                 ("post", lambda: participant.assessment("post", wrong={3})),
                 ("survey", lambda: participant.survey(RATINGS)))
        for name, step in steps:
            step()
            if name == stage:
                return participant
        raise ValueError(stage)

    def browser_at(self, stage, **options):
        """A browser that continues the journey of participant_at(stage)."""
        user = self.browser_user(**options)
        user.adopt(self.participant_at(stage))
        return user


class FunctionalRequirementTests(AcceptanceCase):
    """AT-01 to AT-11: one case for each functional requirement."""

    at_start = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        config = settings()
        if config.admin_user and config.admin_password and cls.at_start is None:
            # What the researcher sees before this run adds its own participants:
            # AT-11 needs to know whether the reporting threshold could be observed.
            administrator = Administrator()
            administrator.sign_in()
            complete = administrator.counts()["complete"]
            export, _header, _rows = administrator.export()
            FunctionalRequirementTests.at_start = {
                "complete": complete, "dashboard": administrator.dashboard().visible,
                "export_status": export.status, "export_page": export.visible}
            administrator.client.close()

    def test_01_nothing_is_stored_before_an_adult_consents(self):
        """AT-01 | FR-01 | critical |
        Consent information is shown, and nothing is stored before an adult agrees"""
        administrator = self.administrator()
        before = administrator.counts()["consented"]
        # Given a visitor, when they open the site,
        user = self.browser_user()
        user.open("/")
        # then they are shown what taking part means, and nothing else is reachable.
        self.assertEqual(user.path(), "/consent")
        information = user.main_text()
        for heading in ("What you will do", "What is stored", "What is never collected",
                        "How long it is kept", "Your rights", "Time limit"):
            self.assertIn(heading, information)
        user.open("/dashboard")
        self.assertTrue(user.path().startswith("/consent"))
        # When the form is sent without the age confirmation (past the browser's own check),
        visitor = self.client()
        refused = visitor.post("/consent", {"agree": "yes", "csrf_token": visitor.token()})
        # then it is refused with an explanation,
        self.assertEqual(refused.status, 400)
        self.assertIn("Confirm that you are 18 or older", refused.visible)
        # and when the visitor declines, the page says that nothing was stored.
        user.open("/consent")
        user.follow("a[href$='/consent/declined']")
        self.assertEqual(user.heading(), "Nothing was stored")
        self.assertEqual(administrator.counts()["consented"], before)
        # When an adult agrees, the learning path opens, and one record exists.
        user.consent()
        self.assertEqual(user.heading(), "Your learning path")
        self.assertEqual(administrator.counts()["consented"], before + 1)
        self.shot(user, "learning-path")
        self.assert_no_problems(user)

    def test_02_a_participant_is_known_only_by_a_random_identifier(self):
        """AT-02 | FR-02 | critical |
        A participant is known by a random ID only, and no personal detail is asked for"""
        fields = set()
        tags = []

        def note_fields(page):
            fields.update(page.locator("input, textarea, select").evaluate_all(
                "all => all.map(e => e.tagName.toLowerCase() + ':' + (e.type || ''))"))

        for _ in range(2):
            user = self.browser_user()
            user.checks.append(note_fields)
            user.journey(pre_wrong={0}, ratings=RATINGS, finish=False)
            tags.append(user.page.locator(".session-tag").inner_text())
            content = session_content(user.copy_of_cookies().session_cookie[1])
            self.assertEqual(sorted(content), ["_csrf_token", "_permanent", "participant_id"])
            self.assertRegex(content["participant_id"], f"^{UUID.pattern}$")
            self.assertEqual(tags[-1].split()[-1], content["participant_id"][-6:])
        # Then the only things a participant can enter are check boxes and choices,
        self.assertEqual(fields, {"input:hidden", "input:checkbox", "input:radio"})
        # and two participants have different identifiers.
        self.assertNotEqual(tags[0], tags[1])
        self.assertRegex(tags[0], r"^Anonymous session [0-9a-f]{6}$")

    def test_03_the_pre_assessment_has_twelve_items_and_no_feedback(self):
        """AT-03 | FR-03 | critical |
        The pre-assessment has twelve balanced items, gives no feedback, and ends with a score"""
        user = self.browser_at("consent")
        user.open("/dashboard")
        user.follow("a[href$='/assessment/pre']")
        items = []
        for position in range(12):
            text = user.main_text()
            self.assertIn(f"{position + 1} of 12", text)
            self.assertIn("phishing or legitimate?", text)
            for word in FEEDBACK_WORDS:
                self.assertNotIn(word, text)                     # no feedback before the lessons
            items.append(self.assert_specimen_is_fictional_and_inert(user))
            if position == 0:
                self.shot(user, "item")
            user.answer("pre", correct=position not in (2, 9))
        self.assert_balanced_form(items)
        self.assertEqual(len({item["pool"] for item in items}), 1)
        # Then the baseline is shown: ten of twelve correct.
        self.assertEqual(user.heading(), "Your baseline is saved")
        self.assertIn("83.3%", user.main_text())
        self.assertIn("You classified 10 of 12 correctly", user.main_text())
        self.assert_no_problems(user)

    def test_04_six_lessons_cover_the_six_cues(self):
        """AT-04 | FR-04 | critical |
        Six lessons, one for each phishing cue, open after the pre-assessment"""
        user = self.browser_at("consent")
        user.open("/learn")                          # given a participant before the pre-test
        self.assertEqual(user.path(), "/assessment/pre")
        user.assessment("pre")                       # when the pre-assessment is done
        user.follow("a[href$='/learn']")             # then the lessons open
        self.assertEqual(user.page.locator("li.lesson").count(), 6)
        shown = user.main_text()
        content = lessons()
        self.assertEqual({lesson["cue"] for lesson in content}, set(CUES))
        for lesson in content:
            for part in (lesson["title"], lesson["summary"], lesson["example"],
                         lesson["safe_action"], *lesson["look_for"]):
                self.assertIn(" ".join(part.split()), shown, lesson["cue"])
        self.shot(user, "lessons")
        user.open("/dashboard")                      # and the practice step is unlocked
        self.assertEqual(user.page.locator("a[href$='/practice']").count(), 1)
        self.assert_no_problems(user)

    def test_05_practice_scenarios_are_fictional_emails_and_web_pages(self):
        """AT-05 | FR-05 | critical |
        Practice presents fictional emails and web pages that cannot be clicked or typed into"""
        user = self.browser_at("lessons")
        user.open("/practice")
        items = []
        for position in range(6):
            self.assertIn(f"{position + 1} of 6", user.main_text())
            item = self.assert_specimen_is_fictional_and_inert(user)
            items.append(item)
            if item["channel"] == "web":
                self.assertIn("Nothing on it can be typed into or submitted", user.main_text())
            user.answer("practice")
            user.follow(".panel a.btn--primary")
        self.assertEqual({item["channel"] for item in items}, {"email", "web"})
        self.assertEqual({item["cue"] for item in items}, set(CUES))
        self.assertEqual({item["pool"] for item in items}, {"P"})   # a pool of their own
        self.assertIn("Every organization, person, and web address here is fictional", user.text())
        self.assert_no_problems(user)

    def test_06_every_practice_decision_is_explained_at_once(self):
        """AT-06 | FR-06 | critical |
        Each practice decision is followed at once by a cue-by-cue explanation"""
        user = self.browser_at("lessons")
        user.open("/practice")
        for position in range(6):
            correct = position % 2 == 1                 # wrong and right answers in turn
            item = user.answer("practice", correct=correct)
            # Then the very next screen explains the decision.
            text = user.text()
            self.assertIn("Correct. Well spotted." if correct
                          else "Not quite. Here is what to look for.", text)
            self.assertIn(item["label"].capitalize(), text)
            self.assertIn(f"the cue it tests is {CUE_LABELS[item['cue']].lower()}", text)
            self.assertIn("Red flags" if item["label"] == "phishing" else "Trust signals", text)
            panel = " ".join(user.page.locator(".workspace__side .panel").text_content().split())
            feedback = item["feedback"]
            for part in (feedback["explanation"], feedback["safe_action"], *feedback["cues"]):
                self.assertIn(" ".join(part.split()), panel, item["id"])
            if item["channel"] == "email" and item["email"].get("link"):
                self.assertTrue(user.page.locator("details.link-preview").get_attribute("open")
                                is not None, "the real link destination is not revealed")
            if position == 0:
                self.shot(user, "feedback")
            user.follow(".panel a.btn--primary")
        self.assertIn("You classified 3 of 6 correctly", user.main_text())
        self.assert_no_problems(user)

    def test_07_the_post_assessment_is_a_parallel_form_of_new_items(self):
        """AT-07 | FR-07 | critical |
        The post-assessment is a parallel form of twelve items that the participant has not seen"""
        user = self.browser_at("practice")
        earlier = set(user.expected.answered["pre"]) | set(user.expected.answered["practice"])
        user.open("/dashboard")
        user.follow("a[href$='/assessment/post']")
        items = []
        for position in range(12):
            self.assertIn(f"{position + 1} of 12", user.main_text())
            for word in FEEDBACK_WORDS:
                self.assertNotIn(word, user.main_text())
            items.append(self.assert_specimen_is_fictional_and_inert(user))
            user.answer("post", correct=position != 4)
        self.assert_balanced_form(items)
        self.assertFalse(earlier & {item["id"] for item in items})    # all twelve are new
        self.assertNotEqual(user.expected.forms["pre"], user.expected.forms["post"])
        self.assertEqual(user.heading(), "Your final score is saved")
        self.assertIn("91.7%", user.main_text())
        self.assert_no_problems(user)

    def test_08_results_show_both_scores_the_gain_and_cue_feedback(self):
        """AT-08 | FR-08 | critical |
        The results page shows both scores, the gain, and feedback for each cue"""
        participant = self.participant()
        participant.consent()
        participant.assessment("pre", wrong={0, 1, 2, 3, 4, 5})
        participant.lessons()
        participant.practice()
        participant.assessment("post", wrong={1, 7, 8})
        user = self.browser_user()
        user.adopt(participant)
        user.open("/dashboard")
        user.follow("a[href$='/results']")
        expected = user.expected
        scores, bars = user.results_shown()
        self.assertEqual(scores, [50.0, 75.0, 25.0])
        self.assertEqual(scores, [expected.percent("pre"), expected.percent("post"), expected.gain])
        self.assertEqual(bars, {CUE_LABELS[cue]: (expected.cue_percent("pre", cue),
                                                  expected.cue_percent("post", cue))
                                for cue in CUES})
        # The cues still missed after training, the worst first, at most two.
        misses = {cue: 2 - expected.correct["post"][cue] for cue in CUES}
        focus = sorted((cue for cue in CUES if misses[cue]), key=lambda cue: -misses[cue])[:2]
        self.assertEqual(user.page.locator(".next-steps .cue-list li").all_inner_texts(),
                         [CUE_LABELS[cue] for cue in focus])
        self.shot(user, "results")
        self.assert_no_problems(user)
        # A participant with a perfect post-assessment is told so.
        perfect = self.participant()
        perfect.consent()
        perfect.assessment("pre")
        perfect.lessons()
        perfect.practice()
        perfect.assessment("post")
        self.assertIn("You made no mistakes in the post-assessment", perfect.results().visible)

    def test_09_the_usability_survey_is_the_system_usability_scale(self):
        """AT-09 | FR-09 | critical |
        The survey has the ten SUS statements, and the standard score is stored"""
        administrator = self.administrator()
        self.completed_cohort(5)                     # the export needs five participants
        before = administrator.counts()["survey_done"]
        user = self.browser_at("post")
        user.open("/results")
        user.follow("a[href$='/survey']")
        statements = [" ".join(text.split()) for text in
                      user.page.locator(".sus__item legend").all_inner_texts()]
        self.assertEqual([re.sub(r"^\d+\s*", "", text) for text in statements],
                         list(SUS_STATEMENTS))
        self.assertEqual(user.page.locator("input[type=radio]").count(), 50)
        first_scale = user.page.locator(".sus__item").first.locator("label")
        self.assertEqual([" ".join(text.split()).lstrip("12345 ") for text in
                          first_scale.all_text_contents()], list(SCALE))
        # When one statement is left out, the form comes back with the answers kept.
        for number in range(1, 10):
            user.page.check(f"input[name=q{number}][value='{RATINGS[number - 1]}']")
        user.page.evaluate("document.querySelectorAll('input[required]')"
                           ".forEach(input => input.removeAttribute('required'))")
        user.follow("form.sus button[type=submit]")
        self.assertIn("Choose one answer for every statement", user.main_text())
        self.assertEqual(user.page.locator("input[type=radio]:checked").count(), 9)
        self.assertEqual(administrator.counts()["survey_done"], before)
        # When all ten are answered, the ratings are stored once.
        user.page.check(f"input[name=q10][value='{RATINGS[9]}']")
        user.follow("form.sus button[type=submit]")
        self.assertEqual(user.heading(), "Thank you for taking part")
        user.expected.ratings = list(RATINGS)
        self.assertEqual(administrator.counts()["survey_done"], before + 1)
        self.assertEqual(user.expected.sus, 82.5)
        # Then the export holds this participant's row with the standard score.
        _reply, _header, rows = administrator.export()
        exported = [[row[1]] + [float(value) if value else None for value in row[2:6]]
                    + [int(value) for value in row[6:]] for row in rows]
        self.assertIn(user.expected.export_row, exported)
        self.assert_no_problems(user)

    def test_10_withdrawal_at_any_step_deletes_every_record(self):
        """AT-10 | FR-10 | critical |
        A participant can withdraw at any step, and every record is deleted at once"""
        administrator = self.administrator()
        for stage, deleted in (("consent", {"consented"}),
                               ("pre", {"consented", "pre_done"}),
                               ("practice", {"consented", "pre_done", "lessons_opened"}),
                               ("survey", {"consented", "pre_done", "lessons_opened", "complete",
                                           "survey_done"})):
            with self.subTest(withdraw_after=stage):
                user = self.browser_at(stage)
                before = administrator.counts()
                user.open("/dashboard")
                copy = user.copy_of_cookies()
                user.follow("a[href$='/withdraw']")              # offered in the top bar
                self.assertEqual(user.heading(), "Withdraw and delete your data?")
                user.follow("a.btn--ghost")                      # "Keep my data and go back"
                self.assertEqual(user.path(), "/dashboard")
                self.assertEqual(administrator.counts(), before)
                user.follow("a[href$='/withdraw']")
                user.follow("form button.btn--danger")
                self.assertEqual(user.heading(), "You have withdrawn")
                after = administrator.counts()
                self.assertEqual(after, {name: count - (1 if name in deleted else 0)
                                         for name, count in before.items()})
                user.open("/dashboard")                          # the session is over,
                self.assertTrue(user.path().startswith("/consent"))
                self.assertEqual(copy.page("/dashboard").path, "/consent")   # for a copy too
                copy.close()
        # While a session lasts, every screen offers the way out.
        user = self.browser_user()
        missing = []
        user.checks.append(lambda page: page.locator("a[href$='/withdraw']").count() == 1
                           or missing.append(user.path(page)))
        user.journey(ratings=RATINGS, finish=False)
        self.assertEqual(missing, ["/consent"])   # only the page before the session began

    def test_11_the_researcher_sees_aggregates_only_and_a_de_identified_export(self):
        """AT-11 | FR-11 | critical |
        The researcher sees aggregates only, from five participants, with a de-identified export"""
        visitor = self.client()
        for address in ("/admin", "/admin/export.csv"):
            reply = visitor.get(address)                 # given no sign-in: nothing is shown
            self.assertEqual((reply.status, reply.location), (302, "/admin/login"))
        administrator = self.administrator()
        start = self.at_start
        observed = start is not None and start["complete"] < 5
        if observed:
            # Below five completed participants the statistics and the export are withheld.
            self.assertIn("Statistics appear at 5 completed participants", start["dashboard"])
            self.assertNotIn("Mean gain", start["dashboard"])
            self.assertEqual(start["export_status"], 403)
            self.assertIn("available once 5 participants", start["export_page"])
        people = [self.participant() for _ in range(5)]
        for number, person in enumerate(people):
            person.journey(pre_wrong=set(range(number + 3)), post_wrong=set(range(number)),
                           ratings=RATINGS if number else None)
        dashboard = administrator.dashboard()
        complete = administrator.counts()["complete"]
        self.assertGreaterEqual(complete, 5)
        self.assertIn(f"Learning outcome (RQ1), n = {complete}", dashboard.visible)
        for part in ("Mean before training", "Mean after training", "Mean gain",
                     "Correct answers by cue (RQ2)", "Usability (RQ3)", "De-identified export"):
            self.assertIn(part, dashboard.visible)
        self.assertIsNone(UUID.search(dashboard.text))            # aggregates, nobody's record
        self.assertNotIn("Anonymous session", dashboard.visible)
        reply, header, rows = administrator.export()
        self.assertEqual(reply.status, 200)
        self.assertIn("attachment", reply.header("Content-Disposition"))
        self.assertEqual(header, EXPORT_COLUMNS)                  # no identifier, no time
        self.assertEqual(len(rows), complete)
        self.assertEqual([row[0] for row in rows], [str(n) for n in range(1, complete + 1)])
        self.assertIsNone(UUID.search(reply.text))
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", reply.text))
        for row in rows:                                          # every row agrees with itself
            pre, post, gain = (float(value) for value in row[2:5])
            self.assertEqual(round(sum(int(v) for v in row[6:12]) / 12 * 100, 1), pre)
            self.assertEqual(round(sum(int(v) for v in row[12:18]) / 12 * 100, 1), post)
            self.assertEqual(round(post - pre, 1), gain)
        exported = [[row[1]] + [float(value) if value else None for value in row[2:6]]
                    + [int(value) for value in row[6:]] for row in rows]
        for person in people:
            self.assertIn(person.expected.export_row, exported)
        # The order of the rows says nothing: three downloads are not three times the same.
        orders = {tuple(tuple(row[1:]) for row in administrator.export()[2]) for _ in range(3)}
        orders.add(tuple(tuple(row[1:]) for row in rows))
        self.assertGreater(len(orders), 1)
        self.note = ("threshold observed below five participants" if observed else
                     f"the instance already held {start['complete'] if start else 'five or more'}"
                     " completed participants, so the threshold itself was not observed")


class OpenFindingTests(AcceptanceCase):
    """AT-12 to AT-17: the findings that release 0.6.0 left open."""

    def test_12_an_ended_session_is_explained(self):
        """AT-12 | NFR-02 (U-1) | A participant whose session has ended is told so, and why"""
        user = self.browser_at("pre")
        user.open("/learn")
        # Given a form that is open when the session ends (the cookie expires after two hours),
        user.open("/practice")
        user.choose()
        user.context.clear_cookies()
        # when it is sent, then a page says that the session has ended, and offers the start.
        user.follow("form.decision button[type=submit]")
        self.assertIn("session has ended", user.main_text().lower())
        self.assertIn("2 hours", user.main_text())
        self.shot(user, "form-after-the-session-ended")
        self.assertEqual(user.page.locator("main a.btn").count(), 1)
        # When the participant follows a link to a step, the consent page explains as well.
        user.open("/dashboard")
        self.assertTrue(user.path().startswith("/consent"))
        notice = user.page.locator("main [role=status]")
        self.assertEqual(notice.count(), 1, "the consent page gives no reason")
        self.assertIn("session has ended", notice.inner_text().lower())
        self.assertIn("2 hours", notice.inner_text())
        self.shot(user, "consent-after-the-session-ended")
        # A first-time visitor sees no such notice.
        newcomer = self.browser_user()
        newcomer.open("/")
        self.assertEqual(newcomer.page.locator("main [role=status]").count(), 0)

    def test_13_the_contact_line_is_on_every_page(self):
        """AT-13 | NFR-02 (U-2) | The researcher's contact line is on every page of the journey"""
        visitor = self.client()
        match = re.search(r"Contact the researcher: (.+?)</p>", visitor.page("/consent").text)
        if match is None:
            self.skipTest("the instance shows no contact line: set PHISHAWARE_CONTACT on it")
        contact = " ".join(html.unescape(match.group(1)).split())
        user = self.browser_user()
        without = []
        user.checks.append(
            lambda page: contact in user.text(page) or without.append(user.path(page)))
        user.journey(ratings=RATINGS)
        user.open("/no-such-page")
        self.assertEqual(len(without), 0, f"of {user.screens} screens, this many do not show "
                                          f"{contact!r}; the first are {without[:3]}")
        self.assertGreaterEqual(user.screens, 45)
        self.shot(user, "contact-line", full_page=False)

    def test_14_the_survey_fits_a_phone(self):
        """AT-14 | NFR-02 (U-3) | On a 360 px screen each survey scale stands in one row"""
        user = self.browser_at("post", size=PHONE, touch=True)
        user.open("/survey")
        for number in (1, 10):
            boxes = [box.bounding_box() for box in
                     user.page.locator(f"input[name=q{number}]").all()]
            tops = [box["y"] for box in boxes]
            self.assertLess(max(tops) - min(tops), 4,
                            f"the choices of statement {number} are stacked")
        for target in user.page.locator(".sus__choice").all():
            box = target.bounding_box()
            self.assertGreaterEqual(min(box["width"], box["height"]), 24)   # WCAG 2.5.8
        height = user.page.evaluate("document.documentElement.scrollHeight")
        self.assertLessEqual(height, 2600, "the survey is longer than three and a half screens")
        self.assertFalse(user.scrolls_sideways())
        for label in ("Strongly disagree", "Strongly agree"):   # the ends of the scale are named
            widths = [box["width"] for box in (
                element.bounding_box() for element in
                user.page.get_by_text(label, exact=True).all()) if box]
            self.assertGreater(max(widths, default=0), 40, f"{label!r} is not shown")
        self.shot(user, "survey-360")
        user.survey(RATINGS)                             # and it can still be filled in
        self.assertEqual(user.heading(), "Thank you for taking part")
        self.note = f"survey page {height} px high at 360 px"

    def test_15_an_unknown_address_is_answered_in_plain_words(self):
        """AT-15 | NFR-02 (U-4) | An unknown address is answered in the application's plain words"""
        user = self.browser_at("consent")
        response = user.open("/this-page-does-not-exist")
        self.assertEqual(response.status, 404)
        self.assertEqual(user.heading(), "Page not found")
        text = user.main_text()
        self.assertNotIn("The requested URL was not found on the server", text)
        self.assertNotIn("Error 404", text)              # no code as the page's headline
        for kicker in user.page.locator("main .kicker").all_inner_texts():
            self.assertNotRegex(kicker, r"\d")
        self.assertEqual(user.page.locator("main a.btn").count(), 1)
        self.shot(user, "page-not-found")
        user.follow("main a.btn")                        # the way back works,
        self.assertEqual(user.path(), "/dashboard")      # and the session was kept

    def test_16_a_bracket_stays_with_the_address_it_encloses(self):
        """AT-16 | NFR-04 (U-5) | A bracket is never separated from the address it encloses"""
        user = self.browser_at("lessons", size=PHONE, touch=True)
        user.open("/practice")
        stranded = {}
        for position in range(6):
            item = user.answer("practice", correct=False)
            for width in (320, 360, 412):
                user.page.set_viewport_size({"width": width, "height": PHONE[1]})
                found = user.page.evaluate(STRANDED_BRACKETS)
                if found:
                    stranded[f"{item['id']} at {width} px"] = found
                if position == 0 and width == 360:
                    self.shot(user, "feedback-360")
            user.page.set_viewport_size({"width": PHONE[0], "height": PHONE[1]})
            user.follow(".panel a.btn--primary")
        self.assertEqual(len(stranded), 0, "of 18 screens, this many show a bracket apart from "
                                           f"its address; the first: {list(stranded.items())[:1]}")

    def test_17_a_commonly_used_password_is_refused(self):
        """AT-17 | NFR-10 (S-8) |
        A commonly used password is refused for the administrator account"""
        name = f"probe-{uuid.uuid4().hex[:8]}"
        for password in ("qwertyqwerty", "QWERTYqwerty", "123456789012"):
            with self.subTest(password=password):
                result = self.cli("create-admin", "--username", name, "--password", password)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("commonly used", (result.stdout + result.stderr).lower())
        passphrase = f"{uuid.uuid4().hex[:6]}-orchid-{uuid.uuid4().hex[:6]}-ledger"
        result = self.cli("create-admin", "--username", name, "--password", passphrase)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Administrator '{name}' created.", result.stdout)


class OperatorTests(AcceptanceCase):
    """AT-18: the operator's acceptance of the documented commands."""

    def test_18_the_documented_commands_work_on_the_running_instance(self):
        """AT-18 | NFR-12 | The operator's documented commands work on the running instance"""
        counts = self.administrator().counts()
        analytics = self.cli("analytics")
        self.assertEqual(analytics.returncode, 0, analytics.stderr)
        self.assertIn(f"Participants who consented: {counts['consented']}", analytics.stdout)
        self.assertIsNone(UUID.search(analytics.stdout))
        backup = self.cli("backup-db")
        self.assertEqual(backup.returncode, 0, backup.stderr)
        self.assertRegex(backup.stdout, r"Encrypted backup written: \S+phishaware-\d{8}T\d{6}Z"
                                        r"\.db\.enc \(\d+ bytes\)")
        dry_run = self.cli("purge-expired", "--dry-run")
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertIn("Would delete 0 participant record(s) older than", dry_run.stdout)
        self.assertEqual(self.administrator().counts(), counts)       # nothing was changed
        refused = self.cli("create-admin", "--username", "x", "--password", "long-enough-password")
        self.assertEqual(refused.returncode, 2)                       # a name of one character
        self.assertEqual(len(bank()), 30)
