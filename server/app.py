"""App factory, blueprint registration, and server-rendered page routes.

Run directly (`python -m server.app`) for local dev, or via `run.sh`.
"""
import logging
import os
import sys

import flask
from werkzeug.exceptions import HTTPException

from .auth import bp as auth_bp
from .state import bp as state_bp
from .spotify import bp as spotify_bp
from .spotify_requests import bp as spotify_requests_bp, list_all_requests, mark_request_added
from .leaderboard import bp as leaderboard_bp
from .config import get_config
from .db import init_db, get_db
from .hardening import init_hardening
from .security import (
    ApiError,
    create_session,
    current_user,
    hash_token,
    json_error,
    login_required,
    parse_iso,
    require_csrf,
    set_session_cookie,
)
from .db import iso_or_none, utcnow, utcnow_iso


TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def create_app() -> flask.Flask:
    cfg = get_config()

    app = flask.Flask(
        __name__,
        template_folder=TEMPLATE_DIR,
        static_folder=STATIC_DIR,
        static_url_path="/static",
    )
    app.secret_key = cfg.secret_key

    # ProxyFix, security headers, MAX_CONTENT_LENGTH, and locking down
    # debug/reloader -- see server/hardening.py. Applied before the routes
    # below so every response (including error responses) gets the headers.
    init_hardening(app)

    init_db(app)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(state_bp, url_prefix="/api")
    app.register_blueprint(spotify_bp, url_prefix="/api/spotify")
    app.register_blueprint(spotify_requests_bp, url_prefix="/api/spotify")
    app.register_blueprint(leaderboard_bp, url_prefix="/api")

    # Under gunicorn, Flask's app.logger has no handler attached to
    # gunicorn's error stream, so anything it logs -- including the
    # traceback for an unhandled 500 -- is written nowhere. In production
    # that makes the app undebuggable: it returns a JSON 500 and leaves no
    # record of why. Adopt gunicorn's handlers when running under it.
    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)
    elif not app.logger.handlers:
        logging.basicConfig(stream=sys.stdout, level=logging.INFO)

    # psycopg logs connection-level detail (DNS failure, auth rejection, TLS
    # problem, refused connection) to its own logger. Without a handler on
    # it, a caller only ever sees the generic exception from get_db() and
    # loses that detail -- exactly the wall hit while debugging a Postgres
    # outage in production (server/db.py has the full story).
    for _name in ("psycopg",):
        _lg = logging.getLogger(_name)
        if gunicorn_logger.handlers:
            _lg.handlers = gunicorn_logger.handlers
        _lg.setLevel(logging.INFO)

    @app.errorhandler(ApiError)
    def _handle_api_error(err: ApiError):
        return json_error(err.code, err.message, err.status)

    @app.errorhandler(Exception)
    def _handle_unexpected(err: Exception):
        # HTTPExceptions are deliberate (404/405/403...) -- let the handler
        # below format them. Anything else is a genuine bug or an
        # infrastructure failure, and must leave a traceback behind.
        if isinstance(err, HTTPException):
            return err
        app.logger.exception(
            "Unhandled exception on %s %s",
            flask.request.method,
            flask.request.path,
        )
        if flask.request.path.startswith("/api/"):
            return json_error(
                "internal_server_error",
                "The server hit an unexpected error. Please try again.",
                500,
            )
        return ("Internal Server Error", 500)

    @app.errorhandler(HTTPException)
    def _handle_http_exception(err: HTTPException):
        # Keep /api/* errors JSON-shaped even for framework-level aborts
        # (404s, 405s, etc.) instead of Flask's default HTML error page.
        if flask.request.path.startswith("/api/"):
            code = (err.name or "error").lower().replace(" ", "_")
            return json_error(code, err.description or err.name or "Error", err.code or 500)
        return err

    _register_page_routes(app)

    return app


def _register_page_routes(app: flask.Flask) -> None:
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @app.route("/healthz")
    def healthz():
        return flask.jsonify({"status": "ok"})

    @app.route("/")
    def landing_page():
        # Unauthenticated landing page -- always renders, logged in or not
        # (see SPEC-PHASE4.md's routing table). `authenticated` lets
        # landing.html/landing.js skip straight through to /app once the
        # arrival animation finishes for a user who's already logged in,
        # instead of making them click "Log in" again.
        return flask.render_template("landing.html", authenticated=current_user() is not None)

    @app.route("/app")
    @login_required
    def index():
        # This is what "/" used to serve directly; the study app now lives
        # behind /app, gated exactly as it always was.
        return flask.send_from_directory(root_dir, "index.html")

    @app.route("/login")
    def login_page():
        if current_user() is not None:
            return flask.redirect("/app")
        return flask.render_template("login.html")

    @app.route("/register")
    def register_page():
        if current_user() is not None:
            return flask.redirect("/app")
        return flask.render_template("register.html")

    @app.route("/forgot")
    def forgot_page():
        return flask.render_template("forgot.html")

    @app.route("/reset")
    def reset_page():
        token = flask.request.args.get("token", "")
        return flask.render_template("reset.html", token=token)

    @app.route("/verify")
    def verify_page():
        raw_token = flask.request.args.get("token", "")
        db = get_db()
        success = False
        heading = "That link doesn't look right"
        detail = "This verification link is invalid. Please request a new one from the login page."

        row = db.execute(
            "SELECT * FROM email_tokens WHERE token_hash = %s AND purpose = 'verify'",
            (hash_token(raw_token),),
        ).fetchone() if raw_token else None

        if row is None:
            pass
        elif row["used_at"] is not None:
            # Failure path (spec: "already used" must not create a session)
            # -- someone clicking a link twice, or a bookmarked/forwarded
            # link, still gets a friendly page, just no auto-login.
            heading = "Already verified"
            detail = "This link was already used. You can log in now."
            success = True
        elif parse_iso(row["expires_at"]) <= utcnow():
            # Failure path -- no session.
            heading = "That link expired"
            detail = "Verification links expire after 24 hours. Request a new one from the login page."
        else:
            # is_verified is a real BOOLEAN column on Postgres -- an integer
            # literal `1` is rejected outright ("column is of type boolean
            # but expression is of type integer"), so bind a Python bool.
            db.execute("UPDATE users SET is_verified = %s WHERE id = %s", (True, row["user_id"]))
            db.execute("UPDATE email_tokens SET used_at = %s WHERE id = %s", (utcnow_iso(), row["id"]))
            db.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (utcnow_iso(), row["user_id"]))
            db.commit()

            # Auto-login: a valid, single-use, not-yet-used, not-expired
            # verification token is treated as proof of identity here --
            # this deliberately extends the same trust already implicit in
            # "click this link to confirm your account" (only someone with
            # access to the inbox the link was mailed to can possess the raw
            # token) to also mean "and therefore is this user", the same way
            # a password reset link does. Worth being explicit about: this
            # request is now authenticated as row["user_id"], with no
            # password involved, on the strength of that token alone.
            #
            # Session issued exactly as auth.py's login() does: a fresh
            # token via create_session(), the same cookie flags via
            # set_session_cookie() (HttpOnly, SameSite=Lax, Path=/,
            # Secure when serving HTTPS), same 30-day rotation. Every other
            # branch above returns the rendered message page and must NOT
            # reach this code.
            raw = create_session(row["user_id"], flask.request.headers.get("User-Agent"))
            resp = flask.redirect("/app?verified=1")
            set_session_cookie(resp, raw)
            return resp

        return flask.render_template(
            "message.html", success=success, heading=heading, detail=detail
        )

    def _is_owner(user, cfg) -> bool:
        # If OWNER_EMAIL is unset, this must be unreachable for EVERYONE --
        # never fall open to "no owner configured means anyone can view
        # it". That's why the check is `not cfg.owner_email` first, not
        # just an empty-string comparison that a user with no email could
        # coincidentally satisfy.
        if not cfg.owner_email or user is None:
            return False
        return (user["email"] or "").strip().lower() == cfg.owner_email

    @app.route("/admin/spotify-requests")
    @login_required
    def admin_spotify_requests():
        # This page lists every user's submitted Spotify email -- personal
        # data handed to the owner, and to no one else. A non-owner (or
        # anyone at all when OWNER_EMAIL isn't set) gets a plain 404, not a
        # 403 or a "not authorized" page -- neither of those tells an
        # attacker anything, but a 403 at least confirms the route exists.
        cfg = get_config()
        if not _is_owner(current_user(), cfg):
            flask.abort(404)
        db = get_db()
        raw_rows = list_all_requests(db)
        rows = [
            {
                "id": r["id"],
                "spotify_email": r["spotify_email"],
                "user_email": r["user_email"],
                "user_display_name": r["user_display_name"],
                "status": r["status"],
                # created_at/updated_at come back as a tz-aware datetime on
                # Postgres, an ISO string on SQLite -- normalise before
                # handing to Jinja so the rendered page looks the same on
                # both backends (see db.iso_or_none).
                "created_at": iso_or_none(r["created_at"]),
                "updated_at": iso_or_none(r["updated_at"]),
            }
            for r in raw_rows
        ]
        return flask.render_template("admin_spotify_requests.html", rows=rows)

    @app.route("/admin/spotify-requests/<int:request_id>/mark-added", methods=["POST"])
    @login_required
    def admin_mark_spotify_request_added(request_id):
        cfg = get_config()
        if not _is_owner(current_user(), cfg):
            flask.abort(404)
        require_csrf()
        db = get_db()
        mark_request_added(db, request_id)
        return flask.jsonify({"ok": True})


if __name__ == "__main__":
    cfg = get_config()
    app = create_app()
    # debug/use_reloader are hardcoded, not read from any env var -- see
    # server/hardening.py:init_hardening for why. This __main__ block is
    # local-dev-only; production runs under gunicorn (PART C), never this.
    app.run(host="127.0.0.1", port=cfg.port, debug=False, use_reloader=False)
