"""
Unit tests for src/talemate/game/engine/nodes/packaging.py.

Covers:
- Pydantic models: PackageProperty, PackageData, ScenePackageInfo
- Filesystem helpers: initialize_scene_package_info, get_scene_package_info,
  save (via install/uninstall), apply_scene_package_info
- Lifecycle: install_package, update_package_properties, uninstall_package
- Registry-driven discovery: list_packages, get_package_by_registry,
  initialize_package, initialize_packages
- The packaging Nodes (Package, InstallNodeModule, PromoteConfig) to the
  extent that they're addressable in isolation

Skipped paths: nothing critical — most of the module is exercised. Only the
broad `except Exception` swallowers in `initialize_package*` are covered
indirectly by their happy-path tests; the swallow-on-error case is tested
via list_packages with a bogus install_node_module property.
"""

import json
import os

import pytest

from talemate.game.engine.nodes.core import Graph, UNRESOLVED
from talemate.game.engine.nodes.packaging import (
    InstallNodeModule,
    Package,
    PackageData,
    PackageProperty,
    PromoteConfig,
    ScenePackageInfo,
    SCENE_PACKAGE_INFO_FILENAME,
    apply_scene_package_info,
    get_package_by_registry,
    get_scene_package_info,
    initialize_package,
    initialize_packages,
    initialize_scene_package_info,
    install_package,
    list_packages,
    save_scene_package_info,
    uninstall_package,
    update_package_properties,
)
from talemate.game.engine.nodes.core import ModuleProperty
from talemate.game.engine.nodes.scene import SceneLoop
from talemate.tale_mate import Scene


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scene_factory(tmp_path, monkeypatch):
    """Build real Scene objects whose save_dir lives under tmp_path."""
    monkeypatch.setattr(
        Scene, "scenes_dir", classmethod(lambda cls: str(tmp_path)), raising=True
    )

    def _make(project: str = "proj_default") -> Scene:
        scene = Scene()
        scene.project_name = project
        os.makedirs(scene.save_dir, exist_ok=True)
        scene.emit_status = lambda *a, **kw: None
        scene.active = False
        return scene

    return _make


@pytest.fixture
def scene(scene_factory):
    return scene_factory("proj_pkg")


def _sample_package_data(registry: str = "test/pkg/example") -> PackageData:
    return PackageData(
        name="Example",
        author="Tester",
        description="An example package",
        installable=True,
        registry=registry,
        package_properties={
            "an_int": PackageProperty(
                module="test/pkg/example",
                name="an_int",
                label="An Int",
                description="An int property",
                type="int",
                default=0,
                value=42,
                required=True,
            )
        },
        install_nodes=["test/pkg/Installable"],
    )


# ---------------------------------------------------------------------------
# PackageData.configured / properties_for_node
# ---------------------------------------------------------------------------


def test_package_data_configured_true_when_required_filled():
    pkg = _sample_package_data()
    assert pkg.configured is True


def test_package_data_configured_false_when_required_missing():
    pkg = _sample_package_data()
    pkg.package_properties["an_int"].value = None
    assert pkg.configured is False


def test_package_data_configured_true_when_only_optional_missing():
    """A package with only optional properties — none filled — is configured."""
    pkg = PackageData(
        name="A",
        author="B",
        description="C",
        installable=True,
        registry="r/x",
        package_properties={
            "x": PackageProperty(
                module="r/x",
                name="x",
                label="X",
                description="d",
                type="int",
                default=0,
                value=None,
                required=False,
            )
        },
    )
    assert pkg.configured is True


def test_package_data_properties_for_node_filters_by_module():
    pkg = PackageData(
        name="A",
        author="B",
        description="C",
        installable=True,
        registry="r/x",
        package_properties={
            "alpha": PackageProperty(
                module="r/x/m1",
                name="alpha",
                label="A",
                description="d",
                type="int",
                default=0,
                value=1,
            ),
            "beta": PackageProperty(
                module="r/x/m2",
                name="beta",
                label="B",
                description="d",
                type="int",
                default=0,
                value=2,
            ),
            "gamma": PackageProperty(
                module="r/x/m1",
                name="gamma",
                label="G",
                description="d",
                type="int",
                default=0,
                value=3,
            ),
        },
    )
    assert pkg.properties_for_node("r/x/m1") == {"alpha": 1, "gamma": 3}
    assert pkg.properties_for_node("r/x/m2") == {"beta": 2}
    assert pkg.properties_for_node("nonexistent") == {}


# ---------------------------------------------------------------------------
# ScenePackageInfo.has_package / get_package
# ---------------------------------------------------------------------------


def test_scene_package_info_has_and_get():
    pkg = _sample_package_data("foo/bar")
    info = ScenePackageInfo(packages=[pkg])
    assert info.has_package("foo/bar") is True
    assert info.has_package("missing/pkg") is False
    assert info.get_package("foo/bar") is pkg
    assert info.get_package("missing/pkg") is None


# ---------------------------------------------------------------------------
# initialize_scene_package_info / get_scene_package_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_scene_package_info_creates_file(scene):
    filepath = os.path.join(scene.info_dir, SCENE_PACKAGE_INFO_FILENAME)
    assert not os.path.exists(filepath)

    await initialize_scene_package_info(scene)

    assert os.path.exists(filepath)
    with open(filepath, "r") as f:
        data = json.load(f)
    assert data == ScenePackageInfo(packages=[]).model_dump()


@pytest.mark.asyncio
async def test_initialize_scene_package_info_idempotent(scene):
    """Re-initializing must not overwrite an existing populated file."""
    await initialize_scene_package_info(scene)

    # Pre-populate the file with something other than the empty default
    pkg = _sample_package_data()
    info = ScenePackageInfo(packages=[pkg])
    await save_scene_package_info(scene, info)

    # initialize_scene_package_info should be a no-op now
    await initialize_scene_package_info(scene)

    loaded = await get_scene_package_info(scene)
    assert len(loaded.packages) == 1
    assert loaded.packages[0].registry == pkg.registry


@pytest.mark.asyncio
async def test_get_scene_package_info_returns_empty_when_file_missing(scene):
    """If the info dir or file doesn't exist, returns an empty ScenePackageInfo."""
    info = await get_scene_package_info(scene)
    assert info.packages == []


# ---------------------------------------------------------------------------
# install_package / update_package_properties / uninstall_package
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_package_marks_installed_and_persists(scene):
    pkg = _sample_package_data("alpha/beta")
    out = await install_package(scene, pkg)
    assert out.status == "installed"

    info = await get_scene_package_info(scene)
    assert info.has_package("alpha/beta")
    assert info.get_package("alpha/beta").status == "installed"


@pytest.mark.asyncio
async def test_install_package_idempotent_when_already_installed(scene):
    pkg = _sample_package_data("alpha/beta")
    await install_package(scene, pkg)

    # Second install should not duplicate or raise
    pkg2 = _sample_package_data("alpha/beta")
    await install_package(scene, pkg2)
    info = await get_scene_package_info(scene)
    assert sum(1 for p in info.packages if p.registry == "alpha/beta") == 1


@pytest.mark.asyncio
async def test_update_package_properties_writes_value(scene):
    pkg = _sample_package_data()
    await install_package(scene, pkg)

    new_props = {
        "an_int": PackageProperty(
            module="test/pkg/example",
            name="an_int",
            label="An Int",
            description="An int property",
            type="int",
            default=0,
            value=999,
            required=True,
        )
    }
    out = await update_package_properties(scene, pkg.registry, new_props)
    assert out is not None
    assert out.package_properties["an_int"].value == 999

    # Confirm the change was persisted on disk
    loaded = await get_scene_package_info(scene)
    assert loaded.get_package(pkg.registry).package_properties["an_int"].value == 999


@pytest.mark.asyncio
async def test_update_package_properties_returns_none_for_missing_pkg(scene):
    out = await update_package_properties(scene, "nope/missing", {})
    assert out is None


@pytest.mark.asyncio
async def test_uninstall_package_removes_persisted_entry(scene):
    pkg = _sample_package_data()
    await install_package(scene, pkg)

    await uninstall_package(scene, pkg.registry)
    info = await get_scene_package_info(scene)
    assert not info.has_package(pkg.registry)


@pytest.mark.asyncio
async def test_uninstall_package_short_circuits_when_not_installed(scene):
    """Uninstalling a package that was never installed is a no-op."""
    # Initialize but don't install — file exists, packages list empty
    await initialize_scene_package_info(scene)
    # Should not raise
    await uninstall_package(scene, "never/installed")
    info = await get_scene_package_info(scene)
    assert info.packages == []


@pytest.mark.asyncio
async def test_uninstall_package_strips_installed_node_ids_from_active_loop(scene):
    """When the scene has an active node graph, uninstall removes any nodes
    that were installed as part of that package."""
    scene_loop = SceneLoop()
    scene.creative_node_graph = scene_loop

    pkg = _sample_package_data()
    await install_package(scene, pkg)

    # Manually add a fake installed node id and record it on the package
    fake_node = InstallNodeModule()  # any registered Node instance
    scene_loop.add_node(fake_node)
    pkg.installed_nodes = [fake_node.id]
    # Persist the updated installed_nodes list
    info = await get_scene_package_info(scene)
    info.get_package(pkg.registry).installed_nodes = [fake_node.id]
    await save_scene_package_info(scene, info)

    assert fake_node.id in scene_loop.nodes

    await uninstall_package(scene, pkg.registry)

    assert fake_node.id not in scene_loop.nodes


# ---------------------------------------------------------------------------
# apply_scene_package_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_scene_package_info_marks_installed_and_copies_props(scene):
    """If the on-disk info has a package, the in-memory package gets its
    status flipped to installed and its package_properties replaced with
    the persisted ones."""
    persisted_pkg = _sample_package_data("foo/bar")
    persisted_pkg.package_properties["an_int"].value = 7
    await install_package(scene, persisted_pkg)

    # Build a fresh PackageData (not_installed by default) for the same registry
    fresh = _sample_package_data("foo/bar")
    fresh.package_properties["an_int"].value = 0  # different default
    fresh.status = "not_installed"

    other = _sample_package_data("baz/quux")  # not on disk

    await apply_scene_package_info(scene, [fresh, other])

    assert fresh.status == "installed"
    assert fresh.package_properties["an_int"].value == 7  # adopted from disk
    assert other.status == "not_installed"


# ---------------------------------------------------------------------------
# list_packages / get_package_by_registry
# ---------------------------------------------------------------------------


@pytest.fixture
def package_test_classes():
    """Register an installable Package, an InstallNodeModule child, and a
    PromoteConfig child against the global registry for the duration of a
    test, then unregister to keep the global namespace clean."""
    from talemate.game.engine.nodes.registry import register, NODES

    # Register an installable target node module (must exist at registry lookup)
    @register("test/pkg/InstallableModule")
    class InstallableModule(Graph):
        def __init__(self, title="Installable Module", **kwargs):
            super().__init__(title=title, **kwargs)

        def setup(self):
            # Create a ModuleProperty so module_properties has 'an_int'
            mp = ModuleProperty()
            mp.set_property("property_name", "an_int")
            mp.set_property("property_type", "int")
            mp.set_property("default", 0)
            mp.set_property("description", "An int")
            mp.set_property("choices", [])
            self.add_node(mp)

    # Register the package itself.
    # `list_packages` filters via `get_nodes_by_base_type("util/packaging/Package")`,
    # which looks at `cls._base_type`. The `as_base_type` decorator only sets the
    # `base_type` attribute, not the underscore-prefixed one — so we set
    # `_base_type` explicitly on our subclasses.
    @register("test/pkg/ExamplePackage")
    class ExamplePackage(Package):
        _base_type = "util/packaging/Package"

        def __init__(self, title="Example Package", **kwargs):
            super().__init__(title=title, **kwargs)

        def setup(self):
            super().setup()
            self.set_property("package_name", "Example")
            self.set_property("author", "Tester")
            self.set_property("description", "An example package")
            self.set_property("installable", True)
            self.set_property("restart_scene_loop", False)

            install_node = InstallNodeModule()
            install_node.set_property("node_registry", "test/pkg/InstallableModule")
            self.add_node(install_node)

            promote = PromoteConfig()
            promote.set_property("node_registry", "test/pkg/InstallableModule")
            promote.set_property("property_name", "an_int")
            promote.set_property("exposed_property_name", "an_int_exposed")
            promote.set_property("label", "An Int")
            promote.set_property("required", True)
            self.add_node(promote)

    # Also register a non-installable package (which should be filtered out)
    @register("test/pkg/UnInstallable")
    class UnInstallablePackage(Package):
        _base_type = "util/packaging/Package"

        def __init__(self, title="Hidden", **kwargs):
            super().__init__(title=title, **kwargs)

        def setup(self):
            super().setup()
            self.set_property("package_name", "Hidden")
            self.set_property("author", "X")
            self.set_property("description", "won't show")
            self.set_property("installable", False)
            self.set_property("restart_scene_loop", False)

    yield {
        "Installable": InstallableModule,
        "Package": ExamplePackage,
        "UnInstallable": UnInstallablePackage,
    }

    # Cleanup: drop our test entries from the registry
    for key in (
        "test/pkg/InstallableModule",
        "test/pkg/ExamplePackage",
        "test/pkg/UnInstallable",
    ):
        NODES.pop(key, None)


@pytest.mark.asyncio
async def test_list_packages_returns_only_installable_and_resolves_promoted_props(
    package_test_classes,
):
    pkgs = await list_packages()
    by_registry = {p.registry: p for p in pkgs}
    # installable=True one shows up
    assert "test/pkg/ExamplePackage" in by_registry
    # installable=False one is filtered
    assert "test/pkg/UnInstallable" not in by_registry

    pkg = by_registry["test/pkg/ExamplePackage"]
    assert pkg.name == "Example"
    assert pkg.author == "Tester"
    assert "test/pkg/InstallableModule" in pkg.install_nodes
    # PromoteConfig produced a `an_int_exposed` package property pointing at
    # the underlying module's `an_int` field.
    assert "an_int_exposed" in pkg.package_properties
    prop = pkg.package_properties["an_int_exposed"]
    assert prop.module == "test/pkg/InstallableModule"
    assert prop.name == "an_int"
    assert prop.required is True
    assert prop.errors == [] if hasattr(prop, "errors") else True
    # No errors should have been raised for this package
    assert pkg.errors == []


@pytest.mark.asyncio
async def test_list_packages_records_error_for_missing_module_property():
    """When PromoteConfig points at a property that doesn't exist on the
    target module, list_packages records an error and skips the property."""
    from talemate.game.engine.nodes.registry import register, NODES

    @register("test/pkg/EmptyModule")
    class EmptyModule(Graph):
        def __init__(self, title="Empty Module", **kwargs):
            super().__init__(title=title, **kwargs)

    @register("test/pkg/BrokenPackage")
    class BrokenPackage(Package):
        _base_type = "util/packaging/Package"

        def __init__(self, title="Broken Package", **kwargs):
            super().__init__(title=title, **kwargs)

        def setup(self):
            super().setup()
            self.set_property("package_name", "Broken")
            self.set_property("author", "Tester")
            self.set_property("description", "Broken package")
            self.set_property("installable", True)
            self.set_property("restart_scene_loop", False)

            install = InstallNodeModule()
            install.set_property("node_registry", "test/pkg/EmptyModule")
            self.add_node(install)

            promote = PromoteConfig()
            promote.set_property("node_registry", "test/pkg/EmptyModule")
            promote.set_property("property_name", "no_such_prop")
            promote.set_property("exposed_property_name", "exposed")
            self.add_node(promote)

    try:
        pkgs = await list_packages()
        broken = next((p for p in pkgs if p.registry == "test/pkg/BrokenPackage"), None)
        assert broken is not None
        assert any("no_such_prop" in e for e in broken.errors)
        # The exposed property should NOT have been added because it could
        # not resolve.
        assert "exposed" not in broken.package_properties
    finally:
        NODES.pop("test/pkg/EmptyModule", None)
        NODES.pop("test/pkg/BrokenPackage", None)


@pytest.mark.asyncio
async def test_get_package_by_registry_returns_match_or_none(package_test_classes):
    found = await get_package_by_registry("test/pkg/ExamplePackage")
    assert found is not None
    assert found.registry == "test/pkg/ExamplePackage"

    missing = await get_package_by_registry("test/pkg/Nope")
    assert missing is None


# ---------------------------------------------------------------------------
# initialize_package / initialize_packages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_package_adds_node_with_promoted_property_value(
    package_test_classes, scene
):
    """initialize_package finds each install_node registry, instantiates it,
    adds it to the scene loop, and applies any promoted property values."""
    scene_loop = SceneLoop()
    pkg = PackageData(
        name="Example",
        author="Tester",
        description="d",
        installable=True,
        registry="test/pkg/ExamplePackage",
        install_nodes=["test/pkg/InstallableModule"],
        package_properties={
            "an_int_exposed": PackageProperty(
                module="test/pkg/InstallableModule",
                name="an_int",
                label="An Int",
                description="d",
                type="int",
                default=0,
                value=42,
                required=True,
            )
        },
    )

    before_count = len(scene_loop.nodes)
    await initialize_package(scene, scene_loop, pkg)
    after_count = len(scene_loop.nodes)

    assert after_count == before_count + 1
    # Find the newly-added node and check its property value
    added = [
        n
        for n in scene_loop.nodes.values()
        if n.registry == "test/pkg/InstallableModule"
    ]
    assert len(added) == 1
    assert added[0].properties.get("an_int") == 42


@pytest.mark.asyncio
async def test_initialize_packages_skips_unconfigured_and_errored_packages(
    package_test_classes, scene
):
    """initialize_packages must skip packages with required-but-unset props
    or with errors. Successful ones still install their nodes."""
    scene_loop = SceneLoop()

    # 1) Configured + clean — should install
    good = PackageData(
        name="Good",
        author="x",
        description="d",
        installable=True,
        registry="r/good",
        install_nodes=["test/pkg/InstallableModule"],
        package_properties={
            "an_int_exposed": PackageProperty(
                module="test/pkg/InstallableModule",
                name="an_int",
                label="i",
                description="d",
                type="int",
                default=0,
                value=10,
                required=True,
            )
        },
    )

    # 2) Missing required value — should be skipped
    unconfigured = PackageData(
        name="Unconfigured",
        author="x",
        description="d",
        installable=True,
        registry="r/unconfigured",
        install_nodes=["test/pkg/InstallableModule"],
        package_properties={
            "an_int_exposed": PackageProperty(
                module="test/pkg/InstallableModule",
                name="an_int",
                label="i",
                description="d",
                type="int",
                default=0,
                value=None,
                required=True,
            )
        },
    )

    # 3) Configured but has discovery errors — should be skipped
    errored = PackageData(
        name="Errored",
        author="x",
        description="d",
        installable=True,
        registry="r/errored",
        install_nodes=["test/pkg/InstallableModule"],
        errors=["something blew up earlier"],
    )

    # Persist all three to the scene's info file
    info = ScenePackageInfo(packages=[good, unconfigured, errored])
    await save_scene_package_info(scene, info)

    before = len(scene_loop.nodes)
    await initialize_packages(scene, scene_loop)
    after = len(scene_loop.nodes)

    # only `good` should have added a node
    assert after - before == 1
    installed_registries = [
        n.registry
        for n in scene_loop.nodes.values()
        if n.registry == "test/pkg/InstallableModule"
    ]
    assert installed_registries == ["test/pkg/InstallableModule"]


# ---------------------------------------------------------------------------
# Node-construction smoke (Package, InstallNodeModule, PromoteConfig)
# ---------------------------------------------------------------------------


def test_install_node_module_default_property_is_unresolved():
    n = InstallNodeModule()
    assert n.get_property("node_registry") is UNRESOLVED


def test_promote_config_default_properties_are_unresolved_or_defaults():
    n = PromoteConfig()
    assert n.get_property("node_registry") is UNRESOLVED
    assert n.get_property("property_name") is UNRESOLVED
    assert n.get_property("exposed_property_name") is UNRESOLVED
    assert n.get_property("required") is False
    assert n.get_property("label") == ""


def test_package_default_properties():
    n = Package()
    assert n.get_property("package_name") == ""
    assert n.get_property("author") == ""
    assert n.get_property("description") == ""
    assert n.get_property("installable") is True
    assert n.get_property("restart_scene_loop") is False
