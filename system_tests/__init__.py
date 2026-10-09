"""System and acceptance tests: black-box tests of a running PhishAware instance.

The tests in tests/ run inside the Python process and look at one module, or at a
few modules together. The tests in this package know nothing about the code. They
use the deployed system the way a participant, a researcher, an operator, or an
attacker would: over HTTPS, through a real browser, and through the documented
command-line tools. docs/test-plan.md lists every case.

    python -m system_tests.run --base-url https://localhost --cacert caddy-root.crt \\
        --admin-user evaluator --admin-password "$PASSWORD"

Every case leaves scripted records behind, so run the tests against a test
instance and never against the database of a live study.
"""
