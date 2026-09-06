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
    assert "as-now-bar" in js


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
    sidebar_footer = html[html.index('<div class="sidebar-footer">'):html.index('<div class="user-profile">')]
    assert "theme-switch-row" not in sidebar_footer
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


def test_sidebar_collapse_toggle_persisted():
    html = _read("index.html")
    assert "aurastudy_sidebar_collapsed" in html
    assert "sidebar-collapsed" in html
    assert 'id="sidebar-collapse-toggle"' in html
    assert "function toggleSidebarCollapsed" in html
    assert "function applySidebarCollapsedState" in html
    assert "localStorage.getItem('aurastudy_sidebar_collapsed')" in html
    sidebar_block = html[html.index('id="app-sidebar"'):html.index("<!-- Main Workspace Container -->")]
    assert 'title="Dashboard"' in sidebar_block
    assert 'title="Timer"' in sidebar_block
    assert "html.sidebar-collapsed .nav-item > span:not(.nav-unread-badge)" in html
