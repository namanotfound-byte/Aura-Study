"""Guest routing, cookie behaviour, and API access."""
import json
import os
import re
from datetime import timedelta

from server import guest as guest_module
from server.db import utcnow

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def register_verify(client, outbox, email="guest-test@example.com", password="pw123456"):
    client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    match = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1]["text"])
    resp = client.get("/verify?token={}".format(match.group(1)))
    assert resp.status_code == 302


def start_guest_trial(client):
    """Follow /app?guest=1 and return the final /app response."""
    resp = client.get("/app?guest=1", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/app")
    assert guest_module.GUEST_COOKIE_NAME in resp.headers.get("Set-Cookie", "")
    return client.get("/app")


def test_landing_includes_guest_cta(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Continue as guest" in body
    assert "/app?guest=1" in body
    assert "Completely free. No credit card. Nothing required." in body
    assert "free forever" in body.lower()


def test_guest_start_serves_app_without_login(client):
    resp = start_guest_trial(client)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="view-timer"' in body
    assert '"is_guest":true' in body
    follow_up = client.get("/app")
    assert follow_up.status_code == 200
    assert '"is_guest":true' in follow_up.get_data(as_text=True)


def test_app_without_guest_or_login_redirects(client):
    resp = client.get("/app")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login?next=%2Fapp"


def test_logged_in_app_unchanged(client, outbox):
    register_verify(client, outbox)
    resp = client.get("/app")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="view-timer"' in body
    assert '"is_guest":false' in body


def test_old_guest_cookie_still_serves_app(client, monkeypatch):
    started = utcnow() - timedelta(days=guest_module.GUEST_TRIAL_DAYS + 1)
    client.set_cookie(
        guest_module.GUEST_COOKIE_NAME,
        json.dumps({"started_at": started.isoformat()}),
    )
    resp = client.get("/app")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="view-timer"' in body
    assert '"is_guest":true' in body
    assert '"expired"' not in body


def test_guest_leaderboard_get_allowed_state_still_401(client):
    start_guest_trial(client)
    allowed = [
        ("/api/leaderboard", "GET"),
        ("/api/leaderboard/pets", "GET"),
    ]
    for path, method in allowed:
        resp = client.open(path, method=method, headers=JSON_HEADERS)
        assert resp.status_code == 200, path

    blocked = [
        ("/api/state", "GET"),
        ("/api/spotify/status", "GET"),
        ("/api/support/messages", "GET"),
    ]
    for path, method in blocked:
        resp = client.open(path, method=method, headers=JSON_HEADERS)
        assert resp.status_code == 401, path

    name_resp = client.put(
        "/api/leaderboard/name",
        data=json.dumps({"public_name": "GuestTry"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert name_resp.status_code == 401


def test_guest_cookie_is_httponly_samesite_lax(client):
    resp = client.get("/app?guest=1", follow_redirects=False)
    cookie_header = resp.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie_header
    assert "SameSite=Lax" in cookie_header
    assert "Path=/" in cookie_header


def test_logged_in_user_ignores_guest_query(client, outbox):
    register_verify(client, outbox, email="logged-in-guest@example.com")
    resp = client.get("/app?guest=1", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/app")
    assert "Set-Cookie" not in resp.headers or guest_module.GUEST_COOKIE_NAME not in resp.headers.get("Set-Cookie", "")


def test_login_and_register_include_guest_cta(client):
    for path in ("/login", "/register"):
        resp = client.get(path)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Continue as guest" in body
        assert "/app?guest=1" in body


def test_guest_app_shows_spotify_locked_panel_markup(client):
    resp = start_guest_trial(client)
    body = resp.get_data(as_text=True)
    assert "initGuestExperience" in body
    assert "showGuestLockedPanel" in _read("static", "guest.js")
    assert 'id="view-spotify"' in body
    assert "guest-locked-panel" in _read("static", "guest.js")
    assert "Log in or sign up to connect Spotify" in _read("static", "guest.js")
