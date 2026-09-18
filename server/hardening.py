"""Proxy trust, security headers, and request-body size limits.

Applied once from create_app() so every response (including errors) gets the
headers. See SPEC-PHASE3 PART B for the reasoning behind each directive.
"""
import flask

from .config import get_config


def apply_proxy_fix(app: flask.Flask) -> None:
    """Trust X-Forwarded-* from Render's reverse proxy so request.is_secure
    and request.remote_addr reflect the client, not the hop."""
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


# Every external host this app actually loads something from or connects to,
# by directive. Kept as narrow as the app allows -- see the hardening report
# for exactly which directives needed loosening and why.
#
#   script-src / style-src 'unsafe-inline':
#     index.html uses inline <script>, an inline <style> block, and ~40
#     inline onclick/onchange/oninput handler attributes. None of that can
#     be removed without rewriting index.html, which this task does not own
#     (another agent is actively editing it for the branding pass) and which
#     SPEC-PHASE3 explicitly allows as a documented tradeoff rather than a
#     silent wildcard. Nonces/hashes are not a substitute here: they secure
#     <script> *tags*, not inline event-handler *attributes* -- the only
#     CSP-native way to allow those is 'unsafe-inline' (or the
#     poorly-supported CSP3 'unsafe-hashes').
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
    ],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'", "data:"],
    "connect-src": ["'self'"],
    "media-src": ["'self'", "blob:"],
    "frame-src": ["'self'"],
    "worker-src": ["'self'", "blob:"],
}

_CSP_HEADER_VALUE = "; ".join(
    "{} {}".format(directive, " ".join(sources)) for directive, sources in CSP_DIRECTIVES.items()
)

# Deny only what this app never uses. Deliberately NOT included here (per
# SPEC-PHASE3 PART B, and confirmed by reading the code):
#   - picture-in-picture, screen-wake-lock: static/pip.js's Focus Mode
#   - autoplay, fullscreen: index.html's timer-fullscreen feature
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

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = cfg.app_base_url.startswith("https")

    # Debug mode and the reloader must never be reachable via environment
    # variables in this app -- both leak stack traces/source and, for the
    # reloader, are not meant for a production process at all. Setting these
    # directly (rather than trusting FLASK_DEBUG/FLASK_ENV) means even
    # running this app via `flask run` with those set can't turn debug on;
    # only the explicit `python -m server.app` dev entrypoint can.
    app.config["DEBUG"] = False
    app.debug = False

    @app.after_request
    def _security_headers(response):
        return _apply_security_headers(response)
