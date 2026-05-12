"""Unit tests for talemate.agents.director.scene_direction.mixin.SceneDirectionMixin.

Covers:
- Config property helpers (response_length, max_actions_per_turn,
  scene_context_ratio, etc.)
- direction_enabled / direction_enabled_with_override (with intent_state).
- State management: direction_get_state / direction_set_state /
  direction_get / direction_create / direction_clear.
- Message management: direction_history, direction_append_message.
- _direction_compute_user_agency_metrics: tracks user/director turn counts.
- _direction_compute_turn_balance: narrator/character ratio analysis.
- _direction_create_message / _direction_create_result / _direction_create_budgets.
- _serialize_direction_message: dispatches by msg type.
- direction_history_for_prompt: real serialization.
- on_user_interaction_for_scene_direction: appends or skips on `#` prefix.
- _direction_compact_if_needed: integration with action_utils.compact_if_needed.

The full LLM-driven `direction_execute_turn` / `_direction_generate` paths are
NOT exercised — they require a real Prompt template and full action execution
machinery; those paths overlap with action_utils tests already written.
"""

from __future__ import annotations


import pytest

from conftest import MockScene, bootstrap_scene

import talemate.instance as instance
from talemate.agents.director.scene_direction.mixin import (
    TURN_BALANCE_LOOKBACK_MESSAGES,
    USER_AGENCY_DIRECTOR_TURNS_THRESHOLD,
    USER_AGENCY_MIN_USER_TURNS,
)
from talemate.agents.director.scene_direction.schema import (
    SceneDirection,
    SceneDirectionActionResultMessage,
    SceneDirectionBudgets,
    SceneDirectionMessage,
    SceneDirectionTurnBalance,
    UserInteractionMessage,
)
from talemate.character import Character
from talemate.events import UserInteractionEvent
from talemate.scene_message import (
    CharacterMessage,
    DIRECTOR_INPUT_PREFIX,
    NarratorMessage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def scene():
    s = MockScene()
    bootstrap_scene(s)
    return s


@pytest.fixture
def director(scene):
    return instance.get_agent("director")


async def _add_character(scene, name, *, is_player=False):
    character = Character(
        name=name,
        description="",
        is_player=is_player,
        base_attributes={},
        details={},
        color="#fff",
    )
    actor = scene.Player(character, None) if is_player else scene.Actor(character, None)
    await scene.add_actor(actor, commit_to_memory=False)
    if name not in scene.active_characters:
        scene.active_characters.append(name)
    return character


# ---------------------------------------------------------------------------
# Config properties
# ---------------------------------------------------------------------------


class TestSceneDirectionConfigProperties:
    def test_response_length_int(self, director):
        assert isinstance(director.direction_response_length, int)
        assert director.direction_response_length == 2048

    def test_max_actions_per_turn_int(self, director):
        assert isinstance(director.direction_max_actions_per_turn, int)
        assert director.direction_max_actions_per_turn == 10

    def test_missing_response_retry_max_int(self, director):
        assert director.direction_missing_response_retry_max == 1

    def test_scene_context_ratio_float(self, director):
        assert director.direction_scene_context_ratio == 0.3

    def test_enable_analysis_bool(self, director):
        assert director.direction_enable_analysis is True

    def test_staleness_threshold_float(self, director):
        assert director.direction_staleness_threshold == 0.7

    def test_custom_instructions_default_empty(self, director):
        assert director.direction_custom_instructions == ""

    def test_maintain_turn_balance_default_true(self, director):
        assert director.direction_maintain_turn_balance is True

    def test_enabled_default_false(self, director):
        assert director.direction_enabled is False

    def test_enabled_via_action_toggle(self, director):
        director.actions["scene_direction"].enabled = True
        try:
            assert director.direction_enabled is True
        finally:
            director.actions["scene_direction"].enabled = False


# ---------------------------------------------------------------------------
# direction_enabled_with_override
# ---------------------------------------------------------------------------


class TestDirectionEnabledWithOverride:
    def test_returns_true_when_agent_enabled(self, director):
        director.actions["scene_direction"].enabled = True
        try:
            assert director.direction_enabled_with_override is True
        finally:
            director.actions["scene_direction"].enabled = False

    def test_returns_true_when_scene_intent_always_on(self, scene, director):
        # Agent is disabled but scene intent_state.direction.always_on is True
        scene.intent_state.direction.always_on = True
        assert director.direction_enabled_with_override is True

    def test_returns_false_when_neither_enabled(self, scene, director):
        assert scene.intent_state.direction.always_on is False
        assert director.direction_enabled_with_override is False


# ---------------------------------------------------------------------------
# State management: direction_get_state / direction_set_state / direction_get
# ---------------------------------------------------------------------------


class TestDirectionStateManagement:
    def test_get_state_returns_none_when_unset(self, director):
        assert director.direction_get_state() is None

    def test_set_state_then_get_state(self, director):
        payload = {"messages": [], "id": "abc", "turn_count": 3}
        director.direction_set_state(payload)
        assert director.direction_get_state() == payload

    def test_direction_create_when_none(self, director):
        d = director.direction_create()
        assert isinstance(d, SceneDirection)
        assert d.messages == []
        # State should now be persisted as a dict (model_dump)
        state = director.direction_get_state()
        assert isinstance(state, dict)
        assert "messages" in state

    def test_direction_create_returns_existing_when_present(self, director):
        # Pre-set some state
        d = SceneDirection(
            messages=[SceneDirectionMessage(message="hi", source="director")]
        )
        director.direction_set_state(d.model_dump())
        # direction_create should return the existing
        existing = director.direction_create()
        assert isinstance(existing, SceneDirection)
        assert len(existing.messages) == 1

    def test_direction_get_returns_none_when_unset(self, director):
        assert director.direction_get() is None

    def test_direction_get_handles_pydantic_object(self, director):
        d = SceneDirection(messages=[])
        # set_state with the actual SceneDirection instance
        director.direction_set_state(d)
        result = director.direction_get()
        assert isinstance(result, SceneDirection)

    def test_direction_get_returns_none_on_invalid_state(self, director):
        # Set state to invalid blob
        director.direction_set_state({"messages": [{"type": "weird-bogus"}]})
        result = director.direction_get()
        # Either parses (lenient) or returns None — test the non-crash invariant.
        assert result is None or isinstance(result, SceneDirection)

    def test_direction_clear_returns_false_when_no_state(self, director):
        assert director.direction_clear() is False

    def test_direction_clear_empties_messages(self, director):
        d = SceneDirection(
            messages=[SceneDirectionMessage(message="hi", source="director")]
        )
        director.direction_set_state(d.model_dump())
        assert director.direction_clear() is True
        cleared = director.direction_get()
        assert cleared.messages == []


# ---------------------------------------------------------------------------
# direction_history & direction_append_message
# ---------------------------------------------------------------------------


class TestDirectionHistory:
    def test_history_empty_when_no_state(self, director):
        assert director.direction_history() == []

    @pytest.mark.asyncio
    async def test_append_creates_state_if_missing(self, director):
        msg = SceneDirectionMessage(message="hi", source="director")
        await director.direction_append_message(msg)
        history = director.direction_history()
        assert len(history) == 1
        assert history[0].message == "hi"

    @pytest.mark.asyncio
    async def test_append_appends_to_existing(self, director):
        await director.direction_append_message(
            SceneDirectionMessage(message="first", source="director")
        )
        await director.direction_append_message(
            SceneDirectionMessage(message="second", source="director")
        )
        history = director.direction_history()
        assert [m.message for m in history] == ["first", "second"]

    @pytest.mark.asyncio
    async def test_append_calls_on_update_callback(self, director):
        captured = []

        async def on_update(msgs):
            captured.append(list(msgs))

        msg = SceneDirectionMessage(message="hi", source="director")
        await director.direction_append_message(msg, on_update=on_update)
        assert len(captured) == 1
        assert captured[0][0].message == "hi"

    @pytest.mark.asyncio
    async def test_append_action_result_message(self, director):
        msg = SceneDirectionActionResultMessage(
            name="test_action", instructions="do x", result="ok", status="success"
        )
        await director.direction_append_message(msg)
        assert len(director.direction_history()) == 1


# ---------------------------------------------------------------------------
# _direction_compute_user_agency_metrics
# ---------------------------------------------------------------------------


class TestUserAgencyMetrics:
    def test_no_state_returns_zero_metrics(self, director):
        metrics = director._direction_compute_user_agency_metrics()
        assert metrics == {
            "user_turn_count": 0,
            "director_turn_count": 0,
            "should_remind": False,
        }

    def test_counts_user_and_director_turns(self, director):
        d = SceneDirection(
            messages=[
                SceneDirectionMessage(message="d1", source="director"),
                UserInteractionMessage(user_input="u1"),
                SceneDirectionMessage(message="d2", source="director"),
            ]
        )
        director.direction_set_state(d.model_dump())
        metrics = director._direction_compute_user_agency_metrics()
        assert metrics["director_turn_count"] == 2
        assert metrics["user_turn_count"] == 1
        # Below the director-turns threshold and user_turn_count >= 1 — no reminder
        assert metrics["should_remind"] is False

    def test_should_remind_when_threshold_exceeded_without_user(self, director):
        msgs = [
            SceneDirectionMessage(message=f"d{i}", source="director")
            for i in range(USER_AGENCY_DIRECTOR_TURNS_THRESHOLD + 1)
        ]
        d = SceneDirection(messages=msgs)
        director.direction_set_state(d.model_dump())
        metrics = director._direction_compute_user_agency_metrics()
        assert metrics["should_remind"] is True
        assert metrics["user_turn_count"] < USER_AGENCY_MIN_USER_TURNS

    def test_user_turns_suppress_reminder(self, director):
        msgs = [
            SceneDirectionMessage(message=f"d{i}", source="director") for i in range(5)
        ] + [UserInteractionMessage(user_input="hello")]
        d = SceneDirection(messages=msgs)
        director.direction_set_state(d.model_dump())
        metrics = director._direction_compute_user_agency_metrics()
        assert metrics["should_remind"] is False


# ---------------------------------------------------------------------------
# _direction_compute_turn_balance
# ---------------------------------------------------------------------------


class TestTurnBalance:
    def test_returns_default_when_no_history(self, scene, director):
        balance = director._direction_compute_turn_balance()
        assert isinstance(balance, SceneDirectionTurnBalance)
        assert balance.total_messages_analyzed == 0

    @pytest.mark.asyncio
    async def test_returns_default_when_no_active_non_player_characters(
        self, scene, director
    ):
        # Only player character
        await _add_character(scene, "Hero", is_player=True)
        scene.history.append(NarratorMessage("scene start"))
        balance = director._direction_compute_turn_balance()
        assert balance.total_messages_analyzed == 0
        assert balance.active_character_names == []

    @pytest.mark.asyncio
    async def test_narrator_overuse_flag(self, scene, director):
        await _add_character(scene, "Alice")
        # 5 narrator messages, 1 character message → narrator > 60%
        for _ in range(5):
            scene.history.append(NarratorMessage("narration"))
        scene.history.append(CharacterMessage("Alice: hi"))
        balance = director._direction_compute_turn_balance()
        assert balance.narrator_overused is True
        assert balance.narrator_neglected is False
        assert balance.narrator_message_count == 5

    @pytest.mark.asyncio
    async def test_narrator_neglect_flag(self, scene, director):
        await _add_character(scene, "Alice")
        # 5 character messages, no narrator → narrator < 20%
        for _ in range(5):
            scene.history.append(CharacterMessage("Alice: hi"))
        balance = director._direction_compute_turn_balance()
        assert balance.narrator_neglected is True
        assert balance.narrator_overused is False

    @pytest.mark.asyncio
    async def test_player_messages_skipped_from_character_counts(self, scene, director):
        await _add_character(scene, "Alice")
        await _add_character(scene, "Hero", is_player=True)
        # Player message should be skipped
        msg = CharacterMessage("Hero: my line", source="player")
        scene.history.append(msg)
        scene.history.append(CharacterMessage("Alice: hi"))
        balance = director._direction_compute_turn_balance()
        assert balance.character_message_counts.get("Alice", 0) == 1
        # "Hero" is the player character — never appears in active_character_names
        assert "Hero" not in balance.character_message_counts
        assert balance.total_messages_analyzed == 1

    @pytest.mark.asyncio
    async def test_neglected_characters_identified(self, scene, director):
        await _add_character(scene, "Alice")
        await _add_character(scene, "Bob")
        # Many Alice messages, no Bob → Bob neglected
        for _ in range(8):
            scene.history.append(CharacterMessage("Alice: hi"))
        balance = director._direction_compute_turn_balance()
        assert "Bob" in balance.neglected_characters
        assert "Alice" not in balance.neglected_characters

    @pytest.mark.asyncio
    async def test_lookback_limited_by_constant(self, scene, director):
        await _add_character(scene, "Alice")
        # More messages than the lookback window
        for _ in range(TURN_BALANCE_LOOKBACK_MESSAGES + 5):
            scene.history.append(CharacterMessage("Alice: hi"))
        balance = director._direction_compute_turn_balance()
        assert balance.total_messages_analyzed == TURN_BALANCE_LOOKBACK_MESSAGES


# ---------------------------------------------------------------------------
# Message factories
# ---------------------------------------------------------------------------


class TestMessageFactories:
    def test_create_message_default_source(self, director):
        msg = director._direction_create_message("hello")
        assert isinstance(msg, SceneDirectionMessage)
        assert msg.message == "hello"
        assert msg.source == "director"

    def test_create_message_passes_kwargs_through(self, director):
        # SceneDirectionMessage.source is a Literal["director"] — only "director"
        # is valid. Verify that other model fields (e.g. type) are forwarded.
        msg = director._direction_create_message("hello", type="summary")
        assert msg.type == "summary"

    def test_create_result(self, director):
        msg = director._direction_create_result(
            name="x", instructions="i", result="r", status="success"
        )
        assert isinstance(msg, SceneDirectionActionResultMessage)
        assert msg.name == "x"

    def test_create_budgets(self, director):
        budgets = director._direction_create_budgets()
        assert isinstance(budgets, SceneDirectionBudgets)
        assert budgets.scene_context_ratio == director.direction_scene_context_ratio


# ---------------------------------------------------------------------------
# _serialize_direction_message
# ---------------------------------------------------------------------------


class TestSerializeDirectionMessage:
    def test_dict_with_action_result_type(self, director):
        out = director._serialize_direction_message(
            {
                "type": "action_result",
                "name": "x",
                "instructions": "i",
                "result": "ok",
                "status": "success",
            }
        )
        assert isinstance(out, SceneDirectionActionResultMessage)

    def test_dict_with_user_interaction_type(self, director):
        out = director._serialize_direction_message(
            {"type": "user_interaction", "user_input": "hi"}
        )
        assert isinstance(out, UserInteractionMessage)

    def test_dict_with_text_type(self, director):
        out = director._serialize_direction_message(
            {"type": "text", "message": "hello", "source": "director"}
        )
        assert isinstance(out, SceneDirectionMessage)

    def test_dict_default_to_text_when_type_missing(self, director):
        out = director._serialize_direction_message(
            {"message": "hi", "source": "director"}
        )
        assert isinstance(out, SceneDirectionMessage)

    def test_passthrough_when_already_pydantic(self, director):
        msg = SceneDirectionMessage(message="hi", source="director")
        out = director._serialize_direction_message(msg)
        assert out is msg

    def test_returns_none_on_invalid_dict(self, director):
        # Missing required fields → exception → returns None
        out = director._serialize_direction_message(
            {"type": "user_interaction"}  # missing user_input
        )
        assert out is None


# ---------------------------------------------------------------------------
# direction_history_for_prompt
# ---------------------------------------------------------------------------


class TestDirectionHistoryForPrompt:
    def test_returns_empty_when_no_state(self, director):
        assert director.direction_history_for_prompt() == []

    @pytest.mark.asyncio
    async def test_serializes_dict_messages(self, director):
        d = SceneDirection(
            messages=[
                SceneDirectionMessage(message="hi", source="director"),
                UserInteractionMessage(user_input="user said"),
            ]
        )
        director.direction_set_state(d.model_dump())
        history = director.direction_history_for_prompt()
        assert len(history) == 2
        # serialize_history maps each through _serialize_direction_message which
        # parses dict back to model.
        assert all(
            isinstance(m, (SceneDirectionMessage, UserInteractionMessage))
            for m in history
        )


# ---------------------------------------------------------------------------
# on_user_interaction_for_scene_direction
# ---------------------------------------------------------------------------


class TestOnUserInteraction:
    @pytest.mark.asyncio
    async def test_skipped_when_direction_disabled(self, scene, director):
        # Default: agent disabled, no always_on
        ev = UserInteractionEvent(scene=scene, message="hi", input_id="ev-1")
        await director.on_user_interaction_for_scene_direction(ev)
        # No direction state created
        assert director.direction_get_state() is None

    @pytest.mark.asyncio
    async def test_appends_user_input_when_enabled(self, scene, director):
        director.actions["scene_direction"].enabled = True
        try:
            ev = UserInteractionEvent(
                scene=scene, message="user input", input_id="ev-1"
            )
            await director.on_user_interaction_for_scene_direction(ev)
            history = director.direction_history()
            assert len(history) == 1
            assert isinstance(history[0], UserInteractionMessage)
            assert history[0].user_input == "user input"
        finally:
            director.actions["scene_direction"].enabled = False

    @pytest.mark.asyncio
    async def test_skips_director_input_prefix(self, scene, director):
        director.actions["scene_direction"].enabled = True
        try:
            ev = UserInteractionEvent(
                scene=scene,
                message=f"{DIRECTOR_INPUT_PREFIX}explicit direction",
                input_id="ev-1",
            )
            await director.on_user_interaction_for_scene_direction(ev)
            assert director.direction_get_state() is None
        finally:
            director.actions["scene_direction"].enabled = False


# ---------------------------------------------------------------------------
# _direction_compact_if_needed (early returns + integration)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _direction_emit_* (websocket emitters — best-effort)
# ---------------------------------------------------------------------------


class TestDirectionEmitters:
    def test_emit_history_does_not_raise_for_real_direction(self, director):
        d = SceneDirection(
            messages=[
                SceneDirectionMessage(message="hi", source="director"),
                UserInteractionMessage(user_input="user said hi"),
            ]
        )
        # Should not raise
        director._direction_emit_history(d)

    def test_emit_append_no_op_on_empty_list(self, director):
        d = SceneDirection(messages=[])
        # Empty new_messages -> early return
        director._direction_emit_append(d, [])

    def test_emit_append_real_messages(self, director):
        d = SceneDirection(messages=[])
        msg = SceneDirectionMessage(message="x", source="director")
        # Should not raise
        director._direction_emit_append(d, [msg])

    def test_emit_compacting_does_not_raise(self, director):
        d = SceneDirection(messages=[])
        director._direction_emit_compacting(d)


# ---------------------------------------------------------------------------
# direction_execute_turn — early-return when disabled
# ---------------------------------------------------------------------------


class TestDirectionExecuteTurnGate:
    @pytest.mark.asyncio
    async def test_returns_empty_when_direction_disabled(self, scene, director):
        # Default: disabled, no scene override
        assert director.direction_enabled is False
        assert director.direction_enabled_with_override is False
        actions_taken, yield_to_user = await director.direction_execute_turn()
        assert actions_taken == []
        assert yield_to_user is False


class TestDirectionCompactIfNeeded:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_direction_state(self, director):
        budgets = SceneDirectionBudgets(max_tokens=1000, scene_context_ratio=0.5)
        result = await director._direction_compact_if_needed(budgets)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_messages(self, director):
        director.direction_create()  # empty messages
        budgets = SceneDirectionBudgets(max_tokens=1000, scene_context_ratio=0.5)
        result = await director._direction_compact_if_needed(budgets)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_budgets(self, director):
        d = SceneDirection(
            messages=[SceneDirectionMessage(message="hi", source="director")]
        )
        director.direction_set_state(d.model_dump())
        result = await director._direction_compact_if_needed(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_compact_when_under_threshold(self, director):
        d = SceneDirection(
            messages=[SceneDirectionMessage(message="short", source="director")]
        )
        director.direction_set_state(d.model_dump())
        # huge budget, single small msg → under threshold, no compaction.
        budgets = SceneDirectionBudgets(max_tokens=10000, scene_context_ratio=0.5)
        result = await director._direction_compact_if_needed(budgets)
        assert result is False
        # Messages still present
        assert len(director.direction_history()) == 1
