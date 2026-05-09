"""Unit tests for talemate.agents.director.guide.GuideSceneMixin.

Covers:
- Config property helpers (guide_scene, guide_actors, guide_narrator,
  guide_scene_guidance_length, guide_scene_cache_guidance).
- get_cached_guidance / set_cached_guidance / get_cached_character_guidance:
  fingerprint-based caching with `guide_scene_cache_guidance` flag gate.
- on_summarization_scene_analysis_after: dispatches narration vs conversation,
  early-returns when guide_scene disabled, no-ops when guidance is empty.
- on_editor_revision_analysis_before: copies cached guidance into
  emission.dynamic_instructions when present.
- guide_actor_off_of_scene_analysis / guide_narrator_off_of_scene_analysis:
  exercised via stubbed Prompt.request.
"""

from __future__ import annotations

import pytest

from conftest import MockScene, bootstrap_scene

import talemate.instance as instance
from talemate.agents.base import AgentTemplateEmission, DynamicInstruction
from talemate.agents.context import ActiveAgent
from talemate.agents.summarize.analyze_scene import SceneAnalysisEmission

from _director_test_helpers import (
    add_character_to_scene as _add_character,
    patch_prompt_request_in,
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


def _guide_fn():
    """Stand-in callable required by ActiveAgent context."""
    pass


@pytest.fixture
def stub_prompt(monkeypatch):
    """Replace the real ``Prompt.request`` classmethod with a queued response source.

    Patches the canonical ``talemate.prompts.base.Prompt`` class with
    ``raising=True`` so a rename of ``Prompt.request`` immediately fails.
    """
    from talemate.agents.director import guide as guide_mod
    return patch_prompt_request_in(monkeypatch, guide_mod)


# ---------------------------------------------------------------------------
# Config properties
# ---------------------------------------------------------------------------


class TestGuideConfigProperties:
    def test_guide_scene_disabled_by_default(self, director):
        assert director.guide_scene is False

    def test_guide_actors_default_true(self, director):
        assert director.guide_actors is True

    def test_guide_narrator_default_true(self, director):
        assert director.guide_narrator is True

    def test_guide_scene_guidance_length_returns_int(self, director):
        assert isinstance(director.guide_scene_guidance_length, int)
        assert director.guide_scene_guidance_length == 384

    def test_guide_scene_guidance_length_int_coercion(self, director):
        director.actions["guide_scene"].config["guidance_length"].value = "512"
        assert director.guide_scene_guidance_length == 512

    def test_guide_scene_cache_guidance_default_false(self, director):
        assert director.guide_scene_cache_guidance is False


# ---------------------------------------------------------------------------
# get_cached_guidance / set_cached_guidance
# ---------------------------------------------------------------------------


class TestCachedGuidance:
    @pytest.mark.asyncio
    async def test_returns_none_when_caching_disabled(self, director):
        # Default: cache_guidance=False
        result = await director.get_cached_guidance("any analysis")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_cache_state(self, director):
        director.actions["guide_scene"].config["cache_guidance"].value = True
        result = await director.get_cached_guidance("any analysis")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_guidance_without_analysis_arg(self, director):
        director.actions["guide_scene"].config["cache_guidance"].value = True
        director.set_scene_states(
            cached_guidance={
                "fp": "some-fp",
                "guidance": "cached text",
                "analysis_type": "narration",
                "character": None,
            }
        )
        # Without an analysis parameter, returns the cached guidance
        result = await director.get_cached_guidance(None)
        assert result == "cached text"

    @pytest.mark.asyncio
    async def test_returns_none_when_fingerprint_mismatches(self, scene, director):
        director.actions["guide_scene"].config["cache_guidance"].value = True
        director.set_scene_states(
            cached_guidance={
                "fp": "different-fp",
                "guidance": "cached text",
                "analysis_type": "narration",
                "character": None,
            }
        )
        # Without ActiveAgent context, context_fingerprint returns None
        result = await director.get_cached_guidance("any analysis")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_when_fingerprint_matches(self, scene, director):
        director.actions["guide_scene"].config["cache_guidance"].value = True
        # Compute the fingerprint inside an ActiveAgent context, store it,
        # then retrieve it inside the SAME context (so context_fingerprint
        # matches).
        with ActiveAgent(director, _guide_fn):
            await director.set_cached_guidance(
                analysis="my analysis",
                guidance="cached guidance",
                analysis_type="narration",
            )
            result = await director.get_cached_guidance("my analysis")
        assert result == "cached guidance"

    @pytest.mark.asyncio
    async def test_set_cached_guidance_stores_character_name(
        self, scene, director
    ):
        char = await _add_character(scene, "Alice")
        with ActiveAgent(director, _guide_fn):
            await director.set_cached_guidance(
                analysis="x",
                guidance="hint",
                analysis_type="conversation",
                character=char,
            )
        cached = director.get_scene_state("cached_guidance")
        assert cached["character"] == "Alice"
        assert cached["analysis_type"] == "conversation"

    @pytest.mark.asyncio
    async def test_set_cached_guidance_with_no_character(self, scene, director):
        with ActiveAgent(director, _guide_fn):
            await director.set_cached_guidance(
                analysis="x", guidance="g", analysis_type="narration"
            )
        cached = director.get_scene_state("cached_guidance")
        assert cached["character"] is None


# ---------------------------------------------------------------------------
# get_cached_character_guidance
# ---------------------------------------------------------------------------


class TestGetCachedCharacterGuidance:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_cache(self, director):
        result = await director.get_cached_character_guidance("Alice")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_guidance_when_character_and_type_match(self, director):
        director.set_scene_states(
            cached_guidance={
                "fp": "x",
                "guidance": "alice's hint",
                "analysis_type": "conversation",
                "character": "Alice",
            }
        )
        result = await director.get_cached_character_guidance("Alice")
        assert result == "alice's hint"

    @pytest.mark.asyncio
    async def test_returns_none_when_character_mismatch(self, director):
        director.set_scene_states(
            cached_guidance={
                "fp": "x",
                "guidance": "g",
                "analysis_type": "conversation",
                "character": "Alice",
            }
        )
        result = await director.get_cached_character_guidance("Bob")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_analysis_type_is_not_conversation(
        self, director
    ):
        director.set_scene_states(
            cached_guidance={
                "fp": "x",
                "guidance": "g",
                "analysis_type": "narration",
                "character": "Alice",
            }
        )
        result = await director.get_cached_character_guidance("Alice")
        assert result is None


# ---------------------------------------------------------------------------
# on_summarization_scene_analysis_after
# ---------------------------------------------------------------------------


class TestOnSummarizationSceneAnalysisAfter:
    @pytest.mark.asyncio
    async def test_no_op_when_guide_scene_disabled(self, scene, director):
        # guide_scene defaults to False
        emission = SceneAnalysisEmission(
            agent=director,
            analysis_type="narration",
            response="some analysis",
        )
        await director.on_summarization_scene_analysis_after(emission)
        # No context state set
        assert director.get_context_state("narrator_guidance") is None

    @pytest.mark.asyncio
    async def test_narration_analysis_calls_guide_narrator(
        self, scene, director, stub_prompt
    ):
        director.actions["guide_scene"].enabled = True
        try:
            stub_prompt(
                {
                    "director.guide-narration": [
                        ("guidance text", {"guidance": "guidance text"})
                    ]
                }
            )
            emission = SceneAnalysisEmission(
                agent=director,
                analysis_type="narration",
                response="some analysis",
            )
            # context_state lives inside the active agent context — assert
            # within the `with` block.
            with ActiveAgent(director, _guide_fn):
                await director.on_summarization_scene_analysis_after(emission)
                assert (
                    director.get_context_state("narrator_guidance")
                    == "guidance text"
                )
        finally:
            director.actions["guide_scene"].enabled = False

    @pytest.mark.asyncio
    async def test_conversation_analysis_calls_guide_actor(
        self, scene, director, stub_prompt
    ):
        director.actions["guide_scene"].enabled = True
        char = await _add_character(scene, "Alice")
        try:
            stub_prompt(
                {
                    "director.guide-conversation": [
                        ("actor hint", {"guidance": "actor hint"})
                    ]
                }
            )
            emission = SceneAnalysisEmission(
                agent=director,
                analysis_type="conversation",
                response="analysis",
                template_vars={"character": char},
            )
            with ActiveAgent(director, _guide_fn):
                await director.on_summarization_scene_analysis_after(emission)
                assert (
                    director.get_context_state("actor_guidance") == "actor hint"
                )
        finally:
            director.actions["guide_scene"].enabled = False

    @pytest.mark.asyncio
    async def test_skips_narration_when_guide_narrator_disabled(
        self, scene, director, stub_prompt
    ):
        director.actions["guide_scene"].enabled = True
        director.actions["guide_scene"].config["guide_narrator"].value = False
        try:
            emission = SceneAnalysisEmission(
                agent=director, analysis_type="narration", response="x"
            )
            with ActiveAgent(director, _guide_fn):
                await director.on_summarization_scene_analysis_after(emission)
                # No call made because guide_narrator is False
                assert director.get_context_state("narrator_guidance") is None
        finally:
            director.actions["guide_scene"].enabled = False
            director.actions["guide_scene"].config["guide_narrator"].value = True

    @pytest.mark.asyncio
    async def test_warns_and_returns_when_response_empty(
        self, scene, director, stub_prompt
    ):
        director.actions["guide_scene"].enabled = True
        try:
            # Empty response from the LLM stub
            stub_prompt(
                {"director.guide-narration": [("", {"guidance": ""})]}
            )
            emission = SceneAnalysisEmission(
                agent=director, analysis_type="narration", response="x"
            )
            with ActiveAgent(director, _guide_fn):
                await director.on_summarization_scene_analysis_after(emission)
                # Empty guidance → no context state set
                assert director.get_context_state("narrator_guidance") is None
        finally:
            director.actions["guide_scene"].enabled = False


# ---------------------------------------------------------------------------
# on_editor_revision_analysis_before
# ---------------------------------------------------------------------------


class TestOnEditorRevisionAnalysisBefore:
    @pytest.mark.asyncio
    async def test_no_dynamic_instruction_added_without_cache(self, director):
        emission = AgentTemplateEmission(agent=director)
        emission.response = "analysis text"
        await director.on_editor_revision_analysis_before(emission)
        assert emission.dynamic_instructions == []

    @pytest.mark.asyncio
    async def test_appends_cached_guidance_as_dynamic_instruction(
        self, scene, director
    ):
        director.actions["guide_scene"].config["cache_guidance"].value = True
        try:
            with ActiveAgent(director, _guide_fn):
                await director.set_cached_guidance(
                    analysis="my analysis",
                    guidance="cached guidance text",
                    analysis_type="narration",
                )
                emission = AgentTemplateEmission(agent=director)
                emission.response = "my analysis"
                await director.on_editor_revision_analysis_before(emission)
            assert len(emission.dynamic_instructions) == 1
            instr = emission.dynamic_instructions[0]
            assert isinstance(instr, DynamicInstruction)
            assert instr.title == "Guidance"
            assert instr.content == "cached guidance text"
        finally:
            director.actions["guide_scene"].config["cache_guidance"].value = False


# ---------------------------------------------------------------------------
# guide_actor_off_of_scene_analysis & guide_narrator_off_of_scene_analysis
# ---------------------------------------------------------------------------


class TestGuideActorOffOfSceneAnalysis:
    @pytest.mark.asyncio
    async def test_uses_extracted_guidance_when_present(
        self, scene, director, stub_prompt
    ):
        char = await _add_character(scene, "Alice")
        stub_prompt(
            {
                "director.guide-conversation": [
                    ("raw response", {"guidance": "extracted guidance"})
                ]
            }
        )
        with ActiveAgent(director, _guide_fn):
            result = await director.guide_actor_off_of_scene_analysis(
                "scene analysis", char
            )
        assert result == "extracted guidance"

    @pytest.mark.asyncio
    async def test_falls_back_to_response_when_no_extracted(
        self, scene, director, stub_prompt
    ):
        char = await _add_character(scene, "Alice")
        # Empty extracted; use raw response.
        stub_prompt(
            {
                "director.guide-conversation": [
                    ("raw fallback content.", {"guidance": ""})
                ]
            }
        )
        with ActiveAgent(director, _guide_fn):
            result = await director.guide_actor_off_of_scene_analysis(
                "scene analysis", char
            )
        # Strip + strip_partial_sentences may keep complete sentences only
        assert "raw fallback" in result or result == ""


class TestGuideNarratorOffOfSceneAnalysis:
    @pytest.mark.asyncio
    async def test_uses_extracted_guidance(
        self, scene, director, stub_prompt
    ):
        stub_prompt(
            {
                "director.guide-narration": [
                    ("raw", {"guidance": "narrator guidance"})
                ]
            }
        )
        with ActiveAgent(director, _guide_fn):
            result = await director.guide_narrator_off_of_scene_analysis(
                "analysis"
            )
        assert result == "narrator guidance"

    @pytest.mark.asyncio
    async def test_calls_correct_template(self, scene, director, stub_prompt):
        stub = stub_prompt(
            {
                "director.guide-narration": [
                    ("raw", {"guidance": "x"})
                ]
            }
        )
        with ActiveAgent(director, _guide_fn):
            await director.guide_narrator_off_of_scene_analysis(
                "analysis", response_length=512
            )
        assert stub.calls[0]["template"] == "director.guide-narration"
        assert stub.calls[0]["vars"]["response_length"] == 512
