"""
Unit tests for `talemate.world_state.templates.defaults`.

`defaults.py` declares a single `DEFAULT_GROUP` plus two factory helpers:
- `create_defaults()`             -> creates a Collection with one group, saves it
- `create_defaults_if_empty_collection(col)` -> overwrites a collection with the default

Each factory writes to disk; all tests redirect TEMPLATE_PATH /
TEMPLATE_PATH_TALEMATE so we don't trample real templates.
"""

import os

import pytest
import yaml

from talemate.world_state.templates.base import Collection, Group
from talemate.world_state.templates.state_reinforcement import StateReinforcement


@pytest.fixture
def isolated_templates(tmp_path, monkeypatch):
    """Redirect TEMPLATE_PATH and TEMPLATE_PATH_TALEMATE to tmp_path.

    Note: `Group.save(path=TEMPLATE_PATH)` binds the default at function
    definition time, so monkey-patching the *module* attribute does NOT
    change the default. We work around that in the tests by saving the
    DEFAULT_GROUP path manually before invoking `create_defaults` so the
    save happens to tmp_path rather than the real templates dir.
    """
    monkeypatch.setattr(
        "talemate.world_state.templates.base.TEMPLATE_PATH",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "talemate.world_state.templates.base.TEMPLATE_PATH_TALEMATE",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "talemate.world_state.templates.defaults.TEMPLATE_PATH_TALEMATE",
        str(tmp_path),
    )
    return tmp_path


# ---------------------------------------------------------------------------
# DEFAULT_GROUP module-level object
# ---------------------------------------------------------------------------


class TestDefaultGroupConstant:
    def test_default_group_is_a_group(self):
        from talemate.world_state.templates.defaults import DEFAULT_GROUP

        assert isinstance(DEFAULT_GROUP, Group)
        assert DEFAULT_GROUP.author == "Talemate"
        assert DEFAULT_GROUP.name == "default"

    def test_default_group_has_three_known_templates(self):
        from talemate.world_state.templates.defaults import DEFAULT_GROUP

        assert set(DEFAULT_GROUP.templates.keys()) == {
            "goals",
            "physical_health",
            "time_of_day",
        }

    def test_default_templates_are_state_reinforcements(self):
        from talemate.world_state.templates.defaults import DEFAULT_GROUP

        for tmpl in DEFAULT_GROUP.templates.values():
            assert isinstance(tmpl, StateReinforcement)
            assert tmpl.template_type == "state_reinforcement"

    def test_goals_template_field_values(self):
        from talemate.world_state.templates.defaults import DEFAULT_GROUP

        goals = DEFAULT_GROUP.templates["goals"]
        assert goals.name == "Goals"
        assert goals.state_type == "npc"
        assert goals.insert == "conversation-context"
        assert goals.interval == 20
        assert goals.favorite is True
        assert goals.auto_create is False

    def test_physical_health_template_field_values(self):
        from talemate.world_state.templates.defaults import DEFAULT_GROUP

        ph = DEFAULT_GROUP.templates["physical_health"]
        assert ph.name == "Physical Health"
        assert ph.state_type == "character"
        assert ph.insert == "sequential"
        assert ph.interval == 10

    def test_time_of_day_template_field_values(self):
        from talemate.world_state.templates.defaults import DEFAULT_GROUP

        tod = DEFAULT_GROUP.templates["time_of_day"]
        assert tod.name == "Time of day"
        assert tod.state_type == "world"
        assert tod.query == "What is the current time of day?"


# ---------------------------------------------------------------------------
# create_defaults()
# ---------------------------------------------------------------------------


class TestCreateDefaults:
    def test_creates_collection_with_default_group_and_saves(self, isolated_templates):
        """create_defaults builds a Collection with the DEFAULT_GROUP and
        persists it. We pre-set DEFAULT_GROUP.path so save() writes into our
        isolated tmp_path rather than the real templates dir."""
        from talemate.world_state.templates.defaults import (
            DEFAULT_GROUP,
            create_defaults,
        )

        original_path = DEFAULT_GROUP.path
        try:
            DEFAULT_GROUP.path = os.path.join(str(isolated_templates), "default.yaml")
            collection = create_defaults()
        finally:
            DEFAULT_GROUP.path = original_path

        assert isinstance(collection, Collection)
        assert len(collection.groups) == 1
        assert collection.groups[0] is DEFAULT_GROUP

        # File written to our redirected path
        expected = os.path.join(str(isolated_templates), "default.yaml")
        assert os.path.exists(expected)

        # Yaml file is valid and contains the templates
        with open(expected, "r") as f:
            data = yaml.safe_load(f)
        assert data["name"] == "default"
        assert "goals" in data["templates"]
        assert "physical_health" in data["templates"]
        assert "time_of_day" in data["templates"]


# ---------------------------------------------------------------------------
# create_defaults_if_empty_collection
# ---------------------------------------------------------------------------


class TestCreateDefaultsIfEmpty:
    def test_overwrites_passed_collection_with_defaults(self, isolated_templates):
        # Note: the source has `if not collection.groups or True:` — this is
        # always True, so it always overwrites. Test the actual behaviour.
        from talemate.world_state.templates.defaults import (
            DEFAULT_GROUP,
            create_defaults_if_empty_collection,
        )

        existing_group = Group(
            author="me",
            name="other",
            description="x",
            uid="other-uid",
        )
        col = Collection(groups=[existing_group])

        original_path = DEFAULT_GROUP.path
        try:
            DEFAULT_GROUP.path = os.path.join(str(isolated_templates), "default.yaml")
            result = create_defaults_if_empty_collection(col)
        finally:
            DEFAULT_GROUP.path = original_path

        # The defaults group has replaced whatever was there
        assert result is col
        assert col.groups == [DEFAULT_GROUP]
        # File was saved to the redirected path
        expected = os.path.join(str(isolated_templates), "default.yaml")
        assert os.path.exists(expected)

    def test_overwrites_empty_collection(self, isolated_templates):
        from talemate.world_state.templates.defaults import (
            DEFAULT_GROUP,
            create_defaults_if_empty_collection,
        )

        col = Collection(groups=[])
        original_path = DEFAULT_GROUP.path
        try:
            DEFAULT_GROUP.path = os.path.join(str(isolated_templates), "default.yaml")
            result = create_defaults_if_empty_collection(col)
        finally:
            DEFAULT_GROUP.path = original_path
        assert result is col
        assert col.groups == [DEFAULT_GROUP]
