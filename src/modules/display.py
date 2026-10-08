"""Presentation helpers for the addresses shown in scenarios (M4, NFR-04).

Reading a domain exactly is the skill PhishAware trains, so the way an address
wraps on a narrow screen matters. Left to itself, a browser breaks a long
address wherever it runs out of room, and it treats a hyphen as a good place to
break. On a 360 px phone the look-alike address
https://learn-northbridge.example/login was shown as "https://learn-" above
"northbridge.example": the genuine-looking domain stood on a line of its own,
which distorts the cue that the item tests.

These helpers split an address into parts. The scenario template marks every
label of the host as unbreakable and allows a break only before a dot, after
the "@", and around the host, so a host stays on one line whenever it fits.
Sentences that mention an address (lesson examples, feedback, message bodies)
get the same treatment through the "addresses" filter.
"""

import re

from markupsafe import Markup, escape

URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/?#\s]+)(\S*)")
ADDRESS = re.compile(r"([^@\s]+@)([^@\s]+)")
# An email address or a bare host inside a sentence: an optional local part with
# its "@", then labels joined by dots, ending in a top-level name of letters.
# The look-behind keeps the pattern from starting in the middle of a word, so a
# file name such as Invoice_88213.zip is left alone.
IN_TEXT = re.compile(
    r"(?<![\w.@%+-])(?:[A-Za-z0-9._%+-]+@)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
    r"(?![\w-])")
LABEL = Markup('<span class="host__label">{}</span>')
HOST = Markup('<span class="host">{}</span>')
BREAK = Markup("<wbr>")


def host_labels(host):
    """Labels of a host with their dots: ['learn-northbridge', '.example']."""
    first, *rest = host.split(".")
    return [first] + ["." + label for label in rest]


def url_parts(value):
    """Scheme, host labels, and the remainder of a URL; None if value is not a URL."""
    match = URL.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    return {"scheme": match.group(1), "labels": host_labels(match.group(2)),
            "rest": match.group(3)}


def address_parts(value):
    """Local part (with its "@") and host labels of an email address, or None."""
    match = ADDRESS.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    return {"local": match.group(1), "labels": host_labels(match.group(2))}


def protect_addresses(text):
    """Escape a sentence and make every address or host in it unbreakable inside a label.

    "grade-portal.test" becomes one unit that may break only before its dot, so
    a narrow column can never show "grade-" at the end of one line and
    "portal.test" on the next. The visible text is unchanged.
    """
    pieces, position = [], 0
    for match in IN_TEXT.finditer(text):
        pieces.append(escape(text[position:match.start()]))
        local, at, host = match.group().rpartition("@")
        if at:
            pieces.append(LABEL.format(local + at) + BREAK)
        pieces.append(HOST.format(BREAK.join(LABEL.format(label) for label in host_labels(host))))
        position = match.end()
    pieces.append(escape(text[position:]))
    return Markup("").join(pieces)


def init_app(app):
    app.add_template_filter(url_parts)
    app.add_template_filter(address_parts)
    app.add_template_filter(protect_addresses, "addresses")
