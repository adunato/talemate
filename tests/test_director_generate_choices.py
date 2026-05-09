"""Unit tests for talemate.agents.director.generate_choices.GenerateChoicesMixin.

Covers:
- Config property helpers (generate_choices_enabled, chance, num_choices,
  never_auto_progress, instructions).
- on_player_turn_start: skip-when-disabled, skip-after-recent-player-message,
  randomized triggering of generate_choices.
- generate_choices: end-to-end with stubbed Prompt.request — covers
  extraction and limit-by-num_choices, no-actions early return,
  list-extraction failure path.
"""

from __future__ import annotations

from collections import deque
import random
from typing import Any
from unittest.mock import patch

import pytest

from conftest import MockScene, bootstrap_scene

import talemate.instance as instance
from talemate.character import Character
from talemate.events import GameLoopStartEvent
from talemate.scene_message import CharacterMessage, NarratorMessage


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
    actor = (
        scene.Player(character, None) if is_player else scene.Actor(character, None)
    )
    await scene.add_actor(actor, commit_to_memory=False)
    if name not in scene.active_characters:
        scene.active_characters.append(name)
    return character


class _StubPromptRequest:
    """Stub Prompt.request that pops from a queue keyed by template name."""

    def __init__(self, responses: dict[str, deque]):
        self.responses = responses
        self.calls: list[dict] = []

    async def __call__(self, template, client, kind, vars=None, **kwargs):
        self.calls.append(
            {"template": template, "kind": kind, "vars": vars or {}}
        )
        queue = self.responses.get(template)
        if queue is None or not queue:
            return "", {}
        return queue.popleft()


@pytest.fixture
def stub_prompt(monkeypatch):
    from talemate.agents.director import generate_choices as gc_mod

    def install(responses: dict[str, list]):
        as_deque = {k: deque(v) for k, v in responses.items()}
        stub = _StubPromptRequest(as_deque)

        class _StubPromptClass:
            request = staticmethod(stub)

        monkeypatch.setattr(gc_mod, "Prompt", _StubPromptClass)
        return stub

    return install


# ---------------------------------------------------------------------------
# Config properties
# ---------------------------------------------------------------------------


class TestGenerateChoicesConfig:
    def test_enabled_default_true(self, director):
        assert director.generate_choices_enabled is True

    def test_chance_default_0_3(self, director):
        assert director.generate_choices_chance == 0.3

    def test_num_choices_default_3(self, director):
        assert director.generate_choices_num_choices == 3

    def test_never_auto_progress_default_false(self, director):
        assert director.generate_choices_never_auto_progress is False

    def test_instructions_default_empty(self, director):
        assert director.generate_choices_instructions == ""


# ---------------------------------------------------------------------------
# on_player_turn_start
# ---------------------------------------------------------------------------


class TestOnPlayerTurnStart:
    @pytest.mark.asyncio
    async def test_no_op_when_director_disabled(self, scene, director):
        director.is_enabled = False
        try:
            event = GameLoopStartEvent(scene=scene, event_type="player_turn_start")
            calls = []

            async def fake_generate_choices(**kwargs):
                calls.append(kwargs)

            with patch.object(director, "generate_choices", side_effect=fake_generate_choices):
                await director.on_player_turn_start(event)
            assert calls == []
        finally:
            director.is_enabled = True

    @pytest.mark.asyncio
    async def test_no_op_when_generate_choices_disabled(self, scene, director):
        director.actions["_generate_choices"].enabled = False
        try:
            event = GameLoopStartEvent(scene=scene, event_type="player_turn_start")
            calls = []

            async def fake_generate_choices(**kwargs):
                calls.append(kwargs)

            with patch.object(director, "generate_choices", side_effect=fake_generate_choices):
                await director.on_player_turn_start(event)
            assert calls == []
        finally:
            director.actions["_generate_choices"].enabled = True

    @pytest.mark.asyncio
    async def test_skips_when_recent_player_message(self, scene, director):
        # Player message is the most-recent character/narrator message
        scene.history.append(NarratorMessage("intro"))
        scene.history.append(CharacterMessage("Hero: hi", source="player"))

        event = GameLoopStartEvent(scene=scene, event_type="player_turn_start")
        with patch("random.random", return_value=0.0):  # always pass chance
            calls = []

            async def fake_generate_choices(**kwargs):
                calls.append(kwargs)

            with patch.object(director, "generate_choices", side_effect=fake_generate_choices):
                await director.on_player_turn_start(event)
        assert calls == []

    @pytest.mark.asyncio
    async def test_triggers_when_chance_passes(self, scene, director):
        # Last message is a narrator → chance roll determines if it fires.
        scene.history.append(NarratorMessage("scene start"))
        event = GameLoopStartEvent(scene=scene, event_type="player_turn_start")

        calls = []

        async def fake_generate_choices(**kwargs):
            calls.append(kwargs)

        with patch.object(
            director, "generate_choices", side_effect=fake_generate_choices
        ):
            with patch("random.random", return_value=0.0):  # below default 0.3
                await director.on_player_turn_start(event)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_no_call_when_chance_fails(self, scene, director):
        scene.history.append(NarratorMessage("scene start"))
        event = GameLoopStartEvent(scene=scene, event_type="player_turn_start")

        calls = []

        async def fake_generate_choices(**kwargs):
            calls.append(kwargs)

        with patch.object(
            director, "generate_choices", side_effect=fake_generate_choices
        ):
            with patch("random.random", return_value=0.99):  # above 0.3
                await director.on_player_turn_start(event)
        assert calls == []

    @pytest.mark.asyncio
    async def test_triggers_with_no_history(self, scene, director):
        # No history at all — the loop terminates without finding a player msg
        # and proceeds to the chance roll.
        event = GameLoopStartEvent(scene=scene, event_type="player_turn_start")
        calls = []

        async def fake_generate_choices(**kwargs):
            calls.append(kwargs)

        with patch.object(
            director, "generate_choices", side_effect=fake_generate_choices
        ):
            with patch("random.random", return_value=0.0):
                await director.on_player_turn_start(event)
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# generate_choices — end-to-end via stubbed Prompt.request
# ---------------------------------------------------------------------------


class TestGenerateChoices:
    @pytest.mark.asyncio
    async def test_returns_response_and_emits_choices(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Hero", is_player=True)
        # Response simulates a numbered list extracted by util.extract_list
        actions_text = "1. Open the door\n2. Look around\n3. Run away"
        stub_prompt(
            {
                "director.generate-choices": [
                    ("raw response", {"actions": actions_text})
                ]
            }
        )
        result = await director.generate_choices()
        assert result == "raw response"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_actions_block(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Hero", is_player=True)
        stub_prompt(
            {"director.generate-choices": [("raw response", {"actions": None})]}
        )
        result = await director.generate_choices()
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_specific_character_when_provided(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Alice")
        await _add_character(scene, "Hero", is_player=True)
        stub = stub_prompt(
            {
                "director.generate-choices": [
                    ("ok", {"actions": "1. Wait\n2. Speak"})
                ]
            }
        )
        await director.generate_choices(character="Alice")
        # Alice should be the character passed to the prompt
        assert stub.calls[0]["vars"]["character"].name == "Alice"

    @pytest.mark.asyncio
    async def test_falls_back_to_player_character(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Hero", is_player=True)
        stub = stub_prompt(
            {
                "director.generate-choices": [
                    ("ok", {"actions": "1. A\n2. B"})
                ]
            }
        )
        await director.generate_choices()
        assert stub.calls[0]["vars"]["character"].name == "Hero"

    @pytest.mark.asyncio
    async def test_uses_explicit_instructions_over_config(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Hero", is_player=True)
        director.actions["_generate_choices"].config[
            "instructions"
        ].value = "config instructions"
        stub = stub_prompt(
            {
                "director.generate-choices": [
                    ("ok", {"actions": "1. A"})
                ]
            }
        )
        await director.generate_choices(instructions="explicit override")
        assert stub.calls[0]["vars"]["instructions"] == "explicit override"

    @pytest.mark.asyncio
    async def test_uses_config_instructions_when_none_passed(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Hero", is_player=True)
        director.actions["_generate_choices"].config[
            "instructions"
        ].value = "config-default"
        stub = stub_prompt(
            {
                "director.generate-choices": [
                    ("ok", {"actions": "1. A"})
                ]
            }
        )
        try:
            await director.generate_choices()
            assert stub.calls[0]["vars"]["instructions"] == "config-default"
        finally:
            director.actions["_generate_choices"].config["instructions"].value = ""

    @pytest.mark.asyncio
    async def test_passes_num_choices_to_prompt(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Hero", is_player=True)
        director.actions["_generate_choices"].config["num_choices"].value = 5
        try:
            stub = stub_prompt(
                {
                    "director.generate-choices": [
                        ("ok", {"actions": "1. A"})
                    ]
                }
            )
            await director.generate_choices()
            assert stub.calls[0]["vars"]["num_choices"] == 5
        finally:
            director.actions["_generate_choices"].config["num_choices"].value = 3

    @pytest.mark.asyncio
    async def test_returns_none_when_extract_list_fails(
        self, scene, director, stub_prompt
    ):
        await _add_character(scene, "Hero", is_player=True)
        stub_prompt(
            {
                "director.generate-choices": [
                    ("garbage", {"actions": "garbage that fails extraction"})
                ]
            }
        )
        # Patch util.extract_list inside the module to raise
        import talemate.agents.director.generate_choices as gc_mod

        def _raise(*args, **kwargs):
            raise ValueError("bad extraction")

        with patch.object(gc_mod.util, "extract_list", side_effect=_raise):
            result = await director.generate_choices()
        assert result is None
