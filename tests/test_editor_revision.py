"""
Tests for ``talemate.agents.editor.revision`` (the RevisionMixin and helpers).

Targets:
- Pure helpers: ``_strip_character_prefix``, ``_reattach_character_prefix``,
  ``_format_length_ratio``, ``_revision_exceeds_max_length``.
- Schema/property behavior: ``Issues.log``, ``RevisionInformation``, the
  context managers (``RevisionDisabled``, ``RevisionContext``).
- RevisionMixin: action registration, config property helpers,
  ``revision_collect_repetition_range``, ``revision_detect_bad_prose`` (regex
  branch), ``revision_collect_issues`` for the fuzzy path,
  ``revision_dedupe`` (full happy-path and reduction-too-high path),
  ``revision_revise`` dispatch + GenerationCancelled handling, and the
  signal hook ``revision_on_generation`` filter logic.
- Async LLM-driven paths (``revision_rewrite``, ``revision_unslop``) are not
  exhaustively driven through Prompt.request; they rely on too much template
  plumbing. Those are covered indirectly via the dispatch tests.
"""

import pytest

from talemate.agents.conversation import ConversationAgentEmission
from talemate.agents.creator.assistant import (
    ContentGenerationContext,
    ContextualGenerateEmission,
)
from talemate.agents.editor.revision import (
    CONTEXTUAL_GENERATION_TYPES,
    Issues,
    RevisionContext,
    RevisionDisabled,
    RevisionInformation,
    _format_length_ratio,
    _reattach_character_prefix,
    _revision_exceeds_max_length,
    _strip_character_prefix,
    revision_context,
    revision_disabled_context,
)
from talemate.agents.narrator import NarratorAgentEmission
from talemate.agents.summarize import SummarizeEmission
from talemate.character import Character
from talemate.exceptions import GenerationCancelled
from talemate.scene_message import CharacterMessage, NarratorMessage
from talemate.world_state.templates.content import PhraseDetection, WritingStyle

from conftest import MockScene, bootstrap_scene


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alice():
    return Character(name="Alice", description="A test character.")


def _make_contextual_emission(editor, context_str: str) -> ContextualGenerateEmission:
    """Build a ContextualGenerateEmission with a real ContentGenerationContext.

    The ``context_type``/``context_name`` are computed properties that read
    from the inner ``ContentGenerationContext.context`` (in ``"type:name"``
    form), so we wire that up here rather than passing keyword args directly.
    """
    return ContextualGenerateEmission(
        agent=editor,
        response="some text",
        content_generation_context=ContentGenerationContext(context=context_str),
    )


class _SceneWithWritingStyle(MockScene):
    """MockScene that allows ``writing_style`` to be set as an attribute.

    The base ``Scene`` exposes ``writing_style`` as a read-only property
    that resolves through ``writing_style_template`` and the world-state
    template collection. For unit tests we want to plug in a
    ``WritingStyle`` directly without a template store, so override the
    property with a mutable attribute.
    """

    _writing_style: WritingStyle | None = None

    @property
    def writing_style(self):  # type: ignore[override]
        return self._writing_style

    @writing_style.setter
    def writing_style(self, value):
        self._writing_style = value


@pytest.fixture
def editor_scene():
    """A bootstrapped scene with the editor (RevisionMixin) wired up."""
    scene = _SceneWithWritingStyle()
    agents = bootstrap_scene(scene)
    editor = agents["editor"]
    # Enable the revision action so config helpers reflect the actual action.
    editor.actions["revision"].enabled = True
    return scene, editor


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestStripAndReattachCharacterPrefix:
    def test_strip_character_prefix_match(self, alice):
        text = "Alice: Hello there"
        body, had = _strip_character_prefix(text, alice)
        assert body == "Hello there"
        assert had is True

    def test_strip_character_prefix_no_match(self, alice):
        text = "Bob: Hello there"
        body, had = _strip_character_prefix(text, alice)
        assert body == text
        assert had is False

    def test_strip_character_prefix_no_character(self):
        text = "Alice: Hi"
        body, had = _strip_character_prefix(text, None)
        assert body == text
        assert had is False

    def test_reattach_character_prefix_when_missing(self, alice):
        result = _reattach_character_prefix("Hi there", alice, had_prefix=True)
        assert result == "Alice: Hi there"

    def test_reattach_character_prefix_idempotent(self, alice):
        # If model already included the prefix, do not double it up.
        result = _reattach_character_prefix("Alice: Hi there", alice, had_prefix=True)
        assert result == "Alice: Hi there"

    def test_reattach_character_prefix_skipped_when_no_prefix_originally(self, alice):
        result = _reattach_character_prefix("Hi", alice, had_prefix=False)
        assert result == "Hi"

    def test_reattach_character_prefix_no_character(self):
        # Without a character, return text unchanged even if had_prefix=True.
        result = _reattach_character_prefix("Hi", None, had_prefix=True)
        assert result == "Hi"


class TestFormatLengthRatio:
    def test_zero_division_returns_inf(self):
        assert _format_length_ratio(10, 0) == "inf"

    def test_basic_ratio(self):
        assert _format_length_ratio(150, 100) == "1.50"

    def test_zero_new(self):
        assert _format_length_ratio(0, 100) == "0.00"


class TestRevisionExceedsMaxLength:
    def test_within_ratio_returns_false(self):
        # 100 -> 120 is within 1.25 ratio.
        assert (
            _revision_exceeds_max_length("test", "x" * 100, "y" * 120, ratio=1.25)
            is False
        )

    def test_at_ratio_returns_false(self):
        # Exactly at the ratio boundary -> not "exceeds".
        assert (
            _revision_exceeds_max_length("test", "x" * 100, "y" * 125, ratio=1.25)
            is False
        )

    def test_above_ratio_returns_true(self):
        assert (
            _revision_exceeds_max_length("test", "x" * 100, "y" * 200, ratio=1.25)
            is True
        )

    def test_empty_original_returns_true_when_revised_has_text(self):
        assert _revision_exceeds_max_length("test", "", "anything", ratio=1.25) is True


# ---------------------------------------------------------------------------
# Schema & context manager tests
# ---------------------------------------------------------------------------


class TestIssuesSchema:
    def test_log_combines_repetition_and_bad_prose(self):
        issues = Issues(
            repetition_log=["rep-a", "rep-b"],
            bad_prose_log=["bp-a"],
        )
        assert issues.log == ["rep-a", "rep-b", "bp-a"]

    def test_empty_log(self):
        assert Issues().log == []

    def test_repetition_matches_default_empty(self):
        assert Issues().repetition_matches == []


class TestRevisionInformationDefaults:
    def test_defaults(self):
        info = RevisionInformation()
        assert info.text is None
        assert info.character is None
        assert info.context_type is None
        assert info.summarization_history is None
        assert info.revision_method is None

    def test_loading_status_excluded_from_dump(self):
        info = RevisionInformation(text="hi")
        dumped = info.model_dump()
        assert "loading_status" not in dumped
        assert dumped["text"] == "hi"


class TestRevisionDisabledContext:
    def test_disabled_inside_context_manager(self):
        assert revision_disabled_context.get() is False
        with RevisionDisabled():
            assert revision_disabled_context.get() is True
        assert revision_disabled_context.get() is False


class TestRevisionContext:
    def test_message_id_set_inside_context_manager(self):
        # default state has message_id=None
        assert revision_context.get().message_id is None
        with RevisionContext(message_id=42):
            assert revision_context.get().message_id == 42
        assert revision_context.get().message_id is None

    def test_no_message_id_inside_context_manager(self):
        with RevisionContext(message_id=None):
            assert revision_context.get().message_id is None


# ---------------------------------------------------------------------------
# Action registration & config helpers
# ---------------------------------------------------------------------------


class TestRevisionActionRegistration:
    def test_action_present_with_expected_keys(self, editor_scene):
        _, editor = editor_scene
        action = editor.actions["revision"]
        assert action.label == "Revision"
        assert action.icon == "mdi-typewriter"
        for key in [
            "automatic_revision",
            "automatic_revision_targets",
            "revision_method",
            "split_on_comma",
            "min_issues",
            "detect_bad_prose",
            "detect_bad_prose_threshold",
            "repetition_detection_method",
            "repetition_threshold",
            "repetition_range",
            "repetition_min_length",
        ]:
            assert key in action.config

    def test_default_method_is_unslop(self, editor_scene):
        _, editor = editor_scene
        assert editor.revision_method == "unslop"

    def test_property_helpers_match_config(self, editor_scene):
        _, editor = editor_scene
        # Set a few config values and confirm property accessors mirror them.
        editor.actions["revision"].config["repetition_threshold"].value = 90
        editor.actions["revision"].config["repetition_range"].value = 5
        editor.actions["revision"].config["repetition_min_length"].value = 25
        editor.actions["revision"].config["min_issues"].value = 2
        editor.actions["revision"].config["split_on_comma"].value = False
        editor.actions["revision"].config["detect_bad_prose"].value = False
        editor.actions["revision"].config["detect_bad_prose_threshold"].value = 0.8
        editor.actions["revision"].config["automatic_revision"].value = False
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "narrator"
        ]
        editor.actions["revision"].config["repetition_detection_method"].value = "fuzzy"

        assert editor.revision_repetition_threshold == 90
        assert editor.revision_repetition_range == 5
        assert editor.revision_repetition_min_length == 25
        assert editor.revision_min_issues == 2
        assert editor.revision_split_on_comma is False
        assert editor.revision_detect_bad_prose_enabled is False
        assert editor.revision_detect_bad_prose_threshold == 0.8
        assert editor.revision_automatic_enabled is False
        assert editor.revision_automatic_targets == ["narrator"]
        assert editor.revision_repetition_detection_method == "fuzzy"
        assert editor.revision_enabled is True


# ---------------------------------------------------------------------------
# revision_collect_repetition_range
# ---------------------------------------------------------------------------


class TestCollectRepetitionRange:
    async def test_collects_character_messages_without_name(self, editor_scene):
        scene, editor = editor_scene
        scene.history = [
            CharacterMessage(message="Alice: Hello there", source="ai"),
            CharacterMessage(message="Bob: Hi back", source="ai"),
            NarratorMessage(message="The wind blows.", source="ai"),
        ]
        editor.actions["revision"].config["repetition_range"].value = 10

        result = await editor.revision_collect_repetition_range()

        # CharacterMessage uses without_name (everything after the colon),
        # NarratorMessage falls into the else branch and uses .message.
        assert any("Hello there" in r for r in result)
        assert any("Hi back" in r for r in result)
        assert any("The wind blows." in r for r in result)
        # Verify CharacterMessage prefix is stripped.
        assert all("Alice:" not in r for r in result)
        assert all("Bob:" not in r for r in result)

    async def test_respects_repetition_range_limit(self, editor_scene):
        scene, editor = editor_scene
        scene.history = [
            CharacterMessage(message=f"Alice: m{i}", source="ai") for i in range(20)
        ]
        editor.actions["revision"].config["repetition_range"].value = 3

        result = await editor.revision_collect_repetition_range()
        assert len(result) == 3

    async def test_empty_history_returns_empty_list(self, editor_scene):
        scene, editor = editor_scene
        scene.history = []
        result = await editor.revision_collect_repetition_range()
        assert result == []


# ---------------------------------------------------------------------------
# revision_detect_bad_prose (regex branch)
# ---------------------------------------------------------------------------


class TestRevisionDetectBadProseRegex:
    async def test_returns_empty_when_no_writing_style(self, editor_scene):
        _, editor = editor_scene
        editor.scene.writing_style = None
        result = await editor.revision_detect_bad_prose("anything")
        assert result == []

    async def test_returns_empty_when_writing_style_has_no_phrases(self, editor_scene):
        _, editor = editor_scene
        editor.scene.writing_style = WritingStyle(
            name="empty", description="x", phrases=[]
        )
        assert await editor.revision_detect_bad_prose("anything") == []

    async def test_regex_match_returns_identified_phrase(self, editor_scene):
        _, editor = editor_scene
        # Use regex method to keep this offline (no embedding needed).
        # The split_on_comma flag transforms sentences into bare strings
        # before the regex helper runs.
        editor.actions["revision"].config["split_on_comma"].value = True
        editor.scene.writing_style = WritingStyle(
            name="ws",
            description="d",
            phrases=[
                PhraseDetection(
                    phrase="purple prose",
                    instructions="Avoid this exact wording.",
                    classification="unwanted",
                    match_method="regex",
                    active=True,
                )
            ],
        )
        # The bad-prose helper unconditionally calls the memory agent's
        # semantic-similarity path even when no semantic-similarity phrases
        # are configured. Stub it out to keep this regex test self-contained.
        from talemate.instance import get_agent

        async def empty_compare(a, b, similarity_threshold=None):
            return {"similarity_matches": []}

        get_agent("memory").compare_string_lists = empty_compare

        result = await editor.revision_detect_bad_prose(
            "She wrote some purple prose for fun."
        )
        assert len(result) == 1
        assert result[0]["matched"] == "purple prose"
        assert result[0]["method"] == "regex"
        assert result[0]["instructions"] == "Avoid this exact wording."

    async def test_inactive_phrases_skipped(self, editor_scene):
        _, editor = editor_scene
        editor.scene.writing_style = WritingStyle(
            name="ws",
            description="d",
            phrases=[
                PhraseDetection(
                    phrase="purple prose",
                    instructions="x",
                    classification="unwanted",
                    match_method="regex",
                    active=False,
                )
            ],
        )
        from talemate.instance import get_agent

        async def empty_compare(a, b, similarity_threshold=None):
            return {"similarity_matches": []}

        get_agent("memory").compare_string_lists = empty_compare

        assert await editor.revision_detect_bad_prose("purple prose abound") == []


# ---------------------------------------------------------------------------
# revision_collect_issues — fuzzy path (no embeddings required)
# ---------------------------------------------------------------------------


class TestRevisionCollectIssuesFuzzy:
    async def test_no_repetition_returns_empty_issues(self, editor_scene, alice):
        scene, editor = editor_scene
        scene.history = []
        editor.actions["revision"].config["repetition_detection_method"].value = "fuzzy"
        editor.scene.writing_style = None
        issues = await editor.revision_collect_issues(
            "Brand new sentence that has nothing matched.", alice
        )
        assert issues.repetition == []
        assert issues.repetition_matches == []
        assert issues.repetition_log == []
        assert issues.bad_prose_log == []
        assert issues.bad_prose == []

    async def test_repetition_detected_via_fuzzy(self, editor_scene, alice):
        scene, editor = editor_scene
        # Build a duplicate sentence in scene history -> the same sentence in
        # incoming text should fuzzy-match against it.
        repeated = "She walked into the dim garden and breathed deeply."
        scene.history = [CharacterMessage(message=f"Alice: {repeated}", source="ai")]
        editor.actions["revision"].config["repetition_detection_method"].value = "fuzzy"
        editor.actions["revision"].config["repetition_threshold"].value = 80
        editor.actions["revision"].config["repetition_range"].value = 5
        editor.actions["revision"].config["repetition_min_length"].value = 5
        editor.scene.writing_style = None

        issues = await editor.revision_collect_issues(repeated, alice)
        assert issues.repetition_matches, "expected at least one fuzzy match"
        assert any("Repetition" in line for line in issues.repetition_log)


# ---------------------------------------------------------------------------
# revision_dedupe
# ---------------------------------------------------------------------------


class TestRevisionDedupe:
    async def test_no_repetition_returns_original(self, editor_scene, alice):
        scene, editor = editor_scene
        editor.actions["revision"].config["repetition_detection_method"].value = "fuzzy"
        editor.scene.writing_style = None
        scene.history = []

        info = RevisionInformation(
            text="Alice: A unique line that has not been said before.",
            character=alice,
        )
        result = await editor.revision_dedupe(info)
        assert result == info.text

    async def test_repetition_dedupes_and_preserves_prefix(self, editor_scene, alice):
        scene, editor = editor_scene
        editor.actions["revision"].config["repetition_detection_method"].value = "fuzzy"
        editor.actions["revision"].config["repetition_threshold"].value = 90
        editor.actions["revision"].config["repetition_range"].value = 10
        editor.actions["revision"].config["repetition_min_length"].value = 5
        editor.scene.writing_style = None

        repeated = "She walked into the dim garden and breathed deeply."
        unique = "Then she pulled out a tiny brass key."
        scene.history = [
            CharacterMessage(message=f"Alice: {repeated}", source="ai"),
        ]
        info = RevisionInformation(
            text=f"Alice: {repeated} {unique}",
            character=alice,
        )
        result = await editor.revision_dedupe(info)
        # Prefix preserved.
        assert result.startswith("Alice: ")
        # Repeated sentence removed; unique sentence preserved.
        assert unique in result
        # Method marker set.
        assert info.revision_method == "dedupe"


# ---------------------------------------------------------------------------
# revision_revise — dispatch & error handling
# ---------------------------------------------------------------------------


class TestRevisionReviseDispatch:
    async def test_dispatch_dedupe(self, editor_scene, alice):
        _, editor = editor_scene
        editor.actions["revision"].config["revision_method"].value = "dedupe"
        called = {}

        async def fake_dedupe(info):
            called["dedupe"] = info
            return "DEDUPED"

        editor.revision_dedupe = fake_dedupe

        info = RevisionInformation(text="some text", character=alice)
        result = await editor.revision_revise(info)
        assert result == "DEDUPED"
        assert called["dedupe"] is info

    async def test_dispatch_rewrite(self, editor_scene, alice):
        _, editor = editor_scene
        editor.actions["revision"].config["revision_method"].value = "rewrite"

        async def fake_rewrite(info):
            return "REWRITTEN"

        editor.revision_rewrite = fake_rewrite
        info = RevisionInformation(text="orig", character=alice)
        assert await editor.revision_revise(info) == "REWRITTEN"

    async def test_dispatch_unslop(self, editor_scene, alice):
        _, editor = editor_scene
        editor.actions["revision"].config["revision_method"].value = "unslop"

        async def fake_unslop(info):
            return "UNSLOPPED"

        editor.revision_unslop = fake_unslop
        info = RevisionInformation(text="orig", character=alice)
        assert await editor.revision_revise(info) == "UNSLOPPED"

    async def test_generation_cancelled_returns_original_text(
        self, editor_scene, alice
    ):
        _, editor = editor_scene
        editor.actions["revision"].config["revision_method"].value = "dedupe"

        async def cancelling(info):
            raise GenerationCancelled("cancelled")

        editor.revision_dedupe = cancelling
        info = RevisionInformation(text="ORIGINAL", character=alice)
        result = await editor.revision_revise(info)
        assert result == "ORIGINAL"

    async def test_other_exception_returns_original_text(self, editor_scene, alice):
        _, editor = editor_scene
        editor.actions["revision"].config["revision_method"].value = "dedupe"

        async def failing(info):
            raise RuntimeError("oops")

        editor.revision_dedupe = failing
        info = RevisionInformation(text="ORIGINAL", character=alice)
        result = await editor.revision_revise(info)
        assert result == "ORIGINAL"


# ---------------------------------------------------------------------------
# revision_on_generation — filter logic
# ---------------------------------------------------------------------------


class TestRevisionOnGeneration:
    """Verify the filter / branching logic of revision_on_generation.

    All tests stub revision_revise to capture whether and with what info it
    was called, so we test the filter behavior rather than the rewrite logic.
    """

    @pytest.fixture
    def stub_revise(self, editor_scene):
        scene, editor = editor_scene
        editor.actions["revision"].enabled = True
        editor.actions["revision"].config["automatic_revision"].value = True

        calls = []

        async def fake_revise(info):
            calls.append(info)
            return "REVISED"

        editor.revision_revise = fake_revise
        return scene, editor, calls

    async def test_skipped_when_disabled(self, editor_scene, alice):
        scene, editor = editor_scene
        editor.actions["revision"].enabled = False
        called = []

        async def revise_spy(info):
            called.append(info)
            return "X"

        editor.revision_revise = revise_spy

        emission = ConversationAgentEmission(
            agent=editor, response="hi", actor=None, character=alice
        )
        await editor.revision_on_generation(emission)
        assert called == []
        # The original response is left untouched.
        assert emission.response == "hi"

    async def test_skipped_when_automatic_off(self, editor_scene, alice):
        _, editor = editor_scene
        editor.actions["revision"].enabled = True
        editor.actions["revision"].config["automatic_revision"].value = False
        called = []

        async def spy(info):
            called.append(info)
            return "X"

        editor.revision_revise = spy
        emission = ConversationAgentEmission(
            agent=editor, response="hi", actor=None, character=alice
        )
        await editor.revision_on_generation(emission)
        assert called == []

    async def test_filter_character_target_disabled(self, stub_revise, alice):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "narrator"
        ]
        emission = ConversationAgentEmission(
            agent=editor, response="hi", actor=None, character=alice
        )
        await editor.revision_on_generation(emission)
        assert calls == []

    async def test_character_target_enabled_runs_revision(self, stub_revise, alice):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "character"
        ]
        emission = ConversationAgentEmission(
            agent=editor, response="orig", actor=None, character=alice
        )
        await editor.revision_on_generation(emission)
        assert len(calls) == 1
        assert calls[0].text == "orig"
        assert calls[0].character is alice
        assert emission.response == "REVISED"

    async def test_filter_narrator_target_disabled(self, stub_revise):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "character"
        ]
        emission = NarratorAgentEmission(agent=editor, response="orig narrator")
        await editor.revision_on_generation(emission)
        assert calls == []

    async def test_narrator_target_enabled_runs_revision(self, stub_revise):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "narrator"
        ]
        emission = NarratorAgentEmission(agent=editor, response="orig narrator")
        await editor.revision_on_generation(emission)
        assert len(calls) == 1
        assert emission.response == "REVISED"

    async def test_filter_contextual_generation_target_disabled(self, stub_revise):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "character"
        ]
        emission = _make_contextual_emission(editor, "character attribute:age")
        await editor.revision_on_generation(emission)
        assert calls == []

    async def test_contextual_generation_unknown_type_skipped(self, stub_revise):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "contextual_generation"
        ]
        emission = _make_contextual_emission(editor, "not-a-real-type:x")
        await editor.revision_on_generation(emission)
        assert calls == []

    async def test_contextual_generation_known_type_runs(self, stub_revise):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "contextual_generation"
        ]
        emission = _make_contextual_emission(
            editor, f"{CONTEXTUAL_GENERATION_TYPES[0]}:x"
        )
        await editor.revision_on_generation(emission)
        assert len(calls) == 1

    async def test_summarize_dialogue_filtered_when_summarization_target_off(
        self, stub_revise
    ):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "character"
        ]
        emission = SummarizeEmission(
            agent=editor,
            response="summary",
            summarization_type="dialogue",
            summarization_history=["prev1"],
        )
        await editor.revision_on_generation(emission)
        assert calls == []

    async def test_summarize_dialogue_runs_when_summarization_target_on(
        self, stub_revise
    ):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "summarization"
        ]
        emission = SummarizeEmission(
            agent=editor,
            response="summary",
            summarization_type="dialogue",
            summarization_history=["prev1"],
        )
        await editor.revision_on_generation(emission)
        assert len(calls) == 1
        assert calls[0].summarization_history == ["prev1"]

    async def test_summarize_events_always_skipped(self, stub_revise):
        """Event summarization is hard-coded as skipped regardless of target."""
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "summarization"
        ]
        emission = SummarizeEmission(
            agent=editor,
            response="summary",
            summarization_type="events",
        )
        await editor.revision_on_generation(emission)
        assert calls == []

    async def test_disabled_through_context_manager(self, stub_revise, alice):
        _, editor, calls = stub_revise
        editor.actions["revision"].config["automatic_revision_targets"].value = [
            "character"
        ]
        emission = ConversationAgentEmission(
            agent=editor, response="orig", actor=None, character=alice
        )
        with RevisionDisabled():
            await editor.revision_on_generation(emission)
        assert calls == []
        assert emission.response == "orig"


# ---------------------------------------------------------------------------
# inject_prompt_paramters
# ---------------------------------------------------------------------------


class TestInjectPromptParameters:
    def test_revision_revise_appends_fix_stop_string(self, editor_scene):
        _, editor = editor_scene
        params = {}
        editor.inject_prompt_paramters(params, "edit_512", "revision_revise")
        assert "extra_stopping_strings" in params
        assert "</FIX>" in params["extra_stopping_strings"]

    def test_other_function_does_not_inject_fix_stop_string(self, editor_scene):
        _, editor = editor_scene
        params = {"extra_stopping_strings": ["BASE"]}
        editor.inject_prompt_paramters(params, "edit_512", "some_other_function")
        # Should not add </FIX> for non-matching functions.
        assert "</FIX>" not in params.get("extra_stopping_strings", [])
