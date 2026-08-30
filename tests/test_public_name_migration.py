"""Direct tests that the `users.public_name` migration (server/db.py:
_sqlite_migrate_public_name / _pg_migrate_public_name, wired into init_db())
is safe against a database that already has a `users` table -- and a real
row in it -- from before this column existed.

This matters specifically because AuraStudy is live in production with a
real user account: `CREATE TABLE IF NOT EXISTS users (...)` is a no-op
against that table, so without an explicit ALTER TABLE step the column would
never appear there and every `SELECT * FROM users` in the app would be
missing it. These tests build a "legacy" users table by hand (the shape it
had before public_name existed), insert a row exactly like the production
row, run the real migration path, and check the row survives untouched
except for a new NULL public_name column.
"""
import sqlite3

import flask
import pytest
from cryptography.fernet import Fernet

from server import config as config_module
from server.db import init_db


def test_sqlite_migration_adds_column_and_preserves_existing_row(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"

    # Build a pre-migration users table: same shape as SQLITE_SCHEMA's users
    # table minus public_name, with one real-looking row already in it --
    # standing in for the production row that predates this change.
    legacy_conn = sqlite3.connect(str(db_path))
    legacy_conn.execute(
        """
        CREATE TABLE users (
          id                INTEGER PRIMARY KEY AUTOINCREMENT,
          email             TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash     TEXT NOT NULL,
          display_name      TEXT,
          is_verified       INTEGER NOT NULL DEFAULT 0,
          created_at        TEXT NOT NULL,
          last_login_at     TEXT
        )
        """
    )
    legacy_conn.execute(
        "INSERT INTO users (email, password_hash, display_name, is_verified, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "real.owner@example.com",
            "pbkdf2_sha256$240000$c2FsdA==$aGFzaA==",
            "Owner",
            1,
            "2026-01-01T00:00:00+00:00",
        ),
    )
    legacy_conn.commit()
    legacy_conn.close()

    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-prod")
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:5055")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    config_module.get_config.cache_clear()

    # This is the exact code path the real app runs at boot.
    dummy_app = flask.Flask(__name__)
    init_db(dummy_app)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE email = 'real.owner@example.com'").fetchone()
    assert row is not None, "the pre-existing row must survive the migration"
    assert row["public_name"] is None, "an old row must NOT be auto-populated"
    assert row["display_name"] == "Owner"  # untouched
    assert row["is_verified"] == 1  # untouched
    assert row["password_hash"] == "pbkdf2_sha256$240000$c2FsdA==$aGFzaA=="  # untouched

    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert "public_name" in cols

    # The unique index must exist and actually enforce case-insensitive
    # uniqueness, including against the migrated legacy row.
    conn.execute("UPDATE users SET public_name = 'Shared Name' WHERE email = 'real.owner@example.com'")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, is_verified, created_at, public_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("second@example.com", "x", None, 0, "2026-01-01T00:00:00+00:00", "shared name"),
        )
    conn.close()

    # Calling init_db again (a normal restart) must be a pure no-op on an
    # already-migrated database -- no error, column still there, row intact.
    init_db(dummy_app)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE email = 'real.owner@example.com'").fetchone()
    assert row["public_name"] == "Shared Name"
    conn.close()

    config_module.get_config.cache_clear()


def test_postgres_migration_adds_column_and_preserves_existing_row(pg_cluster, request, monkeypatch):
    if pg_cluster is None:
        pytest.skip("postgres backend unavailable (see the pg_cluster fixture's warning)")

    db_url = request.getfixturevalue("_pg_test_database")

    import psycopg

    # Build a pre-migration users table: PG_SCHEMA's shape minus public_name,
    # with one real-looking row already in it.
    with psycopg.connect(db_url, autocommit=True) as legacy_conn:
        legacy_conn.execute(
            """
            CREATE TABLE users (
              id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              email             TEXT NOT NULL,
              password_hash     TEXT NOT NULL,
              display_name      TEXT,
              is_verified       BOOLEAN NOT NULL DEFAULT FALSE,
              created_at        TIMESTAMPTZ NOT NULL,
              last_login_at     TIMESTAMPTZ
            )
            """
        )
        legacy_conn.execute("CREATE UNIQUE INDEX idx_users_email_lower ON users (lower(email))")
        legacy_conn.execute(
            "INSERT INTO users (email, password_hash, display_name, is_verified, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("real.owner@example.com", "pbkdf2_sha256$240000$c2FsdA==$aGFzaA==", "Owner", True, "2026-01-01T00:00:00+00:00"),
        )

    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-for-prod")
    monkeypatch.setenv("TOKEN_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:5055")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    config_module.get_config.cache_clear()

    dummy_app = flask.Flask(__name__)
    init_db(dummy_app)  # the exact code path the real app runs at boot

    with psycopg.connect(db_url, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = 'real.owner@example.com'"
        ).fetchone()
        assert row is not None, "the pre-existing row must survive the migration"
        assert row["public_name"] is None, "an old row must NOT be auto-populated"
        assert row["display_name"] == "Owner"  # untouched
        assert row["is_verified"] is True  # untouched

        cols = [
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'"
            ).fetchall()
        ]
        assert "public_name" in cols

        # Unique index must exist and enforce case-insensitive uniqueness.
        conn.execute("UPDATE users SET public_name = 'Shared Name' WHERE email = 'real.owner@example.com'")
        with pytest.raises(psycopg.IntegrityError):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO users (email, password_hash, display_name, is_verified, created_at, public_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    ("second@example.com", "x", None, False, "2026-01-01T00:00:00+00:00", "shared name"),
                )

    # Restart-safety: calling init_db again must be a pure no-op.
    init_db(dummy_app)
    with psycopg.connect(db_url, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = 'real.owner@example.com'"
        ).fetchone()
        assert row["public_name"] == "Shared Name"

    config_module.get_config.cache_clear()
