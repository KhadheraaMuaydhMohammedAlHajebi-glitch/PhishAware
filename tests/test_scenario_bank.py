"""Data-integrity tests for the fictional scenario bank (research validity and ethics)."""

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from src.modules.scoring import CUE_CATEGORIES, validate_form

BANK_FILE = Path(__file__).resolve().parent.parent / "data" / "scenarios.json"
BANK = json.loads(BANK_FILE.read_text(encoding="utf-8"))["scenarios"]


def pool(name):
    return [item for item in BANK if item["pool"] == name]


def hosts(item):
    """Every domain shown in a scenario: sender, reply-to, recipient, link, or URL."""
    found = []
    mail = item.get("email")
    if mail:
        found += [mail[key].split("@")[-1] for key in ("from_address", "reply_to", "to")
                  if mail.get(key)]
        if mail.get("link"):
            found.append(urlparse(mail["link"]["href"]).hostname)
    if item.get("web"):
        found.append(urlparse(item["web"]["url"]).hostname)
    return found


class ScenarioBankTests(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = [item["id"] for item in BANK]
        self.assertEqual(len(ids), len(set(ids)))

    def test_parallel_forms_are_balanced(self):
        for form in ("A", "B"):
            self.assertEqual(validate_form(pool(form)), [], f"Form {form}")

    def test_forms_have_matched_difficulty(self):
        self.assertEqual(
            sum(item["difficulty"] for item in pool("A")),
            sum(item["difficulty"] for item in pool("B")),
        )

    def test_practice_pool_covers_every_cue(self):
        self.assertEqual({item["cue"] for item in pool("P")}, set(CUE_CATEGORIES))

    def test_every_scenario_has_complete_feedback(self):
        for item in BANK:
            feedback = item["feedback"]
            self.assertTrue(feedback["explanation"], item["id"])
            self.assertGreaterEqual(len(feedback["cues"]), 2, item["id"])
            self.assertTrue(feedback["safe_action"], item["id"])

    def test_only_reserved_fictional_domains_are_used(self):
        # Ethics: no real organization's domain may appear (RFC 2606 reserved TLDs).
        for item in BANK:
            for host in hosts(item):
                self.assertTrue(host.endswith((".example", ".test")), f"{item['id']}: {host}")

    def test_an_unbalanced_form_is_reported(self):
        problems = validate_form(pool("A")[1:])   # one item short
        self.assertIn("expected 12 items, found 11", problems)
        self.assertEqual(len(problems), 3)        # item count, one cue, and label balance
