"""
Unit tests for `talemate.world_state.templates.scene`.

Covers:
- `SceneType.to_scene_type_dict()` ID derivation from name
- `apply_to_scene` happy path: writes into scene.scene_intent.scene_types
- `apply_to_scene` no-scene-intent path: returns the dict but does not crash
- field defaults
"""

import pytest

from _world_state_helpers import scene  # noqa: F401 - pytest fixture
from talemate.world_state.templates.scene import SceneType


# ---------------------------------------------------------------------------
# to_scene_type_dict
# ---------------------------------------------------------------------------


class TestSceneTypeToDict:
    def test_id_derived_from_name(self):
        st = SceneType(name="Battle Scene", description="Fight!")
        d = st.to_scene_type_dict()
        assert d["id"] == "battle_scene"
        assert d["name"] == "Battle Scene"
        assert d["description"] == "Fight!"
        assert d["instructions"] is None

    def test_id_handles_multiple_spaces_and_case(self):
        st = SceneType(name="Foo BAR baz", description="d")
        assert st.to_scene_type_dict()["id"] == "foo_bar_baz"

    def test_includes_instructions_when_set(self):
        st = SceneType(
            name="Solo",
            description="d",
            instructions="Make it intense.",
        )
        d = st.to_scene_type_dict()
        assert d["instructions"] == "Make it intense."

    def test_template_type_default(self):
        st = SceneType(name="X", description="y")
        assert st.template_type == "scene_type"


# ---------------------------------------------------------------------------
# apply_to_scene
# ---------------------------------------------------------------------------


class TestApplyToScene:
    def test_apply_writes_to_scene_intent_via_scene_intent_attr(self, scene):
        # Per the source code:
        #   if scene and hasattr(scene, "scene_intent") and scene.scene_intent: ...
        # The Scene class actually exposes the SceneIntent state as
        # `intent_state`. To exercise the success branch, attach a `scene_intent`
        # attribute pointing to the same SceneIntent instance.
        scene.scene_intent = scene.intent_state

        st = SceneType(name="Battle Scene", description="Fight!")
        result = st.apply_to_scene(scene)

        assert result["id"] == "battle_scene"
        assert "battle_scene" in scene.scene_intent.scene_types
        assert (
            scene.scene_intent.scene_types["battle_scene"]["name"]
            == "Battle Scene"
        )

    def test_apply_returns_dict_when_scene_lacks_scene_intent(self, scene):
        # By default, `scene` exposes `intent_state`, NOT `scene_intent`.
        # apply_to_scene should still return the dict but not crash.
        if hasattr(scene, "scene_intent"):
            delattr(scene, "scene_intent")

        st = SceneType(name="My Scene", description="d")
        result = st.apply_to_scene(scene)
        assert result["id"] == "my_scene"
        assert result["name"] == "My Scene"

    def test_apply_with_none_scene(self):
        st = SceneType(name="None Scene", description="d")
        result = st.apply_to_scene(None)
        assert result["id"] == "none_scene"

    def test_apply_with_falsy_scene_intent_skips_storage(self, scene):
        scene.scene_intent = None
        st = SceneType(name="X", description="y")
        result = st.apply_to_scene(scene)
        # We still get a dict back, scene_intent is not mutated (it's None).
        assert result["id"] == "x"
        assert scene.scene_intent is None
