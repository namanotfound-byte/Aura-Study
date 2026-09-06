"""Source-level regression guards for the shared formatDurationHM helper,
hour-aware timer display, and the deterministic Study Coach (no LLM/API).

There is no JS test runner in this project (see tests/test_frontend_security.py),
so these tests parse index.html source text the same way the other frontend
regression tests do.
"""
import os
import re

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def _extract_function_body(source, func_name):
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


def test_format_duration_hm_helper_exists_and_emits_hours_and_minutes():
    html = _read("index.html")
    assert "function formatDurationHM(totalSeconds, options)" in html
    body = _extract_function_body(html, "formatDurationHM")
    assert "Math.floor(" in body
    assert "hours + 'h ' + minutes + 'm'" in body
    assert "hours === 0" in body
    assert "minutes + 'm'" in body
    assert "' hour'" in body or "'hour'" in body
    assert "' minute'" in body or "'minutes'" in body


def test_format_session_history_date_helper_exists_and_formats_ordinals():
    html = _read("index.html")
    assert "function formatSessionHistoryDate(isoDate)" in html
    body = _extract_function_body(html, "formatSessionHistoryDate")
    assert "new Date(" not in body
    assert "'st'" in body and "'nd'" in body and "'rd'" in body and "'th'" in body
    assert "September" in body
    assert "July" in body
    sessions_body = _extract_function_body(html, "renderSessionsTable")
    assert "escapeHtml(formatSessionHistoryDate(s.date))" in sessions_body
    assert "escapeHtml(s.date)" not in sessions_body


def test_format_leaderboard_hours_delegates_to_shared_helper():
    html = _read("index.html")
    body = _extract_function_body(html, "formatLeaderboardHours")
    assert "return formatDurationHM(seconds)" in body


def test_badge_descriptions_no_longer_use_bare_minute_totals():
    html = _read("index.html")
    assert "180 minutes in a single day" not in html
    assert "240 minutes in a single day" not in html
    assert "300 minutes in a single day" not in html
    assert "3 hours 0 minutes in a single day" in html


def test_timer_display_switches_to_hours_minutes_at_one_hour():
    html = _read("index.html")
    body = _extract_function_body(html, "updateEngineDisplayString")
    assert "Math.floor(raw)" in body or "Math.floor(totalRunningSecondsMap" in body
    assert "totalRunningSecondsMap >= 3600" in body
    hour_branch = body.split("totalRunningSecondsMap >= 3600", 1)[1].split("} else", 1)[0]
    assert "'h '" in hour_branch and "'m'" in hour_branch
    assert "+ 's'" not in hour_branch
    assert "padStart(2, '0')" in body
    assert "timer-clock-display--hours" in body
    assert "white-space: nowrap" in html


def test_pip_timer_hides_seconds_and_keeps_one_line_at_one_hour():
    pip_js = _read("static", "pip.js")
    assert "function isHourLongDisplay()" in pip_js
    assert "af-time--hours" in pip_js
    assert "white-space:nowrap" in pip_js
    paint_block = pip_js[pip_js.index("function paintDocumentPip"):pip_js.index("function paintDocumentPipCompletion")]
    assert "isHourLongDisplay()" in paint_block
    assert 'classList.add("af-time--hours")' in paint_block


def test_study_coach_is_local_and_deterministic():
    html = _read("index.html")
    assert "function computeStudyCoachMetrics()" in html
    assert "function generateStudyCoachTip()" in html
    assert "function updateStudyCoachCard()" in html
    assert 'id="study-coach-card"' in html
    assert 'id="study-coach-tip"' in html

    coach_block = _extract_function_body(html, "generateStudyCoachTip")
    assert "computeStudyCoachMetrics()" in coach_block
    assert "formatDurationHM(" in coach_block
    assert "escapeHtml(" in coach_block

    assert "openai" not in html.lower()
    assert "chatgpt" not in html.lower()
    assert re.search(r"fetch\s*\([^)]*api\.openai", html, re.I) is None
    assert re.search(r"fetch\s*\([^)]*anthropic", html, re.I) is None

    coach_region = html[html.index("function computeStudyCoachMetrics"):html.index("function formatLeaderboardHours")]
    assert "fetch(" not in coach_region
