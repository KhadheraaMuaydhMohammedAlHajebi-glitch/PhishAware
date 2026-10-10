# Security checklist: OWASP ASVS 5.0

Requirement NFR-12 asks that no critical or high finding is open before the pilot. This document records how release 0.6.0 was checked against the OWASP Application Security Verification Standard, version 5.0.0 (May 2025), what the check found, and what release 0.7.0 changed (section 7).

## 1. Scope and method

- **Target.** Every applicable Level 1 requirement, and the Level 2 requirements listed in section 4, chosen because they protect participant data or the administrator account. ASVS describes Level 1 as the first layer of defence and a suitable starting point for an application that holds limited sensitive data; PhishAware stores pseudonymous answers and no direct identifiers.
- **Method.** Each of the 70 Level 1 requirements was read against the code and classified as met, not applicable, deviation, or open. A requirement counts as met only where an automated test or a CI check demonstrates it, or where the code makes it true by construction (for example, the application has no file upload). The evidence column names the test or check.
- **Reviewer and date.** The developer, on 8 October 2026; brought up to date for release 0.7.0 on 10 October 2026.
- **Limits.** This is a self-assessment against a checklist. It is not an independent audit and not a penetration test, and a checklist cannot show the absence of flaws it does not ask about.

## 2. Result

| | Requirements |
|---|---|
| Level 1 requirements in ASVS 5.0.0 | 70 |
| Not applicable (no file upload, OAuth, WebSocket, XML, operating-system calls, or generated passwords) | 16 |
| Applicable | 54 |
| Met | 53 (52 in release 0.6.0) |
| Deviation, accepted with a stated reason (6.2.3) | 1 |
| Open | 0 (6.2.4 was open in release 0.6.0) |

The review found eight requirements that the code did not meet when the review began: six at Level 1 and two at Level 2. Seven were corrected in release 0.6.0 (section 5). The eighth, a check of the administrator's password against commonly used ones, was rated low and corrected in release 0.7.0. **No finding of this review is open.** Section 7 lists three further weaknesses that this review did not find and that testing found.

## 3. Level 1 requirements

Status: **Met**, **N/A** (not applicable), **Deviation**, or **Open**. "Fixed" marks a requirement that was not met when the review began.

### V1 Encoding and sanitization

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 1.2.1 | Output is encoded for the HTML context | Met | Jinja2 auto-escaping on every template; `test_contact_line_appears_only_when_configured` checks that markup in a setting is escaped |
| 1.2.2 | URLs are built with encoding; only safe protocols | Met | Every link is built with `url_for`; scenario links are illustrations without a destination |
| 1.2.3 | No injection into JavaScript or JSON | Met | No inline script and no script built from data (the policy forbids both); JSON only through Flask's serializer |
| 1.2.4 | Database queries are parameterized | Met | All SQL is in `src/repository.py` with `?` placeholders; `test_injection_style_scenario_id_is_rejected`; Bandit in CI |
| 1.2.5 | No operating-system command injection | N/A | The application runs no operating-system commands |
| 1.3.1 | Untrusted HTML is sanitized | N/A | No HTML input is accepted |
| 1.3.2 | No `eval` or dynamic code execution | Met | None in the code; Bandit in CI |
| 1.5.1 | XML parsers are configured safely | N/A | No XML is parsed |

### V2 Validation and business logic

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 2.1.1 | Validation rules are documented | Met | Section 6.1 |
| 2.2.1 | Input is validated against an allow list | Met | `test_answer_outside_allowlist_is_rejected`, `test_values_just_outside_the_scale_are_rejected`, `test_invalid_usernames_are_rejected` |
| 2.2.2 | Validation is enforced on the server | Met | Same tests: they post directly to the server |
| 2.3.1 | Steps can only be taken in order | Met | `test_practice_opens_only_after_the_lessons`, `test_practice_rejects_out_of_order_requests`, `test_survey_is_locked_until_the_post_test_is_complete`, `test_finish_is_not_available_before_the_survey` |

### V3 Web front-end security

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 3.2.1 | Content cannot be rendered in the wrong context | Met | `X-Content-Type-Options: nosniff` on every response; the export is sent as an attachment |
| 3.2.2 | Text is rendered as text | Met | Auto-escaping; the one script file never writes HTML |
| 3.3.1 | Cookies are `Secure` and carry the `__Host-` or `__Secure-` prefix | Met (fixed) | `test_https_settings_add_hsts_and_a_host_bound_secure_cookie`; CI smoke test |
| 3.4.1 | HSTS with a maximum age of at least one year | Met | Same test; CI smoke test. The Level 2 addition (all subdomains) is not adopted, see section 4 |
| 3.4.2 | CORS allow-origin is fixed or validated | N/A | No CORS header is sent, so no other origin can read a response |
| 3.5.1 | Cross-origin requests to sensitive functions are rejected | Met | Anti-forgery token on every state-changing request: `test_post_without_csrf_token_is_rejected`, `test_finish_requires_the_security_token`; `SameSite=Lax` |
| 3.5.2 | (If relying on CORS preflight) | N/A | The application does not rely on it |
| 3.5.3 | Sensitive functions use POST | Met | Consent, answers, withdrawal, finish, sign-in, and sign-out are POST routes |

### V4 API and web service

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 4.1.1 | Responses declare their content type and character set | Met | HTML, CSS, JavaScript, SVG, and CSV responses carry `charset=utf-8` |
| 4.4.1 | WebSockets use TLS | N/A | No WebSockets |

### V5 File handling

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 5.2.1, 5.2.2, 5.3.1 | Uploaded files are limited, checked, and never executed | N/A | No file upload |
| 5.3.2 | File paths come from trusted data | Met | Backup names are built from the clock; static files are resolved with `safe_join` (`test_fingerprint_never_reads_outside_the_static_folder`) |

### V6 Authentication (the administrator account)

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 6.1.1 | Defences against guessing are documented | Met | Section 6.2 |
| 6.2.1 | Passwords have at least 8 characters | Met | Minimum 12: `test_short_password_is_rejected`, `test_twelve_character_password_is_accepted` |
| 6.2.2 | The user can change the password | Met | `flask create-admin` replaces it: `test_changing_the_password_signs_out_open_sessions` |
| 6.2.3 | A change requires the current password | Deviation | A change requires command-line access to the host instead. The account holder is the operator of the host, and host access already gives access to the database |
| 6.2.4 | Passwords are checked against the 3,000 most common | Met (fixed in 0.7.0) | `flask create-admin` refuses a password on a list of 3,708 commonly used ones of twelve or more characters, in any capitalisation: acceptance case AT-17 and the tests of `tests/test_admin.py` |
| 6.2.5 | Any composition is allowed | Met | No character rules |
| 6.2.6 | Password fields mask the entry | Met | `type="password"`; the command line does not echo (`test_password_is_prompted_twice_and_never_echoed`) |
| 6.2.7 | Paste and password managers work | Met | Standard fields with `autocomplete` values; nothing blocks pasting |
| 6.2.8 | The password is checked exactly as typed | Met | `test_password_is_checked_exactly_as_typed` |
| 6.3.1 | The documented defences are implemented | Met | `test_sixth_attempt_is_blocked_even_with_the_right_password`, `test_failures_across_many_usernames_also_trigger_the_limit` |
| 6.3.2 | No default accounts | Met | No account exists until `create-admin` is run |
| 6.4.1 | Generated initial passwords are random and expire | N/A | None are generated |
| 6.4.2 | No password hints or secret questions | Met | None exist |

### V7 Session management

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 7.2.1 | Session tokens are verified on the server | Met | The signed cookie is verified on every request |
| 7.2.2 | Tokens are generated dynamically | Met | A new signed cookie per session |
| 7.2.3 | Reference tokens are random | N/A | The session is a self-contained signed cookie (see V9) |
| 7.2.4 | A new token is issued at sign-in | Met | The session is cleared at consent and at sign-in: `test_signing_in_replaces_a_participant_session` |
| 7.4.1 | A terminated session cannot be used again | Met (fixed) | `test_copy_of_the_cookie_is_refused_after_finishing`, `test_copy_of_the_cookie_is_refused_after_sign_out`, `test_session_is_refused_after_two_hours_without_activity` |
| 7.4.2 | Sessions end when the account is deleted | Met | `test_withdrawal_deletes_all_linked_records` (the session is refused afterwards); an administrator session is valid only while its account exists |

### V8 Authorization

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 8.1.1 | Authorization rules are documented | Met | Section 6.3 |
| 8.2.1 | Functions are restricted by role | Met | `test_dashboard_and_export_require_sign_in`, `test_roles_are_separate`, `test_protected_pages_redirect_to_consent` |
| 8.2.2 | Data is restricted to its owner | Met | No request carries a participant identifier; every query uses the identifier in the verified session |
| 8.3.1 | Rules are enforced on the server | Met | Same tests |

### V9 Self-contained tokens (the session cookie)

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 9.1.1 | The signature is verified before the content is used | Met | `test_cookie_signed_with_the_old_sha1_default_is_refused` |
| 9.1.2 | Only allow-listed algorithms; never "none" | Met | One fixed algorithm, HMAC-SHA-256; the cookie has no algorithm field: `test_session_cookie_is_signed_with_hmac_sha256` |
| 9.1.3 | Keys come from a trusted, configured source | Met | `PHISHAWARE_SECRET_KEY`; production refuses to start without 32 characters: `test_production_refuses_to_start_without_a_secret_key` |
| 9.2.1 | The validity period is enforced | Met | `test_session_is_refused_after_two_hours_without_activity` |

### V10 OAuth and OIDC

Five Level 1 requirements (10.4.1 to 10.4.5): **N/A**. The application is not an authorization server and uses none.

### V11 Cryptography

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 11.3.1 | No insecure block mode or weak padding | Met | No ECB; backups use an authenticated mode |
| 11.3.2 | Only approved ciphers and modes, such as AES with GCM | Met (fixed) | Backups use AES-256-GCM: `test_backup_file_is_encrypted_and_decrypts_to_a_database`, `test_altered_backup_is_rejected_and_changes_nothing` |
| 11.4.1 | Only approved hash functions | Met (fixed) | SHA-256 for the cookie signature and file fingerprints; scrypt for passwords |

### V12 Secure communication

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 12.1.1 | Only TLS 1.2 and 1.3, newest preferred | Met | CI connects with each version and checks that TLS 1.1 is refused |
| 12.2.1 | TLS for all traffic, with no fallback | Met | CI smoke test: HTTP is redirected (308) and HSTS is sent |
| 12.2.2 | Publicly trusted certificates | Met on a public host | The proxy obtains the certificate for `PHISHAWARE_DOMAIN`; `localhost` uses a local authority |

### V13 Configuration

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 13.4.1 | No source-control data in the deployment | Met | CI lists the files in the image: code, content, and requirements files only |

### V14 Data protection

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 14.2.1 | No sensitive data in URLs | Met | The session is a cookie; tokens and answers travel in the request body |
| 14.3.1 | Data is cleared from the browser when a session ends | Met (fixed) | Pages are never cached (`test_pages_themselves_are_never_cached`), no browser storage is used, and `Clear-Site-Data` is sent when a session ends (`test_browser_data_is_cleared_only_when_a_session_ends`) |

### V15 Secure coding and architecture

| ID | Requirement (short form) | Status | Evidence |
|---|---|---|---|
| 15.1.1 | Time frames for fixing vulnerable components are documented | Met | Section 6.4 |
| 15.2.1 | No component is past those time frames | Met | `pip-audit` in CI on every push: no known vulnerabilities on the review date |
| 15.3.1 | Responses contain only the fields that are needed | Met | The export has no identifier and no time: `test_export_is_de_identified_and_matches_the_stored_scores`; `test_probe_reveals_no_participant_data` |

## 4. Level 2 requirements that were checked

| ID | Requirement (short form) | Status | Note |
|---|---|---|---|
| 2.3.3 | Operations succeed completely or not at all | Met | Each answer is one committed statement; completing a phase is idempotent and is repeated on the next request if it was interrupted |
| 2.4.1 | Anti-automation | Partly | Sign-in is rate-limited. Consent is not: a limit would need an IP address or another identifier, which the design refuses to collect. The cost is that a script could create empty records; they hold no personal data and expire with the retention period |
| 3.3.2, 3.3.3, 3.3.4 | `SameSite`, `__Host-` prefix, `HttpOnly` | Met | `test_session_cookie_is_hardened` and the HTTPS test |
| 3.4.1 (Level 2 part) | HSTS covers all subdomains | Not adopted | `includeSubDomains` would bind other services on the same domain for a year; that decision belongs to the owner of the domain |
| 3.4.3 | Content security policy with `object-src 'none'` and `base-uri 'none'` | Met (fixed) | `test_security_headers_are_sent` |
| 3.4.4, 3.4.5, 3.4.6 | `nosniff`, referrer policy, `frame-ancestors` | Met | Same test |
| 6.3.3 | Multi-factor authentication | Not adopted | One administrator, aggregate-only data, a 12-character minimum, scrypt, and a rate limit were judged sufficient for a pilot |
| 7.3.1, 7.3.2 | Inactivity time-out and longest session | Met | 15 minutes and 8 hours for the administrator: `test_session_ends_after_fifteen_idle_minutes`, `test_session_ends_eight_hours_after_sign_in_however_active` |
| 7.4.4 | Sign-out is visible on every page | Met | Top bar |
| 11.4.2 | Passwords use an approved slow hash with current parameters | Met (fixed) | scrypt with N = 2^15, r = 8, p = 3: `test_production_hash_uses_a_setting_from_the_owasp_cheat_sheet` |
| 11.5.1 | Unguessable values come from a secure generator | Met | `uuid4`, `secrets`, and `os.urandom` |
| 12.3.3 | TLS between internal services | Not adopted | The proxy reaches the application over the private network of one host |
| 13.3.1 | Secrets are held in a secrets manager | Not adopted | Secrets are in `.env` on the host, readable by its owner only |
| 13.4.2, 13.4.4 | Debug mode off; no TRACE | Met | `test_production_refuses_debug_mode`; TRACE returns 405 |
| 13.4.5 | Monitoring endpoints are not exposed unless intended | Met | `/healthz` is public on purpose and returns a status, the version, and the scenario count |
| 14.3.2 | Sensitive pages are not cached | Met | `Cache-Control: no-store` on every page and on the export |
| 15.1.2 | Inventory of third-party components | Met | `constraints.txt` lists all eleven packages; CI fails if the image differs |
| 15.2.3 | Production contains no test code | Met | CI lists the files in the image |
| 16.2.5 | No sensitive data in logs | Met | No identifier and no IP address is logged; neither server writes an access log |
| 16.3.1 | Authentication events are logged | Met | Success, failure, and rate-limit events, without the username that was tried |
| 16.4.3 | Logs are sent to a separate system | Not adopted | Container logs stay on the host |
| 16.5.1 | Errors show a generic message | Met | `test_unknown_page_returns_safe_404` |

## 5. Findings

| # | Requirement | Finding | Severity | Status |
|---|---|---|---|---|
| S-1 | 7.4.1 (L1) | Finish and sign-out cleared the cookie in the browser, but a copy of the cookie stayed valid until it expired | Medium | Fixed: the end of a session is recorded on the server |
| S-2 | 3.3.1 (L1) | The session cookie had no `__Host-` prefix | Low | Fixed |
| S-3 | 11.3.2 (L1) | Backups used AES in CBC mode (through Fernet), which ASVS 5.0 lists as a legacy mode | Low | Fixed: AES-256-GCM |
| S-4 | 11.4.1 (L1) | The session cookie was signed with HMAC-SHA-1, which ASVS 5.0 lists as legacy | Low | Fixed: HMAC-SHA-256 |
| S-5 | 3.4.3 (L2) | The content security policy lacked `object-src 'none'` and `base-uri 'none'` | Low | Fixed |
| S-6 | 11.4.2 (L2) | Password hashing used a library default that is cheaper than current guidance | Low | Fixed |
| S-7 | 14.3.1 (L1) | Nothing told the browser to clear its data when a session ended. Pages were already never cached | Low | Fixed: `Clear-Site-Data` |
| S-8 | 6.2.4 (L1) | A new administrator password was not compared with a list of common passwords | Low | Fixed in release 0.7.0 |

**Why S-8 was rated low.** The system has one administrator account, created by the operator on the command line. The password must have at least 12 characters, is stored with scrypt, and sign-in is limited to five failed attempts per account in 15 minutes. The account gives access to aggregate statistics and a de-identified export, never to individual records. The remaining risk was that the operator chooses a common 12-character password. Release 0.7.0 closes it: the command refuses such a password and changes nothing.

Severity follows the usual four steps (critical, high, medium, low), judged by what an attacker would gain and what they would need first. S-1 needed a copy of a victim's cookie and gave access to one participant's results, or to the aggregate dashboard, for at most two hours.

## 6. Documented rules

### 6.1 Input validation (requirement 2.1.1)

All validation runs on the server, before any database access.

| Input | Rule |
|---|---|
| Scenario identifier | One of A, B, or P followed by two digits; it must also belong to the form the participant is on |
| Answer | Exactly `phishing` or `legitimate` |
| Usability ratings | Ten whole numbers from 1 to 5; all ten are required |
| Consent | Both check boxes must be present with the value `yes` |
| Anti-forgery token | Required on every POST; compared in constant time |
| Administrator user name | 3 to 32 characters: lowercase letters, digits, dot, underscore, hyphen |
| Administrator password | At least 12 characters; any characters; not on the list of commonly used passwords |
| Any request | A body of at most 64 kB; a larger one is refused with status 413 before it is read |
| Settings | Checked when the application starts (see `docs/deployment.md`, section 4) |

Participants type no free text anywhere in the application.

### 6.2 Defences against password guessing (requirement 6.1.1)

- At most five failed sign-ins per user name in 15 minutes, and at most 50 across all user names, because each attempt costs a deliberately slow hash. Further attempts in the window are refused, even with the right password.
- The limit is keyed on the user name that was tried, not on an IP address.
- A wrong password and an unknown user name receive the same answer after the same amount of work, so the response does not reveal which accounts exist.
- Passwords are stored with scrypt (N = 2^15, r = 8, p = 3) and a random salt.
- A session ends after 15 minutes without a request, 8 hours after sign-in at the latest, at sign-out, and when the password changes.

### 6.3 Authorization (requirement 8.1.1)

| Role | How it is established | May do |
|---|---|---|
| Visitor | No session | Read the consent page; decline |
| Participant | Session created at consent, holding a random identifier | Work through the steps in order; read their own results; withdraw; finish |
| Administrator | Sign-in with user name and password | Read aggregate statistics (shown only from five completed participants); download the de-identified export |

No role can read another participant's records: the application has no page and no export that shows them. An administrator session and a participant session cannot coexist in one browser.

### 6.4 Updating third-party components (requirement 15.1.1)

`pip-audit` runs on every push and compares the pinned packages with the published advisories.

| Severity of the advisory | Fix within |
|---|---|
| Critical or high, and the affected code is used | 7 days, and before the next pilot session |
| Medium | 30 days |
| Low, or the affected code is not used | The next release |

Without an advisory, dependencies are reviewed at the start of each release.

## 7. Release 0.7.0: what testing found that this review had not

The review of section 3 asks, requirement by requirement, whether a control exists and whether a test shows it. Integration and system testing for release 0.7.0 sent hostile input to every form and looked at what the database holds afterwards. They found three weaknesses in controls that the review had counted as met. Each is corrected, with a regression test (`docs/test-report.md`, section 6).

| Defect | Chapter of the standard | What was wrong in release 0.6.0 | Severity | Status |
|---|---|---|---|---|
| D-1 | V2 Validation; V16 Error handling | A form whose anti-forgery token held a character outside ASCII was answered with status 500. The check existed and refused every wrong token that was ASCII | Medium | Fixed: such a token is refused with status 400 |
| D-4 | V14 Data protection (and NFR-11) | A failed sign-in was recorded with the user name as typed, which can be a password typed into the wrong field, and the record stayed until the next sign-in | Low | Fixed: a keyed digest is stored, and the daily pass removes old records |
| D-6 | V2 Validation | A request body had no size limit since Werkzeug 3.1.9, so one request could occupy 150 MiB of memory | Medium | Fixed: 64 kB, refused before the body is read |

Defect D-7, the loss of stored answers when two processes wrote at the same moment, is a defect of integrity and availability and no weakness that an attacker could use better than chance; the test report describes it.

The lesson for this document is its own first limit: a checklist cannot show the absence of flaws it does not ask about. A requirement that is "met" by a passing test is met for the inputs of that test.
