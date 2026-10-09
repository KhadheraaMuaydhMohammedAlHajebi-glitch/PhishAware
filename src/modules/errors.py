"""Error pages in the application's own words (M8, NFR-02, NFR-09).

Whatever goes wrong, the answer is a page of PhishAware: the usual layout, a
plain sentence that says what happened and whether anything was lost, and one
way on.

Flask gives such a page only to the status codes that have a handler of their
own. Every other error is answered with the framework's page: unstyled, with
no way back, and in words such as "The requested URL was not found on the
server". One handler for every HTTP error closes that gap, so a code that
nobody thought of is covered as well.

The wording below is used unless the code that raised the error described it
itself, as the input checks do ("That answer is not one of the allowed
choices."). The framework's own description is never shown, and neither is any
detail of an unexpected failure.
"""

from flask import current_app, make_response, render_template
from markupsafe import escape
from werkzeug.exceptions import HTTPException

# status code: (title, what happened and what the reader can do)
PAGES = {
    400: ("Request not accepted",
          "The form could not be accepted, so nothing was changed. Go back, reload the "
          "page, and try again."),
    403: ("Not available yet",
          "This page is not available to you at the moment."),
    404: ("Page not found",
          "There is no page at this address. The address may have been mistyped, or a "
          "link may be out of date. Answers you have given are saved."),
    405: ("Action not allowed",
          "This address cannot be used in that way. Use the buttons and links on the "
          "pages instead. Answers you have given are saved."),
    413: ("Too much data",
          "The form that arrived is larger than any form of PhishAware, so it was not "
          "read. Nothing was changed."),
    500: ("Something went wrong",
          "An unexpected problem occurred. Your answers so far are saved."),
}
OTHER_CLIENT_ERROR = ("Request not accepted",
                      "The request could not be carried out, and nothing was changed.")

# Sent when the usual page cannot be built. The layout shows the session's
# identifier, which it reads from the database; if the database is what failed,
# the error page would fail with it, and the visitor would get the web server's
# bare "Internal Server Error".
PLAIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} | PhishAware</title>
  <link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
  <main id="main" class="page">
    <section class="sheet sheet--narrow">
      <h1>{title}</h1>
      <p class="lead">{message}</p>
      <div class="actions"><a class="btn btn--primary" href="/">Go to the start page</a></div>
      <p class="muted error-status">If you report this, mention status {code}.</p>
    </section>
  </main>
</body>
</html>
"""


def describe(error):
    """(status code, title, message) for an HTTP error."""
    code = error.code or 500
    if code >= 500:
        title, message = PAGES[500]      # never a detail of the failure
        return code, title, message
    title, message = PAGES.get(code, OTHER_CLIENT_ERROR)
    # An error class of the application may name itself (security.SessionEnded).
    title = getattr(error, "title", title)
    # A description set where the error was raised belongs to the instance. The
    # default of the class is the framework's sentence.
    return code, title, vars(error).get("description") or message


def handle_http_error(error):
    code, title, message = describe(error)
    try:
        page = render_template("error.html", code=code, title=title, message=message)
    except Exception:
        current_app.logger.exception("The error page could not be built; a plain page was sent.")
        page = PLAIN_PAGE.format(title=escape(title), message=escape(message), code=code)
    response = make_response(page, code)
    allowed = getattr(error, "valid_methods", None)
    if allowed:
        response.headers["Allow"] = ", ".join(sorted(allowed))   # RFC 9110 asks for it with a 405
    return response


def init_app(app):
    # An exception that is not an HTTP error reaches the same handler: Flask
    # wraps it in InternalServerError, which is one.
    app.register_error_handler(HTTPException, handle_http_error)
