"""Tests for talemate.util.colors.

Covers the color tables (COLOR_MAP, COLOR_NAMES, COLORS, ALL_COLOR_NAMES)
and the random_color / unique_random_colors helpers.
"""

import re

from talemate.util.colors import (
    ALL_COLOR_NAMES,
    COLOR_MAP,
    COLOR_NAMES,
    COLORS,
    SPECIAL_COLOR_NAMES,
    random_color,
    unique_random_colors,
)


# ---------------------------------------------------------------------------
# Static tables
# ---------------------------------------------------------------------------


HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_color_map_values_are_hex_codes():
    """Every value in COLOR_MAP should be a 6-digit hex color string."""
    assert COLOR_MAP, "COLOR_MAP must not be empty"
    for name, hex_code in COLOR_MAP.items():
        assert HEX_RE.match(hex_code), f"{name}={hex_code} is not a valid hex code"


def test_color_names_is_sorted_keys_of_color_map():
    assert COLOR_NAMES == sorted(COLOR_MAP.keys())


def test_colors_is_sorted_values_of_color_map():
    assert COLORS == sorted(COLOR_MAP.values())


def test_color_map_contains_expected_base_colors():
    """A few well-known base colors must be present."""
    for expected in ("red", "blue", "green", "yellow", "purple"):
        assert expected in COLOR_MAP


def test_color_map_has_lighten_and_darken_variants():
    """Every base color should have lighten-3 and darken-3 variants."""
    base_colors = [
        "red",
        "pink",
        "purple",
        "deep-purple",
        "indigo",
        "blue",
        "light-blue",
        "cyan",
        "teal",
        "green",
        "light-green",
        "lime",
        "yellow",
        "amber",
        "orange",
        "deep-orange",
        "brown",
        "blue-grey",
        "grey",
    ]
    for base in base_colors:
        assert f"{base}-lighten-3" in COLOR_MAP, f"missing {base}-lighten-3"
        assert f"{base}-darken-3" in COLOR_MAP, f"missing {base}-darken-3"


def test_all_color_names_is_special_plus_color_names():
    assert ALL_COLOR_NAMES == SPECIAL_COLOR_NAMES + COLOR_NAMES


def test_special_color_names_are_distinct_from_palette():
    """Special semantic names must not collide with palette names."""
    assert set(SPECIAL_COLOR_NAMES).isdisjoint(set(COLOR_NAMES))


# ---------------------------------------------------------------------------
# random_color
# ---------------------------------------------------------------------------


def test_random_color_returns_value_from_palette():
    """random_color must return one of the COLORS hex codes."""
    for _ in range(20):
        c = random_color()
        assert c in COLORS


# ---------------------------------------------------------------------------
# unique_random_colors
# ---------------------------------------------------------------------------


def test_unique_random_colors_zero_or_negative_returns_empty():
    assert unique_random_colors(0) == []
    assert unique_random_colors(-5) == []


def test_unique_random_colors_returns_correct_count():
    """The function should always return exactly `count` colors."""
    for n in (1, 5, 10, len(COLORS)):
        result = unique_random_colors(n)
        assert len(result) == n


def test_unique_random_colors_are_unique_when_within_palette_size():
    """When count <= len(COLORS), all returned colors must be distinct."""
    count = min(10, len(COLORS))
    result = unique_random_colors(count)
    assert len(set(result)) == count


def test_unique_random_colors_when_count_equals_palette_returns_all():
    """count == len(COLORS) yields all palette colors (possibly reordered)."""
    result = unique_random_colors(len(COLORS))
    assert sorted(result) == COLORS


def test_unique_random_colors_when_count_exceeds_palette_allows_duplicates():
    """When count > len(COLORS), duplicates may appear but length must be respected."""
    count = len(COLORS) + 5
    result = unique_random_colors(count)
    assert len(result) == count
    # All values are still from COLORS
    assert all(c in COLORS for c in result)


def test_unique_random_colors_returned_values_are_hex_codes():
    result = unique_random_colors(5)
    for c in result:
        assert HEX_RE.match(c), f"{c} is not a valid hex code"
