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
    assert "resetEngineDisplayState(true, { keepLogLock: engineBlockLoggedThisSession })" in save_body
    assert "updateTimerTargetBadge()" in save_body
    assert "renderTimerTargetMenu()" in save_body
    assert "armEngineTickMute" in save_body
    assert "forceClearRunningTimerSnapshot()" in save_body
    reset_body = _extract_function_body(html, "resetEngineDisplayState")
    assert "finalizeEngineResetDisplay(options)" in reset_body
    assert "function paintFreshIdleClock" in html
    paint_body = _extract_function_body(html, "paintFreshIdleClock")
    assert 'getElementById(\'timer-display\')' in paint_body or 'writeTimerDisplayFromWholeSeconds' in paint_body
    assert "forceClearRunningTimerSnapshot()" in paint_body
    finalize_body = _extract_function_body(html, "finalizeEngineResetDisplay")
    assert "paintFreshIdleClock()" in finalize_body
    assert "syncTimerBreakStepperVisibility()" in finalize_body
    assert "updateTimerTargetBadge()" in finalize_body
    assert "options.keepLogLock" in finalize_body
    assert re.search(
        r"if\s*\(\s*!options\.keepLogLock\s*\)\s*\{\s*engineBlockLoggedThisSession = false;",
        finalize_body,
    )
    assert "resetEngineDisplayState(true, { keepLogLock: engineBlockLoggedThisSession })" in save_body
    toggle_body = _extract_function_body(html, "toggleEngineExecutionLoop")
    assert re.search(
        r"engineBlockLoggedThisSession = false;\s*updateTimerLogButtonDisabled\(\);",
        toggle_body,
    )
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
    assert "finalizeEngineResetDisplay(options)" in reset_body
    paint_body = _extract_function_body(html, "paintFreshIdleClock")
    assert re.search(
        r"countdownTotalSeconds = wholeSeconds\(appState\.profile\.defaultTimerMinutes \* 60,\s*25 \* 60\)",
        paint_body,
    )
    assert "countdownSecondsRemainingRegister = countdownTotalSeconds" in paint_body
    assert "writeTimerDisplayFromWholeSeconds(countdownSecondsRemainingRegister)" in paint_body


def test_log_path_clears_running_timer_snapshot_unconditionally():
    html = _read("index.html")
    save_body = _extract_function_body(html, "saveEngineWorkspaceBlockData")
    duration_pos = save_body.index("const finalDuration = wholeSeconds(runningAccumulatedSeconds)")
    zero_pos = save_body.index("runningAccumulatedSeconds = 0")
    clear_pos = save_body.index("forceClearRunningTimerSnapshot()")
    mute_pos = save_body.index("armEngineTickMute")
    assert duration_pos < zero_pos
    assert zero_pos < clear_pos
    assert clear_pos < mute_pos
    assert "RUNNING_TIMER_STORAGE_KEY" in html
    assert "function forceClearRunningTimerSnapshot" in html


def test_log_captures_duration_then_zeros_stopwatch_display_before_persist():
    html = _read("index.html")
    save_body = _extract_function_body(html, "saveEngineWorkspaceBlockData")
    duration_pos = save_body.index("const finalDuration = wholeSeconds(runningAccumulatedSeconds)")
    display_pos = save_body.index("writeTimerDisplayFromWholeSeconds(0)")
    merge_pos = save_body.index("mergeSessionsFromOtherTabs()")
    assert duration_pos < display_pos
    assert display_pos < merge_pos
    assert re.search(
        r"if\s*\(\s*appState\.selectedMode === 'countdown'\s*\)\s*\{[\s\S]*writeTimerDisplayFromWholeSeconds\(countdownSecondsRemainingRegister\);[\s\S]*\}\s*else\s*\{\s*writeTimerDisplayFromWholeSeconds\(0\);\s*\}",
        save_body,
    )
    lock_pos = save_body.index("engineBlockLoggedThisSession = true")
    reset_pos = save_body.index("resetEngineDisplayState(true, { keepLogLock: engineBlockLoggedThisSession })")
    assert lock_pos < reset_pos
    assert save_body.index("engineBlockLoggedThisSession = true") < save_body.index("mergeSessionsFromOtherTabs()")


def test_app_html_response_has_no_store_cache_control():
    app_py = _read("server", "app.py")
    assert '"Cache-Control": "no-store"' in app_py or "'Cache-Control': 'no-store'" in app_py


def test_engine_tick_handler_bails_when_muted_or_not_running():
    html = _read("index.html")
    tick_body = _extract_function_body(html, "engineTickHandler")
    assert re.search(r"if\s*\(\s*!isEngineActivelyRunning\s*\|\|\s*isEngineTickMuted\(\)\s*\)\s*return", tick_body)


def test_paint_fresh_idle_clock_stopwatch_shows_zero():
    html = _read("index.html")
    paint_body = _extract_function_body(html, "paintFreshIdleClock")
    assert "writeTimerDisplayFromWholeSeconds(0)" in paint_body
    assert "runningAccumulatedSeconds = 0" in paint_body
    assert "bankedElapsedSeconds = 0" in paint_body


def test_persist_keep_guard_skipped_on_log_reset():
    html = _read("index.html")
    persist_body = _extract_function_body(html, "persistRunningTimerSnapshot")
    assert "isEngineTickMuted()" in persist_body
    assert "options.forceClear" in persist_body
    assert "forceClearRunningTimerSnapshot()" in persist_body
    recovery_body = _extract_function_body(html, "attemptRunningTimerRecovery")
    assert "isEngineTickMuted()" in recovery_body
    flush_body = _extract_function_body(html, "flushRunningTimerOnPageLeave")
    assert "isEngineTickMuted()" in flush_body


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


def test_load_state_preserves_recovered_timer_on_second_call():
    html = _read("index.html")
    load_body = _extract_function_body(html, "loadStateFromLocalStorageRegister")
    assert "preserveTimerRegisters" in load_body
    assert "runningTimerRecoveryCompleted" in load_body
    assert re.search(r"if \(!preserveTimerRegisters\)", load_body)


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


def test_timer_stage_nav_menu_exists_for_tablet_escape():
    html = _read("index.html")
    assert 'id="timer-nav-menu-toggle"' in html
    assert "timer-nav-menu-btn" in html
    assert "body.timer-view-active .timer-nav-menu-btn" in html
    assert "max-width: 1024px" in html[html.index("body.timer-view-active .timer-nav-menu-btn"):html.index("body.timer-view-active .timer-nav-menu-btn") + 200]
    assert "toggleMobileNav()" in html[html.index('id="timer-nav-menu-toggle"'):html.index('id="timer-nav-menu-toggle"') + 280]
    assert "syncMobileNavToggleUi" in html


def test_merge_payloads_rejects_factory_default_course_clobber():
    sync_js = _read("static", "sync.js")
    assert 'FACTORY_DEFAULT_COURSES = ["Math", "Physics", "Chemistry", "Literature"]' in sync_js
    assert "function coursesAreFactoryDefault(" in sync_js
    assert "function mergeCoursesField(" in sync_js
    merge_block = sync_js[sync_js.index("function mergePayloads"):sync_js.index("function notifyAppOfMerge")]
    assert "mergeCoursesField(" in merge_block
    assert "shouldSkipFactoryDefaultPush" in sync_js


def test_account_state_persist_gated_until_hydrate():
    html = _read("index.html")
    assert "let accountHydrated = false" in html
    assert "function canPersistAccountState()" in html
    assert "window.isAccountHydrated" in html
    save_body = _extract_function_body(html, "saveStateToLocalStorageRegister")
    assert "canPersistAccountState()" in save_body
    sync_js = _read("static", "sync.js")
    assert "function canSyncToServer(" in sync_js
    assert "window.isAccountHydrated" in sync_js
    assert "if (!canSyncToServer()) return;" in sync_js


def test_light_ambient_timer_target_uses_dark_text():
    html = _read("index.html")
    light_block = html[html.index(".timer-fullscreen-view:not(.dark-mode-active) .timer-target-trigger {"):html.index(".timer-fullscreen-view.dark-mode-active .timer-target-trigger {")]
    assert "color: #1C1816" in light_block
    assert "#fff !important" not in light_block
