"""Source-level guards for the post-launch UX pass."""
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_leaderboard_has_daily_weekly_toggle_and_no_auto_open_name_editor():
    html = _read("index.html")
    assert 'id="lb-period-day"' in html
    assert 'id="lb-period-week"' in html
    assert "setLeaderboardPeriod('day')" in html
    assert "leaderboardNameEditorAutoOpened" not in html
    assert "You are shown as" in html
    assert "function onTimerKeyboardShortcut" in html
    assert "function syncFullscreenIcon" in html
    assert "function celebrateNewUnlocks" in html
    assert "function openPetGallery" in html
    assert "mSum / 3600" in html
    assert "aurastudy_theme" in html
    assert "Reach at " in html
    assert "text-decoration: none" in html
    assert "hue-sun" in html and "trophyHueClass" in html
    assert "viewIn" in html
    sessions_block = html[html.index("VIEW: SESSIONS LOG"):html.index("VIEW: ACHIEVEMENTS")]
    assert "<th>Mode</th>" not in sessions_block
    assert "<th>Duration</th>" in sessions_block


def test_spotify_timer_bar_is_horizontal():
    js = _read("static", "spotify.js")
    assert ".tmp-now-playing{display:flex;align-items:center" in js
    assert "tmp-popover-stack" in js
    assert "overflow-wrap:anywhere" in js
    assert "as-now-bar" in js


def test_spotify_connect_flow_copy_and_oauth_toast():
    js = _read("static", "spotify.js")
    assert "Extended Quota" in js
    assert "spotifyErrorMessage" in js
    assert "Couldn’t Connect Spotify" in js
    assert "signed into AuraStudy" in js
    assert "Request Access" in js


def test_active_view_is_persisted_and_restored_after_bootstrap():
    html = _read("index.html")
    assert "ACTIVE_VIEW_STORAGE_KEY = 'aurastudy_active_view'" in html
    assert "function persistActiveView" in html
    assert "function restorePersistedActiveView" in html
    assert "persistActiveView(targetPanelKey)" in html
    assert "restorePersistedActiveView()" in html
    assert "GUEST_LOCKED_VIEWS" in html
    assert "'leaderboard'" not in html[html.index("GUEST_LOCKED_VIEWS"):html.index("GUEST_LOCKED_VIEWS") + 80]
    assert "leaderboardRankMedal" in html
    assert "pet-journey-stage" in html
    assert "Ant" in html
    assert "Lion" in html
    assert "trophy-medal-emoji" in html
    assert "maybePromptGuestLogin" in _read("static", "guest.js")
    assert "initGuestExperience" in _read("static", "guest.js")
    assert "initGuestExperience()" in html
    assert "function isViewAccessible" in html
    assert 'id="app-boot-overlay"' in html
    assert "boot-pending" in html
    assert "function finishAppBoot" in html
    assert "finishAppBoot()" in html


def test_reset_flow_uses_branded_confirm_modal_not_window_confirm():
    html = _read("index.html")
    assert "function showAuraConfirmDialog" in html
    assert 'id="aura-confirm-modal"' in html
    reset_block = html[html.index("function resetEngineDisplayState"):html.index("function sessionSignature")]
    assert "window.confirm(" not in reset_block
    assert "showAuraConfirmDialog" in reset_block
    assert "mode: 'reset'" in reset_block
    assert "Yes logs your study time then resets" in reset_block
    assert "choice === null" in reset_block
    assert "saveEngineWorkspaceBlockData()" in reset_block


def test_theme_and_timer_colors_live_in_settings_not_sidebar():
    html = _read("index.html")
    sidebar_footer = html[html.index('<div class="sidebar-footer">'):html.index('id="nav-item-logout"')]
    assert "theme-switch-row" not in sidebar_footer
    assert "user-profile" not in sidebar_footer
    assert 'id="user-email-display"' not in sidebar_footer
    assert "theme-btn-pink" not in sidebar_footer
    assert "settings-theme-btn-pink" in html
    assert "settings-theme-btn-blue" in html
    assert 'id="settings-ambient-picker"' not in html
    assert 'id="settings-break-duration"' in html
    assert "Default Break Length (Minutes)" in html
    assert "function stepSettingsField" in html
    assert "number-stepper-row" in html
    assert "stepSettingsField('settings-break-duration'" in html
    assert "-moz-appearance: textfield" in html
    timer_view = html[html.index('<!-- VIEW: TIMER -->'):html.index("<!-- VIEW: COURSES -->")]
    assert "ambient-selector-bar" in timer_view
    assert 'id="timer-colour-control-wrap"' in timer_view
    assert 'id="timer-colour-trigger-btn"' in timer_view
    assert 'id="timer-colour-popover"' in timer_view
    assert 'data-ambient-key="pink-mintrefresh"' in html
    assert 'data-ambient-key="blue-mintrefresh"' in html
    assert "function migrateAmbientKey" in html
    assert "function timerGradCssVar" in html
    root_block = html[html.index(":root {"):html.index("/* Blueberry Frost")]
    assert root_block.count("--timer-grad-") == 14
    blue_theme_block = html[html.index(":root[data-theme=\"blue\"]"):html.index("/* Theme switch control")]
    assert "--timer-grad-" not in blue_theme_block
    assert "--ambient-grad-" not in blue_theme_block


def test_timer_break_length_editable_on_break_tab():
    html = _read("index.html")
    assert 'id="timer-break-stepper"' in html
    assert 'id="timer-break-stepper-minus"' in html
    assert 'id="timer-break-stepper-plus"' in html
    assert 'id="timer-break-stepper-input"' in html
    input_snippet = html[html.index('<input type="number" class="timer-break-stepper-value'):html.index('<input type="number" class="timer-break-stepper-value') + 250]
    assert 'id="timer-break-stepper-input"' in input_snippet
    assert 'type="number"' in input_snippet
    assert "function stepTimerBreakMinutes" in html
    assert "function applyBreakMinutes" in html
    assert "function commitTimerBreakMinutesFromInput" in html
    assert "function syncTimerBreakStepperVisibility" in html
    timer_view = html[html.index('<!-- VIEW: TIMER -->'):html.index("<!-- VIEW: COURSES -->")]
    assert 'id="timer-break-stepper"' in timer_view
    assert 'id="timer-break-stepper-input"' in timer_view
    step_body = html[html.index("function stepTimerBreakMinutes"):html.index("function refreshAutoBreakLabel")]
    assert "isEngineActivelyRunning" in step_body
    commit_body = html[html.index("function commitTimerBreakMinutesFromInput"):html.index("function applyBreakMinutes")]
    assert "isEngineActivelyRunning" in commit_body
    assert "applyBreakMinutes" in commit_body
    disabled_body = html[html.index("function updateTimerBreakStepperDisabled"):html.index("function commitTimerBreakMinutesFromInput")]
    assert "timer-break-stepper-input" in disabled_body
    assert "inputEl.disabled" in disabled_body


def test_custom_404_template_exists():
    app_py = _read("server", "app.py")
    template = _read("server", "templates", "404.html")
    assert "404.html" in app_py
    assert "Page not found" in template
    assert 'href="/app"' in template


def test_timer_meta_has_transparent_backdrop_not_dark_chip():
    html = _read("index.html")
    assert 'class="timer-meta" id="timer-start-timestamp"' in html
    assert ".timer-fullscreen-view.dark-mode-active .timer-header-badge,\n        .timer-fullscreen-view.dark-mode-active .timer-meta" not in html
    dark_meta = html[
        html.index(".timer-fullscreen-view.dark-mode-active .timer-meta"):
        html.index(".timer-fullscreen-view.dark-mode-active .timer-meta") + 320
    ]
    assert "rgba(0, 0, 0" not in dark_meta
    assert "background: transparent !important" in dark_meta
    assert "var(--text-muted)" in html[html.index(".timer-fullscreen-view:not(.dark-mode-active) .timer-meta"):html.index(".timer-fullscreen-view:not(.dark-mode-active) .timer-meta") + 120]


def test_timer_view_hides_global_header():
    html = _read("index.html")
    assert 'id="global-header"' in html
    assert "function syncGlobalHeaderForView" in html
    assert "timer-view-active" in html
    assert "body.timer-view-active #global-header" in html
    switch_body = html[html.index("function switchView"):html.index("function getActiveEngineModeKey")]
    assert "syncGlobalHeaderForView(targetPanelKey)" in switch_body


def test_sidebar_brand_navigates_to_dashboard_and_help_uses_question_icon():
    html = _read("index.html")
    assert 'id="sidebar-brand-home"' in html
    assert "switchView('dashboard'" in html[html.index('id="sidebar-brand-home"'):html.index('id="sidebar-brand-home"') + 320]
    assert 'data-lucide="help-circle"' in html[html.index('id="nav-item-help-toggle"'):html.index('id="nav-item-help-toggle"') + 220]
    assert 'data-lucide="life-buoy"' not in html[html.index('id="nav-item-help-toggle"'):html.index('id="nav-item-help-toggle"') + 220]


def test_session_admin_chip_shares_date_row():
    html = _read("index.html")
    assert ".session-date-cell" in html
    sessions_body = html[html.index("function renderSessionsTable"):html.index("function clearAllLogs")]
    assert 'class="session-date-cell"' in sessions_body
    assert "createElement('br')" not in sessions_body


def test_timer_target_cluster_centered_with_more_gap():
    html = _read("index.html")
    cluster_block = html[html.index(".timer-secondary-cluster {"):html.index(".timer-header-badge {")]
    badge_block = html[html.index(".timer-header-badge {"):html.index(".timer-fullscreen-view:not(.dark-mode-active) .timer-header-badge")]
    assert "gap: 16px" in cluster_block
    assert "align-self: center" in badge_block
    assert "width: fit-content" in badge_block


def test_sidebar_collapse_toggle_persisted():
    html = _read("index.html")
    assert "aurastudy_sidebar_collapsed" in html
    assert "sidebar-collapsed" in html
    assert 'id="sidebar-collapse-toggle"' in html
    assert "function toggleSidebarCollapsed" in html
    assert "function applySidebarCollapsedState" in html
    assert "localStorage.getItem('aurastudy_sidebar_collapsed')" in html
    sidebar_block = html[html.index('id="app-sidebar"'):html.index("<!-- Main Workspace Container -->")]
    brand_snippet = html[
        html.index('id="sidebar-brand-home"') : html.index('id="sidebar-brand-home"') + 320
    ]
    assert 'title="Dashboard"' not in brand_snippet
    assert 'aria-label="Go to Dashboard"' in brand_snippet
    assert 'title="Dashboard"' in sidebar_block
    assert 'title="Timer"' in sidebar_block
    assert "html.sidebar-collapsed .nav-item > span:not(.nav-unread-badge)" in html
    brand_span_block = html[html.index(".brand span {") : html.index(".brand span {") + 260]
    assert "-webkit-background-clip: text" in brand_span_block
    assert "html.sidebar-collapsed .brand span" in html
    collapsed_brand_span = html[
        html.index("html.sidebar-collapsed .brand span")
        : html.index("html.sidebar-collapsed .brand span") + 80
    ]
    assert "display: none" in collapsed_brand_span
    collapsed_brand_row = html[
        html.index("html.sidebar-collapsed .sidebar-brand-row")
        : html.index("html.sidebar-collapsed .brand span")
    ]
    assert "gap: 8px" in collapsed_brand_row
    assert "margin-bottom: 8px" in collapsed_brand_row


def test_switchview_wrap_opens_float_before_original_when_leaving_timer():
    """PiP must open on the nav gesture before heavy switchView work runs."""
    pip_js = _read("static", "pip.js")
    assert "suppressPipClose" in pip_js
    m = re.search(r'wrapGlobalFn\("switchView", function \(original, thisArg, args\) \{', pip_js)
    assert m, "switchView wrap not found"
    start = m.end()
    depth = 1
    i = start
    while i < len(pip_js) and depth:
        if pip_js[i] == "{":
            depth += 1
        elif pip_js[i] == "}":
            depth -= 1
        i += 1
    body = pip_js[start : i - 1]
    float_idx = body.index("maybeFloatOnLeavingTimer()")
    original_idx = body.index("original.apply(thisArg, args)")
    assert float_idx < original_idx, "maybeFloatOnLeavingTimer must run before original.apply"
    assert "leavingTimer" in body
    assert "STATE.suppressPipClose = true" in body
    assert re.search(r"STATE\.suppressPipClose\s*=\s*false", body)


def test_close_pip_if_timer_view_respects_suppress_flag():
    pip_js = _read("static", "pip.js")
    m = re.search(r"function closePipIfTimerViewVisible\(\)\s*\{", pip_js)
    assert m
    start = m.end()
    depth = 1
    i = start
    while i < len(pip_js) and depth:
        if pip_js[i] == "{":
            depth += 1
        elif pip_js[i] == "}":
            depth -= 1
        i += 1
    body = pip_js[start : i - 1]
    assert "STATE.suppressPipClose" in body
    assert body.index("STATE.suppressPipClose") < body.index("closeFloatingWindow()")


def test_change_engine_mode_wrap_skips_cleanup_on_cancelled_switch():
    pip_js = _read("static", "pip.js")
    m = re.search(r'wrapGlobalFn\("changeEngineMode", function \(original, thisArg, args\) \{', pip_js)
    assert m
    start = m.end()
    depth = 1
    i = start
    while i < len(pip_js) and depth:
        if pip_js[i] == "{":
            depth += 1
        elif pip_js[i] == "}":
            depth -= 1
        i += 1
    body = pip_js[start : i - 1]
    assert "wasRunning" in body
    assert "isEngineActivelyRunning" in body
    assert "typeof result.then" in body
    assert "endSessionCleanup()" in body
    assert "wasRunning && !isEngineActivelyRunning" in body
