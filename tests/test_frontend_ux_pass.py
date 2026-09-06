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
    assert "function isViewAccessible" in html


def test_reset_flow_uses_branded_confirm_modal_not_window_confirm():
    html = _read("index.html")
    assert "function showAuraConfirmDialog" in html
    assert 'id="aura-confirm-modal"' in html
    reset_block = html[html.index("function resetEngineDisplayState"):html.index("function sessionSignature")]
    assert "window.confirm(" not in reset_block
    assert "showAuraConfirmDialog" in reset_block


def test_theme_and_timer_colors_live_in_settings_not_sidebar():
    html = _read("index.html")
    sidebar_footer = html[html.index('<div class="sidebar-footer">'):html.index('<div class="user-profile">')]
    assert "theme-switch-row" not in sidebar_footer
    assert "theme-btn-pink" not in sidebar_footer
    assert "settings-theme-btn-pink" in html
    assert "settings-theme-btn-blue" in html
    assert 'id="settings-ambient-picker"' in html
    timer_view = html[html.index('<!-- VIEW: TIMER -->'):html.index("<!-- VIEW: COURSES -->")]
    assert "ambient-selector-bar" not in timer_view
    assert "data-ambient-key=\"mintrefresh\"" in html
    blue_theme_block = html[html.index(":root[data-theme=\"blue\"]"):html.index("/* Theme switch control")]
    assert "--ambient-grad-mintrefresh" in blue_theme_block


def test_custom_404_template_exists():
    app_py = _read("server", "app.py")
    template = _read("server", "templates", "404.html")
    assert "404.html" in app_py
    assert "Page not found" in template
    assert 'href="/app"' in template
