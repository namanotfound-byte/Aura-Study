"""Guest cookie helpers (free-forever local use, no user row).

The guest cookie marks a browser as a guest so /app loads without login.
Guests use the timer indefinitely with localStorage; account-only features
(Spotify, Help, cloud sync, appearing on leaderboards) stay locked while
leaderboard GET endpoints remain view-only.
"""
import datetime
import json
from typing import Any, Dict, Optional

import flask

from .config import get_config
from .db import parse_iso, utcnow

GUEST_COOKIE_NAME = "aurastudy_guest"
# Kept for backwards-compatible tests referencing the constant name.
GUEST_TRIAL_DAYS = 7
GUEST_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 400


def guest_started_at_from_request() -> Optional[datetime.datetime]:
    """Return the trial start time from the guest cookie, or None."""
    raw = flask.request.cookies.get(GUEST_COOKIE_NAME)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        started = parsed.get("started_at")
        if not started:
            return None
        return parse_iso(started)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def has_guest_cookie() -> bool:
    return guest_started_at_from_request() is not None


def guest_context_dict(started_at) -> Dict[str, Any]:
    return {
        "is_guest": True,
        "started_at": started_at.isoformat(),
    }


def logged_out_context_dict() -> Dict[str, Any]:
    return {"is_guest": False}


def set_guest_cookie(response: flask.Response, started_at=None) -> None:
    """Set or refresh the guest cookie on ``response``."""
    cfg = get_config()
    when = started_at or utcnow()
    payload = json.dumps({"started_at": when.isoformat()}, separators=(",", ":"))
    response.set_cookie(
        GUEST_COOKIE_NAME,
        payload,
        max_age=GUEST_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="Lax",
        path="/",
        secure=cfg.app_base_url.startswith("https"),
    )


def inject_guest_context(html: str, ctx: Dict[str, Any]) -> str:
    """Inject ``window.__AURA_GUEST_CTX__`` before ``</head>`` in index.html."""
    script = (
        '<script>window.__AURA_GUEST_CTX__='
        + json.dumps(ctx, separators=(",", ":"))
        + ";</script>"
    )
    marker = "</head>"
    if marker not in html:
        return html
    return html.replace(marker, script + "\n" + marker, 1)
