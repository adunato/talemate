"""
Unit tests for talemate.prompts.content_context.

Covers PromptContextState (push / has / extend) and the PromptContext
context manager that swaps the active ContextVar state on entry/exit.
"""

import pytest

from talemate.prompts.content_context import (
    PromptContext,
    PromptContextState,
    current_prompt_context,
)


class TestPromptContextStatePush:
    """Tests for PromptContextState.push."""

    def test_push_appends_new_content_to_state_and_proxy(self):
        state = PromptContextState()
        proxy: list[str] = []

        state.push("hello", proxy)

        assert state.content == ["hello"]
        assert proxy == ["hello"]

    def test_push_skips_duplicate_content(self):
        state = PromptContextState()
        proxy: list[str] = []

        state.push("hello", proxy)
        state.push("hello", proxy)

        # Duplicate must NOT be appended to either list.
        assert state.content == ["hello"]
        assert proxy == ["hello"]

    def test_push_preserves_insertion_order_for_distinct_items(self):
        state = PromptContextState()
        proxy: list[str] = []

        for value in ("a", "b", "c"):
            state.push(value, proxy)

        assert state.content == ["a", "b", "c"]
        assert proxy == ["a", "b", "c"]

    def test_push_proxy_is_independent_per_call(self):
        """Each call appends only to the supplied proxy, not previous proxies."""
        state = PromptContextState()
        proxy_a: list[str] = []
        proxy_b: list[str] = []

        state.push("first", proxy_a)
        state.push("second", proxy_b)

        # State sees both, but the proxies are local accumulators.
        assert state.content == ["first", "second"]
        assert proxy_a == ["first"]
        assert proxy_b == ["second"]


class TestPromptContextStateHas:
    """Tests for PromptContextState.has."""

    def test_has_returns_true_after_push(self):
        state = PromptContextState()
        state.push("found", [])

        assert state.has("found") is True

    def test_has_returns_false_for_missing_content(self):
        state = PromptContextState()
        state.push("found", [])

        assert state.has("not-there") is False

    def test_has_on_empty_state(self):
        state = PromptContextState()
        assert state.has("anything") is False


class TestPromptContextStateExtend:
    """Tests for PromptContextState.extend."""

    def test_extend_appends_each_unique_item(self):
        state = PromptContextState()
        proxy: list[str] = []

        state.extend(["a", "b", "c"], proxy)

        assert state.content == ["a", "b", "c"]
        assert proxy == ["a", "b", "c"]

    def test_extend_skips_duplicates_within_input(self):
        state = PromptContextState()
        proxy: list[str] = []

        state.extend(["a", "a", "b"], proxy)

        assert state.content == ["a", "b"]
        assert proxy == ["a", "b"]

    def test_extend_skips_items_already_in_state(self):
        state = PromptContextState()
        state.push("existing", [])
        proxy: list[str] = []

        state.extend(["existing", "new"], proxy)

        # "existing" is filtered, only "new" reaches state and proxy.
        assert state.content == ["existing", "new"]
        assert proxy == ["new"]

    def test_extend_with_empty_list_is_noop(self):
        state = PromptContextState()
        proxy: list[str] = []

        state.extend([], proxy)

        assert state.content == []
        assert proxy == []


class TestPromptContextManager:
    """Tests for the PromptContext context manager."""

    def test_default_contextvar_is_none_outside_block(self):
        # Before entering, the ContextVar should hold the module default.
        assert current_prompt_context.get() is None

    def test_enter_sets_state_in_contextvar(self):
        with PromptContext() as state:
            assert current_prompt_context.get() is state
            assert isinstance(state, PromptContextState)
            assert state.content == []

    def test_exit_restores_previous_value(self):
        # Sanity-check that the value before entry is restored on exit.
        before = current_prompt_context.get()
        with PromptContext():
            pass
        assert current_prompt_context.get() is before

    def test_state_persists_pushed_content(self):
        """Content pushed inside the block is retained on the yielded state."""
        proxy: list[str] = []

        with PromptContext() as state:
            state.push("entry-1", proxy)
            state.push("entry-2", proxy)

            assert current_prompt_context.get().content == ["entry-1", "entry-2"]
            assert proxy == ["entry-1", "entry-2"]

        # Yielded state object still readable after exit.
        assert state.content == ["entry-1", "entry-2"]

    def test_nested_contexts_are_independent(self):
        """Inner block sees its own state, outer state is restored on exit."""
        with PromptContext() as outer:
            outer.push("outer-only", [])

            with PromptContext() as inner:
                # Inner state is a fresh instance.
                assert inner is not outer
                assert inner.content == []
                assert current_prompt_context.get() is inner

                inner.push("inner-only", [])
                assert inner.has("inner-only")
                # Outer's content is NOT visible to inner.
                assert not inner.has("outer-only")

            # After inner exits, outer is the active state again.
            assert current_prompt_context.get() is outer
            assert outer.has("outer-only")
            # The inner content didn't leak into outer.
            assert not outer.has("inner-only")

    def test_exit_returns_false_to_not_swallow_exceptions(self):
        """__exit__ returns False so exceptions inside the block propagate."""
        with pytest.raises(RuntimeError, match="boom"):
            with PromptContext():
                raise RuntimeError("boom")

        # And the ContextVar is reset cleanly even after an exception.
        assert current_prompt_context.get() is None
