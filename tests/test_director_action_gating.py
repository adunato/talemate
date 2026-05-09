"""Unit tests for talemate.agents.director.action_core.gating.

Tests are written against the public API (CallbackDescriptor,
is_action_id_enabled, get_disabled_action_ids, extract_callback_descriptors,
extract_all_callback_descriptors, get_all_callback_choices) plus internal
graph-extraction helpers via real DirectorChatAction graphs that we build
in-memory.

LLM-dependent paths are not exercised — extraction, gating and choice-builder
logic are pure data transforms that can be tested without an LLM client.
"""

from __future__ import annotations

import pytest

import talemate.instance as instance
from conftest import MockScene, bootstrap_scene
from talemate.agents.director import DirectorAgent
from talemate.agents.director.action_core.gating import (
    CallbackDescriptor,
    extract_callback_descriptors,
    extract_all_callback_descriptors,
    get_all_callback_choices,
    get_disabled_action_ids,
    is_action_id_enabled,
)
from talemate.game.engine.nodes.core import Graph
from talemate.game.engine.nodes.registry import (
    NODES,
    import_talemate_node_definitions,
)


# ---------------------------------------------------------------------------
# Session-scoped node registry import (DirectorChatAction/SubAction nodes)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _import_node_definitions():
    """Ensure DirectorChatAction / DirectorChatSubAction node classes are registered."""
    import_talemate_node_definitions()


# ---------------------------------------------------------------------------
# Real DirectorAgent fixture — gating reads `get_scene_state("disabled_sub_actions")`
# off the agent's bound scene. We bootstrap a real scene + real director and
# write the disabled-actions config directly into `scene.agent_state["director"]`,
# the same place production stores it.
# ---------------------------------------------------------------------------


@pytest.fixture
def director() -> DirectorAgent:
    """Bootstrap a real Scene + DirectorAgent and return the director."""
    bootstrap_scene(MockScene())
    return instance.get_agent("director")


def _set_disabled(director: DirectorAgent, value) -> None:
    """Write `disabled_sub_actions` into the real director's scene state.

    Production reads via ``get_scene_state("disabled_sub_actions")`` which
    is backed by ``self.scene.agent_state[self.agent_type]``. We poke the
    same dict (rather than calling ``set_scene_states``) because the
    gating tests verify defensive branches where this value is the wrong
    type entirely — ``set_scene_states`` would still accept it but using
    the underlying dict makes the test's intent obvious.
    """
    if value is None:
        director.scene.agent_state.pop("director", None)
        return
    director.scene.agent_state["director"] = {"disabled_sub_actions": value}


def _desc(
    action_id: str,
    *,
    availability: str = "both",
    force_enabled: bool = False,
    action_title: str = "",
    group: str = "",
    description_chat: str = "",
    description_scene_direction: str = "",
    instruction_examples=None,
) -> CallbackDescriptor:
    """Helper to build a CallbackDescriptor without verbosity in each test."""
    return CallbackDescriptor(
        action_id=action_id,
        action_title=action_title,
        group=group,
        description_chat=description_chat,
        description_scene_direction=description_scene_direction,
        instruction_examples=instruction_examples or [],
        availability=availability,  # type: ignore[arg-type]
        force_enabled=force_enabled,
    )


# ---------------------------------------------------------------------------
# CallbackDescriptor.get_description
# ---------------------------------------------------------------------------


class TestCallbackDescriptorDescription:
    def test_chat_returns_chat_when_set(self):
        d = _desc("a", description_chat="chat-text", description_scene_direction="sd-text")
        assert d.get_description("chat") == "chat-text"

    def test_scene_direction_returns_sd_when_set(self):
        d = _desc("a", description_chat="chat-text", description_scene_direction="sd-text")
        assert d.get_description("scene_direction") == "sd-text"

    def test_chat_falls_back_to_scene_direction_when_chat_missing(self):
        d = _desc("a", description_chat="", description_scene_direction="sd-text")
        assert d.get_description("chat") == "sd-text"

    def test_scene_direction_falls_back_to_chat_when_sd_missing(self):
        d = _desc("a", description_chat="chat-text", description_scene_direction="")
        assert d.get_description("scene_direction") == "chat-text"

    def test_returns_empty_string_when_neither_set(self):
        d = _desc("a")
        assert d.get_description("chat") == ""
        assert d.get_description("scene_direction") == ""


# ---------------------------------------------------------------------------
# get_disabled_action_ids
# ---------------------------------------------------------------------------


class TestGetDisabledActionIds:
    def test_empty_when_no_state_set(self, director):
        assert get_disabled_action_ids("chat", director) == []

    def test_returns_mode_specific_list(self, director):
        _set_disabled(
            director,
            {
                "chat": ["a", "b"],
                "scene_direction": ["c"],
            },
        )
        assert get_disabled_action_ids("chat", director) == ["a", "b"]
        assert get_disabled_action_ids("scene_direction", director) == ["c"]

    def test_returns_empty_for_unknown_mode_key(self, director):
        _set_disabled(director, {"chat": ["a"]})
        # mode without entry → empty
        assert get_disabled_action_ids("scene_direction", director) == []

    def test_returns_empty_when_state_is_not_a_dict(self, director):
        # ill-typed state value → empty list (defensive branch)
        _set_disabled(director, ["not", "a", "dict"])
        assert get_disabled_action_ids("chat", director) == []

    def test_returns_empty_when_mode_value_is_not_a_list(self, director):
        _set_disabled(director, {"chat": "string-not-list"})
        assert get_disabled_action_ids("chat", director) == []


# ---------------------------------------------------------------------------
# is_action_id_enabled
# ---------------------------------------------------------------------------


class TestIsActionIdEnabled:
    def test_availability_both_chat_mode_enabled_when_not_in_denylist(self, director):
        d = _desc("a", availability="both")
        assert is_action_id_enabled("chat", "a", director, descriptor=d) is True

    def test_availability_chat_only_disabled_in_scene_direction(self, director):
        d = _desc("a", availability="chat")
        assert is_action_id_enabled("scene_direction", "a", director, descriptor=d) is False

    def test_availability_scene_direction_only_disabled_in_chat(self, director):
        d = _desc("a", availability="scene_direction")
        assert is_action_id_enabled("chat", "a", director, descriptor=d) is False

    def test_force_enabled_overrides_denylist(self, director):
        _set_disabled(director, {"chat": ["a"]})
        d = _desc("a", availability="both", force_enabled=True)
        # Even though "a" is on the denylist, force_enabled wins.
        assert is_action_id_enabled("chat", "a", director, descriptor=d) is True

    def test_disabled_via_denylist(self, director):
        _set_disabled(director, {"chat": ["a"]})
        d = _desc("a", availability="both", force_enabled=False)
        assert is_action_id_enabled("chat", "a", director, descriptor=d) is False

    def test_availability_check_runs_before_force_enabled(self, director):
        """availability=chat blocks scene_direction even with force_enabled=True."""
        d = _desc("a", availability="chat", force_enabled=True)
        assert is_action_id_enabled("scene_direction", "a", director, descriptor=d) is False


# ---------------------------------------------------------------------------
# Graph-based extraction helpers
# ---------------------------------------------------------------------------
#
# We build small DirectorChatAction graphs in memory by instantiating real
# node classes from the registry, wiring DirectorChatSubAction children inside,
# then exercising the extraction helpers.


def _make_director_chat_action(name: str, sub_actions: list[dict]) -> Graph:
    """Build a minimal Graph with SubAction nodes (mimics a DirectorChatAction).

    We don't instantiate the real DirectorChatAction concrete subclasses here
    because each one ships with a pre-built graph (validators, helper nodes)
    that requires a wide range of unrelated node-type imports. The gating
    extractor only inspects Graph.nodes for nodes whose registry equals the
    SubAction registry — so a vanilla Graph holding SubAction nodes is the
    minimal real fixture that exercises extraction.
    """
    SubActionCls = NODES["agents/director/chat/DirectorChatSubAction"]

    graph = Graph(title=name)
    # Tag the graph with a name property mirroring DirectorChatAction's
    # `name` property — extract_callback_descriptors uses it to match.
    graph.set_property("name", name)

    for props in sub_actions:
        sub = SubActionCls()
        for key, value in props.items():
            sub.set_property(key, value)
        graph.nodes[sub.id] = sub

    return graph


class TestExtractCallbackDescriptorsFromGraph:
    @pytest.mark.asyncio
    async def test_extracts_descriptors_with_full_properties(self):
        graph = _make_director_chat_action(
            "narrate",
            [
                {
                    "action_id": "tell",
                    "action_title": "Tell",
                    "group": "story",
                    "description_chat": "chat desc",
                    "description_scene_direction": "sd desc",
                    "availability": "chat",
                    "force_enabled": True,
                    "instruction_examples": ["ex1", "ex2"],
                },
            ],
        )

        # Hijack get_nodes_by_base_type by registering this graph in the registry
        # is unnecessary — extract_callback_descriptors iterates the registry.
        # Instead, directly use the private helper via the public extract
        # mechanism: replace the iteration target.
        from talemate.agents.director.action_core import gating as gating_mod

        descriptors = await gating_mod._extract_callbacks_from_graph(graph, "narrate")
        assert len(descriptors) == 1
        d = descriptors[0]
        assert d.action_id == "tell"
        assert d.action_title == "Tell"
        assert d.group == "story"
        assert d.description_chat == "chat desc"
        assert d.description_scene_direction == "sd desc"
        assert d.availability == "chat"
        assert d.force_enabled is True
        assert d.instruction_examples == ["ex1", "ex2"]
        assert d.parent_action_name == "narrate"

    @pytest.mark.asyncio
    async def test_skips_subactions_with_missing_action_id(self):
        graph = _make_director_chat_action(
            "narrate",
            [
                {"action_id": "good", "action_title": "Good"},
                # action_id deliberately omitted -> default ""
                {"action_id": "", "action_title": "Bad"},
            ],
        )
        from talemate.agents.director.action_core import gating as gating_mod

        descriptors = await gating_mod._extract_callbacks_from_graph(graph, "narrate")
        ids = [d.action_id for d in descriptors]
        assert ids == ["good"]

    @pytest.mark.asyncio
    async def test_normalizes_invalid_availability_to_both(self):
        graph = _make_director_chat_action(
            "narrate",
            [
                {"action_id": "x", "availability": "weird-mode"},
            ],
        )
        from talemate.agents.director.action_core import gating as gating_mod

        descriptors = await gating_mod._extract_callbacks_from_graph(graph, "narrate")
        assert descriptors[0].availability == "both"


# ---------------------------------------------------------------------------
# Registry-driven extraction (extract_callback_descriptors / extract_all_*)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_action_registry(monkeypatch):
    """Patch get_nodes_by_base_type to return a curated set of action classes."""
    from talemate.agents.director.action_core import gating as gating_mod

    actions: list[Graph] = []

    def install(*action_graphs: Graph):
        actions.clear()
        actions.extend(action_graphs)

        def _fake_get_nodes_by_base_type(base_type):
            assert base_type == "agents/director/DirectorChatAction"
            return [_node_cls_for(g) for g in actions]

        def _node_cls_for(g: Graph):
            # Closure factory so each "class" returns the prepared graph instance
            class _Cls:
                def __init__(self):
                    pass

                def __new__(cls):
                    return g

            return _Cls

        monkeypatch.setattr(
            gating_mod, "get_nodes_by_base_type", _fake_get_nodes_by_base_type
        )

    return install


class TestExtractCallbackDescriptorsByName:
    @pytest.mark.asyncio
    async def test_returns_descriptors_for_matching_action(self, fake_action_registry):
        action = _make_director_chat_action(
            "alpha",
            [{"action_id": "a1", "action_title": "A1"}],
        )
        fake_action_registry(action)

        descriptors = await extract_callback_descriptors("alpha")
        assert [d.action_id for d in descriptors] == ["a1"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_action_matches(self, fake_action_registry):
        action = _make_director_chat_action(
            "alpha",
            [{"action_id": "a1"}],
        )
        fake_action_registry(action)

        descriptors = await extract_callback_descriptors("does-not-exist")
        assert descriptors == []


class TestExtractAllCallbackDescriptors:
    @pytest.mark.asyncio
    async def test_returns_dict_keyed_by_action_name(self, fake_action_registry):
        a = _make_director_chat_action("alpha", [{"action_id": "a1"}])
        b = _make_director_chat_action("beta", [{"action_id": "b1"}, {"action_id": "b2"}])
        fake_action_registry(a, b)

        result = await extract_all_callback_descriptors()

        assert set(result.keys()) == {"alpha", "beta"}
        assert [d.action_id for d in result["alpha"]] == ["a1"]
        assert sorted(d.action_id for d in result["beta"]) == ["b1", "b2"]

    @pytest.mark.asyncio
    async def test_excludes_actions_with_no_descriptors(self, fake_action_registry):
        empty = _make_director_chat_action("empty", [])
        nonempty = _make_director_chat_action("nonempty", [{"action_id": "x"}])
        fake_action_registry(empty, nonempty)

        result = await extract_all_callback_descriptors()
        assert "empty" not in result
        assert "nonempty" in result


# ---------------------------------------------------------------------------
# get_all_callback_choices
# ---------------------------------------------------------------------------


class TestGetAllCallbackChoices:
    @pytest.mark.asyncio
    async def test_returns_unique_choices_sorted_by_label(self, fake_action_registry):
        action = _make_director_chat_action(
            "main",
            [
                {"action_id": "z", "action_title": "Zebra", "group": "Animals"},
                {"action_id": "a", "action_title": "Aardvark", "group": "Animals"},
            ],
        )
        fake_action_registry(action)

        choices = await get_all_callback_choices()

        # Sorted by label - "[Animals] Aardvark" comes before "[Animals] Zebra"
        labels = [c["label"] for c in choices]
        assert labels == ["[Animals] Aardvark", "[Animals] Zebra"]
        assert [c["value"] for c in choices] == ["a", "z"]

    @pytest.mark.asyncio
    async def test_filters_by_mode_when_provided(self, fake_action_registry):
        action = _make_director_chat_action(
            "main",
            [
                {"action_id": "chat-only", "availability": "chat"},
                {"action_id": "sd-only", "availability": "scene_direction"},
                {"action_id": "both", "availability": "both"},
            ],
        )
        fake_action_registry(action)

        chat_choices = await get_all_callback_choices(mode="chat")
        chat_ids = sorted(c["value"] for c in chat_choices)
        assert chat_ids == ["both", "chat-only"]

        sd_choices = await get_all_callback_choices(mode="scene_direction")
        sd_ids = sorted(c["value"] for c in sd_choices)
        assert sd_ids == ["both", "sd-only"]

    @pytest.mark.asyncio
    async def test_marks_locked_when_force_enabled_and_director_provided(
        self, fake_action_registry, director
    ):
        action = _make_director_chat_action(
            "main",
            [
                {"action_id": "locked", "force_enabled": True},
                {"action_id": "open", "force_enabled": False},
            ],
        )
        fake_action_registry(action)

        choices = await get_all_callback_choices(director=director)

        by_value = {c["value"]: c for c in choices}
        assert by_value["locked"].get("locked") is True
        assert "locked" not in by_value["open"]

    @pytest.mark.asyncio
    async def test_uses_action_id_as_label_when_no_title(self, fake_action_registry):
        action = _make_director_chat_action(
            "main",
            [{"action_id": "raw-id", "action_title": ""}],
        )
        fake_action_registry(action)

        choices = await get_all_callback_choices()
        assert choices[0]["label"] == "raw-id"

    @pytest.mark.asyncio
    async def test_deduplicates_repeated_action_ids(self, fake_action_registry):
        a1 = _make_director_chat_action("alpha", [{"action_id": "shared"}])
        a2 = _make_director_chat_action("beta", [{"action_id": "shared"}])
        fake_action_registry(a1, a2)

        choices = await get_all_callback_choices()
        # "shared" appears in both actions but should be returned only once
        assert sum(1 for c in choices if c["value"] == "shared") == 1
