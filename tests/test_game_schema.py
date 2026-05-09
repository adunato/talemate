"""
Unit tests for `talemate.game.schema` — pure pydantic Condition / ConditionGroup
evaluation logic and the wire-format helper `condition_groups_match`.

These tests exercise:
- All supported operators (==, !=, >, <, >=, <=, in, not_in, is_true, is_false,
  is_null, is_not_null + variants).
- Numeric coercion semantics.
- Mixed string/numeric handling.
- Path resolution (slash-delimited, missing paths, malformed paths).
- ConditionGroup AND/OR combining and empty-group fallback.
- `condition_groups_match` wire format normalization.
"""

import pytest

from talemate.game.schema import (
    Condition,
    ConditionGroup,
    condition_groups_match,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state():
    """Generic nested dict game state."""
    return {
        "stats": {
            "hp": 50,
            "level": "12",  # numeric-string for coercion tests
            "alive": True,
            "dead": False,
            "name": "alice",
            "tags": ["mage", "elf"],
            "hp_str": "fifty",
        },
        "empty_str": "",
        "zero": 0,
        "none_value": None,
    }


# ---------------------------------------------------------------------------
# Path resolution / missing-path semantics
# ---------------------------------------------------------------------------


class TestConditionPathResolution:
    def test_missing_top_level_key_evaluates_false(self, state):
        c = Condition(path="missing", operator="==", value="anything")
        assert c.evaluate(state) is False

    def test_missing_nested_key_evaluates_false(self, state):
        c = Condition(path="stats/missing_nested", operator="==", value="x")
        assert c.evaluate(state) is False

    def test_missing_intermediate_returns_false(self, state):
        # "absent" doesn't exist; parent traversal returns None
        c = Condition(path="absent/leaf", operator="==", value="x")
        assert c.evaluate(state) is False

    def test_empty_path_returns_false(self, state):
        # split_state_path raises ValueError on empty -> caught -> False
        c = Condition(path="", operator="==", value=1)
        assert c.evaluate(state) is False

    def test_leading_slash_is_stripped(self, state):
        c = Condition(path="/stats/hp", operator="==", value=50)
        assert c.evaluate(state) is True


class _DuckGameState:
    """Mimics GameState's has_var/get_var duck-typed API."""

    def __init__(self, store):
        self._store = store

    def has_var(self, key):
        return key in self._store

    def get_var(self, key):
        return self._store[key]


class TestConditionDuckTypedContainer:
    def test_uses_has_var_get_var_when_available(self):
        gs = _DuckGameState({"hp": 5})
        # Wrap to traverse single-segment path resolving to gs as parent
        c = Condition(path="hp", operator=">", value=3)

        # Outer container is itself the duck-typed parent for single-segment path.
        # In schema.py, parent comes from get_path_parent's traversal.
        # For single-segment path, the parent IS the supplied container.
        assert c.evaluate(gs) is True

    def test_has_var_returning_false_makes_evaluation_false(self):
        gs = _DuckGameState({"hp": 5})
        c = Condition(path="missing", operator="==", value=1)
        assert c.evaluate(gs) is False


# ---------------------------------------------------------------------------
# Unary operators
# ---------------------------------------------------------------------------


class TestUnaryOperators:
    def test_is_true(self, state):
        c = Condition(path="stats/alive", operator="is_true")
        assert c.evaluate(state) is True

    def test_is_true_strict_truthiness(self, state):
        # Truthy non-bool should NOT pass is_true
        c = Condition(path="stats/hp", operator="is_true")
        assert c.evaluate(state) is False

    def test_is_false(self, state):
        c = Condition(path="stats/dead", operator="is_false")
        assert c.evaluate(state) is True

    def test_is_false_strict_falseness(self, state):
        # Falsy non-False should NOT match is_false
        c = Condition(path="zero", operator="is_false")
        assert c.evaluate(state) is False

    def test_is_null(self, state):
        c = Condition(path="none_value", operator="is_null")
        assert c.evaluate(state) is True

    def test_is_null_false_for_zero(self, state):
        c = Condition(path="zero", operator="is_null")
        assert c.evaluate(state) is False

    def test_is_not_null_underscore_form(self, state):
        c = Condition(path="stats/hp", operator="is_not_null")
        assert c.evaluate(state) is True

    def test_is_not_null_space_form(self, state):
        c = Condition(path="stats/hp", operator="is not null")
        assert c.evaluate(state) is True

    def test_is_not_null_for_actual_null(self, state):
        c = Condition(path="none_value", operator="is_not_null")
        assert c.evaluate(state) is False


# ---------------------------------------------------------------------------
# Equality / inequality
# ---------------------------------------------------------------------------


class TestEqualityOperators:
    def test_equality_int_to_int(self, state):
        c = Condition(path="stats/hp", operator="==", value=50)
        assert c.evaluate(state) is True

    def test_equality_string_value_coerced_to_int_for_numeric_state(self, state):
        # state hp=50 (int), value="50" (str) -> both coerced to int -> equal
        c = Condition(path="stats/hp", operator="==", value="50")
        assert c.evaluate(state) is True

    def test_equality_string_state_coerced_to_int(self, state):
        # state level="12" (str), value=12 (int) -> coerced and equal
        c = Condition(path="stats/level", operator="==", value=12)
        assert c.evaluate(state) is True

    def test_equality_str_to_str(self, state):
        c = Condition(path="stats/name", operator="==", value="alice")
        assert c.evaluate(state) is True

    def test_inequality_str_to_str(self, state):
        c = Condition(path="stats/name", operator="!=", value="bob")
        assert c.evaluate(state) is True

    def test_inequality_for_equal_values_is_false(self, state):
        c = Condition(path="stats/hp", operator="!=", value=50)
        assert c.evaluate(state) is False

    def test_equality_with_none_value_returns_false_for_non_unary(self, state):
        # Spec: For non-unary ops, missing value can't match.
        c = Condition(path="stats/hp", operator="==", value=None)
        assert c.evaluate(state) is False


# ---------------------------------------------------------------------------
# Numeric-only operators
# ---------------------------------------------------------------------------


class TestNumericOperators:
    @pytest.mark.parametrize(
        "op,value,expected",
        [
            (">", 49, True),
            (">", 50, False),
            ("<", 51, True),
            ("<", 50, False),
            (">=", 50, True),
            (">=", 51, False),
            ("<=", 50, True),
            ("<=", 49, False),
        ],
    )
    def test_numeric_operators(self, state, op, value, expected):
        c = Condition(path="stats/hp", operator=op, value=value)
        assert c.evaluate(state) is expected

    def test_numeric_operator_with_non_numeric_state_is_false(self, state):
        # hp_str="fifty" can't be parsed -> False
        c = Condition(path="stats/hp_str", operator=">", value=10)
        assert c.evaluate(state) is False

    def test_numeric_operator_with_non_numeric_value_is_false(self, state):
        c = Condition(path="stats/hp", operator=">", value="abc")
        assert c.evaluate(state) is False

    def test_numeric_with_negative_string(self, state):
        c = Condition(path="stats/hp", operator=">", value="-1")
        assert c.evaluate(state) is True

    def test_bool_is_not_treated_as_number(self, state):
        # `True` should NOT be coerced to 1 — bool excluded from _try_parse_number
        c = Condition(path="stats/alive", operator=">", value=0)
        assert c.evaluate(state) is False

    def test_float_string_coercion(self):
        c = Condition(path="x", operator=">=", value="2.5")
        assert c.evaluate({"x": 2.5}) is True


# ---------------------------------------------------------------------------
# Membership operators
# ---------------------------------------------------------------------------


class TestMembershipOperators:
    def test_in_operator_string_in_list(self, state):
        c = Condition(path="stats/tags", operator="in", value="mage")
        assert c.evaluate(state) is True

    def test_in_operator_missing_member(self, state):
        c = Condition(path="stats/tags", operator="in", value="bard")
        assert c.evaluate(state) is False

    def test_not_in_operator(self, state):
        c = Condition(path="stats/tags", operator="not_in", value="bard")
        assert c.evaluate(state) is True

    def test_in_substring(self, state):
        # "in" works on strings for substring containment
        c = Condition(path="stats/name", operator="in", value="lic")
        assert c.evaluate(state) is True


# ---------------------------------------------------------------------------
# Exception safety
# ---------------------------------------------------------------------------


class TestExceptionSafety:
    def test_exception_during_evaluate_returns_false(self, state):
        # Trigger TypeError via "in" against non-iterable -> caught -> False
        c = Condition(path="stats/hp", operator="in", value="ignored")
        # state hp=50 (int), value="ignored" (str), neither numeric on RHS,
        # mixed branch sets right=str("ignored") and tries `right in left`
        # where left=50 -> TypeError -> caught -> False
        assert c.evaluate(state) is False


# ---------------------------------------------------------------------------
# ConditionGroup logic
# ---------------------------------------------------------------------------


class TestConditionGroup:
    def _hp_gt(self, value):
        return Condition(path="stats/hp", operator=">", value=value)

    def _name_eq(self, value):
        return Condition(path="stats/name", operator="==", value=value)

    def test_empty_group_evaluates_false(self, state):
        g = ConditionGroup(conditions=[], operator="and")
        assert g.evaluate(state) is False

    def test_and_all_true(self, state):
        g = ConditionGroup(
            conditions=[self._hp_gt(10), self._name_eq("alice")],
            operator="and",
        )
        assert g.evaluate(state) is True

    def test_and_one_false(self, state):
        g = ConditionGroup(
            conditions=[self._hp_gt(10), self._name_eq("bob")],
            operator="and",
        )
        assert g.evaluate(state) is False

    def test_or_one_true(self, state):
        g = ConditionGroup(
            conditions=[self._hp_gt(1000), self._name_eq("alice")],
            operator="or",
        )
        assert g.evaluate(state) is True

    def test_or_all_false(self, state):
        g = ConditionGroup(
            conditions=[self._hp_gt(1000), self._name_eq("bob")],
            operator="or",
        )
        assert g.evaluate(state) is False

    def test_default_operator_is_and(self, state):
        # No operator specified -> default "and"
        g = ConditionGroup(
            conditions=[self._hp_gt(10), self._name_eq("alice")],
        )
        assert g.evaluate(state) is True


# ---------------------------------------------------------------------------
# condition_groups_match wire-format helper
# ---------------------------------------------------------------------------


class TestConditionGroupsMatch:
    def test_none_returns_false(self, state):
        assert condition_groups_match(None, state) is False

    def test_empty_list_returns_false(self, state):
        assert condition_groups_match([], state) is False

    def test_non_list_returns_false(self, state):
        assert condition_groups_match("not a list", state) is False
        assert condition_groups_match({"groups": []}, state) is False

    def test_wire_format_dict_groups(self, state):
        groups = [
            {
                "operator": "and",
                "conditions": [
                    {"path": "stats/hp", "operator": ">", "value": 10},
                    {"path": "stats/name", "operator": "==", "value": "alice"},
                ],
            }
        ]
        assert condition_groups_match(groups, state) is True

    def test_multiple_groups_combine_with_or(self, state):
        groups = [
            {
                "operator": "and",
                "conditions": [
                    {"path": "stats/name", "operator": "==", "value": "bob"},
                ],
            },
            {
                "operator": "and",
                "conditions": [
                    {"path": "stats/hp", "operator": ">", "value": 1},
                ],
            },
        ]
        # Group 1 false, group 2 true -> any() true
        assert condition_groups_match(groups, state) is True

    def test_groups_with_real_models(self, state):
        groups = [
            ConditionGroup(
                conditions=[
                    Condition(path="stats/hp", operator=">", value=10),
                ],
                operator="and",
            )
        ]
        assert condition_groups_match(groups, state) is True

    def test_invalid_group_element_returns_false(self, state):
        groups = [
            {"operator": "and", "conditions": []},
            "totally invalid",
        ]
        # On encountering non-dict non-Group element, returns False
        assert condition_groups_match(groups, state) is False

    def test_invalid_condition_dict_swallowed_returns_false(self, state):
        # Missing required fields -> ValidationError -> caught -> False
        groups = [
            {"operator": "and", "conditions": [{"oops": True}]},
        ]
        assert condition_groups_match(groups, state) is False
