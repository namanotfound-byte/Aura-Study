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
    assert "updateTimerTargetBadge()" in save_body
    assert "renderTimerTargetMenu()" in save_body
    reset_body = _extract_function_body(html, "resetEngineDisplayState")
    assert "finalizeEngineResetDisplay()" in reset_body
    assert "function forceWriteEngineDisplayAfterReset" in html
    force_body = _extract_function_body(html, "forceWriteEngineDisplayAfterReset")
    assert 'getElementById(\'timer-display\')' in force_body or 'getElementById("timer-display")' in force_body
    assert "clearRunningTimerSnapshot()" in force_body
    assert "updateEngineDisplayString()" in force_body
    assert "requestAnimationFrame" in force_body
    finalize_body = _extract_function_body(html, "finalizeEngineResetDisplay")
    assert "forceWriteEngineDisplayAfterReset()" in finalize_body
    assert "syncTimerBreakStepperVisibility()" in finalize_body
    assert "updateTimerTargetBadge()" in finalize_body
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
    force_body = _extract_function_body(html, "forceWriteEngineDisplayAfterReset")
    assert "countdownSecondsRemainingRegister = countdownTotalSeconds" in force_body


def test_fullscreen_reparents_confirm_and_toast_into_timer_container():
    html = _read("index.html")
    assert "function reparentTimerOverlaysForFullscreen" in html
    assert "function restoreTimerOverlaysFromFullscreen" in html
    assert "function syncFullscreenOverlays" in html
    reparent_body = _extract_function_body(html, "reparentTimerOverlaysForFullscreen")
    assert "aura-confirm-modal" in reparent_body
    assert "toast-alert" in reparent_body
    assert "timer-view-container" in reparent_body
    assert "syncFullscreenOverlays()" in html[html.index("function syncFullscreenIcon"):html.index("function toggleNativeWindowFullscreenEngine")]
    assert "reparentTimerOverlaysForFullscreen()" in _extract_function_body(html, "showAuraConfirmDialog")
    assert "reparentTimerOverlaysForFullscreen()" in _extract_function_body(html, "triggerAlertToast")


def test_selected_course_persists_across_log_reset_and_load():
    html = _read("index.html")
    assert "LAST_TARGET_STORAGE_KEY = 'aurastudy_last_target'" in html
    assert "function persistLastTargetCourse" in html
    assert "function resolveSelectedCourseOnLoad" in html
    select_body = _extract_function_body(html, "selectTimerTargetCourse")
    assert "persistLastTargetCourse(courseName)" in select_body
    assert "AuraSync.flush()" in select_body
    load_body = _extract_function_body(html, "loadStateFromLocalStorageRegister")
    assert "resolveSelectedCourseOnLoad(parsed)" in load_body
    save_body = _extract_function_body(html, "saveEngineWorkspaceBlockData")
    assert "persistLastTargetCourse(appState.selectedCourse)" in save_body
    sync_js = _read("static", "sync.js")
    merge_block = sync_js[sync_js.index("function mergePayloads"):sync_js.index("function notifyAppOfMerge")]
    assert "aurastudy_last_target" in merge_block
    assert "merged.selectedCourse = base.selectedCourse" in merge_block
