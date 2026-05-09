"""Unit tests for talemate.save: combine_paths, SceneEncoder, save_node_module."""

import json
import os

import pytest

from talemate.save import SceneEncoder, combine_paths, save_node_module
from talemate.scene_message import (
    CharacterMessage,
    DirectorMessage,
    Flags,
    NarratorMessage,
    SceneMessage,
    reset_message_id,
)
from talemate.game.engine.nodes.core import UNRESOLVED, Graph
from talemate.game.engine.nodes.scene import SceneLoop


# ---------------------------------------------------------------------------
# combine_paths
# ---------------------------------------------------------------------------


class TestCombinePaths:
    def test_takes_filename_only_from_relative_path(self):
        # The implementation deliberately drops directory components from the
        # relative path and joins only the final component to the absolute
        # base. This guards against accidental nested directories.
        result = combine_paths("/abs/base", "some/nested/file.json")
        assert result == os.path.join("/abs/base", "file.json")

    def test_passes_through_bare_filename(self):
        result = combine_paths("/abs/base", "file.json")
        assert result == os.path.join("/abs/base", "file.json")

    def test_normalizes_redundant_segments_in_relative(self):
        result = combine_paths("/abs/base", "./sub/../file.json")
        # os.path.normpath collapses the redundant traversal
        assert result == os.path.join("/abs/base", "file.json")

    def test_does_not_modify_absolute_base(self):
        # The absolute path is concatenated as-is (no normalization)
        result = combine_paths("/a/b/c", "x.json")
        assert result.startswith("/a/b/c")
        assert result.endswith("x.json")


# ---------------------------------------------------------------------------
# SceneEncoder
# ---------------------------------------------------------------------------


class TestSceneEncoder:
    @pytest.fixture(autouse=True)
    def _reset_message_id(self):
        reset_message_id()
        yield
        reset_message_id()

    def test_encodes_scene_message_via_dict_method(self):
        msg = SceneMessage(message="hello", source="ai")
        encoded = json.dumps(msg, cls=SceneEncoder)
        decoded = json.loads(encoded)
        assert decoded["message"] == "hello"
        assert decoded["typ"] == "scene"
        assert decoded["source"] == "ai"

    def test_encodes_character_message_with_subclass_fields(self):
        msg = CharacterMessage(
            message="Alice: hi",
            from_choice="choice-1",
            asset_id="img-1",
            asset_type="avatar",
        )
        decoded = json.loads(json.dumps(msg, cls=SceneEncoder))
        assert decoded["from_choice"] == "choice-1"
        assert decoded["asset_id"] == "img-1"
        assert decoded["typ"] == "character"

    def test_encodes_director_message_action(self):
        msg = DirectorMessage(message="proceed", action="user_direction")
        decoded = json.loads(json.dumps(msg, cls=SceneEncoder))
        assert decoded["action"] == "user_direction"

    def test_encodes_narrator_message_with_meta(self):
        msg = NarratorMessage(
            message="The sun rises",
            meta={
                "agent": "narrator",
                "function": "progress_story",
                "arguments": {"narrative_direction": "advance"},
            },
        )
        decoded = json.loads(json.dumps(msg, cls=SceneEncoder))
        assert decoded["meta"]["function"] == "progress_story"

    def test_unresolved_sentinel_serialised_as_null(self):
        # The custom encoder normalises UNRESOLVED placeholders to null so
        # downstream consumers see plain JSON without sentinel objects.
        encoded = json.dumps({"value": UNRESOLVED}, cls=SceneEncoder)
        assert json.loads(encoded) == {"value": None}

    def test_falls_through_to_default_for_unknown_types(self):
        # Unknown non-serialisable objects must still raise TypeError to
        # match the standard json.JSONEncoder contract.
        class Opaque:
            pass

        with pytest.raises(TypeError):
            json.dumps(Opaque(), cls=SceneEncoder)

    def test_encodes_list_of_messages(self):
        msgs = [
            SceneMessage(message="a"),
            CharacterMessage(message="Alice: hi"),
            NarratorMessage(message="The wind blows"),
        ]
        encoded = json.dumps(msgs, cls=SceneEncoder)
        decoded = json.loads(encoded)
        assert [m["typ"] for m in decoded] == ["scene", "character", "narrator"]

    def test_encodes_hidden_flag_as_int(self):
        msg = SceneMessage(message="secret", flags=Flags.HIDDEN)
        decoded = json.loads(json.dumps(msg, cls=SceneEncoder))
        assert decoded["flags"] == int(Flags.HIDDEN)


# ---------------------------------------------------------------------------
# save_node_module
# ---------------------------------------------------------------------------


class _FakeScene:
    """Minimal Scene-like object exposing only what save_node_module reads.

    ``nodes_filepath`` is a dynamic property in the real Scene class - it
    derives from ``nodes_filename`` at access time. We mirror that here so
    save_node_module sees the up-to-date value after it mutates the field.
    """

    def __init__(self, nodes_dir: str, nodes_filename: str = "scene-loop.json"):
        self.nodes_dir = nodes_dir
        self.nodes_filename = nodes_filename

    @property
    def nodes_filepath(self) -> str:
        return os.path.join(self.nodes_dir, self.nodes_filename)


def _make_fake_scene(nodes_dir: str, nodes_filename: str = "scene-loop.json"):
    return _FakeScene(nodes_dir, nodes_filename)


class TestSaveNodeModule:
    @pytest.mark.asyncio
    async def test_creates_nodes_directory_if_missing(self, tmp_path):
        scene = _make_fake_scene(str(tmp_path / "nodes"))
        # Build a minimal SceneLoop graph (real type, no LLM client needed)
        graph = SceneLoop()
        assert not os.path.exists(scene.nodes_dir)

        await save_node_module(scene, graph, set_as_main=True)

        assert os.path.isdir(scene.nodes_dir)

    @pytest.mark.asyncio
    async def test_scene_loop_with_set_as_main_writes_to_default_filename(
        self, tmp_path
    ):
        scene = _make_fake_scene(str(tmp_path / "nodes"))
        graph = SceneLoop()

        result = await save_node_module(scene, graph, set_as_main=True)

        # Default filename is "scene-loop.json"
        assert scene.nodes_filename == "scene-loop.json"
        assert result.endswith("scene-loop.json")
        assert os.path.isfile(result)

        # The written file should be valid JSON containing graph data
        with open(result) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "nodes" in data

    @pytest.mark.asyncio
    async def test_scene_loop_with_set_as_main_uses_explicit_filename(
        self, tmp_path
    ):
        scene = _make_fake_scene(str(tmp_path / "nodes"))
        graph = SceneLoop()

        result = await save_node_module(
            scene, graph, filename="custom.json", set_as_main=True
        )

        # set_as_main + filename overrides the default
        assert scene.nodes_filename == "custom.json"
        assert result.endswith("custom.json")
        assert os.path.isfile(result)

    @pytest.mark.asyncio
    async def test_non_scene_loop_requires_filename(self, tmp_path):
        scene = _make_fake_scene(str(tmp_path / "nodes"))
        graph = Graph()  # Graph (not SceneLoop)

        with pytest.raises(ValueError, match="filename is required"):
            await save_node_module(scene, graph, filename=None)

    @pytest.mark.asyncio
    async def test_non_scene_loop_strips_relative_path_components(self, tmp_path):
        scene = _make_fake_scene(str(tmp_path / "nodes"))
        graph = Graph()

        # The filename includes a relative directory; combine_paths strips it
        result = await save_node_module(
            scene, graph, filename="some/nested/widget.json"
        )

        # Only the final component is used, joined to nodes_dir
        assert result == os.path.join(scene.nodes_dir, "widget.json")
        assert os.path.isfile(result)

    @pytest.mark.asyncio
    async def test_scene_loop_without_set_as_main_falls_through_to_filename_branch(
        self, tmp_path
    ):
        # When set_as_main is False, even a SceneLoop must go through the
        # explicit-filename branch and a missing filename must raise.
        scene = _make_fake_scene(str(tmp_path / "nodes"))
        graph = SceneLoop()

        with pytest.raises(ValueError, match="filename is required"):
            await save_node_module(scene, graph, filename=None, set_as_main=False)

    @pytest.mark.asyncio
    async def test_scene_loop_without_set_as_main_writes_to_named_file(
        self, tmp_path
    ):
        scene = _make_fake_scene(str(tmp_path / "nodes"))
        graph = SceneLoop()

        result = await save_node_module(
            scene, graph, filename="loop-fragment.json", set_as_main=False
        )

        # nodes_filename should NOT be updated when set_as_main is False
        assert scene.nodes_filename == "scene-loop.json"  # unchanged default
        assert result.endswith("loop-fragment.json")
        assert os.path.isfile(result)
