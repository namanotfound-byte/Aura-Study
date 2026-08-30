"""Email sending: Brevo HTTP API, SMTP, or a dev-outbox fallback. See spec
section 7.

Render's free web tier blocks outbound traffic to SMTP ports 25/465/587
(since 2025-09-26), so SMTP is unreachable from that host no matter how
correct the credentials are. The Brevo HTTP API runs over port 443 instead,
which is not blocked -- see `_send_brevo_api` below.

`send_email` is the low-level primitive; `send_verification_email` and
`send_reset_email` build the branded AuraStudy messages on top of it.
"""
import datetime
import email.utils
import logging
import os
import re
import smtplib
from email.message import EmailMessage

import requests

from .config import get_config

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTBOX_DIR = os.path.join(ROOT_DIR, "server", "dev_outbox")

_URL_RE = re.compile(r"https?://\S+")

ACCENT = "#FF66B2"

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def send_email(to: str, subject: str, html_body: str, text_body: str) -> bool:
    cfg = get_config()
    if cfg.brevo_api_key:
        return _send_brevo_api(cfg, to, subject, html_body, text_body)
    if not cfg.smtp_host:
        return _send_dev_outbox(to, subject, html_body, text_body)
    return _send_smtp(cfg, to, subject, html_body, text_body)


def _send_dev_outbox(to: str, subject: str, html_body: str, text_body: str) -> bool:
    os.makedirs(OUTBOX_DIR, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe_to = to.replace("/", "_").replace("@", "_at_")
    path = os.path.join(OUTBOX_DIR, "{}-{}.html".format(timestamp, safe_to))
    with open(path, "w") as f:
        f.write("<!-- To: {} -->\n<!-- Subject: {} -->\n{}".format(to, subject, html_body))

    match = _URL_RE.search(text_body) or _URL_RE.search(html_body)
    url = match.group(0).rstrip(".,)\"'") if match else None

    lines = [
        "",
        "=" * 64,
        "AuraStudy dev outbox -- no SMTP_HOST configured, email was not sent.",
        "To:      {}".format(to),
        "Subject: {}".format(subject),
    ]
    if url:
        lines.append("Link:    {}".format(url))
    lines.append("Saved:   {}".format(path))
    lines.append("=" * 64)
    lines.append("")
    print("\n".join(lines), flush=True)
    return True


def _send_brevo_api(cfg, to: str, subject: str, html_body: str, text_body: str) -> bool:
    """Send via Brevo's HTTPS REST API (https://developers.brevo.com --
    POST /v3/smtp/email), which runs over port 443 and so isn't blocked by
    Render's free-tier SMTP port block. `SMTP_FROM` stays the single config
    surface for the sender identity; it's split into the API's
    `sender: {email, name}` shape with `email.utils.parseaddr`, which also
    handles a bare address with no display name.
    """
    sender_name, sender_email = email.utils.parseaddr(cfg.smtp_from)
    sender = {"email": sender_email}
    if sender_name:
        sender["name"] = sender_name

    payload = {
        "sender": sender,
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }
    headers = {
        "api-key": cfg.brevo_api_key,
        "content-type": "application/json",
        "accept": "application/json",
    }
    try:
        resp = requests.post(BREVO_API_URL, json=payload, headers=headers, timeout=15)
        if resp.status_code != 201:
            # Brevo's response body names the real problem (unverified
            # sender, bad key, quota exceeded) -- never log headers/payload,
            # which would leak the api-key.
            logging.getLogger(__name__).error(
                "MAIL FAILED to=%s transport=brevo_api status=%s from=%r -- %s",
                to, resp.status_code, cfg.smtp_from, resp.text,
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 -- never let mail failure 500 the request
        logging.getLogger(__name__).error(
            "MAIL FAILED to=%s transport=brevo_api from=%r -- %s: %s",
            to, cfg.smtp_from, type(exc).__name__, exc,
        )
        return False


def _send_smtp(cfg, to: str, subject: str, html_body: str, text_body: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_from
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as smtp:
            if cfg.smtp_use_tls:
                smtp.starttls()
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 -- never let mail failure 500 the request
        # Log rather than print: under gunicorn a bare print() goes to a
        # buffered stdout and can be lost entirely, which hid the real
        # reason verification emails were not arriving in production.
        logging.getLogger(__name__).error(
            "MAIL FAILED to=%s host=%s port=%s user_set=%s from=%r -- %s: %s",
            to, cfg.smtp_host, cfg.smtp_port, bool(cfg.smtp_user),
            cfg.smtp_from, type(exc).__name__, exc,
        )
        return False


def _wrap_html(heading: str, body_html: str, link: str) -> str:
    return """<!doctype html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;background:#FFF5F9;
padding:32px;color:#3a2233;">
  <div style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:20px;
      padding:32px;box-shadow:0 8px 24px rgba(255,102,178,0.18);">
    <h1 style="color:{accent};margin-top:0;">AuraStudy</h1>
    <h2 style="margin-bottom:8px;">{heading}</h2>
    <p style="line-height:1.5;">{body}</p>
    <p style="margin:24px 0;">
      <a href="{link}" style="background:{accent};color:#fff;text-decoration:none;
        padding:12px 20px;border-radius:999px;display:inline-block;font-weight:600;">
        Take me there
      </a>
    </p>
    <p style="font-size:13px;color:#8a6b78;">
      Or copy and paste this link into your browser:<br>
      <a href="{link}" style="color:{accent};word-break:break-all;">{link}</a>
    </p>
  </div>
</body></html>""".format(accent=ACCENT, heading=heading, body=body_html, link=link)


def send_verification_email(to: str, raw_token: str) -> bool:
    cfg = get_config()
    link = "{}/verify?token={}".format(cfg.app_base_url, raw_token)
    subject = "Verify your email for AuraStudy"
    body_html = ("Welcome to AuraStudy! Confirm this is really you and "
                 "your account will be ready to study with. This link expires in "
                 "<strong>24 hours</strong>.")
    text_body = (
        "Welcome to AuraStudy!\n\n"
        "Confirm your email to finish setting up your account. This link expires "
        "in 24 hours:\n\n{}\n".format(link)
    )
    html_body = _wrap_html("Confirm your email", body_html, link)
    return send_email(to, subject, html_body, text_body)


def send_reset_email(to: str, raw_token: str) -> bool:
    cfg = get_config()
    link = "{}/reset?token={}".format(cfg.app_base_url, raw_token)
    subject = "Reset your AuraStudy password"
    body_html = ("Someone (hopefully you!) asked to reset the password on this "
                 "AuraStudy account. This link expires in <strong>1 hour</strong>. "
                 "If it wasn't you, you can safely ignore this email.")
    text_body = (
        "Reset your AuraStudy password. This link expires in 1 hour. If this "
        "wasn't you, you can ignore this email.\n\n{}\n".format(link)
    )
    html_body = _wrap_html("Reset your password", body_html, link)
    return send_email(to, subject, html_body, text_body)
