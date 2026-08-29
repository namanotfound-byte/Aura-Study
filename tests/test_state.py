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
