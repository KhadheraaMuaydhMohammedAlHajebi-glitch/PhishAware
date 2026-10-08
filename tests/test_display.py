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
        self.assertIn('&lt;grades@<wbr><span class="host">', page)
        self.assertIn('<span class="host__label">.example&gt;</span>', page)

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
