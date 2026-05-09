"""
Unit tests for talemate.prompts.overrides.

Covers TemplateOverride dataclass and get_template_overrides() — which scans
template directories (default plus prepended overrides) and reports
templates that exist in more than one location, with mtime-based age info.
"""

import os
from pathlib import Path

import pytest

from talemate.prompts.base import prepended_template_dirs
from talemate.prompts.overrides import (
    TemplateOverride,
    get_template_overrides,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _write_template(path: Path, content: str = "x", mtime: float | None = None) -> Path:
    """Create a .jinja2 file at ``path`` and optionally stamp its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def isolated_prepend():
    """
    Set ``prepended_template_dirs`` to a fresh list for the test, then reset.

    Tests that use this fixture call ``isolated_prepend([...])`` to install
    their template directories.
    """
    tokens = []

    def _set(dirs: list[str]):
        token = prepended_template_dirs.set(list(dirs))
        tokens.append(token)

    yield _set

    # Reset all installed tokens (LIFO) so other tests are unaffected.
    for token in reversed(tokens):
        prepended_template_dirs.reset(token)


@pytest.fixture
def two_layer_dirs(tmp_path):
    """
    Build two prepended template directories for an arbitrary agent_type.

    Layout::
        override/<agent>/<template_name>.jinja2
        default/<agent>/<template_name>.jinja2

    Returns a dict with the two directories and the agent name so tests can
    add specific templates and inject the dirs via ``prepended_template_dirs``.
    """
    override_dir = tmp_path / "override"
    default_dir = tmp_path / "default"
    agent = "narrator"
    (override_dir / agent).mkdir(parents=True)
    (default_dir / agent).mkdir(parents=True)

    return {
        "override_dir": override_dir,
        "default_dir": default_dir,
        "agent": agent,
        "dirs": [str(override_dir), str(default_dir)],
    }


# ---------------------------------------------------------------------------
# TemplateOverride dataclass
# ---------------------------------------------------------------------------


class TestTemplateOverrideDataclass:
    """Tests for the TemplateOverride dataclass."""

    def test_stores_all_fields(self):
        override = TemplateOverride(
            template_name="foo.jinja2",
            override_path="/a/foo.jinja2",
            default_path="/b/foo.jinja2",
            age_difference="2 days",
            override_newer=True,
        )

        assert override.template_name == "foo.jinja2"
        assert override.override_path == "/a/foo.jinja2"
        assert override.default_path == "/b/foo.jinja2"
        assert override.age_difference == "2 days"
        assert override.override_newer is True


# ---------------------------------------------------------------------------
# get_template_overrides — core behavior
# ---------------------------------------------------------------------------


class TestGetTemplateOverridesBasics:
    """Basic resolution tests for get_template_overrides()."""

    def test_returns_empty_list_when_no_dirs_exist(self, isolated_prepend, tmp_path):
        """Non-existent prepended dirs and a fake agent yield no overrides."""
        # Point prepended dirs at a path that doesn't exist on disk.
        isolated_prepend([str(tmp_path / "does-not-exist")])

        # Use a clearly-fake agent name so the default agent dir won't exist either.
        result = get_template_overrides("totally-fake-agent-xyz")

        assert result == []

    def test_returns_empty_when_only_one_location_has_template(
        self, isolated_prepend, two_layer_dirs
    ):
        """A template that only exists in one place is not considered an override."""
        agent = two_layer_dirs["agent"]
        # Only put the template in override_dir, not default_dir.
        _write_template(two_layer_dirs["override_dir"] / agent / "lonely.jinja2")

        isolated_prepend([str(two_layer_dirs["override_dir"])])

        result = get_template_overrides("totally-fake-agent-xyz")

        # Should not include 'lonely' — it's only in one location.
        names = [o.template_name for o in result]
        assert all("lonely" not in n for n in names)

    def test_detects_override_when_template_in_two_dirs(
        self, isolated_prepend, two_layer_dirs
    ):
        """Same template in two dirs is reported as a TemplateOverride."""
        agent = two_layer_dirs["agent"]
        override_path = two_layer_dirs["override_dir"] / agent / "shared.jinja2"
        default_path = two_layer_dirs["default_dir"] / agent / "shared.jinja2"
        _write_template(override_path)
        _write_template(default_path)

        isolated_prepend(two_layer_dirs["dirs"])

        # The agent_type is irrelevant here — files are discovered via prepended dirs.
        result = get_template_overrides("totally-fake-agent-xyz")

        shared = [o for o in result if "shared.jinja2" in o.template_name]
        assert len(shared) == 1
        entry = shared[0]
        # First-listed prepend dir wins as the "override".
        assert entry.override_path == str(override_path)
        assert entry.default_path == str(default_path)


# ---------------------------------------------------------------------------
# Override newness / age
# ---------------------------------------------------------------------------


class TestOverrideNewness:
    """Tests for override_newer flag and age_difference formatting."""

    def test_override_newer_true_when_override_mtime_greater(
        self, isolated_prepend, two_layer_dirs
    ):
        agent = two_layer_dirs["agent"]
        # Default written *before* override.
        default_path = _write_template(
            two_layer_dirs["default_dir"] / agent / "ovr.jinja2", mtime=1_000_000.0
        )
        override_path = _write_template(
            two_layer_dirs["override_dir"] / agent / "ovr.jinja2",
            mtime=1_000_000.0 + 2 * 86400,  # 2 days newer
        )

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        entry = next(o for o in result if "ovr.jinja2" in o.template_name)

        assert entry.override_newer is True
        # Default is older than override → human diff should mention "2 days".
        assert "2 days" in entry.age_difference
        assert entry.override_path == str(override_path)
        assert entry.default_path == str(default_path)

    def test_override_newer_false_when_default_mtime_greater(
        self, isolated_prepend, two_layer_dirs
    ):
        agent = two_layer_dirs["agent"]
        # Override is OLDER than default — represents a stale override.
        _write_template(
            two_layer_dirs["override_dir"] / agent / "stale.jinja2", mtime=1_000_000.0
        )
        _write_template(
            two_layer_dirs["default_dir"] / agent / "stale.jinja2",
            mtime=1_000_000.0 + 5 * 86400,  # default is 5 days newer
        )

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        entry = next(o for o in result if "stale.jinja2" in o.template_name)

        assert entry.override_newer is False
        assert "5 days" in entry.age_difference

    def test_age_difference_formats_hours(self, isolated_prepend, two_layer_dirs):
        agent = two_layer_dirs["agent"]
        _write_template(
            two_layer_dirs["override_dir"] / agent / "h.jinja2", mtime=1_000_000.0
        )
        _write_template(
            two_layer_dirs["default_dir"] / agent / "h.jinja2",
            mtime=1_000_000.0 + 3 * 3600,  # 3 hours diff, no full day
        )

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        entry = next(o for o in result if "h.jinja2" in o.template_name)

        assert "hours" in entry.age_difference
        assert "3 hours" in entry.age_difference

    def test_age_difference_formats_minutes(self, isolated_prepend, two_layer_dirs):
        agent = two_layer_dirs["agent"]
        _write_template(
            two_layer_dirs["override_dir"] / agent / "m.jinja2", mtime=1_000_000.0
        )
        _write_template(
            two_layer_dirs["default_dir"] / agent / "m.jinja2",
            mtime=1_000_000.0 + 7 * 60,  # 7 minutes
        )

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        entry = next(o for o in result if "m.jinja2" in o.template_name)

        assert "7 minutes" in entry.age_difference

    def test_age_difference_falls_back_to_less_than_a_minute(
        self, isolated_prepend, two_layer_dirs
    ):
        """Sub-minute mtime difference reports 'less than a minute'."""
        agent = two_layer_dirs["agent"]
        # Same mtime to within a few seconds.
        t = 1_000_000.0
        _write_template(two_layer_dirs["override_dir"] / agent / "z.jinja2", mtime=t)
        _write_template(two_layer_dirs["default_dir"] / agent / "z.jinja2", mtime=t + 5)

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        entry = next(o for o in result if "z.jinja2" in o.template_name)

        assert entry.age_difference == "less than a minute"


# ---------------------------------------------------------------------------
# Multiple templates / nested structure
# ---------------------------------------------------------------------------


class TestMultipleTemplates:
    """Ensure all overrides are reported and nested templates are walked."""

    def test_reports_multiple_overridden_templates(
        self, isolated_prepend, two_layer_dirs
    ):
        agent = two_layer_dirs["agent"]
        for name in ("a.jinja2", "b.jinja2", "c.jinja2"):
            _write_template(two_layer_dirs["override_dir"] / agent / name)
            _write_template(two_layer_dirs["default_dir"] / agent / name)

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        names = {os.path.basename(o.template_name) for o in result}

        for expected in ("a.jinja2", "b.jinja2", "c.jinja2"):
            assert expected in names

    def test_walks_nested_subdirectories(self, isolated_prepend, two_layer_dirs):
        """Templates nested under subdirs are still discovered."""
        agent = two_layer_dirs["agent"]
        # Nested at agent/sub/deep.jinja2
        _write_template(two_layer_dirs["override_dir"] / agent / "sub" / "deep.jinja2")
        _write_template(two_layer_dirs["default_dir"] / agent / "sub" / "deep.jinja2")

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        # template_name is built from os.path.relpath of the dir from the template_dir
        # so it should include the nested 'sub' segment plus 'deep.jinja2'.
        deep_entries = [o for o in result if "deep.jinja2" in o.template_name]
        assert len(deep_entries) == 1
        # The path to override file should reflect the nested layout.
        assert "sub" in deep_entries[0].override_path

    def test_non_jinja_files_are_ignored(self, isolated_prepend, two_layer_dirs):
        """Files that aren't .jinja2 must not be reported."""
        agent = two_layer_dirs["agent"]
        # Non-jinja files in both dirs with the same name.
        (two_layer_dirs["override_dir"] / agent / "readme.md").write_text("nope")
        (two_layer_dirs["default_dir"] / agent / "readme.md").write_text("nope")
        # And one real jinja override so we know the function is running.
        _write_template(two_layer_dirs["override_dir"] / agent / "real.jinja2")
        _write_template(two_layer_dirs["default_dir"] / agent / "real.jinja2")

        isolated_prepend(two_layer_dirs["dirs"])

        result = get_template_overrides("totally-fake-agent-xyz")
        names = [o.template_name for o in result]

        assert any("real.jinja2" in n for n in names)
        assert all("readme.md" not in n for n in names)


# ---------------------------------------------------------------------------
# Three-dir resolution
# ---------------------------------------------------------------------------


class TestThreeLayerResolution:
    """When 3+ dirs hold the same template, override=first, default=last."""

    def test_first_is_override_last_is_default(self, isolated_prepend, tmp_path):
        agent = "narrator"
        first = tmp_path / "first" / agent
        middle = tmp_path / "middle" / agent
        last = tmp_path / "last" / agent
        first.mkdir(parents=True)
        middle.mkdir(parents=True)
        last.mkdir(parents=True)

        first_path = _write_template(first / "t.jinja2", mtime=2_000_000.0)
        _write_template(middle / "t.jinja2", mtime=1_500_000.0)
        last_path = _write_template(last / "t.jinja2", mtime=1_000_000.0)

        isolated_prepend(
            [str(tmp_path / "first"), str(tmp_path / "middle"), str(tmp_path / "last")]
        )

        result = get_template_overrides("totally-fake-agent-xyz")
        entry = next(o for o in result if "t.jinja2" in o.template_name)

        assert entry.override_path == str(first_path)
        assert entry.default_path == str(last_path)
        assert entry.override_newer is True


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


class TestRobustness:
    """Edge cases: empty / missing prepend dirs."""

    def test_empty_prepend_dirs_does_not_error(self, isolated_prepend):
        """An empty prepended list still runs cleanly (default dirs may not exist)."""
        isolated_prepend([])

        # Should never raise.
        result = get_template_overrides("totally-fake-agent-xyz")

        # Must return a list (possibly empty).
        assert isinstance(result, list)

    def test_skips_nonexistent_dir(self, isolated_prepend, two_layer_dirs):
        """A non-existent dir among prepends is skipped, others still scanned."""
        agent = two_layer_dirs["agent"]
        _write_template(two_layer_dirs["override_dir"] / agent / "x.jinja2")
        _write_template(two_layer_dirs["default_dir"] / agent / "x.jinja2")

        # Insert a junk path that doesn't exist.
        isolated_prepend(
            [
                str(two_layer_dirs["override_dir"]),
                "/this/path/definitely/does/not/exist/12345",
                str(two_layer_dirs["default_dir"]),
            ]
        )

        # Should still detect the override across the two real dirs.
        result = get_template_overrides("totally-fake-agent-xyz")
        names = [o.template_name for o in result]
        assert any("x.jinja2" in n for n in names)
