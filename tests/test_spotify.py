"""Tests for server/spotify.py.

Uses the `client`/`app` fixtures from tests/conftest.py (Agent A). All calls
to the real Spotify API go through `requests`, which is mocked/monkeypatched
in every test here -- the real network is never hit.
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from server import spotify
from server.db import get_db, utcnow
from server.security import encrypt_token


JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def register_and_login(client, email="spotify-user@example.com", password="hunter2pw", verify=True):
    client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    if verify:
        with client.application.app_context():
            db = get_db()
            db.execute("UPDATE users SET is_verified = 1 WHERE email = ?", (email,))
            db.commit()
            user_id = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    login_resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert login_resp.status_code == 200, login_resp.get_data(as_text=True)
    return user_id


def connect_fake_account(app, user_id, expires_in=3600, product="premium"):
    """Insert a spotify_accounts row directly, as if OAuth already happened."""
    with app.app_context():
        db = get_db()
        db.execute(
            """
            INSERT INTO spotify_accounts
                (user_id, spotify_user_id, display_name, product, access_token,
                 refresh_token, expires_at, scopes, connected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                spotify_user_id=excluded.spotify_user_id,
                display_name=excluded.display_name,
                product=excluded.product,
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at,
                scopes=excluded.scopes,
                connected_at=excluded.connected_at
            """,
            (
                user_id,
                "spotify-uid-123",
                "Test Listener",
                product,
                encrypt_token("fake-access-token"),
                encrypt_token("fake-refresh-token"),
                (utcnow() + timedelta(seconds=expires_in)).isoformat(),
                spotify.SCOPES,
                utcnow().isoformat(),
            ),
        )
        db.commit()


def fake_response(status_code=200, json_data=None, content=b"{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content if json_data is None else b"non-empty"
    resp.json = MagicMock(return_value=json_data if json_data is not None else {})
    return resp


# ---------------------------------------------------------------------------
# status / configuration
# ---------------------------------------------------------------------------

def test_status_unconfigured(client, app):
    register_and_login(client)
    app.config["_TEST_CFG"] = None  # no-op, cfg comes from get_config()
    resp = client.get("/api/spotify/status", headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["configured"] is False
    assert data["connected"] is False
    assert data["premium"] is False


def test_status_requires_auth(client):
    resp = client.get("/api/spotify/status", headers=JSON_HEADERS)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthenticated"


# ---------------------------------------------------------------------------
# auth required across the board
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/spotify/status"),
        ("GET", "/api/spotify/login"),
        ("POST", "/api/spotify/disconnect"),
        ("GET", "/api/spotify/token"),
        ("GET", "/api/spotify/playlists"),
        ("GET", "/api/spotify/now-playing"),
        ("PUT", "/api/spotify/play"),
        ("PUT", "/api/spotify/pause"),
        ("POST", "/api/spotify/next"),
        ("POST", "/api/spotify/previous"),
        ("PUT", "/api/spotify/volume"),
        ("GET", "/api/spotify/devices"),
    ],
)
def test_endpoints_require_login(client, method, path):
    resp = client.open(path, method=method, headers=JSON_HEADERS)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthenticated"


# ---------------------------------------------------------------------------
# not-connected -> 409 spotify_not_connected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/spotify/token"),
        ("GET", "/api/spotify/playlists"),
        ("GET", "/api/spotify/now-playing"),
        ("PUT", "/api/spotify/play"),
        ("PUT", "/api/spotify/pause"),
        ("POST", "/api/spotify/next"),
        ("POST", "/api/spotify/previous"),
        ("PUT", "/api/spotify/volume"),
        ("GET", "/api/spotify/devices"),
    ],
)
def test_endpoints_409_when_not_connected(client, app, monkeypatch, method, path):
    monkeypatch.setattr(spotify, "_configured", lambda cfg: True)
    register_and_login(client)
    body = json.dumps({"percent": 50}) if path.endswith("volume") else None
    kwargs = {"headers": JSON_HEADERS}
    if body:
        kwargs["data"] = body
        kwargs["content_type"] = "application/json"
    resp = client.open(path, method=method, **kwargs)
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "spotify_not_connected"


# ---------------------------------------------------------------------------
# get_valid_access_token: refresh behaviour
# ---------------------------------------------------------------------------

def test_get_valid_access_token_returns_cached_when_fresh(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, expires_in=3600)

    post_mock = MagicMock()
    monkeypatch.setattr(spotify.requests, "post", post_mock)

    with app.app_context():
        token = spotify.get_valid_access_token(user_id)
    assert token == "fake-access-token"
    post_mock.assert_not_called()


def test_get_valid_access_token_refreshes_when_expired(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, expires_in=-10)  # already expired

    refreshed = fake_response(
        200, {"access_token": "new-access-token", "expires_in": 3600, "refresh_token": "rotated-refresh"}
    )
    post_mock = MagicMock(return_value=refreshed)
    monkeypatch.setattr(spotify.requests, "post", post_mock)

    with app.app_context():
        token = spotify.get_valid_access_token(user_id)
        assert token == "new-access-token"
        post_mock.assert_called_once()

        db = get_db()
        row = db.execute("SELECT * FROM spotify_accounts WHERE user_id=?", (user_id,)).fetchone()
        from server.security import decrypt_token

        assert decrypt_token(row["access_token"]) == "new-access-token"
        assert decrypt_token(row["refresh_token"]) == "rotated-refresh"


def test_get_valid_access_token_invalid_grant_disconnects(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, expires_in=-10)

    bad_resp = fake_response(400, {"error": "invalid_grant"})
    monkeypatch.setattr(spotify.requests, "post", MagicMock(return_value=bad_resp))

    with app.app_context():
        with pytest.raises(spotify.SpotifyNotConnected):
            spotify.get_valid_access_token(user_id)
        db = get_db()
        row = db.execute("SELECT * FROM spotify_accounts WHERE user_id=?", (user_id,)).fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# premium_required / no_active_device mapping
# ---------------------------------------------------------------------------

def test_play_returns_premium_required(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, product="free")

    denied = fake_response(403, {"error": {"status": 403, "message": "Player command failed", "reason": "PREMIUM_REQUIRED"}})
    monkeypatch.setattr(spotify.requests, "request", MagicMock(return_value=denied))

    resp = client.put("/api/spotify/play", data=json.dumps({}), content_type="application/json", headers=JSON_HEADERS)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "premium_required"


def test_play_returns_no_active_device(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, product="premium")

    no_device = fake_response(404, {"error": {"status": 404, "message": "No active device", "reason": "NO_ACTIVE_DEVICE"}})
    monkeypatch.setattr(spotify.requests, "request", MagicMock(return_value=no_device))

    resp = client.put("/api/spotify/play", data=json.dumps({}), content_type="application/json", headers=JSON_HEADERS)
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "no_active_device"


def test_pause_maps_403_to_premium_required(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, product="free")

    denied = fake_response(403, {"error": {"status": 403, "message": "forbidden", "reason": ""}})
    monkeypatch.setattr(spotify.requests, "request", MagicMock(return_value=denied))

    resp = client.put("/api/spotify/pause", headers=JSON_HEADERS)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "premium_required"


# ---------------------------------------------------------------------------
# happy paths, mocked
# ---------------------------------------------------------------------------

def test_now_playing_happy_path(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, product="premium")

    payload = {
        "is_playing": True,
        "progress_ms": 1000,
        "item": {
            "name": "Study Beats",
            "artists": [{"name": "Lofi Girl"}],
            "album": {"name": "Focus", "images": [{"url": "https://img/x.png"}]},
            "duration_ms": 200000,
            "uri": "spotify:track:abc",
        },
        "device": {"id": "dev1", "name": "Laptop", "is_active": True},
    }
    ok = fake_response(200, payload)
    monkeypatch.setattr(spotify.requests, "request", MagicMock(return_value=ok))

    resp = client.get("/api/spotify/now-playing", headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["is_playing"] is True
    assert data["track"]["name"] == "Study Beats"
    assert data["track"]["artists"] == "Lofi Girl"
    assert data["device"]["id"] == "dev1"


def test_now_playing_no_content_when_nothing_playing(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, product="premium")

    empty = fake_response(204, content=b"")
    monkeypatch.setattr(spotify.requests, "request", MagicMock(return_value=empty))

    resp = client.get("/api/spotify/now-playing", headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["is_playing"] is False
    assert data["track"] is None


def test_playlists_happy_path(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, product="premium")

    payload = {
        "items": [
            {"id": "pl1", "name": "Deep Focus", "images": [{"url": "https://img/pl1.png"}], "tracks": {"total": 42}, "uri": "spotify:playlist:pl1"}
        ]
    }
    ok = fake_response(200, payload)
    monkeypatch.setattr(spotify.requests, "request", MagicMock(return_value=ok))

    resp = client.get("/api/spotify/playlists", headers=JSON_HEADERS)
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    assert items[0]["id"] == "pl1"
    assert items[0]["tracks"] == 42
    assert items[0]["image"] == "https://img/pl1.png"


def test_disconnect(client, app):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id)

    resp = client.post("/api/spotify/disconnect", headers=JSON_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    with app.app_context():
        db = get_db()
        row = db.execute("SELECT * FROM spotify_accounts WHERE user_id=?", (user_id,)).fetchone()
        assert row is None


def test_disconnect_requires_csrf_header(client, app):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id)
    resp = client.post("/api/spotify/disconnect")  # no X-Requested-With
    assert resp.status_code == 403


def test_token_endpoint_never_returns_refresh_token(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, expires_in=3600)

    resp = client.get("/api/spotify/token", headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["access_token"] == "fake-access-token"
    assert "refresh_token" not in data


def test_volume_validation_error(client, app, monkeypatch):
    user_id = register_and_login(client)
    connect_fake_account(app, user_id, product="premium")

    resp = client.put(
        "/api/spotify/volume",
        data=json.dumps({"percent": 150}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"
