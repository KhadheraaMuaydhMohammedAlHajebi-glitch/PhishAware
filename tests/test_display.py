"""Tests for how scenario addresses are split and rendered (M4, NFR-04).

A look-alike domain must never be broken at its hyphen: on a narrow screen that
would leave the genuine-looking part on a line of its own. White-box tests cover
the two helper functions; black-box tests check the markup that a participant's
browser receives.
"""

import html
import re
import unittest

from flask import render_template

from src import repository
from src.modules import display
from tests.helpers import AppTestCase

TAGS = re.compile(r"<[^>]+>")
LABEL = re.compile(r'<span class="host__label">([^<]*)</span>')


class UrlPartsTests(unittest.TestCase):
    def test_hyphenated_lookalike_host_stays_in_one_label(self):
        self.assertEqual(
            display.url_parts("https://learn-northbridge.example/login"),
            {"scheme": "https://", "labels": ["learn-northbridge", ".example"],
             "rest": "/login"})

    def test_subdomain_trick_is_split_only_at_its_dots(self):
        parts = display.url_parts("http://northbridge.example.grade-portal.test/login")
        self.assertEqual(parts["labels"], ["northbridge", ".example", ".grade-portal", ".test"])

    def test_query_string_and_missing_path(self):
        parts = display.url_parts("https://cloudlocker.example-files.test/view?id=88213")
        self.assertEqual(parts["rest"], "/view?id=88213")
        self.assertEqual(display.url_parts("https://paywave.example")["rest"], "")
        self.assertEqual(display.url_parts("https://paywave.example?next=1")["rest"], "?next=1")

    def test_parts_join_back_into_the_original_address(self):
        for url in ("https://learn.northbridge.example/grades/info3310",
                    "http://wifi-northbridge.test/auth",
                    "https://user@host.example:8443/a#b"):
            parts = display.url_parts(url)
            self.assertEqual(parts["scheme"] + "".join(parts["labels"]) + parts["rest"], url)

    def test_text_that_is_not_a_url_is_left_alone(self):
        for value in ("Open document", "https://", "see https://a.example now", "", None, 42):
            self.assertIsNone(display.url_parts(value), value)


class AddressPartsTests(unittest.TestCase):
    def test_local_part_keeps_its_at_sign(self):
        self.assertEqual(
            display.address_parts("dean.office@northbridge-admin.example"),
            {"local": "dean.office@", "labels": ["northbridge-admin", ".example"]})

    def test_text_that_is_not_an_address_is_left_alone(self):
        for value in ("not an address", "a@b@c", "@northbridge.example", "you@", "", None):
            self.assertIsNone(display.address_parts(value), value)


class AddressesInTextTests(unittest.TestCase):
    """The "addresses" filter, used wherever a sentence mentions a domain."""

    def text_of(self, markup):
        return html.unescape(TAGS.sub("", str(markup)))

    def test_lookalike_domain_in_a_sentence_becomes_one_unit(self):
        sentence = "northbridge.example.grade-portal.test belongs to grade-portal.test"
        markup = display.protect_addresses(sentence)
        self.assertEqual(
            LABEL.findall(str(markup)),
            ["northbridge", ".example", ".grade-portal", ".test", "grade-portal", ".test"])
        self.assertEqual(self.text_of(markup), sentence)       # nothing added or lost

    def test_email_address_keeps_its_local_part_and_its_host_whole(self):
        markup = str(display.protect_addresses("it-services@northbrldge.example is not genuine"))
        self.assertTrue(markup.startswith(
            '<span class="host__label">it-services@</span><wbr><span class="host">'
            '<span class="host__label">northbrldge</span><wbr>'
            '<span class="host__label">.example</span></span> is not genuine'), markup)

    def test_punctuation_that_touches_a_host_stays_on_its_line(self):
        # Finding U-5: on a phone, "(" ended a line and the address began the next.
        sentence = "A look-alike domain (northbridge-admin.example), then learn.x.test."
        markup = str(display.protect_addresses(sentence))
        self.assertEqual(
            LABEL.findall(markup),
            ["(northbridge-admin", ".example),", "learn", ".x", ".test."])
        self.assertTrue(markup.startswith('A look-alike domain <span class="host">'), markup)
        self.assertEqual(self.text_of(markup), sentence)       # nothing added or lost

    def test_a_bracket_before_an_email_address_stays_with_its_local_part(self):
        sentence = 'Dana Lee (dana.lee@student.northbridge.example) shared a file'
        markup = str(display.protect_addresses(sentence))
        self.assertIn('Dana Lee <span class="host__label">(dana.lee@</span><wbr>'
                      '<span class="host"><span class="host__label">student</span>', markup)
        self.assertIn('<span class="host__label">.example)</span></span> shared a file', markup)
        self.assertEqual(self.text_of(markup), sentence)

    def test_quotation_marks_and_sentence_punctuation_are_kept_with_the_address(self):
        for sentence, first, last in (
                ('Type "paywave.example".', '&#34;paywave', '.example&#34;.'),
                ("Is it paywave.example?", "paywave", ".example?"),
                ("See [help.northbridge.example]; then wait", "[help", ".example];"),
                ("\u201cgrade-portal.test\u201d is fake", "\u201cgrade-portal", ".test\u201d")):
            markup = str(display.protect_addresses(sentence))
            labels = LABEL.findall(markup)
            self.assertEqual((labels[0], labels[-1]), (first, last), sentence)
            self.assertEqual(self.text_of(markup), sentence)

    def test_punctuation_that_does_not_touch_an_address_is_left_alone(self):
        sentence = "The page (not secure) is at wifi-northbridge.test now"
        markup = str(display.protect_addresses(sentence))
        self.assertTrue(markup.startswith("The page (not secure) is at <span"), markup)
        self.assertEqual(LABEL.findall(markup), ["wifi-northbridge", ".test"])

    def test_host_inside_a_url_is_protected_and_the_rest_is_left(self):
        markup = str(display.protect_addresses("Open https://learn-northbridge.example/login now"))
        self.assertIn('https://<span class="host"><span class="host__label">learn-northbridge'
                      '</span>', markup)
        self.assertIn("</span></span>/login now", markup)

    def test_file_names_numbers_and_abbreviations_are_left_alone(self):
        for sentence in ("Invoice_88213.zip from a bank you have never used",
                         "Scores rose by 3.5 points, e.g. after practice",
                         "Your enrollment will be cancelled at 5:00 PM today",
                         "Reply with the 6-digit code we sent to your phone"):
            self.assertEqual(str(display.protect_addresses(sentence)), sentence)

    def test_markup_in_the_sentence_is_escaped(self):
        markup = str(display.protect_addresses('<script>x</script> at evil.example & "more"'))
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; at ", markup)
        self.assertIn("&amp;", markup)


class ScenarioMarkupTests(AppTestCase):
    def item_page(self, scenario_id):
        """Render one scenario exactly as the assessment page does."""
        with self.app.test_request_context():
            row = repository.get_scenario(scenario_id, scenario_id[0])
            return render_template("_scenario.html", scenario=repository.scenario_content(row),
                                   reveal=True)

    def test_every_host_label_is_unbreakable_and_text_is_unchanged(self):
        page = self.item_page("A04")   # https://northbridge-portal-login.test/signin
        self.assertIn("northbridge-portal-login", LABEL.findall(page))
        self.assertIn("https://northbridge-portal-login.test/signin",
                      html.unescape(TAGS.sub("", page)))

    def test_break_points_exist_only_around_the_host_and_before_dots(self):
        page = self.item_page("A01")   # link goes to northbridge.example.grade-portal.test
        self.assertIn(
            'http://<wbr><span class="host"><span class="host__label">northbridge</span><wbr>'
            '<span class="host__label">.example</span><wbr>'
            '<span class="host__label">.grade-portal</span><wbr>'
            '<span class="host__label">.test</span></span><wbr>/login', page)

    def test_sender_address_keeps_the_closing_bracket_with_the_domain(self):
        page = self.item_page("A01")
        self.assertIn('<span class="host__label">&lt;grades@</span><wbr><span class="host">', page)
        self.assertIn('<span class="host__label">.example&gt;</span>', page)

    def test_hyphenated_local_part_is_unbreakable_too(self):
        page = self.item_page("A02")   # From: it-services@northbridge.example
        self.assertIn('<span class="host__label">&lt;it-services@</span><wbr>', page)

    def test_address_in_a_message_body_is_protected(self):
        page = self.item_page("A11")   # the body names (dana.lee@student.northbridge.example)
        self.assertIn(
            'Dana Lee <span class="host__label">(dana.lee@</span><wbr><span class="host">'
            '<span class="host__label">student</span>', page)
        self.assertIn('<span class="host__label">.example)</span></span> shared a folder', page)

    def test_no_scenario_shows_an_address_that_could_break_inside_a_label(self):
        addresses = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|[a-z]+://[\w-]+(?:\.[\w-]+)+")
        for row in self.query("SELECT id FROM scenario ORDER BY id"):
            page = self.item_page(row["id"])
            # Remove the marked-up hosts; no bare host may remain in a header or link.
            marked = re.sub(r'<span class="host">.*?</span></span>', "", page, flags=re.S)
            for block in re.findall(r'<(?:dd|code)>.*?</(?:dd|code)>|class="(?:fake-link|'
                                    r'browser__url)">.*?</span>\s*(?:</p>|</div>)',
                                    marked, flags=re.S):
                self.assertIsNone(addresses.search(TAGS.sub("", block)), (row["id"], block))

    def test_mock_controls_are_announced_to_screen_readers(self):
        web = self.item_page("A04")
        for words in ("Address bar: ", "Text field: ", "Button: "):
            self.assertIn(f'<span class="visually-hidden">{words}</span>', web)
        self.assertIn('<span class="visually-hidden">Link: </span>', self.item_page("A01"))


class SentencesWithAddressesTests(AppTestCase):
    """Lessons and feedback mention domains in running text; none may be left bare."""

    BARE = display.IN_TEXT
    MARKED = re.compile(r'(?:<span class="host__label">[^<]*@</span><wbr>)?'
                        r'<span class="host">.*?</span></span>', re.S)

    def bare_addresses(self, page):
        main = page.split("<main", 1)[1].split("</main>", 1)[0]
        return self.BARE.findall(html.unescape(TAGS.sub(" ", self.MARKED.sub(" ", main))))

    def test_lessons_show_no_address_that_could_break_inside_a_label(self):
        self.consent()
        self.answer_pretest()
        page = self.client.get("/learn").get_data(as_text=True)
        self.assertIn('<span class="host__label">.grade-portal</span>', page)
        self.assertIn('<span class="host__label">it-services@</span>', page)
        self.assertEqual(self.bare_addresses(page), [])

    def test_feedback_shows_no_address_that_could_break_inside_a_label(self):
        self.consent()
        self.answer_pretest()
        self.open_lessons()
        marked = 0
        for _ in range(6):
            response = self.answer_current("/practice")       # redirects to the feedback
            page = self.client.get(response.headers["Location"]).get_data(as_text=True)
            self.assertIn("Safe action", page)
            self.assertEqual(self.bare_addresses(page), [])
            marked += page.count('<span class="host">')
        self.assertGreater(marked, 6)   # the six practice items do mention domains

    def test_feedback_text_of_every_scenario_renders_without_a_bare_address(self):
        with self.app.test_request_context():
            for row in self.query("SELECT id, pool FROM scenario ORDER BY id"):
                scenario = repository.scenario_content(
                    repository.get_scenario(row["id"], row["pool"]))
                page = render_template(
                    "practice_feedback.html", scenario=scenario, correct=True,
                    answer=scenario["label"], cue_label="cue", answered=1, total=6)
                self.assertEqual(self.bare_addresses(page), [], row["id"])


class DecisionFormTests(AppTestCase):
    def test_answer_is_chosen_first_and_sent_with_a_separate_button(self):
        self.consent()
        page = self.client.get("/assessment/pre").get_data(as_text=True)
        for value in ("phishing", "legitimate"):
            self.assertIn(f'<input type="radio" name="answer" value="{value}" required>', page)
        self.assertIn('<button type="submit" class="btn btn--primary btn--block">Submit answer',
                      page)
        self.assertNotIn('<button type="submit" name="answer"', page)   # no one-click answers
        self.assertIn("You can change your choice until you submit", page)

    def test_practice_uses_the_same_two_step_form(self):
        self.consent()
        self.answer_pretest()
        self.client.get("/learn")
        page = self.client.get("/practice").get_data(as_text=True)
        self.assertEqual(page.count('type="radio" name="answer"'), 2)
