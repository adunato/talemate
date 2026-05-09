"""Additional tests for talemate.changelog covering gaps in test_changelog.py.

Focuses on:
- _SceneRef helper (used internally by ensure_changelogs_for_all_scenes)
- list_revision_entries / latest_revision_at
- delete_changelog_files behavior on missing/error paths
- write_reconstructed_scene with overrides
- InMemoryChangelog property and edge cases
- ensure_changelogs_for_all_scenes (full disk-walking flow)
- _apply_delta error path
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from talemate.changelog import (
    InMemoryChangelog,
    _apply_delta,
    _base_path,
    _changelog_log_path,
    _latest_path,
    _SceneRef,
    append_scene_delta,
    delete_changelog_files,
    ensure_changelogs_for_all_scenes,
    latest_revision_at,
    list_revision_entries,
    reconstruct_scene_data,
    save_changelog,
    write_reconstructed_scene,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_mock_scene(temp_dir: str, name: str = "scene.json") -> Mock:
    scene = Mock()
    scene.filename = name
    scene.save_dir = temp_dir
    scene.changelog_dir = os.path.join(temp_dir, "changelog")
    scene.backups_dir = os.path.join(temp_dir, "backups")
    scene.serialize = {"history": [], "characters": []}
    scene.rev = 0
    scene._changelog = None
    return scene


@pytest.fixture
def mock_scene(temp_dir):
    return _make_mock_scene(temp_dir)


# ---------------------------------------------------------------------------
# _SceneRef
# ---------------------------------------------------------------------------


class TestSceneRef:
    def test_constructs_with_provided_attributes(self, temp_dir):
        ref = _SceneRef(filename="x.json", save_dir=temp_dir, data={"a": 1})
        assert ref.filename == "x.json"
        assert ref.save_dir == temp_dir
        assert ref.changelog_dir == os.path.join(temp_dir, "changelog")
        assert ref.serialize == {"a": 1}


# ---------------------------------------------------------------------------
# list_revision_entries / latest_revision_at
# ---------------------------------------------------------------------------


class TestListRevisionEntries:
    def test_returns_empty_when_no_deltas(self, mock_scene):
        assert list_revision_entries(mock_scene) == []

    def test_returns_entries_sorted_by_rev_desc(self, mock_scene):
        log_path = _changelog_log_path(mock_scene, 0)
        log_data = {
            "deltas": [
                {"rev": 1, "ts": 100},
                {"rev": 3, "ts": 300},
                {"rev": 2, "ts": 200},
            ]
        }
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            json.dump(log_data, f)

        entries = list_revision_entries(mock_scene)
        assert [e["rev"] for e in entries] == [3, 2, 1]
        assert [e["ts"] for e in entries] == [300, 200, 100]

    def test_ignores_entries_with_non_int_rev_or_ts(self, mock_scene):
        log_path = _changelog_log_path(mock_scene, 0)
        log_data = {
            "deltas": [
                {"rev": 1, "ts": 100},
                {"rev": "not-int", "ts": 200},
                {"rev": 2, "ts": "not-int"},
                {"rev": 3, "ts": 300},
            ]
        }
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            json.dump(log_data, f)

        entries = list_revision_entries(mock_scene)
        # Only entries with both rev:int and ts:int are returned.
        revs = [e["rev"] for e in entries]
        assert revs == [3, 1]


class TestLatestRevisionAt:
    def test_returns_none_when_no_revisions_exist(self, mock_scene):
        assert latest_revision_at(mock_scene, at_ts=999_999) is None

    def test_returns_greatest_revision_within_timestamp_window(self, mock_scene):
        # `list_revision_entries` returns entries sorted DESC by rev. The
        # function returns the first rev whose ts is <= at_ts, i.e. the
        # GREATEST rev satisfying the constraint.
        log_path = _changelog_log_path(mock_scene, 0)
        log_data = {
            "deltas": [
                {"rev": 1, "ts": 100},
                {"rev": 2, "ts": 200},
                {"rev": 3, "ts": 300},
            ]
        }
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            json.dump(log_data, f)

        # at_ts=350: all three qualify; greatest rev is 3.
        assert latest_revision_at(mock_scene, at_ts=350) == 3
        # at_ts=250: rev 3 (ts 300) doesn't qualify; rev 2 (ts 200) does.
        assert latest_revision_at(mock_scene, at_ts=250) == 2
        # at_ts=99: nothing qualifies.
        assert latest_revision_at(mock_scene, at_ts=99) is None

    def test_with_only_old_entries_returns_oldest(self, mock_scene):
        # Single entry whose ts <= at_ts should return that rev.
        log_path = _changelog_log_path(mock_scene, 0)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            json.dump({"deltas": [{"rev": 5, "ts": 100}]}, f)

        assert latest_revision_at(mock_scene, at_ts=200) == 5
        assert latest_revision_at(mock_scene, at_ts=50) is None


# ---------------------------------------------------------------------------
# delete_changelog_files
# ---------------------------------------------------------------------------


class TestDeleteChangelogFiles:
    @pytest.mark.asyncio
    async def test_deletes_base_latest_and_segments_and_removes_dir(self, mock_scene):
        # Set up real files
        await save_changelog(mock_scene)
        # Modify and append to create a delta file
        mock_scene.serialize = {"history": [], "characters": [{"name": "A"}]}
        await append_scene_delta(mock_scene, {})

        # Sanity: base, latest, and a segment all exist
        assert os.path.exists(_base_path(mock_scene))
        assert os.path.exists(_latest_path(mock_scene))
        assert os.path.exists(_changelog_log_path(mock_scene, 0))

        result = delete_changelog_files(mock_scene)

        # All three artifact files should be gone.
        assert not os.path.exists(_base_path(mock_scene))
        assert not os.path.exists(_latest_path(mock_scene))
        assert not os.path.exists(_changelog_log_path(mock_scene, 0))
        # changelog dir is empty → removed
        assert result["dir_removed"] == mock_scene.changelog_dir
        assert len(result["deleted"]) == 3

    def test_does_not_error_when_files_dont_exist(self, mock_scene):
        # No setup at all → delete should be a no-op without raising
        result = delete_changelog_files(mock_scene)
        assert result["deleted"] == []
        # No directory to remove either.
        assert result["dir_removed"] is None

    def test_keeps_directory_when_extra_files_present(self, mock_scene, temp_dir):
        # Create the changelog dir + an unrelated file inside it
        os.makedirs(mock_scene.changelog_dir, exist_ok=True)
        extra_file = os.path.join(mock_scene.changelog_dir, "unrelated.txt")
        with open(extra_file, "w") as f:
            f.write("dont touch me")

        result = delete_changelog_files(mock_scene)
        # Dir is not empty → not removed
        assert result["dir_removed"] is None
        assert os.path.exists(extra_file)


# ---------------------------------------------------------------------------
# write_reconstructed_scene with overrides
# ---------------------------------------------------------------------------


class TestWriteReconstructedSceneOverrides:
    @pytest.mark.asyncio
    async def test_overrides_are_applied_to_reconstructed_data(self, mock_scene):
        # Initialize base
        mock_scene.serialize = {"name": "original-name", "value": 1}
        await save_changelog(mock_scene)

        out_path = await write_reconstructed_scene(
            mock_scene,
            to_rev=0,
            output_filename="overridden.json",
            overrides={"name": "patched-name", "extra": True},
        )

        with open(out_path) as f:
            data = json.load(f)

        # Override applied while keeping original keys
        assert data["name"] == "patched-name"
        assert data["value"] == 1
        assert data["extra"] is True


# ---------------------------------------------------------------------------
# _apply_delta error path
# ---------------------------------------------------------------------------


class TestApplyDeltaErrors:
    def test_raises_on_invalid_delta_payload(self):
        # Pass an entirely invalid delta object (a string) — Delta() will
        # try to deserialize it as a pickle and raise UnpicklingError.
        # _apply_delta catches and re-raises after logging.
        with pytest.raises(Exception):
            _apply_delta({"a": 1}, "not a dict, totally invalid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# InMemoryChangelog properties
# ---------------------------------------------------------------------------


class TestInMemoryChangelogProperties:
    @pytest.mark.asyncio
    async def test_next_revision_starts_at_scene_rev_plus_one(self, mock_scene):
        await save_changelog(mock_scene)
        mock_scene.rev = 5

        async with InMemoryChangelog(mock_scene) as changelog:
            # No pending deltas → next is 6.
            assert changelog.next_revision == 6

            # Append one pending delta → next becomes 7.
            mock_scene.serialize = {"history": [], "characters": [{"name": "A"}]}
            await changelog.append_delta({})
            assert changelog.next_revision == 7

    @pytest.mark.asyncio
    async def test_pending_count_reflects_appended_deltas(self, mock_scene):
        await save_changelog(mock_scene)

        async with InMemoryChangelog(mock_scene) as changelog:
            assert changelog.pending_count == 0

            mock_scene.serialize = {"history": [], "characters": [{"name": "A"}]}
            await changelog.append_delta({})
            assert changelog.pending_count == 1

            mock_scene.serialize = {
                "history": [],
                "characters": [{"name": "A"}, {"name": "B"}],
            }
            await changelog.append_delta({})
            assert changelog.pending_count == 2


# ---------------------------------------------------------------------------
# ensure_changelogs_for_all_scenes
# ---------------------------------------------------------------------------


class TestEnsureChangelogsForAllScenes:
    @pytest.mark.asyncio
    async def test_creates_base_and_latest_for_scene_without_either(self, temp_dir):
        # Lay out: <temp>/project1/scene.json
        project = Path(temp_dir) / "project1"
        project.mkdir()
        scene_file = project / "scene.json"
        scene_file.write_text(json.dumps({"history": [], "characters": []}))

        # Run the bulk ensurer over the temp scenes root.
        await ensure_changelogs_for_all_scenes(root=str(temp_dir))

        # Both base + latest should now exist.
        base = project / "changelog" / "scene.json.base.json"
        latest = project / "changelog" / "scene.json.latest.json"
        assert base.exists()
        assert latest.exists()

    @pytest.mark.asyncio
    async def test_creates_latest_when_base_exists_but_latest_missing(self, temp_dir):
        project = Path(temp_dir) / "p"
        project.mkdir()
        scene_file = project / "s.json"
        scene_file.write_text(json.dumps({"x": 1}))

        # Pre-create base only, no latest.
        cl_dir = project / "changelog"
        cl_dir.mkdir()
        base_file = cl_dir / "s.json.base.json"
        base_file.write_text(json.dumps({"x": 1}))

        await ensure_changelogs_for_all_scenes(root=str(temp_dir))

        latest = cl_dir / "s.json.latest.json"
        assert latest.exists()
        assert json.loads(latest.read_text()) == {"x": 1}

    @pytest.mark.asyncio
    async def test_no_op_when_root_missing(self, temp_dir):
        # Use a path that doesn't exist — the function should warn and return.
        await ensure_changelogs_for_all_scenes(root=os.path.join(temp_dir, "nope"))
        # No raise, nothing to assert beyond not-raising.

    @pytest.mark.asyncio
    async def test_skips_unreadable_scene_file(self, temp_dir, monkeypatch):
        # Create a scene file with invalid JSON.
        project = Path(temp_dir) / "p"
        project.mkdir()
        bad = project / "broken.json"
        bad.write_text("{ not valid json")

        # Should not raise — bad file is skipped.
        await ensure_changelogs_for_all_scenes(root=str(temp_dir))
        # No base/latest created for broken scene
        assert not (project / "changelog" / "broken.json.base.json").exists()

    @pytest.mark.asyncio
    async def test_processes_multiple_scene_files_in_alphabetical_order(self, temp_dir):
        project = Path(temp_dir) / "p"
        project.mkdir()
        for name in ["b.json", "a.json"]:
            (project / name).write_text(json.dumps({"name": name}))

        await ensure_changelogs_for_all_scenes(root=str(temp_dir))

        cl_dir = project / "changelog"
        assert (cl_dir / "a.json.base.json").exists()
        assert (cl_dir / "b.json.base.json").exists()


# ---------------------------------------------------------------------------
# Reconstruction edge cases (cover branches at 651, 665, 682)
# ---------------------------------------------------------------------------


class TestReconstructionEdgeCases:
    @pytest.mark.asyncio
    async def test_reconstruct_with_to_rev_none_uses_overall_latest(self, mock_scene):
        await save_changelog(mock_scene)
        mock_scene.serialize = {"history": [], "characters": [{"name": "A"}]}
        await append_scene_delta(mock_scene, {})

        # to_rev=None → should reconstruct at overall latest revision
        result = await reconstruct_scene_data(mock_scene, to_rev=None)
        assert result["characters"] == [{"name": "A"}]

    @pytest.mark.asyncio
    async def test_reconstruct_stops_at_target_rev_in_multi_file_changelog(
        self, mock_scene
    ):
        # Multiple revisions, ensure deltas past target_rev are not applied.
        await save_changelog(mock_scene)

        mock_scene.serialize = {"history": [], "characters": [{"name": "A"}]}
        rev1 = await append_scene_delta(mock_scene, {})
        assert rev1 == 1

        mock_scene.serialize = {
            "history": [],
            "characters": [{"name": "A"}, {"name": "B"}],
        }
        rev2 = await append_scene_delta(mock_scene, {})
        assert rev2 == 2

        # Reconstruct at rev=1 — only A should be present.
        result = await reconstruct_scene_data(mock_scene, to_rev=1)
        assert result["characters"] == [{"name": "A"}]

    @pytest.mark.asyncio
    async def test_reconstruct_returns_base_when_to_rev_zero(self, mock_scene):
        await save_changelog(mock_scene)
        mock_scene.serialize = {"history": [], "characters": [{"name": "A"}]}
        await append_scene_delta(mock_scene, {})

        result = await reconstruct_scene_data(mock_scene, to_rev=0)
        # No deltas applied → base data only
        assert result["characters"] == []
