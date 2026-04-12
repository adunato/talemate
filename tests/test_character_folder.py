"""
Tests for the character "folder" organization feature introduced in prep-0.37.0.

Covers four targets:
1. ``talemate.util.strings.normalize_name`` — generic string normalizer used for
   free-form user-entered names (trim, empty-collapse, truncate).
2. ``WorldStateManager.update_character_folder`` — sets/clears a single
   character's folder.
3. ``WorldStateManager.rename_character_folder`` — bulk-renames a folder across
   every character (active and inactive) currently assigned to it.
4. ``Character.apply_shared_context`` — regression: folder must propagate from
   the source character even when being *cleared* to ``None``, which the
   ``exclude_none=True`` model dump used for the rest of shared context would
   otherwise drop.
"""

import pytest

from conftest import MockScene, bootstrap_scene
from talemate.character import Character
from talemate.tale_mate import Actor
from talemate.util.strings import normalize_name
from talemate.world_state.manager import WorldStateManager


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def scene():
    """A real MockScene wired up with all agents."""
    mock_scene = MockScene()
    bootstrap_scene(mock_scene)
    return mock_scene


def _add_active_character(scene, name: str, folder: str | None = None) -> Character:
    """Create a Character and register it as an active actor in the scene.

    Active characters are visible through ``scene.get_characters()`` AND
    ``scene.character_data``.
    """
    character = Character(name=name, folder=folder)
    actor = Actor(character=character, agent=None)
    scene.actors.append(actor)
    scene.character_data[character.name] = character
    return character


def _add_inactive_character(scene, name: str, folder: str | None = None) -> Character:
    """Create a Character and register it as inactive (present in
    ``character_data`` but not in ``active_characters``).
    """
    character = Character(name=name, folder=folder)
    scene.character_data[character.name] = character
    # Active/inactive partition is derived from ``active_characters``; leaving
    # the name out of that list marks the character as inactive.
    if character.name in scene.active_characters:
        scene.active_characters.remove(character.name)
    return character


@pytest.fixture
def manager(scene):
    return WorldStateManager(scene)


# ---------------------------------------------------------------------------
# 1. normalize_name
# ---------------------------------------------------------------------------


class TestNormalizeName:
    """
    ``normalize_name`` is the shared helper that backs the
    ``_normalize_folder_name`` wrapper used by the websocket handlers. It
    trims whitespace, collapses empty / whitespace-only input to ``None``,
    and truncates to ``max_length`` characters.
    """

    def test_none_input_returns_none(self):
        assert normalize_name(None, 29) is None

    def test_empty_string_returns_none(self):
        assert normalize_name("", 29) is None

    def test_whitespace_only_returns_none(self):
        assert normalize_name("   \t\n  ", 29) is None

    def test_simple_string_returned_unchanged(self):
        assert normalize_name("Adventurers", 29) == "Adventurers"

    def test_leading_and_trailing_whitespace_stripped(self):
        assert normalize_name("  Adventurers  ", 29) == "Adventurers"

    def test_internal_whitespace_preserved(self):
        """Only edge whitespace is trimmed; inner spaces are content."""
        assert normalize_name("  The Council  ", 29) == "The Council"

    def test_truncates_when_over_max_length(self):
        raw = "a" * 50
        result = normalize_name(raw, 29)
        assert result == "a" * 29
        assert len(result) == 29

    def test_preserves_length_at_exact_max(self):
        raw = "x" * 29
        assert normalize_name(raw, 29) == raw

    def test_preserves_length_just_under_max(self):
        raw = "y" * 28
        assert normalize_name(raw, 29) == raw

    def test_trim_happens_before_truncation(self):
        """Leading/trailing whitespace should not count toward the length
        budget: ``"   abc   "`` with ``max_length=3`` must return ``"abc"``,
        not the first three whitespace characters.
        """
        assert normalize_name("   abc   ", 3) == "abc"

    def test_long_input_with_surrounding_whitespace(self):
        """Combined behaviour: strip then truncate."""
        raw = "  " + ("q" * 40) + "  "
        result = normalize_name(raw, 10)
        assert result == "q" * 10

    def test_max_length_zero_collapses_to_empty_string(self):
        """A ``max_length`` of zero is legal and produces an empty-string
        slice. The function does NOT re-collapse to ``None`` after
        truncation — it only collapses before truncation.
        """
        assert normalize_name("abc", 0) == ""


# ---------------------------------------------------------------------------
# 2. WorldStateManager.update_character_folder
# ---------------------------------------------------------------------------


class TestUpdateCharacterFolder:
    @pytest.mark.asyncio
    async def test_assigns_folder_to_existing_character(self, scene, manager):
        character = _add_active_character(scene, "Alice")
        assert character.folder is None

        await manager.update_character_folder("Alice", "Heroes")

        assert character.folder == "Heroes"

    @pytest.mark.asyncio
    async def test_clears_folder_when_given_none(self, scene, manager):
        character = _add_active_character(scene, "Alice", folder="Heroes")

        await manager.update_character_folder("Alice", None)

        assert character.folder is None

    @pytest.mark.asyncio
    async def test_overwrites_existing_folder(self, scene, manager):
        character = _add_active_character(scene, "Alice", folder="Heroes")

        await manager.update_character_folder("Alice", "Villains")

        assert character.folder == "Villains"

    @pytest.mark.asyncio
    async def test_unknown_character_is_noop(self, scene, manager):
        """Unknown names are logged and silently ignored — must not raise."""
        existing = _add_active_character(scene, "Alice", folder="Heroes")

        # Should not raise, and should leave unrelated state alone.
        await manager.update_character_folder("DoesNotExist", "Heroes")

        assert existing.folder == "Heroes"

    @pytest.mark.asyncio
    async def test_only_targeted_character_is_affected(self, scene, manager):
        alice = _add_active_character(scene, "Alice", folder="Heroes")
        bob = _add_active_character(scene, "Bob", folder="Heroes")

        await manager.update_character_folder("Alice", "Villains")

        assert alice.folder == "Villains"
        assert bob.folder == "Heroes"

    @pytest.mark.asyncio
    async def test_can_update_inactive_character(self, scene, manager):
        """``get_character`` resolves inactive characters as well, so the
        update path must work for them too.
        """
        inactive = _add_inactive_character(scene, "Ghost")

        await manager.update_character_folder("Ghost", "Spirits")

        assert inactive.folder == "Spirits"


# ---------------------------------------------------------------------------
# 3. WorldStateManager.rename_character_folder
# ---------------------------------------------------------------------------


class TestRenameCharacterFolder:
    @pytest.mark.asyncio
    async def test_renames_single_matching_character(self, scene, manager):
        alice = _add_active_character(scene, "Alice", folder="Heroes")

        await manager.rename_character_folder("Heroes", "Champions")

        assert alice.folder == "Champions"

    @pytest.mark.asyncio
    async def test_renames_all_matching_characters(self, scene, manager):
        alice = _add_active_character(scene, "Alice", folder="Heroes")
        bob = _add_active_character(scene, "Bob", folder="Heroes")
        carol = _add_active_character(scene, "Carol", folder="Heroes")

        await manager.rename_character_folder("Heroes", "Champions")

        assert alice.folder == "Champions"
        assert bob.folder == "Champions"
        assert carol.folder == "Champions"

    @pytest.mark.asyncio
    async def test_only_renames_exact_folder_matches(self, scene, manager):
        """Non-matching folders and None folders must be left untouched."""
        hero = _add_active_character(scene, "Alice", folder="Heroes")
        villain = _add_active_character(scene, "Mallory", folder="Villains")
        loose = _add_active_character(scene, "Eve", folder=None)

        await manager.rename_character_folder("Heroes", "Champions")

        assert hero.folder == "Champions"
        assert villain.folder == "Villains"
        assert loose.folder is None

    @pytest.mark.asyncio
    async def test_rename_covers_inactive_characters(self, scene, manager):
        """``rename_character_folder`` iterates ``scene.character_data`` which
        contains BOTH active and inactive characters — both must be renamed.
        """
        active = _add_active_character(scene, "Alice", folder="Heroes")
        inactive = _add_inactive_character(scene, "Ghost", folder="Heroes")

        await manager.rename_character_folder("Heroes", "Champions")

        assert active.folder == "Champions"
        assert inactive.folder == "Champions"

    @pytest.mark.asyncio
    async def test_no_matches_is_noop(self, scene, manager):
        alice = _add_active_character(scene, "Alice", folder="Heroes")

        await manager.rename_character_folder("NonexistentFolder", "Whatever")

        assert alice.folder == "Heroes"

    @pytest.mark.asyncio
    async def test_rename_with_none_old_matches_unorganized(self, scene, manager):
        """Documented behavior of the low-level manager method: passing
        ``None`` as ``old_name`` will match every character whose folder is
        also ``None`` (because ``None == None``). The handler layer
        (``handle_rename_character_folder``) guards against this by bailing
        out when ``old_name`` normalizes to ``None``, but the manager method
        itself has no such guard — it is the raw bulk updater.
        """
        unorganized = _add_active_character(scene, "Alice", folder=None)
        already_tagged = _add_active_character(scene, "Bob", folder="Heroes")

        await manager.rename_character_folder(None, "Spirits")

        # Unorganized character gets swept into the new folder...
        assert unorganized.folder == "Spirits"
        # ...but characters with a different, non-None folder are untouched.
        assert already_tagged.folder == "Heroes"

    @pytest.mark.asyncio
    async def test_rename_to_same_name_is_idempotent(self, scene, manager):
        alice = _add_active_character(scene, "Alice", folder="Heroes")

        await manager.rename_character_folder("Heroes", "Heroes")

        assert alice.folder == "Heroes"


# ---------------------------------------------------------------------------
# 4. Character.apply_shared_context — folder propagation regression
# ---------------------------------------------------------------------------


class TestApplySharedContextFolder:
    """
    Regression coverage: ``apply_shared_context`` dumps the source character
    with ``exclude_none=True`` and applies the result via ``update(**)``. That
    dump drops ``None`` values — which means clearing ``folder`` on the shared
    source would silently fail to propagate. The fix assigns ``self.folder =
    other_character.folder`` explicitly AFTER the update call.
    """

    @pytest.mark.asyncio
    async def test_folder_propagates_when_set_on_source(self):
        source = Character(name="Alice", folder="Heroes")
        target = Character(name="Alice")

        await target.apply_shared_context(source)

        assert target.folder == "Heroes"

    @pytest.mark.asyncio
    async def test_folder_cleared_when_source_is_none(self):
        """The regression case: target had a folder, source has ``None`` —
        the target's folder MUST be cleared.
        """
        source = Character(name="Alice", folder=None)
        target = Character(name="Alice", folder="Heroes")

        await target.apply_shared_context(source)

        assert target.folder is None

    @pytest.mark.asyncio
    async def test_folder_overwritten_when_both_set(self):
        source = Character(name="Alice", folder="Villains")
        target = Character(name="Alice", folder="Heroes")

        await target.apply_shared_context(source)

        assert target.folder == "Villains"

    @pytest.mark.asyncio
    async def test_folder_remains_none_when_neither_set(self):
        source = Character(name="Alice", folder=None)
        target = Character(name="Alice", folder=None)

        await target.apply_shared_context(source)

        assert target.folder is None

    @pytest.mark.asyncio
    async def test_folder_propagation_does_not_break_other_shared_state(self):
        """Sanity: propagating ``folder`` mustn't clobber the rest of the
        shared context machinery.

        ``apply_shared_context`` first dumps the source (which carries the
        ``shared_attributes`` / ``shared_details`` whitelists). That dump is
        applied to the target, and only THEN is the whitelist walked against
        the source's ``base_attributes`` and ``details`` — so the whitelist
        must live on the source, not the target.
        """
        source = Character(
            name="Alice",
            folder="Heroes",
            description="A brave warrior",
            base_attributes={"strength": "15", "class": "Knight"},
            details={"appearance": "Tall", "background": "Noble"},
            shared_attributes=["strength"],
            shared_details=["appearance"],
        )
        target = Character(name="Alice")

        await target.apply_shared_context(source)

        assert target.folder == "Heroes"
        assert target.description == "A brave warrior"
        assert target.base_attributes.get("strength") == "15"
        # Non-shared attribute should NOT be copied
        assert "class" not in target.base_attributes
        assert target.details.get("appearance") == "Tall"
        # Non-shared detail should NOT be copied
        assert "background" not in target.details
        assert target.memory_dirty is True
