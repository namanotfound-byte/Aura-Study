"""Source-level regression guards for the "timer rendered as a fractional
decimal after sleep/wake" production bug and its wider bug class.

Background (see the commit "Fix timer display blowing up after sleep, and
confirm before reset"): the owner's laptop slept with a stopwatch running,
and on wake the timer rendered as `.503000000000...` filling the screen. The
immediate cause was `updateEngineDisplayString()` doing
`secs.toString().padStart(2,'0')` on a value that was never actually a whole
number of seconds -- millisecond clock-delta arithmetic (`msDelta / 1000`)
had leaked a fraction into it. That commit floored the display itself and
one recovery-path source. This file guards the REST of that bug class: every
other place a fractional/NaN/Infinity/negative value could reach
runningAccumulatedSeconds, bankedElapsedSeconds,
countdownSecondsRemainingRegister or countdownTotalSeconds, and the
`durationSeconds` written into a logged session.

There is no JS test runner in this project (no Node/npm as a project
dependency, per SPEC.md's environment constraints -- everything is plain
browser JS loaded via <script> tags -- see tests/test_frontend_security.py's
docstring for the same caveat), so these tests can't execute the timer
engine in a real event loop. Instead they parse the actual source text of
index.html and static/pip.js: structurally, not by grepping for one known
line, so a future edit that reintroduces an unguarded assignment to one of
the timer registers -- even a NEW one, not just the exact lines fixed here
-- fails the audit below. This proves the guarding mechanism exists and is
applied at every current write site; it does NOT prove the arithmetic is
correct at runtime for every real sleep/wake timing -- that was verified
manually against a locally-run server with a simulated clock jump (see the
fix report).
"""
import os
import re

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TIMER_REGISTERS = [
    "runningAccumulatedSeconds",
    "bankedElapsedSeconds",
    "countdownSecondsRemainingRegister",
    "countdownTotalSeconds",
]

# Local variables that are themselves proven-safe elsewhere in the same
# function (established by a dedicated test below) and are therefore an
# acceptable bare RHS for a register assignment without re-wrapping.
SAFE_LOCAL_ALIASES = {"countdownTotal", "confirmedElapsed"}


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def _extract_function_body(source, func_name):
    """Return the `{ ... }` body of a top-level `function funcName(...) {`
    declaration, found by counting braces from the opening one so an inner
    `if { ... }` block can't be mistaken for the end of the function."""
    m = re.search(r"function\s+" + re.escape(func_name) + r"\s*\([^)]*\)\s*\{", source)
    assert m, "function {} not found in source".format(func_name)
    start = m.end()
    depth = 1
    i = start
    while depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return source[start:i - 1]


def _register_assignments(body, register):
    """All `register = <expr>;` assignments in `body` (not `==`/`===`
    comparisons, not `let`/`const` declarations -- callers pass a function
    body, which never contains the top-level `let` declarations)."""
    return re.findall(
        re.escape(register) + r"\s*=(?!=)\s*(.*?);",
        body,
        re.DOTALL,
    )


def _assert_all_assignments_safe(body, func_name):
    """The actual audit: every assignment to every timer register inside
    `body` must be a numeric literal, a call that contains `wholeSeconds(`,
    or a bare reference to one of SAFE_LOCAL_ALIASES (a local var separately
    proven to have been passed through wholeSeconds first)."""
    for register in TIMER_REGISTERS:
        for rhs in _register_assignments(body, register):
            rhs_stripped = rhs.strip()
            is_numeric_literal = re.fullmatch(r"-?\d+(\.\d+)?(\s*\*\s*\d+(\.\d+)?)?", rhs_stripped) is not None
            is_wrapped = "wholeSeconds(" in rhs_stripped
            is_safe_alias = rhs_stripped in SAFE_LOCAL_ALIASES or rhs_stripped in TIMER_REGISTERS
            assert is_numeric_literal or is_wrapped or is_safe_alias, (
                "{}(): assignment `{} = {}` is not provably a whole, "
                "non-negative, finite number of seconds -- wrap it in "
                "wholeSeconds(), or add it to SAFE_LOCAL_ALIASES with a test "
                "proving it's already safe.".format(func_name, register, rhs_stripped)
            )


# ------------------------------------------------------- the guard itself


def test_whole_seconds_helper_exists_and_floors_validates_and_clamps():
    html = _read("index.html")
    body = _extract_function_body(html, "wholeSeconds")
    assert "Math.floor(" in body
    assert "isFinite(" in body
    assert "Math.max(0" in body
    # Must reject non-numbers (typeof), not just NaN specifically -- a
    # corrupt snapshot field can be a string, an object, or missing entirely.
    assert "typeof value !== 'number'" in body or 'typeof value !== "number"' in body


@pytest.mark.parametrize(
    "func_name",
    [
        "syncEngineRegistersFromClock",
        "toggleEngineExecutionLoop",
        "changeEngineMode",
        "resetEngineDisplayState",
        "loadStateFromLocalStorageRegister",
        "attemptRunningTimerRecovery",
    ],
)
def test_every_register_write_in_function_is_provably_whole(func_name):
    """The structural audit, run against every function that's allowed to
    write a timer register. This is what would have caught the exact
    original bug (and does, if reverted -- see
    test_gap_guard_regression_would_be_caught_by_the_audit below): the
    gap-guard branch in syncEngineRegistersFromClock used to assign
    `bankedElapsedSeconds = priorElapsed` where `priorElapsed` was computed
    from un-floored millisecond division, with no wholeSeconds() wrapper and
    no established-safe alias."""
    html = _read("index.html")
    body = _extract_function_body(html, func_name)
    _assert_all_assignments_safe(body, func_name)


def test_gap_guard_regression_would_be_caught_by_the_audit():
    """Directly demonstrates the audit's teeth: replaying the ORIGINAL,
    unfixed gap-guard line against the same checker used above must fail.
    If this test ever fails to fail (i.e. errors because the checker no
    longer rejects the bug), the audit itself has been weakened."""
    buggy_body = (
        "if (x) {\n"
        "    const priorElapsed = bankedElapsedSeconds + Math.max(0, (lastSyncClockMs - engineAnchorMs) / 1000);\n"
        "    bankedElapsedSeconds = (appState.selectedMode === \"countdown\")\n"
        "        ? Math.min(priorElapsed, countdownTotalSeconds)\n"
        "        : priorElapsed;\n"
        "}\n"
    )
    with pytest.raises(AssertionError):
        _assert_all_assignments_safe(buggy_body, "syncEngineRegistersFromClock (simulated pre-fix)")


def test_countdown_total_and_confirmed_elapsed_aliases_are_actually_safe():
    """SAFE_LOCAL_ALIASES lets attemptRunningTimerRecovery assign
    `countdownTotalSeconds = countdownTotal` and
    `bankedElapsedSeconds = confirmedElapsed` without re-wrapping -- this
    pins that those two locals really are only ever produced via
    wholeSeconds() first, so the alias-list isn't a loophole."""
    html = _read("index.html")
    body = _extract_function_body(html, "attemptRunningTimerRecovery")

    # countdownTotal is a ternary of two already-safe things: a wholeSeconds()
    # -derived local, or the register countdownTotalSeconds itself (which is
    # a register, and therefore covered by its own writer-audit test).
    countdown_total_from_snapshot_def = re.search(r"const countdownTotalFromSnapshot = ([^;]*);", body)
    assert countdown_total_from_snapshot_def, "countdownTotalFromSnapshot must be defined via a single const"
    assert "wholeSeconds(" in countdown_total_from_snapshot_def.group(1)

    countdown_total_def = re.search(r"const countdownTotal = ([^;]*);", body)
    assert countdown_total_def, "countdownTotal must be defined via a single const in attemptRunningTimerRecovery"
    assert re.fullmatch(
        r"countdownTotalFromSnapshot > 0 \? countdownTotalFromSnapshot : countdownTotalSeconds",
        countdown_total_def.group(1).strip(),
    ), "countdownTotal must be built only from the wholeSeconds()-derived local or the countdownTotalSeconds register"

    # confirmedElapsed starts as `banked` (itself wholeSeconds()-derived --
    # see the next test) and is re-wrapped in wholeSeconds() before any
    # register reads it.
    assert re.search(r"confirmedElapsed = wholeSeconds\(confirmedElapsed\);", body)
    # ...and that re-wrap must happen BEFORE it's assigned into a register.
    rewrap_pos = body.index("confirmedElapsed = wholeSeconds(confirmedElapsed);")
    first_register_use_pos = min(
        body.index("bankedElapsedSeconds = confirmedElapsed"),
        body.index("runningAccumulatedSeconds = confirmedElapsed"),
    )
    assert rewrap_pos < first_register_use_pos


# ---------------------------------------------- specific mechanism pins


def test_recovery_snapshot_fields_validated_with_isfinite_not_typeof_number():
    """typeof NaN === 'number' is true -- a corrupt/hand-edited snapshot
    with bankedElapsedSeconds or engineAnchorMs set to NaN (or Infinity)
    used to sail past a bare `typeof x === 'number'` check. Number.isFinite
    rejects both."""
    html = _read("index.html")
    body = _extract_function_body(html, "attemptRunningTimerRecovery")
    assert "Number.isFinite(snapshot.savedAtMs)" in body
    assert "Number.isFinite(anchor)" in body
    assert "typeof snapshot.savedAtMs" not in body
    assert "typeof anchor" not in body


def test_recovery_banked_seconds_routed_through_whole_seconds():
    html = _read("index.html")
    body = _extract_function_body(html, "attemptRunningTimerRecovery")
    assert re.search(r"const banked = wholeSeconds\(snapshot\.bankedElapsedSeconds\)", body)


def test_logged_session_duration_is_the_last_line_of_defence():
    """Mirrors the display's own defence-in-depth: durationSeconds is what
    ends up in appState.sessions and from there the leaderboard, the charts
    and the pet -- it must never trust runningAccumulatedSeconds to already
    be a whole number, no matter how many upstream writers claim to floor
    it."""
    html = _read("index.html")
    body = _extract_function_body(html, "saveEngineWorkspaceBlockData")
    assert re.search(r"const finalDuration = wholeSeconds\(runningAccumulatedSeconds\)", body)
    assert re.search(r"durationSeconds:\s*finalDuration", body)


def test_default_timer_minutes_from_profile_cannot_inject_a_bad_countdown_total():
    """appState.profile is overwritten wholesale from whatever JSON was in
    localStorage or synced from the server (loadStateFromLocalStorageRegister
    does `appState.profile = parsed.profile` with no validation) -- a
    corrupted/hand-edited/legacy defaultTimerMinutes (string, float, negative,
    missing) must not reach countdownTotalSeconds unguarded. All four sites
    that derive countdownTotalSeconds from it must go through wholeSeconds()
    with a positive fallback, not a bare `* 60`."""
    html = _read("index.html")
    unguarded = re.findall(r"countdownTotalSeconds = appState\.profile\.defaultTimerMinutes \* 60;", html)
    assert unguarded == [], "found unguarded countdownTotalSeconds derivations: {}".format(unguarded)
    guarded = re.findall(
        r"countdownTotalSeconds = wholeSeconds\(appState\.profile\.defaultTimerMinutes \* 60,\s*25 \* 60\)",
        html,
    )
    assert len(guarded) == 4, "expected all 4 call sites (load, changeEngineMode, start-fresh, reset) to be guarded, found {}".format(len(guarded))


# ------------------------------------------------------------ pip.js: Reset


def test_pip_reset_button_does_not_call_reset_unconditionally():
    """The bug: the floating window's Reset button called
    resetEngineDisplayState() directly, which shows a window.confirm() on
    the MAIN window -- easy to miss entirely when the floating window is on
    top of (or the only thing the user can see over) the main one, making
    Reset look like it silently did nothing."""
    pip_js = _read("static", "pip.js")
    assert 'els.resetBtn.addEventListener("click", function () {\n      resetEngineDisplayState();\n    });' not in pip_js
    assert "els.resetBtn.addEventListener(\"click\", handlePipResetClick)" in pip_js


def test_pip_reset_confirms_inside_the_floating_window_when_something_is_at_stake():
    pip_js = _read("static", "pip.js")
    m = re.search(r"function handlePipResetClick\(\)\s*\{", pip_js)
    assert m, "handlePipResetClick not found"
    start = m.end()
    depth = 1
    i = start
    while depth > 0:
        if pip_js[i] == "{":
            depth += 1
        elif pip_js[i] == "}":
            depth -= 1
        i += 1
    body = pip_js[start:i - 1]

    # Mirrors the same "is there something to lose" threshold as
    # resetEngineDisplayState's own guard (elapsedOnClock >= 60 there).
    assert "elapsedOnClock >= 60" in body
    # The confirm must be shown on the FLOATING window, not the bare global
    # `confirm(...)` (which would be the main window's, the exact bug).
    assert "STATE.pipWindow.confirm" in body or re.search(r"confirmFn\.call\(\s*STATE\.pipWindow", body)
    assert re.search(r"^\s*confirm\(", body, re.MULTILINE) is None
    # Once confirmed inside the floating window, the main window must not
    # show a second, possibly-hidden prompt of its own.
    assert "resetEngineDisplayState(true)" in body
    # A cancelled confirm must return without ever calling reset -- never a
    # silent discard the other way either.
    assert re.search(r"if\s*\(!ok\)\s*return;", body)
