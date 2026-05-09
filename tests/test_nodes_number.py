"""
Unit tests for src/talemate/game/engine/nodes/number.py.

Each test instantiates a node, pre-loads inputs as properties (the standard
test pattern — see `_node_test_helpers`), runs the node, and asserts the
output socket values.
"""

import math
import random

import pytest

from talemate.game.engine.nodes.core import (
    InputValueError,
    UNRESOLVED,
)
from talemate.game.engine.nodes.number import (
    Average,
    AsNumber,
    BasicArithmetic,
    Clamp,
    Compare,
    MakeNumber,
    MinMax,
    NumberNode,
    Random,
    Sum,
)

from _node_test_helpers import run_node


# ---------------------------------------------------------------------------
# NumberNode.normalized_number_input (helper exercised in isolation)
# ---------------------------------------------------------------------------


class _NumberNodeProbe(NumberNode):
    """Bare NumberNode subclass exposing one input named `value`."""

    def setup(self):
        self.add_input("value")
        self.add_output("value")
        self.set_property("value", 0)


def test_normalized_number_input_string_to_int():
    n = _NumberNodeProbe()
    n.set_property("value", "42")
    assert n.normalized_number_input("value", types=(int,)) == 42


def test_normalized_number_input_string_to_float():
    n = _NumberNodeProbe()
    n.set_property("value", "3.14")
    assert n.normalized_number_input("value", types=(float,)) == 3.14


def test_normalized_number_input_invalid_raises():
    n = _NumberNodeProbe()
    n.set_property("value", "not-a-number")
    with pytest.raises(InputValueError):
        n.normalized_number_input("value")


def test_normalized_number_input_unresolved_passthrough():
    n = _NumberNodeProbe()
    # deliberately leave value UNRESOLVED
    n.properties["value"] = UNRESOLVED
    assert n.normalized_number_input("value") is UNRESOLVED


def test_normalized_number_input_none_returns_unresolved():
    n = _NumberNodeProbe()
    n.set_property("value", None)
    assert n.normalized_number_input("value") is UNRESOLVED


# ---------------------------------------------------------------------------
# MakeNumber
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_number_float_path():
    node = MakeNumber()
    out = await run_node(node, inputs={"value": 7.5, "number_type": "float"})
    assert out["value"] == 7.5
    assert isinstance(out["value"], float)


@pytest.mark.asyncio
async def test_make_number_int_path_truncates_string():
    """number_type=int forces int conversion of string-valued inputs."""
    node = MakeNumber()
    out = await run_node(node, inputs={"value": "12", "number_type": "int"})
    assert out["value"] == 12
    assert isinstance(out["value"], int)


# ---------------------------------------------------------------------------
# AsNumber
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_as_number_int_converts_value():
    node = AsNumber()
    out = await run_node(node, inputs={"value": "9", "number_type": "int"})
    assert out["value"] == 9
    assert isinstance(out["value"], int)


@pytest.mark.asyncio
async def test_as_number_float_converts_value():
    node = AsNumber()
    out = await run_node(node, inputs={"value": "9.5", "number_type": "float"})
    assert out["value"] == 9.5


@pytest.mark.asyncio
async def test_as_number_falls_back_to_default_when_value_unresolved():
    node = AsNumber()
    # leave value UNRESOLVED, supply default
    node.properties["value"] = UNRESOLVED
    node.set_property("default", 100)
    node.set_property("number_type", "int")
    out = await run_node(node)
    assert out["value"] == 100


@pytest.mark.asyncio
async def test_as_number_returns_unresolved_when_both_value_and_default_unset():
    node = AsNumber()
    node.properties["value"] = UNRESOLVED
    node.properties["default"] = UNRESOLVED
    node.set_property("number_type", "int")
    out = await run_node(node)
    assert out["value"] is UNRESOLVED


# ---------------------------------------------------------------------------
# BasicArithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation,a,b,expected",
    [
        ("add", 2, 3, 5),
        ("subtract", 10, 4, 6),
        ("multiply", 4, 5, 20),
        ("divide", 10, 4, 2.5),
        ("power", 2, 8, 256),
        ("modulo", 17, 5, 2),
    ],
)
@pytest.mark.asyncio
async def test_basic_arithmetic_operations(operation, a, b, expected):
    node = BasicArithmetic()
    out = await run_node(node, inputs={"a": a, "b": b, "operation": operation})
    assert out["result"] == expected


@pytest.mark.asyncio
async def test_basic_arithmetic_division_by_zero_raises():
    node = BasicArithmetic()
    with pytest.raises(InputValueError):
        await run_node(
            node, inputs={"a": 10, "b": 0, "operation": "divide"}
        )


@pytest.mark.asyncio
async def test_basic_arithmetic_modulo_by_zero_raises():
    node = BasicArithmetic()
    with pytest.raises(InputValueError):
        await run_node(
            node, inputs={"a": 10, "b": 0, "operation": "modulo"}
        )


@pytest.mark.asyncio
async def test_basic_arithmetic_unresolved_input_returns_no_output():
    """When `a` is UNRESOLVED the node returns early without setting result."""
    node = BasicArithmetic()
    node.properties["a"] = UNRESOLVED
    node.set_property("b", 5)
    node.set_property("operation", "add")
    out = await run_node(node)
    assert out["result"] is UNRESOLVED


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation,a,b,expected",
    [
        ("equals", 5, 5, True),
        ("equals", 5, 4, False),
        ("not_equals", 5, 4, True),
        ("not_equals", 5, 5, False),
        ("greater_than", 6, 5, True),
        ("greater_than", 5, 6, False),
        ("less_than", 4, 5, True),
        ("less_than", 6, 5, False),
        ("greater_equal", 5, 5, True),
        ("greater_equal", 4, 5, False),
        ("less_equal", 5, 5, True),
        ("less_equal", 6, 5, False),
    ],
)
@pytest.mark.asyncio
async def test_compare_operations(operation, a, b, expected):
    node = Compare()
    out = await run_node(node, inputs={"a": a, "b": b, "operation": operation})
    assert out["result"] is expected


@pytest.mark.asyncio
async def test_compare_equals_uses_tolerance_for_floats():
    """Two values within `tolerance` are considered equal."""
    node = Compare()
    out = await run_node(
        node,
        inputs={
            "a": 1.0,
            "b": 1.000001,
            "operation": "equals",
            "tolerance": 0.01,
        },
    )
    assert out["result"] is True


@pytest.mark.asyncio
async def test_compare_unresolved_inputs_short_circuit():
    node = Compare()
    node.properties["a"] = UNRESOLVED
    node.set_property("b", 5)
    node.set_property("operation", "equals")
    out = await run_node(node)
    assert out["result"] is UNRESOLVED


# ---------------------------------------------------------------------------
# MinMax
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_min_max_returns_min_and_index():
    node = MinMax()
    out = await run_node(
        node, inputs={"numbers": [3, 1, 4, 1, 5], "operation": "min"}
    )
    assert out["result"] == 1
    assert out["index"] == 1  # first index of value 1


@pytest.mark.asyncio
async def test_min_max_returns_max_and_index():
    node = MinMax()
    out = await run_node(
        node, inputs={"numbers": [3, 1, 4, 1, 5], "operation": "max"}
    )
    assert out["result"] == 5
    assert out["index"] == 4


@pytest.mark.asyncio
async def test_min_max_empty_list_raises():
    node = MinMax()
    with pytest.raises(InputValueError):
        await run_node(node, inputs={"numbers": [], "operation": "min"})


@pytest.mark.asyncio
async def test_min_max_non_numeric_raises():
    node = MinMax()
    with pytest.raises(InputValueError):
        await run_node(
            node, inputs={"numbers": [1, "two", 3], "operation": "min"}
        )


# ---------------------------------------------------------------------------
# Sum
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sum_adds_all_numbers():
    node = Sum()
    out = await run_node(node, inputs={"numbers": [1, 2, 3, 4]})
    assert out["result"] == 10


@pytest.mark.asyncio
async def test_sum_empty_list_returns_zero():
    """An empty list of numbers is a valid input — sum is 0."""
    node = Sum()
    out = await run_node(node, inputs={"numbers": []})
    assert out["result"] == 0


@pytest.mark.asyncio
async def test_sum_non_numeric_raises():
    node = Sum()
    with pytest.raises(InputValueError):
        await run_node(node, inputs={"numbers": [1, "x", 2]})


# ---------------------------------------------------------------------------
# Average
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_average_mean():
    node = Average()
    out = await run_node(node, inputs={"numbers": [2, 4, 6], "method": "mean"})
    assert out["result"] == 4


@pytest.mark.asyncio
async def test_average_median():
    node = Average()
    out = await run_node(
        node, inputs={"numbers": [1, 2, 3, 100], "method": "median"}
    )
    assert out["result"] == 2.5


@pytest.mark.asyncio
async def test_average_mode():
    node = Average()
    out = await run_node(
        node, inputs={"numbers": [1, 2, 2, 3], "method": "mode"}
    )
    assert out["result"] == 2


@pytest.mark.asyncio
async def test_average_empty_list_raises():
    node = Average()
    with pytest.raises(InputValueError):
        await run_node(node, inputs={"numbers": [], "method": "mean"})


@pytest.mark.asyncio
async def test_average_non_numeric_raises():
    node = Average()
    with pytest.raises(InputValueError):
        await run_node(
            node, inputs={"numbers": [1, "two"], "method": "mean"}
        )


# ---------------------------------------------------------------------------
# Random
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_random_uniform_within_range():
    """Uniform draws should fall within [min, max]."""
    random.seed(42)
    node = Random()
    out = await run_node(
        node, inputs={"min": 0.0, "max": 10.0, "method": "uniform"}
    )
    assert 0.0 <= out["result"] <= 10.0
    assert isinstance(out["result"], float)


@pytest.mark.asyncio
async def test_random_integer_within_range():
    random.seed(42)
    node = Random()
    out = await run_node(
        node, inputs={"min": 1, "max": 10, "method": "integer"}
    )
    assert 1 <= out["result"] <= 10
    assert isinstance(out["result"], int)


@pytest.mark.asyncio
async def test_random_normal_distribution_returns_finite_value():
    """Normal draws are unbounded but must be a finite float."""
    random.seed(42)
    node = Random()
    out = await run_node(
        node, inputs={"mean": 0.0, "std_dev": 1.0, "method": "normal"}
    )
    assert isinstance(out["result"], float)
    assert math.isfinite(out["result"])


@pytest.mark.asyncio
async def test_random_normal_zero_std_dev_raises():
    node = Random()
    with pytest.raises(InputValueError):
        await run_node(
            node,
            inputs={"mean": 0.0, "std_dev": 0.0, "method": "normal"},
        )


@pytest.mark.asyncio
async def test_random_choice_picks_from_list():
    random.seed(42)
    node = Random()
    choices = ["a", "b", "c"]
    out = await run_node(
        node, inputs={"choices": choices, "method": "choice"}
    )
    assert out["result"] in choices


@pytest.mark.asyncio
async def test_random_choice_empty_list_raises():
    node = Random()
    with pytest.raises(InputValueError):
        await run_node(node, inputs={"choices": [], "method": "choice"})


# ---------------------------------------------------------------------------
# Clamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,lo,hi,expected",
    [
        (5, 0, 10, 5),  # within range
        (-3, 0, 10, 0),  # below min
        (15, 0, 10, 10),  # above max
        (10, 0, 10, 10),  # exactly max
        (0, 0, 10, 0),  # exactly min
    ],
)
@pytest.mark.asyncio
async def test_clamp_constrains_value(value, lo, hi, expected):
    node = Clamp()
    out = await run_node(node, inputs={"value": value, "min": lo, "max": hi})
    assert out["result"] == expected


@pytest.mark.asyncio
async def test_clamp_min_greater_than_max_raises():
    node = Clamp()
    with pytest.raises(InputValueError):
        await run_node(node, inputs={"value": 5, "min": 10, "max": 1})


@pytest.mark.asyncio
async def test_clamp_unresolved_inputs_short_circuit():
    """If any input is UNRESOLVED the node returns without setting result."""
    node = Clamp()
    node.properties["value"] = UNRESOLVED
    node.set_property("min", 0)
    node.set_property("max", 10)
    out = await run_node(node)
    assert out["result"] is UNRESOLVED
