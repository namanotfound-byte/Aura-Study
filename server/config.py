"""Environment configuration loading for AuraStudy.

Reads `.env` from the project root (via python-dotenv) plus process
environment variables, and exposes a single cached `Config` object through
`get_config()`. See spec section 3 for the full env var table and section 13
for the frozen attribute contract other modules rely on.
"""
import os
import secrets
from functools import lru_cache

from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT_DIR, ".env")


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _ensure_token_enc_key() -> str:
    """Generate a Fernet key and persist it into .env so it's stable across restarts.

    Only called when TOKEN_ENC_KEY is not already set in the environment (e.g. by
    a test harness), so we never clobber a real project .env during tests.
    """
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    try:
        lines = []
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r") as f:
                lines = f.readlines()
        found = False
        for i, line in enumerate(lines):
            if line.startswith("TOKEN_ENC_KEY="):
                lines[i] = "TOKEN_ENC_KEY={}\n".format(key)
                found = True
                break
        if not found:
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            lines.append("TOKEN_ENC_KEY={}\n".format(key))
        with open(ENV_PATH, "w") as f:
            f.writelines(lines)
    except OSError:
        pass  # best-effort; fall back to an in-memory key for this process
    os.environ["TOKEN_ENC_KEY"] = key
    return key


class Config(object):
    """Plain settings object; one attribute per env var, lowercased."""

    def __init__(self):
        load_dotenv(ENV_PATH, override=False)

        self.secret_key = os.environ.get("SECRET_KEY") or ""
        if not self.secret_key:
            print("[config] WARNING: SECRET_KEY is not set; using a random per-boot "
                  "key. Sessions will not survive a restart. Set SECRET_KEY in .env "
                  "for anything beyond local dev.")
            self.secret_key = secrets.token_hex(32)

        self.app_base_url = (os.environ.get("APP_BASE_URL") or "http://127.0.0.1:5055").rstrip("/")
        self.port = int(os.environ.get("PORT") or "5055")
        self.database_path = os.environ.get("DATABASE_PATH") or os.path.join(ROOT_DIR, "aurastudy.db")

        self.token_enc_key = os.environ.get("TOKEN_ENC_KEY") or _ensure_token_enc_key()

        self.smtp_host = os.environ.get("SMTP_HOST") or ""
        self.smtp_port = int(os.environ.get("SMTP_PORT") or "587")
        self.smtp_user = os.environ.get("SMTP_USER") or ""
        self.smtp_password = os.environ.get("SMTP_PASSWORD") or ""
        self.smtp_from = os.environ.get("SMTP_FROM") or "AuraStudy <no-reply@aurastudy.local>"
        self.smtp_use_tls = _as_bool(os.environ.get("SMTP_USE_TLS", "true"))

        self.spotify_client_id = os.environ.get("SPOTIFY_CLIENT_ID") or ""
        self.spotify_client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or ""

        self.require_email_verification = _as_bool(os.environ.get("REQUIRE_EMAIL_VERIFICATION", "true"))


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Cached singleton. Call `get_config.cache_clear()` to force a re-read
    (used by the test suite, which sets env vars per-test before app creation)."""
    return Config()
