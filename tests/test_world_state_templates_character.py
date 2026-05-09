"""
Unit tests for `talemate.world_state.templates.character`.

The full `generate()` paths drive the creator agent's `contextual_generate`
pipeline, which renders large prompt templates that depend on a full
scene/agent stack (rag_cache, history, etc.). To unit-test the apply-vs-not
branches without standing up the full LLM stack, we replace the creator
agent's `contextual_generate_from_args` method with a tiny coroutine that
returns a canned string, then verify what `Attribute.generate` /
`Detail.generate` do with the response.

Tests cover:

- Template field defaults / validation
- The `character not found` early-return branch
- `apply=False` does NOT mutate the character
- `apply=True` writes to base_attributes / details
- Returned `GeneratedAttribute` / `GeneratedDetail` round-trip the response
- The `formatted()` helper applied to attribute / detail / instructions
- Default `GenerationOptions` construction
"""

import pytest

import talemate.instance as instance
from _world_state_helpers import (
    install_tracking_memory,
    make_actor,
    scene,  # noqa: F401 - pytest fixture
)
from talemate.world_state.templates.character import (
    Attribute,
    Detail,
    GeneratedAttribute,
    GeneratedDetail,
)
from talemate.world_state.templates.content import GenerationOptions


def _stub_creator_response(scene, response: str):
    """Replace the creator agent's `contextual_generate_from_args` on the
    INSTANCE (not class) to return a fixed string. This narrowly bypasses
    the large LLM prompt pipeline so we can unit-test the post-response
    apply / GeneratedAttribute logic in `Attribute.generate` /
    `Detail.generate` without faking the function under test (those are
    still real)."""

    creator = instance.get_agent("creator")

    async def fake_generate(*args, **kwargs):
        return response

    creator.contextual_generate_from_args = fake_generate


# ---------------------------------------------------------------------------
# Template construction / defaults
# ---------------------------------------------------------------------------


class TestAttributeFields:
    def test_attribute_template_defaults(self):
        a = Attribute(name="Personality", attribute="personality")
        assert a.name == "Personality"
        assert a.attribute == "personality"
        assert a.template_type == "character_attribute"
        assert a.supports_spice is False
        assert a.supports_style is False
        assert a.instructions is None
        assert a.description is None

    def test_detail_template_defaults(self):
        d = Detail(name="Hometown", detail="hometown")
        assert d.name == "Hometown"
        assert d.detail == "hometown"
        assert d.template_type == "character_detail"
        assert d.supports_spice is False
        assert d.supports_style is False


# ---------------------------------------------------------------------------
# Generated* pydantic models
# ---------------------------------------------------------------------------


class TestGeneratedModels:
    def test_generated_attribute_holds_template_ref(self):
        a = Attribute(name="N", attribute="x")
        ga = GeneratedAttribute(
            attribute="x", value="v", character="Alice", template=a
        )
        assert ga.template is a
        assert ga.attribute == "x"
        assert ga.character == "Alice"
        assert ga.value == "v"

    def test_generated_detail_holds_template_ref(self):
        d = Detail(name="N", detail="x")
        gd = GeneratedDetail(
            detail="x", value="v", character="Alice", template=d
        )
        assert gd.template is d
        assert gd.detail == "x"
        assert gd.character == "Alice"


# ---------------------------------------------------------------------------
# generate() - character-not-found path (no LLM required)
# ---------------------------------------------------------------------------


class TestAttributeGenerateMissingCharacter:
    @pytest.mark.asyncio
    async def test_returns_none_when_character_not_found(self, scene):
        install_tracking_memory(scene)
        tmpl = Attribute(name="P", attribute="personality")
        result = await tmpl.generate(scene, character_name="Ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_with_explicit_generation_options(self, scene):
        install_tracking_memory(scene)
        tmpl = Attribute(name="P", attribute="personality")
        # Even with generation_options passed the early-return still fires
        result = await tmpl.generate(
            scene,
            character_name="Ghost",
            generation_options=GenerationOptions(),
        )
        assert result is None


class TestDetailGenerateMissingCharacter:
    @pytest.mark.asyncio
    async def test_returns_none_when_character_not_found(self, scene):
        install_tracking_memory(scene)
        tmpl = Detail(name="H", detail="hometown")
        result = await tmpl.generate(scene, character_name="Ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_with_explicit_generation_options(self, scene):
        install_tracking_memory(scene)
        tmpl = Detail(name="H", detail="hometown")
        result = await tmpl.generate(
            scene,
            character_name="Ghost",
            generation_options=GenerationOptions(),
        )
        assert result is None


# ---------------------------------------------------------------------------
# Template.formatted() applied to Attribute/Detail
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# generate() - happy-path with stubbed creator response
#
# Note: we are not mocking `Attribute.generate` itself (the function under
# test). We are stubbing the *external dependency* `creator.contextual_
# generate_from_args` because that method is the LLM call. The post-LLM
# branches (apply=True writes to character; apply=False does not; the
# GeneratedAttribute is shaped correctly) are real test code that runs.
# ---------------------------------------------------------------------------


class TestAttributeGenerateApply:
    @pytest.mark.asyncio
    async def test_apply_false_does_not_write_attribute(self, scene):
        ch = make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "Brave and curious.")

        tmpl = Attribute(name="P", attribute="personality")
        result = await tmpl.generate(scene, character_name="Alice", apply=False)

        assert isinstance(result, GeneratedAttribute)
        assert result.value == "Brave and curious."
        assert result.character == "Alice"
        assert result.attribute == "personality"
        assert "personality" not in ch.base_attributes

    @pytest.mark.asyncio
    async def test_apply_true_writes_attribute(self, scene):
        ch = make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "Brave and curious.")

        tmpl = Attribute(name="P", attribute="personality")
        result = await tmpl.generate(scene, character_name="Alice", apply=True)

        assert ch.base_attributes["personality"] == "Brave and curious."
        assert result.value == "Brave and curious."

    @pytest.mark.asyncio
    async def test_attribute_name_formatted_with_character(self, scene):
        ch = make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "warrior")

        tmpl = Attribute(name="P", attribute="{character_name}_role")
        result = await tmpl.generate(scene, character_name="Alice", apply=True)

        assert result.attribute == "Alice_role"
        assert ch.base_attributes["Alice_role"] == "warrior"

    @pytest.mark.asyncio
    async def test_explicit_generation_options(self, scene):
        make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "x")
        tmpl = Attribute(name="P", attribute="personality")
        result = await tmpl.generate(
            scene,
            character_name="Alice",
            apply=False,
            generation_options=GenerationOptions(),
        )
        assert result.value == "x"


class TestDetailGenerateApply:
    @pytest.mark.asyncio
    async def test_apply_false_does_not_write_detail(self, scene):
        ch = make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "Eldoria")

        tmpl = Detail(name="H", detail="hometown")
        result = await tmpl.generate(scene, character_name="Alice", apply=False)

        assert isinstance(result, GeneratedDetail)
        assert result.value == "Eldoria"
        assert result.character == "Alice"
        assert result.detail == "hometown"
        assert "hometown" not in ch.details

    @pytest.mark.asyncio
    async def test_apply_true_writes_detail(self, scene):
        ch = make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "Eldoria")

        tmpl = Detail(name="H", detail="hometown")
        await tmpl.generate(scene, character_name="Alice", apply=True)
        assert ch.details["hometown"] == "Eldoria"

    @pytest.mark.asyncio
    async def test_detail_name_formatted_with_character(self, scene):
        ch = make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "Eldoria")

        tmpl = Detail(name="H", detail="{character_name}_home")
        result = await tmpl.generate(scene, character_name="Alice", apply=True)
        assert result.detail == "Alice_home"
        assert ch.details["Alice_home"] == "Eldoria"

    @pytest.mark.asyncio
    async def test_explicit_generation_options(self, scene):
        make_actor(scene, "Alice")
        install_tracking_memory(scene)
        _stub_creator_response(scene, "x")
        tmpl = Detail(name="H", detail="hometown")
        result = await tmpl.generate(
            scene,
            character_name="Alice",
            apply=False,
            generation_options=GenerationOptions(),
        )
        assert result.value == "x"


class TestTemplateFormattedOnCharacterTemplates:
    def test_attribute_format_interpolates_character_name(self, scene):
        make_actor(scene, "Alice")
        tmpl = Attribute(
            name="X",
            attribute="{character_name}_role",
            instructions="What is {character_name}'s role?",
        )
        assert tmpl.formatted("attribute", scene, "Alice") == "Alice_role"
        assert (
            tmpl.formatted("instructions", scene, "Alice")
            == "What is Alice's role?"
        )

    def test_detail_format_interpolates_character_name(self, scene):
        make_actor(scene, "Alice")
        tmpl = Detail(
            name="X",
            detail="{character_name}_home",
            instructions="Where is {character_name} from?",
        )
        assert tmpl.formatted("detail", scene, "Alice") == "Alice_home"
        assert (
            tmpl.formatted("instructions", scene, "Alice")
            == "Where is Alice from?"
        )

    def test_attribute_format_returns_none_for_missing_instructions(self, scene):
        tmpl = Attribute(name="X", attribute="role", instructions=None)
        # None -> returned unchanged
        assert tmpl.formatted("instructions", scene, "Alice") is None

    def test_detail_format_returns_none_for_missing_instructions(self, scene):
        tmpl = Detail(name="X", detail="role", instructions=None)
        assert tmpl.formatted("instructions", scene, "Alice") is None
