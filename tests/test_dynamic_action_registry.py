"""Tests for the dynamic action registry framework on ``Agent``.

These tests document the framework contract independently of the TTS
agent specialization. The contract is exercised through a minimal
in-test ``Agent`` subclass that declares a single registry action.

External integrations (LLM clients, websockets) are not used: agents
are instantiated directly.
"""

from __future__ import annotations

import asyncio
import functools
import json

import pytest

from talemate.agents.base import (
    Agent,
    AgentAction,
    AgentActionConfig,
    DYNAMIC_CHILDREN_FIELD,
)
from talemate.util import slugify


# ---------------------------------------------------------------------------
# Test agent: minimal subclass with a single registry action
# ---------------------------------------------------------------------------


REGISTRY_KEY = "test_registry"


class _DynamicTestAgent(Agent):
    """Minimal Agent that declares a single dynamic-children registry.

    Lifecycle hooks accumulate ``(slug, label)`` tuples on instance lists so
    tests can assert which hooks fired and in what order.
    """

    agent_type = "dynamic-test"
    requires_llm_client = False

    def __init__(self):
        self.actions = self._build_actions()
        self.added: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.renamed: list[tuple[str, str]] = []

    @staticmethod
    def _build_actions() -> dict[str, AgentAction]:
        return {
            REGISTRY_KEY: AgentAction(
                enabled=True,
                container=True,
                label="Test Registry",
                config={
                    DYNAMIC_CHILDREN_FIELD: AgentActionConfig(
                        type="blob",
                        value="[]",
                        label="Registered children",
                    ),
                },
            ),
            # An ordinary action *without* a dynamic_children field — used to
            # verify the registry detector doesn't false-positive.
            "static_action": AgentAction(
                enabled=True,
                label="Static",
                config={
                    "value": AgentActionConfig(
                        type="text",
                        value="ordinary",
                        label="Value",
                    ),
                },
            ),
        }

    def dynamic_action_factory(
        self, registry_key: str, slug: str, label: str
    ) -> AgentAction:
        if registry_key != REGISTRY_KEY:
            return super().dynamic_action_factory(registry_key, slug, label)
        return AgentAction(
            enabled=True,
            label=label,
            parent_key=registry_key,
            config={
                "value": AgentActionConfig(
                    type="text",
                    value="default",
                    label="Value",
                ),
            },
        )

    # Per-child helpers used to test dynamic_attr / dynamic_method.
    def _test_registry_value(self, slug: str) -> str:
        return self.actions[slug].config["value"].value

    def _test_registry_doubled(self, slug: str, n: int) -> str:
        # Method (vs property): takes additional args.
        return f"{slug}-{n * 2}"

    # Lifecycle spy hooks
    def on_dynamic_child_added(
        self, registry_key: str, slug: str, label: str
    ) -> None:
        self.added.append((slug, label))

    def on_dynamic_child_removed(self, registry_key: str, slug: str) -> None:
        self.removed.append(slug)

    def on_dynamic_child_renamed(
        self, registry_key: str, slug: str, label: str
    ) -> None:
        self.renamed.append((slug, label))


@pytest.fixture
def agent() -> _DynamicTestAgent:
    return _DynamicTestAgent()


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic_ascii_lowercased(self):
        assert slugify("Hello") == "hello"

    def test_multi_space_collapses_to_single_dash(self):
        assert slugify("hello   world") == "hello-world"

    def test_leading_and_trailing_non_alpha_stripped(self):
        assert slugify("  hello world  ") == "hello-world"
        assert slugify("---hello---") == "hello"

    def test_empty_input_returns_empty_string(self):
        assert slugify("") == ""

    def test_none_input_returns_empty_string(self):
        # Function tolerates None-like input via ``or ""``
        assert slugify(None) == ""  # type: ignore[arg-type]

    def test_non_alpha_runs_collapse_to_single_dash(self):
        assert slugify("a__b...c!!d") == "a-b-c-d"

    def test_unicode_punctuation_falls_through_to_dashes(self):
        # Non-ASCII letters are not in [a-z0-9] and become dashes.
        assert slugify("café-OSR") == "caf-osr"

    def test_digits_preserved(self):
        assert slugify("Backend 42") == "backend-42"


# ---------------------------------------------------------------------------
# is_dynamic_registry / dynamic_registry_keys
# ---------------------------------------------------------------------------


class TestRegistryDetection:
    def test_detects_action_with_dynamic_children_field(self, agent):
        assert agent.is_dynamic_registry(REGISTRY_KEY) is True

    def test_does_not_detect_ordinary_action(self, agent):
        assert agent.is_dynamic_registry("static_action") is False

    def test_unknown_action_is_not_a_registry(self, agent):
        assert agent.is_dynamic_registry("nonexistent") is False

    def test_dynamic_registry_keys_lists_only_registries(self, agent):
        assert agent.dynamic_registry_keys() == [REGISTRY_KEY]


# ---------------------------------------------------------------------------
# dynamic_children_entries / dynamic_child_slugs
# ---------------------------------------------------------------------------


class TestDynamicChildrenEntries:
    def test_empty_blob_returns_empty_list(self, agent):
        assert agent.dynamic_children_entries(REGISTRY_KEY) == []
        assert agent.dynamic_child_slugs(REGISTRY_KEY) == []

    def test_garbage_json_returns_empty_with_warning(self, agent, caplog):
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = "{not json"
        # Should not raise — just return [] and log a warning.
        assert agent.dynamic_children_entries(REGISTRY_KEY) == []
        assert agent.dynamic_child_slugs(REGISTRY_KEY) == []

    def test_valid_blob_round_trips(self, agent):
        entries = [
            {"slug": "alpha", "label": "Alpha"},
            {"slug": "beta", "label": "Beta"},
        ]
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps(entries)
        assert agent.dynamic_children_entries(REGISTRY_KEY) == entries
        assert agent.dynamic_child_slugs(REGISTRY_KEY) == ["alpha", "beta"]

    def test_entries_without_slug_filtered_out(self, agent):
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps(
            [
                {"slug": "alpha", "label": "Alpha"},
                {"label": "Missing slug"},
                {"slug": "", "label": "Empty"},
            ]
        )
        slugs = agent.dynamic_child_slugs(REGISTRY_KEY)
        assert slugs == ["alpha"]

    def test_returns_empty_for_non_registry_action(self, agent):
        # static_action has no dynamic_children field — should be treated as
        # if it had no entries, never raise.
        assert agent.dynamic_children_entries("static_action") == []
        assert agent.dynamic_child_slugs("static_action") == []


# ---------------------------------------------------------------------------
# install_dynamic_children
# ---------------------------------------------------------------------------


class TestInstallDynamicChildren:
    def test_synthesizes_one_action_per_entry(self, agent):
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps(
            [
                {"slug": "alpha", "label": "Alpha"},
                {"slug": "beta", "label": "Beta"},
            ]
        )
        agent.install_dynamic_children(REGISTRY_KEY)
        assert "alpha" in agent.actions
        assert "beta" in agent.actions
        assert agent.actions["alpha"].label == "Alpha"
        assert agent.actions["beta"].label == "Beta"

    def test_installed_children_carry_parent_key(self, agent):
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps([{"slug": "alpha", "label": "Alpha"}])
        agent.install_dynamic_children(REGISTRY_KEY)
        assert agent.actions["alpha"].parent_key == REGISTRY_KEY

    def test_idempotent_second_call_is_a_no_op(self, agent):
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps([{"slug": "alpha", "label": "Alpha"}])
        agent.install_dynamic_children(REGISTRY_KEY)
        first = agent.actions["alpha"]
        # Mutate a child config value and re-install — value must survive,
        # the action object itself must be the same instance.
        agent.actions["alpha"].config["value"].value = "user-edited"
        agent.install_dynamic_children(REGISTRY_KEY)
        assert agent.actions["alpha"] is first
        assert agent.actions["alpha"].config["value"].value == "user-edited"

    def test_stale_entries_pruned_when_blob_shrinks(self, agent):
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps(
            [
                {"slug": "alpha", "label": "Alpha"},
                {"slug": "beta", "label": "Beta"},
            ]
        )
        agent.install_dynamic_children(REGISTRY_KEY)
        assert "beta" in agent.actions

        # Remove "beta" from the blob and re-install — it should be dropped.
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps([{"slug": "alpha", "label": "Alpha"}])
        agent.install_dynamic_children(REGISTRY_KEY)
        assert "alpha" in agent.actions
        assert "beta" not in agent.actions

    def test_does_not_remove_unrelated_actions(self, agent):
        # Static action exists; install should not touch it.
        agent.actions[REGISTRY_KEY].config[
            DYNAMIC_CHILDREN_FIELD
        ].value = json.dumps([{"slug": "alpha", "label": "Alpha"}])
        agent.install_dynamic_children(REGISTRY_KEY)
        assert "static_action" in agent.actions

    def test_install_on_non_registry_is_no_op(self, agent):
        before = dict(agent.actions)
        agent.install_dynamic_children("static_action")
        assert agent.actions == before


# ---------------------------------------------------------------------------
# register_dynamic_child / unregister_dynamic_child
# ---------------------------------------------------------------------------


class TestRegisterDynamicChild:
    def test_appends_entry_to_blob(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        entries = agent.dynamic_children_entries(REGISTRY_KEY)
        assert entries == [{"slug": "alpha", "label": "Alpha"}]

    def test_installs_synthesized_child(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        assert "alpha" in agent.actions
        assert agent.actions["alpha"].parent_key == REGISTRY_KEY

    def test_fires_on_dynamic_child_added(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        assert agent.added == [("alpha", "Alpha")]

    def test_falls_back_to_slug_when_label_empty(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "")
        # Stored entry's label falls back to the slug
        entries = agent.dynamic_children_entries(REGISTRY_KEY)
        assert entries == [{"slug": "alpha", "label": "alpha"}]
        assert agent.added == [("alpha", "alpha")]

    def test_rejects_empty_slug(self, agent):
        with pytest.raises(ValueError):
            agent.register_dynamic_child(REGISTRY_KEY, "", "Whatever")

    def test_rejects_duplicate_slug(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        with pytest.raises(ValueError):
            agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha 2")

    def test_rejects_non_registry_key(self, agent):
        with pytest.raises(ValueError):
            agent.register_dynamic_child("static_action", "alpha", "Alpha")

    def test_rejects_reserved_slug(self, agent, monkeypatch):
        # Patch reserved_slugs_for_registry on this instance to claim "alpha".
        monkeypatch.setattr(
            agent, "reserved_slugs_for_registry", lambda key: {"alpha"}
        )
        with pytest.raises(ValueError, match="reserved"):
            agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")


class TestUnregisterDynamicChild:
    def test_removes_entry_from_blob(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.unregister_dynamic_child(REGISTRY_KEY, "alpha")
        assert agent.dynamic_children_entries(REGISTRY_KEY) == []

    def test_removes_synthesized_action(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        assert "alpha" in agent.actions
        agent.unregister_dynamic_child(REGISTRY_KEY, "alpha")
        assert "alpha" not in agent.actions

    def test_fires_on_dynamic_child_removed(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.unregister_dynamic_child(REGISTRY_KEY, "alpha")
        assert agent.removed == ["alpha"]

    def test_unknown_slug_is_a_silent_no_op(self, agent):
        # No registered slug of "ghost" — no exception, no side effect.
        agent.unregister_dynamic_child(REGISTRY_KEY, "ghost")
        assert agent.removed == []
        assert agent.dynamic_children_entries(REGISTRY_KEY) == []

    def test_rejects_non_registry_key(self, agent):
        with pytest.raises(ValueError):
            agent.unregister_dynamic_child("static_action", "alpha")


# ---------------------------------------------------------------------------
# rename_dynamic_child_label
# ---------------------------------------------------------------------------


class TestRenameDynamicChildLabel:
    def test_updates_blob_entry_label(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.rename_dynamic_child_label(REGISTRY_KEY, "alpha", "Alpha v2")
        entries = agent.dynamic_children_entries(REGISTRY_KEY)
        assert entries == [{"slug": "alpha", "label": "Alpha v2"}]

    def test_updates_synthesized_action_label(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.rename_dynamic_child_label(REGISTRY_KEY, "alpha", "Alpha v2")
        assert agent.actions["alpha"].label == "Alpha v2"

    def test_slug_does_not_change(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.rename_dynamic_child_label(REGISTRY_KEY, "alpha", "Alpha v2")
        # Slug stays frozen — only label changes.
        assert "alpha" in agent.actions
        assert agent.dynamic_child_slugs(REGISTRY_KEY) == ["alpha"]

    def test_fires_on_dynamic_child_renamed(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.rename_dynamic_child_label(REGISTRY_KEY, "alpha", "Alpha v2")
        assert agent.renamed == [("alpha", "Alpha v2")]

    def test_unknown_slug_is_a_silent_no_op(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.rename_dynamic_child_label(REGISTRY_KEY, "ghost", "Ghost")
        # Hook not fired; existing entry unchanged.
        assert agent.renamed == []
        entries = agent.dynamic_children_entries(REGISTRY_KEY)
        assert entries == [{"slug": "alpha", "label": "Alpha"}]

    def test_empty_label_falls_back_to_slug(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.rename_dynamic_child_label(REGISTRY_KEY, "alpha", "")
        entries = agent.dynamic_children_entries(REGISTRY_KEY)
        assert entries == [{"slug": "alpha", "label": "alpha"}]
        assert agent.actions["alpha"].label == "alpha"

    def test_rejects_non_registry_key(self, agent):
        with pytest.raises(ValueError):
            agent.rename_dynamic_child_label("static_action", "alpha", "Alpha")


# ---------------------------------------------------------------------------
# dynamic_attr / dynamic_method
# ---------------------------------------------------------------------------


class TestDynamicResolvers:
    def test_dynamic_attr_dispatches_via_underscored_helper(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        agent.actions["alpha"].config["value"].value = "hello"
        # _test_registry_value("alpha") -> "hello"
        assert agent.dynamic_attr(REGISTRY_KEY, "alpha", "value") == "hello"

    def test_dynamic_attr_returns_default_when_helper_missing(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        assert (
            agent.dynamic_attr(
                REGISTRY_KEY, "alpha", "nonexistent_helper", default="fallback"
            )
            == "fallback"
        )

    def test_dynamic_method_returns_partial_prebound_to_slug(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        method = agent.dynamic_method(REGISTRY_KEY, "alpha", "doubled")
        assert isinstance(method, functools.partial)
        # Invoke without supplying slug — partial pre-bound it.
        assert method(3) == "alpha-6"

    def test_dynamic_method_returns_default_when_helper_missing(self, agent):
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        assert (
            agent.dynamic_method(
                REGISTRY_KEY, "alpha", "nonexistent_method", default="fallback"
            )
            == "fallback"
        )


# ---------------------------------------------------------------------------
# apply_config pre-pass
# ---------------------------------------------------------------------------


class TestApplyConfigPrePass:
    def test_synthesizes_children_from_saved_blob(self, agent):
        # Saved kwargs as they'd come from the persisted config — a
        # dynamic_children blob plus per-child config values.
        saved_kwargs = {
            "actions": {
                REGISTRY_KEY: {
                    "enabled": True,
                    "config": {
                        DYNAMIC_CHILDREN_FIELD: {
                            "value": json.dumps(
                                [{"slug": "alpha", "label": "Alpha Saved"}]
                            ),
                        },
                    },
                },
                "alpha": {
                    "enabled": True,
                    "config": {
                        "value": {"value": "restored-from-disk"},
                    },
                },
            }
        }
        asyncio.run(agent.apply_config(**saved_kwargs))

        # Child action was synthesized
        assert "alpha" in agent.actions
        assert agent.actions["alpha"].parent_key == REGISTRY_KEY
        assert agent.actions["alpha"].label == "Alpha Saved"

    def test_per_child_config_values_applied(self, agent):
        saved_kwargs = {
            "actions": {
                REGISTRY_KEY: {
                    "enabled": True,
                    "config": {
                        DYNAMIC_CHILDREN_FIELD: {
                            "value": json.dumps(
                                [{"slug": "alpha", "label": "Alpha"}]
                            ),
                        },
                    },
                },
                "alpha": {
                    "enabled": True,
                    "config": {
                        "value": {"value": "restored-from-disk"},
                    },
                },
            }
        }
        asyncio.run(agent.apply_config(**saved_kwargs))
        assert (
            agent.actions["alpha"].config["value"].value
            == "restored-from-disk"
        )

    def test_blob_value_restored_before_synthesis(self, agent):
        saved_kwargs = {
            "actions": {
                REGISTRY_KEY: {
                    "enabled": True,
                    "config": {
                        DYNAMIC_CHILDREN_FIELD: {
                            "value": json.dumps(
                                [
                                    {"slug": "alpha", "label": "Alpha"},
                                    {"slug": "beta", "label": "Beta"},
                                ]
                            ),
                        },
                    },
                },
            }
        }
        asyncio.run(agent.apply_config(**saved_kwargs))
        # Both children synthesized from the restored blob
        assert "alpha" in agent.actions
        assert "beta" in agent.actions
        assert agent.dynamic_child_slugs(REGISTRY_KEY) == ["alpha", "beta"]

    def test_no_blob_in_kwargs_leaves_existing_state(self, agent):
        # Pre-populate an entry, then call apply_config with no actions data.
        agent.register_dynamic_child(REGISTRY_KEY, "alpha", "Alpha")
        asyncio.run(agent.apply_config())  # no kwargs at all
        # apply_config only mutates state when kwargs["actions"] is present.
        assert "alpha" in agent.actions
