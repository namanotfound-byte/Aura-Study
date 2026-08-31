"""Tests for server/state.py -- GET/PUT /api/state. See spec section 10."""
import json
import re

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def register_verify_login(client, outbox, email="state-user@example.com", password="pw123456"):
    client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    match = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1]["text"])
    client.get("/verify?token={}".format(match.group(1)))
    resp = client.post(
        "/api/auth/login",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200


def test_state_requires_login(client):
    resp = client.get("/api/state", headers=JSON_HEADERS)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthenticated"

    put_resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"a": 1}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert put_resp.status_code == 401


def test_state_empty_before_first_write(client, outbox):
    register_verify_login(client, outbox)
    resp = client.get("/api/state", headers=JSON_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["payload"] is None
    assert data["version"] == 0


def test_state_put_get_round_trip(client, outbox):
    register_verify_login(client, outbox)

    put_resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"sessions": [1, 2, 3]}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert put_resp.status_code == 200
    put_data = put_resp.get_json()
    assert put_data["ok"] is True
    assert put_data["version"] == 1
    assert "updated_at" in put_data

    get_resp = client.get("/api/state", headers=JSON_HEADERS)
    assert get_resp.status_code == 200
    get_data = get_resp.get_json()
    assert get_data["payload"] == {"sessions": [1, 2, 3]}
    assert get_data["version"] == 1


def test_state_put_updates_existing_row(client, outbox):
    register_verify_login(client, outbox)

    client.put(
        "/api/state",
        data=json.dumps({"payload": {"n": 1}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    second = client.put(
        "/api/state",
        data=json.dumps({"payload": {"n": 2}, "version": 1}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert second.status_code == 200
    assert second.get_json()["version"] == 2

    get_resp = client.get("/api/state", headers=JSON_HEADERS)
    assert get_resp.get_json()["payload"] == {"n": 2}


def test_state_version_conflict(client, outbox):
    register_verify_login(client, outbox)

    client.put(
        "/api/state",
        data=json.dumps({"payload": {"n": 1}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )

    # Stale version (still 0) now conflicts, since the server moved to 1.
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"n": 2}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["error"] == "conflict"
    assert data["payload"] == {"n": 1}
    assert data["version"] == 1


def test_state_put_requires_csrf_header(client, outbox):
    register_verify_login(client, outbox)
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"n": 1}, "version": 0}),
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_state_put_rejects_non_object_payload(client, outbox):
    register_verify_login(client, outbox)
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": [1, 2, 3], "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation_error"


def test_state_put_rejects_oversized_payload(client, outbox):
    register_verify_login(client, outbox)
    huge = {"blob": "x" * (1024 * 1024 + 10)}
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": huge, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 413
    assert resp.get_json()["error"] == "payload_too_large"


def test_state_put_accepts_optional_local_date(client, outbox):
    """static/sync.js sends the client's own local calendar date (see
    server/leaderboard.py:current_week_start) on every PUT so the weekly-
    total recompute lands in the same week the client's own session.date
    strings agree on. A missing or garbage value must never break the save
    itself -- it just falls back to the server's UTC date for that recompute."""
    register_verify_login(client, outbox)
    resp = client.put(
        "/api/state",
        data=json.dumps({"payload": {"sessions": []}, "version": 0, "local_date": "not-a-real-date"}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 200

    resp2 = client.put(
        "/api/state",
        data=json.dumps({"payload": {"sessions": []}, "version": 1}),  # local_date omitted entirely
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert resp2.status_code == 200


def test_concurrent_state_put_does_not_silently_lose_an_update(app, client, outbox, monkeypatch):
    """Regression for a lost-update race in the version-check above: two
    requests for the same user that both read the row's `version` before
    either commits must not both succeed. Before this fix, the second
    request's UPDATE had no guard tying it back to the version it actually
    read -- Postgres's default READ COMMITTED isolation lets it block on the
    row lock, then proceed once the first request commits, silently
    overwriting the first request's just-saved data using its own now-stale
    `new_version` -- with no 409, no error, and no signal to either caller
    that anything went wrong. One of the two writes would simply vanish:
    exactly the "logged but missing" symptom this whole fix pass is about,
    just caused server-side instead of client-side.

    This is now real (two nearly-simultaneous PUTs for the same user is a
    live possibility): a session log's immediate flush, the regular
    debounce, and the pagehide/visibilitychange teardown flush (see
    static/sync.js) can all fire close together, and two browser tabs can
    push around the same moment too.

    Simulated deterministically here (rather than via real OS-thread timing,
    which can't reliably force a specific interleaving) by intercepting THIS
    request's own first `db.execute()` call inside PUT /api/state --
    server/state.py's SELECT that reads `current_version` -- and, as a side
    effect right after it captures that result, committing a second, fully
    independent write to the same row through a separate raw connection.
    That models "another request's PUT already committed in the gap between
    this request's read and its own write" exactly.
    """
    register_verify_login(client, outbox, email="race@example.com")

    first = client.put(
        "/api/state",
        data=json.dumps({"payload": {"sessions": [{"n": 1}]}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    assert first.status_code == 200
    assert first.get_json()["version"] == 1

    from server import state as state_module
    from server.config import get_config
    from server.db import get_db as real_get_db

    raced_payload = {"sessions": [{"n": 999, "raced_in_by_another_request": True}]}

    class _RaceInjectingConnWrapper:
        """Forwards to the real per-request connection, but on the FIRST
        execute() call (put_state's SELECT reading current_version) also
        commits an independent write to the same row through a brand-new
        connection first -- simulating another request's PUT having already
        landed in the gap between this request's read and its own write.
        A plain wrapper object (not the real adapter) because the real
        adapters use __slots__ and can't have `execute` monkeypatched onto
        an instance directly."""

        def __init__(self, real_conn):
            self._real = real_conn
            self._call_count = 0

        def execute(self, sql, params=()):
            self._call_count += 1
            cur = self._real.execute(sql, params)
            if self._call_count == 1:
                cfg = get_config()
                if cfg.database_url:
                    import psycopg

                    other = psycopg.connect(cfg.database_url, autocommit=True)
                    other.execute(
                        "UPDATE user_state SET version = version + 1, payload = %s WHERE user_id = %s",
                        (json.dumps(raced_payload), params[0]),
                    )
                else:
                    import sqlite3

                    other = sqlite3.connect(cfg.database_path)
                    other.execute(
                        "UPDATE user_state SET version = version + 1, payload = ? WHERE user_id = ?",
                        (json.dumps(raced_payload), params[0]),
                    )
                    other.commit()
                other.close()
            return cur

        def commit(self):
            self._real.commit()

        def rollback(self):
            self._real.rollback()

        def close(self):
            self._real.close()

    def sneaky_get_db():
        # server/state.py:put_state calls get_db() exactly once per request
        # (the returned connection is then reused for every db.execute() in
        # that request), so a fresh wrapper per call wraps each request's
        # connection exactly once -- no idempotency bookkeeping needed.
        return _RaceInjectingConnWrapper(real_get_db())

    monkeypatch.setattr(state_module, "get_db", sneaky_get_db)

    second = client.put(
        "/api/state",
        data=json.dumps({"payload": {"sessions": [{"n": 2}]}, "version": 1}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    # Must be reported as a conflict -- never a silent 200 that actually
    # clobbered the raced-in write.
    assert second.status_code == 409
    data = second.get_json()
    assert data["error"] == "conflict"
    assert data["version"] == 2
    assert data["payload"] == raced_payload

    monkeypatch.undo()
    final = client.get("/api/state", headers=JSON_HEADERS)
    final_data = final.get_json()
    assert final_data["version"] == 2
    assert final_data["payload"] == raced_payload  # the "second" request's own data never landed, and never silently ate the race winner's


def test_state_is_isolated_per_user(client, outbox):
    register_verify_login(client, outbox, email="alice@example.com")
    client.put(
        "/api/state",
        data=json.dumps({"payload": {"owner": "alice"}, "version": 0}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    client.post("/api/auth/logout", headers=JSON_HEADERS)

    register_verify_login(client, outbox, email="bob@example.com")
    resp = client.get("/api/state", headers=JSON_HEADERS)
    assert resp.get_json()["payload"] is None
