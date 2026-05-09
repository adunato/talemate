"""Coverage-focused unit tests for talemate.game.engine.nodes.registry.

The registry is a process-global singleton (NODES). To avoid bleeding test
state into the real registry, every test that mutates NODES uses an
isolated `container` dict and the `register(..., container=...)` form, or
restores NODES after the test.

Filesystem-based importers (`import_scene_node_definitions`,
`import_talemate_node_definitions`) are exercised via tmp_path with real
JSON files.
"""

from __future__ import annotations

import json
import os

import pytest

from talemate.context import ActiveScene

# Import data nodes module so that the data/* registry entries are
# populated. We test discovery via `get_node("data/Sort")` below.
from talemate.game.engine.nodes import data as _data_module  # noqa: F401
from talemate.game.engine.nodes import registry as registry_module
from talemate.game.engine.nodes.registry import (
    NODES,
    NodeNotFoundError,
    export_node_definitions,
    get_node,
    get_nodes_by_base_type,
    import_node_definition,
    import_node_definitions,
    import_scene_node_definitions,
    import_talemate_node_definitions,
    normalize_registry_name,
    register,
    validate_registry_path,
)
from talemate.tale_mate import Scene


# ---------------------------------------------------------------------------
# normalize_registry_name
# ---------------------------------------------------------------------------


class TestNormalizeRegistryName:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("my node", "myNode"),
            ("My Node", "myNode"),
            ("My-Node", "myNode"),
            ("My  Other  Node", "myOtherNode"),
            ("a", "a"),
            ("AB", "ab"),
            ("foo_bar", "fooBar"),
        ],
    )
    def test_normalizes_to_camel_case(self, raw, expected):
        assert normalize_registry_name(raw) == expected


# ---------------------------------------------------------------------------
# register decorator (container override + as_base_type)
# ---------------------------------------------------------------------------


class TestRegisterDecorator:
    def test_register_in_isolated_container(self):
        # Use an isolated container so we don't mutate the global NODES.
        from talemate.game.engine.nodes.core import Node

        container = {}

        @register("test/IsolatedRegister", container=container)
        class FakeNode(Node):
            pass

        assert "test/IsolatedRegister" in container
        assert container["test/IsolatedRegister"] is FakeNode
        # _registry is set on the class
        assert FakeNode._registry == "test/IsolatedRegister"
        # Global registry is untouched
        assert "test/IsolatedRegister" not in NODES

    def test_register_as_base_type_also_registers_base_type(self):
        from talemate.game.engine.nodes.base_types import BASE_TYPES, get_base_type
        from talemate.game.engine.nodes.core import Node

        container = {}
        original_base = dict(BASE_TYPES)
        try:

            @register("test/BaseTypeRegister", as_base_type=True, container=container)
            class FakeNode(Node):
                pass

            assert get_base_type("test/BaseTypeRegister") is FakeNode
        finally:
            # Restore BASE_TYPES so we don't leak state across tests.
            BASE_TYPES.clear()
            BASE_TYPES.update(original_base)


# ---------------------------------------------------------------------------
# get_node / NodeNotFoundError — depend on active_scene contextvar
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_scene():
    """Scene used only as a holder for `_NODE_DEFINITIONS` lookups."""
    s = Scene()
    s._NODE_DEFINITIONS = {}
    return s


class TestGetNode:
    def test_returns_none_for_falsy_name(self, stub_scene):
        with ActiveScene(stub_scene):
            assert get_node("") is None
            assert get_node(None) is None

    def test_finds_in_global_registry(self, stub_scene):
        with ActiveScene(stub_scene):
            # Use a known-built-in node from the global registry.
            cls = get_node("data/Sort")
            assert cls is not None
            assert cls.__name__ == "Sort"

    def test_scene_node_takes_priority(self, stub_scene):
        from talemate.game.engine.nodes.core import Node

        class SceneSpecificNode(Node):
            pass

        stub_scene._NODE_DEFINITIONS["test/SceneOnly"] = SceneSpecificNode
        with ActiveScene(stub_scene):
            assert get_node("test/SceneOnly") is SceneSpecificNode

    def test_unknown_name_raises(self, stub_scene):
        with ActiveScene(stub_scene):
            with pytest.raises(NodeNotFoundError, match="not found"):
                get_node("test/DoesNotExist__definitely__")


# ---------------------------------------------------------------------------
# get_nodes_by_base_type
# ---------------------------------------------------------------------------


class TestGetNodesByBaseType:
    def test_returns_classes_with_matching_base_type(self, stub_scene):
        # Real built-in nodes have base types from BASE_TYPES — Graph nodes
        # have base_type "core/Graph". Use it as a smoke filter that returns
        # at least one match.
        with ActiveScene(stub_scene):
            results = get_nodes_by_base_type("core/Graph")
        assert isinstance(results, list)
        # All matches' _base_type must be "core/Graph"
        assert all(cls._base_type == "core/Graph" for cls in results)

    def test_scene_nodes_overlay_global(self, stub_scene):
        from talemate.game.engine.nodes.core import Node

        class SceneNode(Node):
            _base_type = "test/Custom"

        stub_scene._NODE_DEFINITIONS["test/SceneCustom"] = SceneNode
        with ActiveScene(stub_scene):
            results = get_nodes_by_base_type("test/Custom")
        assert SceneNode in results


# ---------------------------------------------------------------------------
# validate_registry_path
# ---------------------------------------------------------------------------


class TestValidateRegistryPath:
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            validate_registry_path("")

    def test_single_part_raises(self):
        with pytest.raises(ValueError, match="at least two parts"):
            validate_registry_path("oneword")

    def test_valid_path_passes(self):
        # Two-part path is acceptable as long as it's not a prefix of an
        # existing path.
        validate_registry_path("test/Brand_New", node_definitions={"nodes": {}})

    def test_prefix_collision_raises(self):
        defs = {"nodes": {"foo/bar/baz": {}}}
        with pytest.raises(ValueError, match="colliding"):
            validate_registry_path("foo/bar", node_definitions=defs)


# ---------------------------------------------------------------------------
# import_node_definition / dynamic_node_import via the registry's import path
# ---------------------------------------------------------------------------


class TestImportNodeDefinition:
    def test_creates_dynamic_node_class_when_missing(self):
        container = {}
        node_data = {
            "registry": "test/DynamicReg",
            "base_type": "core/Graph",
            "title": "Dynamic",
            "nodes": {},
            "edges": {},
        }
        cls = import_node_definition(node_data, registry=container)
        assert "test/DynamicReg" in container
        assert cls is container["test/DynamicReg"]
        assert cls._base_type == "core/Graph"

    def test_uses_existing_class_in_container(self):
        from talemate.game.engine.nodes.core import Graph

        container = {"test/Existing": Graph}
        node_data = {
            "registry": "test/Existing",
            "base_type": "core/Graph",
            "title": "Reuse",
            "nodes": {},
            "edges": {},
        }
        cls = import_node_definition(node_data, registry=container)
        # Existing class is reused.
        assert cls is Graph

    def test_unknown_base_type_raises(self):
        container = {}
        node_data = {
            "registry": "test/Bad",
            "base_type": "core/NotARealBaseType",
            "title": "X",
            "nodes": {},
            "edges": {},
        }
        with pytest.raises(ValueError):
            import_node_definition(node_data, registry=container)


class TestImportNodeDefinitions:
    def test_imports_each_node(self):
        # Patch into NODES temporarily for the test (import_node_definitions
        # always writes to NODES — see registry source). Restore on exit.
        try:
            data = {
                "nodes": [
                    {
                        "registry": "test/Bulk1",
                        "base_type": "core/Graph",
                        "title": "B1",
                        "nodes": {},
                        "edges": {},
                    },
                    {
                        "registry": "test/Bulk2",
                        "base_type": "core/Graph",
                        "title": "B2",
                        "nodes": {},
                        "edges": {},
                    },
                ]
            }
            import_node_definitions(data)
            assert "test/Bulk1" in NODES
            assert "test/Bulk2" in NODES
        finally:
            # Strip what we added so the global registry is unchanged.
            for key in ("test/Bulk1", "test/Bulk2"):
                NODES.pop(key, None)
            assert "test/Bulk1" not in NODES


# ---------------------------------------------------------------------------
# import_scene_node_definitions — filesystem-based; uses tmp_path
# ---------------------------------------------------------------------------


class TestImportSceneNodeDefinitions:
    @pytest.fixture
    def fs_scene(self, tmp_path, monkeypatch):
        # Use a Scene with a tmp save_dir so its `nodes_dir` is real.
        monkeypatch.setattr(
            Scene,
            "scenes_dir",
            classmethod(lambda cls: str(tmp_path)),
            raising=True,
        )
        scene = Scene()
        scene.project_name = "proj_node_defs"
        os.makedirs(scene.nodes_dir, exist_ok=True)
        return scene

    def test_skips_when_nodes_dir_missing(self, fs_scene, monkeypatch):
        # Override nodes_dir to a non-existent path; should early-return.
        monkeypatch.setattr(
            type(fs_scene),
            "nodes_dir",
            property(lambda self: "/no/such/dir"),
        )
        # _NODE_DEFINITIONS is created and remains empty
        import_scene_node_definitions(fs_scene)
        assert fs_scene._NODE_DEFINITIONS == {}

    def test_loads_definitions_from_directory(self, fs_scene):
        # Drop a JSON node definition next to the scene loop file.
        node_def = {
            "registry": "test/SceneNodeDef",
            "base_type": "core/Graph",
            "title": "Scene",
            "nodes": {},
            "edges": {},
        }
        path = os.path.join(fs_scene.nodes_dir, "node_def.json")
        with open(path, "w") as f:
            json.dump(node_def, f)

        import_scene_node_definitions(fs_scene)
        assert "test/SceneNodeDef" in fs_scene._NODE_DEFINITIONS

    def test_skips_scene_loop_file(self, fs_scene):
        # The file matching scene.nodes_filename must NOT be imported as a
        # node definition.
        scene_loop_data = {
            "registry": "test/ShouldNotImport",
            "base_type": "core/Graph",
            "title": "Loop",
            "nodes": {},
            "edges": {},
        }
        loop_path = os.path.join(fs_scene.nodes_dir, fs_scene.nodes_filename)
        with open(loop_path, "w") as f:
            json.dump(scene_loop_data, f)

        import_scene_node_definitions(fs_scene)
        assert "test/ShouldNotImport" not in fs_scene._NODE_DEFINITIONS

    def test_skips_definition_without_registry(self, fs_scene):
        # JSON files without a `registry` field are warned about and skipped.
        path = os.path.join(fs_scene.nodes_dir, "no_registry.json")
        with open(path, "w") as f:
            json.dump({"title": "anonymous"}, f)
        import_scene_node_definitions(fs_scene)
        assert fs_scene._NODE_DEFINITIONS == {}


# ---------------------------------------------------------------------------
# import_talemate_node_definitions — filesystem; uses monkeypatched SEARCH_PATHS
# ---------------------------------------------------------------------------


class TestImportTalemateNodeDefinitions:
    def test_loads_definitions_from_search_paths(self, tmp_path, monkeypatch):
        # Replace SEARCH_PATHS with our tmp dir and drop a JSON file in it.
        custom_dir = tmp_path / "modules"
        custom_dir.mkdir()
        node_def = {
            "registry": "test/SearchPathNode",
            "base_type": "core/Graph",
            "title": "X",
            "nodes": {},
            "edges": {},
        }
        (custom_dir / "node.json").write_text(json.dumps(node_def))

        monkeypatch.setattr(registry_module, "SEARCH_PATHS", [str(custom_dir)])

        try:
            import_talemate_node_definitions()
            assert "test/SearchPathNode" in NODES
            cls = NODES["test/SearchPathNode"]
            assert cls._module_path  # set on success
        finally:
            NODES.pop("test/SearchPathNode", None)


# ---------------------------------------------------------------------------
# export_node_definitions — depends on active_scene
# ---------------------------------------------------------------------------


class TestExportNodeDefinitions:
    def test_export_returns_node_dict_keyed_by_registry(self, stub_scene):
        with ActiveScene(stub_scene):
            export = export_node_definitions()
        assert "nodes" in export
        # Top-level value is keyed by registry path.
        assert isinstance(export["nodes"], dict)
        # All entries must include the marker fields populated by the
        # exporter.
        for reg_name, defn in export["nodes"].items():
            assert defn["registry"] == reg_name
            assert "fields" in defn
            assert "selectable" in defn
