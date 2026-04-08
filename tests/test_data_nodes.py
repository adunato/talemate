"""Unit tests for pure helpers on data/ node classes."""

from talemate.game.engine.nodes.data import DictGetPath


class TestDictGetPathWalk:
    def test_empty_path_returns_whole_dict(self):
        data = {"a": 1}
        value, found = DictGetPath._walk(data, "")
        assert found is True
        assert value == data

    def test_single_level_key(self):
        value, found = DictGetPath._walk({"a": 1}, "a")
        assert found is True
        assert value == 1

    def test_nested_dict(self):
        data = {"modes": {"generate_arc": {"close_arc": True}}}
        value, found = DictGetPath._walk(data, "modes.generate_arc.close_arc")
        assert found is True
        assert value is True

    def test_missing_final_key(self):
        data = {"modes": {"generate_arc": {}}}
        value, found = DictGetPath._walk(data, "modes.generate_arc.close_arc")
        assert found is False
        assert value is None

    def test_missing_intermediate_key(self):
        data = {"modes": {}}
        value, found = DictGetPath._walk(data, "modes.generate_arc.close_arc")
        assert found is False
        assert value is None

    def test_list_index(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        value, found = DictGetPath._walk(data, "items.1.name")
        assert found is True
        assert value == "b"

    def test_list_index_out_of_range(self):
        data = {"items": [{"name": "a"}]}
        value, found = DictGetPath._walk(data, "items.5.name")
        assert found is False
        assert value is None

    def test_negative_list_index_not_supported(self):
        # Negative indices short-circuit to not-found on purpose —
        # unambiguous lookups only.
        data = {"items": [1, 2, 3]}
        value, found = DictGetPath._walk(data, "items.-1")
        assert found is False
        assert value is None

    def test_non_integer_list_index_short_circuits(self):
        data = {"items": [1, 2, 3]}
        value, found = DictGetPath._walk(data, "items.foo")
        assert found is False
        assert value is None

    def test_traversal_into_scalar_fails(self):
        data = {"a": 1}
        value, found = DictGetPath._walk(data, "a.b")
        assert found is False
        assert value is None

    def test_falsy_value_is_still_found(self):
        # Ensure `found` flag distinguishes "key present with false value"
        # from "key absent" — critical for close_arc defaulting.
        data = {"modes": {"generate_arc": {"close_arc": False}}}
        value, found = DictGetPath._walk(data, "modes.generate_arc.close_arc")
        assert found is True
        assert value is False


class TestDictGetPathDeriveKey:
    def test_last_segment_of_nested_path(self):
        assert (
            DictGetPath._derive_key("modes.generate_arc.close_arc") == "close_arc"
        )

    def test_single_segment(self):
        assert DictGetPath._derive_key("close_arc") == "close_arc"

    def test_empty_path_returns_empty_string(self):
        assert DictGetPath._derive_key("") == ""

    def test_terminal_list_index_is_joined_with_parent(self):
        # A bare numeric index is a useless dict key, so the derivation
        # folds the parent segment in with an underscore.
        assert DictGetPath._derive_key("items.0") == "items_0"

    def test_terminal_list_index_nested(self):
        assert DictGetPath._derive_key("a.b.items.0") == "items_0"

    def test_terminal_list_index_alone_returns_index(self):
        # Degenerate case: only an index, no parent to join with. Return
        # the index as-is rather than crashing.
        assert DictGetPath._derive_key("0") == "0"

    def test_negative_index_treated_as_index(self):
        # Negative numeric indices don't traverse in _walk (we short-circuit),
        # but _derive_key still recognizes them as indices for key naming.
        assert DictGetPath._derive_key("items.-1") == "items_-1"
