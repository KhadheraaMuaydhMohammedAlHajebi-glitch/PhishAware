"""Static-file fingerprints: long-lived browser caching without stale files (NFR-01).

Every static URL that the templates generate carries a short hash of the file's
content, for example /static/css/style.css?v=3f2a9c1b7e. A response to such a
URL may be kept by the browser for a year, because a file that changes gets a
new URL. A request without the current fingerprint is answered with "no-cache",
so an outdated file can never be pinned in a cache.

Without this, Flask asks browsers to revalidate each static file on every page
view. On a slow mobile connection those extra round trips cost more than the
page itself (see docs/evaluation.md).
"""

import hashlib
from pathlib import Path

from flask import current_app, request
from werkzeug.security import safe_join

ONE_YEAR = 365 * 24 * 60 * 60
FRESH = f"public, max-age={ONE_YEAR}, immutable"
REVALIDATE = "no-cache"
_fingerprints = {}   # file path -> (modification time, size, fingerprint)


def fingerprint(filename):
    """Ten hex digits of the file's SHA-256, or None if there is no such static file."""
    path = safe_join(current_app.static_folder, filename)   # None for "../" tricks
    if path is None:
        return None
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    cached = _fingerprints.get(path)
    if cached is None or cached[:2] != (stat.st_mtime_ns, stat.st_size):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:10]
        cached = _fingerprints[path] = (stat.st_mtime_ns, stat.st_size, digest)
    return cached[2]


def add_fingerprint(endpoint, values):
    """url_defaults hook: url_for('static', filename=...) gains the v parameter."""
    if endpoint == "static" and "filename" in values and "v" not in values:
        version = fingerprint(values["filename"])
        if version:
            values["v"] = version


def cache_static_files(response):
    """after_request hook: a year for the current fingerprint, revalidation otherwise."""
    if request.endpoint == "static" and response.status_code in (200, 304):
        current = fingerprint(request.view_args["filename"])
        fresh = current is not None and request.args.get("v") == current
        response.headers["Cache-Control"] = FRESH if fresh else REVALIDATE
    return response


def init_app(app):
    app.url_defaults(add_fingerprint)
    app.after_request(cache_static_files)
