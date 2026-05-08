"""
Baseline snapshot tests for shared/common prompt utilities that render
without going through an agent — currently the `render_game_state` Jinja
global and its underlying `gamestate-context-path.jinja2` template.

These tests render templates directly via `Prompt.get` / `Prompt.from_text`
and snapshot the output. Run with --update-baselines to create/update.
"""

from unittest.mock import Mock

from talemate.context import active_scene
from talemate.prompts.base import Prompt
from talemate.util.path import get_path_value


AGENT = "common"


def _render_template(
    value,
    *,
    path="player/status",
    title="GAMESTATE",
    max_gamestate_tokens=1024,
    data_format="json",
) -> str:
    prompt = Prompt.get(
        "gamestate-context-path",
        vars={
            "path": path,
            "title": title,
            "value": value,
            "max_gamestate_tokens": max_gamestate_tokens,
        },
    )
    prompt.data_format_type = data_format
    return prompt.render()


def _render_via_global(
    template_text: str,
    *,
    scene_variables: dict | None = None,
    vars: dict | None = None,
    client=None,
) -> str:
    """Render a template that calls `render_game_state(...)`.

    `scene_variables`, when provided, populates `active_scene.game_state.variables`
    for the duration of the render — that is the SoT the helper reads from.
    `vars` is forwarded to the prompt for any other template needs (rarely used
    here since `render_game_state` no longer reads vars).
    """
    prompt = Prompt.from_text(template_text, vars=vars or {})
    if client is not None:
        prompt.client = client

    if scene_variables is None:
        return prompt.render()

    scene = Mock()
    scene.game_state = Mock()
    scene.game_state.variables = scene_variables
    token = active_scene.set(scene)
    try:
        return prompt.render()
    finally:
        active_scene.reset(token)


def _client_with_format(data_format: str) -> Mock:
    """Minimal client mock that exposes only what the rendering layer reads."""
    client = Mock()
    client.data_format = data_format
    client.can_be_coerced = True
    client.reason_enabled = False
    return client


# ---------------------------------------------------------------------------
# Direct template snapshots: gamestate-context-path.jinja2
# ---------------------------------------------------------------------------


class TestGamestatePathTemplate:
    """Snapshot the rendered output of gamestate-context-path.jinja2 directly."""

    def test_json_dict(self, baseline_checker):
        out = _render_template({"hp": 12, "mana": 5})
        baseline_checker(out, AGENT, "gamestate_path_json_dict")

    def test_yaml_dict(self, baseline_checker):
        out = _render_template({"hp": 12, "mana": 5}, data_format="yaml")
        baseline_checker(out, AGENT, "gamestate_path_yaml_dict")

    def test_scalar_value(self, baseline_checker):
        out = _render_template("ready", path="player/state")
        baseline_checker(out, AGENT, "gamestate_path_scalar_value")

    def test_list_value(self, baseline_checker):
        out = _render_template(["sword", "potion", "key"], path="inventory")
        baseline_checker(out, AGENT, "gamestate_path_list_value")

    def test_missing_value(self, baseline_checker):
        # value=None means the path didn't resolve — must render empty.
        out = _render_template(None, path="player/missing")
        baseline_checker(out, AGENT, "gamestate_path_missing_value")

    def test_custom_title(self, baseline_checker):
        out = _render_template({"hp": 12}, path="player/status", title="PLAYER_STATUS")
        baseline_checker(out, AGENT, "gamestate_path_custom_title")

    def test_budget_exceeded(self, baseline_checker):
        # Tiny budget — large value gets dropped.
        big = {f"item_{i}": "x" * 50 for i in range(40)}
        out = _render_template(big, path="inventory", max_gamestate_tokens=10)
        baseline_checker(out, AGENT, "gamestate_path_budget_exceeded")

    def test_budget_zero_means_unlimited(self, baseline_checker):
        # max_gamestate_tokens=0 is falsy → bypass the cap, always render.
        out = _render_template({"hp": 12}, max_gamestate_tokens=0)
        baseline_checker(out, AGENT, "gamestate_path_budget_zero")


# ---------------------------------------------------------------------------
# End-to-end snapshots: {{ render_game_state(...) }} via Jinja global
# ---------------------------------------------------------------------------


class TestRenderGameStateGlobal:
    """Snapshot the full chain: outer template → render_game_state global
    → active_scene.game_state.variables → get_path_value → sub-Prompt render.

    `render_game_state` reads strictly from the SoT (active scene's game state)
    and ignores any `gamestate` value passed via prompt vars."""

    def test_resolves_nested_path(self, baseline_checker):
        out = _render_via_global(
            'BEFORE\n{{ render_game_state("player/status") }}\nAFTER',
            scene_variables={"player": {"status": {"hp": 12, "mana": 5}}},
        )
        baseline_checker(out, AGENT, "gamestate_path_global_resolves")

    def test_resolves_single_segment_path(self, baseline_checker):
        # Top-level key with no slashes — common shape for flat gamestates.
        out = _render_via_global(
            '{{ render_game_state("player_stats") }}',
            scene_variables={"player_stats": {"hp": 12, "mana": 5}},
        )
        baseline_checker(out, AGENT, "gamestate_path_global_single_segment")

    def test_missing_path_renders_empty(self, baseline_checker):
        out = _render_via_global(
            'BEFORE\n{{ render_game_state("nope/missing") }}\nAFTER',
            scene_variables={"player": {"hp": 12}},
        )
        baseline_checker(out, AGENT, "gamestate_path_global_missing")

    def test_no_active_scene_renders_empty(self, baseline_checker):
        # No scene set + no scene_variables → renders empty, no crash.
        out = _render_via_global(
            'BEFORE\n{{ render_game_state("player") }}\nAFTER',
        )
        baseline_checker(out, AGENT, "gamestate_path_global_no_gamestate_var")

    def test_custom_title(self, baseline_checker):
        out = _render_via_global(
            '{{ render_game_state("player/status", title="PLAYER") }}',
            scene_variables={"player": {"status": {"hp": 12}}},
        )
        baseline_checker(out, AGENT, "gamestate_path_global_custom_title")

    def test_leading_slash_tolerated(self, baseline_checker):
        out = _render_via_global(
            '{{ render_game_state("/player/status") }}',
            scene_variables={"player": {"status": {"hp": 12}}},
        )
        baseline_checker(out, AGENT, "gamestate_path_global_leading_slash")

    def test_intermediate_non_dict_renders_empty(self, baseline_checker):
        # 'world' is a scalar — can't traverse into it, so the path is
        # treated as unresolvable and the section is omitted.
        out = _render_via_global(
            'BEFORE\n{{ render_game_state("world/inner") }}\nAFTER',
            scene_variables={"world": "flat"},
        )
        baseline_checker(out, AGENT, "gamestate_path_global_intermediate_non_dict")

    def test_reads_from_active_scene(self, baseline_checker):
        # The canonical case: the user puts `{{ render_game_state("vegu_status") }}`
        # in a custom override (e.g. dialogue.jinja2) and the helper resolves
        # against scene.game_state.variables without any bridging boilerplate.
        out = _render_via_global(
            '{{ render_game_state("vegu_status") }}',
            scene_variables={
                "vegu_status": {"time": "17:00", "health": "normal"},
            },
        )
        baseline_checker(out, AGENT, "gamestate_path_global_active_scene_fallback")

    def test_vars_gamestate_is_ignored(self, baseline_checker):
        # Even if a caller passes a `gamestate` var, the SoT (active scene's
        # game state) still wins. This guarantees rendered output cannot
        # diverge from the live state.
        out = _render_via_global(
            '{{ render_game_state("vegu_status") }}',
            scene_variables={"vegu_status": {"from": "scene"}},
            vars={"gamestate": {"vegu_status": {"from": "vars"}}},
        )
        baseline_checker(out, AGENT, "gamestate_path_global_vars_ignored")

    def test_yaml_format_propagates_from_client(self, baseline_checker):
        # When the parent prompt has a client with data_format='yaml', the
        # sub-Prompt created by render_game_state must inherit it so the
        # block renders as YAML rather than the json default.
        out = _render_via_global(
            '{{ render_game_state("player/status") }}',
            scene_variables={"player": {"status": {"hp": 12, "mana": 5}}},
            client=_client_with_format("yaml"),
        )
        baseline_checker(out, AGENT, "gamestate_path_global_yaml_via_client")

    def test_json_format_propagates_from_client(self, baseline_checker):
        # Sanity counterpart: an explicit json client renders json (matching
        # the default) — locks in that the propagation path is exercised in
        # both directions, not just yaml.
        out = _render_via_global(
            '{{ render_game_state("player/status") }}',
            scene_variables={"player": {"status": {"hp": 12, "mana": 5}}},
            client=_client_with_format("json"),
        )
        baseline_checker(out, AGENT, "gamestate_path_global_json_via_client")


# ---------------------------------------------------------------------------
# Plain assertions for get_path_value (the helper backing render_game_state).
# Edge cases are easier to read as direct asserts than as snapshot files.
# ---------------------------------------------------------------------------


class TestGetPathValue:
    GS = {
        "player": {"status": {"hp": 10, "mana": 4}, "name": "Vegu"},
        "world": "flat",  # scalar mid-path
        "inventory": ["sword", "potion"],
    }

    def test_resolves_nested_dict(self):
        assert get_path_value(self.GS, "player/status") == {"hp": 10, "mana": 4}

    def test_resolves_leaf_scalar(self):
        assert get_path_value(self.GS, "player/status/hp") == 10

    def test_resolves_top_level_scalar(self):
        assert get_path_value(self.GS, "world") == "flat"

    def test_resolves_top_level_list(self):
        assert get_path_value(self.GS, "inventory") == ["sword", "potion"]

    def test_missing_segment_returns_default(self):
        assert get_path_value(self.GS, "player/missing") is None
        assert get_path_value(self.GS, "player/missing", default="-") == "-"

    def test_non_dict_intermediate_returns_default(self):
        # 'world' is a string; can't walk into it.
        assert get_path_value(self.GS, "world/inner") is None
        assert get_path_value(self.GS, "world/inner", default="-") == "-"

    def test_leading_and_trailing_slashes_tolerated(self):
        assert get_path_value(self.GS, "/player/name") == "Vegu"
        assert get_path_value(self.GS, "player/name/") == "Vegu"

    def test_none_container_returns_default(self):
        assert get_path_value(None, "player") is None
        assert get_path_value(None, "player", default="-") == "-"

    def test_empty_path_returns_default(self):
        assert get_path_value(self.GS, "") is None
        assert get_path_value(self.GS, "/") is None
