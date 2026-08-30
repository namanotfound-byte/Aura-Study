"""Shared pytest fixtures for the AuraStudy backend test suite.

Builds a fresh Flask app per test, with REQUIRE_EMAIL_VERIFICATION on and the
mailer monkeypatched so no real email is ever sent -- messages are captured
into the `outbox` fixture instead.

Runs against **both** database backends: the `app`/`client` fixtures are
parametrized over "sqlite" (a fresh temp file per test -- the local-dev
fallback) and "postgres" (a fresh, throwaway database per test on a real
PostgreSQL server spun up once for the whole session via the `pgserver`
package, which bundles real Postgres binaries -- no Homebrew/Docker/network
needed). Every test in this suite therefore runs twice unless it explicitly
opts out.

If `pgserver` (or the `postgresql-wheel` fallback) isn't installed/working
on this machine, the postgres-parametrized runs are skipped with a loud,
session-scoped warning printed once -- they are never silently downgraded to
SQLite-only in a way that could be mistaken for "Postgres was verified".

`app` and `client` are also consumed by tests/test_spotify.py.
"""
import os
import uuid
import warnings
from urllib.parse import urlparse, urlunparse

import pytest
from cryptography.fernet import Fernet

from server import config as config_module


# ---------------------------------------------------------------------------
# Real Postgres cluster (session-scoped) via pgserver
# ---------------------------------------------------------------------------

def _try_start_pgserver(pgdata_dir):
    """Returns (server, admin_uri) or (None, reason) if pgserver isn't usable."""
    try:
        import pgserver
    except ImportError:
        return None, "pgserver is not installed"
    try:
        srv = pgserver.get_server(str(pgdata_dir), cleanup_mode="delete")
        # Smoke-test the connection before trusting it for the whole session.
        import psycopg

        with psycopg.connect(srv.get_uri(), autocommit=True) as conn:
            conn.execute("SELECT 1")
        return srv, srv.get_uri()
    except Exception as exc:  # noqa: BLE001 -- report exactly why, don't guess
        return None, "pgserver failed to start a real Postgres server: {}".format(exc)


PG_UNAVAILABLE_REASON = None  # set by the pg_cluster fixture the first time it runs


@pytest.fixture(scope="session")
def pg_cluster(tmp_path_factory):
    """Starts one real PostgreSQL server (via pgserver) for the whole test
    session, or yields None if that isn't possible on this machine."""
    global PG_UNAVAILABLE_REASON
    pgdata_dir = tmp_path_factory.mktemp("pgdata")
    srv, admin_uri_or_reason = _try_start_pgserver(pgdata_dir)
    if srv is None:
        PG_UNAVAILABLE_REASON = admin_uri_or_reason
        warnings.warn(
            "\n"
            + "=" * 72
            + "\nPOSTGRES TESTING UNAVAILABLE: {}\n"
            "All postgres-parametrized tests will be SKIPPED, not silently run "
            "on SQLite. See the test summary for skip counts.\n".format(admin_uri_or_reason)
            + "=" * 72
        )
        yield None
        return

    yield {"server": srv, "admin_uri": admin_uri_or_reason}
    srv.cleanup()  # stops the server and deletes the pgdata directory


def _make_test_db_url(admin_uri: str, dbname: str) -> str:
    parsed = urlparse(admin_uri)
    return urlunparse(parsed._replace(path="/" + dbname))


@pytest.fixture
def _pg_test_database(pg_cluster):
    """Creates a throwaway Postgres database for one test and drops it
    afterward. Only used when the test is parametrized onto the postgres
    backend and a cluster is available."""
    import psycopg

    admin_uri = pg_cluster["admin_uri"]
    dbname = "test_{}".format(uuid.uuid4().hex[:16])
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute('CREATE DATABASE "{}"'.format(dbname))

    db_url = _make_test_db_url(admin_uri, dbname)
    yield db_url

    # The app no longer pools connections app-side (server/db.py opens one
    # per request and closes it at teardown), so there's nothing to release
    # here -- but a request that raised mid-test, or a fixture that grabbed
    # `get_db()` directly, could in principle still be holding one open.
    # Postgres refuses to DROP DATABASE while any session is still connected
    # to it, so terminate any stragglers before dropping, same as before.
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        conn.execute('DROP DATABASE IF EXISTS "{}"'.format(dbname))


# ---------------------------------------------------------------------------
# App / client fixtures -- parametrized over both backends
# ---------------------------------------------------------------------------

@pytest.fixture
def outbox():
    """List of dicts: {"to", "subject", "html", "text"} -- one per email that
    would have been sent, in send order."""
    return []


@pytest.fixture(autouse=True)
def _stub_hibp(monkeypatch):
    """Every password is treated as NOT breached by default, with zero
    network access -- the register/reset/change-password breached-password
    check must never hit the real HIBP API from the test suite. Tests that
    specifically exercise the breached or HIBP-unreachable paths override
    this themselves with their own monkeypatch.

    Patches BOTH `server.security.is_password_breached` (for anything that
    imports the module and calls it via attribute access) AND
    `server.auth.is_password_breached` -- auth.py does `from .security
    import is_password_breached`, which binds its own name at import time,
    so patching security.py's copy alone does NOT affect what the
    register/reset-password/change-password routes actually call. Autouse
    so this applies even to tests that don't use the `app`/`client`
    fixtures.
    """
    from server import auth, security

    monkeypatch.setattr(security, "is_password_breached", lambda pw: False)
    monkeypatch.setattr(auth, "is_password_breached", lambda pw: False)


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, pg_cluster):
    if request.param == "postgres" and pg_cluster is None:
        pytest.skip("postgres backend unavailable: {}".format(PG_UNAVAILABLE_REASON))
    return request.param


@pytest.fixture
def owner_email():
    """Empty by default -- OWNER_EMAIL unset, so /admin/spotify-requests must
    be unreachable for everyone (see server/app.py). Overridden locally (as
    a same-named fixture) by tests/test_spotify_requests.py's owner-gated
    admin-page tests, which need a real value here before the `app` fixture
    below builds the app -- that's why `app` takes this as a dependency
    instead of a test setting the env var itself: by the time a test's own
    body runs, `app` (and the Config it read once inside create_app()) has
    already been built."""
    return ""


@pytest.fixture
def app(request, tmp_path, monkeypatch, outbox, backend, owner_email):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-prod")
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:5055")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    # A real (non-empty) value here, even though `mailer.send_email` is
    # monkeypatched below and never actually touches SMTP: the
    # postgres-backend runs set DATABASE_URL, which now puts Config into
    # "production" mode (see server/config.py), and production refuses to
    # boot with SMTP_HOST unset. "smtp.test.invalid" is an RFC 2606 reserved
    # test domain -- it is never resolved or connected to in this suite.
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "")
    if owner_email:
        monkeypatch.setenv("OWNER_EMAIL", owner_email)
    else:
        monkeypatch.delenv("OWNER_EMAIL", raising=False)

    if backend == "postgres":
        db_url = request.getfixturevalue("_pg_test_database")
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.delenv("DATABASE_PATH", raising=False)
    else:
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)

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
