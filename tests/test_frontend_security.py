"""Source-level regression guards for frontend fixes made during the
pre-deployment security review.

There is no JS test runner in this project (no Node/npm, per SPEC.md's
environment constraints -- everything is plain browser JS loaded via
<script> tags), so these tests can't exercise the DOM directly. Instead they
assert on the actual source text of the fixed files: that the vulnerable
pattern is gone and the fix's specific mechanism is present. Weaker than a
real browser-level XSS test (which was run manually against a live server as
part of the review -- see the report), but it stops a future edit from
silently reintroducing the exact bug found here.
"""
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------- XSS


def test_index_html_defines_an_html_escaping_helper():
    html = _read("index.html")
    assert "function escapeHtml(" in html


def test_todo_text_is_escaped_before_innerhtml():
    """Stored XSS: appState.todoItems[].text is free-form text the signed-in
    user types and which now syncs to their account (server/state.py), and
    it used to be interpolated into an innerHTML template literal completely
    unescaped -- `<span class="todo-text-span">${item.text}</span>`. A todo
    item like `<img src=x onerror=...>` would execute as script in that
    user's own session (and, via social-engineering "paste this todo to
    unlock a secret" bait, in a victim's session) every time the list
    rendered, with full access to that session's authenticated fetch calls."""
    html = _read("index.html")
    assert "${item.text}" not in html
    assert "${escapeHtml(item.text)}" in html


def test_course_name_is_escaped_and_not_interpolated_into_an_onclick_attribute():
    """Stored XSS (two sinks in one template): appState.courses entries are
    free-form text from the "add course" field. They were interpolated raw
    into innerHTML text (`${c}`) AND, worse, into an inline onclick handler's
    JS-string-literal argument (`onclick="deleteExisting...('${c}')"`) --
    a course name containing a single quote could break out of that argument
    and inject arbitrary JS directly into the handler, no HTML-entity
    encoding even needed to get script execution. The fix escapes the
    display text and moves the delete action to addEventListener bound to
    the original (never HTML/JS-string-serialized) course-name variable."""
    html = _read("index.html")
    assert "deleteExistingCourseTargetDomain('${c}')" not in html
    assert "${escapeHtml(c)}" in html
    assert "addEventListener('click', () => deleteExistingCourseTargetDomain(c))" in html


def test_session_table_course_is_escaped():
    """Same appState.courses value, second render site: the session-history
    table stamped it into a row via innerHTML unescaped."""
    html = _read("index.html")
    assert "${s.course}</span>" not in html
    assert "${escapeHtml(s.course)}</span>" in html


def test_spotify_js_still_escapes_untrusted_fields():
    """static/spotify.js already had its own esc() helper for
    Spotify-account-controlled strings (display name, playlist name/id/uri,
    image URL) before this review -- confirm that control wasn't
    incidentally weakened while everything else in this file was audited."""
    js = _read("static", "spotify.js")
    assert "function esc(" in js
    assert "esc(p.name)" in js
    assert "esc(p.id)" in js
    assert "esc(s.display_name" in js or "esc(s.display_name || '')" in js


# ------------------------------------------------------------ open redirect


def test_login_next_param_is_validated_before_use():
    """Open redirect (and, via a `javascript:` value, reflected same-origin
    script execution): auth.js used to hand the `?next=` query param on
    /login straight to `window.location.href` after a successful login with
    no validation at all -- `window.location.href = params.get("next") ||
    "/"`. A link like `/login?next=https://evil.example/phish` would send a
    victim who just entered their real credentials straight to a phishing
    site; `/login?next=javascript:...` would execute attacker script in the
    app's own origin immediately after a genuine login. The fix only ever
    honours a root-relative, same-origin path."""
    js = _read("static", "auth.js")
    assert 'window.location.href = params.get("next") || "/"' not in js
    assert "function safeNextPath(" in js
    assert "window.location.href = safeNextPath(params.get(\"next\"))" in js


def test_safe_next_path_rejects_absolute_and_protocol_relative_urls():
    """Executes the actual safeNextPath() regex (extracted from the source
    rather than re-implemented) against the URLs the fix is meant to reject,
    plus a few it must still accept, so a future tweak to the regex itself
    is caught even though this suite can't run real browser JS."""
    js = _read("static", "auth.js")
    # The invalid-path fallback is "/app" (not "/") since SPEC-PHASE4.md's
    # routing change moved the app from "/" to "/app" -- "/" is now the
    # unauthenticated landing page, so falling back to it after a successful
    # login would just show the arrival animation again instead of the app.
    match = re.search(r"if \(!(\/\^.*?\/)\.test\(raw\)\) return \"/app\";", js)
    assert match, "could not find the safeNextPath validation regex in static/auth.js"
    pattern = match.group(1)[1:-1]  # strip the JS regex literal's slashes

    def accepted(path):
        return re.match(pattern, path) is not None

    for bad in [
        "https://evil.example/phish",
        "//evil.example/phish",
        "/\\evil.example",
        "javascript:alert(1)",
        "http://evil.example",
    ]:
        assert not accepted(bad), "safeNextPath should reject {!r}".format(bad)

    for good in ["/", "/dashboard", "/settings?tab=focus"]:
        assert accepted(good), "safeNextPath should accept {!r}".format(good)
