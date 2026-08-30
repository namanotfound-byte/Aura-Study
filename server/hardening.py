"""Production hardening that sits at the WSGI/HTTP layer, separate from the
auth-specific primitives in security.py: trusting Render's reverse proxy for
exactly one hop, and the security headers applied to every response.

See SPEC-PHASE3.md PART B.
"""
import flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_config

# ---------------------------------------------------------------------------
# Proxy trust
# ---------------------------------------------------------------------------


def apply_proxy_fix(app: flask.Flask) -> None:
    """Trust exactly one reverse-proxy hop (Render's own edge) for the
    client IP, protocol and host.

    `x_for=1` makes Werkzeug take the *rightmost* entry of `X-Forwarded-For`
    as `request.remote_addr` -- i.e. the entry the trusted proxy itself
    appended -- and discard everything to its left, which is exactly the
    part a client can freely forge. A request straight from the internet
    with a hand-crafted `X-Forwarded-For: 1.2.3.4` cannot make itself look
    like IP 1.2.3.4: ProxyFix still reads the *last* comma-separated value,
    which is whatever the one trusted hop in front of this process put
    there. (This assumes -- as is true on Render, Heroku, Fly, and similar
    PaaS platforms -- that the app process is only reachable through that
    proxy, never directly from the public internet; ProxyFix has no way to
    verify that on its own. See the hardening report for this as a stated
    residual assumption.)

    `x_proto=1` and `x_host=1` similarly trust `X-Forwarded-Proto` /
    `X-Forwarded-Host` for one hop, which is what makes `request.is_secure`
    (and therefore the `Secure` cookie flag and the HSTS header below)
    correct when Render terminates TLS in front of a plain-HTTP backend.
    """
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]


def get_client_ip() -> str:
    """The client IP as resolved by `apply_proxy_fix` above -- i.e. already
    corrected for Render's proxy hop and not spoofable via a forged
    `X-Forwarded-For`. A thin, named wrapper around `request.remote_addr` so
    call sites (rate limiting) read as "the real client IP", not
    "whatever WSGI handed us"."""
    return flask.request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

# Every external host this app actually loads something from or connects to,
# by directive. Kept as narrow as the app allows -- see the hardening report
# for exactly which directives needed loosening and why.
#
#   script-src / style-src 'unsafe-inline':
#     index.html uses inline <script>, an inline <style> block, and ~40
#     inline onclick/onchange/oninput handler attributes; static/spotify.js
#     builds markup with inline style="" attributes. None of that can be
#     removed without rewriting index.html, which this task does not own
#     (another agent is actively editing it for the branding pass) and which
#     SPEC-PHASE3 explicitly allows as a documented tradeoff rather than a
#     silent wildcard. Nonces/hashes are not a substitute here: they secure
#     <script> *tags*, not inline event-handler *attributes* -- the only
#     CSP-native way to allow those is 'unsafe-inline' (or the
#     poorly-supported CSP3 'unsafe-hashes').
#   connect-src / media-src / worker-src *.spotify.com, *.scdn.co, wss:
#     the Web Playback SDK's realtime ("dealer") connection and encrypted
#     audio streaming use undocumented subdomains under these two domains
#     that change over time; a fixed host list breaks the moment Spotify
#     rotates one. Restricted to Spotify's own domains (not a blanket "*"),
#     which is the "as tight as the SDK realistically allows" middle ground
#     called for in the spec, not a wildcard-and-call-it-hardened policy.
CSP_DIRECTIVES = {
    "default-src": ["'self'"],
    "base-uri": ["'self'"],
    "object-src": ["'none'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
    "script-src": [
        "'self'",
        "'unsafe-inline'",
        "https://cdn.jsdelivr.net",
        "https://unpkg.com",
        "https://sdk.scdn.co",
    ],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:", "https://i.scdn.co", "https://*.scdn.co",
               "https://*.spotifycdn.com"],
    "font-src": ["'self'", "data:"],
    "connect-src": [
        "https://*.spotifycdn.com",
        "'self'",
        "https://api.spotify.com",
        "https://sdk.scdn.co",
        "https://*.spotify.com",
        "wss://*.spotify.com",
    ],
    "media-src": ["'self'", "blob:", "https://*.scdn.co",
                 "https://*.spotifycdn.com"],
    # The Web Playback SDK mounts its own iframe on sdk.scdn.co to run
    # playback/EME. Allowing only open.spotify.com blocked it, so the SDK
    # never registered the browser as a device and Spotify reported
    # "No Active Device" with nothing to play on.
    "frame-src": ["https://open.spotify.com", "https://sdk.scdn.co",
                  "https://*.spotify.com"],
    "worker-src": ["'self'", "blob:"],
}

_CSP_HEADER_VALUE = "; ".join(
    "{} {}".format(directive, " ".join(sources)) for directive, sources in CSP_DIRECTIVES.items()
)

# Deny only what this app never uses. Deliberately NOT included here (per
# SPEC-PHASE3 PART B, and confirmed by reading the code):
#   - picture-in-picture, screen-wake-lock: static/pip.js's Focus Mode
#   - autoplay, fullscreen, clipboard-write, encrypted-media: the Spotify
#     embed iframe's own `allow` attribute requests these (static/spotify.js);
#     an iframe can only ever get a feature the top-level Permissions-Policy
#     already permits, so denying any of them here would silently break the
#     embed regardless of its `allow` attribute. fullscreen is also used
#     directly by index.html's own timer-fullscreen feature.
_PERMISSIONS_POLICY_VALUE = ", ".join([
    "geolocation=()",
    "microphone=()",
    "camera=()",
    "usb=()",
    "payment=()",
    "magnetometer=()",
    "gyroscope=()",
    "midi=()",
    "interest-cohort=()",
])


def _apply_security_headers(response: flask.Response) -> flask.Response:
    cfg = get_config()

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = _CSP_HEADER_VALUE
    response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY_VALUE

    # HSTS only ever makes sense to advertise over HTTPS. `request.is_secure`
    # reflects X-Forwarded-Proto once apply_proxy_fix() is in place (see
    # above); app_base_url is checked too so this still activates for a
    # direct-HTTPS deployment that isn't behind Render's proxy at all.
    if flask.request.is_secure or cfg.app_base_url.startswith("https"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


def init_hardening(app: flask.Flask) -> None:
    """Wire up proxy trust, security headers, and the request-body size cap.
    Call once from create_app()."""
    apply_proxy_fix(app)

    cfg = get_config()
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_content_length

    # Debug mode and the reloader must never be reachable via environment
    # variables in this app -- both leak stack traces/source and, for the
    # reloader, are not meant for a production process at all. Setting these
    # directly (rather than trusting FLASK_DEBUG/FLASK_ENV) means even
    # running this app via `flask run` with those set can't turn debug on;
    # the intended production entrypoint is gunicorn (PART C), which doesn't
    # consult them at all.
    app.config["DEBUG"] = False
    app.debug = False

    app.after_request(_apply_security_headers)
