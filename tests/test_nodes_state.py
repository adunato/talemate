"""Coverage-focused unit tests for talemate.game.engine.nodes.state.

Exercises every state/* node's `run` method through a real `GraphContext`,
including local/parent/shared/scene-loop/game scopes and the path-based
variants. Uses `Scene.game_state` (a real GameState) for the `game` scope
and an outer `GraphState` for `parent`.

LLM/agent paths are not involved here — state/* is purely a context-bound
key/value store.
"""

from __future__ import annotations

import pytest

from _node_test_helpers import apply_inputs, capture_outputs, run_node
from talemate.context import ActiveScene
from talemate.game.engine.nodes.core import GraphContext, GraphState, InputValueError
from talemate.game.engine.nodes.state import (
    ConditionalCounterState,
    ConditionalSetState,
    ConditionalUnsetState,
    CounterState,
    CounterStatePath,
    GetState,
    GetStatePath,
    HasState,
    HasStatePath,
    SetState,
    SetStatePath,
    StateManipulation,
    UnpackGameState,
    UnsetState,
    UnsetStatePath,
    coerce_to_type,
)
from talemate.tale_mate import Scene


# ---------------------------------------------------------------------------
# coerce_to_type
# ---------------------------------------------------------------------------


class TestCoerceToType:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (1, "1"),
            ("hello", "hello"),
            (1.5, "1.5"),
        ],
    )
    def test_str(self, value, expected):
        assert coerce_to_type(value, "str") == expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("3.14", 3.14),
            (1, 1.0),
        ],
    )
    def test_number(self, value, expected):
        assert coerce_to_type(value, "number") == expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("true", True),
            ("True", True),
            ("1", True),
            (1, True),
            ("false", False),
            ("0", False),
            (0, False),
            ("anything-else", False),
        ],
    )
    def test_bool(self, value, expected):
        assert coerce_to_type(value, "bool") is expected

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Cannot coerce"):
            coerce_to_type("x", "weird")


# ---------------------------------------------------------------------------
# StateManipulation.get_state_container — exercised via SetState since
# StateManipulation alone has no run().
# ---------------------------------------------------------------------------


class TestSetGetStateLocalScope:
    @pytest.mark.asyncio
    async def test_round_trip_local_scope(self):
        # Set within a single GraphContext, then immediately read back.
        with GraphContext() as state:
            setter = SetState()
            setter.set_property("name", "k")
            setter.set_property("value", "v")
            setter.set_property("scope", "local")
            await setter.run(state)
            assert state.data["k"] == "v"

            getter = GetState()
            getter.set_property("name", "k")
            getter.set_property("scope", "local")
            await getter.run(state)
            assert getter.get_output_socket("value").value == "v"
            assert getter.get_output_socket("name").value == "k"
            assert getter.get_output_socket("scope").value == "local"

    @pytest.mark.asyncio
    async def test_get_returns_default_when_unset(self):
        with GraphContext() as state:
            n = GetState()
            n.set_property("name", "missing")
            n.set_property("scope", "local")
            n.set_property("default", "fallback")
            await n.run(state)
            assert n.get_output_socket("value").value == "fallback"

    @pytest.mark.asyncio
    async def test_get_returns_none_when_default_input_unresolved(self):
        # If `default` is unresolved, GetState falls back to None.
        with GraphContext() as state:
            n = GetState()
            n.set_property("name", "x")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("value").value is None


class TestStateScopes:
    @pytest.mark.asyncio
    async def test_parent_scope_writes_to_outer_state(self):
        outer = GraphState()
        with GraphContext() as state:
            state.outer = outer
            n = SetState()
            n.set_property("name", "k")
            n.set_property("value", 1)
            n.set_property("scope", "parent")
            await n.run(state)
        assert outer.data["k"] == 1

    @pytest.mark.asyncio
    async def test_parent_scope_with_no_outer_writes_to_empty_dict(self):
        # When there is no outer state, the scope falls back to a one-shot
        # empty dict — write succeeds but is not observable. We just ensure
        # the path doesn't error.
        with GraphContext() as state:
            state.outer = None
            n = SetState()
            n.set_property("name", "k")
            n.set_property("value", 1)
            n.set_property("scope", "parent")
            await n.run(state)  # must not raise

    @pytest.mark.asyncio
    async def test_shared_scope_writes_to_state_shared(self):
        with GraphContext() as state:
            n = SetState()
            n.set_property("name", "k")
            n.set_property("value", "shared-val")
            n.set_property("scope", "shared")
            await n.run(state)
            assert state.shared["k"] == "shared-val"

    @pytest.mark.asyncio
    async def test_scene_loop_scope_with_no_loop_warns_and_uses_empty(self, caplog):
        # When there is no scene_loop in shared, the helper logs a warning and
        # uses an ephemeral dict.
        with GraphContext() as state:
            n = SetState()
            n.set_property("name", "k")
            n.set_property("value", 1)
            n.set_property("scope", "scene loop")
            await n.run(state)  # must not raise

    @pytest.mark.asyncio
    async def test_scene_loop_scope_writes_into_shared_loop(self):
        loop_ctx = {}
        with GraphContext() as state:
            state.shared["scene_loop"] = loop_ctx
            n = SetState()
            n.set_property("name", "k")
            n.set_property("value", 9)
            n.set_property("scope", "scene loop")
            await n.run(state)
        assert loop_ctx == {"k": 9}

    @pytest.mark.asyncio
    async def test_game_scope_writes_to_scene_game_state(self):
        scene = Scene()
        with ActiveScene(scene):
            with GraphContext() as state:
                n = SetState()
                n.set_property("name", "k")
                n.set_property("value", 42)
                n.set_property("scope", "game")
                await n.run(state)
        # GameState supports __setitem__ and __getitem__
        assert scene.game_state["k"] == 42

    @pytest.mark.asyncio
    async def test_unknown_scope_raises(self):
        with GraphContext() as state:
            n = SetState()
            n.set_property("name", "k")
            n.set_property("value", 1)
            n.set_property("scope", "weird-scope")
            with pytest.raises(InputValueError):
                await n.run(state)


# ---------------------------------------------------------------------------
# UnsetState / HasState
# ---------------------------------------------------------------------------


class TestUnsetState:
    @pytest.mark.asyncio
    async def test_pops_and_returns_value(self):
        with GraphContext() as state:
            state.data["k"] = "v"
            n = UnsetState()
            n.set_property("name", "k")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("value").value == "v"
        assert "k" not in state.data

    @pytest.mark.asyncio
    async def test_missing_key_returns_none_value(self):
        with GraphContext() as state:
            n = UnsetState()
            n.set_property("name", "absent")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("value").value is None


class TestHasState:
    @pytest.mark.asyncio
    async def test_returns_name_and_scope_outputs(self):
        # NOTE: HasState inherits its output sockets from StateManipulation,
        # which does NOT include `exists`. The current run() pushes `exists`
        # into set_output_values but it has nowhere to land. We assert what
        # actually IS pushed (name/scope) so the test pins observable
        # behavior without colluding with that quirk.
        with GraphContext() as state:
            state.data["k"] = "v"
            n = HasState()
            n.set_property("name", "k")
            n.set_property("scope", "local")
            # Should not raise even though `exists` has no socket.
            await n.run(state)
            assert n.get_output_socket("name").value == "k"
            assert n.get_output_socket("scope").value == "local"

    @pytest.mark.asyncio
    async def test_absent(self):
        with GraphContext() as state:
            n = HasState()
            n.set_property("name", "missing")
            n.set_property("scope", "local")
            await n.run(state)
            # Same — verify run completes; `exists` socket is absent.
            assert n.get_output_socket("name").value == "missing"


# ---------------------------------------------------------------------------
# CounterState and reset_cap behavior
# ---------------------------------------------------------------------------


class TestCounterState:
    @pytest.mark.asyncio
    async def test_first_increment_marks_new_cycle(self):
        with GraphContext() as state:
            n = CounterState()
            n.set_property("name", "k")
            n.set_property("scope", "local")
            n.set_property("increment", 1)
            await n.run(state)
            assert n.get_output_socket("value").value == 1
            # Starting from 0 -> new_cycle is True
            assert n.get_output_socket("new_cycle").value is True

    @pytest.mark.asyncio
    async def test_subsequent_increment_not_new_cycle(self):
        with GraphContext() as state:
            n = CounterState()
            n.set_property("name", "k")
            n.set_property("scope", "local")
            n.set_property("increment", 2)
            await n.run(state)  # 0 -> 2
            await n.run(state)  # 2 -> 4
            assert n.get_output_socket("value").value == 4
            assert n.get_output_socket("new_cycle").value is False

    @pytest.mark.asyncio
    async def test_reset_branch(self):
        with GraphContext() as state:
            state.data["k"] = 99
            n = CounterState()
            n.set_property("name", "k")
            n.set_property("scope", "local")
            n.set_property("reset", True)
            await n.run(state)
            assert n.get_output_socket("value").value == 0
            assert state.data["k"] == 0

    @pytest.mark.asyncio
    async def test_reset_cap_triggers_reset(self):
        with GraphContext() as state:
            state.data["k"] = 4
            n = CounterState()
            n.set_property("name", "k")
            n.set_property("scope", "local")
            n.set_property("increment", 1)
            n.set_property("reset_cap", 5)
            await n.run(state)  # 4 -> 5 -> reset to 0
            assert n.get_output_socket("value").value == 0
            assert n.get_output_socket("reset").value is True

    @pytest.mark.asyncio
    async def test_invalid_reset_cap_swallowed(self):
        # A non-numeric reset_cap is logged but does not crash the node.
        with GraphContext() as state:
            n = CounterState()
            n.set_property("name", "k")
            n.set_property("scope", "local")
            n.set_property("reset_cap", "not-a-number")
            await n.run(state)
            # Counter still incremented despite the cap conversion failure.
            assert n.get_output_socket("value").value == 1


# ---------------------------------------------------------------------------
# ConditionalSetState / ConditionalUnsetState / ConditionalCounterState
# These wrap their non-conditional counterparts and pass through `state`.
# ---------------------------------------------------------------------------


class TestConditionalNodes:
    @pytest.mark.asyncio
    async def test_conditional_set_passes_state(self):
        with GraphContext() as state:
            n = ConditionalSetState()
            n.set_property("state", "passthrough")
            n.set_property("name", "k")
            n.set_property("value", 1)
            n.set_property("scope", "local")
            await n.run(state)
            assert state.data["k"] == 1
            assert n.get_output_socket("state").value == "passthrough"

    @pytest.mark.asyncio
    async def test_conditional_unset_passes_state(self):
        with GraphContext() as state:
            state.data["k"] = "v"
            n = ConditionalUnsetState()
            n.set_property("state", "ok")
            n.set_property("name", "k")
            n.set_property("scope", "local")
            await n.run(state)
            assert "k" not in state.data
            assert n.get_output_socket("state").value == "ok"

    @pytest.mark.asyncio
    async def test_conditional_counter_passes_state(self):
        with GraphContext() as state:
            n = ConditionalCounterState()
            n.set_property("state", "ok")
            n.set_property("name", "k")
            n.set_property("scope", "local")
            n.set_property("increment", 3)
            await n.run(state)
            assert state.data["k"] == 3
            assert n.get_output_socket("state").value == "ok"


# ---------------------------------------------------------------------------
# Path-based variants — SetStatePath, GetStatePath, UnsetStatePath,
# HasStatePath, CounterStatePath
# ---------------------------------------------------------------------------


class TestSetStatePath:
    @pytest.mark.asyncio
    async def test_creates_nested_dicts(self):
        # NOTE: GraphState.data is also used by sockets internally — we only
        # assert that the targeted nested key was created, not the entire
        # dict equality.
        with GraphContext() as state:
            n = SetStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "a/b/c")
            n.set_property("value", "leaf")
            n.set_property("scope", "local")
            await n.run(state)
        assert state.data["a"] == {"b": {"c": "leaf"}}

    @pytest.mark.asyncio
    async def test_empty_path_raises(self):
        with GraphContext() as state:
            n = SetStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "")
            n.set_property("value", 1)
            n.set_property("scope", "local")
            with pytest.raises(InputValueError):
                await n.run(state)

    @pytest.mark.asyncio
    async def test_intermediate_non_dict_raises(self):
        # If an intermediate segment exists but isn't a dict, the path
        # creation refuses.
        with GraphContext() as state:
            state.data["a"] = "scalar"
            n = SetStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "a/b/c")
            n.set_property("value", 1)
            n.set_property("scope", "local")
            with pytest.raises(InputValueError):
                await n.run(state)


class TestGetStatePath:
    @pytest.mark.asyncio
    async def test_resolves_existing_path(self):
        with GraphContext() as state:
            state.data["a"] = {"b": {"c": "leaf"}}
            n = GetStatePath()
            n.set_property("name", "a/b/c")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("value").value == "leaf"

    @pytest.mark.asyncio
    async def test_missing_path_returns_default(self):
        with GraphContext() as state:
            n = GetStatePath()
            n.set_property("name", "a/b")
            n.set_property("scope", "local")
            n.set_property("default", "fb")
            await n.run(state)
            assert n.get_output_socket("value").value == "fb"

    @pytest.mark.asyncio
    async def test_path_present_but_leaf_missing_returns_default(self):
        with GraphContext() as state:
            state.data["a"] = {}
            n = GetStatePath()
            n.set_property("name", "a/missing")
            n.set_property("scope", "local")
            n.set_property("default", 0)
            await n.run(state)
            assert n.get_output_socket("value").value == 0


class TestUnsetStatePath:
    @pytest.mark.asyncio
    async def test_pops_existing_leaf(self):
        with GraphContext() as state:
            state.data["a"] = {"b": "v"}
            n = UnsetStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "a/b")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("value").value == "v"
            assert state.data["a"] == {}

    @pytest.mark.asyncio
    async def test_missing_path_returns_none(self):
        with GraphContext() as state:
            n = UnsetStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "a/b")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("value").value is None


class TestHasStatePath:
    @pytest.mark.asyncio
    async def test_run_completes_for_present_path(self):
        # As with HasState, the HasStatePath subclass inherits its outputs
        # from StateManipulation (no `exists` socket). We verify that run()
        # completes without raising and the name/scope outputs are emitted.
        with GraphContext() as state:
            state.data["a"] = {"b": 1}
            n = HasStatePath()
            n.set_property("name", "a/b")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("name").value == "a/b"

    @pytest.mark.asyncio
    async def test_run_completes_for_absent_path(self):
        with GraphContext() as state:
            n = HasStatePath()
            n.set_property("name", "a/b/c")
            n.set_property("scope", "local")
            await n.run(state)
            assert n.get_output_socket("scope").value == "local"

    @pytest.mark.asyncio
    async def test_empty_path_raises(self):
        with GraphContext() as state:
            n = HasStatePath()
            n.set_property("name", "")
            n.set_property("scope", "local")
            with pytest.raises(InputValueError):
                await n.run(state)


class TestCounterStatePath:
    @pytest.mark.asyncio
    async def test_creates_nested_counter(self):
        with GraphContext() as state:
            n = CounterStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "ns/counter")
            n.set_property("scope", "local")
            n.set_property("increment", 1)
            await n.run(state)
            assert state.data["ns"]["counter"] == 1
            assert n.get_output_socket("new_cycle").value is True

    @pytest.mark.asyncio
    async def test_increments_existing_counter(self):
        with GraphContext() as state:
            state.data["ns"] = {"counter": 5}
            n = CounterStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "ns/counter")
            n.set_property("scope", "local")
            n.set_property("increment", 2)
            await n.run(state)
            assert state.data["ns"]["counter"] == 7
            assert n.get_output_socket("new_cycle").value is False

    @pytest.mark.asyncio
    async def test_reset_cap_triggers_reset(self):
        with GraphContext() as state:
            state.data["c"] = 4
            n = CounterStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "c")
            n.set_property("scope", "local")
            n.set_property("increment", 1)
            n.set_property("reset_cap", 5)
            await n.run(state)
            assert state.data["c"] == 0
            assert n.get_output_socket("reset").value is True

    @pytest.mark.asyncio
    async def test_empty_name_raises(self):
        with GraphContext() as state:
            n = CounterStatePath()
            n.set_property("state", "ok")
            n.set_property("name", "")
            n.set_property("scope", "local")
            with pytest.raises(InputValueError):
                await n.run(state)


# ---------------------------------------------------------------------------
# UnpackGameState
# ---------------------------------------------------------------------------


class TestUnpackGameState:
    @pytest.mark.asyncio
    async def test_returns_game_state_variables(self):
        scene = Scene()
        scene.game_state.set_var("hero", "Alice")

        with ActiveScene(scene):
            with GraphContext() as state:
                n = UnpackGameState()
                await n.run(state)
                assert n.get_output_socket("variables").value == {"hero": "Alice"}
