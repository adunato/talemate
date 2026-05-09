"""Unit tests for talemate.export.

Tests exercise both export formats (talemate JSON and talemate_complete ZIP)
against a minimal but real Scene-like fixture rooted in tmp_path. We use a
duck-typed scene object instead of constructing a full talemate.Scene because
export only reads scene.json, scene.reset, scene.assets.asset_directory,
scene.save_dir, scene.restore_from, and scene.name — all easy to provide.
"""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from talemate.export import (
    ExportFormat,
    ExportOptions,
    export,
    export_talemate,
    export_talemate_complete,
)


# ---------------------------------------------------------------------------
# Minimal Scene fixture: duck-types the surface that export reads
# ---------------------------------------------------------------------------


class _StubAssets:
    def __init__(self, scene):
        self.scene = scene

    @property
    def asset_directory(self):
        return os.path.join(self.scene.save_dir, "assets")


class _StubScene:
    """Mimics the surface of talemate.tale_mate.Scene that export uses."""

    def __init__(self, save_dir: str, name: str = "test-scene"):
        self.save_dir = save_dir
        self.name = name
        self.filename = "scene.json"
        self.restore_from = None
        self.assets = _StubAssets(self)
        self._payload: dict = {"name": name, "history": []}
        self.reset_called_count = 0

    def reset(self):
        self.reset_called_count += 1
        # Mirror real Scene.reset's effect: clear history.
        self._payload["history"] = []

    @property
    def serialize(self) -> dict:
        return dict(self._payload)

    @property
    def json(self) -> str:
        return json.dumps(self.serialize)


@pytest.fixture
def scene_dir(tmp_path):
    """Create scene save_dir with predictable structure."""
    save_dir = tmp_path / "scenes" / "test-scene"
    save_dir.mkdir(parents=True)
    return save_dir


@pytest.fixture
def scene(scene_dir):
    return _StubScene(save_dir=str(scene_dir))


def _make_subdir(parent: Path, name: str, files: dict[str, str]) -> Path:
    """Helper: create a subdirectory with given files mapping (relpath -> content)."""
    sub = parent / name
    sub.mkdir(exist_ok=True)
    for rel, content in files.items():
        path = sub / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return sub


# ---------------------------------------------------------------------------
# ExportOptions
# ---------------------------------------------------------------------------


class TestExportOptions:
    def test_defaults(self):
        opts = ExportOptions(name="x")
        assert opts.name == "x"
        assert opts.format == ExportFormat.talemate
        assert opts.reset_progress is True
        assert opts.include_assets is True
        assert opts.include_nodes is True
        assert opts.include_info is True
        assert opts.include_templates is True

    def test_format_enum_values(self):
        assert ExportFormat.talemate.value == "talemate"
        assert ExportFormat.talemate_complete.value == "talemate_complete"


# ---------------------------------------------------------------------------
# export() dispatcher
# ---------------------------------------------------------------------------


class TestExportDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_to_talemate_format(self, scene):
        opts = ExportOptions(name="x", format=ExportFormat.talemate)
        result = await export(scene, opts)
        # talemate format returns a base64 string
        assert isinstance(result, str)
        # Should decode to valid JSON
        decoded = base64.b64decode(result).decode()
        json.loads(decoded)  # raises if not valid JSON

    @pytest.mark.asyncio
    async def test_dispatches_to_talemate_complete_format(self, scene):
        opts = ExportOptions(name="x", format=ExportFormat.talemate_complete)
        result = await export(scene, opts)
        # talemate_complete returns bytes (a ZIP)
        assert isinstance(result, bytes)
        # Should be a valid ZIP
        with zipfile.ZipFile(io.BytesIO(result)) as zf:
            assert "scene.json" in zf.namelist()


# ---------------------------------------------------------------------------
# export_talemate (legacy JSON-only base64)
# ---------------------------------------------------------------------------


class TestExportTalemate:
    @pytest.mark.asyncio
    async def test_returns_base64_encoded_json(self, scene):
        # Skip the reset path so the payload is faithfully round-tripped.
        scene._payload = {"name": "test", "value": 42}
        result = await export_talemate(
            scene, ExportOptions(name="x", reset_progress=False)
        )

        decoded = base64.b64decode(result).decode()
        assert json.loads(decoded) == {"name": "test", "value": 42}

    @pytest.mark.asyncio
    async def test_resets_progress_when_option_enabled(self, scene):
        opts = ExportOptions(name="x", reset_progress=True)
        await export_talemate(scene, opts)
        assert scene.reset_called_count == 1

    @pytest.mark.asyncio
    async def test_skips_reset_when_option_disabled(self, scene):
        opts = ExportOptions(name="x", reset_progress=False)
        await export_talemate(scene, opts)
        assert scene.reset_called_count == 0


# ---------------------------------------------------------------------------
# export_talemate_complete (ZIP format)
# ---------------------------------------------------------------------------


class TestExportTalemateComplete:
    @pytest.mark.asyncio
    async def test_zip_always_contains_scene_json(self, scene):
        opts = ExportOptions(name="x")
        zip_bytes = await export_talemate_complete(scene, opts)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "scene.json" in zf.namelist()
            content = zf.read("scene.json").decode()
            assert json.loads(content) == scene.serialize

    @pytest.mark.asyncio
    async def test_includes_assets_when_enabled_and_directory_exists(
        self, scene, scene_dir
    ):
        # Create the assets directory + a file inside it
        assets_dir = scene_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "library.json").write_text('{"assets": {}}')
        (assets_dir / "image.png").write_bytes(b"\x89PNG fake")

        opts = ExportOptions(name="x", include_assets=True)
        zip_bytes = await export_talemate_complete(scene, opts)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "assets/library.json" in names
            assert "assets/image.png" in names

    @pytest.mark.asyncio
    async def test_excludes_assets_when_option_disabled(self, scene, scene_dir):
        assets_dir = scene_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "library.json").write_text('{"assets": {}}')

        opts = ExportOptions(name="x", include_assets=False)
        zip_bytes = await export_talemate_complete(scene, opts)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert not any(n.startswith("assets/") for n in names)

    @pytest.mark.asyncio
    async def test_includes_nodes_directory_when_present(self, scene, scene_dir):
        _make_subdir(scene_dir, "nodes", {"main.json": '{"nodes": {}}'})

        opts = ExportOptions(name="x", include_nodes=True)
        zip_bytes = await export_talemate_complete(scene, opts)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "nodes/main.json" in zf.namelist()

    @pytest.mark.asyncio
    async def test_skips_nodes_directory_when_option_disabled(self, scene, scene_dir):
        _make_subdir(scene_dir, "nodes", {"main.json": "{}"})

        opts = ExportOptions(name="x", include_nodes=False)
        zip_bytes = await export_talemate_complete(scene, opts)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert not any(n.startswith("nodes/") for n in zf.namelist())

    @pytest.mark.asyncio
    async def test_includes_info_directory(self, scene, scene_dir):
        _make_subdir(scene_dir, "info", {"about.md": "# About"})

        zip_bytes = await export_talemate_complete(
            scene, ExportOptions(name="x", include_info=True)
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "info/about.md" in zf.namelist()

    @pytest.mark.asyncio
    async def test_excludes_info_directory_when_disabled(self, scene, scene_dir):
        _make_subdir(scene_dir, "info", {"about.md": "# About"})

        zip_bytes = await export_talemate_complete(
            scene, ExportOptions(name="x", include_info=False)
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert not any(n.startswith("info/") for n in zf.namelist())

    @pytest.mark.asyncio
    async def test_includes_templates_directory(self, scene, scene_dir):
        _make_subdir(scene_dir, "templates", {"world.j2": "{{ scene.name }}"})

        zip_bytes = await export_talemate_complete(
            scene, ExportOptions(name="x", include_templates=True)
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "templates/world.j2" in zf.namelist()

    @pytest.mark.asyncio
    async def test_excludes_templates_directory_when_disabled(self, scene, scene_dir):
        _make_subdir(scene_dir, "templates", {"world.j2": "x"})

        zip_bytes = await export_talemate_complete(
            scene, ExportOptions(name="x", include_templates=False)
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert not any(n.startswith("templates/") for n in zf.namelist())

    @pytest.mark.asyncio
    async def test_resets_when_option_enabled(self, scene):
        await export_talemate_complete(
            scene, ExportOptions(name="x", reset_progress=True)
        )
        assert scene.reset_called_count == 1

    @pytest.mark.asyncio
    async def test_skips_reset_when_option_disabled(self, scene):
        await export_talemate_complete(
            scene, ExportOptions(name="x", reset_progress=False)
        )
        assert scene.reset_called_count == 0

    @pytest.mark.asyncio
    async def test_includes_restore_file_when_set(self, scene, scene_dir):
        # Create a restore file in the save_dir
        restore_filename = "old-version.json"
        (scene_dir / restore_filename).write_text('{"restored": true}')
        scene.restore_from = restore_filename

        zip_bytes = await export_talemate_complete(scene, ExportOptions(name="x"))

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert restore_filename in names
            content = zf.read(restore_filename).decode()
            assert json.loads(content) == {"restored": true_value()}

    @pytest.mark.asyncio
    async def test_handles_missing_restore_file_gracefully(self, scene):
        # restore_from is set but the file does not exist
        scene.restore_from = "nonexistent.json"

        # Should not raise — only emit a warning log; ZIP still has scene.json
        zip_bytes = await export_talemate_complete(scene, ExportOptions(name="x"))
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "scene.json" in zf.namelist()
            assert "nonexistent.json" not in zf.namelist()

    @pytest.mark.asyncio
    async def test_handles_missing_assets_directory_gracefully(self, scene):
        # No assets directory exists — export should still succeed.
        opts = ExportOptions(name="x", include_assets=True)
        zip_bytes = await export_talemate_complete(scene, opts)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "scene.json" in zf.namelist()
            assert not any(n.startswith("assets/") for n in zf.namelist())

    @pytest.mark.asyncio
    async def test_full_zip_roundtrip_includes_all_directories(self, scene, scene_dir):
        # Set up a fully-populated scene with all auxiliary directories.
        assets_dir = scene_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "library.json").write_text('{"assets":{}}')
        _make_subdir(scene_dir, "nodes", {"a.json": "1"})
        _make_subdir(scene_dir, "info", {"b.md": "2"})
        _make_subdir(scene_dir, "templates", {"c.j2": "3"})

        scene.restore_from = "previous.json"
        (scene_dir / "previous.json").write_text('{"prev": true}')

        zip_bytes = await export_talemate_complete(scene, ExportOptions(name="x"))
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = set(zf.namelist())
            assert "scene.json" in names
            assert "assets/library.json" in names
            assert "nodes/a.json" in names
            assert "info/b.md" in names
            assert "templates/c.j2" in names
            assert "previous.json" in names


# ---------------------------------------------------------------------------
# export() unsupported format
# ---------------------------------------------------------------------------


class TestExportUnsupportedFormat:
    @pytest.mark.asyncio
    async def test_raises_for_unknown_format(self, scene, monkeypatch):
        # Build an ExportOptions instance and bypass enum constraint by
        # directly substituting a foreign value to exercise the raise branch.
        opts = ExportOptions(name="x")
        opts.format = "unknown_fmt"  # type: ignore[assignment]

        with pytest.raises(ValueError, match="Unsupported export format"):
            await export(scene, opts)


# Helper used in test_includes_restore_file_when_set so we don't repeat ourselves.
def true_value():
    """Returns Python True — exists only to keep the JSON fixture above readable."""
    return True
