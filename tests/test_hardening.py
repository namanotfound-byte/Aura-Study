"""Tests for SPEC-PHASE3.md PART B: proxy trust / anti-spoofing, security
headers, and production-boot safety (server/hardening.py, server/config.py).

Note: these tests deliberately avoid the shared `app`/`client` fixtures for
the production-boot-safety cases below, since those fixtures always set a
full set of valid secrets. They instead build a `Config` directly with a
fully controlled environment.
"""
import json
import re

import pytest
from cryptography.fernet import Fernet

from server import config as config_module

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}

_VALID_FERNET_KEY = Fernet.generate_key().decode()


def _register_verify_login(client, outbox, email="hardening-user@example.com", password="pw123456"):
    client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    match = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1]["text"])
    client.get("/verify?token={}".format(match.group(1)))
    resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Point Config's dotenv loading at an empty, nonexistent file so these
    tests are never affected by (or able to affect) the real project `.env`
    -- and clear every env var Config reads, so each test starts from a
    truly blank slate rather than whatever happens to be in this process's
    environment."""
    monkeypatch.setattr(config_module, "ENV_PATH", str(tmp_path / "does-not-exist.env"))
    for var in (
        "SECRET_KEY", "APP_BASE_URL", "PORT", "DATABASE_PATH", "DATABASE_URL",
        "ENVIRONMENT", "TOKEN_ENC_KEY", "BREVO_API_KEY", "SMTP_HOST", "SMTP_PORT",
        "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_USE_TLS", "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET", "REQUIRE_EMAIL_VERIFICATION", "MAX_CONTENT_LENGTH",
    ):
        monkeypatch.delenv(var, raising=False)
    config_module.get_config.cache_clear()
    yield
    config_module.get_config.cache_clear()


# --------------------------------------------------------- production boot

def test_production_refuses_to_boot_without_secret_key(isolated_env, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    with pytest.raises(config_module.ProductionConfigError, match="SECRET_KEY"):
        config_module.Config()


def test_production_refuses_placeholder_secret_key(isolated_env, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("SECRET_KEY", "changeme")
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    with pytest.raises(config_module.ProductionConfigError, match="SECRET_KEY"):
        config_module.Config()


def test_production_refuses_to_boot_without_token_enc_key(isolated_env, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("SECRET_KEY", "a-real-64-char-hex-secret-0123456789abcdef0123456789abcdef01")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    with pytest.raises(config_module.ProductionConfigError, match="TOKEN_ENC_KEY"):
        config_module.Config()


def test_production_refuses_to_boot_without_smtp_host(isolated_env, monkeypatch):
    # Neither BREVO_API_KEY nor SMTP_HOST is set (isolated_env clears both) --
    # the boot check must name both options in its error message.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("SECRET_KEY", "a-real-64-char-hex-secret-0123456789abcdef0123456789abcdef01")
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    with pytest.raises(config_module.ProductionConfigError, match="BREVO_API_KEY"):
        config_module.Config()
    with pytest.raises(config_module.ProductionConfigError, match="SMTP_HOST"):
        config_module.Config()


def test_production_boots_with_only_brevo_api_key_set(isolated_env, monkeypatch):
    """The mail-transport boot check must be satisfied by BREVO_API_KEY
    alone -- Render's free tier can never set SMTP_HOST (SMTP is blocked
    outbound), so requiring it too would make the app unbootable there."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("SECRET_KEY", "a-real-64-char-hex-secret-0123456789abcdef0123456789abcdef01")
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test-key")
    cfg = config_module.Config()
    assert cfg.brevo_api_key == "xkeysib-test-key"
    assert cfg.smtp_host == ""


def test_production_forces_email_verification_on(isolated_env, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("SECRET_KEY", "a-real-64-char-hex-secret-0123456789abcdef0123456789abcdef01")
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    cfg = config_module.Config()
    assert cfg.require_email_verification is True


def test_explicit_environment_production_also_triggers_the_gate(isolated_env, monkeypatch):
    """The gate isn't only DATABASE_URL-based -- ENVIRONMENT=production alone
    (e.g. a deploy that still uses SQLite) must trigger the same checks."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(config_module.ProductionConfigError, match="SECRET_KEY"):
        config_module.Config()


def test_invalid_fernet_key_rejected_even_outside_production(isolated_env, monkeypatch):
    """Garbage TOKEN_ENC_KEY must fail fast at boot, not fail mysteriously
    the first time Spotify token encryption is attempted -- checked
    regardless of production/dev, since it's never safe."""
    monkeypatch.setenv("TOKEN_ENC_KEY", "not-a-real-fernet-key")
    with pytest.raises(config_module.ProductionConfigError, match="Fernet"):
        config_module.Config()


def test_dev_mode_still_auto_generates_ephemeral_secret_key(isolated_env):
    """Local dev (no DATABASE_URL, no ENVIRONMENT=production) keeps the
    original zero-config behaviour: an ephemeral SECRET_KEY with a warning,
    not a hard failure."""
    cfg = config_module.Config()
    assert cfg.is_production is False
    assert len(cfg.secret_key) >= 32


def test_max_content_length_defaults_to_about_2mb(isolated_env, monkeypatch):
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    cfg = config_module.Config()
    assert cfg.max_content_length == 2 * 1024 * 1024


# ----------------------------------------------------------- request cap

def test_oversized_request_body_rejected(client, outbox):
    """MAX_CONTENT_LENGTH is enforced by Flask/Werkzeug before the view body
    is read -- distinct from (and larger than) state.py's own 1 MB payload
    check, which this must not shadow for a merely-1MB-ish request (see
    tests/test_state.py's own 413 test for that boundary). Requires an
    authenticated request: an unauthenticated one is rejected by
    @login_required before the body is ever read, which would trivially
    "pass" without proving anything about MAX_CONTENT_LENGTH."""
    _register_verify_login(client, outbox)
    huge = "x" * (3 * 1024 * 1024)
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"blob": huge}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 413


# --------------------------------------------------------- security headers

def test_security_headers_present_on_every_response(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "https://cdn.jsdelivr.net" in csp  # Chart.js
    assert "https://unpkg.com" in csp  # Lucide
    assert "https://sdk.scdn.co" in csp  # Spotify Web Playback SDK
    assert "https://i.scdn.co" in csp  # Spotify art
    assert "https://open.spotify.com" in csp  # Spotify embed
    assert "https://api.spotify.com" in csp  # Spotify API

    permissions_policy = resp.headers["Permissions-Policy"]
    # These three must NOT be restricted -- Focus Mode (picture-in-picture,
    # screen-wake-lock) and the Spotify embed (autoplay) need them.
    assert "picture-in-picture=()" not in permissions_policy
    assert "screen-wake-lock=()" not in permissions_policy
    assert "autoplay=()" not in permissions_policy
    # Actually unused features should be denied.
    assert "geolocation=()" in permissions_policy
    assert "camera=()" in permissions_policy


def test_hsts_present_when_base_url_is_https(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "ENV_PATH", str(tmp_path / "does-not-exist.env"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-prod")
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    monkeypatch.setenv("APP_BASE_URL", "https://aurastudy.example.com")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "hsts_test.db"))
    config_module.get_config.cache_clear()

    from server.app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as https_client:
        resp = https_client.get("/healthz")
        assert resp.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"

    config_module.get_config.cache_clear()


def test_hsts_absent_over_plain_http(client):
    """Local dev's APP_BASE_URL is http://127.0.0.1:5055 (see conftest.py) --
    HSTS should not be advertised, since sending it over plain HTTP is at
    best a no-op and at worst misleading."""
    resp = client.get("/healthz")
    assert "Strict-Transport-Security" not in resp.headers


def test_session_cookie_is_secure_when_base_url_is_https(monkeypatch, tmp_path):
    """Re-verifies the pre-existing conditional-Secure-cookie behaviour
    (server/security.py:set_session_cookie) still holds after the Postgres
    migration and the ProxyFix change."""
    monkeypatch.setattr(config_module, "ENV_PATH", str(tmp_path / "does-not-exist.env"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-prod")
    monkeypatch.setenv("TOKEN_ENC_KEY", _VALID_FERNET_KEY)
    monkeypatch.setenv("APP_BASE_URL", "https://aurastudy.example.com")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "secure_cookie_test.db"))
    config_module.get_config.cache_clear()

    from server import mailer
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: True)

    from server.app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as https_client:
        resp = https_client.post(
            "/api/auth/register",
            data=json.dumps({"email": "secure@example.com", "password": "pw123456"}),
            content_type="application/json",
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 202
        resp = https_client.post(
            "/api/auth/login",
            data=json.dumps({"email": "secure@example.com", "password": "pw123456"}),
            content_type="application/json",
            headers=JSON_HEADERS,
        )
        assert resp.status_code == 200
        set_cookie_headers = resp.headers.getlist("Set-Cookie")
        session_cookie = next(h for h in set_cookie_headers if h.startswith("aurastudy_session="))
        assert "Secure" in session_cookie

    config_module.get_config.cache_clear()


# --------------------------------------------- X-Forwarded-For anti-spoofing

def _register(client, email, xff, real_remote_addr="10.10.10.10"):
    return client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": "pw123456"}),
        content_type="application/json",
        headers=JSON_HEADERS,
        environ_overrides={"REMOTE_ADDR": real_remote_addr, "HTTP_X_FORWARDED_FOR": xff},
    )


def test_forged_x_forwarded_for_cannot_evade_the_ip_rate_limit(client):
    """Proves ProxyFix(x_for=1) resolves the client IP to the single hop
    nearest the app -- the one a real reverse proxy (Render) itself appends
    -- and ignores anything a client prepends. A naive fix (e.g. always
    taking the *first* X-Forwarded-For entry, or trusting the header
    verbatim) would let an attacker dodge the register endpoint's
    5-per-hour-per-IP limiter forever by rotating a fake leading entry on
    every request. This proves that doesn't work: five requests, each with a
    *different* forged first hop but the same real last hop, still hit the
    limiter on the 6th.
    """
    real_hop = "203.0.113.9"  # what the trusted proxy actually appended
    for i in range(5):
        forged_prefix = "10.0.0.{}".format(i)  # attacker-controlled, different every time
        xff = "{}, {}".format(forged_prefix, real_hop)
        resp = _register(client, "spoof{}@example.com".format(i), xff)
        assert resp.status_code == 202, resp.get_json()

    # 6th request: yet another forged leading hop, same real last hop --
    # must still be blocked.
    xff = "10.0.0.99, {}".format(real_hop)
    resp = _register(client, "spoof99@example.com", xff)
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"

    # Sanity check the mechanism isn't just "block everything after 5
    # requests total": a genuinely different real last hop (a different
    # real client) is unaffected.
    xff_other_client = "10.0.0.1, 198.51.100.42"
    resp = _register(client, "someone-else@example.com", xff_other_client)
    assert resp.status_code == 202


def test_x_forwarded_for_without_a_real_proxy_hop_is_not_trusted_blindly(client):
    """A single-entry X-Forwarded-For (no real proxy in front, or a proxy
    that doesn't append) is still taken as *the* hop by ProxyFix(x_for=1) --
    this is a known, documented limitation of trusting exactly N hops rather
    than an IP allowlist. Recorded here as an explicit regression marker for
    that residual assumption (see server/hardening.py's apply_proxy_fix
    docstring and the hardening report): it only holds because the app
    process is not reachable except through Render's own proxy.
    """
    for i in range(5):
        resp = _register(
            client,
            "onehop{}@example.com".format(i),
            xff="9.9.9.9",  # single entry -- ProxyFix takes it as-is
            real_remote_addr="10.10.10.10",
        )
        assert resp.status_code == 202
    resp = _register(client, "onehop-blocked@example.com", xff="9.9.9.9")
    assert resp.status_code == 429
