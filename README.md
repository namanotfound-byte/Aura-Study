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
- [Deploying / pushing to GitHub](#deploying--pushing-to-github)
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

- **Python 3.9+** (the code targets 3.9 syntax specifically).
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
| `SECRET_KEY` | random, generated per boot | Flask's session-signing secret. A random key means Flask's signed session cookie (used only for the brief Spotify OAuth handshake) won't survive a restart. Set this explicitly for anything beyond local dev. |
| `APP_BASE_URL` | `http://127.0.0.1:5055` | Base URL the app is served from. Used to build the links in verification/reset emails and to build the Spotify OAuth redirect URI. Use the literal loopback IP for local dev, not `localhost`. |
| `PORT` | `5055` | Port the dev server listens on. (Port 5000 is reserved by macOS AirPlay Receiver — don't use it.) |
| `DATABASE_PATH` | `<project root>/aurastudy.db` | Path to the SQLite database file. |
| `TOKEN_ENC_KEY` | auto-generated into `.env` on first run | Fernet key used to encrypt Spotify access/refresh tokens at rest. You normally never set this by hand — the app generates and persists one for you the first time it boots without one. |
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

- **Password hashing**: PBKDF2-HMAC-SHA256, 240,000 iterations, a random 16-byte salt per password, stored as `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`. Verification uses a constant-time comparison (`hmac.compare_digest`).
- **Sessions**: the `aurastudy_session` cookie is `HttpOnly`, `SameSite=Lax`, scoped to `/`, and only marked `Secure` when `APP_BASE_URL` is `https`. The cookie itself is an opaque random token — the server stores only its SHA-256 hash, in a `sessions` table, alongside an expiry (30 days) and user agent, so a stolen database dump doesn't hand over live sessions. A fresh token is issued on every login; logout deletes the row server-side.
- **Email tokens**: verification and password-reset links use single-use, expiring tokens (24h / 1h respectively) generated with `secrets.token_urlsafe(32)` and stored only as a SHA-256 hash. Issuing a new token invalidates any previous unused one of the same purpose for that user.
- **CSRF**: every state-changing `/api/*` request must carry `X-Requested-With: XMLHttpRequest` or it's rejected with `403`. Combined with the cookie's `SameSite=Lax`, this is sufficient for a same-origin JSON API like this one.
- **Rate limiting**: failed logins are capped per email (10 per 15 minutes) and registrations are capped per IP (5 per hour), both tracked in an `auth_attempts` table and pruned after 24h; both return `429 {"error":"rate_limited"}` once tripped.
- **Enumeration resistance**: register, forgot-password, and resend-verification all return the same response regardless of whether the email exists.
- **Spotify tokens at rest**: access and refresh tokens are encrypted with Fernet (symmetric AES) using `TOKEN_ENC_KEY` before being written to SQLite, and `/api/spotify/token` only ever returns the short-lived access token — the refresh token never leaves the server.
- **Passwords, raw tokens and Spotify tokens are never logged.**
- **This runs Flask's built-in development server** (`app.run(...)`), which is explicitly not designed for production traffic. If you ever deploy AuraStudy somewhere reachable by the public internet, put it behind a real WSGI server (gunicorn, uWSGI, waitress, …) and HTTPS — don't expose the dev server directly.

---

## Project structure

```
Study Timer/
├── index.html                  # the app itself: timer, courses, sessions, todos, badges, Mochi, charts, themes
├── requirements.txt
├── run.sh                      # creates .venv, installs deps, copies .env.example -> .env, runs the server
├── .env.example
├── .gitignore
├── README.md
├── LICENSE
├── server/
│   ├── __init__.py
│   ├── app.py                  # app factory, blueprint registration, page routes (/, /login, /verify, ...)
│   ├── config.py                # env loading -> Config singleton
│   ├── db.py                    # sqlite3 connection, schema, timestamp helpers
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

## Deploying / pushing to GitHub

`.env` and every `*.db` (plus `*.db-wal` / `*.db-shm`) are git-ignored — they hold your secrets and your actual study data, and must never be committed. Double check `git status` before your first push if you're not sure your working tree is clean.

```bash
cd "/Users/namanarora/Downloads/Projects/Study Timer"
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

If you ever run this somewhere other than your own machine, remember the [security notes](#security-notes) above: set a real `SECRET_KEY`, put it behind HTTPS, and don't run the Flask dev server directly in production.

---

## License

MIT — see [`LICENSE`](./LICENSE). Copyright (c) 2026 Naman Arora.
