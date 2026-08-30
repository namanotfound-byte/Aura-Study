"""Tests for the Phase 4 routing change (SPEC-PHASE4.md "Routing change"):

    GET /      -- unauthenticated landing page, always
    GET /app   -- @login_required, serves the study app (what "/" used to do)
    GET /verify -- covered separately in test_auth.py's auto-login tests
"""
import json
import re

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def register_verify(client, outbox, email="routing-user@example.com", password="pw123456"):
    client.post(
        "/api/auth/register",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
        headers=JSON_HEADERS,
    )
    match = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1]["text"])
    # /verify auto-logs in now, so this alone leaves `client` authenticated.
    resp = client.get("/verify?token={}".format(match.group(1)))
    assert resp.status_code == 302


def test_root_serves_landing_page_unauthenticated(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-authenticated="false"' in body
    # The landing page, not the study app.
    assert 'id="view-timer"' not in body


def test_root_serves_landing_page_when_authenticated_too(client, outbox):
    """Per spec: "/" always renders the landing page, logged in or not --
    landing.js is what carries an already-authenticated visitor through to
    /app once the arrival animation finishes."""
    register_verify(client, outbox)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-authenticated="true"' in body


def test_app_requires_login_and_redirects_to_login_with_next(client):
    resp = client.get("/app")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login?next=%2Fapp"


def test_app_serves_the_study_app_when_authenticated(client, outbox):
    register_verify(client, outbox)
    resp = client.get("/app")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="view-timer"' in body


def test_login_page_redirects_authenticated_user_to_app(client, outbox):
    register_verify(client, outbox)
    resp = client.get("/login")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app"


def test_register_page_redirects_authenticated_user_to_app(client, outbox):
    register_verify(client, outbox)
    resp = client.get("/register")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app"


def test_login_page_renders_for_logged_out_visitor(client):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_healthz_is_unauthenticated_and_does_not_redirect(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
