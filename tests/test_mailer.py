"""Tests for server/mailer.py's transport selection: Brevo HTTP API vs SMTP
vs the dev-outbox fallback.

`requests` is always mocked here -- the real Brevo API is never called. Most
tests build a `Config`-like object directly (a tiny stand-in with just the
attributes `send_email`/`_send_brevo_api` read) rather than going through the
full `app`/`client` fixtures, since those fixtures monkeypatch
`mailer.send_email` itself (see tests/conftest.py) to keep the rest of the
suite from touching this module at all.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from server import mailer


def _cfg(**overrides):
    base = dict(
        brevo_api_key="",
        smtp_host="",
        smtp_port=587,
        smtp_user="",
        smtp_password="",
        smtp_from="AuraStudy <no-reply@aurastudy.local>",
        smtp_use_tls=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_response(status_code=201, text="{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# --------------------------------------------------------- transport choice

def test_brevo_api_chosen_when_key_set(monkeypatch):
    """send_email() must go through _send_brevo_api, not SMTP or the dev
    outbox, whenever BREVO_API_KEY is set -- regardless of whether SMTP_HOST
    is also set (API takes priority per the spec)."""
    cfg = _cfg(brevo_api_key="xkeysib-test", smtp_host="smtp.example.com")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)

    post_mock = MagicMock(return_value=_fake_response(201))
    monkeypatch.setattr(mailer.requests, "post", post_mock)
    smtp_mock = MagicMock()
    monkeypatch.setattr(mailer, "_send_smtp", smtp_mock)
    outbox_mock = MagicMock()
    monkeypatch.setattr(mailer, "_send_dev_outbox", outbox_mock)

    result = mailer.send_email("to@example.com", "Subject", "<p>hi</p>", "hi")

    assert result is True
    post_mock.assert_called_once()
    smtp_mock.assert_not_called()
    outbox_mock.assert_not_called()


def test_smtp_still_chosen_when_only_smtp_host_set(monkeypatch):
    cfg = _cfg(brevo_api_key="", smtp_host="smtp.example.com")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)

    smtp_mock = MagicMock(return_value=True)
    monkeypatch.setattr(mailer, "_send_smtp", smtp_mock)
    post_mock = MagicMock()
    monkeypatch.setattr(mailer.requests, "post", post_mock)

    result = mailer.send_email("to@example.com", "Subject", "<p>hi</p>", "hi")

    assert result is True
    smtp_mock.assert_called_once()
    post_mock.assert_not_called()


def test_dev_outbox_chosen_when_neither_set(monkeypatch, tmp_path):
    cfg = _cfg(brevo_api_key="", smtp_host="")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)
    monkeypatch.setattr(mailer, "OUTBOX_DIR", str(tmp_path))

    post_mock = MagicMock()
    monkeypatch.setattr(mailer.requests, "post", post_mock)

    result = mailer.send_email("to@example.com", "Subject", "<p>hi</p>", "hi")

    assert result is True
    post_mock.assert_not_called()


# --------------------------------------------------------------- payload

def test_brevo_api_payload_shape(monkeypatch):
    cfg = _cfg(brevo_api_key="xkeysib-test", smtp_from="AuraStudy <aurastudy.app@gmail.com>")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)

    post_mock = MagicMock(return_value=_fake_response(201))
    monkeypatch.setattr(mailer.requests, "post", post_mock)

    mailer.send_email("student@example.com", "Verify your email", "<p>html</p>", "text")

    assert post_mock.call_count == 1
    args, kwargs = post_mock.call_args
    assert args[0] == "https://api.brevo.com/v3/smtp/email"

    headers = kwargs["headers"]
    assert headers["api-key"] == "xkeysib-test"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "application/json"

    payload = kwargs["json"]
    assert payload["sender"] == {"email": "aurastudy.app@gmail.com", "name": "AuraStudy"}
    assert payload["to"] == [{"email": "student@example.com"}]
    assert payload["subject"] == "Verify your email"
    assert payload["htmlContent"] == "<p>html</p>"
    assert payload["textContent"] == "text"

    assert kwargs["timeout"] == 15


def test_smtp_from_parses_name_and_email():
    import email.utils

    name, addr = email.utils.parseaddr("AuraStudy <aurastudy.app@gmail.com>")
    assert name == "AuraStudy"
    assert addr == "aurastudy.app@gmail.com"


def test_smtp_from_parses_bare_address_with_no_display_name():
    import email.utils

    name, addr = email.utils.parseaddr("no-reply@aurastudy.local")
    assert name == ""
    assert addr == "no-reply@aurastudy.local"


def test_brevo_api_payload_sender_omits_name_for_bare_address(monkeypatch):
    cfg = _cfg(brevo_api_key="xkeysib-test", smtp_from="no-reply@aurastudy.local")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)

    post_mock = MagicMock(return_value=_fake_response(201))
    monkeypatch.setattr(mailer.requests, "post", post_mock)

    mailer.send_email("to@example.com", "Subject", "<p>hi</p>", "hi")

    payload = post_mock.call_args.kwargs["json"]
    assert payload["sender"] == {"email": "no-reply@aurastudy.local"}


# --------------------------------------------------------------- failures

def test_brevo_api_non_201_returns_false_without_raising(monkeypatch, caplog):
    cfg = _cfg(brevo_api_key="xkeysib-test")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)

    post_mock = MagicMock(return_value=_fake_response(401, text='{"code":"unauthorized","message":"Key not found"}'))
    monkeypatch.setattr(mailer.requests, "post", post_mock)

    with caplog.at_level("ERROR"):
        result = mailer.send_email("to@example.com", "Subject", "<p>hi</p>", "hi")

    assert result is False
    assert "401" in caplog.text
    assert "unauthorized" in caplog.text
    assert "xkeysib-test" not in caplog.text


def test_brevo_api_network_exception_returns_false_without_raising(monkeypatch, caplog):
    cfg = _cfg(brevo_api_key="xkeysib-test")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)

    def _raise(*args, **kwargs):
        raise mailer.requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(mailer.requests, "post", _raise)

    with caplog.at_level("ERROR"):
        result = mailer.send_email("to@example.com", "Subject", "<p>hi</p>", "hi")

    assert result is False
    assert "boom" in caplog.text
    assert "xkeysib-test" not in caplog.text


def test_brevo_api_key_never_logged_on_success(monkeypatch, caplog):
    cfg = _cfg(brevo_api_key="xkeysib-super-secret")
    monkeypatch.setattr(mailer, "get_config", lambda: cfg)
    monkeypatch.setattr(mailer.requests, "post", MagicMock(return_value=_fake_response(201)))

    with caplog.at_level("DEBUG"):
        result = mailer.send_email("to@example.com", "Subject", "<p>hi</p>", "hi")

    assert result is True
    assert "xkeysib-super-secret" not in caplog.text
