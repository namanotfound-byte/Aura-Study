"""Spotify OAuth (Authorization Code + PKCE) and playback-control API.

Blueprint 'spotify', mounted by server/app.py at url_prefix="/api/spotify".
See SPEC.md sections 6 and 8 for the exact contract this file implements.
"""
import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from flask import Blueprint, g, jsonify, redirect, request, session

from .config import get_config
from .db import get_db, utcnow, utcnow_iso, parse_iso
from .security import login_required, json_error, encrypt_token, decrypt_token, require_csrf

bp = Blueprint("spotify", __name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = (
    "user-read-private user-read-email user-read-playback-state "
    "user-modify-playback-state user-read-currently-playing "
    "playlist-read-private playlist-read-collaborative streaming"
)
REQUEST_TIMEOUT = 10  # seconds; never let a Spotify outage hang the app


class SpotifyNotConnected(Exception):
    """Raised when the user has no linked Spotify account (or it was revoked)."""


class SpotifyApiError(Exception):
    """Raised when talking to Spotify itself fails (network error, bad response)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _configured(cfg):
    return bool(cfg.spotify_client_id and cfg.spotify_client_secret)


def _redirect_uri(cfg):
    # Must match verbatim what is registered in the Spotify dashboard.
    return cfg.app_base_url.rstrip("/") + "/api/spotify/callback"


def _iso(dt):
    return dt.isoformat()


def _gen_pkce_verifier():
    # RFC 7636: 43-128 chars from [A-Za-z0-9-._~]. token_urlsafe(64) yields
    # ~86 url-safe chars (no '.'/'~' but that's fine, they're optional chars
    # in the allowed set, not required ones).
    return secrets.token_urlsafe(64)


def _pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _first_image(obj):
    images = obj.get("images")
    if not images and obj.get("album"):
        images = obj["album"].get("images")
    return images[0]["url"] if images else None


def _spotify_api(method, path, access_token, **kwargs):
    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = "Bearer " + access_token
    return requests.request(
        method, API_BASE + path, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
    )


def _map_spotify_error(resp):
    try:
        body = resp.json()
    except ValueError:
        body = {}
    err = (body.get("error") or {}) if isinstance(body.get("error"), dict) else {}
    reason = err.get("reason") or ""
    message = err.get("message") or "Spotify request failed."
    if resp.status_code == 403 or reason == "PREMIUM_REQUIRED":
        return json_error("premium_required", "Spotify playback control requires Premium.", 403)
    if resp.status_code == 404 or reason == "NO_ACTIVE_DEVICE":
        return json_error("no_active_device", "No active Spotify device found.", 404)
    return json_error("spotify_error", message, 502)


def get_valid_access_token(user_id):
    """Return a live Spotify access token for user_id, refreshing if needed.

    Refreshes transparently when the stored token expires within 60s, persists
    the refreshed (possibly rotated) tokens, and deletes the account row on
    invalid_grant so the caller can surface spotify_not_connected.
    """
    cfg = get_config()
    db = get_db()
    row = db.execute(
        "SELECT * FROM spotify_accounts WHERE user_id = %s", (user_id,)
    ).fetchone()
    if not row or not row["refresh_token"]:
        raise SpotifyNotConnected()

    expires_at = parse_iso(row["expires_at"]) if row["expires_at"] else None
    if expires_at is not None and (expires_at - utcnow()).total_seconds() > 60:
        return decrypt_token(row["access_token"])

    refresh_token = decrypt_token(row["refresh_token"])
    try:
        resp = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(cfg.spotify_client_id, cfg.spotify_client_secret),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        raise SpotifyApiError("network_error")

    if resp.status_code != 200:
        try:
            err_code = resp.json().get("error")
        except ValueError:
            err_code = None
        if err_code == "invalid_grant":
            db.execute("DELETE FROM spotify_accounts WHERE user_id = %s", (user_id,))
            db.commit()
            raise SpotifyNotConnected()
        raise SpotifyApiError("token_refresh_failed")

    data = resp.json()
    access_token = data["access_token"]
    # Spotify may or may not rotate the refresh token on refresh; keep the old
    # one if a new one wasn't issued.
    new_refresh_token = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 3600)
    new_expires_at = _iso(utcnow() + timedelta(seconds=expires_in))

    db.execute(
        "UPDATE spotify_accounts SET access_token=%s, refresh_token=%s, expires_at=%s WHERE user_id=%s",
        (encrypt_token(access_token), encrypt_token(new_refresh_token), new_expires_at, user_id),
    )
    db.commit()
    return access_token


def _get_token_or_error():
    """Convenience for routes: returns (token, None) or (None, error_response)."""
    try:
        return get_valid_access_token(g.user["id"]), None
    except SpotifyNotConnected:
        return None, json_error("spotify_not_connected", "Connect your Spotify account first.", 409)
    except SpotifyApiError:
        return None, json_error("spotify_error", "Could not reach Spotify.", 502)


# ---------------------------------------------------------------------------
# Status / account
# ---------------------------------------------------------------------------

@bp.route("/status", methods=["GET"])
@login_required
def status():
    cfg = get_config()
    if not _configured(cfg):
        return jsonify(
            {"configured": False, "connected": False, "display_name": None, "product": None, "premium": False}
        )
    db = get_db()
    row = db.execute(
        "SELECT * FROM spotify_accounts WHERE user_id = %s", (g.user["id"],)
    ).fetchone()
    if not row or not row["refresh_token"]:
        return jsonify(
            {"configured": True, "connected": False, "display_name": None, "product": None, "premium": False}
        )
    return jsonify(
        {
            "configured": True,
            "connected": True,
            "display_name": row["display_name"],
            "product": row["product"],
            "premium": row["product"] == "premium",
        }
    )


@bp.route("/login", methods=["GET"])
@login_required
def spotify_login():
    cfg = get_config()
    if not _configured(cfg):
        return json_error("spotify_not_configured", "Spotify integration is not configured.", 503)

    verifier = _gen_pkce_verifier()
    state = secrets.token_urlsafe(24)
    session["spotify_state"] = state
    session["spotify_code_verifier"] = verifier
    session["spotify_oauth_user_id"] = g.user["id"]

    params = {
        "client_id": cfg.spotify_client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(cfg),
        "state": state,
        "scope": SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": _pkce_challenge(verifier),
    }
    return redirect(AUTHORIZE_URL + "?" + urlencode(params))


@bp.route("/callback", methods=["GET"])
@login_required
def spotify_callback():
    cfg = get_config()

    def fail(reason):
        return redirect("/app?spotify=error&reason=" + reason)

    if request.args.get("error"):
        return fail(request.args.get("error"))

    expected_state = session.pop("spotify_state", None)
    verifier = session.pop("spotify_code_verifier", None)
    oauth_user_id = session.pop("spotify_oauth_user_id", None)
    state = request.args.get("state")
    code = request.args.get("code")

    if not state or not expected_state or state != expected_state:
        return fail("state_mismatch")
    if not verifier or oauth_user_id != g.user["id"]:
        return fail("session_expired")
    if not code:
        return fail("missing_code")

    try:
        token_resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(cfg),
                "code_verifier": verifier,
            },
            auth=(cfg.spotify_client_id, cfg.spotify_client_secret),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return fail("network_error")

    if token_resp.status_code != 200:
        return fail("token_exchange_failed")

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    scopes = token_data.get("scope", SCOPES)

    try:
        me_resp = requests.get(
            API_BASE + "/me",
            headers={"Authorization": "Bearer " + access_token},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return fail("network_error")

    if me_resp.status_code != 200:
        return fail("profile_fetch_failed")

    me = me_resp.json()
    db = get_db()
    db.execute(
        """
        INSERT INTO spotify_accounts
            (user_id, spotify_user_id, display_name, product, access_token,
             refresh_token, expires_at, scopes, connected_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            g.user["id"],
            me.get("id"),
            me.get("display_name"),
            me.get("product"),
            encrypt_token(access_token),
            encrypt_token(refresh_token) if refresh_token else None,
            _iso(utcnow() + timedelta(seconds=expires_in)),
            scopes,
            utcnow_iso(),
        ),
    )
    db.commit()
    return redirect("/app?spotify=connected")


@bp.route("/disconnect", methods=["POST"])
@login_required
def disconnect():
    require_csrf()
    db = get_db()
    db.execute("DELETE FROM spotify_accounts WHERE user_id = %s", (g.user["id"],))
    db.commit()
    return jsonify({"ok": True})


@bp.route("/token", methods=["GET"])
@login_required
def token():
    access_token, err = _get_token_or_error()
    if err:
        return err
    db = get_db()
    row = db.execute(
        "SELECT expires_at FROM spotify_accounts WHERE user_id = %s", (g.user["id"],)
    ).fetchone()
    expires_in = 3600
    if row and row["expires_at"]:
        try:
            expires_in = max(0, int((parse_iso(row["expires_at"]) - utcnow()).total_seconds()))
        except (ValueError, TypeError):
            pass
    return jsonify({"access_token": access_token, "expires_in": expires_in})


# ---------------------------------------------------------------------------
# Playback / library
# ---------------------------------------------------------------------------

@bp.route("/playlists", methods=["GET"])
@login_required
def playlists():
    access_token, err = _get_token_or_error()
    if err:
        return err
    try:
        resp = _spotify_api("GET", "/me/playlists?limit=50", access_token)
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)
    if resp.status_code != 200:
        return _map_spotify_error(resp)
    items = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "image": _first_image(p),
            "tracks": (p.get("tracks") or {}).get("total"),
            "uri": p.get("uri"),
        }
        for p in (resp.json().get("items") or [])
    ]
    return jsonify({"items": items})


@bp.route("/now-playing", methods=["GET"])
@login_required
def now_playing():
    access_token, err = _get_token_or_error()
    if err:
        return err
    try:
        resp = _spotify_api("GET", "/me/player", access_token)
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)

    if resp.status_code == 204 or not resp.content:
        return jsonify({"is_playing": False, "track": None, "device": None})
    if resp.status_code != 200:
        return _map_spotify_error(resp)

    data = resp.json()
    item = data.get("item")
    track = None
    if item:
        track = {
            "name": item.get("name"),
            "artists": ", ".join(a.get("name", "") for a in item.get("artists", [])),
            "album": (item.get("album") or {}).get("name"),
            "image": _first_image(item),
            "duration_ms": item.get("duration_ms"),
            "progress_ms": data.get("progress_ms"),
            "uri": item.get("uri"),
        }
    device = data.get("device")
    device_out = (
        {"id": device.get("id"), "name": device.get("name"), "is_active": bool(device.get("is_active"))}
        if device
        else None
    )
    return jsonify({"is_playing": bool(data.get("is_playing")), "track": track, "device": device_out})


@bp.route("/devices", methods=["GET"])
@login_required
def devices():
    access_token, err = _get_token_or_error()
    if err:
        return err
    try:
        resp = _spotify_api("GET", "/me/player/devices", access_token)
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)
    if resp.status_code != 200:
        return _map_spotify_error(resp)
    items = [
        {"id": d.get("id"), "name": d.get("name"), "type": d.get("type"), "is_active": bool(d.get("is_active"))}
        for d in (resp.json().get("devices") or [])
    ]
    return jsonify({"items": items})


@bp.route("/play", methods=["PUT"])
@login_required
def play():
    require_csrf()
    access_token, err = _get_token_or_error()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    params = {}
    if body.get("device_id"):
        params["device_id"] = body["device_id"]
    payload = {}
    if body.get("context_uri"):
        payload["context_uri"] = body["context_uri"]
    try:
        resp = _spotify_api(
            "PUT", "/me/player/play", access_token, params=params, json=(payload or None)
        )
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)
    if resp.status_code not in (200, 202, 204):
        return _map_spotify_error(resp)
    return jsonify({"ok": True})


@bp.route("/pause", methods=["PUT"])
@login_required
def pause():
    require_csrf()
    access_token, err = _get_token_or_error()
    if err:
        return err
    try:
        resp = _spotify_api("PUT", "/me/player/pause", access_token)
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)
    if resp.status_code not in (200, 202, 204):
        return _map_spotify_error(resp)
    return jsonify({"ok": True})


@bp.route("/next", methods=["POST"])
@login_required
def next_track():
    require_csrf()
    access_token, err = _get_token_or_error()
    if err:
        return err
    try:
        resp = _spotify_api("POST", "/me/player/next", access_token)
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)
    if resp.status_code not in (200, 202, 204):
        return _map_spotify_error(resp)
    return jsonify({"ok": True})


@bp.route("/previous", methods=["POST"])
@login_required
def previous_track():
    require_csrf()
    access_token, err = _get_token_or_error()
    if err:
        return err
    try:
        resp = _spotify_api("POST", "/me/player/previous", access_token)
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)
    if resp.status_code not in (200, 202, 204):
        return _map_spotify_error(resp)
    return jsonify({"ok": True})


@bp.route("/volume", methods=["PUT"])
@login_required
def volume():
    require_csrf()
    access_token, err = _get_token_or_error()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    percent = body.get("percent")
    if not isinstance(percent, int) or isinstance(percent, bool) or not (0 <= percent <= 100):
        return json_error("validation_error", "percent must be an integer 0-100.", 400)
    try:
        resp = _spotify_api(
            "PUT", "/me/player/volume", access_token, params={"volume_percent": percent}
        )
    except requests.RequestException:
        return json_error("spotify_error", "Could not reach Spotify.", 502)
    if resp.status_code not in (200, 202, 204):
        return _map_spotify_error(resp)
    return jsonify({"ok": True})
