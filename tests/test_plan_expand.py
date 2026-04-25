"""
Unit tests for the director plan expand system.

Tests cover:
- Deliberate chunking (tension-valley splitting)
- Arc position metadata computation
- Leaked tag detection
- PlanMixin config properties
- Chat creation for generate_arc modes
"""

from unittest.mock import Mock

from talemate.agents.director.plan.expand import (
    compute_chunks,
    compute_arc_info,
    has_leaked_tags,
    MIN_CHUNK_BEATS,
)
from talemate.agents.director.plan.schema import Beat


def _make_beats(tensions: list[float]) -> list[Beat]:
    """Helper to create Beat objects with given tension values."""
    return [
        Beat(
            description=f"Beat {i + 1}",
            order=i + 1,
            tension=t,
            pacing="moderate",
            type="narration",
        )
        for i, t in enumerate(tensions)
    ]


class TestComputeChunks:
    """Tests for deliberate chunking at tension valleys."""

    def test_single_chunk_when_under_max(self):
        beats = _make_beats([0.2, 0.4, 0.6, 0.8])
        chunks = compute_chunks(beats, max_chunk_size=8)
        assert len(chunks) == 1
        assert len(chunks[0]) == 4

    def test_single_chunk_when_equal_to_max(self):
        beats = _make_beats([0.2, 0.4, 0.6, 0.8, 1.0])
        chunks = compute_chunks(beats, max_chunk_size=5)
        assert len(chunks) == 1
        assert len(chunks[0]) == 5

    def test_splits_at_tension_valley(self):
        # Tension rises to 0.7 then drops to 0.3 — should split at the valley
        beats = _make_beats([0.2, 0.4, 0.7, 0.3, 0.5, 0.8, 1.0])
        chunks = compute_chunks(beats, max_chunk_size=6)
        assert len(chunks) == 2
        # First chunk: beats 1-3 (tension rises to 0.7)
        assert len(chunks[0]) == 3
        # Second chunk: beats 4-7 (starts at 0.3)
        assert len(chunks[1]) == 4

    def test_respects_min_chunk_size(self):
        # Valley at beat 2, but that would leave chunk 1 with only 2 beats
        beats = _make_beats([0.5, 0.3, 0.4, 0.6, 0.8, 1.0])
        chunks = compute_chunks(beats, max_chunk_size=5)
        # Should not split at beat 2 because chunk would be < MIN_CHUNK_BEATS
        assert all(len(c) >= MIN_CHUNK_BEATS for c in chunks)

    def test_splits_at_max_size_when_no_valley(self):
        # Monotonically increasing — no valleys, must split at max
        beats = _make_beats([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
        chunks = compute_chunks(beats, max_chunk_size=4)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4
        assert len(chunks[1]) == 4

    def test_merges_small_leftover(self):
        # 7 beats, max 4 — would split to [4, 3], but if valley is at 5
        # we might get [5, 2] which should merge to [7]
        beats = _make_beats([0.2, 0.4, 0.6, 0.8, 0.5, 0.3, 0.2])
        chunks = compute_chunks(beats, max_chunk_size=6)
        # With valley at beat 4→5, should split to [4, 3]
        # But leftover of 2 is < MIN_CHUNK_BEATS, so merges back
        for chunk in chunks:
            assert len(chunk) >= MIN_CHUNK_BEATS or len(chunks) == 1

    def test_multiple_valleys(self):
        beats = _make_beats([0.2, 0.5, 0.8, 0.3, 0.6, 0.9, 0.4, 0.7, 1.0])
        chunks = compute_chunks(beats, max_chunk_size=8)
        # Two valleys: beat 3→4 (0.8→0.3) and beat 6→7 (0.9→0.4)
        assert len(chunks) == 3

    def test_empty_beats(self):
        chunks = compute_chunks([], max_chunk_size=5)
        assert len(chunks) == 1
        assert len(chunks[0]) == 0


class TestComputeArcInfo:
    """Tests for arc position metadata computation."""

    def test_single_chunk_is_full_open_by_default(self):
        # Default is continuation mode (close_arc=False) -> full_open
        beats = _make_beats([0.2, 0.5, 0.8])
        chunks = [beats]
        infos = compute_arc_info(chunks, beats)
        assert len(infos) == 1
        assert infos[0].position == "full_open"

    def test_single_chunk_is_full_when_closed(self):
        beats = _make_beats([0.2, 0.5, 0.8])
        chunks = [beats]
        infos = compute_arc_info(chunks, beats, close_arc=True)
        assert len(infos) == 1
        assert infos[0].position == "full"

    def test_two_chunks_opening_and_climax(self):
        beats = _make_beats([0.2, 0.4, 0.6, 0.8, 0.9, 1.0])
        chunks = [beats[:3], beats[3:]]
        infos = compute_arc_info(chunks, beats)
        assert infos[0].position == "opening"
        assert infos[1].position == "climax"

    def test_three_chunks_opening_rising_climax(self):
        beats = _make_beats([0.2, 0.3, 0.5, 0.6, 0.8, 0.9, 1.0])
        chunks = [beats[:2], beats[2:4], beats[4:]]
        infos = compute_arc_info(chunks, beats)
        assert infos[0].position == "opening"
        assert infos[1].position == "rising"
        assert infos[2].position == "climax"

    def test_last_chunk_stays_rising_when_peak_not_in_last(self):
        # Continuation mode: never emits `resolution`.
        # Last chunk without peak is `rising`, not `resolution`.
        beats = _make_beats([0.2, 0.5, 1.0, 0.8, 0.4, 0.3])
        chunks = [beats[:3], beats[3:]]
        infos = compute_arc_info(chunks, beats)
        assert infos[0].position == "opening"
        assert infos[1].position == "rising"

    def test_resolution_when_peak_not_in_last_chunk_closed(self):
        beats = _make_beats([0.2, 0.5, 1.0, 0.8, 0.4, 0.3])
        chunks = [beats[:3], beats[3:]]
        infos = compute_arc_info(chunks, beats, close_arc=True)
        assert infos[0].position == "opening"  # has peak but is first chunk
        assert infos[1].position == "resolution"

    def test_last_chunk_stays_climax_when_winds_down_continuation(self):
        # Continuation mode: never emits `climax_and_resolution`.
        # Even if tension winds down within the chunk, position stays `climax`.
        beats = _make_beats([0.2, 0.5, 0.8, 1.0, 0.7, 0.5, 0.3])
        chunks = [beats[:3], beats[3:]]
        infos = compute_arc_info(chunks, beats)
        assert infos[0].position == "opening"
        assert infos[1].position == "climax"

    def test_climax_and_resolution_when_winds_down_closed(self):
        beats = _make_beats([0.2, 0.5, 0.8, 1.0, 0.7, 0.5, 0.3])
        chunks = [beats[:3], beats[3:]]
        infos = compute_arc_info(chunks, beats, close_arc=True)
        assert infos[0].position == "opening"
        # Second chunk has peak (1.0) but winds down to 0.3
        assert infos[1].position == "climax_and_resolution"

    def test_tension_range_computed_correctly(self):
        beats = _make_beats([0.2, 0.8, 0.5])
        chunks = [beats]
        infos = compute_arc_info(chunks, beats)
        assert infos[0].tension_range == (0.2, 0.8)

    def test_has_peak_within_tolerance(self):
        beats = _make_beats([0.2, 0.5, 0.95, 1.0])
        chunks = [beats[:2], beats[2:]]
        infos = compute_arc_info(chunks, beats)
        # 0.95 is within 0.05 of peak 1.0
        assert infos[1].has_peak is True


class TestHasLeakedTags:
    """Tests for leaked block tag detection."""

    def test_clean_blocks(self):
        blocks = [
            {"type": "narrator", "content": "The ship drifted silently."},
            {"type": "character", "name": "Elmer", "content": "Let's go."},
        ]
        assert has_leaked_tags(blocks) is False

    def test_leaked_narrator_tag(self):
        blocks = [
            {"type": "narrator", "content": "Some text <NARRATOR> more text"},
        ]
        assert has_leaked_tags(blocks) is True

    def test_leaked_character_tag(self):
        blocks = [
            {"type": "narrator", "content": 'Text <CHARACTER name="Kaira"> more'},
        ]
        assert has_leaked_tags(blocks) is True

    def test_leaked_closing_tag(self):
        blocks = [
            {"type": "character", "content": "Text </NARRATOR> more"},
        ]
        assert has_leaked_tags(blocks) is True

    def test_empty_blocks(self):
        assert has_leaked_tags([]) is False

    def test_empty_content(self):
        blocks = [{"type": "narrator", "content": ""}]
        assert has_leaked_tags(blocks) is False


class TestPlanMixinConfig:
    """Tests for PlanMixin configuration properties."""

    def test_plan_action_registered(self):
        from talemate.agents.director import DirectorAgent

        actions = DirectorAgent.init_actions()
        assert "plan" in actions
        plan = actions["plan"]
        assert plan.label == "Arc Generation"
        assert plan.container is True
        assert plan.icon == "mdi-movie-open"

    def test_plan_config_keys(self):
        from talemate.agents.director import DirectorAgent

        actions = DirectorAgent.init_actions()
        config = actions["plan"].config
        assert "dialogue_ratio" in config
        assert "expand_chunk_size" in config
        assert "outline_critique" in config
        assert "expand_critique" in config

    def test_plan_config_defaults(self):
        from talemate.agents.director import DirectorAgent

        actions = DirectorAgent.init_actions()
        config = actions["plan"].config
        assert config["dialogue_ratio"].value == 0.4
        assert config["expand_chunk_size"].value == 5
        assert config["outline_critique"].value is True
        assert config["expand_critique"].value is True


class TestChatCreateGenerateArc:
    """Tests for creating arc generation chats."""

    @staticmethod
    def _make_director():
        from talemate.agents.director import DirectorAgent

        director = DirectorAgent.__new__(DirectorAgent)
        director.actions = DirectorAgent.init_actions()
        director._chats = {}
        director._last_active_chat_id = None
        # Mock scene with agent_state for chat persistence
        director.scene = Mock()
        director.scene.agent_state = {"director": {}}
        director.scene.agent_persona.return_value = None
        return director

    def test_create_generate_arc_default_mode(self):
        director = self._make_director()
        chat = director.chat_create_generate_arc("Test instructions", 8)
        assert chat.mode == "generate_arc"
        assert chat.confirm_write_actions is False
        assert len(chat.messages) == 2

    def test_create_generate_arc_expand_mode(self):
        director = self._make_director()
        chat = director.chat_create_generate_arc(
            "Test instructions", 8, mode="generate_arc_expand"
        )
        assert chat.mode == "generate_arc_expand"

    def test_create_generate_arc_instructions_in_message(self):
        director = self._make_director()
        chat = director.chat_create_generate_arc("Write a horror scene", 12)
        user_msg = chat.messages[1]
        assert "Write a horror scene" in user_msg.message
        assert "12 beats" in user_msg.message
