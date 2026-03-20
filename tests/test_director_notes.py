"""
Tests for the DirectorNotesMixin.

Uses real Scene and DirectorAgent objects (MockScene + bootstrap_scene),
with only emit/signals suppressed.
"""

import pytest
from unittest.mock import patch

from talemate.context import ActiveScene
from talemate.scene_message import NarratorMessage, CharacterMessage

from conftest import MockScene, bootstrap_scene


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def suppress_emit():
    """Suppress emit() calls."""
    with patch("talemate.agents.director.notes.async_signals") as mock_signals:
        # Make connect/get return a mock that does nothing
        mock_signal = type("MockSignal", (), {"connect": lambda *a, **kw: None, "send": lambda *a, **kw: None})()
        mock_signals.get.return_value = mock_signal
        yield


@pytest.fixture
def scene_with_director():
    """Real MockScene + real DirectorAgent with active_scene context var set."""
    scene = MockScene()
    agents_dict = bootstrap_scene(scene)
    director = agents_dict["director"]

    with ActiveScene(scene):
        yield scene, director


# ---------------------------------------------------------------------------
# Tests: CRUD
# ---------------------------------------------------------------------------


class TestNotesCRUD:
    def test_add_note(self, scene_with_director):
        scene, director = scene_with_director
        note = director.notes_add(text="Focus on combat", turns=5)

        assert note.text == "Focus on combat"
        assert note.turns_remaining == 5
        assert note.turns_initial == 5
        assert note.id

        notes = director.notes_get_all()
        assert len(notes) == 1
        assert notes[0].id == note.id

    def test_add_multiple_notes(self, scene_with_director):
        scene, director = scene_with_director
        director.notes_add(text="Note 1", turns=10)
        director.notes_add(text="Note 2", turns=5)
        director.notes_add(text="Note 3", turns=3)

        notes = director.notes_get_all()
        assert len(notes) == 3

    def test_get_note_by_id(self, scene_with_director):
        scene, director = scene_with_director
        note = director.notes_add(text="Find me", turns=10)

        found = director.notes_get(note.id)
        assert found is not None
        assert found.text == "Find me"

    def test_get_note_not_found(self, scene_with_director):
        scene, director = scene_with_director
        assert director.notes_get("nonexistent") is None

    def test_update_note_text(self, scene_with_director):
        scene, director = scene_with_director
        note = director.notes_add(text="Original", turns=10)

        updated = director.notes_update(note.id, text="Modified")
        assert updated is not None
        assert updated.text == "Modified"

        # Verify persisted
        found = director.notes_get(note.id)
        assert found.text == "Modified"

    def test_update_note_turns(self, scene_with_director):
        scene, director = scene_with_director
        note = director.notes_add(text="Decaying", turns=10)

        updated = director.notes_update(note.id, turns=20)
        assert updated.turns_remaining == 20
        assert updated.turns_initial == 20

    def test_update_nonexistent_note(self, scene_with_director):
        scene, director = scene_with_director
        result = director.notes_update("nonexistent", text="Nope")
        assert result is None

    def test_remove_note(self, scene_with_director):
        scene, director = scene_with_director
        note = director.notes_add(text="Remove me", turns=5)

        removed = director.notes_remove(note.id)
        assert removed is True
        assert director.notes_get(note.id) is None
        assert len(director.notes_get_all()) == 0

    def test_remove_nonexistent_note(self, scene_with_director):
        scene, director = scene_with_director
        assert director.notes_remove("nonexistent") is False


# ---------------------------------------------------------------------------
# Tests: Decay
# ---------------------------------------------------------------------------


class TestNotesDecay:
    def test_decay_decrements_turns(self, scene_with_director):
        scene, director = scene_with_director
        director.notes_add(text="Short lived", turns=5)

        expired = director.notes_decay(elapsed_turns=2)
        assert len(expired) == 0

        note = director.notes_get_all()[0]
        assert note.turns_remaining == 3

    def test_decay_removes_expired(self, scene_with_director):
        scene, director = scene_with_director
        director.notes_add(text="About to expire", turns=2)

        expired = director.notes_decay(elapsed_turns=2)
        assert len(expired) == 1
        assert expired[0].text == "About to expire"
        assert len(director.notes_get_all()) == 0

    def test_decay_clamps_to_zero(self, scene_with_director):
        scene, director = scene_with_director
        director.notes_add(text="Overkill", turns=1)

        expired = director.notes_decay(elapsed_turns=100)
        assert len(expired) == 1
        assert len(director.notes_get_all()) == 0

    def test_decay_mixed_notes(self, scene_with_director):
        scene, director = scene_with_director
        director.notes_add(text="Short", turns=2)
        director.notes_add(text="Long", turns=10)

        expired = director.notes_decay(elapsed_turns=3)
        assert len(expired) == 1
        assert expired[0].text == "Short"

        remaining = director.notes_get_all()
        assert len(remaining) == 1
        assert remaining[0].text == "Long"
        assert remaining[0].turns_remaining == 7


# ---------------------------------------------------------------------------
# Tests: Prompt helpers
# ---------------------------------------------------------------------------


class TestNotesForPrompt:
    def test_empty_notes(self, scene_with_director):
        scene, director = scene_with_director
        assert director.notes_for_prompt() == []

    def test_note_format(self, scene_with_director):
        scene, director = scene_with_director
        director.notes_add(text="Focus on combat", turns=5)

        prompt_notes = director.notes_for_prompt()
        assert len(prompt_notes) == 1
        note = prompt_notes[0]
        assert note["text"] == "Focus on combat"
        assert note["turns_remaining"] == 5
        assert note["turns_initial"] == 5

    def test_turns_elapsed(self, scene_with_director):
        scene, director = scene_with_director
        # Add some history to make turns_elapsed meaningful
        scene.history.append(NarratorMessage(message="The sun set."))
        scene.history.append(CharacterMessage(message="Alice: Hello."))

        director.notes_add(text="Focus on combat", turns=5)

        # Add more history after note creation
        scene.history.append(NarratorMessage(message="Night fell."))

        prompt_notes = director.notes_for_prompt()
        assert len(prompt_notes) == 1
        pn = prompt_notes[0]
        assert pn["turns_elapsed"] == 1  # 3 messages now vs 2 at creation
