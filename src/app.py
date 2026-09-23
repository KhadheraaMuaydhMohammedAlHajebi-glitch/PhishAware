"""PhishAware Flask application factory (skeleton – implementation starts Week 4)."""
from flask import Flask

def create_app():
    app = Flask(__name__)
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", DEBUG=False)
    return app
