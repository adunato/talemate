"""Coverage-focused unit tests for talemate.game.engine.nodes.assets.

Each test runs the node's real `run` method through the shared
`run_node` helper. Scene-bound state (asset library, characters,
history) lives on a real `Scene` instance whose `scenes_dir` is
monkeypatched into a tmp_path, so tests don't touch the user's data.

Pattern mirrors tests/test_scene_assets.py for the Scene fixtures.
LLM/visual-generation paths are skipped — only the data-plane behavior
of each asset node is exercised.
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from _node_test_helpers import apply_inputs, capture_outputs, run_node
from talemate.character import Character
from talemate.context import ActiveScene
from talemate.game.engine.nodes.assets import (
    AddAsset,
    AssetExists,
    GetAsset,
    GetAssets,
    ListAssets,
    MakeAssetAttachmentContext,
    MakeAssetMeta,
    RemoveAsset,
    SearchAssets,
    SelectAssets,
    SetAvatarImage,
    SetCoverImage,
    UnpackAssetMeta,
    UnpackAssetSelectionContext,
    UpdateMessageAsset,
)
from talemate.game.engine.nodes.core import GraphContext, InputValueError, UNRESOLVED
from talemate.scene_assets import (
    Asset,
    AssetMeta,
    AssetSelectionContext,
    TAG_MATCH_MODE,
)
from talemate.agents.visual.schema import (
    AssetAttachmentContext,
    FORMAT_TYPE,
    GEN_TYPE,
    Resolution,
    VIS_TYPE,
)
from talemate.scene_message import CharacterMessage, NarratorMessage
from talemate.tale_mate import Scene


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes(width: int = 4, height: int = 4, color=(0, 128, 255)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _png_data_url(width: int = 4, height: int = 4, color=(0, 128, 255)) -> str:
    return "data:image/png;base64," + base64.b64encode(
        _png_bytes(width, height, color)
    ).decode("utf-8")


@pytest.fixture
def scene_factory(tmp_path, monkeypatch):
    """Same pattern as tests/test_scene_assets.py — Scene with a tmp save_dir."""
    monkeypatch.setattr(
        Scene, "scenes_dir", classmethod(lambda cls: str(tmp_path)), raising=True
    )

    def _make(project: str = "proj_default") -> Scene:
        scene = Scene()
        scene.project_name = project
        os.makedirs(scene.save_dir, exist_ok=True)
        scene.emit_status = lambda *a, **kw: None  # avoid debounced async emit
        scene.active = False
        return scene

    return _make


@pytest.fixture
def scene(scene_factory):
    return scene_factory("proj_assets_main")


# ---------------------------------------------------------------------------
# AddAsset
# ---------------------------------------------------------------------------


class TestAddAssetNode:
    @pytest.mark.asyncio
    async def test_creates_asset_from_data_url(self, scene):
        url = _png_data_url(width=10, height=20)
        out = await run_node(
            AddAsset(),
            scene=scene,
            inputs={"image_data": url},
        )
        # AddAsset emits the GraphState back on the `state` output (not the
        # input property we set). We only assert that the asset was created
        # and the metadata propagated.
        assert out["asset_id"] == out["asset"].id
        assert out["asset"].id in scene.assets.assets
        # Image dimensions propagated to meta
        assert out["meta"].resolution == Resolution(width=10, height=20)

    @pytest.mark.asyncio
    async def test_invalid_data_wraps_value_error_as_input_value_error(self, scene):
        with pytest.raises(InputValueError):
            await run_node(
                AddAsset(),
                scene=scene,
                inputs={"state": "ok", "image_data": "not-a-data-url"},
            )


# ---------------------------------------------------------------------------
# GetAsset
# ---------------------------------------------------------------------------


class TestGetAssetNode:
    @pytest.mark.asyncio
    async def test_returns_meta_and_id(self, scene):
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        out = await run_node(GetAsset(), scene=scene, inputs={"asset_id": a.id})
        assert out["asset_id"] == a.id
        assert out["asset"].id == a.id
        assert out["file_type"] == "png"
        assert out["media_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_unknown_id_raises_input_value_error(self, scene):
        with pytest.raises(InputValueError):
            await run_node(GetAsset(), scene=scene, inputs={"asset_id": "absent"})

    @pytest.mark.asyncio
    async def test_empty_asset_id_returns_silently(self, scene):
        # The node early-returns when asset_id is unset. No outputs are
        # populated and run() completes without raising.
        out = await run_node(GetAsset(), scene=scene, inputs={"asset_id": ""})
        # `asset` socket retains its default UNRESOLVED.
        assert out["asset"] is UNRESOLVED


# ---------------------------------------------------------------------------
# GetAssets
# ---------------------------------------------------------------------------


class TestGetAssetsNode:
    @pytest.mark.asyncio
    async def test_returns_assets_and_count(self, scene):
        a = await scene.assets.add_asset(b"a", "png", "image/png")
        b = await scene.assets.add_asset(b"b", "png", "image/png")
        out = await run_node(
            GetAssets(),
            scene=scene,
            inputs={"asset_ids": [a.id, b.id]},
        )
        assert out["asset_count"] == 2
        assert sorted(out["asset_ids"]) == sorted([a.id, b.id])
        assert {asset.id for asset in out["assets"]} == {a.id, b.id}

    @pytest.mark.asyncio
    async def test_skips_unknown_ids(self, scene):
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        out = await run_node(
            GetAssets(),
            scene=scene,
            inputs={"asset_ids": [a.id, "missing"]},
        )
        # Missing ids are dropped, not raised.
        assert out["asset_count"] == 1
        assert out["asset_ids"] == [a.id]

    @pytest.mark.asyncio
    async def test_non_list_input_normalized_to_empty(self, scene):
        out = await run_node(
            GetAssets(), scene=scene, inputs={"asset_ids": "not-a-list"}
        )
        assert out["asset_ids"] == []
        assert out["asset_count"] == 0


# ---------------------------------------------------------------------------
# AssetExists
# ---------------------------------------------------------------------------


class TestAssetExistsNode:
    @pytest.mark.asyncio
    async def test_present_yes_branch(self, scene):
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        out = await run_node(AssetExists(), scene=scene, inputs={"asset_id": a.id})
        assert out["yes"] == a.id
        assert out["no"] is UNRESOLVED
        assert out["yes__deactivated"] is False
        assert out["no__deactivated"] is True

    @pytest.mark.asyncio
    async def test_absent_no_branch(self, scene):
        out = await run_node(AssetExists(), scene=scene, inputs={"asset_id": "ghost"})
        assert out["yes"] is UNRESOLVED
        assert out["no"] == "ghost"
        assert out["yes__deactivated"] is True
        assert out["no__deactivated"] is False

    @pytest.mark.asyncio
    async def test_return_bool_mode(self, scene):
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        node = AssetExists()
        node.set_property("return_bool", True)
        out = await run_node(
            node, scene=scene, inputs={"asset_id": a.id, "return_bool": True}
        )
        assert out["yes"] is True
        assert out["no"] is UNRESOLVED

    @pytest.mark.asyncio
    async def test_allow_partial(self, scene):
        a = await scene.assets.add_asset(b"unique-content", "png", "image/png")
        # Use the first 6 chars as a prefix probe
        prefix = a.id[:6]
        node = AssetExists()
        node.set_property("allow_partial", True)
        out = await run_node(
            node, scene=scene, inputs={"asset_id": prefix, "allow_partial": True}
        )
        # yes branch activated, value is the prefix asset_id
        assert out["yes"] == prefix


# ---------------------------------------------------------------------------
# RemoveAsset
# ---------------------------------------------------------------------------


class TestRemoveAssetNode:
    @pytest.mark.asyncio
    async def test_removes_existing_asset(self, scene):
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        assert a.id in scene.assets.assets
        out = await run_node(
            RemoveAsset(),
            scene=scene,
            inputs={"state": "ok", "asset_id": a.id},
        )
        assert out["asset_id"] == a.id
        assert a.id not in scene.assets.assets


# ---------------------------------------------------------------------------
# ListAssets
# ---------------------------------------------------------------------------


class TestListAssetsNode:
    @pytest.mark.asyncio
    async def test_lists_all_assets(self, scene):
        a = await scene.assets.add_asset(b"a", "png", "image/png")
        b = await scene.assets.add_asset(b"b", "png", "image/png")
        out = await run_node(ListAssets(), scene=scene)
        assert out["asset_count"] == 2
        assert sorted(out["asset_ids"]) == sorted([a.id, b.id])

    @pytest.mark.asyncio
    async def test_reference_only_filters(self, scene):
        # First asset is a "reference" type, second is not.
        a = await scene.assets.add_asset(
            b"a",
            "png",
            "image/png",
            meta=AssetMeta(reference=[VIS_TYPE.CHARACTER_PORTRAIT]),
        )
        await scene.assets.add_asset(b"b", "png", "image/png")

        node = ListAssets()
        node.set_property("reference_only", True)
        out = await run_node(node, scene=scene)
        assert out["asset_ids"] == [a.id]


# ---------------------------------------------------------------------------
# SearchAssets
# ---------------------------------------------------------------------------


@pytest.fixture
async def populated_scene(scene):
    """Three assets with diverse meta for filter tests."""
    scene.assets.save_asset(
        Asset(
            id="alice_portrait",
            file_type="png",
            media_type="image/png",
            meta=AssetMeta(
                vis_type=VIS_TYPE.CHARACTER_PORTRAIT,
                character_name="Alice",
                tags=["red", "Hero"],
                reference=[VIS_TYPE.CHARACTER_PORTRAIT],
            ),
        )
    )
    scene.assets.save_asset(
        Asset(
            id="bob_card",
            file_type="png",
            media_type="image/png",
            meta=AssetMeta(
                vis_type=VIS_TYPE.CHARACTER_CARD,
                character_name="Bob",
                tags=["blue"],
                reference=[VIS_TYPE.SCENE_CARD],
            ),
        )
    )
    scene.assets.save_asset(
        Asset(
            id="scene_bg",
            file_type="png",
            media_type="image/png",
            meta=AssetMeta(
                vis_type=VIS_TYPE.SCENE_BACKGROUND,
                tags=[],
                reference=[],
            ),
        )
    )
    return scene


class TestSearchAssetsNode:
    @pytest.mark.asyncio
    async def test_filter_by_vis_type(self, populated_scene):
        out = await run_node(
            SearchAssets(),
            scene=populated_scene,
            inputs={"vis_type": VIS_TYPE.CHARACTER_PORTRAIT.value},
        )
        assert out["asset_ids"] == ["alice_portrait"]
        assert out["asset_count"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_vis_type_list(self, populated_scene):
        out = await run_node(
            SearchAssets(),
            scene=populated_scene,
            inputs={
                "vis_type": [
                    VIS_TYPE.CHARACTER_PORTRAIT.value,
                    VIS_TYPE.CHARACTER_CARD.value,
                ]
            },
        )
        assert sorted(out["asset_ids"]) == ["alice_portrait", "bob_card"]

    @pytest.mark.asyncio
    async def test_filter_by_character_name(self, populated_scene):
        out = await run_node(
            SearchAssets(),
            scene=populated_scene,
            inputs={"character_name": "alice"},
        )
        assert out["asset_ids"] == ["alice_portrait"]

    @pytest.mark.asyncio
    async def test_tags_with_match_any(self, populated_scene):
        node = SearchAssets()
        node.set_property("tag_match_mode", TAG_MATCH_MODE.ANY.value)
        out = await run_node(
            node,
            scene=populated_scene,
            inputs={"tags": ["red", "blue"]},
        )
        assert sorted(out["asset_ids"]) == ["alice_portrait", "bob_card"]

    @pytest.mark.asyncio
    async def test_invalid_vis_type_raises(self, populated_scene):
        with pytest.raises(InputValueError):
            await run_node(
                SearchAssets(),
                scene=populated_scene,
                inputs={"vis_type": "NOT_A_REAL_TYPE"},
            )

    @pytest.mark.asyncio
    async def test_invalid_vis_type_in_list_raises(self, populated_scene):
        with pytest.raises(InputValueError):
            await run_node(
                SearchAssets(),
                scene=populated_scene,
                inputs={"vis_type": ["NOT_A_REAL_TYPE"]},
            )

    @pytest.mark.asyncio
    async def test_invalid_reference_vis_types_raises(self, populated_scene):
        with pytest.raises(InputValueError):
            await run_node(
                SearchAssets(),
                scene=populated_scene,
                inputs={"reference_vis_types": ["NOT_REAL"]},
            )

    @pytest.mark.asyncio
    async def test_invalid_tag_match_mode_raises(self, populated_scene):
        node = SearchAssets()
        node.set_property("tag_match_mode", "weird")
        with pytest.raises(InputValueError):
            await run_node(node, scene=populated_scene)


# ---------------------------------------------------------------------------
# MakeAssetMeta
# ---------------------------------------------------------------------------


class TestMakeAssetMetaNode:
    @pytest.mark.asyncio
    async def test_builds_meta_from_inputs(self):
        out = await run_node(
            MakeAssetMeta(),
            inputs={
                "name": "hero",
                "vis_type": VIS_TYPE.CHARACTER_PORTRAIT.value,
                "gen_type": GEN_TYPE.TEXT_TO_IMAGE.value,
                "character_name": "Alice",
                "prompt": "regal pose",
                "negative_prompt": "blurry",
                "tags": ["t1"],
                "reference": [VIS_TYPE.CHARACTER_CARD.value],
            },
        )
        meta = out["meta"]
        assert meta.name == "hero"
        assert meta.vis_type == VIS_TYPE.CHARACTER_PORTRAIT
        assert meta.gen_type == GEN_TYPE.TEXT_TO_IMAGE
        assert meta.character_name == "Alice"
        assert meta.prompt == "regal pose"
        assert meta.negative_prompt == "blurry"
        assert meta.tags == ["t1"]
        assert meta.reference == [VIS_TYPE.CHARACTER_CARD]
        # `format` derived from vis_type via VIS_TYPE_TO_FORMAT
        assert out["format"] == FORMAT_TYPE.SQUARE  # CHARACTER_PORTRAIT -> SQUARE

    @pytest.mark.asyncio
    async def test_invalid_vis_type_raises(self):
        with pytest.raises(InputValueError):
            await run_node(MakeAssetMeta(), inputs={"vis_type": "NOT_REAL"})

    @pytest.mark.asyncio
    async def test_invalid_gen_type_raises(self):
        with pytest.raises(InputValueError):
            await run_node(MakeAssetMeta(), inputs={"gen_type": "NOT_REAL"})

    @pytest.mark.asyncio
    async def test_invalid_reference_vis_type_raises(self):
        with pytest.raises(InputValueError):
            await run_node(MakeAssetMeta(), inputs={"reference": ["NOT_REAL"]})


# ---------------------------------------------------------------------------
# UnpackAssetMeta
# ---------------------------------------------------------------------------


class TestUnpackAssetMetaNode:
    @pytest.mark.asyncio
    async def test_returns_each_field(self):
        meta = AssetMeta(
            name="x",
            vis_type=VIS_TYPE.CHARACTER_PORTRAIT,
            character_name="Alice",
            prompt="hello",
            tags=["a"],
        )
        meta.set_dimensions(100, 200)
        out = await run_node(UnpackAssetMeta(), inputs={"meta": meta})
        assert out["name"] == "x"
        assert out["vis_type"] == VIS_TYPE.CHARACTER_PORTRAIT
        assert out["character_name"] == "Alice"
        assert out["prompt"] == "hello"
        assert out["tags"] == ["a"]
        assert out["width"] == 100
        assert out["height"] == 200
        assert out["format"] == FORMAT_TYPE.PORTRAIT

    @pytest.mark.asyncio
    async def test_no_resolution_returns_none_dimensions(self):
        meta = AssetMeta(name="x")
        out = await run_node(UnpackAssetMeta(), inputs={"meta": meta})
        assert out["width"] is None
        assert out["height"] is None


# ---------------------------------------------------------------------------
# MakeAssetAttachmentContext
# ---------------------------------------------------------------------------


class TestMakeAssetAttachmentContextNode:
    @pytest.mark.asyncio
    async def test_constructs_context(self):
        out = await run_node(
            MakeAssetAttachmentContext(),
            inputs={
                "asset_name": "hero",
                "tags": ["t1"],
                "allow_auto_attach": True,
                "scene_cover": True,
                "message_ids": [1, 2],
            },
        )
        ctx: AssetAttachmentContext = out["context"]
        assert ctx.asset_name == "hero"
        assert ctx.tags == ["t1"]
        assert ctx.allow_auto_attach is True
        assert ctx.scene_cover is True
        assert ctx.message_ids == [1, 2]


# ---------------------------------------------------------------------------
# SetCoverImage
# ---------------------------------------------------------------------------


class TestSetCoverImageNode:
    @pytest.mark.asyncio
    async def test_sets_scene_cover(self, scene):
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        out = await run_node(
            SetCoverImage(),
            scene=scene,
            inputs={
                "state": "ok",
                "asset_id": a.id,
                "set_on_scene": True,
                "override_scene": True,
            },
        )
        assert scene.assets.cover_image == a.id
        assert out["asset_id"] == a.id

    @pytest.mark.asyncio
    async def test_sets_character_cover(self, scene):
        char = Character(name="Alice")
        scene.character_data["Alice"] = char
        a = await scene.assets.add_asset(b"y", "png", "image/png")
        await run_node(
            SetCoverImage(),
            scene=scene,
            inputs={
                "state": "ok",
                "asset_id": a.id,
                "character": char,
                "override_character": True,
            },
        )
        assert char.cover_image == a.id


# ---------------------------------------------------------------------------
# SetAvatarImage
# ---------------------------------------------------------------------------


class TestSetAvatarImageNode:
    @pytest.mark.asyncio
    async def test_default_avatar(self, scene):
        char = Character(name="Alice")
        scene.character_data["Alice"] = char
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        await run_node(
            SetAvatarImage(),
            scene=scene,
            inputs={
                "state": "ok",
                "asset_id": a.id,
                "character": char,
                "avatar_type": "default",
            },
        )
        assert char.avatar == a.id

    @pytest.mark.asyncio
    async def test_current_avatar(self, scene):
        char = Character(name="Alice")
        scene.character_data["Alice"] = char
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        await run_node(
            SetAvatarImage(),
            scene=scene,
            inputs={
                "state": "ok",
                "asset_id": a.id,
                "character": char,
                "avatar_type": "current",
            },
        )
        assert char.current_avatar == a.id

    @pytest.mark.asyncio
    async def test_invalid_asset_id_raises(self, scene):
        char = Character(name="Alice")
        scene.character_data["Alice"] = char
        with pytest.raises(InputValueError):
            await run_node(
                SetAvatarImage(),
                scene=scene,
                inputs={
                    "state": "ok",
                    "asset_id": "ghost",
                    "character": char,
                    "avatar_type": "default",
                },
            )

    @pytest.mark.asyncio
    async def test_invalid_avatar_type_raises(self, scene):
        char = Character(name="Alice")
        scene.character_data["Alice"] = char
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        with pytest.raises(InputValueError):
            await run_node(
                SetAvatarImage(),
                scene=scene,
                inputs={
                    "state": "ok",
                    "asset_id": a.id,
                    "character": char,
                    "avatar_type": "weird",
                },
            )


# ---------------------------------------------------------------------------
# UpdateMessageAsset
# ---------------------------------------------------------------------------


class TestUpdateMessageAssetNode:
    @pytest.mark.asyncio
    async def test_updates_message_asset(self, scene):
        a = await scene.assets.add_asset(
            b"x",
            "png",
            "image/png",
            meta=AssetMeta(vis_type=VIS_TYPE.CHARACTER_PORTRAIT),
        )
        msg = CharacterMessage(message="Alice: hi")
        scene.history = [msg]
        out = await run_node(
            UpdateMessageAsset(),
            scene=scene,
            inputs={
                "state": "ok",
                "message_ids": [msg.id],
                "asset_id": a.id,
            },
        )
        assert msg.asset_id == a.id
        assert out["messages"] == [msg]

    @pytest.mark.asyncio
    async def test_invalid_asset_id_type_raises(self, scene):
        msg = CharacterMessage(message="Alice: hi")
        scene.history = [msg]
        with pytest.raises(InputValueError):
            await run_node(
                UpdateMessageAsset(),
                scene=scene,
                inputs={
                    "state": "ok",
                    "message_ids": [msg.id],
                    "asset_id": 123,  # not a string
                },
            )

    @pytest.mark.asyncio
    async def test_invalid_message_ids_type_raises(self, scene):
        a = await scene.assets.add_asset(b"x", "png", "image/png")
        with pytest.raises(InputValueError):
            await run_node(
                UpdateMessageAsset(),
                scene=scene,
                inputs={
                    "state": "ok",
                    "message_ids": "not-a-list",
                    "asset_id": a.id,
                },
            )


# ---------------------------------------------------------------------------
# SelectAssets / UnpackAssetSelectionContext
# ---------------------------------------------------------------------------


class TestSelectAssetsNode:
    @pytest.mark.asyncio
    async def test_first_node_creates_context_and_filters(self, populated_scene):
        # First node in a chain — `selection_context` is None on entry.
        out = await run_node(
            SelectAssets(),
            scene=populated_scene,
            inputs={
                "asset_ids": ["alice_portrait", "bob_card", "scene_bg"],
                "vis_types": [VIS_TYPE.CHARACTER_PORTRAIT.value],
            },
        )
        assert out["asset_ids"] == ["alice_portrait"]
        assert out["asset_count"] == 1
        assert out["selected"] is True
        assert out["selection_context"].selected is True

    @pytest.mark.asyncio
    async def test_noop_skips_when_already_selected(self, populated_scene):
        # If selection_context already has a selection in noop mode, the
        # node short-circuits and emits the existing selection.
        ctx = AssetSelectionContext(
            mode="noop",
            selected=True,
            selected_asset_ids=["alice_portrait"],
            original_asset_ids=["alice_portrait", "bob_card"],
        )
        out = await run_node(
            SelectAssets(),
            scene=populated_scene,
            inputs={
                "asset_ids": [],
                "selection_context": ctx,
                "vis_types": [VIS_TYPE.CHARACTER_CARD.value],
            },
        )
        # Selection unchanged, `selected` is False because *this* node didn't
        # contribute.
        assert out["asset_ids"] == ["alice_portrait"]
        assert out["selected"] is False

    @pytest.mark.asyncio
    async def test_prioritize_concats_results(self, populated_scene):
        ctx = AssetSelectionContext(
            mode="prioritize",
            selected=True,
            selected_asset_ids=["alice_portrait"],
            original_asset_ids=["alice_portrait", "bob_card", "scene_bg"],
        )
        out = await run_node(
            SelectAssets(),
            scene=populated_scene,
            inputs={
                "asset_ids": [],
                "selection_context": ctx,
                "vis_types": [VIS_TYPE.CHARACTER_CARD.value],
            },
        )
        # bob_card is appended after alice_portrait
        assert out["asset_ids"] == ["alice_portrait", "bob_card"]

    @pytest.mark.asyncio
    async def test_invalid_vis_type_raises(self, populated_scene):
        with pytest.raises(InputValueError):
            await run_node(
                SelectAssets(),
                scene=populated_scene,
                inputs={
                    "asset_ids": ["alice_portrait"],
                    "vis_types": ["NOT_REAL"],
                },
            )

    @pytest.mark.asyncio
    async def test_invalid_reference_vis_type_raises(self, populated_scene):
        with pytest.raises(InputValueError):
            await run_node(
                SelectAssets(),
                scene=populated_scene,
                inputs={
                    "asset_ids": ["alice_portrait"],
                    "reference_vis_types": ["NOT_REAL"],
                },
            )


class TestUnpackAssetSelectionContextNode:
    @pytest.mark.asyncio
    async def test_returns_unpacked_fields(self, populated_scene):
        ctx = AssetSelectionContext(
            mode="prioritize",
            selected=True,
            selected_asset_ids=["alice_portrait", "bob_card"],
            original_asset_ids=["alice_portrait", "bob_card", "scene_bg"],
        )
        out = await run_node(
            UnpackAssetSelectionContext(),
            scene=populated_scene,
            inputs={"selection_context": ctx},
        )
        assert out["mode"] == "prioritize"
        assert out["selected"] is True
        assert out["asset_count"] == 2
        assert out["asset_ids"] == ["alice_portrait", "bob_card"]
        assert out["original_asset_ids"] == [
            "alice_portrait",
            "bob_card",
            "scene_bg",
        ]
        # first / last asset references look up real Asset objects
        assert out["first"].id == "alice_portrait"
        assert out["last"].id == "bob_card"

    @pytest.mark.asyncio
    async def test_empty_selection_omits_first_and_last(self, populated_scene):
        ctx = AssetSelectionContext(mode="noop", selected=False)
        out = await run_node(
            UnpackAssetSelectionContext(),
            scene=populated_scene,
            inputs={"selection_context": ctx},
        )
        # Empty selection -> first/last default to UNRESOLVED.
        assert out["first"] is UNRESOLVED
        assert out["last"] is UNRESOLVED
        assert out["asset_count"] == 0
