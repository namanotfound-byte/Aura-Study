"""Guards for countdown reset, log burst, reset dialog, and session dedupe fixes."""
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def _extract_function_body(html, func_name):
    marker = "function " + func_name
    start = html.index(marker)
    brace = html.index("{", start)
    depth = 1
    i = brace + 1
    while i < len(html) and depth:
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
        i += 1
    return html[brace + 1 : i - 1]


def test_countdown_stepper_locks_on_start_and_pause():
    html = _read("index.html")
    assert "function isCountdownStepperLocked" in html
    lock_body = _extract_function_body(html, "isCountdownStepperLocked")
    assert "isEngineActivelyRunning" in lock_body
    assert "runningAccumulatedSeconds" in lock_body
    assert "bankedElapsedSeconds" in lock_body
    disabled_body = _extract_function_body(html, "updateTimerCountdownStepperDisabled")
    assert "isCountdownStepperLocked()" in disabled_body
    toggle_body = _extract_function_body(html, "toggleEngineExecutionLoop")
    assert toggle_body.count("updateTimerCountdownStepperDisabled()") >= 2
    step_body = _extract_function_body(html, "stepTimerCountdownMinutes")
    assert "isEngineActivelyRunning" in step_body


def test_reset_dialog_has_three_explicit_study_actions():
    html = _read("index.html")
    assert 'id="aura-confirm-third"' in html
    reset_block = html[html.index("function resetEngineDisplayState"):html.index("function sessionSignature")]
    assert "mode: 'reset'" in reset_block
    assert "Reset — log time" in reset_block
    assert "Reset — don't log time" in reset_block
    assert "choice === 'log'" in reset_block
    assert "choice === 'discard'" in reset_block
    assert "mode: 'reset-break'" in reset_block
    init_block = html[html.index("(function initAuraConfirmDialog()"):html.index("function switchView")]
    assert "closeAuraConfirmDialog('log')" in init_block or "auraConfirmMode === 'reset' ? 'log'" in init_block
    assert "closeAuraConfirmDialog('discard')" in init_block


def test_log_button_reentrancy_and_immediate_reset():
    html = _read("index.html")
    assert 'id="timer-btn-log"' in html
    assert "engineLogSaveInFlight" in html
    assert "engineBlockLoggedThisSession" in html
    save_body = _extract_function_body(html, "saveEngineWorkspaceBlockData")
    assert re.search(r"if\s*\(\s*engineLogSaveInFlight\s*\|\|\s*engineBlockLoggedThisSession\s*\)\s*return", save_body)
    assert "updateTimerLogButtonDisabled()" in save_body
    assert '"Logged"' in save_body
    assert "resetEngineDisplayState(true)" in save_body
    reset_body = _extract_function_body(html, "resetEngineDisplayState")
    assert "finalizeEngineResetDisplay()" in reset_body
    finalize_body = _extract_function_body(html, "finalizeEngineResetDisplay")
    assert "countdownSecondsRemainingRegister" not in finalize_body
    assert "syncTimerBreakStepperVisibility()" in finalize_body
    assert "updateTimerCountdownStepperDisabled" in html[html.index("function syncTimerBreakStepperVisibility"):html.index("function updateTimerBreakStepperDisabled") + 400]


def test_burst_dedupe_on_load_merge_and_sync():
    html = _read("index.html")
    assert "function sessionBurstSignature" in html
    assert "function dedupeBurstSessionsInPlace" in html
    union_body = _extract_function_body(html, "unionMissingSessions")
    assert "dedupeBurstSessionsInPlace()" in union_body
    load_body = _extract_function_body(html, "loadStateFromLocalStorageRegister")
    assert "dedupeBurstSessionsInPlace()" in load_body
    apply_block = html[html.index("window.applyMergedSyncPayloadToAppState"):html.index("function saveEngineWorkspaceBlockData")]
    assert "dedupeBurstSessionsInPlace()" in apply_block
    sync_js = _read("static", "sync.js")
    assert "function sessionBurstSignature" in sync_js
    assert "function dedupeBurstSessions" in sync_js
    assert "dedupeBurstSessions(baseSessions.concat(missing))" in sync_js


def test_countdown_reset_restores_configured_focus_length():
    html = _read("index.html")
    reset_body = _extract_function_body(html, "resetEngineDisplayState")
    assert re.search(
        r"countdownTotalSeconds = wholeSeconds\(appState\.profile\.defaultTimerMinutes \* 60,\s*25 \* 60\)",
        reset_body,
    )
    assert "countdownSecondsRemainingRegister = countdownTotalSeconds" in reset_body
    assert "finalizeEngineResetDisplay()" in reset_body
