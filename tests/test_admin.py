"""Black-box tests for M7 administrator reporting (FR-11, NFR-10, NFR-11).

The tests drive the sign-in form, the dashboard, and the export through Flask's
test client and check only responses and stored records. Boundary values are the
fifth failed sign-in, the fifth completed participant, and the 15-minute idle limit.
"""

import csv
import io
import time
from unittest import mock

from src import repository
from src.db import get_db
from src.modules import admin
from tests.helpers import ADMIN_PASSWORD, ADMIN_USER, AppTestCase

SUS_85 = [4, 2, 5, 1, 4, 2, 5, 2, 4, 1]
# (correct before, correct after, survey ratings): five scripted journeys whose
# statistics can be checked by hand.
COHORT = (
    (6, 9, SUS_85),         # 50.0 -> 75.0, gain +25.0, SUS 85.0
    (9, 11, [4, 2] * 5),    # 75.0 -> 91.7, gain +16.7, SUS 75.0
    (7, 10, [3] * 10),      # 58.3 -> 83.3, gain +25.0, SUS 50.0
    (8, 8, [5, 1] * 5),     # 66.7 -> 66.7, gain   0.0, SUS 100.0
    (10, 12, None),         # 83.3 -> 100.0, gain +16.7, no survey
)


class AdminAccessTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.create_admin().exit_code, 0)

    def test_dashboard_and_export_require_sign_in(self):
        for url in ("/admin", "/admin/export.csv"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/admin/login"))

    def test_valid_credentials_open_the_dashboard(self):
        response = self.admin_sign_in()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin"))
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Pilot results", page.data)
        # The top bar names the account; narrow screens show the short form.
        self.assertIn(
            f'<span class="only-wide">Administrator</span>'
            f'<span class="only-narrow">Admin</span> {ADMIN_USER}'.encode(), page.data)
        self.assertEqual(page.headers["Cache-Control"], "no-store")

    def test_username_is_trimmed_and_case_insensitive(self):
        self.assertEqual(self.admin_sign_in(username="  Researcher ").status_code, 302)

    def test_wrong_password_and_unknown_user_get_the_same_answer(self):
        wrong = self.admin_sign_in(password="not-the-password")
        unknown = self.admin_sign_in(username="nobody", password=ADMIN_PASSWORD)
        for response in (wrong, unknown):
            self.assertEqual(response.status_code, 401)
            self.assertIn(admin.FAILED_MESSAGE.encode(), response.data)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_password_is_checked_exactly_as_typed(self):
        spaced = "  two spaces around, Mixed Case  "
        self.create_admin(username="exact", password=spaced)
        for altered in (spaced.strip(), spaced.lower(), spaced[:20]):
            self.assertEqual(
                self.admin_sign_in(username="exact", password=altered).status_code, 401)
        self.assertEqual(self.admin_sign_in(username="exact", password=spaced).status_code, 302)

    def test_password_is_stored_only_as_a_salted_hash(self):
        self.create_admin(username="second-admin")
        hashes = [row["password_hash"] for row in self.query(
            "SELECT password_hash FROM admin_user ORDER BY id")]
        self.assertEqual(len(hashes), 2)
        self.assertNotEqual(hashes[0], hashes[1])  # same password, different salts
        for stored in hashes:
            self.assertTrue(stored.startswith("scrypt:"))
            self.assertNotIn(ADMIN_PASSWORD, stored)

    def test_production_hash_uses_a_setting_from_the_owasp_cheat_sheet(self):
        from src.config import Config
        self.assertEqual(Config.ADMIN_PASSWORD_METHOD, "scrypt:32768:8:3")
        self.app.config["ADMIN_PASSWORD_METHOD"] = Config.ADMIN_PASSWORD_METHOD
        self.create_admin(username="production-admin")
        stored = self.query(
            "SELECT password_hash FROM admin_user WHERE username = 'production-admin'"
        )[0]["password_hash"]
        self.assertTrue(stored.startswith("scrypt:32768:8:3$"))
        self.assertEqual(self.admin_sign_in(username="production-admin").status_code, 302)

    def test_sixth_attempt_is_blocked_even_with_the_right_password(self):
        for _ in range(5):
            self.assertEqual(self.admin_sign_in(password="guess").status_code, 401)
        blocked = self.admin_sign_in()
        self.assertEqual(blocked.status_code, 429)
        self.assertIn(admin.LOCKED_MESSAGE.encode(), blocked.data)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_four_failures_do_not_lock_the_account(self):
        for _ in range(4):
            self.admin_sign_in(password="guess")
        self.assertEqual(self.admin_sign_in().status_code, 302)
        self.assertEqual(self.count("admin_login_attempt"), 0)  # success clears the count

    def test_lock_applies_to_one_username_and_ends_after_the_window(self):
        self.create_admin(username="second-admin")
        for _ in range(5):
            self.admin_sign_in(password="guess")
        self.assertEqual(self.admin_sign_in(username="second-admin").status_code, 302)
        self.client.post("/admin/logout", data={"csrf_token": self.token()})
        with self.app.app_context():                # age the attempts past the window
            get_db().execute(
                "UPDATE admin_login_attempt SET attempted_at = '2026-01-01T00:00:00+00:00'")
            get_db().commit()
        self.assertEqual(self.admin_sign_in().status_code, 302)
        self.assertEqual(self.count("admin_login_attempt"), 0)  # expired attempts are pruned

    def test_failures_across_many_usernames_also_trigger_the_limit(self):
        self.app.config["ADMIN_MAX_FAILED_TOTAL"] = 3
        for username in ("guess-one", "guess-two"):
            self.assertEqual(self.admin_sign_in(username=username).status_code, 401)
        self.assertEqual(self.admin_sign_in(password="guess").status_code, 401)
        self.assertEqual(self.admin_sign_in().status_code, 429)   # third failure reached the cap

    def test_no_ip_address_is_stored_with_a_failed_attempt(self):
        self.admin_sign_in(password="guess")
        columns = [row["name"] for row in self.query("PRAGMA table_info(admin_login_attempt)")]
        self.assertEqual(columns, ["id", "username", "attempted_at"])

    def test_session_ends_after_fifteen_idle_minutes(self):
        self.admin_sign_in()
        with self.client.session_transaction() as session:
            session["admin_seen"] = int(time.time()) - 15 * 60 - 1
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login?expired=1", response.headers["Location"])
        page = self.client.get(response.headers["Location"])
        self.assertIn(admin.EXPIRED_MESSAGE.encode(), page.data)

    def test_activity_within_the_limit_keeps_the_session_open(self):
        self.admin_sign_in()
        with self.client.session_transaction() as session:
            session["admin_seen"] = int(time.time()) - 14 * 60
        self.assertEqual(self.client.get("/admin").status_code, 200)
        with self.client.session_transaction() as session:
            self.assertGreater(session["admin_seen"], int(time.time()) - 5)

    def test_session_ends_eight_hours_after_sign_in_however_active(self):
        self.admin_sign_in()
        with self.client.session_transaction() as session:
            session["admin_since"] = int(time.time()) - 8 * 3600 + 60   # one minute left
        self.assertEqual(self.client.get("/admin").status_code, 200)
        with self.client.session_transaction() as session:
            session["admin_since"] = int(time.time()) - 8 * 3600 - 1
            session["admin_seen"] = int(time.time())                    # active a moment ago
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login?expired=1", response.headers["Location"])

    def test_session_without_a_sign_in_time_is_refused(self):
        self.admin_sign_in()
        with self.client.session_transaction() as session:
            del session["admin_since"]
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_changing_the_password_signs_out_open_sessions(self):
        self.admin_sign_in()
        result = self.create_admin(password="a-different-long-password")
        self.assertIn("updated; earlier sessions are signed out", result.output)
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.assertEqual(self.admin_sign_in().status_code, 401)
        self.assertEqual(
            self.admin_sign_in(password="a-different-long-password").status_code, 302)

    def test_sign_out_needs_a_csrf_token_and_ends_the_session(self):
        self.admin_sign_in()
        self.assertEqual(self.client.post("/admin/logout").status_code, 400)
        self.assertEqual(self.client.get("/admin").status_code, 200)
        response = self.client.post("/admin/logout", data={"csrf_token": self.token()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_copy_of_the_cookie_is_refused_after_sign_out(self):
        self.admin_sign_in()
        copy = self.copy_session()                       # taken while signed in
        self.assertEqual(copy.get("/admin").status_code, 200)
        response = self.client.post("/admin/logout", data={"csrf_token": self.token()})
        self.assertEqual(response.headers["Clear-Site-Data"], '"cache", "storage"')
        for url in ("/admin", "/admin/export.csv"):
            self.assertEqual(copy.get(url).status_code, 302, url)

    def test_sign_out_ends_the_sessions_on_every_device(self):
        self.admin_sign_in()
        other_device = self.app.test_client()
        self.admin_sign_in(client=other_device)
        self.client.post("/admin/logout", data={"csrf_token": self.token()})
        response = other_device.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login?expired=1", response.headers["Location"])
        self.assertEqual(self.admin_sign_in(client=other_device).status_code, 302)

    def test_sign_out_without_a_session_changes_nothing(self):
        before = self.query("SELECT session_stamp FROM admin_user")[0]["session_stamp"]
        response = self.client.post("/admin/logout", data={"csrf_token": self.token()})
        self.assertEqual(response.status_code, 302)
        after = self.query("SELECT session_stamp FROM admin_user")[0]["session_stamp"]
        self.assertEqual(before, after)

    def test_signed_in_administrator_skips_the_sign_in_form(self):
        self.admin_sign_in()
        response = self.client.get("/admin/login")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin"))

    def test_roles_are_separate(self):
        participant = self.app.test_client()
        self.consent(participant)
        self.assertEqual(participant.get("/admin").status_code, 302)   # no administrator role
        self.admin_sign_in()
        response = self.client.get("/dashboard")                       # no participant role
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/consent"))

    def test_signing_in_replaces_a_participant_session(self):
        self.consent()
        self.admin_sign_in()
        with self.client.session_transaction() as session:
            self.assertNotIn("participant_id", session)


class AdminReportingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.create_admin()
        self.admin_sign_in()

    def run_cohort(self, cohort=COHORT):
        return [self.complete_session(*journey) for journey in cohort]

    def dashboard(self):
        return self.client.get("/admin").get_data(as_text=True)

    def export(self):
        response = self.client.get("/admin/export.csv")
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        return response, rows[0], rows[1:]

    def test_empty_database_shows_zero_counts_and_no_rate(self):
        page = self.dashboard()
        self.assertIn("0 of 5 participants have finished both assessments", page)
        self.assertNotIn("of those who consented", page)

    def test_statistics_stay_hidden_below_five_completed_participants(self):
        self.run_cohort(COHORT[:4])
        page = self.dashboard()
        self.assertIn("Statistics appear at 5 completed participants", page)
        self.assertIn("4 of 5 participants have finished both assessments", page)
        for hidden in ("Mean gain", "Score summary", "Download the CSV export"):
            self.assertNotIn(hidden, page)
        response = self.client.get("/admin/export.csv")
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"available once 5 participants", response.data)

    def test_fifth_completed_participant_unlocks_the_statistics(self):
        self.run_cohort()
        page = self.dashboard()
        self.assertIn("Learning outcome (RQ1), n = 5", page)
        for expected in (
            "66.7%", "83.3%", "+16.7 points",            # mean before, after, and gain
            "<td>66.7</td><td>66.7</td><td>13.2</td>",   # pre: mean, median, SD
            "<td>83.3</td><td>83.3</td><td>13.2</td>",   # post
            "<td>16.7</td><td>16.7</td><td>10.2</td>",   # gain
            "= 1.63, t(4) = 3.65",                       # 16.667 / 10.206; times sqrt(5)
            "<strong>77.5</strong> (median 80.0) from 4 survey responses",
            "80% of those who consented (target 80%)",   # four of five finished the survey
        ):
            self.assertIn(expected, page)

    def test_incomplete_participants_are_counted_but_not_averaged(self):
        self.run_cohort()
        self.consent(self.app.test_client())              # consent only
        unfinished = self.app.test_client()
        self.consent(unfinished)
        self.answer_pretest(correct=False, client=unfinished)   # 0% and no post-test
        page = self.dashboard()
        self.assertIn("Learning outcome (RQ1), n = 5", page)
        self.assertIn("+16.7 points", page)
        self.assertIn("57% of those who consented", page)  # 4 of 7
        with self.app.app_context():
            self.assertEqual(repository.funnel_counts(), {
                "consented": 7, "pre_done": 6, "lessons_opened": 5, "practice_done": 5,
                "post_done": 5, "survey_done": 4})

    def test_identical_gains_are_reported_without_an_effect_size(self):
        self.run_cohort([(6, 9, None)] * 5)
        page = self.dashboard()
        self.assertIn("no effect size can be computed", page)
        self.assertIn("No completed participant has answered the usability survey yet", page)

    def test_single_survey_response_is_worded_in_the_singular(self):
        self.run_cohort([(6, 9, SUS_85)] + [(6, 8, None)] * 4)
        self.assertIn("from 1 survey response.", self.dashboard())

    def test_export_is_de_identified_and_matches_the_stored_scores(self):
        participant_ids = self.run_cohort()
        response, header, rows = self.export()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn("phishaware-export.csv", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(header, admin.EXPORT_COLUMNS)
        self.assertEqual(len(header), 18)
        self.assertEqual([row[0] for row in rows], ["1", "2", "3", "4", "5"])
        self.assertEqual(sorted(row[2:6] for row in rows), sorted([
            ["50.0", "75.0", "25.0", "85.0"],
            ["75.0", "91.7", "16.7", "75.0"],
            ["58.3", "83.3", "25.0", "50.0"],
            ["66.7", "66.7", "0.0", "100.0"],
            ["83.3", "100.0", "16.7", ""],
        ]))
        self.assertEqual(sorted(row[1] for row in rows), ["AB", "AB", "AB", "BA", "BA"])
        for row in rows:
            pre_cues, post_cues = [int(v) for v in row[6:12]], [int(v) for v in row[12:18]]
            self.assertTrue(all(0 <= value <= 2 for value in pre_cues + post_cues))
            self.assertEqual(sum(pre_cues), round(float(row[2]) * 12 / 100))
            self.assertEqual(sum(post_cues), round(float(row[3]) * 12 / 100))
        body = response.get_data(as_text=True)
        for participant_id in participant_ids:
            self.assertNotIn(participant_id, body)
            self.assertNotIn(participant_id[-6:], body)
        self.assertNotIn("2026", body)  # no timestamp of any kind

    def test_export_rows_are_shuffled_before_they_are_numbered(self):
        self.run_cohort()
        with self.app.app_context():
            records = repository.export_records()
        with mock.patch.object(admin.secrets, "SystemRandom") as system_random:
            system_random.return_value.shuffle.side_effect = lambda items: items.reverse()
            rows = admin.export_rows(records)
        self.assertEqual([row[0] for row in rows], [1, 2, 3, 4, 5])
        self.assertEqual([row[2] for row in rows], [83.3, 66.7, 58.3, 75.0, 50.0])
        self.assertEqual(len(records), 5)  # the caller's list is left in enrolment order
        self.assertEqual(records[0]["form_order"], "AB")


class CreateAdminCommandTests(AppTestCase):
    def run_command(self, *args, **kwargs):
        return self.app.test_cli_runner().invoke(args=["create-admin", *args], **kwargs)

    def test_password_is_prompted_twice_and_never_echoed(self):
        result = self.run_command(
            "--username", "researcher", input=f"{ADMIN_PASSWORD}\n{ADMIN_PASSWORD}\n")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Administrator 'researcher' created.", result.output)
        self.assertNotIn(ADMIN_PASSWORD, result.output)
        self.assertEqual(self.count("admin_user"), 1)

    def test_short_password_is_rejected(self):
        result = self.create_admin(password="elevenchars")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("at least 12 characters", result.output)
        self.assertEqual(self.count("admin_user"), 0)

    def test_twelve_character_password_is_accepted(self):
        self.assertEqual(self.create_admin(password="twelve-chars").exit_code, 0)

    def test_invalid_usernames_are_rejected(self):
        for username in ("ab", "has space", "a" * 33, "-leading", "semi;colon"):
            result = self.create_admin(username=username)
            self.assertEqual(result.exit_code, 2, username)
        self.assertEqual(self.count("admin_user"), 0)

    def test_username_is_normalised_to_lower_case(self):
        self.create_admin(username="  Lead.Researcher ")
        self.assertEqual(
            self.query("SELECT username FROM admin_user")[0]["username"], "lead.researcher")
