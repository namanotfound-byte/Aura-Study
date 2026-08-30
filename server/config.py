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


class ProductionConfigError(RuntimeError):
    """Raised from Config.__init__ to refuse to boot in production with an
    unsafe configuration. Never caught anywhere -- letting this propagate is
    exactly what "refuse to boot" means: the process exits with a clear
    message on stderr instead of the app silently accepting a config that
    would log every user out on restart, leave Spotify tokens permanently
    undecryptable, or drop verification emails into a file nobody reads."""


# Values that are obviously a placeholder rather than a real generated
# secret. Not exhaustive -- it exists to catch the common case of someone
# copying ".env.example"'s comments or a tutorial's sample value verbatim,
# not to replace actually generating a real key.
_PLACEHOLDER_VALUES = {
    "changeme", "change-me", "change_me", "changethis", "change-this",
    "replace-me", "replaceme", "replace_me", "your-secret-key",
    "your-secret-key-here", "your-secret-here", "insecure", "placeholder",
    "secret", "password", "example", "test", "xxxxxxxx", "dev-secret",
    "fernet-key-here", "todo", "generate-me", "changeme-in-production",
}


def _looks_like_placeholder(value: str) -> bool:
    return value.strip().lower().strip("<>") in _PLACEHOLDER_VALUES


def _require_production_secret(name: str, value: str, generate_hint: str) -> None:
    if not value or _looks_like_placeholder(value):
        raise ProductionConfigError(
            "Refusing to boot in production: {name} is missing, empty, or still a "
            "placeholder value. Generate a real one and set it in the environment "
            "(never commit it):\n"
            "    {hint}\n"
            "A production process must never fall back to an ephemeral, auto-generated "
            "key -- that logs out every user on each restart and makes any Spotify "
            "refresh tokens already stored (encrypted with the old key) permanently "
            "undecryptable.".format(name=name, hint=generate_hint)
        )


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


def _validate_fernet_key(key: str) -> None:
    """Fail fast at boot if TOKEN_ENC_KEY isn't actually a usable Fernet key
    (e.g. someone pasted a random string), rather than letting every Spotify
    token encrypt/decrypt call fail mysteriously at request time."""
    from cryptography.fernet import Fernet

    try:
        Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ProductionConfigError(
            "TOKEN_ENC_KEY is set but is not a valid Fernet key ({}). Generate one with:\n"
            '    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'.format(exc)
        )


class Config(object):
    """Plain settings object; one attribute per env var, lowercased."""

    def __init__(self):
        load_dotenv(ENV_PATH, override=False)

        self.app_base_url = (os.environ.get("APP_BASE_URL") or "http://127.0.0.1:5055").rstrip("/")
        self.port = int(os.environ.get("PORT") or "5055")
        self.database_path = os.environ.get("DATABASE_PATH") or os.path.join(ROOT_DIR, "aurastudy.db")

        # Postgres (Neon) in production; empty means "use SQLite at
        # database_path instead" (see server/db.py). Neon requires TLS --
        # force sslmode=require if the caller didn't already specify one, so
        # a copy-pasted connection string without it still fails safe.
        self.database_url = os.environ.get("DATABASE_URL") or ""
        if self.database_url and "sslmode=" not in self.database_url:
            sep = "&" if "?" in self.database_url else "?"
            self.database_url = "{}{}sslmode=require".format(self.database_url, sep)

        # Production is inferred from having a real (Postgres) DATABASE_URL,
        # or forced explicitly via ENVIRONMENT=production (e.g. a prod
        # deployment that -- unusually -- still points at SQLite). This is
        # what gates every "refuse to boot unsafe" check below. The test
        # suite's Postgres-backend runs DO set DATABASE_URL and therefore DO
        # go through this same gate -- see tests/conftest.py, which sets a
        # real (non-placeholder) SECRET_KEY/TOKEN_ENC_KEY/SMTP_HOST for
        # exactly that reason, rather than being special-cased around it.
        self.environment = (os.environ.get("ENVIRONMENT") or "").strip().lower()
        self.is_production = bool(self.database_url) or self.environment == "production"

        raw_secret_key = os.environ.get("SECRET_KEY") or ""
        if self.is_production:
            _require_production_secret(
                "SECRET_KEY", raw_secret_key,
                'python -c "import secrets; print(secrets.token_hex(32))"',
            )
            self.secret_key = raw_secret_key
        else:
            self.secret_key = raw_secret_key
            if not self.secret_key:
                print("[config] WARNING: SECRET_KEY is not set; using a random per-boot "
                      "key. Sessions will not survive a restart. Set SECRET_KEY in .env "
                      "for anything beyond local dev.")
                self.secret_key = secrets.token_hex(32)

        raw_token_key = os.environ.get("TOKEN_ENC_KEY") or ""
        if self.is_production:
            _require_production_secret(
                "TOKEN_ENC_KEY", raw_token_key,
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
            )
            self.token_enc_key = raw_token_key
        else:
            self.token_enc_key = raw_token_key or _ensure_token_enc_key()
        _validate_fernet_key(self.token_enc_key)

        self.smtp_host = os.environ.get("SMTP_HOST") or ""
        self.smtp_port = int(os.environ.get("SMTP_PORT") or "587")
        self.smtp_user = os.environ.get("SMTP_USER") or ""
        self.smtp_password = os.environ.get("SMTP_PASSWORD") or ""
        self.smtp_from = os.environ.get("SMTP_FROM") or "AuraStudy <no-reply@aurastudy.local>"
        self.smtp_use_tls = _as_bool(os.environ.get("SMTP_USE_TLS", "true"))

        # HTTP API alternative to SMTP (server/mailer.py prefers this when
        # set). Exists because Render's free web tier blocks outbound SMTP
        # ports 25/465/587 -- Brevo's REST API runs over port 443 instead.
        self.brevo_api_key = os.environ.get("BREVO_API_KEY") or ""

        if self.is_production and not self.brevo_api_key and not self.smtp_host:
            raise ProductionConfigError(
                "Refusing to boot in production: neither BREVO_API_KEY nor SMTP_HOST is "
                "set. Without one of them, verification/reset emails would silently fall "
                "back to dev-outbox mode (written to server/dev_outbox/ instead of sent) "
                "-- a signup whose confirmation email lands in a file nobody reads is a "
                "broken product. Set BREVO_API_KEY (Brevo dashboard -> SMTP & API -> API "
                "Keys tab -- required on hosts like Render's free tier that block "
                "outbound SMTP ports) or SMTP_HOST/SMTP_USER/SMTP_PASSWORD for a real "
                "provider (e.g. Brevo)."
            )

        self.spotify_client_id = os.environ.get("SPOTIFY_CLIENT_ID") or ""
        self.spotify_client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or ""

        self.require_email_verification = _as_bool(os.environ.get("REQUIRE_EMAIL_VERIFICATION", "true"))
        if self.is_production and not self.require_email_verification:
            print("[config] WARNING: REQUIRE_EMAIL_VERIFICATION was disabled but this is "
                  "production; forcing it back on.")
            self.require_email_verification = True

        # Bounds every request body (~2 MB): comfortably above the largest
        # legitimate payload (the /api/state 1 MB cap, see server/state.py)
        # while still bounding an attacker's ability to send an enormous
        # request body. Overridable for ops flexibility, but 2 MB by default
        # regardless of environment.
        self.max_content_length = int(os.environ.get("MAX_CONTENT_LENGTH") or str(2 * 1024 * 1024))


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Cached singleton. Call `get_config.cache_clear()` to force a re-read
    (used by the test suite, which sets env vars per-test before app creation)."""
    return Config()
