"""
Unit tests for helpers in ``talemate.history`` (the module-level functions
exported via ``__all__``).

Focuses on pure-ish helpers and Scene-state mutation:
- history_with_relative_time
- pop_history
- count_message_types_at_tail
- collect_source_entries (additional edge cases not covered by test_layered_history)
- collect_time_passages
- compute_layer_stats
- resolve_history_entry
- entry_contained
- emit_archive_add
- update_history_entry
- character_activity
- add_history_entry
- delete_history_entry
- reimport_history / validate_history
- insert/delete/update time passage helpers (covered indirectly via add_history_entry tests)

Reuses the conftest infrastructure (MockScene, bootstrap_scene) for character_activity
and add/delete/reimport tests that touch the memory agent and signal system.
"""

import uuid

import pytest

import talemate.emit.async_signals as async_signals
from conftest import MockScene, bootstrap_scene
from talemate.character import Character
from talemate.history import (
    ArchiveEntry,
    HistoryEntry,
    LayeredArchiveEntry,
    UnregeneratableEntryError,
    add_history_entry,
    character_activity,
    collect_source_entries,
    collect_time_passages,
    compute_layer_stats,
    count_message_types_at_tail,
    delete_history_entry,
    delete_time_passage,
    delete_time_passage_by_id,
    emit_archive_add,
    entry_contained,
    history_with_relative_time,
    insert_time_passage,
    insert_time_passage_after_message,
    pop_history,
    regenerate_history_entry,
    reimport_history,
    resolve_history_entry,
    update_history_entry,
    update_time_passage_by_id,
)
from talemate.scene_message import (
    CharacterMessage,
    DirectorMessage,
    NarratorMessage,
    ReinforcementMessage,
    TimePassageMessage,
)
from talemate.tale_mate import Actor, Player, Scene


# ---------------------------------------------------------------------------
# Shared helpers (kept module-level to avoid duplication across tests)
# ---------------------------------------------------------------------------


def make_character_message(text: str, character: str = "Alice") -> CharacterMessage:
    """Create a CharacterMessage in the canonical ``Name: text`` form."""
    msg = CharacterMessage(message=f"{character}: {text}", source="ai")
    return msg


def make_archived_entry(
    text: str = "summary",
    entry_id: str | None = None,
    start: int | None = None,
    end: int | None = None,
    ts: str = "PT1M",
    ts_start: str | None = None,
    ts_end: str | None = None,
) -> dict:
    """Construct a raw archived_history entry dict (the on-disk shape)."""
    entry: dict = {
        "id": entry_id or str(uuid.uuid4())[:8],
        "text": text,
        "ts": ts,
    }
    if start is not None:
        entry["start"] = start
    if end is not None:
        entry["end"] = end
    if ts_start is not None:
        entry["ts_start"] = ts_start
    if ts_end is not None:
        entry["ts_end"] = ts_end
    return entry


@pytest.fixture
def dummy_scene():
    """Factory that builds a real `Scene` with the requested timeline state.

    Uses the production `Scene` class so changes to its `history`/
    `archived_history`/`layered_history`/`ts` API are caught here. For
    tests that need full agent wiring (signals, memory agent), use the
    ``real_scene`` fixture below instead.
    """

    def _factory(
        history=None,
        archived_history=None,
        layered_history=None,
        ts: str = "PT0S",
    ):
        scene = Scene()
        scene.history = list(history or [])
        scene.archived_history = list(archived_history or [])
        scene.layered_history = list(layered_history or [])
        scene.ts = ts
        return scene

    return _factory


@pytest.fixture
def real_scene():
    """A bootstrapped MockScene with all real agents wired in.

    Use for tests that exercise full Scene methods or trigger signals that
    depend on the agent registry (memory agent, signals, etc.).
    """
    scene = MockScene()
    bootstrap_scene(scene)
    return scene


# ---------------------------------------------------------------------------
# history_with_relative_time
# ---------------------------------------------------------------------------


class TestHistoryWithRelativeTime:
    def test_returns_one_entry_per_input_with_human_time(self):
        history = [
            {"text": "First", "ts": "PT0S", "id": "a"},
            {"text": "Second", "ts": "PT30M", "id": "b"},
        ]
        result = history_with_relative_time(history, scene_time="PT1H")

        assert len(result) == 2
        # Index/layer should be set per-entry; layer defaults to 0.
        assert [r["index"] for r in result] == [0, 1]
        assert all(r["layer"] == 0 for r in result)
        # The "time" string is non-empty whenever ts is non-zero relative to scene_time.
        assert result[0]["time"]
        assert result[1]["time"]

    def test_layer_is_passed_through(self):
        history = [{"text": "x", "ts": "PT0S"}]
        result = history_with_relative_time(history, scene_time="PT1H", layer=2)
        assert result[0]["layer"] == 2

    def test_includes_ts_start_and_ts_end_human_strings(self):
        history = [
            {
                "text": "block",
                "ts": "PT30M",
                "ts_start": "PT0S",
                "ts_end": "PT30M",
            }
        ]
        result = history_with_relative_time(history, scene_time="PT1H")
        assert result[0]["ts_start"] == "PT0S"
        assert result[0]["ts_end"] == "PT30M"
        # time_start/time_end should be human-readable, not raw ISO durations.
        assert result[0]["time_start"]
        assert result[0]["time_end"]

    def test_missing_ts_start_end_yields_empty_human_string(self):
        # iso8601_diff_to_human returns "" when one side is falsy.
        history = [{"text": "block", "ts": "PT30M"}]
        result = history_with_relative_time(history, scene_time="PT1H")
        assert result[0]["time_start"] == ""
        assert result[0]["time_end"] == ""

    def test_empty_history_returns_empty_list(self):
        assert history_with_relative_time([], scene_time="PT1H") == []


# ---------------------------------------------------------------------------
# pop_history
# ---------------------------------------------------------------------------


class TestPopHistory:
    def test_pops_only_last_matching_by_default(self):
        history = [
            make_character_message("hi"),
            NarratorMessage(message="N1", source="ai"),
            make_character_message("bye"),
            NarratorMessage(message="N2", source="ai"),
        ]
        pop_history(history, typ="narrator")
        # Only the *last* narrator is removed.
        assert [type(m).__name__ for m in history] == [
            "CharacterMessage",
            "NarratorMessage",
            "CharacterMessage",
        ]
        assert history[1].message == "N1"

    def test_pops_all_when_all_true(self):
        history = [
            NarratorMessage(message="N1", source="ai"),
            make_character_message("hi"),
            NarratorMessage(message="N2", source="ai"),
        ]
        pop_history(history, typ="narrator", all=True)
        assert len(history) == 1
        assert isinstance(history[0], CharacterMessage)

    def test_filter_by_source(self):
        history = [
            NarratorMessage(message="from-ai-1", source="ai"),
            NarratorMessage(message="from-manual", source="manual"),
            NarratorMessage(message="from-ai-2", source="ai"),
        ]
        pop_history(history, typ="narrator", source="manual", all=True)
        assert len(history) == 2
        assert all(m.source == "ai" for m in history)

    def test_max_iterations_limits_search_depth(self):
        history = [
            NarratorMessage(message="oldest", source="ai"),
            make_character_message("a"),
            make_character_message("b"),
            make_character_message("c"),
        ]
        # Walking from tail, iterations are counted on *non-matching* steps.
        # With max_iterations=2 we only inspect the last 2-3 messages and
        # never reach the lone NarratorMessage at index 0.
        pop_history(history, typ="narrator", max_iterations=2)
        assert any(isinstance(m, NarratorMessage) for m in history)

    def test_reverse_pops_oldest_match_first(self):
        history = [
            NarratorMessage(message="N-old", source="ai"),
            make_character_message("a"),
            NarratorMessage(message="N-new", source="ai"),
        ]
        pop_history(history, typ="narrator", reverse=True)
        # The *oldest* narrator should be the one removed in reverse mode.
        remaining = [m.message for m in history if isinstance(m, NarratorMessage)]
        assert remaining == ["N-new"]

    def test_no_match_leaves_history_untouched(self):
        history = [make_character_message("only one")]
        pop_history(history, typ="narrator", all=True)
        assert len(history) == 1


# ---------------------------------------------------------------------------
# count_message_types_at_tail
# ---------------------------------------------------------------------------


class TestCountMessageTypesAtTail:
    def test_empty_history_returns_zero(self):
        assert count_message_types_at_tail([], target_types=["narrator"]) == 0

    def test_counts_only_consecutive_narrators(self):
        history = [
            make_character_message("a"),
            NarratorMessage(message="n1", source="ai"),
            NarratorMessage(message="n2", source="ai"),
        ]
        assert count_message_types_at_tail(history, ["narrator"]) == 2

    def test_breaks_on_unignored_type(self):
        history = [
            NarratorMessage(message="n0", source="ai"),  # behind the break
            make_character_message("blocker"),
            NarratorMessage(message="n1", source="ai"),
            NarratorMessage(message="n2", source="ai"),
        ]
        assert count_message_types_at_tail(history, ["narrator"]) == 2

    def test_ignore_types_does_not_break_count(self):
        director_msg = DirectorMessage(message="cut!", source="ai")
        history = [
            NarratorMessage(message="n1", source="ai"),
            director_msg,
            NarratorMessage(message="n2", source="ai"),
        ]
        # Without ignore_types, the director would break the count at 1.
        assert count_message_types_at_tail(history, ["narrator"]) == 1
        # With director ignored, the count includes both narrators.
        assert (
            count_message_types_at_tail(
                history, ["narrator"], ignore_types=["director"]
            )
            == 2
        )

    def test_zero_when_tail_is_unrelated(self):
        history = [
            NarratorMessage(message="n", source="ai"),
            make_character_message("char"),
        ]
        assert count_message_types_at_tail(history, ["narrator"]) == 0


# ---------------------------------------------------------------------------
# collect_source_entries (extra edge cases on top of test_layered_history.py)
# ---------------------------------------------------------------------------


class TestCollectSourceEntriesExtra:
    def test_layer0_filters_reinforcement_and_context_investigation(self, dummy_scene):
        good_a = make_character_message("A")
        good_b = make_character_message("B")
        reinf = ReinforcementMessage(message="quiet note", source="ai")
        scene = dummy_scene(history=[good_a, reinf, good_b])

        entry = HistoryEntry(
            text="summary",
            ts="PT1H",
            index=0,
            layer=0,
            start=0,
            end=2,
        )
        result = collect_source_entries(scene, entry)
        # Reinforcement is filtered out, character messages remain.
        assert len(result) == 2
        assert all("quiet note" not in r.text for r in result)
        # SourceEntry __str__ returns the text.
        assert str(result[0]) == result[0].text


# ---------------------------------------------------------------------------
# collect_time_passages
# ---------------------------------------------------------------------------


class TestCollectTimePassages:
    def test_no_time_messages_returns_empty(self, dummy_scene):
        scene = dummy_scene(history=[make_character_message("x")])
        assert collect_time_passages(scene) == []

    def test_collects_each_time_passage_with_human_string(self, dummy_scene):
        tp1 = TimePassageMessage(ts="PT1H", message="an hour later")
        tp2 = TimePassageMessage(ts="P1D", message="a day later")
        scene = dummy_scene(
            history=[make_character_message("x"), tp1, make_character_message("y"), tp2]
        )

        result = collect_time_passages(scene)
        assert len(result) == 2
        # Indices should match the positions in scene.history.
        assert result[0]["history_index"] == 1
        assert result[1]["history_index"] == 3
        # ISO durations should be passed through unchanged.
        assert result[0]["ts"] == "PT1H"
        assert result[1]["ts"] == "P1D"
        # Human strings are non-empty (specific format is owned by util.time).
        assert result[0]["human"]
        assert result[1]["human"]
        # amount/unit decomposition should be present.
        assert "amount" in result[0]
        assert "unit" in result[0]


# ---------------------------------------------------------------------------
# compute_layer_stats
# ---------------------------------------------------------------------------


class TestComputeLayerStats:
    def test_layer_zero_uses_archived_history_and_history_for_sources(
        self, dummy_scene
    ):
        history = [
            make_character_message("a"),
            make_character_message("b"),
            make_character_message("c"),
        ]
        archived = [make_archived_entry("summary 0", start=0, end=2)]
        scene = dummy_scene(history=history, archived_history=archived)

        stats = compute_layer_stats(scene, layer=0)
        assert stats["layer"] == 0
        assert stats["layer_entry_count"] == 1
        assert stats["source_entry_count"] == 3  # a, b, c
        # Token counts should be positive ints (we don't assert exact values
        # because tiktoken-derived counts vary by model).
        assert isinstance(stats["layer_tokens"], int)
        assert isinstance(stats["source_tokens"], int)
        assert stats["layer_tokens"] > 0
        assert stats["source_tokens"] > 0

    def test_layer_zero_skips_static_entries_for_source_count(self, dummy_scene):
        history = [make_character_message("a"), make_character_message("b")]
        # Static entry has no start/end — its source contribution is zero.
        archived = [
            make_archived_entry("intro static"),
            make_archived_entry("summary", start=0, end=1),
        ]
        scene = dummy_scene(history=history, archived_history=archived)

        stats = compute_layer_stats(scene, layer=0)
        assert stats["layer_entry_count"] == 2
        # Only the summary entry contributes 2 source messages.
        assert stats["source_entry_count"] == 2

    def test_layer_one_sources_from_archived_history(self, dummy_scene):
        archived = [
            make_archived_entry("a-text", start=0, end=2),
            make_archived_entry("b-text", start=3, end=5),
        ]
        layer1 = [
            {
                "id": "L1-0",
                "text": "rolled up",
                "start": 0,
                "end": 1,
                "ts": "PT2H",
            }
        ]
        scene = dummy_scene(archived_history=archived, layered_history=[layer1])

        stats = compute_layer_stats(scene, layer=1)
        assert stats["layer"] == 1
        assert stats["layer_entry_count"] == 1
        assert stats["source_entry_count"] == 2  # both archived entries

    def test_layer_two_sources_from_layered_history_zero(self, dummy_scene):
        layer1 = [
            {"id": "L1-0", "text": "x", "start": 0, "end": 0, "ts": "PT1H"},
            {"id": "L1-1", "text": "y", "start": 1, "end": 1, "ts": "PT2H"},
        ]
        layer2 = [{"id": "L2-0", "text": "rolled", "start": 0, "end": 1, "ts": "PT3H"}]
        scene = dummy_scene(layered_history=[layer1, layer2])

        stats = compute_layer_stats(scene, layer=2)
        assert stats["layer_entry_count"] == 1
        assert stats["source_entry_count"] == 2

    def test_invalid_layer_raises_value_error(self, dummy_scene):
        scene = dummy_scene()
        with pytest.raises(ValueError):
            compute_layer_stats(scene, layer=5)


# ---------------------------------------------------------------------------
# resolve_history_entry
# ---------------------------------------------------------------------------


class TestResolveHistoryEntry:
    def test_layer_zero_returns_archive_entry(self, dummy_scene):
        archived = [make_archived_entry("hello", entry_id="aa", start=0, end=1)]
        scene = dummy_scene(archived_history=archived)
        entry = HistoryEntry(text="hello", ts="PT1M", index=0, layer=0)
        resolved = resolve_history_entry(scene, entry)

        assert isinstance(resolved, ArchiveEntry)
        # Layered fields are not present on bare ArchiveEntry.
        assert (
            not hasattr(resolved, "ts_start")
            or getattr(resolved, "ts_start", None) is None
            or not isinstance(resolved, LayeredArchiveEntry)
        )
        assert resolved.id == "aa"
        assert resolved.text == "hello"

    def test_nonzero_layer_returns_layered_archive_entry(self, dummy_scene):
        layer1 = [
            {
                "id": "L1",
                "text": "rolled",
                "start": 0,
                "end": 0,
                "ts": "PT3H",
                "ts_start": "PT0S",
                "ts_end": "PT3H",
            }
        ]
        scene = dummy_scene(layered_history=[layer1])
        entry = HistoryEntry(text="rolled", ts="PT3H", index=0, layer=1)
        resolved = resolve_history_entry(scene, entry)

        assert isinstance(resolved, LayeredArchiveEntry)
        assert resolved.ts_start == "PT0S"
        assert resolved.ts_end == "PT3H"


# ---------------------------------------------------------------------------
# entry_contained
# ---------------------------------------------------------------------------


class TestEntryContained:
    def test_finds_id_in_layer_zero_source_messages(self, dummy_scene):
        msg = make_character_message("hello")
        target_id = msg.id
        scene = dummy_scene(history=[make_character_message("first"), msg])

        container = HistoryEntry(
            text="summary",
            ts="PT1M",
            index=0,
            layer=0,
            start=0,
            end=1,
        )
        assert entry_contained(scene, target_id, container)

    def test_returns_false_when_id_not_present(self, dummy_scene):
        scene = dummy_scene(history=[make_character_message("hello")])
        container = HistoryEntry(
            text="summary",
            ts="PT1M",
            index=0,
            layer=0,
            start=0,
            end=0,
        )
        assert not entry_contained(scene, "no-such-id", container)

    def test_finds_id_recursively_through_layers(self, dummy_scene):
        # Layer-1 entry refers to archived entries 0..0; archived[0] refers
        # to history[0..0]; we should be able to find the underlying message id.
        msg = make_character_message("deep")
        archived = [
            make_archived_entry("summary", entry_id="arch-0", start=0, end=0, ts="PT1H")
        ]
        layer1 = [
            {
                "id": "L1-0",
                "text": "rolled",
                "start": 0,
                "end": 0,
                "ts": "PT1H",
            }
        ]
        scene = dummy_scene(
            history=[msg], archived_history=archived, layered_history=[layer1]
        )

        container = HistoryEntry(
            text="rolled", ts="PT1H", index=0, layer=2, start=0, end=0
        )
        assert entry_contained(scene, msg.id, container)
        # Negative case: an arbitrary id is not found.
        assert not entry_contained(scene, "ghost-id", container)


# ---------------------------------------------------------------------------
# emit_archive_add
# ---------------------------------------------------------------------------


class TestEmitArchiveAdd:
    @pytest.mark.asyncio
    async def test_signal_receives_archive_event(self, dummy_scene):
        scene = dummy_scene()
        captured: list = []

        async def listener(event):
            captured.append(event)

        signal = async_signals.get("archive_add")
        signal.connect(listener)
        try:
            entry = ArchiveEntry(text="my entry", id="abc123", ts="PT5M")
            await emit_archive_add(scene, entry)
        finally:
            signal.disconnect(listener)

        assert len(captured) == 1
        evt = captured[0]
        assert evt.scene is scene
        assert evt.event_type == "archive_add"
        assert evt.text == "my entry"
        assert evt.ts == "PT5M"
        assert evt.memory_id == "abc123"


# ---------------------------------------------------------------------------
# update_history_entry
# ---------------------------------------------------------------------------


class TestUpdateHistoryEntry:
    @pytest.mark.asyncio
    async def test_updates_layer_zero_and_emits_signal(self, dummy_scene):
        scene = dummy_scene(
            archived_history=[make_archived_entry("old", entry_id="x", start=0, end=1)]
        )

        captured: list = []

        async def listener(event):
            captured.append(event)

        signal = async_signals.get("archive_add")
        signal.connect(listener)
        try:
            entry = HistoryEntry(
                text="new text",
                ts="PT2H",
                id="x",
                index=0,
                layer=0,
                start=0,
                end=1,
            )
            result = await update_history_entry(scene, entry)
        finally:
            signal.disconnect(listener)

        assert isinstance(result, ArchiveEntry)
        assert result.text == "new text"
        # The underlying raw dict in archived_history should be replaced.
        assert scene.archived_history[0]["text"] == "new text"
        assert scene.archived_history[0]["id"] == "x"
        # The signal should have fired exactly once with the updated fields.
        assert len(captured) == 1
        assert captured[0].text == "new text"

    @pytest.mark.asyncio
    async def test_updates_layered_history_in_place(self, dummy_scene):
        layer1 = [{"id": "L1", "text": "old", "start": 0, "end": 1, "ts": "PT2H"}]
        scene = dummy_scene(layered_history=[layer1])

        entry = HistoryEntry(
            text="rolled-new",
            ts="PT2H",
            id="L1",
            index=0,
            layer=1,
            start=0,
            end=1,
        )
        result = await update_history_entry(scene, entry)
        assert isinstance(result, LayeredArchiveEntry)
        assert scene.layered_history[0][0]["text"] == "rolled-new"


# ---------------------------------------------------------------------------
# regenerate_history_entry: error paths only (the success path requires LLM
# calls and is intentionally out of scope for unit tests).
# ---------------------------------------------------------------------------


class TestRegenerateHistoryEntryErrors:
    @pytest.mark.asyncio
    async def test_no_start_end_raises(self, real_scene):
        entry = HistoryEntry(text="static", ts="PT0S", index=0, layer=0)
        with pytest.raises(UnregeneratableEntryError):
            await regenerate_history_entry(real_scene, entry)

    @pytest.mark.asyncio
    async def test_no_source_entries_raises(self, real_scene):
        # Layer-0 entry pointing at an empty/non-existent slice of history.
        entry = HistoryEntry(
            text="empty",
            ts="PT0S",
            index=0,
            layer=0,
            start=0,
            end=0,
        )
        # scene.history is empty -> collect_source_entries returns []
        with pytest.raises(UnregeneratableEntryError):
            await regenerate_history_entry(real_scene, entry)

    @pytest.mark.asyncio
    async def test_entry_index_not_found_raises(self, real_scene):
        # Provide a valid history range but reference a nonexistent archived
        # index, so resolve_history_entry hits IndexError and raises.
        real_scene.history = [make_character_message("a")]
        real_scene.archived_history = []
        entry = HistoryEntry(
            text="missing", ts="PT0S", index=99, layer=0, start=0, end=0
        )
        with pytest.raises(UnregeneratableEntryError):
            await regenerate_history_entry(real_scene, entry)


# ---------------------------------------------------------------------------
# character_activity
# ---------------------------------------------------------------------------


def _add_character(scene, name: str, is_player: bool = False) -> Character:
    """Attach a Character (and Actor) to scene; mark active."""
    character = Character(name=name, is_player=is_player)
    actor_cls = Player if is_player else Actor
    actor = actor_cls(character=character, agent=None)
    actor.scene = scene
    scene.actors.append(actor)
    scene.character_data[name] = character
    if name not in scene.active_characters:
        scene.active_characters.append(name)
    return character


class TestCharacterActivity:
    @pytest.mark.asyncio
    async def test_no_messages_marks_none_have_acted(self, real_scene):
        _add_character(real_scene, "Alice")
        _add_character(real_scene, "Bob")

        result = await character_activity(real_scene)
        assert result.none_have_acted is True
        # Both characters appended (in iteration order of character_names).
        assert [c.name for c in result.characters] == ["Alice", "Bob"]

    @pytest.mark.asyncio
    async def test_most_recent_actor_first(self, real_scene):
        _add_character(real_scene, "Alice")
        _add_character(real_scene, "Bob")

        # Alice spoke earlier, Bob spoke last.
        real_scene.history = [
            make_character_message("hi", character="Alice"),
            make_character_message("yo", character="Bob"),
        ]
        result = await character_activity(real_scene)

        assert result.none_have_acted is False
        assert result.characters[0].name == "Bob"
        assert result.characters[1].name == "Alice"

    @pytest.mark.asyncio
    async def test_silent_characters_appended_after_actors(self, real_scene):
        _add_character(real_scene, "Alice")
        _add_character(real_scene, "Bob")
        _add_character(real_scene, "Carol")

        real_scene.history = [make_character_message("yo", character="Bob")]
        result = await character_activity(real_scene)

        # Bob spoke, Alice/Carol did not — Bob comes first; remaining order
        # follows scene.character_names.
        names = [c.name for c in result.characters]
        assert names[0] == "Bob"
        assert set(names[1:]) == {"Alice", "Carol"}
        assert result.none_have_acted is False

    @pytest.mark.asyncio
    async def test_messages_for_unknown_character_are_ignored(self, real_scene):
        _add_character(real_scene, "Alice")
        # Ghost is not registered as an active character.
        real_scene.history = [make_character_message("hi", character="Ghost")]
        result = await character_activity(real_scene)

        assert result.none_have_acted is True
        assert [c.name for c in result.characters] == ["Alice"]


# ---------------------------------------------------------------------------
# add_history_entry / delete_history_entry / reimport_history
#
# These exercise the manual base-layer entry path. They depend on the memory
# agent (via reimport_history -> validate_history -> emit_archive_add), so
# they require the bootstrapped real_scene fixture.
# ---------------------------------------------------------------------------


class TestAddHistoryEntry:
    @pytest.mark.asyncio
    async def test_first_entry_sets_scene_ts_and_appends(self, real_scene):
        assert real_scene.archived_history == []
        result = await add_history_entry(real_scene, "scene begins", offset="PT1H")

        # The first entry is always pushed at PT0S, and scene.ts becomes the offset.
        assert real_scene.ts == "PT1H"
        assert len(real_scene.archived_history) == 1
        assert real_scene.archived_history[0]["text"] == "scene begins"
        assert real_scene.archived_history[0]["ts"] == "PT0S"
        assert "id" in real_scene.archived_history[0]
        # The dict-ish return shape should round-trip through ArchiveEntry.
        assert ArchiveEntry(**result).text == "scene begins"

    @pytest.mark.asyncio
    async def test_must_be_older_than_first_summary_entry(self, real_scene):
        # Pre-existing summarized archive at ts=PT30M, with history support.
        real_scene.history = [make_character_message("a")]
        real_scene.archived_history = [
            make_archived_entry(
                "first summary",
                entry_id="s1",
                start=0,
                end=0,
                ts="PT30M",
            )
        ]
        real_scene.ts = "PT1H"

        # offset PT10M means new_ts = PT50M, which is *not* older than PT30M.
        with pytest.raises(ValueError):
            await add_history_entry(real_scene, "too late", offset="PT10M")

    @pytest.mark.asyncio
    async def test_inserts_in_chronological_order(self, real_scene):
        # Two pre-existing manual entries at PT5M and PT15M (both static, no
        # start/end -> first_summary stays None and the time check is skipped).
        real_scene.archived_history = [
            make_archived_entry("first", entry_id="m1", ts="PT5M"),
            make_archived_entry("second", entry_id="m2", ts="PT15M"),
        ]
        real_scene.ts = "PT1H"

        # offset PT50M -> new_ts = PT10M -> should slot between PT5M and PT15M.
        await add_history_entry(real_scene, "middle", offset="PT50M")

        texts = [e["text"] for e in real_scene.archived_history]
        assert texts == ["first", "middle", "second"]


class TestDeleteHistoryEntry:
    @pytest.mark.asyncio
    async def test_only_manual_base_layer_entries_can_be_deleted(self, real_scene):
        # Layered entry must be rejected.
        layered_entry = HistoryEntry(text="x", ts="PT0S", index=0, layer=1, id="any")
        with pytest.raises(ValueError):
            await delete_history_entry(real_scene, layered_entry)

        # Summarized base entry (start/end set) must be rejected.
        summarized = HistoryEntry(
            text="x", ts="PT0S", index=0, layer=0, id="x", start=0, end=1
        )
        with pytest.raises(ValueError):
            await delete_history_entry(real_scene, summarized)

    @pytest.mark.asyncio
    async def test_missing_entry_raises(self, real_scene):
        real_scene.archived_history = [
            make_archived_entry("only", entry_id="here", ts="PT0S")
        ]
        entry = HistoryEntry(text="ghost", ts="PT0S", index=0, layer=0, id="missing")
        with pytest.raises(ValueError):
            await delete_history_entry(real_scene, entry)

    @pytest.mark.asyncio
    async def test_removes_entry_and_returns_archive_entry(self, real_scene):
        real_scene.archived_history = [
            make_archived_entry("a", entry_id="aa", ts="PT0S"),
            make_archived_entry("b", entry_id="bb", ts="PT5M"),
        ]
        real_scene.ts = "PT1H"

        entry = HistoryEntry(text="b", ts="PT5M", index=1, layer=0, id="bb")
        result = await delete_history_entry(real_scene, entry)

        assert isinstance(result, ArchiveEntry)
        assert result.id == "bb"
        assert [e["id"] for e in real_scene.archived_history] == ["aa"]


# ---------------------------------------------------------------------------
# reimport_history
# ---------------------------------------------------------------------------


class TestReimportHistory:
    @pytest.mark.asyncio
    async def test_assigns_missing_ids_to_layered_entries(self, real_scene):
        real_scene.archived_history = [
            make_archived_entry("ok", entry_id="a1", ts="PT0S"),
        ]
        # Layered entry with no id -> validate_history should fill one in.
        real_scene.layered_history = [
            [{"text": "no id here", "ts": "PT1H", "start": 0, "end": 0}]
        ]

        await reimport_history(real_scene, emit_status=False)

        assigned_id = real_scene.layered_history[0][0]["id"]
        assert assigned_id  # non-empty
        assert isinstance(assigned_id, str)
        # Reimport should be idempotent: a second pass keeps the same id.
        prior = assigned_id
        await reimport_history(real_scene, emit_status=False)
        assert real_scene.layered_history[0][0]["id"] == prior

    @pytest.mark.asyncio
    async def test_archived_entries_with_missing_ids_get_normalized(self, real_scene):
        # An archived entry without an id should be regenerated through
        # ArchiveEntry validation, which assigns a default uuid-based id.
        real_scene.archived_history = [
            {"text": "no id", "ts": "PT0S"},
        ]
        await reimport_history(real_scene, emit_status=False)
        normalized = real_scene.archived_history[0]
        assert "id" in normalized
        assert normalized["text"] == "no id"


# ---------------------------------------------------------------------------
# insert_time_passage / delete_time_passage / *_by_id / update_time_passage_by_id
#
# These helpers mutate scene.history and re-index archived_history. They rely
# on real Scene methods (message_index, fix_time), so we use the bootstrapped
# real_scene fixture.
# ---------------------------------------------------------------------------


class TestInsertTimePassage:
    def test_inserts_before_archive_source_range(self, real_scene):
        msgs = [make_character_message("a"), make_character_message("b")]
        real_scene.history = list(msgs)
        # Archive entry covers history[0..1].
        real_scene.archived_history = [
            make_archived_entry("summary", entry_id="s1", start=0, end=1, ts="PT0S"),
        ]
        real_scene.ts = "PT0S"

        tp = insert_time_passage(real_scene, archive_index=0, amount=2, unit="hours")

        # The TimePassageMessage should be inserted at the start of the source
        # range (history[0]), pushing both character messages back one slot.
        assert isinstance(real_scene.history[0], TimePassageMessage)
        assert real_scene.history[0] is tp
        assert isinstance(real_scene.history[1], CharacterMessage)
        assert isinstance(real_scene.history[2], CharacterMessage)

        # The archived entry's start/end indices should both be shifted by +1
        # since the insertion point sits at start.
        assert real_scene.archived_history[0]["start"] == 1
        assert real_scene.archived_history[0]["end"] == 2

    def test_out_of_range_raises_index_error(self, real_scene):
        with pytest.raises(IndexError):
            insert_time_passage(real_scene, archive_index=3, amount=1, unit="hours")

    def test_static_archive_entry_raises_value_error(self, real_scene):
        # Static manual archive entry has no start/end -> can't anchor a passage.
        real_scene.history = [make_character_message("a")]
        real_scene.archived_history = [
            make_archived_entry("static", entry_id="m", ts="PT0S"),
        ]
        with pytest.raises(ValueError):
            insert_time_passage(real_scene, archive_index=0, amount=1, unit="hours")


class TestDeleteTimePassage:
    def test_deletes_passage_and_decrements_following_indices(self, real_scene):
        a = make_character_message("a")
        tp = TimePassageMessage(ts="PT1H", message="an hour later")
        b = make_character_message("b")
        c = make_character_message("c")
        real_scene.history = [a, tp, b, c]
        # Archive covers messages after the time passage; their indices need
        # to shrink by 1 once the passage is removed.
        real_scene.archived_history = [
            make_archived_entry("summary", entry_id="s1", start=2, end=3, ts="PT1H"),
        ]
        real_scene.ts = "PT1H"

        delete_time_passage(real_scene, history_index=1)

        assert len(real_scene.history) == 3
        assert all(not isinstance(m, TimePassageMessage) for m in real_scene.history)
        # start was 2 (> 1) -> becomes 1; end was 3 -> becomes 2.
        assert real_scene.archived_history[0]["start"] == 1
        assert real_scene.archived_history[0]["end"] == 2

    def test_out_of_range_index_raises(self, real_scene):
        real_scene.history = [make_character_message("a")]
        with pytest.raises(IndexError):
            delete_time_passage(real_scene, history_index=5)

    def test_non_time_passage_raises_value_error(self, real_scene):
        real_scene.history = [make_character_message("not a passage")]
        with pytest.raises(ValueError):
            delete_time_passage(real_scene, history_index=0)


class TestTimePassageByIdHelpers:
    def test_insert_after_message_id(self, real_scene):
        a = make_character_message("a")
        b = make_character_message("b")
        real_scene.history = [a, b]
        real_scene.ts = "PT0S"

        tp = insert_time_passage_after_message(
            real_scene, message_id=a.id, amount=30, unit="minutes"
        )
        # tp inserted between a and b.
        assert real_scene.history[0] is a
        assert real_scene.history[1] is tp
        assert real_scene.history[2] is b

    def test_insert_after_unknown_message_raises(self, real_scene):
        real_scene.history = [make_character_message("a")]
        with pytest.raises(ValueError):
            insert_time_passage_after_message(
                real_scene, message_id=987654321, amount=1, unit="hours"
            )

    def test_delete_by_id(self, real_scene):
        a = make_character_message("a")
        tp = TimePassageMessage(ts="PT1H", message="an hour later")
        b = make_character_message("b")
        real_scene.history = [a, tp, b]
        real_scene.ts = "PT1H"

        delete_time_passage_by_id(real_scene, message_id=tp.id)
        assert tp not in real_scene.history
        assert len(real_scene.history) == 2

    def test_delete_by_unknown_id_raises(self, real_scene):
        real_scene.history = [make_character_message("a")]
        with pytest.raises(ValueError):
            delete_time_passage_by_id(real_scene, message_id=987654321)

    def test_update_by_id_changes_duration(self, real_scene):
        a = make_character_message("a")
        tp = TimePassageMessage(ts="PT1H", message="an hour later")
        real_scene.history = [a, tp]
        real_scene.ts = "PT1H"

        update_time_passage_by_id(real_scene, message_id=tp.id, amount=2, unit="days")
        assert tp.ts == "P2D"
        # The human-readable message should be regenerated and non-empty.
        assert tp.message
        # And the message string should reflect the new duration somehow.
        assert tp.message != "an hour later"

    def test_update_unknown_id_raises(self, real_scene):
        real_scene.history = [make_character_message("a")]
        with pytest.raises(ValueError):
            update_time_passage_by_id(
                real_scene, message_id=987654321, amount=1, unit="hours"
            )

    def test_update_by_id_on_non_time_passage_raises(self, real_scene):
        char_msg = make_character_message("hello")
        real_scene.history = [char_msg]
        with pytest.raises(ValueError):
            update_time_passage_by_id(
                real_scene, message_id=char_msg.id, amount=1, unit="hours"
            )


# ---------------------------------------------------------------------------
# add_history_entry: timeline-shift path
# ---------------------------------------------------------------------------


class TestAddHistoryEntryTimelineShift:
    @pytest.mark.asyncio
    async def test_offset_predates_scene_shifts_timeline(self, real_scene):
        # Two static manual entries at PT5M and PT15M (no start/end) so the
        # first_summary check is bypassed and the shift path can fire.
        real_scene.archived_history = [
            make_archived_entry("first", entry_id="m1", ts="PT5M"),
            make_archived_entry("second", entry_id="m2", ts="PT15M"),
        ]
        real_scene.ts = "PT30M"

        # offset=PT2H means new_ts_td = scene_td(30m) - offset_td(2h) = -1h30m,
        # which is negative -> shift_scene_timeline is invoked with PT1H30M.
        await add_history_entry(real_scene, "ancient", offset="PT2H")

        # The new entry sits at zero (PT0S/P0D); existing entries are pushed
        # forward by the shift amount (PT1H30M -> PT5M+1h30m=PT1H35M, etc.).
        ts_values = [e["ts"] for e in real_scene.archived_history]
        # The new entry was inserted at zero (rendered as P0D by isodate).
        assert any(ts in {"PT0S", "P0D"} for ts in ts_values)
        # And the previously-existing entries were pushed forward.
        assert "PT1H35M" in ts_values  # PT5M  + PT1H30M
        assert "PT1H45M" in ts_values  # PT15M + PT1H30M
        # scene.ts was 30m; shifted forward by 1h30m -> PT2H.
        assert real_scene.ts == "PT2H"

    @pytest.mark.asyncio
    async def test_delete_oldest_manual_entry_shifts_timeline(self, real_scene):
        # Two static manual entries; deleting index 0 (PT0S) triggers the
        # timeline-shift path: shift_scene_timeline is called with
        # ``-PT15M`` so what *was* the second entry now anchors at zero.
        real_scene.archived_history = [
            make_archived_entry("oldest", entry_id="m1", ts="PT0S"),
            make_archived_entry("next", entry_id="m2", ts="PT15M"),
        ]
        real_scene.ts = "PT1H"

        entry = HistoryEntry(text="oldest", ts="PT0S", index=0, layer=0, id="m1")
        await delete_history_entry(real_scene, entry)

        # The single remaining entry's ts should now be zero (the shift moved
        # PT15M -> P0D). isodate emits "P0D" for zero, not "PT0S".
        assert len(real_scene.archived_history) == 1
        remaining_ts = real_scene.archived_history[0]["ts"]
        assert remaining_ts in {"PT0S", "P0D"}
        # scene.ts is then re-synced from that archived entry.
        assert real_scene.ts in {"PT0S", "P0D"}
