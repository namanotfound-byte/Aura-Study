"""Guest trial cookie helpers (7-day try-before-sign-up, no user row).

The guest cookie stores when the trial started. Privileges expire after
GUEST_TRIAL_DAYS, but the cookie itself is kept so /app still loads and the
frontend can soft-lock features without wiping localStorage via a redirect.
"""
import datetime
import json
from typing import Any, Dict, Optional

import flask

from .config import get_config
from .db import parse_iso, utcnow

GUEST_COOKIE_NAME = "aurastudy_guest"
GUEST_TRIAL_DAYS = 7
# Keep the cookie long after privileges expire so /app keeps serving.
GUEST_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 400


def _trial_duration() -> datetime.timedelta:
    return datetime.timedelta(days=GUEST_TRIAL_DAYS)


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


def is_guest_trial_expired(started_at: datetime.datetime) -> bool:
    return utcnow() >= started_at + _trial_duration()


def guest_days_left(started_at: datetime.datetime) -> float:
    remaining = (started_at + _trial_duration()) - utcnow()
    if remaining.total_seconds() <= 0:
        return 0.0
    return remaining.total_seconds() / (60 * 60 * 24)


def guest_context_dict(started_at: datetime.datetime) -> Dict[str, Any]:
    expired = is_guest_trial_expired(started_at)
    return {
        "is_guest": True,
        "started_at": started_at.isoformat(),
        "expired": expired,
        "days_left": round(guest_days_left(started_at), 2),
    }


def logged_out_context_dict() -> Dict[str, Any]:
    return {"is_guest": False}


def set_guest_cookie(response: flask.Response, started_at: Optional[datetime.datetime] = None) -> None:
    """Set or refresh the guest trial cookie on ``response``."""
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
