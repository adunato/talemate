"""Unit tests for talemate.regenerate.

Tests target the public entry points (regenerate_target_message,
ensure_regenerate_allowed, regenerate_message, regenerate) using a real
Scene instance and real SceneMessage subclasses. Network/UI side effects
(emit, async_signals.send) are silenced by stubbing the imported names
inside the regenerate module.

Skipped path:
- regenerate_character_message: depends on talemate.tale_mate.Actor and
  conversation agent's `converse(actor, instruction=...)` returning a list
  of messages. We test the wrapper (regenerate_message) instead, which
  routes character messages through the real ConversationAgent (with its
  `converse` method stubbed at the instance level — peripheral RPC).
"""

from __future__ import annotations

import pytest

import talemate.instance as instance
import talemate.regenerate as regenerate_mod
from talemate.agents.conversation import ConversationAgent
from talemate.regenerate import (
    ensure_regenerate_allowed,
    regenerate,
    regenerate_message,
    regenerate_target_message,
)
from talemate.scene_message import (
    CharacterMessage,
    ContextInvestigationMessage,
    NarratorMessage,
    ReinforcementMessage,
)
from talemate.tale_mate import Actor, Scene


# ---------------------------------------------------------------------------
# Module-level stubs for emit + signals — keep tests deterministic
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _silence_emit_and_signals(monkeypatch):
    """Stub emit + async_signals.get(...) so regenerate doesn't touch the bus.

    Returns the list of emit-call records: list of {"name": str, "kwargs": dict}.
    """
    emitted: list[dict] = []

    def fake_emit(name, *args, **kwargs):
        emitted.append({"name": name, "args": args, "kwargs": kwargs})

    monkeypatch.setattr(regenerate_mod, "emit", fake_emit)

    class _Signal:
        async def send(self, *args, **kwargs):
            pass

    class _Signals:
        def get(self, name):
            return _Signal()

    monkeypatch.setattr(regenerate_mod, "async_signals", _Signals())

    return emitted


# ---------------------------------------------------------------------------
# Scene fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def scene():
    """Real Scene instance with no actors/history. Tests configure as needed."""
    s = Scene()
    return s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_actor(character) -> Actor:
    """Build a real `Actor` linked to `character` (no agent needed for these tests)."""
    return Actor(character=character, agent=None)


def _make_character_message(
    name: str = "Alice", body: str = "Hello"
) -> CharacterMessage:
    """Build a real CharacterMessage with proper "Name: body" format."""
    return CharacterMessage(message=f"{name}: {body}")


# ---------------------------------------------------------------------------
# regenerate_target_message
# ---------------------------------------------------------------------------


class TestRegenerateTargetMessage:
    def test_returns_none_for_empty_history(self, scene):
        assert regenerate_target_message(scene) is None

    def test_returns_last_message_by_default(self, scene):
        m1 = NarratorMessage(message="first")
        m2 = NarratorMessage(message="second")
        scene.history = [m1, m2]
        assert regenerate_target_message(scene) is m2

    def test_skips_trailing_reinforcement_messages(self, scene):
        narration = NarratorMessage(message="real target")
        r1 = ReinforcementMessage(message="reinforce 1")
        r2 = ReinforcementMessage(message="reinforce 2")
        scene.history = [narration, r1, r2]
        # Default idx=-1 → walk back over reinforcements → narration
        assert regenerate_target_message(scene) is narration

    def test_returns_none_when_only_reinforcements_present(self, scene):
        # When all messages are reinforcements, walk-back exhausts history → None
        scene.history = [ReinforcementMessage(message="r1")]
        assert regenerate_target_message(scene) is None

    def test_specific_index_resolution(self, scene):
        m1 = NarratorMessage(message="first")
        m2 = NarratorMessage(message="second")
        m3 = NarratorMessage(message="third")
        scene.history = [m1, m2, m3]
        assert regenerate_target_message(scene, idx=0) is m1
        assert regenerate_target_message(scene, idx=1) is m2

    def test_handles_out_of_bounds_index(self, scene):
        scene.history = [NarratorMessage(message="x")]
        assert regenerate_target_message(scene, idx=99) is None


# ---------------------------------------------------------------------------
# ensure_regenerate_allowed
# ---------------------------------------------------------------------------


class TestEnsureRegenerateAllowed:
    def test_allows_when_history_is_empty(self, scene):
        allowed, reason = ensure_regenerate_allowed(scene)
        assert allowed is True
        assert reason is None

    def test_allows_for_non_character_messages(self, scene):
        scene.history = [NarratorMessage(message="x")]
        allowed, reason = ensure_regenerate_allowed(scene)
        assert allowed is True
        assert reason is None

    def test_blocks_when_character_not_found(self, scene):
        scene.history = [_make_character_message("Bob")]
        # No characters in scene → get_character returns None → blocked.
        allowed, reason = ensure_regenerate_allowed(scene)
        assert allowed is False
        assert "not found" in reason.lower()

    def test_blocks_when_character_inactive(self, scene):
        # Stub get_character + active_characters: character exists but inactive.
        from talemate.character import Character

        char = Character(name="Bob")
        scene.character_data["Bob"] = char
        # Note: scene.active_characters is empty list — Bob is inactive.
        scene.history = [_make_character_message("Bob")]
        allowed, reason = ensure_regenerate_allowed(scene)
        assert allowed is False
        assert "inactive" in reason.lower()

    def test_blocks_when_character_has_no_actor(self, scene, monkeypatch):
        """An active character without an actor cannot be regenerated."""
        from talemate.character import Character

        char = Character(name="Bob")  # actor=None by default
        # Bypass scene.get_character's strict lookup: we can't easily attach
        # an actor in a real Scene without going through Actor wiring. Patch
        # get_character on this instance.
        scene.history = [_make_character_message("Bob")]
        monkeypatch.setattr(scene, "get_character", lambda name: char)
        scene.active_characters = ["Bob"]

        allowed, reason = ensure_regenerate_allowed(scene)
        assert allowed is False
        assert "no active actor" in reason.lower()

    def test_allows_when_character_active_and_has_actor(self, scene, monkeypatch):
        from talemate.character import Character

        char = Character(name="Bob")
        _make_actor(char)
        monkeypatch.setattr(scene, "get_character", lambda name: char)
        scene.active_characters = ["Bob"]
        scene.history = [_make_character_message("Bob")]

        allowed, reason = ensure_regenerate_allowed(scene)
        assert allowed is True
        assert reason is None


# ---------------------------------------------------------------------------
# Helpers for regenerate / regenerate_message tests
# ---------------------------------------------------------------------------


def _conversation_agent_with_canned(messages):
    """Build a real `ConversationAgent` with `converse` stubbed at the instance level.

    Returns the agent. `agent.calls` is the list of `{"actor", "instruction"}`
    records captured per `converse` invocation. `converse` is a peripheral RPC
    method here — the unit under test is `regenerate_message`, which routes
    character messages through this agent. Stubbing the method on a real
    agent (instead of redefining the whole class) preserves contract checks
    on every other ConversationAgent attribute.
    """
    agent = ConversationAgent()
    agent.calls = []

    async def _converse(actor, instruction=None):
        agent.calls.append({"actor": actor, "instruction": instruction})
        return list(messages)

    agent.converse = _converse
    return agent


@pytest.fixture
def register_agent():
    """Register / restore agents in instance.AGENTS."""
    original = instance.AGENTS.copy()

    def _set(name, agent):
        instance.AGENTS[name] = agent

    yield _set
    instance.AGENTS.clear()
    instance.AGENTS.update(original)


# ---------------------------------------------------------------------------
# regenerate_message — non-character path (most code paths)
# ---------------------------------------------------------------------------


class TestRegenerateMessageNonCharacter:
    @pytest.mark.asyncio
    async def test_calls_agent_function_and_pushes_new_message(
        self, scene, register_agent
    ):
        new_text = "fresh narration"

        class _NarratorAgent:
            async def progress_story(self, **kwargs):
                return NarratorMessage(message=new_text)

        register_agent("narrator", _NarratorAgent())

        original = NarratorMessage(message="old narration")
        original.meta = {
            "agent": "narrator",
            "function": "progress_story",
            "arguments": {"narrative_direction": "forward"},
        }

        result = await regenerate_message(original, scene)

        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], NarratorMessage)
        assert result[0].message == new_text
        # The new message should have been pushed onto history.
        assert result[0] in scene.history

    @pytest.mark.asyncio
    async def test_string_return_wrapped_in_message_class(self, scene, register_agent):
        class _NarratorAgent:
            async def progress_story(self, **kwargs):
                # Return raw string — function should wrap it in NarratorMessage
                # using the original message's __class__.
                return "raw text"

        register_agent("narrator", _NarratorAgent())

        original = NarratorMessage(message="old")
        original.meta = {
            "agent": "narrator",
            "function": "progress_story",
            "arguments": {},
        }

        result = await regenerate_message(original, scene)
        assert isinstance(result[0], NarratorMessage)
        assert result[0].message == "raw text"
        # meta is copied from original
        assert result[0].meta == original.meta

    @pytest.mark.asyncio
    async def test_context_investigation_preserves_sub_type(
        self, scene, register_agent
    ):
        class _Investigator:
            async def investigate(self, **kwargs):
                return ContextInvestigationMessage(message="result text")

        register_agent("investigator", _Investigator())

        original = ContextInvestigationMessage(message="prior")
        original.sub_type = "query"
        original.meta = {
            "agent": "investigator",
            "function": "investigate",
            "arguments": {},
        }

        result = await regenerate_message(original, scene)
        assert result and isinstance(result[0], ContextInvestigationMessage)
        # sub_type carries from the original.
        assert result[0].sub_type == "query"

    @pytest.mark.asyncio
    async def test_returns_none_when_agent_function_returns_none(
        self, scene, register_agent
    ):
        class _NarratorAgent:
            async def progress_story(self, **kwargs):
                return None

        register_agent("narrator", _NarratorAgent())

        original = NarratorMessage(message="old")
        original.meta = {
            "agent": "narrator",
            "function": "progress_story",
            "arguments": {},
        }

        result = await regenerate_message(original, scene)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_function_not_found_on_agent(
        self, scene, register_agent
    ):
        class _MinimalAgent:
            pass

        register_agent("narrator", _MinimalAgent())

        original = NarratorMessage(message="old")
        original.meta = {
            "agent": "narrator",
            "function": "does_not_exist",
            "arguments": {},
        }

        result = await regenerate_message(original, scene)
        assert result is None

    @pytest.mark.asyncio
    async def test_string_character_argument_resolved_to_object(
        self, scene, register_agent, monkeypatch
    ):
        from talemate.character import Character

        captured = {}

        class _NarratorAgent:
            async def narrate_character(self, **kwargs):
                captured.update(kwargs)
                return NarratorMessage(message="ok")

        register_agent("narrator", _NarratorAgent())

        bob = Character(name="Bob")
        monkeypatch.setattr(
            scene,
            "get_character",
            lambda name: bob if name == "Bob" else None,
        )

        original = NarratorMessage(message="old")
        original.meta = {
            "agent": "narrator",
            "function": "narrate_character",
            "arguments": {"character": "Bob"},
        }

        await regenerate_message(original, scene)
        # Agent received a Character object — not the original string.
        assert captured.get("character") is bob


# ---------------------------------------------------------------------------
# regenerate_message — character path (delegates to conversation agent)
# ---------------------------------------------------------------------------


class TestRegenerateCharacterMessage:
    @pytest.mark.asyncio
    async def test_returns_none_when_character_not_in_scene(
        self, scene, register_agent
    ):
        """When the named character is missing, regenerate_message returns
        None and the conversation agent is never called. The outer
        `regenerate()` handles None by restoring the original message; direct
        callers get the same contract.
        """
        conv = _conversation_agent_with_canned([])
        register_agent("conversation", conv)
        msg = _make_character_message("Ghost")

        result = await regenerate_message(msg, scene)

        assert result is None
        assert conv.calls == []

    @pytest.mark.asyncio
    async def test_static_player_message_returns_none(
        self, scene, register_agent, monkeypatch
    ):
        """Static player messages (source='player' without from_choice)
        cannot be regenerated. regenerate_message returns None instead of
        crashing.
        """
        from talemate.character import Character

        char = Character(name="Alice")
        _make_actor(char)
        monkeypatch.setattr(scene, "get_character", lambda name: char)

        # source=player and no from_choice → "static user message" path.
        msg = _make_character_message("Alice", "static line")
        msg.source = "player"
        msg.from_choice = None

        conv = _conversation_agent_with_canned([])
        register_agent("conversation", conv)

        result = await regenerate_message(msg, scene)

        assert result is None
        # The conversation agent's `converse` was not invoked.
        assert conv.calls == []

    @pytest.mark.asyncio
    async def test_regenerates_via_conversation_agent_when_from_choice_set(
        self, scene, register_agent, monkeypatch
    ):
        from talemate.character import Character

        char = Character(name="Alice")
        _make_actor(char)
        monkeypatch.setattr(scene, "get_character", lambda name: char)

        new_msg = _make_character_message("Alice", "regenerated dialogue")
        conv = _conversation_agent_with_canned([new_msg])
        register_agent("conversation", conv)

        original = _make_character_message("Alice", "original dialogue")
        original.source = "player"
        original.from_choice = "Be cheerful"

        result = await regenerate_message(original, scene)

        # Conversation agent was called with the actor and from_choice.
        assert len(conv.calls) == 1
        assert conv.calls[0]["actor"] is char.actor
        assert conv.calls[0]["instruction"] == "Be cheerful"
        # The new message was pushed to history.
        assert new_msg in scene.history
        assert result == [new_msg]


# ---------------------------------------------------------------------------
# regenerate (top-level orchestrator)
# ---------------------------------------------------------------------------


class TestRegenerate:
    @pytest.mark.asyncio
    async def test_returns_none_when_history_empty(self, scene):
        # Empty history → IndexError caught → returns None
        result = await regenerate(scene)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_static_player_message(
        self, scene, register_agent
    ):
        from talemate.character import Character

        char = Character(name="Alice")
        scene.character_data["Alice"] = char
        scene.active_characters = ["Alice"]
        msg = _make_character_message("Alice", "hello")
        msg.source = "player"  # static player msg with no from_choice
        scene.history = [msg]

        result = await regenerate(scene)
        # Static player messages are not regeneratable.
        assert result == []
        # History is preserved.
        assert msg in scene.history

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_unsupported_message_type(self, scene):
        # Reinforcement-only history: walk-back consumes them all → message ends
        # up at index -1 of an empty/popped state → control flow path varies.
        # Use a simpler unsupported type instead: a plain SceneMessage subclass
        # not in the supported tuple. We use TimePassageMessage.
        from talemate.scene_message import TimePassageMessage

        msg = TimePassageMessage(message="some time later")
        scene.history = [msg]

        result = await regenerate(scene)
        assert result == []
        # Because the message wasn't supported, history is left untouched.
        assert msg in scene.history

    @pytest.mark.asyncio
    async def test_pops_reinforcements_and_regenerates_underlying_message(
        self, scene, register_agent
    ):
        """Trailing reinforcements are popped, primary message regenerated, then re-added."""
        new_text = "regen narration"

        class _NarratorAgent:
            async def progress_story(self, **kwargs):
                return NarratorMessage(message=new_text)

        register_agent("narrator", _NarratorAgent())

        narration = NarratorMessage(message="old")
        narration.meta = {
            "agent": "narrator",
            "function": "progress_story",
            "arguments": {},
        }
        reinforce = ReinforcementMessage(message="reinforced")
        reinforce.meta = {
            "agent": "world_state",
            "function": "update_reinforcement",
            "arguments": {},
        }
        scene.history = [narration, reinforce]

        # The reinforcement message will be regenerated via its agent. Register a
        # world_state agent that returns a new reinforcement message.
        class _WorldStateAgent:
            async def update_reinforcement(self, **kwargs):
                return ReinforcementMessage(message="new reinforced")

        register_agent("world_state", _WorldStateAgent())

        result = await regenerate(scene)

        assert isinstance(result, list)
        # Result should contain the new narrator message + new reinforcement.
        assert any(m.message == new_text for m in result)

    @pytest.mark.asyncio
    async def test_restores_original_when_regeneration_returns_no_messages(
        self, scene, register_agent
    ):
        """If regenerate_message returns None, the original is restored."""

        class _NarratorAgent:
            async def progress_story(self, **kwargs):
                return None  # triggers restoration path

        register_agent("narrator", _NarratorAgent())

        original = NarratorMessage(message="original text")
        original.meta = {
            "agent": "narrator",
            "function": "progress_story",
            "arguments": {},
        }
        scene.history = [original]

        result = await regenerate(scene)

        # Original message is restored to history.
        assert original in scene.history
        # No regenerated messages returned.
        assert result == []

    @pytest.mark.asyncio
    async def test_restores_original_and_reinforcements_on_failure(
        self, scene, register_agent
    ):
        """If regen fails, both the primary message AND reinforcements are restored."""

        class _NarratorAgent:
            async def progress_story(self, **kwargs):
                return None

        register_agent("narrator", _NarratorAgent())

        original = NarratorMessage(message="primary")
        original.meta = {
            "agent": "narrator",
            "function": "progress_story",
            "arguments": {},
        }
        reinforce = ReinforcementMessage(message="reinforce")
        scene.history = [original, reinforce]

        await regenerate(scene)

        # Both messages are present in history again.
        assert original in scene.history
        assert reinforce in scene.history

    @pytest.mark.asyncio
    async def test_handles_exception_during_regeneration_by_restoring(
        self, scene, register_agent, _silence_emit_and_signals
    ):
        class _Boom:
            async def progress_story(self, **kwargs):
                raise RuntimeError("boom")

        register_agent("narrator", _Boom())

        original = NarratorMessage(message="primary")
        original.meta = {
            "agent": "narrator",
            "function": "progress_story",
            "arguments": {},
        }
        scene.history = [original]

        result = await regenerate(scene)

        # Original is restored despite the exception.
        assert original in scene.history
        assert result == []
        # An error status was emitted.
        names = [e["name"] for e in _silence_emit_and_signals]
        assert "status" in names
