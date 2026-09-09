"""Source-level guards for the post-launch UX pass."""
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_leaderboard_has_daily_weekly_lifetime_toggle_and_no_auto_open_name_editor():
    html = _read("index.html")
    assert 'id="lb-period-day"' in html
    assert 'id="lb-period-week"' in html
    assert 'id="lb-period-lifetime"' in html
    assert "setLeaderboardPeriod('day')" in html
    assert "setLeaderboardPeriod('lifetime')" in html
    assert "No one is ranked yet." in html
    assert "leaderboardNameEditorAutoOpened" not in html
    assert "You are shown as" in html
    assert "function onTimerKeyboardShortcut" in html
    assert "function syncFullscreenIcon" in html
    assert "function celebrateNewUnlocks" in html
    assert "function openPetGallery" in html
    assert "mSum / 3600" in html
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
    assert "function completeBootSequence" in html
    assert "persistActiveView(targetPanelKey)" in html
    assert "restorePersistedActiveView()" in html
    boot_block = html[html.index("function completeBootSequence"):html.index("function completeBootSequence") + 900]
    assert "AuraTour.shouldPlayTour" in boot_block
    assert "restorePersistedActiveView()" in boot_block
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
    assert "Reset — log time" in reset_block
    assert "Reset — don't log time" in reset_block
    assert "choice === null" in reset_block
    assert "choice === 'log'" in reset_block
    assert "saveEngineWorkspaceBlockData()" in reset_block


def test_timer_colors_on_timer_view_not_appearance_theme_in_settings():
    html = _read("index.html")
    sidebar_footer = html[html.index('<div class="sidebar-footer">'):html.index('id="nav-item-logout"')]
    assert "theme-switch-row" not in sidebar_footer
    assert "user-profile" not in sidebar_footer
    assert 'id="user-email-display"' not in sidebar_footer
    assert "theme-btn-pink" not in sidebar_footer
    assert "settings-theme-btn-pink" not in html
    assert "settings-theme-btn-blue" not in html
    assert "applyColourTheme" not in html
    assert "Colour Theme" not in html
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
    assert 'onclick="openTimerColourPopover()"' in timer_view
    assert "function initTimerColourPopover" in html
    assert "z-index: 130" in html[html.index(".timer-colour-control-wrap"):html.index(".timer-colour-control-wrap") + 220]
    assert 'data-ambient-key="pink-mintrefresh"' in html
    assert 'data-ambient-key="blue-mintrefresh"' in html
    assert "function migrateAmbientKey" in html
    assert "function timerGradCssVar" in html
    root_block = html[html.index(":root {"):html.index("/* Branded in-app confirm dialog")]
    assert root_block.count("--timer-grad-") == 14
    assert "/static/brand/aurastudy-mascot.png" in html
    assert "/static/brand/aurastudy-wordmark.png" in html
    assert "--font-serif" in html
    assert "Fraunces" in html
    landing_css = _read("static", "landing.css")
    assert "min-height: 100dvh" in landing_css
    assert ".landing-logo" in landing_css
    landing_html = _read("server", "templates", "landing.html")
    assert landing_html.count("/static/brand/aurastudy-mascot.png") >= 1
    assert landing_html.count("/static/brand/aurastudy-wordmark.png") >= 1
    base_html = _read("server", "templates", "base.html")
    assert "/static/brand/aurastudy-mascot.png" in base_html
    assert "/static/brand/aurastudy-wordmark.png" in base_html


def test_dashboard_matches_mock_layout_single_start_session():
    html = _read("index.html")
    dash = html[html.index('<!-- VIEW: DASHBOARD -->'):html.index('<!-- VIEW: TIMER -->')]
    assert dash.count("Start Session") == 1
    assert 'id="dash-start-session-btn"' in dash
    assert "btn-dash-start" in dash
    assert 'id="header-notify-btn"' in html
    assert 'id="header-notify-badge"' in html
    assert 'id="header-notify-list"' in html
    assert 'function refreshHeaderNotifications' in html
    assert 'id="header-avatar-btn"' not in html
    assert 'id="header-greeting-icon"' in html
    assert "dash-hero-scenery" in dash
    assert "dash-pet-row" in dash
    assert "dash-metrics-row" in dash
    assert "dash-trends-row" in dash
    assert "dash-chart-period-btn" in dash
    assert "setAnalyticsChartPeriod" in html
    assert "type: 'line'" in html or "type:'line'" in html.replace(" ", "")
    header_actions = html[html.index('id="global-header"'):html.index("<!-- VIEW: DASHBOARD -->")]
    assert "Start Session" not in header_actions


def test_countdown_stepper_and_target_picker_exist():
    html = _read("index.html")
    assert 'id="timer-countdown-stepper"' in html
    assert "stepTimerCountdownMinutes" in html
    assert 'id="timer-target-trigger"' in html
    assert "toggleTimerTargetMenu" in html
    assert 'id="timer-subject-pills"' in html
    assert "#timer-subject-pills { display: none" in html


def test_tour_overlay_and_storage_key_exist():
    html = _read("index.html")
    tour_js = _read("static", "tour.js")
    assert "/static/tour.js" in html
    assert "/static/tour.css" in html
    assert "aurastudy_tour_done" in tour_js
    assert "aura-tour-overlay" in tour_js
    assert "hasExistingStudyData" in tour_js
    assert "markTourDoneIfReturningUser" in tour_js
    assert "shouldPlayTour" in tour_js
    assert "syncTimerCountdownStepperVisibility" in tour_js
    assert "initAppTour(appState)" in html
    countdown_step = tour_js[tour_js.index("Countdown length"):tour_js.index("Countdown length") + 1400]
    assert "requestAnimationFrame" in countdown_step
    assert "changeEngineMode('countdown')" in countdown_step
    assert "syncTimerCountdownStepperVisibility()" in countdown_step
    dashboard_step = tour_js[tour_js.index("title: 'Dashboard'"):tour_js.index("title: 'Dashboard'") + 520]
    assert "aurastudy_active_view" in dashboard_step


def test_leaderboard_row_tooltip_helpers_exist():
    html = _read("index.html")
    assert "bindLeaderboardHoverTooltip" in html
    assert "lb-hover-tooltip" in html
    assert "formatLeaderboardPetLine" in html


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
    target_block = html[html.index(".timer-target-trigger {"):html.index(".timer-target-menu {")]
    assert "gap: 16px" in cluster_block
    assert "align-self: center" in badge_block
    assert "width: fit-content" in badge_block
    assert "font-size: 13px" in target_block
    assert "color: #fff !important" in target_block
    assert "padding: 8px 18px" in target_block


def test_sidebar_collapse_toggle_persisted():
    html = _read("index.html")
    assert "aurastudy_sidebar_collapsed" in html
    assert "sidebar-collapsed" in html
    assert 'id="sidebar-collapse-toggle"' in html
    assert "function toggleSidebarCollapsed" in html
    assert "function applySidebarCollapsedState" in html
    assert "localStorage.getItem('aurastudy_sidebar_collapsed')" in html
    sidebar_block = html[html.index('id="app-sidebar"'):html.index("<!-- Main Workspace Container -->")]
    assert 'id="nav-item-music-toggle"' in sidebar_block
    assert "overflow-y: auto" in html[html.index(".sidebar > div:first-child"):html.index(".sidebar > div:first-child") + 180]
    brand_snippet = html[
        html.index('id="sidebar-brand-home"') : html.index('id="sidebar-brand-home"') + 320
    ]
    assert 'title="Dashboard"' not in brand_snippet
    assert 'aria-label="Go to Dashboard"' in brand_snippet
    assert 'title="Dashboard"' in sidebar_block
    assert 'title="Timer"' in sidebar_block
    assert "html.sidebar-collapsed .nav-item > span:not(.nav-unread-badge)" in html
    assert "brand-mascot" in html
    assert "brand-wordmark" in html
    brand_snippet = html[html.index('id="sidebar-brand-home"'):html.index('id="sidebar-brand-home"') + 480]
    assert "/static/brand/aurastudy-mascot.png" in brand_snippet
    assert "/static/brand/aurastudy-wordmark.png" in brand_snippet
    assert "html.sidebar-collapsed .brand-wordmark" in html
    collapsed_brand_wordmark = html[
        html.index("html.sidebar-collapsed .brand-wordmark")
        : html.index("html.sidebar-collapsed .brand-wordmark") + 160
    ]
    assert "display: none" in collapsed_brand_wordmark
    collapsed_brand_row = html[
        html.index("html.sidebar-collapsed .sidebar-brand-row")
        : html.index("html.sidebar-collapsed .brand-wordmark")
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


def test_dashboard_chart_tooltip_is_larger_and_quote_card_centers_content():
    html = _read("index.html")
    chart_block = html[html.index("function buildAnalyticsChartCanvasComponent"):html.index("function formatHelpTimestamp")]
    assert "padding: 16" in chart_block
    assert "titleFont:" in chart_block
    assert "bodyFont:" in chart_block
    assert "boxPadding: 8" in chart_block
    quote_block = html[html.index(".dash-quote-card {"):html.index(".dash-quote-seedling {")]
    assert "justify-content: center" in quote_block
    assert "dash-quote-content" in html


def test_brand_uses_png_mascot_and_wordmark_not_jpg_crop():
    html = _read("index.html")
    assert "brand-tagline" not in html
    assert "Focus / Learn / Grow" not in html
    assert "/static/brand/aurastudy-logo.jpg" not in html
    assert "object-position: 22% center" not in html
    assert "mix-blend-mode: multiply" not in html[html.index(".brand-mascot"):html.index(".brand-mascot") + 220]
    landing_css = _read("static", "landing.css")
    assert "object-position: 22% center" not in landing_css
    auth_css = _read("static", "auth.css")
    assert "mix-blend-mode: multiply" not in auth_css
    mascot_path = os.path.join(ROOT_DIR, "static", "brand", "aurastudy-mascot.png")
    wordmark_path = os.path.join(ROOT_DIR, "static", "brand", "aurastudy-wordmark.png")
    assert os.path.isfile(mascot_path)
    assert os.path.isfile(wordmark_path)
    assert os.path.getsize(mascot_path) > 1000
    assert os.path.getsize(wordmark_path) > 1000


def test_study_coach_has_no_focus_tip_button():
    html = _read("index.html")
    coach_block = html[html.index('id="study-coach-card"'):html.index('id="study-coach-card"') + 520]
    assert "Focus tip" not in coach_block
    assert 'id="study-coach-tip"' in coach_block
    assert "Loading tip" not in coach_block
    assert "function updateStudyCoachCard" in html
    finish_boot_block = html[html.index("function finishAppBoot"):html.index("function finishAppBoot") + 480]
    assert "initDashboardMetricsAndCharts();" in finish_boot_block
    assert "rebuildDashboardChartAfterLayout();" in finish_boot_block
    chart_rebuild_block = html[html.index("function rebuildDashboardChartAfterLayout"):html.index("function rebuildDashboardChartAfterLayout") + 420]
    assert "operationalBarChartInstance.destroy()" in chart_rebuild_block
    assert "resizeDashboardChartIfNeeded()" in chart_rebuild_block
    init_dash_block = html[html.index("function initDashboardMetricsAndCharts"):html.index("function initDashboardMetricsAndCharts") + 600]
    assert "updateStudyCoachCard();" in init_dash_block


def test_default_todos_empty_and_legacy_strings_stripped():
    html = _read("index.html")
    state_block = html[html.index("let appState = {"):html.index("let engineIntervalRegister")]
    assert "todoItems: []" in state_block
    assert "stripLegacyDefaultTodos" in html
    assert "LEGACY_DEFAULT_TODO_TEXTS" in html
    assert "Review today's lecture notes" not in state_block
    assert "Finish a practice problem set" not in state_block


def test_placeholder_clears_on_focus_for_courses_and_auth():
    html = _read("index.html")
    assert "bindPlaceholderFocusClear" in html
    assert "initPlaceholderFocusClearInputs" in html
    assert "input:focus::placeholder" in html
    auth_js = _read("static", "auth.js")
    auth_css = _read("static", "auth.css")
    assert "bindPlaceholderFocusClear" in auth_js
    assert "input:focus::placeholder" in auth_css
    login_html = _read("server", "templates", "login.html")
    assert "/static/auth.js" in login_html


def test_focus_mode_settings_use_ios_style_switches():
    html = _read("index.html")
    settings_block = html[html.index('<!-- VIEW: SETTINGS -->'):html.index("<!-- Branded confirm dialog")]
    assert 'id="focus-toggle-float"' in settings_block
    assert 'id="focus-toggle-notify"' in settings_block
    assert 'id="focus-toggle-wakelock"' in settings_block
    assert 'id="focus-toggle-sound"' in settings_block
    assert "focus-toggle-switch" in settings_block
    assert "focus-toggle-slider" in settings_block
    assert "focus-toggle-checkbox" not in settings_block
    pip_js = _read("static", "pip.js")
    assert ".focus-toggle-switch" in pip_js
    assert ".focus-toggle-slider" in pip_js
    assert "function renderFocusSettingsUI" in pip_js
    assert "renderFocusSettingsUI: renderFocusSettingsUI" in pip_js
    assert "AuraFocus.init()" in html
    assert "AuraFocus.renderFocusSettingsUI()" in html


def test_timer_primary_action_button_is_white_on_all_ambients():
    html = _read("index.html")
    block = html[html.index(".timer-btn-primary-act,"):html.index(".timer-btn-icon {")]
    assert "background: #FFFFFF" in block
    assert "color: #1C1816" in block
    assert "background: #F5F2EB" in block
    assert ".timer-fullscreen-view.dark-mode-active .timer-btn-primary-act" in block


def test_analytics_chart_defaults_to_all_time():
    html = _read("index.html")
    assert "let analyticsChartPeriod = 'all';" in html
    chart_btns = html[
        html.index('class="dash-chart-period-btn" data-chart-period="7"')
        : html.index("All time</button>") + len("All time</button>")
    ]
    assert 'class="dash-chart-period-btn active" data-chart-period="all"' in chart_btns


def test_switchview_initializes_spotify_for_signed_in_users():
    html = _read("index.html")
    switch_block = html[html.index("function switchView(targetPanelKey"):html.index("function changeEngineMode")]
    assert "AuraSpotify.init();" in switch_block
    assert "AuraSpotify.onViewShown();" in switch_block
    assert "initGuestExperience" in switch_block
    spotify_js = _read("static", "spotify.js")
    assert "function ensureSpotifyPanelRendered" in spotify_js
    assert "isInited:" in spotify_js


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
