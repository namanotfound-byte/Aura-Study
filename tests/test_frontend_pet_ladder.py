"""Source-level regression guards for the garden pet evolution ladder."""
import os
import re

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
    return source[start : i - 1]


def test_pet_level_costs_array_present():
    html = _read("index.html")
    assert "PET_LEVEL_COSTS_HOURS = [2, 3, 5, 8, 12, 16, 22, 30, 40, 55, 75]" in html


def test_pet_cumulative_level_logic_present():
    html = _read("index.html")
    assert "function getPetCumulativeHoursForLevel(level)" in html
    assert "function getPetLevelCostHours(level)" in html
    body = _extract_function_body(html, "getPetLevelCostHours")
    assert "100 + (level - 12) * 25" in body
    update_body = _extract_function_body(html, "updatePetEvolutionState")
    assert "getPetCumulativeHoursForLevel(level + 1)" in update_body
    assert "durationSeconds" in update_body


def test_unicorn_forever_path_removed():
    html = _read("index.html")
    assert "Unicorn" not in html
    assert "🦄" not in html
    update_body = _extract_function_body(html, "updatePetEvolutionState")
    assert "Scholar Cat" not in update_body
    assert "Bunny" not in update_body


def test_garden_form_names_present():
    html = _read("index.html")
    for name in [
        "Seedling",
        "Sprout Bun",
        "Leaf Fox",
        "Blossom Cat",
        "Grove Owl",
        "Orchard Stag",
        "Canopy Wolf",
        "Storm Cedar",
        "Mountain Grove",
        "Season Keeper",
        "World Tree",
        "Eternal Bloom",
    ]:
        assert name in html


def test_xp_label_uses_format_duration_hm():
    html = _read("index.html")
    body = _extract_function_body(html, "updatePetEvolutionState")
    assert "formatDurationHM(progressSeconds)" in body
    assert "formatDurationHM(nextCostSeconds)" in body
    assert "toward ${nextForm.name}" in body
    assert 'id="pet-xp-numerical-label">0h 0m / 2h 0m toward Sprout Bun<' in html


def test_no_sixty_minute_flat_level_formula():
    html = _read("index.html")
    body = _extract_function_body(html, "updatePetEvolutionState")
    assert "Math.floor(netMinutes / 60)" not in body
    assert "netMinutes % 60" not in body
    assert "60 * 60" not in body or "nextCostSeconds" in body
