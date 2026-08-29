"""App factory, blueprint registration, and server-rendered page routes.

Run directly (`python -m server.app`) for local dev, or via `run.sh`.
"""
import os

import flask
from werkzeug.exceptions import HTTPException

from .auth import bp as auth_bp
from .state import bp as state_bp
from .spotify import bp as spotify_bp
from .config import get_config
from .db import init_db, get_db
from .security import (
    ApiError,
    current_user,
    hash_token,
    json_error,
    login_required,
    parse_iso,
)
from .db import utcnow


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

    init_db(app)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(state_bp, url_prefix="/api")
    app.register_blueprint(spotify_bp, url_prefix="/api/spotify")

    @app.errorhandler(ApiError)
    def _handle_api_error(err: ApiError):
        return json_error(err.code, err.message, err.status)

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
    @login_required
    def index():
        return flask.send_from_directory(root_dir, "index.html")

    @app.route("/login")
    def login_page():
        if current_user() is not None:
            return flask.redirect("/")
        return flask.render_template("login.html")

    @app.route("/register")
    def register_page():
        if current_user() is not None:
            return flask.redirect("/")
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
            "SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = 'verify'",
            (hash_token(raw_token),),
        ).fetchone() if raw_token else None

        if row is None:
            pass
        elif row["used_at"] is not None:
            heading = "Already verified"
            detail = "This link was already used. You can log in now."
            success = True
        elif parse_iso(row["expires_at"]) <= utcnow():
            heading = "That link expired"
            detail = "Verification links expire after 24 hours. Request a new one from the login page."
        else:
            from .db import utcnow_iso
            db.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (row["user_id"],))
            db.execute("UPDATE email_tokens SET used_at = ? WHERE id = ?", (utcnow_iso(), row["id"]))
            db.commit()
            success = True
            heading = "You're verified! ✨"
            detail = "Your email is confirmed -- you can log in now and start studying."

        return flask.render_template(
            "message.html", success=success, heading=heading, detail=detail
        )


if __name__ == "__main__":
    cfg = get_config()
    app = create_app()
    app.run(host="127.0.0.1", port=cfg.port, debug=False)
