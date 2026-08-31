"""Source-level regression guards for the study-time-logging reliability
fixes (owner report: "a lot of the time isn't getting logged... that time is
just lost").

There is no JS test runner in this project (no Node/npm, per SPEC.md's
environment constraints -- everything is plain browser JS loaded via
<script> tags -- see tests/test_frontend_security.py's docstring for the
same caveat), so these tests can't exercise the DOM or a real browser event
loop. Instead they assert on the actual source text of static/sync.js and
index.html: that the buggy pattern is gone and the fix's specific mechanism
is present. This proves the code exists and is wired up; it does NOT prove
it behaves correctly at runtime (a closed-tab race, an actual 409, a real
sleep/wake cycle) -- that was verified manually against a locally-run server
in a real browser, tracked separately in the fix report. A future edit that
silently reintroduces one of these exact bugs is what this guards against.
"""
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------- defect: UTC dates


def test_local_date_helper_exists():
    html = _read("index.html")
    assert "function getLocalDateStr(" in html
    # Must be true LOCAL calendar math (getFullYear/getMonth/getDate), not a
    # wrapper that still bottoms out in UTC.
    assert "d.getFullYear()" in html
    assert "d.getMonth()" in html
    assert "d.getDate()" in html


def test_no_more_utc_date_bucketing_in_index_html():
    """The exact bug: `new Date().toISOString().split('T')[0]` (and the same
    pattern on an arbitrary Date `d`) is the UTC calendar date, not the
    user's local one -- anything studied late at night (or early morning,
    depending on the offset's sign) got filed under the wrong day for every
    daily total, streak, badge, and chart. None of that pattern should
    remain as CODE anywhere in the app (it's fine for a comment to still
    name the old pattern while explaining why it was replaced)."""
    html = _read("index.html")
    code_lines = [
        line for line in html.splitlines()
        if "//" not in line.split("toISOString")[0] if "toISOString" in line
    ]
    offenders = [line for line in code_lines if "toISOString().split(" in line or "toISOString()[" in line]
    assert offenders == [], "UTC-date bucketing pattern still present as code: {}".format(offenders)


def test_session_log_date_uses_local_date():
    html = _read("index.html")
    assert re.search(r"date:\s*getLocalDateStr\(\)", html), (
        "saveEngineWorkspaceBlockData must date a newly-logged session with "
        "the user's LOCAL calendar day, not the UTC one"
    )


def test_dashboard_today_and_streaks_and_badges_use_local_date():
    html = _read("index.html")
    # Dashboard "today" total.
    assert "const today = getLocalDateStr();" in html
    # Badges/achievements "today" total.
    assert "const todayStr = getLocalDateStr();" in html
    # Streak-dot loop and the running-streak walk-back both compare against
    # a LOCAL date string, not a UTC one, for every day they check.
    assert "const checkStr = getLocalDateStr(d);" in html
    assert "const sStr = getLocalDateStr(checkDate);" in html
    assert "sStr === getLocalDateStr(today)" in html


def test_leaderboard_local_date_plumbed_to_server():
    """The client's local "today" must reach the server on both the write
    (PUT /api/state, via static/sync.js) and the reads (GET
    /api/leaderboard, PUT /api/leaderboard/opt) that bucket by week -- see
    server/leaderboard.py:current_week_start's docstring for why the two
    must agree."""
    html = _read("index.html")
    sync_js = _read("static", "sync.js")

    assert "local_date=" in html and "getLocalDateStr()" in html  # GET /api/leaderboard query param
    assert "local_date: getLocalDateStr()" in html  # PUT /api/leaderboard/opt body
    assert "local_date" in sync_js  # PUT /api/state body (see putServerState)
    assert "localDateParam" in sync_js


def test_leaderboard_server_accepts_and_bounds_client_local_date():
    lb = _read("server", "leaderboard.py")
    assert "_parse_client_local_date" in lb
    assert "_MAX_LOCAL_DATE_SKEW_DAYS" in lb
    # current_week_start must be able to take a client-reported date and fall
    # back to the server's own UTC date otherwise.
    assert re.search(r"def current_week_start\(local_date=None\)", lb)


def test_admin_time_corrections_do_not_reinterpret_existing_utc_dates():
    """The fix must not migrate/reinterpret dates already stored under the
    old UTC convention -- there's no time-of-day recorded to recover a true
    local date from. admin.py's own date handling (unrelated to this fix,
    owner-facing) is untouched and still server-UTC-anchored."""
    admin = _read("server", "admin.py")
    assert "utcnow().date()" in admin


# ------------------------------------------------- defect: nothing flushes on close


def test_sync_js_flushes_on_pagehide_and_hidden():
    js = _read("static", "sync.js")
    assert "addEventListener(\"pagehide\"" in js or "addEventListener('pagehide'" in js
    assert "visibilitychange" in js
    assert "document.hidden" in js


def test_sync_js_uses_keepalive_fetch_not_sendbeacon_for_teardown():
    """navigator.sendBeacon cannot set custom request headers, and this
    app's CSRF check (server/security.py require_csrf) requires
    X-Requested-With on every state-mutating request with no beacon
    exception -- so a beacon-based flush would be silently rejected with a
    403 server-side. fetch(..., {keepalive: true}) is the one API that both
    survives page teardown and allows the header."""
    js = _read("static", "sync.js")
    assert "navigator.sendBeacon(" not in js  # the doc comment above may still name it, explaining why it's not used
    assert "keepalive" in js


def test_sync_js_csrf_header_present_on_every_fetch():
    js = _read("static", "sync.js")
    assert "X-Requested-With" in js
    assert "XMLHttpRequest" in js
    assert "credentials: \"same-origin\"" in js or "credentials: 'same-origin'" in js


def test_session_logged_flushes_immediately_not_debounced():
    """A completed session is exactly the moment durability matters most --
    it must not sit in the normal 2s debounce window."""
    html = _read("index.html")
    assert "AuraSync.flush()" in html


# ------------------------------------------------- defect: silent local-data loss


def test_sync_js_persists_a_pending_flag_across_reloads():
    """The old bootstrap() unconditionally overwrote localStorage with
    whatever the server had, any time the server had ANY payload -- even if
    a previous page life's push had failed (offline) and never confirmed.
    That silently discarded the only copy of the unsynced edit on next load.
    A persisted pending flag is what lets bootstrap() tell "fully synced"
    apart from "an edit from last time never confirmed" and merge instead of
    overwrite."""
    js = _read("static", "sync.js")
    assert "aurastudy_sync_meta_v1" in js
    assert "pendingSince" in js
    assert "markSynced" in js
    assert "markPending" in js


def test_sync_js_retries_on_reconnect():
    js = _read("static", "sync.js")
    assert "addEventListener(\"online\"" in js or "addEventListener('online'" in js


def test_bootstrap_merges_pending_local_edits_instead_of_overwriting():
    js = _read("static", "sync.js")
    # The pending branch inside bootstrap() must go through mergePayloads,
    # not a bare writeLocalPayload(stateData.payload) the way the old
    # unconditional-adopt path does for the CLEAN (non-pending) case.
    assert re.search(r"if \(meta\.pendingSince !== null\)[\s\S]{0,800}mergePayloads\(", js)


# ------------------------------------------------------ defect: 409 discards data


def test_conflict_merges_sessions_instead_of_blind_overwrite():
    """The old 409 handler adopted the server's payload/version and then
    retried the PUT with the SAME STALE LOCAL PAYLOAD -- i.e. it overwrote
    whatever the server had (another tab, another device, or an owner-added
    time correction -- see server/admin.py) with a payload that, by
    definition, didn't know about it. That is a silent data-loss path, just
    triggered from the opposite direction of the other bugs here."""
    js = _read("static", "sync.js")
    assert "function mergePayloads(" in js
    assert "function handleConflict(" in js
    # The union must be keyed so real sessions from either side survive.
    assert "sessionMergeKey" in js
    assert "clientId" in js


def test_conflict_merge_updates_the_live_in_memory_app_too():
    """A mid-session conflict resolved by merging must not leave the
    CURRENTLY OPEN tab's own rendered view silently stale until reload."""
    js = _read("static", "sync.js")
    html = _read("index.html")
    assert "applyMergedSyncPayloadToAppState" in js
    assert "window.applyMergedSyncPayloadToAppState = function" in html


def test_sessions_get_a_stable_client_id_for_exact_deduping():
    html = _read("index.html")
    assert "clientId:" in html
    assert "function unionMissingSessions(" in html
    assert "function sessionMergeKey(" in html


def test_two_tabs_do_not_silently_overwrite_each_others_sessions():
    """localStorage is one shared bucket per browser origin, not per tab --
    logging a session must fold in whatever another open tab already wrote
    before this tab overwrites the whole blob."""
    html = _read("index.html")
    assert "function mergeSessionsFromOtherTabs(" in html
    assert re.search(r"mergeSessionsFromOtherTabs\(\);[\s\S]{0,200}appState\.sessions\.unshift", html)


# -------------------------------------------------- defect: running timer loss


def test_running_timer_is_persisted():
    html = _read("index.html")
    assert "aurastudy_running_timer_v1" in html
    assert "function persistRunningTimerSnapshot(" in html
    assert "function clearRunningTimerSnapshot(" in html
    # Persisted on the actual state transitions, not just a stray call.
    assert "persistRunningTimerSnapshot()" in html


def test_running_timer_recovery_exists_and_runs_on_boot():
    html = _read("index.html")
    assert "function attemptRunningTimerRecovery(" in html
    assert "attemptRunningTimerRecovery();" in html


def test_recovery_uses_last_heartbeat_not_wall_clock_now():
    """The reconstructed elapsed time for a closed/crashed tab must be
    computed from the last confirmed-alive heartbeat (`savedAtMs`), not
    Date.now() -- otherwise the entire time the tab was closed gets counted
    as if the timer had kept running, silently inventing study time. Also
    pins a bug found in manual browser verification: the gap must be
    Math.floor()'d -- an un-floored fractional-second value flowed into
    `secs` in updateEngineDisplayString and rendered the timer as literally
    "00:10.775" instead of "00:10" after a recovery."""
    html = _read("index.html")
    assert re.search(r"confirmedElapsed = banked \+ Math\.floor\(Math\.max\(0, \(snapshot\.savedAtMs - anchor\)", html)


def test_recovery_caps_and_prompts_for_implausibly_long_sessions():
    """'a session running for 14 hours because a laptop was shut overnight
    needs a sane cap and a prompt rather than a silent N-hour log' -- pinned
    literally: a cap constant, and a confirm() gate before anything is
    logged for the implausible case."""
    html = _read("index.html")
    assert "MAX_PLAUSIBLE_RECOVERY_SECONDS" in html
    assert "window.confirm(" in html
    assert re.search(r"confirmedElapsed <= MAX_PLAUSIBLE_RECOVERY_SECONDS", html)


def test_recovery_never_auto_logs_a_session_without_a_decision_point():
    """The plausible-recovery branch must restore the timer (paused) for the
    user to see and act on -- not silently create a session entry on its
    own. Only the capped/implausible branch ever writes to
    appState.sessions, and only after window.confirm() returns true."""
    html = _read("index.html")
    m = re.search(
        r"if \(confirmedElapsed <= MAX_PLAUSIBLE_RECOVERY_SECONDS\) \{([\s\S]*?)\n            \} else \{",
        html,
    )
    assert m, "expected the plausible-recovery branch to be present"
    plausible_branch = m.group(1)
    assert "appState.sessions.unshift" not in plausible_branch
    assert "Resume Block" in plausible_branch


def test_gap_guard_excludes_sleep_suspend_time_from_the_live_timer():
    """Same failure mode, but for a timer that's still running IN THE SAME
    PAGE LIFE after the device wakes from sleep (no reload involved) --
    syncEngineRegistersFromClock must exclude an unobserved gap rather than
    rolling the wall-clock anchor straight through it."""
    html = _read("index.html")
    assert "MAX_UNOBSERVED_GAP_SECONDS" in html
    assert "lastSyncClockMs" in html
    assert re.search(r"\(nowMs - lastSyncClockMs\) / 1000 > MAX_UNOBSERVED_GAP_SECONDS", html)
