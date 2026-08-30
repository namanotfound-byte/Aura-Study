# AuraStudy ✨

A cute pastel study timer with a Pomodoro-style countdown and a continuous stopwatch, courses, a session log, todos, badges & achievements, a "Mochi" study pet that levels up as you rack up focus minutes, Chart.js analytics, and pink/blue themes — now with real accounts and Spotify.

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Setting up Spotify](#setting-up-spotify)
- [Setting up email](#setting-up-email)
- [API reference](#api-reference)
- [Running the tests](#running-the-tests)
- [Security notes](#security-notes)
- [Project structure](#project-structure)
- [Deployment](#deployment)
- [License](#license)

---

## Features

### The app

- **Timer engine** with two modes: a **Countdown Block** (Pomodoro-style, configurable default duration) and a **Continuous Stopwatch**.
- **Courses** to organize study sessions by subject.
- **Session log** ("Sessions Log") recording every completed focus block.
- **Todos** with a completed-task filter, scoped to your study workflow.
- **Badges & achievements** ("Trophies Cabinet") — daily/weekly badges plus a longer-term achievements list, unlocked by study behaviour (e.g. first countdown session, active days).
- **Mochi**, a study pet that hatches and evolves through levels as your total focus minutes add up.
- **Analytics dashboard** built with Chart.js (bar charts of study time, etc).
- **Sticky note scratchpad** on the timer view for jotting stray thoughts without breaking focus.
- **Pink ("Bubblegum Pink") and Blue ("Blueberry Frost") themes**, switchable from the sidebar.
- Pure client-side app (`index.html`) — all of this ran on `localStorage` alone before the backend below existed, and still works offline as a cache.

### New: accounts & music

- **Email/password accounts** with email verification and password reset, backed by a real Flask + SQLite server.
- **Server-side sync** of your study data — your courses, sessions, todos, badges and Mochi's progress follow you across browsers/devices instead of living only in one browser's `localStorage`.
- **Spotify integration**: connect your Spotify account, see what's playing, browse your playlists, and (Premium only) control playback — all from inside AuraStudy, including a compact now-playing strip on the Timer view.

---

## Requirements

- **Python 3.9+** locally (the code targets 3.9 syntax specifically, since that's what's installed on the reference dev machine). Production (Render) runs **Python 3.12** — see [Deployment](#deployment) — and the code is compatible with both.
- No Node.js, npm, or any JS build step — the frontend is plain browser JavaScript loaded with `<script>` tags directly by `index.html`. There is nothing to `npm install` or bundle.

---

## Quick start

```bash
./run.sh
```

This creates a `.venv` virtualenv if one doesn't exist, installs `requirements.txt`, copies `.env.example` to `.env` on first run (if `.env` is missing), and starts the server.

Then open:

```
http://127.0.0.1:5055
```

You'll land on the login page. Click through to **Register** and create an account — no configuration is required to try this out. With no SMTP server configured (the default), AuraStudy runs in **dev outbox mode**: instead of sending a real email, it writes the verification message to `server/dev_outbox/` as an HTML file *and* prints a banner straight to the terminal containing the verification link, e.g.:

```
================================================================
AuraStudy dev outbox -- no SMTP_HOST configured, email was not sent.
To:      you@example.com
Subject: Verify your email for AuraStudy ✨
Link:    http://127.0.0.1:5055/verify?token=...
Saved:   server/dev_outbox/20260829T161230123456-you_at_example.com.html
================================================================
```

Copy that link into your browser, verify, and log in — the whole signup flow works immediately with zero configuration. Spotify and real outbound email are both optional and can be set up later (see below).

---

## Configuration

All settings are read from a `.env` file in the project root (via `python-dotenv`), copied from `.env.example` on first run by `run.sh`. `.env` is git-ignored and should never contain secrets you commit.

| Env var | Default | What it does |
|---|---|---|
| `SECRET_KEY` | random, generated per boot (local dev only) | Flask's session-signing secret. A random key means Flask's signed session cookie (used only for the brief Spotify OAuth handshake) won't survive a restart. **Required in production** — see [Deployment](#deployment) step 4 for the exact command to generate one; the app refuses to boot without it. |
| `APP_BASE_URL` | `http://127.0.0.1:5055` | Base URL the app is served from. Used to build the links in verification/reset emails and to build the Spotify OAuth redirect URI. Use the literal loopback IP for local dev, not `localhost`. |
| `PORT` | `5055` | Port the dev server listens on. (Port 5000 is reserved by macOS AirPlay Receiver — don't use it.) |
| `DATABASE_PATH` | `<project root>/aurastudy.db` | Path to the SQLite database file. Only used when `DATABASE_URL` is empty. |
| `DATABASE_URL` | *(empty)* | Postgres connection string (e.g. from Neon). Leave empty for local dev — the app falls back to SQLite with zero configuration. Setting this switches the app into **production mode**: `SECRET_KEY`, `TOKEN_ENC_KEY` and `SMTP_HOST` all become mandatory and the app refuses to boot without them; the dev-outbox email fallback is disabled. `sslmode=require` is appended automatically if missing — Neon requires TLS. See [Deployment](#deployment). |
| `ENVIRONMENT` | *(empty)* | Set to `production` to force production-mode checks explicitly (normally implied automatically by `DATABASE_URL` being set — this exists so it's never ambiguous from a dashboard alone). Leave empty locally. |
| `TOKEN_ENC_KEY` | auto-generated into `.env` on first run (local dev only) | Fernet key used to encrypt Spotify access/refresh tokens at rest. Locally, the app generates and persists one for you. **In production this must be set explicitly** — see [Deployment](#deployment) step 4 for the exact command; the app validates it's a real Fernet key and refuses to boot otherwise. |
| `SMTP_HOST` | *(empty)* | SMTP server hostname. Leave empty to use dev outbox mode (see [Setting up email](#setting-up-email)). |
| `SMTP_PORT` | `587` | SMTP port. |
| `SMTP_USER` | *(empty)* | SMTP username. |
| `SMTP_PASSWORD` | *(empty)* | SMTP password (or app password — see the Gmail walkthrough below). |
| `SMTP_FROM` | `AuraStudy <no-reply@aurastudy.local>` | The `From:` header on outgoing mail. |
| `SMTP_USE_TLS` | `true` | Whether to issue `STARTTLS` after connecting. |
| `SPOTIFY_CLIENT_ID` | *(empty)* | Your Spotify app's Client ID. Leave empty to keep the Spotify feature disabled (the UI shows a "not configured" state instead of erroring). |
| `SPOTIFY_CLIENT_SECRET` | *(empty)* | Your Spotify app's Client Secret. |
| `REQUIRE_EMAIL_VERIFICATION` | `true` | If true, unverified accounts get `403 email_unverified` on login until they click the verification link. |

---

## Setting up Spotify

Spotify is fully optional — with no client ID/secret set, the Music tab just shows a friendly "ask the owner to configure this" card and nothing else breaks.

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and log in with your Spotify account.
2. Click **Create app**. Give it any name/description you like.
3. In the app's settings, add this exact **Redirect URI**:

   ```
   http://127.0.0.1:5055/api/spotify/callback
   ```

   It must be the literal loopback IP `127.0.0.1`, **not** `localhost` — Spotify's redirect URI policy (since April 2025) rejects bare `localhost` redirect URIs but still allows an explicit loopback IP. If your `APP_BASE_URL` differs (different port, or a real domain in production), the redirect URI must match `APP_BASE_URL` + `/api/spotify/callback` exactly, character for character, or Spotify will reject the OAuth callback.
4. Save, then open the app's **Settings** to find its **Client ID** and **Client Secret**.
5. Copy both into your `.env`:

   ```
   SPOTIFY_CLIENT_ID=your-client-id-here
   SPOTIFY_CLIENT_SECRET=your-client-secret-here
   ```

6. Restart the server (`./run.sh`, or re-run `.venv/bin/python -m server.app`). The Music tab will now show a **Connect Spotify** button.

### Scopes requested

AuraStudy asks for exactly these scopes during the OAuth flow:

| Scope | Why |
|---|---|
| `user-read-private` | Read basic profile info, including your Spotify product tier (Premium vs. Free). |
| `user-read-email` | Read your Spotify account email (used only to identify the connected account, not for login). |
| `user-read-playback-state` | See what's currently playing and which device is active. |
| `user-modify-playback-state` | Play/pause, skip, and change volume from within AuraStudy. |
| `user-read-currently-playing` | Power the now-playing card and the compact strip on the Timer view. |
| `playlist-read-private` | List your own playlists in the Music tab. |
| `playlist-read-collaborative` | Include collaborative playlists you're a member of. |
| `streaming` | Let the browser register itself as a playback device via the Web Playback SDK (Premium only). |

### Premium vs. Free

Spotify's Web Playback SDK and the playback-control endpoints (`play`, `pause`, `next`, `previous`, `volume`) **require a Spotify Premium account** — this is a restriction Spotify itself enforces, not something AuraStudy can work around. If a Free account attempts a control action, AuraStudy passes through Spotify's own error as `403 {"error":"premium_required"}`.

- **Premium accounts** get full in-app control: transport buttons, volume, and the browser itself becomes a playable device ("AuraStudy ✨") via the Web Playback SDK.
- **Free accounts** get a read-only experience instead: no transport controls, a note explaining that control needs Premium, and an embedded Spotify player (`open.spotify.com/embed/playlist/<id>`) for the selected playlist so you can still listen.

---

## Setting up email

By default (`SMTP_HOST` empty) AuraStudy runs in **dev outbox mode**: verification and password-reset emails are never actually sent. Instead, each one is written as an HTML file to `server/dev_outbox/` and the link is printed to the server's stdout, so you can develop and test the full auth flow without ever touching a mail server. This is the recommended setting for local use.

To send real email, fill in the SMTP block in `.env`:

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASSWORD=your-password-or-app-password
SMTP_FROM=AuraStudy <no-reply@example.com>
SMTP_USE_TLS=true
```

AuraStudy connects with `smtplib.SMTP`, issues `STARTTLS` when `SMTP_USE_TLS` is true, logs in if `SMTP_USER` is set, and sends a message with both plain-text and HTML parts. If sending fails for any reason, the request still succeeds (registration/reset always return their normal response) — the failure is only logged as a warning, never surfaced to the user as a 500.

### Gmail app-password walkthrough

Gmail won't accept your normal account password over SMTP if you have 2-Step Verification on (which you should). Use an app password instead:

1. Turn on **2-Step Verification** on your Google account, if it isn't already: [myaccount.google.com/security](https://myaccount.google.com/security).
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Create a new app password (name it something like "AuraStudy").
4. Google shows you a 16-character password — copy it.
5. Set:

   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your-gmail-address@gmail.com
   SMTP_PASSWORD=the-16-character-app-password
   SMTP_FROM=AuraStudy <your-gmail-address@gmail.com>
   SMTP_USE_TLS=true
   ```

6. Restart the server. Registration/reset emails will now actually be delivered.

---

## API reference

All `/api/*` responses are JSON. Errors are shaped `{"error": "<snake_case_code>", "message": "<human text>"}`. Every state-changing `/api/*` request (anything that isn't a plain `GET`) must include the header `X-Requested-With: XMLHttpRequest` or it is rejected with `403 {"error":"csrf_failed"}`.

### Page routes

| Method | Path | Behaviour |
|---|---|---|
| GET | `/` | Login required; serves `index.html`. Redirects to `/login` if not authenticated. |
| GET | `/login` | Renders the login page (redirects to `/` if already logged in). |
| GET | `/register` | Renders the registration page. |
| GET | `/forgot` | Renders the "forgot password" page. |
| GET | `/reset?token=…` | Renders the "set a new password" page for a reset token. |
| GET | `/verify?token=…` | Consumes an email verification token and renders a success/failure message page. |
| GET | `/static/<path>` | Static files (`auth.css`, `auth.js`, `spotify.js`, …). |
| GET | `/healthz` | `{"status":"ok"}`, no auth required. |

### Auth API — `/api/auth`

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/api/auth/register` | `{email, password, display_name?}` | `202 {"ok":true,"message":"…"}` |
| POST | `/api/auth/resend-verification` | `{email}` | `202 {"ok":true}` |
| POST | `/api/auth/login` | `{email, password}` | `200 {"ok":true,"user":{...}}` + sets the `aurastudy_session` cookie |
| POST | `/api/auth/logout` | — | `200 {"ok":true}` + clears the cookie |
| GET | `/api/auth/me` | — | `200 {"user":{...},"spotify_connected":bool}` or `401` |
| POST | `/api/auth/forgot-password` | `{email}` | `202 {"ok":true}` |
| POST | `/api/auth/reset-password` | `{token, password}` | `200 {"ok":true}` |
| POST | `/api/auth/change-password` | `{current_password, new_password}` (login required) | `200 {"ok":true}`; revokes every other session |

`register`, `forgot-password` and `resend-verification` always return the same generic success response whether or not the email exists (enumeration resistance). Login failures: `invalid_credentials` (401), `email_unverified` (403), `rate_limited` (429), `validation_error` (400).

### State API — `/api/state` (login required)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/state` | — | `200 {"payload": <object or null>, "version": <int>, "updated_at": <iso or null>}` |
| PUT | `/api/state` | `{"payload": {...}, "version": <int last known>}` | `200 {"ok":true,"version":<new>,"updated_at":…}`, or `409 {"error":"conflict","payload":…,"version":…}` on a version mismatch |

`payload` must be a JSON object under 1 MB (`413 payload_too_large` otherwise); the server stores it opaquely and doesn't interpret its contents. A first-ever write with no prior state succeeds against `version: 0`.

### Spotify API — `/api/spotify` (login required)

| Method | Path | Response |
|---|---|---|
| GET | `/api/spotify/status` | `{"configured":bool,"connected":bool,"display_name":str\|null,"product":str\|null,"premium":bool}` |
| GET | `/api/spotify/login` | 302 to Spotify's authorize URL (PKCE flow) |
| GET | `/api/spotify/callback` | Exchanges the code, then 302 to `/?spotify=connected` or `/?spotify=error&reason=…` |
| POST | `/api/spotify/disconnect` | `{"ok":true}` |
| GET | `/api/spotify/token` | `{"access_token":…, "expires_in":…}` — short-lived, for the Web Playback SDK; refreshes transparently if needed. Never returns the refresh token. |
| GET | `/api/spotify/playlists` | `{"items":[{id,name,image,tracks,uri}]}` |
| GET | `/api/spotify/now-playing` | `{"is_playing":bool,"track":{...}\|null,"device":{...}\|null}` |
| GET | `/api/spotify/devices` | `{"items":[{id,name,type,is_active}]}` |
| PUT | `/api/spotify/play` | body `{"context_uri"?, "device_id"?}` → `{"ok":true}` |
| PUT | `/api/spotify/pause` | `{"ok":true}` |
| POST | `/api/spotify/next` | `{"ok":true}` |
| POST | `/api/spotify/previous` | `{"ok":true}` |
| PUT | `/api/spotify/volume` | body `{"percent": 0-100}` → `{"ok":true}` |

Spotify errors are passed through consistently: `403 {"error":"premium_required"}` when the account isn't Premium, `404 {"error":"no_active_device"}` when nothing is playing anywhere, and `409 {"error":"spotify_not_connected"}` when the user hasn't linked Spotify at all.

---

## Running the tests

```bash
.venv/bin/python -m pytest -q
```

Tests spin up the Flask app against a temporary SQLite database per test, with `REQUIRE_EMAIL_VERIFICATION=true` and the mailer monkeypatched so no real email is ever sent — messages are captured in memory instead. Spotify's own API is never hit in tests; `requests` calls are mocked.

---

## Security notes

This is a personal project that has had one deliberate security-hardening pass, not an independently audited product. The list below is meant to be accurate, not reassuring — read the "What isn't covered" half too, especially before deciding what a compromised account could see.

### What's protected

- **Password hashing**: PBKDF2-HMAC-SHA256, 240,000 iterations, a random 16-byte salt per password, stored as `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`. Verification uses a constant-time comparison (`hmac.compare_digest`).
- **Breached-password rejection**: registration, password reset, and password change all check the candidate password against the [Have I Been Pwned](https://haveibeenpwned.com/API/v3#PwnedPasswords) k-anonymity range API — only the first 5 characters of the password's SHA-1 hash are ever sent, never the password or the full hash. If the API is unreachable, the check **fails open** (a warning is logged, but signup/reset isn't blocked) with a 3-second timeout, so an HIBP outage never becomes an AuraStudy outage.
- **Sessions**: the `aurastudy_session` cookie is `HttpOnly`, `SameSite=Lax`, scoped to `/`, and only marked `Secure` when the app is actually being served over HTTPS. The cookie itself is an opaque random token — the server stores only its SHA-256 hash, in a `sessions` table, alongside an expiry (30 days) and user agent, so a stolen database dump doesn't hand over live sessions. A fresh token is issued on every login; logout deletes the row server-side; a password reset or change revokes every *other* session.
- **Email tokens**: verification and password-reset links use single-use, expiring tokens (24h / 1h respectively) generated with `secrets.token_urlsafe(32)` and stored only as a SHA-256 hash. Issuing a new token invalidates any previous unused one of the same purpose for that user.
- **CSRF**: every state-changing `/api/*` request must carry `X-Requested-With: XMLHttpRequest` or it's rejected with `403`. Combined with the cookie's `SameSite=Lax`, this is sufficient for a same-origin JSON API like this one.
- **Rate limiting + login lockout**: failed logins are capped per email (10 per 15 minutes) with an additional exponential backoff/temporary lockout on top for repeated failures on the same account, and registrations are capped per IP (5 per hour) — all tracked in an `auth_attempts` table pruned after 24h. Tripping either returns `429`.
- **Enumeration resistance**: register, forgot-password, and resend-verification all return the same response regardless of whether the email exists.
- **Spotify tokens at rest**: access and refresh tokens are encrypted with Fernet (symmetric AES) using `TOKEN_ENC_KEY` before being written to the database, and `/api/spotify/token` only ever returns the short-lived access token — the refresh token never leaves the server.
- **Database transport**: in production the app talks to Postgres (Neon) over TLS — `sslmode=require` is enforced on `DATABASE_URL` even if you paste a connection string without it. Local dev's SQLite fallback has no network exposure at all (it's a file).
- **Security headers on every response**: `Strict-Transport-Security` (once serving HTTPS), a `Content-Security-Policy` scoped to exactly the external hosts the app actually loads from (Spotify's SDK/API/embed domains — see the caveat below), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` + `frame-ancestors 'none'` (the app itself can't be framed; the Spotify embed is an unaffected child iframe), `Referrer-Policy: strict-origin-when-cross-origin`, and a `Permissions-Policy` that denies everything the app doesn't use while deliberately leaving `picture-in-picture`, `screen-wake-lock` and `autoplay` open for Focus Mode and Spotify. See `server/hardening.py` for the exact policy and the reasoning behind each directive.
- **Passwords, raw tokens and Spotify tokens are never logged.**
- **Runs behind gunicorn in production**, not Flask's development server — the dev server is explicitly not designed for production traffic (concurrency, security posture) and is only ever used locally via `./run.sh`. Debug mode and Flask's auto-reloader are hardcoded off and cannot be re-enabled by any environment variable, even accidentally.

### What isn't covered — residual risks

- **The Content-Security-Policy allows `'unsafe-inline'`** for scripts and styles. `index.html` uses inline `<script>`/`<style>` blocks and roughly 40 inline `onclick`/`onchange`/`oninput` handler attributes; a CSP nonce or hash only ever covers `<script>` *tags*, not inline event-handler *attributes*, so there's no CSP-native way to keep those working under a strict policy. In practice this means the CSP's script/style restrictions are much weaker defense-in-depth than a fully strict CSP would be — if an attacker ever found a way to inject markup into the page, `'unsafe-inline'` means injected inline JS could still run. Closing this gap requires rewriting `index.html`'s inline handlers into addEventListener-based code, which hasn't been done.
- **No CAPTCHA or bot-mitigation on registration.** The per-IP rate limit (5 registrations/hour) bounds a single source but doesn't stop registration abuse spread across many IPs.
- **Proxy trust is single-hop by configuration, not by verification.** `ProxyFix(x_for=1, ...)` correctly stops a client from spoofing its own `X-Forwarded-For` *as long as Render's edge really is the only path to this process* — true on Render, but if this app were ever hosted somewhere that also puts a proxy in front of Render (or run directly reachable from the internet), that assumption breaks and IP-based rate limiting could be bypassed again.
- **No automated dependency-vulnerability scanning.** `pip-audit` was run once by hand against `requirements.txt` (see that file's header comment for the findings); nothing re-runs it in CI on new commits or dependency bumps, so a newly-disclosed CVE in a pinned dependency won't be caught automatically.
- **The app's security ultimately extends to the accounts that run it.** Anyone who gains access to the Render, Neon, or Brevo dashboards used in [Deployment](#deployment) can read the production database, rotate `SECRET_KEY`/`TOKEN_ENC_KEY`, or intercept outgoing email, regardless of how well the application code itself behaves. **Turn on two-factor authentication on all three accounts** — this is arguably more consequential than any of the code-level protections above.
- This has not had independent penetration testing or a third-party security review. Treat everything above as "a reasonable, deliberate effort," not a guarantee — no software is unhackable, and this app doesn't claim to be.

---

## Project structure

```
Study Timer/
├── index.html                  # the app itself: timer, courses, sessions, todos, badges, Mochi, charts, themes
├── requirements.txt
├── runtime.txt                 # pins the Python version Render builds with (3.12)
├── gunicorn.conf.py            # production WSGI server config (Render only; local dev uses run.sh)
├── render.yaml                 # Render Blueprint: service definition + every required env var
├── run.sh                      # creates .venv, installs deps, copies .env.example -> .env, runs the server
├── .env.example
├── .gitignore
├── README.md
├── LICENSE
├── server/
│   ├── __init__.py
│   ├── app.py                  # app factory, blueprint registration, page routes (/, /login, /verify, ...)
│   ├── config.py                # env loading -> Config singleton; refuses to boot in production with unsafe config
│   ├── db.py                    # SQLite (dev) or Postgres/Neon (prod) connection, schema, timestamp helpers
│   ├── hardening.py             # proxy trust (ProxyFix), security headers, CSP, MAX_CONTENT_LENGTH
│   ├── security.py              # password hashing, tokens, sessions, login_required, CSRF, rate limiting, Fernet
│   ├── auth.py                  # blueprint 'auth'    -> /api/auth/*
│   ├── mailer.py                # SMTP send + dev-outbox fallback
│   ├── state.py                 # blueprint 'state'   -> /api/state
│   ├── spotify.py               # blueprint 'spotify' -> /api/spotify/*
│   ├── dev_outbox/               # emails written here when SMTP_HOST is empty (git-ignored)
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── register.html
│       ├── forgot.html
│       ├── reset.html
│       └── message.html         # generic success/error page (used by /verify)
├── static/
│   ├── auth.css                 # styling for the auth pages
│   ├── auth.js                  # auth page form handling
│   ├── sync.js                  # server<->localStorage state sync helpers
│   └── spotify.js               # Spotify panel logic
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_state.py
    └── test_spotify.py
```

---

## Deployment

This section takes AuraStudy from "runs on my machine" to a real HTTPS site anyone can sign up to, using only free tiers: **Render** (hosting), **Neon** (Postgres), **Brevo** (email), and a free **`aurastudy.is-a.dev`** subdomain. None of it is required to use the app locally — skip this whole section if `./run.sh` is all you need.

Do the steps in order; several later steps need values copied from earlier ones. Budget 30–45 minutes for a first pass, plus a review-wait for step 7.

### 1. Push the repo to GitHub

This machine has no `gh` CLI and no git credentials already configured, so you'll authenticate with either a **Personal Access Token** (simplest) or an **SSH key**.

1. On [github.com](https://github.com/new), create a new **empty** repository (don't check "Add a README" — this project already has one). Note its URL, e.g. `https://github.com/<you>/aurastudy.git`.
2. Authenticate, pick one:
   - **Personal Access Token (HTTPS)** — go to [github.com/settings/tokens](https://github.com/settings/tokens) → **Generate new token (classic)** → check the `repo` scope → **Generate token** → copy it now (GitHub only shows it once). When `git push` later prompts for a username/password, enter your GitHub username and paste the token as the password.
   - **SSH key** — run `ssh-keygen -t ed25519 -C "you@example.com"` (accept the default file location), then `cat ~/.ssh/id_ed25519.pub` and paste the output at [github.com/settings/keys](https://github.com/settings/keys) → **New SSH key**. Use the `git@github.com:...` form of the repo URL below instead of the HTTPS one.
3. Push:

   ```bash
   cd "/Users/namanarora/Downloads/Projects/Study Timer"
   git remote add origin https://github.com/<you>/aurastudy.git   # or the git@ SSH URL
   git branch -M main
   git push -u origin main
   ```

   `.env` and every `*.db` (plus `*.db-wal`/`*.db-shm`) are git-ignored — they hold your secrets and your actual study data. Run `git status` first if you're at all unsure your working tree is clean; nothing under those patterns should show as about to be committed.

### 2. Create the Neon Postgres database

Neon's free tier doesn't expire (unlike Render's own free Postgres, which is deleted after 30 days), which is why the app is wired for it.

1. Go to [neon.tech](https://neon.tech) and sign up (GitHub login is the fastest path).
2. **Create a project** — name it `aurastudy`, pick any region (ideally close to wherever you pick for Render in step 5).
3. Neon creates a default database and role for you automatically. On the project dashboard, click **Connect** and copy the **connection string** — it looks like:

   ```
   postgresql://<user>:<password>@<host>.neon.tech/<dbname>?sslmode=require
   ```

4. Keep that string somewhere safe for a minute — it becomes `DATABASE_URL` in step 5. (If the string Neon gives you is missing `sslmode=require`, don't worry — `server/config.py` adds it automatically; Neon requires TLS either way.) Neon offers both a *direct* and a *pooled* connection string — prefer the **direct** one, since the app already manages its own small connection pool (`server/db.py`); either works, but stacking two pools is redundant.

### 3. Create the Brevo account and find your SMTP credentials

1. Go to [brevo.com](https://www.brevo.com) and sign up for a free account (verify your email).
2. In the dashboard, open **SMTP & API** (under your account menu → *Senders, Domains & Dedicated IPs*, or search for it directly).
3. Open the **SMTP** tab. Brevo shows you:
   - **SMTP server**: `smtp-relay.brevo.com`
   - **Port**: `587`
   - **Login**: your Brevo account email
   - **Password**: click **Generate a new SMTP key** if none exists yet, then copy it immediately — this is a generated key, not your Brevo account password.
4. The free plan sends **300 emails/day**. That's ample for a personal deployment (each signup/reset is one email) but worth knowing if you ever invite a lot of people at once.
5. Map these to env vars for step 5:

   ```
   SMTP_HOST=smtp-relay.brevo.com
   SMTP_PORT=587
   SMTP_USER=<your Brevo login email>
   SMTP_PASSWORD=<the SMTP key you generated, not your account password>
   SMTP_FROM=AuraStudy <no-reply@aurastudy.is-a.dev>
   SMTP_USE_TLS=true
   ```

   Brevo may ask you to verify the sending domain/address behind `SMTP_FROM` (under **Senders**) before it will relay mail from it — if so, either verify `aurastudy.is-a.dev` there (you won't have it yet until step 7 — use your own email as `SMTP_FROM` initially and switch later) or verify your own email address as a sender and use that instead.

### 4. Generate `SECRET_KEY` and `TOKEN_ENC_KEY`

Both are required in production — `server/config.py` refuses to boot without them (see [Configuration](#configuration)). Run these from the project root, using the project's own virtualenv so `cryptography` is guaranteed to be installed:

```bash
cd "/Users/namanarora/Downloads/Projects/Study Timer"

# SECRET_KEY -- Flask's session-signing secret. Any 64 hex chars of real randomness works.
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"

# TOKEN_ENC_KEY -- MUST be a real Fernet key (not just a random string). The app
# validates this at boot and refuses to start if it isn't one.
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run each command once, copy its output, and paste the two values into Render in the next step (not into your local `.env` — those are for production only). Store them somewhere durable (a password manager) — losing `TOKEN_ENC_KEY` after users have connected Spotify means every stored Spotify token becomes permanently undecryptable and those users have to reconnect.

### 5. Deploy on Render

1. Go to [render.com](https://render.com) and sign up (GitHub login is easiest — it also simplifies connecting the repo).
2. **New** → **Blueprint** → connect the GitHub repo from step 1. Render reads this repo's `render.yaml` and proposes a single web service, `aurastudy`.
3. Before or right after creating it, Render will prompt you to fill in every env var marked `sync: false` in `render.yaml`. Set them all:

   | Env var | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string from step 2 |
   | `SECRET_KEY` | from step 4 |
   | `TOKEN_ENC_KEY` | from step 4 |
   | `APP_BASE_URL` | for now, the `https://aurastudy.onrender.com`-style URL Render assigns (visible on the service page) — you'll change this to the custom domain in step 7 |
   | `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | from step 3 |
   | `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | from your Spotify app (see [Setting up Spotify](#setting-up-spotify)) — or leave blank for now and fill in later; the Music tab just shows "not configured" until you do |

4. Deploy. Watch the build logs — `pip install -r requirements.txt` runs, then gunicorn starts. The dashboard's **Logs** tab is where you'll see the app's own boot messages (or a `ProductionConfigError` if any of the above was missed — the error message says exactly which var and how to fix it).
5. Once live, open the Render URL — you should land on `/login`.

### 6. Update the Spotify app's redirect URI

1. Back in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), open your app's **Settings**.
2. Under **Redirect URIs**, **add** (don't replace) a second entry:

   ```
   https://<your-render-or-custom-domain>/api/spotify/callback
   ```

   using whatever `APP_BASE_URL` is currently set to in Render. Spotify allows multiple redirect URIs on one app, so keep the existing local one too:

   ```
   http://127.0.0.1:5055/api/spotify/callback
   ```

   so local development keeps working unchanged.
3. **Save**. If you change `APP_BASE_URL` again later (e.g. after step 7 switches to the custom domain), come back and add the new URI here too — it must match exactly, character for character.

### 7. Claim the `aurastudy.is-a.dev` subdomain

This is a community-run free-subdomain service ([is-a.dev](https://is-a.dev)); you get the domain by opening a pull request against their registry, which a human reviewer merges.

1. **Fork** [github.com/is-a-dev/register](https://github.com/is-a-dev/register) (top-right **Fork** button, not just clone).
2. In your fork, create a new file at `domains/aurastudy.json`. Match the shape the registry actually uses (this is a real, current example from that repo, `domains/0.json` — the schema is `owner` + `records`, note the **plural** `records`):

   ```json
   {
     "owner": {
       "username": "<your-github-username>",
       "email": "naman@aihifusion.com"
     },
     "records": {
       "CNAME": "aurastudy.onrender.com"
     }
   }
   ```

   Use the exact hostname Render assigned your service (visible on the Render service page, e.g. `aurastudy-xxxx.onrender.com`) as the `CNAME` target — not the custom domain itself, since that's what you're in the middle of creating.
3. In the **Render** dashboard, go to your service → **Settings** → **Custom Domains** → **Add Custom Domain** → enter `aurastudy.is-a.dev`. Render will show it as "pending" until the CNAME above actually resolves; that's expected at this point.
4. Commit the file in your fork and open a **pull request** against `is-a-dev/register`'s `main` branch. A maintainer reviews it — keep an eye on the PR for review comments and respond promptly if changes are requested.

   The registry's own docs explicitly warn that AI-generated registration requests often get the schema wrong and delay approval — the example above matches a real, current file in their repo as a starting point, but before you submit, skim [docs.is-a.dev](https://docs.is-a.dev) yourself in case the process has changed since this was written, and don't submit on faith alone.
5. Once merged, DNS publishes within a few minutes. Back in Render, the custom domain should flip to "verified" and Render issues it a free TLS certificate automatically.
6. Finally, in Render's env vars, update `APP_BASE_URL` to `https://aurastudy.is-a.dev` and redeploy, then repeat step 6 above (add `https://aurastudy.is-a.dev/api/spotify/callback` as a Spotify redirect URI too).

### 8. First-deploy checks

Once everything above is wired up:

1. Open the live URL and **register your own account**.
2. Check your actual inbox for the verification email (check spam the first time — a brand-new Brevo sending domain sometimes lands there initially). This is the single most important check: production has no dev-outbox fallback (`server/config.py` refuses to boot if `SMTP_HOST` is unset), so a real email arriving confirms Brevo is actually wired up correctly end to end, not just configured.
3. Click the verification link, then **log in**.
4. Confirm the browser shows **HTTPS** (padlock icon) — this matters beyond "looks secure": the floating always-on-top timer window, browser notifications, and the screen wake lock (all part of Focus Mode) are browser APIs that most browsers only grant to secure (HTTPS) origins. On plain HTTP they'll silently fail to work.

### Cold starts (free tier)

Render's free web services **sleep after ~15 minutes with no traffic** and take **about 50 seconds to wake up** on the next request. The first request after a quiet period will hang for that long before the login page appears — this is expected free-tier behaviour, not a bug. Don't add a keep-alive pinger to work around it; that defeats the point of the free tier and Render may act on services that do it. If the wait becomes a real problem, Render's paid tiers remove it.

---

## License

MIT — see [`LICENSE`](./LICENSE). Copyright (c) 2026 Naman Arora.
