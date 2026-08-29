"""Shared pytest fixtures for the AuraStudy backend test suite.

Builds a fresh Flask app per test against a temp SQLite file, with
REQUIRE_EMAIL_VERIFICATION on and the mailer monkeypatched so no real email
is ever sent -- messages are captured into the `outbox` fixture instead.

`app` and `client` are also consumed by Agent B's tests/test_spotify.py.
"""
import pytest
from cryptography.fernet import Fernet

from server import config as config_module


@pytest.fixture
def outbox():
    """List of dicts: {"to", "subject", "html", "text"} -- one per email that
    would have been sent, in send order."""
    return []


@pytest.fixture
def app(tmp_path, monkeypatch, outbox):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-prod")
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:5055")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "")

    # Config is a cached singleton (see server/config.py); force a fresh read
    # of the env vars we just set for this test.
    config_module.get_config.cache_clear()

    from server import mailer

    def fake_send_email(to, subject, html_body, text_body):
        outbox.append({"to": to, "subject": subject, "html": html_body, "text": text_body})
        return True

    monkeypatch.setattr(mailer, "send_email", fake_send_email)

    from server.app import create_app

    flask_app = create_app()
    flask_app.config.update(TESTING=True)

    yield flask_app

    config_module.get_config.cache_clear()


@pytest.fixture
def client(app):
    return app.test_client()
