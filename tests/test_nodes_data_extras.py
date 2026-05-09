"""Coverage-focused unit tests for talemate.game.engine.nodes.data.

This file complements tests/test_data_nodes.py (which already covers
DictGetPath helpers) by exercising the public `Node.run` of every other
data/* node — Sort, JSON, Contains, DictGet, DictSet, DictPop, DictUpdate,
MakeDict, Get, Set, SetConditional, MakeList, ListAppend, ListRemove,
Length, CapLength, SelectItem, DictCollector, ListCollector, CombineList,
DictKeyValuePairs, MakeKeyValuePair, UUID, UpdateObject, DictGetPath.

Every test runs the node via the shared `run_node` helper which wraps
`Node.run` in a real `GraphContext` — no internals are mocked.
"""

from __future__ import annotations

import json
import uuid as uuid_module

import pytest

from _node_test_helpers import apply_inputs, capture_outputs, run_node
from talemate.game.engine.nodes.core import GraphContext, InputValueError, UNRESOLVED
from talemate.game.engine.nodes.data import (
    CapLength,
    CombineList,
    Contains,
    DictCollector,
    DictGet,
    DictGetPath,
    DictKeyValuePairs,
    DictPop,
    DictSet,
    DictUpdate,
    Get,
    JSON,
    Length,
    ListAppend,
    ListCollector,
    ListRemove,
    MakeDict,
    MakeKeyValuePair,
    MakeList,
    SelectItem,
    Set,
    SetConditional,
    Sort,
    UUID,
    UpdateObject,
)


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


class TestSort:
    @pytest.mark.asyncio
    async def test_basic_sort_no_keys(self):
        out = await run_node(Sort(), inputs={"items": [3, 1, 2], "reverse": False})
        assert out["sorted_items"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_reverse_sort(self):
        out = await run_node(Sort(), inputs={"items": [3, 1, 2], "reverse": True})
        assert out["sorted_items"] == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_sort_keys_as_json_string(self):
        # JSON-encoded sort_keys must be parsed and applied to attributes via
        # getattr (so we use simple namespace objects).
        from types import SimpleNamespace

        items = [SimpleNamespace(weight=2), SimpleNamespace(weight=1)]
        out = await run_node(
            Sort(), inputs={"items": items, "sort_keys": '["weight"]'}
        )
        assert [i.weight for i in out["sorted_items"]] == [1, 2]

    @pytest.mark.asyncio
    async def test_sort_keys_invalid_type_raises(self):
        # A non-list, non-string sort_keys must be rejected.
        node = Sort()
        with pytest.raises(InputValueError):
            await run_node(node, inputs={"items": [1, 2], "sort_keys": 123})


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


class TestJSON:
    @pytest.mark.asyncio
    async def test_decodes_object(self):
        out = await run_node(JSON(), inputs={"json": '{"a": 1}'})
        assert out["data"] == {"a": 1}

    @pytest.mark.asyncio
    async def test_decodes_list(self):
        out = await run_node(JSON(), inputs={"json": "[1, 2]"})
        assert out["data"] == [1, 2]

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            await run_node(JSON(), inputs={"json": "not json"})


# ---------------------------------------------------------------------------
# Contains
# ---------------------------------------------------------------------------


class TestContains:
    @pytest.mark.asyncio
    async def test_list_contains(self):
        out = await run_node(Contains(), inputs={"object": [1, 2, 3], "value": 2})
        assert out["contains"] is True

    @pytest.mark.asyncio
    async def test_list_does_not_contain(self):
        out = await run_node(Contains(), inputs={"object": [1, 2, 3], "value": 5})
        assert out["contains"] is False

    @pytest.mark.asyncio
    async def test_dict_key_membership(self):
        # For dicts, membership tests against keys.
        out = await run_node(
            Contains(), inputs={"object": {"a": 1, "b": 2}, "value": "a"}
        )
        assert out["contains"] is True

    @pytest.mark.asyncio
    async def test_string_substring(self):
        out = await run_node(
            Contains(), inputs={"object": "hello world", "value": "world"}
        )
        assert out["contains"] is True

    @pytest.mark.asyncio
    async def test_generator_is_consumed_to_list(self):
        # Non-(dict/list/str) iterables get consumed via list().
        gen = (x for x in [1, 2, 3])
        out = await run_node(Contains(), inputs={"object": gen, "value": 2})
        assert out["contains"] is True


# ---------------------------------------------------------------------------
# DictGet / DictSet / DictPop / DictUpdate / MakeDict
# ---------------------------------------------------------------------------


class TestDictGet:
    @pytest.mark.asyncio
    async def test_returns_value_for_existing_key(self):
        out = await run_node(
            DictGet(), inputs={"dict": {"a": 1, "b": 2}, "key": "a"}
        )
        assert out["value"] == 1
        assert out["key"] == "a"

    @pytest.mark.asyncio
    async def test_missing_key_returns_none(self):
        out = await run_node(
            DictGet(), inputs={"dict": {"a": 1}, "key": "missing"}
        )
        assert out["value"] is None


class TestDictSet:
    @pytest.mark.asyncio
    async def test_sets_in_existing_dict(self):
        d = {"a": 1}
        out = await run_node(
            DictSet(), inputs={"dict": d, "key": "b", "value": 99}
        )
        assert out["dict"] == {"a": 1, "b": 99}
        # Operates in-place
        assert d == {"a": 1, "b": 99}

    @pytest.mark.asyncio
    async def test_creates_new_dict_when_input_unset(self):
        # If `dict` is UNRESOLVED, DictSet creates a new dict and writes into it.
        node = DictSet()
        node.set_property("key", "k")
        node.set_property("value", "v")
        # Don't set "dict" — it stays UNRESOLVED
        out = await run_node(node)
        assert out["dict"] == {"k": "v"}


class TestDictPop:
    @pytest.mark.asyncio
    async def test_pops_existing_key(self):
        d = {"a": 1, "b": 2}
        out = await run_node(DictPop(), inputs={"dict": d, "key": "a"})
        assert out["value"] == 1
        assert "a" not in out["dict"]

    @pytest.mark.asyncio
    async def test_pops_missing_key_returns_none(self):
        out = await run_node(DictPop(), inputs={"dict": {"a": 1}, "key": "x"})
        assert out["value"] is None


class TestDictUpdate:
    @pytest.mark.asyncio
    async def test_shallow_update_mutates_in_place(self):
        d = {"a": 1}
        out = await run_node(
            DictUpdate(),
            inputs={"dict": d, "dicts": [{"b": 2}, {"c": 3}], "merge": False},
        )
        assert out["dict"] == {"a": 1, "b": 2, "c": 3}

    @pytest.mark.asyncio
    async def test_deep_merge(self):
        d = {"a": {"x": 1}}
        out = await run_node(
            DictUpdate(),
            inputs={"dict": d, "dicts": [{"a": {"y": 2}}], "merge": True},
        )
        # Deep merge keeps `x` and adds `y`
        assert out["dict"] == {"a": {"x": 1, "y": 2}}

    @pytest.mark.asyncio
    async def test_deep_merge_empty_replaces(self):
        # An explicitly empty dict in the source replaces the target value.
        d = {"a": {"x": 1}}
        out = await run_node(
            DictUpdate(),
            inputs={"dict": d, "dicts": [{"a": {}}], "merge": True},
        )
        assert out["dict"] == {"a": {}}

    @pytest.mark.asyncio
    async def test_create_copy_does_not_mutate_input(self):
        d = {"a": 1}
        out = await run_node(
            DictUpdate(),
            inputs={"dict": d, "dicts": [{"b": 2}], "create_copy": True},
        )
        assert d == {"a": 1}  # original untouched
        assert out["dict"] == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_invalid_dicts_entry_wrapped_as_input_value_error(self):
        # If `dicts` contains something that `update()` can't consume,
        # DictUpdate must wrap the failure in InputValueError.
        with pytest.raises(InputValueError):
            await run_node(
                DictUpdate(),
                inputs={"dict": {}, "dicts": [123], "merge": False},
            )


class TestMakeDict:
    @pytest.mark.asyncio
    async def test_returns_deep_copy_of_property(self):
        node = MakeDict()
        node.set_property("data", {"a": [1]})
        out = await run_node(node)
        # Returned value is a deep copy — mutating it must not leak back into
        # the property.
        out["dict"]["a"].append(2)
        assert node.get_property("data") == {"a": [1]}


# ---------------------------------------------------------------------------
# Get / Set / SetConditional
# ---------------------------------------------------------------------------


class TestGetNode:
    @pytest.mark.asyncio
    async def test_dict_get(self):
        out = await run_node(
            Get(), inputs={"object": {"a": 1}, "attribute": "a"}
        )
        assert out["value"] == 1

    @pytest.mark.asyncio
    async def test_attribute_get(self):
        from types import SimpleNamespace

        ns = SimpleNamespace(foo="bar")
        out = await run_node(Get(), inputs={"object": ns, "attribute": "foo"})
        assert out["value"] == "bar"

    @pytest.mark.asyncio
    async def test_list_index_get(self):
        out = await run_node(
            Get(), inputs={"object": ["a", "b", "c"], "attribute": "1"}
        )
        assert out["value"] == "b"

    @pytest.mark.asyncio
    async def test_list_index_out_of_range_returns_unresolved(self):
        out = await run_node(
            Get(), inputs={"object": ["a"], "attribute": "5"}
        )
        assert out["value"] is UNRESOLVED

    @pytest.mark.asyncio
    async def test_list_non_integer_attribute_raises(self):
        with pytest.raises(InputValueError):
            await run_node(
                Get(), inputs={"object": ["a"], "attribute": "foo"}
            )


class TestSetNode:
    @pytest.mark.asyncio
    async def test_dict_set(self):
        d = {}
        out = await run_node(
            Set(), inputs={"object": d, "attribute": "k", "value": 1}
        )
        assert d == {"k": 1}
        assert out["object"] is d

    @pytest.mark.asyncio
    async def test_attribute_set(self):
        from types import SimpleNamespace

        ns = SimpleNamespace()
        await run_node(
            Set(), inputs={"object": ns, "attribute": "x", "value": 42}
        )
        assert ns.x == 42

    @pytest.mark.asyncio
    async def test_list_index_set(self):
        lst = [0, 0, 0]
        await run_node(
            Set(), inputs={"object": lst, "attribute": "1", "value": 99}
        )
        assert lst == [0, 99, 0]

    @pytest.mark.asyncio
    async def test_list_non_integer_attribute_raises(self):
        with pytest.raises(InputValueError):
            await run_node(
                Set(),
                inputs={"object": [0], "attribute": "x", "value": 1},
            )


class TestSetConditional:
    @pytest.mark.asyncio
    async def test_passes_through_state_input(self):
        d = {}
        # SetConditional needs `state`; pre-load it as a property.
        out = await run_node(
            SetConditional(),
            inputs={
                "state": "passthrough",
                "object": d,
                "attribute": "k",
                "value": "v",
            },
        )
        assert d == {"k": "v"}
        assert out["state"] == "passthrough"


# ---------------------------------------------------------------------------
# MakeList / ListAppend / ListRemove / Length / CapLength
# ---------------------------------------------------------------------------


class TestMakeList:
    @pytest.mark.asyncio
    async def test_returns_deep_copy_of_property(self):
        node = MakeList()
        node.set_property("items", [{"a": 1}])
        out = await run_node(node)
        # Modify result, ensure node property unaffected.
        out["list"][0]["a"] = 99
        assert node.get_property("items") == [{"a": 1}]

    @pytest.mark.asyncio
    async def test_unresolved_input_falls_back_to_property_item_type(self):
        # When the `item_type` input is unresolved, the node uses its property
        # value. This verifies the fallback path is reached without raising.
        node = MakeList()
        node.set_property("item_type", "str")
        node.set_property("items", ["x"])
        out = await run_node(node)
        assert out["list"] == ["x"]


class TestListAppend:
    @pytest.mark.asyncio
    async def test_appends_to_existing_list(self):
        lst = [1, 2]
        out = await run_node(
            ListAppend(), inputs={"list": lst, "item": 3}
        )
        assert out["list"] == [1, 2, 3]
        assert out["item"] == 3

    @pytest.mark.asyncio
    async def test_creates_list_when_input_unresolved(self):
        node = ListAppend()
        # Don't set `list` — leave unresolved.
        node.set_property("item", "first")
        out = await run_node(node)
        assert out["list"] == ["first"]


class TestListRemove:
    @pytest.mark.asyncio
    async def test_removes_existing_item(self):
        lst = [1, 2, 3]
        out = await run_node(ListRemove(), inputs={"list": lst, "item": 2})
        assert out["list"] == [1, 3]
        assert out["removed"] is True

    @pytest.mark.asyncio
    async def test_missing_item_marked_not_removed(self):
        lst = [1, 2, 3]
        out = await run_node(ListRemove(), inputs={"list": lst, "item": 99})
        assert out["list"] == [1, 2, 3]
        assert out["removed"] is False

    @pytest.mark.asyncio
    async def test_unresolved_list_raises(self):
        node = ListRemove()
        node.set_property("item", "anything")
        with pytest.raises(InputValueError):
            await run_node(node)


class TestLength:
    @pytest.mark.asyncio
    async def test_list(self):
        out = await run_node(Length(), inputs={"object": [1, 2, 3]})
        assert out["length"] == 3

    @pytest.mark.asyncio
    async def test_dict(self):
        out = await run_node(Length(), inputs={"object": {"a": 1, "b": 2}})
        assert out["length"] == 2

    @pytest.mark.asyncio
    async def test_generator_is_consumed_to_list(self):
        out = await run_node(Length(), inputs={"object": (x for x in range(4))})
        assert out["length"] == 4


class TestCapLength:
    @pytest.mark.asyncio
    async def test_under_limit_returns_unchanged(self):
        out = await run_node(
            CapLength(),
            inputs={"iterable": [1, 2, 3], "max_length": 10, "side": "right"},
        )
        assert out["capped"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_pops_from_right(self):
        out = await run_node(
            CapLength(),
            inputs={"iterable": [1, 2, 3, 4], "max_length": 2, "side": "right"},
        )
        assert out["capped"] == [1, 2]

    @pytest.mark.asyncio
    async def test_pops_from_left(self):
        out = await run_node(
            CapLength(),
            inputs={"iterable": "abcdef", "max_length": 3, "side": "left"},
        )
        assert out["capped"] == "def"

    @pytest.mark.asyncio
    async def test_invalid_iterable_type_raises(self):
        with pytest.raises(InputValueError):
            await run_node(
                CapLength(),
                inputs={"iterable": 12345, "max_length": 5, "side": "right"},
            )

    @pytest.mark.asyncio
    async def test_negative_max_length_raises(self):
        with pytest.raises(InputValueError):
            await run_node(
                CapLength(),
                inputs={"iterable": [1, 2], "max_length": -1, "side": "right"},
            )


# ---------------------------------------------------------------------------
# SelectItem (random/cycle/sorted_cycle/direct)
# ---------------------------------------------------------------------------


class TestSelectItem:
    @pytest.mark.asyncio
    async def test_direct_index_in_range(self):
        node = SelectItem()
        node.set_property("selection_function", "direct")
        node.set_property("index", 1)
        out = await run_node(node, inputs={"items": ["a", "b", "c"]})
        assert out["selected_item"] == "b"

    @pytest.mark.asyncio
    async def test_direct_index_out_of_range_unresolved(self):
        node = SelectItem()
        node.set_property("selection_function", "direct")
        node.set_property("index", 99)
        out = await run_node(node, inputs={"items": ["a"]})
        assert out["selected_item"] is UNRESOLVED

    @pytest.mark.asyncio
    async def test_cycle_advances_state(self):
        # Each run of a SelectItem(cycle) advances the cycle index in
        # state.data — verify three consecutive runs hit each distinct item.
        node = SelectItem()
        node.set_property("selection_function", "cycle")
        node.set_property("items", ["x", "y", "z"])

        with GraphContext() as state:
            await node.run(state)
            a = node.get_output_socket("selected_item").value
            await node.run(state)
            b = node.get_output_socket("selected_item").value
            await node.run(state)
            c = node.get_output_socket("selected_item").value
        assert {a, b, c} == {"x", "y", "z"}

    @pytest.mark.asyncio
    async def test_cycle_with_except_filters_items(self):
        node = SelectItem()
        node.set_property("selection_function", "cycle")
        out = await run_node(
            node,
            inputs={"items": ["a", "b", "c"], "except": "b"},
        )
        assert out["selected_item"] in {"a", "c"}

    @pytest.mark.asyncio
    async def test_sorted_cycle_returns_sorted_first(self):
        node = SelectItem()
        node.set_property("selection_function", "sorted_cycle")
        out = await run_node(node, inputs={"items": ["c", "a", "b"]})
        # First run -> first item of sorted list
        assert out["selected_item"] == "a"

    @pytest.mark.asyncio
    async def test_random_returns_member(self):
        node = SelectItem()
        node.set_property("selection_function", "random")
        out = await run_node(node, inputs={"items": ["x"]})
        assert out["selected_item"] == "x"


# ---------------------------------------------------------------------------
# DictCollector / ListCollector / CombineList — dynamic input nodes.
# To exercise the dynamic input branches, we wire a ValueNode source to one
# of the dynamic input slots so socket.source is set and socket.value can be
# read.
# ---------------------------------------------------------------------------


from talemate.game.engine.nodes.core import Graph, Node


class _ValueProducer(Node):
    """Simple producer used to feed dynamic-socket collector inputs."""

    def __init__(self, value, title="Producer", **kwargs):
        super().__init__(title=title, **kwargs)
        self._emit = value

    def setup(self):
        self.add_output("value")

    async def run(self, state):
        self.set_output_values({"value": self._emit})


def _wire_dynamic_input(graph, collector, value, label_name=None):
    """Add a dynamic input to the collector and wire a producer to it.

    Returns (producer, dynamic_socket).
    """
    # Build the dynamic-input socket via the same mechanism the editor uses.
    template = collector.dynamic_input_label
    label = label_name or template.format(i=len(collector.dynamic_inputs))
    collector.dynamic_inputs.append(
        {"name": label, "type": collector.dynamic_input_type}
    )
    sock = collector.add_input(label, socket_type=collector.dynamic_input_type, optional=True)

    producer = _ValueProducer(value)
    graph.add_node(producer)
    graph.connect(producer.outputs[0], sock)
    return producer, sock


class TestDictCollector:
    @pytest.mark.asyncio
    async def test_collects_tuple_inputs_as_keyvalues(self):
        # Wire a Producer of tuple ("k", "v") into a dynamic input — collector
        # treats 2-tuples as (key, value).
        g = Graph()
        coll = DictCollector()
        g.add_node(coll)
        producer, _ = _wire_dynamic_input(g, coll, ("foo", "bar"))

        await g.execute()
        assert coll.get_output_socket("dict").value == UNRESOLVED  # outside ctx
        # Re-execute to read the value within graph state.
        with GraphContext() as state:
            # Set up inputs / sources inside the context.
            await producer.run(state)
            await coll.run(state)
            result = coll.get_output_socket("dict").value
        assert result == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_inherits_existing_dict(self):
        g = Graph()
        coll = DictCollector()
        coll.set_property("dict", {"keep": 1})
        g.add_node(coll)
        producer, _ = _wire_dynamic_input(g, coll, ("new", 2))

        with GraphContext() as state:
            await producer.run(state)
            await coll.run(state)
            result = coll.get_output_socket("dict").value
        assert result == {"keep": 1, "new": 2}


class TestListCollector:
    @pytest.mark.asyncio
    async def test_collects_appended_inputs(self):
        g = Graph()
        coll = ListCollector()
        g.add_node(coll)
        p1, _ = _wire_dynamic_input(g, coll, "first", "item0")
        p2, _ = _wire_dynamic_input(g, coll, "second", "item1")

        with GraphContext() as state:
            await p1.run(state)
            await p2.run(state)
            await coll.run(state)
            result = coll.get_output_socket("list").value
        # Order depends on iteration order of self.inputs which is insertion
        # order — both items should be present.
        assert sorted(result) == ["first", "second"]


class TestCombineList:
    @pytest.mark.asyncio
    async def test_extends_each_input_list(self):
        g = Graph()
        coll = CombineList()
        g.add_node(coll)
        p1, _ = _wire_dynamic_input(g, coll, [1, 2], "list0")
        p2, _ = _wire_dynamic_input(g, coll, [3, 4], "list1")

        with GraphContext() as state:
            await p1.run(state)
            await p2.run(state)
            await coll.run(state)
            result = coll.get_output_socket("list").value
        assert result == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_create_copy_does_not_mutate_seed_list(self):
        g = Graph()
        coll = CombineList()
        seed = [0]
        coll.set_property("create_copy", True)
        coll.set_property("list", seed)
        g.add_node(coll)
        p1, _ = _wire_dynamic_input(g, coll, [1, 2], "list0")

        with GraphContext() as state:
            await p1.run(state)
            await coll.run(state)
            result = coll.get_output_socket("list").value
        # Result has both — seed is NOT mutated thanks to create_copy.
        assert result == [0, 1, 2]
        assert seed == [0]


# ---------------------------------------------------------------------------
# DictKeyValuePairs / MakeKeyValuePair
# ---------------------------------------------------------------------------


class TestDictKeyValuePairs:
    @pytest.mark.asyncio
    async def test_emits_pairs_list(self):
        out = await run_node(
            DictKeyValuePairs(), inputs={"dict": {"a": 1, "b": 2}}
        )
        assert sorted(out["kvs"]) == [("a", 1), ("b", 2)]


class TestMakeKeyValuePair:
    @pytest.mark.asyncio
    async def test_returns_tuple_and_separate_outputs(self):
        out = await run_node(
            MakeKeyValuePair(), inputs={"key": "k", "value": 9}
        )
        assert out["kv"] == ("k", 9)
        assert out["key"] == "k"
        assert out["value"] == 9


# ---------------------------------------------------------------------------
# UUID
# ---------------------------------------------------------------------------


class TestUUID:
    @pytest.mark.asyncio
    async def test_emits_full_uuid_string(self):
        node = UUID()
        node.set_property("max_length", 36)
        out = await run_node(node)
        # Round-trip via uuid.UUID validates the format.
        assert uuid_module.UUID(out["uuid"])  # raises if invalid

    @pytest.mark.asyncio
    async def test_truncates_to_max_length(self):
        node = UUID()
        node.set_property("max_length", 8)
        out = await run_node(node)
        assert len(out["uuid"]) == 8

    @pytest.mark.asyncio
    async def test_zero_max_length_returns_full(self):
        # max_length <= 0 leaves the string untruncated (per the implementation).
        node = UUID()
        node.set_property("max_length", 0)
        out = await run_node(node)
        assert len(out["uuid"]) == 36


# ---------------------------------------------------------------------------
# UpdateObject (dynamic-socket node)
# ---------------------------------------------------------------------------


class TestUpdateObject:
    @pytest.mark.asyncio
    async def test_updates_dict_target(self):
        g = Graph()
        node = UpdateObject()
        g.add_node(node)
        p, _ = _wire_dynamic_input(g, node, ("k", "v"))

        target = {}
        node.set_property("object", target)

        with GraphContext() as state:
            await p.run(state)
            await node.run(state)
            assert node.get_output_socket("object").value is target
        assert target == {"k": "v"}

    @pytest.mark.asyncio
    async def test_updates_object_target_via_setattr(self):
        from types import SimpleNamespace

        g = Graph()
        node = UpdateObject()
        g.add_node(node)
        p, _ = _wire_dynamic_input(g, node, ("attr", 5))

        target = SimpleNamespace()
        node.set_property("object", target)

        with GraphContext() as state:
            await p.run(state)
            await node.run(state)
        assert target.attr == 5


# ---------------------------------------------------------------------------
# DictGetPath.run — the static helpers are covered in test_data_nodes.py;
# here we exercise the actual run() path including the derived `key`
# property side effect.
# ---------------------------------------------------------------------------


class TestDictGetPathRun:
    @pytest.mark.asyncio
    async def test_resolved_path_emits_value_and_found(self):
        node = DictGetPath()
        node.set_property("path", "a.b")
        out = await run_node(
            node, inputs={"dict": {"a": {"b": 99}}, "path": "a.b"}
        )
        assert out["value"] == 99
        assert out["found"] is True
        assert out["path"] == "a.b"

    @pytest.mark.asyncio
    async def test_missing_path_returns_default(self):
        node = DictGetPath()
        out = await run_node(
            node,
            inputs={"dict": {"a": {}}, "path": "a.b", "default": "fallback"},
        )
        assert out["value"] == "fallback"
        assert out["found"] is False

    @pytest.mark.asyncio
    async def test_run_publishes_collector_key_property(self):
        # When a path is provided, run() updates the node's `key` property in
        # the active GraphState (not the static node.properties dict) so a
        # downstream DictCollector/UpdateObject can pick it up via
        # best_key_name_for_socket. Read it inside the GraphContext.
        node = DictGetPath()
        node.set_property("path", "modes.go")
        with GraphContext() as state:
            # Pre-load the dict input as a property fallback.
            node.properties["dict"] = {"modes": {"go": True}}
            await node.run(state)
            assert state.get_node_property(node, "key") == "go"
