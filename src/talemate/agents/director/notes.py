"""
Director Notes system.

Provides dynamic, decaying instructions that the director follows
during scene direction, director chat, and (later) scene guidance.

Notes are stored in ``scene.agent_state["director"]["notes"]`` as a list of
serialised :class:`DirectorNote` dicts.
"""

import structlog
import uuid

import pydantic
from typing import TYPE_CHECKING

import talemate.emit.async_signals as async_signals
from talemate.scene_message import CharacterMessage, NarratorMessage

if TYPE_CHECKING:
    from talemate.tale_mate import Scene

__all__ = [
    "DirectorNote",
    "DirectorNotesMixin",
]

log = structlog.get_logger("talemate.agent.director.notes")

NOTES_STATE_KEY = "notes"

# Sentinel for distinguishing "not provided" from an explicit value in notes_update
_SENTINEL = object()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class DirectorNote(pydantic.BaseModel):
    """A single director instruction that decays over scene turns."""

    id: str = pydantic.Field(default_factory=lambda: str(uuid.uuid4())[:10])
    text: str
    turns_remaining: int
    turns_initial: int
    created_at_turn: int = 0  # scene turn index when created


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class DirectorNotesMixin:
    """
    Agent mixin that manages director notes stored in scene agent state.

    Storage layout::

        scene.agent_state["director"]["notes"] = [DirectorNote.model_dump(), ...]

    Call :meth:`connect` from the owning agent to wire up automatic decay.
    """

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def connect(self, scene: "Scene"):
        super().connect(scene)
        async_signals.get("scene_loop_end_cycle").connect(
            self._on_scene_loop_end_cycle_for_notes
        )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def notes_get_all(self) -> list[DirectorNote]:
        """Return all active director notes."""
        raw_list = self.get_scene_state(NOTES_STATE_KEY, default=[])
        notes: list[DirectorNote] = []
        for item in raw_list:
            try:
                note = item if isinstance(item, DirectorNote) else DirectorNote(**item)
                notes.append(note)
            except Exception as exc:
                log.warning("director.notes.deserialize_error", error=exc, item=item)
        return notes

    def notes_get(self, note_id: str) -> DirectorNote | None:
        """Return a single note by id, or None."""
        for note in self.notes_get_all():
            if note.id == note_id:
                return note
        return None

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _notes_save(self, notes: list[DirectorNote]):
        """Persist the full notes list to scene agent state."""
        self.set_scene_states(
            **{NOTES_STATE_KEY: [n.model_dump() for n in notes]}
        )

    def notes_add(
        self,
        text: str,
        turns: int,
    ) -> DirectorNote:
        """
        Add a new director note.

        Args:
            text: The instruction text.
            turns: Number of scene turns before the note expires.
        Returns:
            The created :class:`DirectorNote`.
        """
        current_turn = self._notes_current_turn_index()
        note = DirectorNote(
            text=text,
            turns_remaining=turns,
            turns_initial=turns,
            created_at_turn=current_turn,
        )
        notes = self.notes_get_all()
        notes.append(note)
        self._notes_save(notes)
        log.debug(
            "director.notes.added",
            note_id=note.id,
            turns=turns,
        )
        return note

    def notes_update(
        self,
        note_id: str,
        text: str | None = None,
        turns: int | None = _SENTINEL,
    ) -> DirectorNote | None:
        """
        Update an existing note.  Only provided fields are changed.

        Omit ``turns`` (or pass the sentinel) to leave it unchanged.
        """
        notes = self.notes_get_all()
        for note in notes:
            if note.id == note_id:
                if text is not None:
                    note.text = text
                if turns is not _SENTINEL:
                    note.turns_remaining = turns
                    note.turns_initial = turns
                self._notes_save(notes)
                return note
        return None

    def notes_remove(self, note_id: str) -> bool:
        """Remove a note by id. Returns True if found and removed."""
        notes = self.notes_get_all()
        before = len(notes)
        notes = [n for n in notes if n.id != note_id]
        if len(notes) < before:
            self._notes_save(notes)
            return True
        return False

    # ------------------------------------------------------------------
    # Decay
    # ------------------------------------------------------------------

    def notes_decay(self, elapsed_turns: int = 1) -> list[DirectorNote]:
        """
        Decrement ``turns_remaining`` on all notes and remove expired ones.

        Args:
            elapsed_turns: How many scene turns elapsed since last decay.

        Returns:
            List of notes that were removed (expired).
        """
        notes = self.notes_get_all()
        surviving: list[DirectorNote] = []
        expired: list[DirectorNote] = []

        for note in notes:
            note.turns_remaining = max(0, note.turns_remaining - elapsed_turns)
            if note.turns_remaining <= 0:
                expired.append(note)
            else:
                surviving.append(note)

        if notes:
            self._notes_save(surviving)

        if expired:
            log.debug(
                "director.notes.decayed",
                expired_count=len(expired),
                remaining_count=len(surviving),
            )

        return expired

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def notes_for_prompt(self) -> list[dict]:
        """
        Return notes formatted for template consumption.

        Each dict has keys: ``text``, ``turns_remaining``,
        ``turns_initial``, ``turns_elapsed``.
        """
        current_turn = self._notes_current_turn_index()
        result = []
        for note in self.notes_get_all():
            turns_elapsed = max(0, current_turn - note.created_at_turn)
            result.append(
                {
                    "id": note.id,
                    "text": note.text,
                    "turns_remaining": note.turns_remaining,
                    "turns_initial": note.turns_initial,
                    "turns_elapsed": turns_elapsed,
                }
            )
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _notes_current_turn_index(self) -> int:
        """Return the current scene turn index (len of history)."""
        if self.scene and hasattr(self.scene, "history"):
            return len(self.scene.history)
        return 0

    async def _on_scene_loop_end_cycle_for_notes(self, emission):
        """
        Called at the end of each scene loop cycle.
        Count how many new narrator/character messages appeared and decay.
        """
        if not self.scene or not hasattr(self.scene, "history"):
            return

        notes = self.notes_get_all()
        if not notes:
            return

        # Count narrator + character messages in this cycle
        # We use a simple approach: the scene loop tracks the last known
        # history length in agent state, and we diff.
        last_len = self.get_scene_state("_notes_last_history_len", default=0)
        current_len = len(self.scene.history)

        if current_len <= last_len:
            return

        # Count only narrator and character messages in the new range
        new_turns = 0
        for i in range(last_len, current_len):
            msg = self.scene.history[i]
            if isinstance(msg, (NarratorMessage, CharacterMessage)):
                new_turns += 1

        self.set_scene_states(_notes_last_history_len=current_len)

        if new_turns > 0:
            self.notes_decay(elapsed_turns=new_turns)
