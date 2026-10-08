"""Tests for static-file fingerprints and caching (NFR-01).

Black-box tests request real static files; two white-box tests point the
application at a temporary static folder to change a file and to try a path
outside the folder.
"""

import os
import re

from flask import url_for

from src.modules import assets
from tests.helpers import AppTestCase

STYLESHEET = re.compile(r'href="(/static/css/style\.css\?v=([0-9a-f]{10}))"')
ONE_YEAR = "public, max-age=31536000, immutable"


class StaticCachingTests(AppTestCase):
    def stylesheet_url(self):
        page = self.client.get("/consent").get_data(as_text=True)
        return STYLESHEET.search(page).group(1)

    def test_pages_link_to_fingerprinted_files(self):
        page = self.client.get("/consent").get_data(as_text=True)
        self.assertRegex(page, STYLESHEET)
        self.assertRegex(page, r'src="/static/js/app\.js\?v=[0-9a-f]{10}"')
        self.assertRegex(page, r'href="/static/img/favicon\.svg\?v=[0-9a-f]{10}"')

    def test_current_fingerprint_may_be_cached_for_a_year(self):
        response = self.client.get(self.stylesheet_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], ONE_YEAR)
        response.close()

    def test_missing_or_outdated_fingerprint_must_be_revalidated(self):
        for url in ("/static/css/style.css", "/static/css/style.css?v=0000000000"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(response.headers["Cache-Control"], "no-cache", url)
            response.close()

    def test_conditional_request_keeps_the_caching_rule(self):
        url = self.stylesheet_url()
        first = self.client.get(url)
        etag = first.headers["ETag"]
        first.close()
        again = self.client.get(url, headers={"If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.headers["Cache-Control"], ONE_YEAR)
        again.close()

    def test_unknown_file_is_a_plain_404(self):
        response = self.client.get("/static/css/missing.css?v=0000000000")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("max-age", response.headers.get("Cache-Control", ""))

    def test_pages_themselves_are_never_cached(self):
        self.assertEqual(self.client.get("/consent").headers["Cache-Control"], "no-store")

    def test_changed_file_gets_a_new_address(self):
        self.app.static_folder = self._tmp.name
        path = os.path.join(self._tmp.name, "theme.css")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("body { color: black; }")
        with self.app.test_request_context():
            before = url_for("static", filename="theme.css")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("body { color: navy; } /* edited */")
            after = url_for("static", filename="theme.css")
            self.assertRegex(before, r"^/static/theme\.css\?v=[0-9a-f]{10}$")
            self.assertNotEqual(before, after)
            self.assertEqual(url_for("static", filename="absent.css"), "/static/absent.css")

    def test_fingerprint_never_reads_outside_the_static_folder(self):
        with self.app.test_request_context():
            self.assertIsNotNone(assets.fingerprint("css/style.css"))
            self.assertIsNone(assets.fingerprint("../config.py"))
            self.assertIsNone(assets.fingerprint("../../requirements.txt"))
