"""WSGI entry point for a production server:  gunicorn --config gunicorn.conf.py wsgi:app"""

from src.app import create_app

app = create_app()
