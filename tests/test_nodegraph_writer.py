"""
Tests for ``talemate.game.engine.nodes.tools`` mutation + layout APIs.

See also ``tests/test_nodegraph_tools.py`` for the analysis layer.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from talemate.game.engine.nodes.tools import (
    AlreadyConnectedError,
    CycleError,
    DynamicInputError,
    GraphWriter,
    GroupColor,
    GroupError,
    LayoutError,
    LayoutOptions,
    NodeNotFoundError,
    NotConnectedError,
    UnknownPropertyError,
    UnknownRegistryError,
    UnknownSocketError,
    add_group,
    analysis,
    ensure_registry_loaded,
    get_node_metadata,
    layout_graph,
    load_graph,
)
from talemate.game.engine.nodes.tools import writer as writer_mod
from talemate.game.engine.nodes.tools.layout import (
    WCCKind,
    _apply_estimated_heights,
    _classify_wcc,
    _estimate_height,
    _wcc_over_subset,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DIRECTOR_FIXTURE = (
    REPO_ROOT
    / "src/talemate/agents/director/modules/director-action-direct-story-arc.json"
)
ROLL_DICE_FIXTURE = REPO_ROOT / "scenes/infinity-quest/nodes/roll-dice.json"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _load_registry():
    ensure_registry_loaded()
    yield


def _empty_graph() -> dict:
    """Minimal valid graph dict for writer tests."""

    return {
        "title": "Test",
        "id": str(uuid.uuid4()),
        "registry": "test/WriterGraph",
        "base_type": "core/Graph",
        "extends": None,
        "properties": {},
        "nodes": {},
        "edges": {},
        "groups": [],
        "comments": [],
        "inputs": [],
        "outputs": [],
        "module_properties": {},
    }


def _node_at(graph: dict, nid: str) -> dict:
    return graph["nodes"][nid]


# ---------------------------------------------------------------------------
# add_node / remove_node
# ---------------------------------------------------------------------------


def test_add_node_happy_path():
    g = _empty_graph()
    w = GraphWriter(g)
    nid = w.add_node(
        "data/number/Random",
        title="Roll",
        properties={"method": "integer"},
    )

    # id is a valid uuid4 string
    uuid.UUID(nid)  # raises if invalid

    node = _node_at(g, nid)
    assert node["registry"] == "data/number/Random"
    assert node["title"] == "Roll"
    assert node["properties"] == {"method": "integer"}
    assert node["base_type"] == "core/Node"
    assert node["x"] == 0
    assert node["y"] == 0
    assert node["width"] == 210
    # Writer intentionally does NOT set a default height; layout owns
    # height estimation so collision avoidance has a realistic rect.
    assert "height" not in node


def test_add_node_respects_explicit_height():
    g = _empty_graph()
    w = GraphWriter(g)
    nid = w.add_node(
        "core/Watch",
        title="Pinned",
        height=200,
    )
    node = _node_at(g, nid)
    assert node["height"] == 200


def test_add_node_unknown_registry_raises():
    g = _empty_graph()
    w = GraphWriter(g)
    with pytest.raises(UnknownRegistryError, match="not registered"):
        w.add_node("totally/Fake")


def test_add_node_dynamic_class_gets_dynamic_inputs_list():
    g = _empty_graph()
    w = GraphWriter(g)
    nid = w.add_node("data/string/AdvancedFormat", title="Format")
    assert _node_at(g, nid).get("dynamic_inputs") == []


def test_remove_node_prunes_edges():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch", title="A")
    b = w.add_node("core/Watch", title="B")
    c = w.add_node("core/Watch", title="C")

    w.connect(a, "value", b, "value")
    w.connect(b, "value", c, "value")

    writer_mod.remove_node(g, b)

    assert b not in g["nodes"]
    # Every edge that referenced b should be gone
    for src, targets in g["edges"].items():
        assert src.split(".", 1)[0] != b
        for tgt in targets:
            assert tgt.split(".", 1)[0] != b


def test_remove_node_missing_raises():
    g = _empty_graph()
    GraphWriter(g)
    with pytest.raises(NodeNotFoundError):
        writer_mod.remove_node(g, "no-such-node")


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


def test_connect_happy_path_creates_edge():
    g = _empty_graph()
    w = GraphWriter(g)
    get_min = w.add_node(
        "state/GetState",
        title="GET min",
        properties={"name": "min", "scope": "local"},
    )
    roll = w.add_node(
        "data/number/Random",
        title="Roll",
        properties={"method": "integer"},
    )

    w.connect(get_min, "value", roll, "min")

    edges = g["edges"]
    key = f"{get_min}.value"
    assert key in edges
    assert f"{roll}.min" in edges[key]


def test_connect_dotted_shortcut():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")
    w.connect(f"{a}.value", f"{b}.value")
    assert f"{a}.value" in g["edges"]


def test_connect_unknown_source_socket():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")
    with pytest.raises(UnknownSocketError, match="output socket 'nope'"):
        w.connect(a, "nope", b, "value")


def test_connect_unknown_target_socket():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")
    with pytest.raises(UnknownSocketError, match="input socket 'nope'"):
        w.connect(a, "value", b, "nope")


def test_connect_already_connected_raises():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")
    c = w.add_node("core/Watch")
    w.connect(a, "value", b, "value")
    with pytest.raises(AlreadyConnectedError):
        w.connect(c, "value", b, "value")


def test_connect_cycle_rolls_back_edge():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")
    w.connect(a, "value", b, "value")

    with pytest.raises(CycleError):
        w.connect(b, "value", a, "value")

    # The rolled-back edge must not be present.
    assert f"{b}.value" not in g["edges"]
    # The original edge must still be there.
    assert f"{a}.value" in g["edges"]


def test_connect_self_loop_is_cycle():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    with pytest.raises(CycleError):
        w.connect(a, "value", a, "value")
    assert f"{a}.value" not in g["edges"]


def test_disconnect_happy_path_and_missing():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")
    w.connect(a, "value", b, "value")
    w.disconnect(a, "value", b, "value")
    assert f"{a}.value" not in g["edges"]

    with pytest.raises(NotConnectedError):
        w.disconnect(a, "value", b, "value")


# ---------------------------------------------------------------------------
# dynamic inputs
# ---------------------------------------------------------------------------


def test_add_dynamic_input_on_dynamic_node():
    g = _empty_graph()
    w = GraphWriter(g)
    fmt = w.add_node("data/string/AdvancedFormat", title="Format")
    w.add_dynamic_input(fmt, "item0", "any")
    w.add_dynamic_input(fmt, "item1", "str")
    dyn = _node_at(g, fmt)["dynamic_inputs"]
    assert [d["name"] for d in dyn] == ["item0", "item1"]
    assert dyn[1]["type"] == "str"


def test_add_dynamic_input_duplicate_raises():
    g = _empty_graph()
    w = GraphWriter(g)
    fmt = w.add_node("data/string/AdvancedFormat")
    w.add_dynamic_input(fmt, "item0")
    with pytest.raises(DynamicInputError, match="already exists"):
        w.add_dynamic_input(fmt, "item0")


def test_add_dynamic_input_on_non_dynamic_raises():
    g = _empty_graph()
    w = GraphWriter(g)
    # core/Watch is a regular Node, not a DynamicSocketNodeBase subclass.
    a = w.add_node("core/Watch")
    with pytest.raises(DynamicInputError, match="DynamicSocketNodeBase"):
        w.add_dynamic_input(a, "item0")


def test_remove_dynamic_input_sweeps_edges():
    g = _empty_graph()
    w = GraphWriter(g)
    fmt = w.add_node("data/string/AdvancedFormat")
    src = w.add_node("core/Watch")
    w.add_dynamic_input(fmt, "item0")
    w.connect(src, "value", fmt, "item0")
    assert f"{src}.value" in g["edges"]

    w.remove_dynamic_input(fmt, "item0")
    assert f"{src}.value" not in g["edges"]
    assert _node_at(g, fmt)["dynamic_inputs"] == []

    with pytest.raises(DynamicInputError, match="No dynamic input"):
        w.remove_dynamic_input(fmt, "item0")


# ---------------------------------------------------------------------------
# round-trip via load / save
# ---------------------------------------------------------------------------


def test_graphwriter_load_save_round_trip(tmp_path):
    src = tmp_path / "orig.json"
    g = _empty_graph()
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    g["nodes"] = {
        a: {
            "title": "A",
            "id": a,
            "properties": {},
            "registry": "core/Watch",
            "base_type": "core/Node",
            "x": 10,
            "y": 10,
            "width": 210,
            "height": 100,
        },
        b: {
            "title": "B",
            "id": b,
            "properties": {},
            "registry": "core/Watch",
            "base_type": "core/Node",
            "x": 300,
            "y": 10,
            "width": 210,
            "height": 100,
        },
    }
    g["edges"] = {f"{a}.value": [f"{b}.value"]}
    import json

    src.write_text(json.dumps(g), encoding="utf-8")

    w = GraphWriter.load(src)
    dest = tmp_path / "out.json"
    saved = w.save(dest)
    assert saved == dest

    reloaded = load_graph(dest)
    summary = analysis.summarize(reloaded)
    assert summary.node_count == 2


def test_graphwriter_modify_and_reload(tmp_path):
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")
    w.connect(a, "value", b, "value")

    dest = tmp_path / "mod.json"
    w.save(dest)

    reloaded = load_graph(dest)
    assert analysis.summarize(reloaded).node_count == 2
    assert f"{a}.value" in reloaded["edges"]


def test_save_without_path_raises():
    w = GraphWriter(_empty_graph())
    with pytest.raises(writer_mod.WriterError):
        w.save()


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------


def _make_layout_graph_with_existing() -> tuple[dict, list[str]]:
    """Graph with one existing node at (50, 20) plus three new nodes at origin."""

    g = _empty_graph()
    existing = str(uuid.uuid4())
    g["nodes"][existing] = {
        "title": "Existing",
        "id": existing,
        "properties": {},
        "registry": "core/Watch",
        "base_type": "core/Node",
        "x": 50,
        "y": 20,
        "width": 200,
        "height": 100,
    }

    w = GraphWriter(g)
    a = w.add_node("core/Watch", title="A")
    b = w.add_node("core/Watch", title="B")
    c = w.add_node("core/Watch", title="C")
    w.connect(a, "value", b, "value")
    w.connect(b, "value", c, "value")

    return g, [a, b, c]


def test_layout_anchor_right_places_offset_from_existing():
    g, target = _make_layout_graph_with_existing()
    a, b, c = target
    existing_x = 50
    existing_right = existing_x + 200  # 250

    opts = LayoutOptions()
    layout_graph(g, new_node_ids=target, anchor="right", options=opts)

    # All target nodes end up at x >= existing_right + 300
    first_col = existing_right + 300
    assert g["nodes"][a]["x"] == first_col
    assert g["nodes"][b]["x"] == first_col + opts.col_width
    assert g["nodes"][c]["x"] == first_col + 2 * opts.col_width
    # And they share min_y with the existing node.
    assert g["nodes"][a]["y"] == 20


def test_layout_anchor_below_places_below_existing():
    g, target = _make_layout_graph_with_existing()
    a, b, c = target

    layout_graph(g, new_node_ids=target, anchor="below")

    # Existing node ends at y = 120; target should be at y = 320.
    assert g["nodes"][a]["y"] == 320
    assert g["nodes"][a]["x"] == 50  # same min_x as existing


def test_layout_anchor_full_starts_at_origin_and_relays_everything():
    g, target = _make_layout_graph_with_existing()
    # anchor=full ignores new_node_ids and relays everything.
    existing_id = next(nid for nid in g["nodes"] if nid not in target)

    layout_graph(g, anchor="full")

    xs = [n["x"] for n in g["nodes"].values()]
    ys = [n["y"] for n in g["nodes"].values()]
    assert min(xs) == 0
    assert min(ys) == 0
    # The existing node is now in the target set.
    assert g["nodes"][existing_id]["x"] >= 0


def test_layout_topological_ordering():
    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch", title="A")
    b = w.add_node("core/Watch", title="B")
    c = w.add_node("core/Watch", title="C")
    w.connect(a, "value", b, "value")
    w.connect(b, "value", c, "value")

    layout_graph(g, new_node_ids=[a, b, c], anchor="full")

    ax = g["nodes"][a]["x"]
    bx = g["nodes"][b]["x"]
    cx = g["nodes"][c]["x"]
    assert ax < bx < cx


def test_layout_collision_avoidance_within_column():
    """Two isolated nodes both at depth 0 must land in different rows."""

    g = _empty_graph()
    w = GraphWriter(g)
    a = w.add_node("core/Watch")
    b = w.add_node("core/Watch")

    layout_graph(g, new_node_ids=[a, b], anchor="full")

    assert g["nodes"][a]["x"] == g["nodes"][b]["x"]
    assert g["nodes"][a]["y"] != g["nodes"][b]["y"]


def test_layout_isolated_node_gets_column_zero():
    g = _empty_graph()
    w = GraphWriter(g)
    solo = w.add_node("core/Watch")

    layout_graph(g, new_node_ids=[solo], anchor="full")

    assert g["nodes"][solo]["x"] == 0
    assert g["nodes"][solo]["y"] == 0


def test_layout_does_not_move_nodes_outside_target_set():
    g, target = _make_layout_graph_with_existing()
    existing_id = next(nid for nid in g["nodes"] if nid not in target)
    orig_x = g["nodes"][existing_id]["x"]
    orig_y = g["nodes"][existing_id]["y"]

    layout_graph(g, new_node_ids=target, anchor="right")

    assert g["nodes"][existing_id]["x"] == orig_x
    assert g["nodes"][existing_id]["y"] == orig_y


def test_layout_unknown_anchor_raises():
    g = _empty_graph()
    with pytest.raises(LayoutError):
        layout_graph(g, anchor="sideways")


def test_layout_unknown_target_raises():
    g = _empty_graph()
    w = GraphWriter(g)
    w.add_node("core/Watch")  # layout needs at least one real node
    with pytest.raises(LayoutError):
        layout_graph(g, new_node_ids=["no-such-node"], anchor="right")


# ---------------------------------------------------------------------------
# combined smoke: writer + layout + analysis
# ---------------------------------------------------------------------------


def test_combined_smoke_build_layout_reload(tmp_path):
    w = GraphWriter(_empty_graph())
    in_node = w.add_node(
        "core/Input",
        title="IN value",
        properties={"input_type": "int", "input_name": "value"},
    )
    get_state = w.add_node(
        "state/GetState",
        title="GET local.max",
        properties={"name": "max", "scope": "local"},
    )
    roll = w.add_node(
        "data/number/Random",
        title="Roll",
        properties={"method": "integer"},
    )
    out_node = w.add_node(
        "core/Output",
        title="OUT result",
        properties={"output_type": "int", "output_name": "result"},
    )

    w.connect(in_node, "value", roll, "min")
    w.connect(get_state, "value", roll, "max")
    w.connect(roll, "result", out_node, "value")

    layout_graph(
        w.graph,
        new_node_ids=[in_node, get_state, roll, out_node],
        anchor="full",
    )

    dest = tmp_path / "combined.json"
    w.save(dest)

    reloaded = load_graph(dest)
    summary = analysis.summarize(reloaded)
    assert summary.node_count == 4

    # Layout sanity: Random must sit right of both of its inputs.
    nodes = reloaded["nodes"]
    assert nodes[roll]["x"] > nodes[in_node]["x"]
    assert nodes[roll]["x"] > nodes[get_state]["x"]
    # And the output must sit right of Random.
    assert nodes[out_node]["x"] > nodes[roll]["x"]


# ---------------------------------------------------------------------------
# layout v2: height estimation
# ---------------------------------------------------------------------------


def _expected_height(registry: str, options: LayoutOptions) -> int:
    """Replicate the layout _estimate_height formula for test assertions.

    Mirrors ``layout._estimate_height`` (v2.1 formula):

        title_bar_height
          + (max(num_inputs, num_outputs) + num_properties) * socket_row_height
          + padding
          + dynamic_socket_bonus  (iff class is a DynamicSocketNodeBase)

    Floored at ``options.min_height``.
    """

    meta = get_node_metadata(registry)
    rows = max(len(meta.inputs), len(meta.outputs)) + len(meta.properties)
    estimated = (
        options.title_bar_height + rows * options.socket_row_height + options.padding
    )
    if meta.is_dynamic:
        estimated += options.dynamic_socket_bonus
    return max(estimated, options.min_height)


def test_layout_estimates_height_for_target_nodes():
    """A target node with no height gets an estimate matching the formula."""

    g = _empty_graph()
    w = GraphWriter(g)
    nid = w.add_node("state/SetState")
    # Writer must not have set a height.
    assert "height" not in g["nodes"][nid]

    opts = LayoutOptions()
    layout_graph(g, new_node_ids=[nid], anchor="full", options=opts)

    expected = _expected_height("state/SetState", opts)
    assert g["nodes"][nid]["height"] == expected
    assert expected > opts.min_height  # sanity: SetState has real sockets


def test_layout_height_formula_is_deterministic():
    """Direct unit test on _estimate_height to pin the v2.1 formula."""

    g = _empty_graph()
    w = GraphWriter(g)
    nid = w.add_node("state/SetState")
    opts = LayoutOptions()

    meta = get_node_metadata("state/SetState")
    rows = max(len(meta.inputs), len(meta.outputs)) + len(meta.properties)
    manual = opts.title_bar_height + rows * opts.socket_row_height + opts.padding
    if meta.is_dynamic:
        manual += opts.dynamic_socket_bonus
    manual = max(manual, opts.min_height)
    assert _estimate_height(g["nodes"][nid], opts) == manual


def test_layout_overwrites_target_height_even_when_preset():
    """Target-set height is always replaced, even if the caller pinned one."""

    g = _empty_graph()
    w = GraphWriter(g)
    nid = w.add_node("core/Watch", height=999)
    assert g["nodes"][nid]["height"] == 999  # pinned by writer argument

    opts = LayoutOptions()
    layout_graph(g, new_node_ids=[nid], anchor="full", options=opts)

    expected = _expected_height("core/Watch", opts)
    assert g["nodes"][nid]["height"] == expected
    assert g["nodes"][nid]["height"] != 999


def test_layout_does_not_touch_heights_outside_target_set():
    """Non-target nodes keep whatever height they had before layout ran."""

    g = _empty_graph()
    # Hand-build a non-target existing node with a pinned height.
    existing_id = str(uuid.uuid4())
    g["nodes"][existing_id] = {
        "title": "Existing",
        "id": existing_id,
        "properties": {},
        "registry": "core/Watch",
        "base_type": "core/Node",
        "x": 0,
        "y": 0,
        "width": 210,
        "height": 777,
    }

    w = GraphWriter(g)
    new_id = w.add_node("core/Watch")

    layout_graph(g, new_node_ids=[new_id], anchor="right")

    # Existing node keeps its pinned height
    assert g["nodes"][existing_id]["height"] == 777
    # New node got an estimate
    assert g["nodes"][new_id]["height"] != 777
    assert g["nodes"][new_id]["height"] == _expected_height(
        "core/Watch", LayoutOptions()
    )


def test_apply_estimated_heights_skips_unknown_registry():
    """Helper falls back to min_height for a registry not in NODES."""

    g = _empty_graph()
    bogus = str(uuid.uuid4())
    g["nodes"][bogus] = {
        "title": "Mystery",
        "id": bogus,
        "properties": {},
        "registry": "totally/Fake",
        "base_type": "core/Node",
        "x": 0,
        "y": 0,
        "width": 210,
    }
    opts = LayoutOptions()
    _apply_estimated_heights(g, {bogus}, opts)
    assert g["nodes"][bogus]["height"] == opts.min_height


# ---------------------------------------------------------------------------
# layout v2: WCC classification
# ---------------------------------------------------------------------------


def test_wcc_classify_input_output_passthrough_is_top():
    """A 2-node Input->Output chain is TOP (passthrough)."""

    g = _empty_graph()
    w = GraphWriter(g)
    in_node = w.add_node(
        "core/Input",
        properties={"input_type": "any", "input_name": "state"},
    )
    out_node = w.add_node(
        "core/Output",
        properties={"output_type": "any", "output_name": "state"},
    )
    w.connect(in_node, "value", out_node, "value")

    components = _wcc_over_subset(g, {in_node, out_node})
    assert len(components) == 1
    info = _classify_wcc(g, components[0])
    assert info.kind is WCCKind.TOP


def test_wcc_classify_stage_wins_over_input():
    """A chain containing both Input and Stage classifies as STAGE."""

    g = _empty_graph()
    w = GraphWriter(g)
    in_node = w.add_node(
        "core/Input",
        properties={"input_type": "int", "input_name": "value"},
    )
    stage = w.add_node("core/Stage", properties={"stage": 3})
    w.connect(in_node, "value", stage, "state")

    info = _classify_wcc(g, sorted([in_node, stage]))
    assert info.kind is WCCKind.STAGE
    assert info.stage_value == 3


def test_wcc_classify_stage_value_is_min_across_multiple_stage_nodes():
    """If a WCC contains multiple Stage nodes, stage_value is the min."""

    g = _empty_graph()
    w = GraphWriter(g)
    s_low = w.add_node("core/Stage", properties={"stage": 1})
    s_high = w.add_node("core/Stage", properties={"stage": 5})
    # Link them so they're one WCC.
    w.connect(s_low, "state", s_high, "state")

    info = _classify_wcc(g, sorted([s_low, s_high]))
    assert info.kind is WCCKind.STAGE
    assert info.stage_value == 1


def test_wcc_classify_output_only_is_bottom():
    """A chain with only Output (no Input, no Stage) is BOTTOM."""

    g = _empty_graph()
    w = GraphWriter(g)
    out_node = w.add_node(
        "core/Output",
        properties={"output_type": "int", "output_name": "result"},
    )
    watch = w.add_node("core/Watch")
    w.connect(watch, "value", out_node, "value")

    info = _classify_wcc(g, sorted([out_node, watch]))
    assert info.kind is WCCKind.BOTTOM


def test_wcc_classify_plain_watch_is_middle():
    """A lone Watch (no Input / Output / Stage) is MIDDLE."""

    g = _empty_graph()
    w = GraphWriter(g)
    watch = w.add_node("core/Watch")

    info = _classify_wcc(g, [watch])
    assert info.kind is WCCKind.MIDDLE


# ---------------------------------------------------------------------------
# layout v2: vertical band ordering
# ---------------------------------------------------------------------------


def test_layout_vertical_band_ordering():
    """TOP on top, STAGE(0) above STAGE(1), BOTTOM on the bottom."""

    g = _empty_graph()
    w = GraphWriter(g)

    # TOP component: Input -> Watch (passthrough)
    top_in = w.add_node(
        "core/Input",
        title="IN top",
        properties={"input_type": "any", "input_name": "x"},
    )
    top_watch = w.add_node("core/Watch", title="top watch")
    w.connect(top_in, "value", top_watch, "value")

    # STAGE(0) component
    stage0 = w.add_node("core/Stage", title="Stage 0", properties={"stage": 0})
    stage0_watch = w.add_node("core/Watch", title="s0 watch")
    w.connect(stage0_watch, "value", stage0, "state")

    # STAGE(1) component
    stage1 = w.add_node("core/Stage", title="Stage 1", properties={"stage": 1})
    stage1_watch = w.add_node("core/Watch", title="s1 watch")
    w.connect(stage1_watch, "value", stage1, "state")

    # BOTTOM component: Watch -> Output
    bottom_watch = w.add_node("core/Watch", title="bottom watch")
    bottom_out = w.add_node(
        "core/Output",
        title="OUT bottom",
        properties={"output_type": "any", "output_name": "x"},
    )
    w.connect(bottom_watch, "value", bottom_out, "value")

    opts = LayoutOptions()
    layout_graph(g, anchor="full", options=opts)

    def _comp_y(node_ids: list[str]) -> int:
        return min(g["nodes"][nid]["y"] for nid in node_ids)

    def _comp_bottom(node_ids: list[str]) -> int:
        return max(g["nodes"][nid]["y"] + g["nodes"][nid]["height"] for nid in node_ids)

    top_y = _comp_y([top_in, top_watch])
    stage0_y = _comp_y([stage0, stage0_watch])
    stage1_y = _comp_y([stage1, stage1_watch])
    bottom_y = _comp_y([bottom_watch, bottom_out])

    # Strict vertical ordering
    assert top_y < stage0_y < stage1_y < bottom_y

    # Each band separated from the next by at least band_gap.
    assert stage0_y - _comp_bottom([top_in, top_watch]) >= opts.band_gap
    assert stage1_y - _comp_bottom([stage0, stage0_watch]) >= opts.band_gap
    assert bottom_y - _comp_bottom([stage1, stage1_watch]) >= opts.band_gap

    # Bands don't vertically overlap.
    assert _comp_bottom([top_in, top_watch]) < stage0_y
    assert _comp_bottom([stage0, stage0_watch]) < stage1_y
    assert _comp_bottom([stage1, stage1_watch]) < bottom_y


# ---------------------------------------------------------------------------
# layout v2.1: new formula, dynamic bonus, height-aware + predecessor-aware
# packing
# ---------------------------------------------------------------------------


def test_layout_v21_height_formula_matches_explicit_math():
    """Spell out the v2.1 height math for state/SetState explicitly."""

    g = _empty_graph()
    w = GraphWriter(g)
    nid = w.add_node("state/SetState")
    opts = LayoutOptions()

    meta = get_node_metadata("state/SetState")
    rows = max(len(meta.inputs), len(meta.outputs)) + len(meta.properties)
    expected = opts.title_bar_height + rows * opts.socket_row_height + opts.padding
    # state/SetState is a regular node, not a DynamicSocketNodeBase.
    assert meta.is_dynamic is False
    expected = max(expected, opts.min_height)

    assert _estimate_height(g["nodes"][nid], opts) == expected


def test_layout_v21_dynamic_socket_bonus_applies_to_dynamic_class():
    """data/string/AdvancedFormat is a DynamicSocketNodeBase subclass."""

    g = _empty_graph()
    w = GraphWriter(g)
    dyn_nid = w.add_node("data/string/AdvancedFormat")

    opts = LayoutOptions()
    meta = get_node_metadata("data/string/AdvancedFormat")
    assert meta.is_dynamic is True

    rows = max(len(meta.inputs), len(meta.outputs)) + len(meta.properties)
    base = opts.title_bar_height + rows * opts.socket_row_height + opts.padding
    expected = max(base + opts.dynamic_socket_bonus, opts.min_height)
    assert _estimate_height(g["nodes"][dyn_nid], opts) == expected

    # Matched non-dynamic control: a node without the bonus should be
    # exactly ``dynamic_socket_bonus`` shorter for the same row count.
    # Compute a plain height using the same formula minus the bonus and
    # assert the delta.
    without_bonus = max(base, opts.min_height)
    assert expected - without_bonus == opts.dynamic_socket_bonus


def test_layout_v21_height_aware_column_packing():
    """Two isolated nodes in the same column respect variable heights."""

    from talemate.game.engine.nodes.tools.layout import _place_band

    opts = LayoutOptions()
    g = _empty_graph()

    # Hand-build a tall pre-sized node so we can assert the packer
    # clears its actual height rather than the legacy 160 stride.
    tall_id = str(uuid.uuid4())
    g["nodes"][tall_id] = {
        "title": "Tall",
        "id": tall_id,
        "properties": {},
        "registry": "core/Watch",
        "base_type": "core/Node",
        "x": 0,
        "y": 0,
        "width": 210,
        "height": 200,
    }
    short_id = str(uuid.uuid4())
    g["nodes"][short_id] = {
        "title": "Short",
        "id": short_id,
        "properties": {},
        "registry": "core/Watch",
        "base_type": "core/Node",
        "x": 0,
        "y": 0,
        "width": 210,
        "height": 100,
    }

    # No edges — both nodes land in column 0. Sorted id order
    # determines which goes first.
    placement = _place_band(
        g,
        sorted([tall_id, short_id]),
        origin_x=0,
        origin_y=0,
        options=opts,
    )

    y_tall = g["nodes"][tall_id]["y"]
    y_short = g["nodes"][short_id]["y"]
    assert y_tall != y_short
    top_id, bottom_id = (tall_id, short_id) if y_tall < y_short else (short_id, tall_id)
    top_y = g["nodes"][top_id]["y"]
    top_h = g["nodes"][top_id]["height"]
    bottom_y = g["nodes"][bottom_id]["y"]
    assert bottom_y >= top_y + top_h + opts.min_vertical_gap
    # If the "top" node is the tall one, bottom_y must clear 200 + gap.
    if top_id == tall_id:
        assert bottom_y >= 200 + opts.min_vertical_gap
    # Legacy stride would have put the second node at y=160; make sure
    # we're not doing that anymore.
    assert bottom_y != 160
    # placement.max_y covers both rects.
    assert placement.max_y >= bottom_y + g["nodes"][bottom_id]["height"]


def test_layout_v21_predecessor_aware_row_placement_uncrosses_wires():
    """Crossed Input->SetState edges should be un-crossed by the layout pass.

    Fixture::

        IN a ---> SET local.a
        IN b ---> SET local.b

    With the original IDs chosen so that ``a`` and ``b`` would sort in
    the "wrong" order in column 1 under pure stable-id ordering, we
    should still see ``IN a`` and ``SET local.a`` share a y coordinate
    after layout, and likewise for ``b``.
    """

    g = _empty_graph()
    w = GraphWriter(g)
    # Inputs in column 0
    in_a = w.add_node(
        "core/Input",
        title="IN a",
        properties={"input_type": "any", "input_name": "a"},
    )
    in_b = w.add_node(
        "core/Input",
        title="IN b",
        properties={"input_type": "any", "input_name": "b"},
    )
    # SetStates in column 1 -- connect a -> set_a, b -> set_b, but add
    # them to the graph in reverse-dependency order so that stable-id
    # sort inside the column puts set_b before set_a.
    set_b = w.add_node(
        "state/SetState",
        title="SET local.b",
        properties={"name": "b", "scope": "local"},
    )
    set_a = w.add_node(
        "state/SetState",
        title="SET local.a",
        properties={"name": "a", "scope": "local"},
    )
    w.connect(in_a, "value", set_a, "value")
    w.connect(in_b, "value", set_b, "value")

    layout_graph(g, anchor="full")

    # Each SetState must land at the same y as its source Input.
    assert g["nodes"][in_a]["y"] == g["nodes"][set_a]["y"], (
        "IN a should line up with SET local.a after predecessor-aware placement"
    )
    assert g["nodes"][in_b]["y"] == g["nodes"][set_b]["y"], (
        "IN b should line up with SET local.b after predecessor-aware placement"
    )


def test_layout_v21_col_width_default_is_wider():
    """col_width default bumped from 260 to 360 in v2.1."""

    opts = LayoutOptions()
    assert opts.col_width == 360


# ---------------------------------------------------------------------------
# add_group / GroupColor
# ---------------------------------------------------------------------------


def _empty_graph() -> dict:
    return {
        "title": "Test",
        "id": str(uuid.uuid4()),
        "registry": "test/Test",
        "base_type": "core/Graph",
        "properties": {},
        "x": 0,
        "y": 0,
        "width": 200,
        "height": 100,
        "collapsed": False,
        "inherited": False,
        "nodes": {},
        "edges": {},
        "groups": [],
        "comments": [],
        "extends": None,
        "inputs": [],
        "outputs": [],
        "module_properties": {},
    }


def test_add_group_creates_group_around_nodes():
    """add_group computes bbox from node positions and produces a group dict."""
    g = GraphWriter(_empty_graph())
    a = g.add_node(
        "core/Input", properties={"input_name": "a", "input_type": "any", "num": 0}
    )
    b = g.add_node(
        "core/Output", properties={"output_name": "a", "output_type": "any", "num": 0}
    )
    g.connect(a, "value", b, "value")
    layout_graph(g.graph, anchor="full")

    group = g.add_group("Input", GroupColor.INPUT, [a, b])

    assert group in g.graph["groups"]
    assert group["title"] == "Input"
    assert group["color"] == "#88A"
    assert group["color"] == GroupColor.INPUT
    assert group["font_size"] == 24
    assert group["inherited"] is False

    # Bounding box should contain both placed nodes (with padding).
    a_node = g.graph["nodes"][a]
    b_node = g.graph["nodes"][b]
    assert group["x"] <= min(a_node["x"], b_node["x"])
    assert group["y"] <= min(a_node["y"], b_node["y"])
    assert group["x"] + group["width"] >= max(
        a_node["x"] + a_node["width"], b_node["x"] + b_node["width"]
    )
    assert group["y"] + group["height"] >= max(
        a_node["y"] + a_node["height"], b_node["y"] + b_node["height"]
    )


def test_add_group_resolves_short_prefix_ids():
    """GraphWriter.add_group accepts short-prefix node ids like other methods."""
    g = GraphWriter(_empty_graph())
    a = g.add_node(
        "core/Input", properties={"input_name": "a", "input_type": "any", "num": 0}
    )
    layout_graph(g.graph, anchor="full")
    group = g.add_group("X", GroupColor.SPECIAL, [a[:8]])
    assert group["title"] == "X"


def test_add_group_empty_node_ids_raises():
    g = GraphWriter(_empty_graph())
    with pytest.raises(GroupError):
        g.add_group("Empty", GroupColor.INPUT, [])


def test_add_group_unknown_node_raises():
    g = GraphWriter(_empty_graph())
    with pytest.raises(GroupError):
        add_group(g.graph, "X", GroupColor.INPUT, ["does-not-exist"])


def test_add_group_appends_to_existing_groups():
    """add_group never clobbers user-drawn groups; it appends."""
    g = GraphWriter(_empty_graph())
    g.graph["groups"].append(
        {"title": "User Group", "x": 0, "y": 0, "width": 100, "height": 50}
    )
    a = g.add_node(
        "core/Input", properties={"input_name": "a", "input_type": "any", "num": 0}
    )
    layout_graph(g.graph, anchor="full")
    g.add_group("Auto", GroupColor.OUTPUT, [a])
    titles = [grp["title"] for grp in g.graph["groups"]]
    assert "User Group" in titles
    assert "Auto" in titles
    assert len(g.graph["groups"]) == 2


def test_group_color_palette_matches_litegraph_presets():
    """Sanity check the constants. These are the LiteGraph groupcolor hex
    values; if any drift, the visual match with the frontend breaks."""
    assert GroupColor.INPUT == "#88A"
    assert GroupColor.OUTPUT == "#8A8"
    assert GroupColor.PROCESS == "#3f789e"
    assert GroupColor.PREPARE == "#8AA"
    assert GroupColor.VALIDATION == "#b58b2a"
    assert GroupColor.FUNCTION == "#b06634"
    assert GroupColor.SPECIAL == "#a1309b"
    assert GroupColor.ERROR_HANDLING == "#A88"
    assert GroupColor.UX == "#207e7e"


# ---------------------------------------------------------------------------
# add_node property validation (UnknownPropertyError)
# ---------------------------------------------------------------------------


def test_add_node_accepts_known_property():
    """Sanity check: a property that exists on the class is accepted.

    ``state/SetState`` declares ``name`` and ``scope`` as Fields — passing
    either should work without raising.
    """
    g = GraphWriter(_empty_graph())
    nid = g.add_node(
        "state/SetState",
        properties={"name": "foo", "scope": "local"},
    )
    assert g.graph["nodes"][nid]["properties"] == {"name": "foo", "scope": "local"}


def test_add_node_unknown_property_raises():
    """A property key not declared on the class raises UnknownPropertyError.

    This is the exact failure mode the agent hit when it set
    ``properties={"agent": "summarizer"}`` on ``agents/GetAgent`` instead
    of the real property name ``agent_name``.
    """
    g = GraphWriter(_empty_graph())
    with pytest.raises(UnknownPropertyError) as excinfo:
        g.add_node(
            "agents/GetAgent",
            properties={"agent": "summarizer"},  # bogus key
        )
    msg = str(excinfo.value)
    assert "'agent'" in msg
    assert "agent_name" in msg  # the real property name should appear in the hint


def test_add_node_unknown_property_lists_valid_options_in_message():
    """The error message must list the valid property names so the caller
    can fix the typo without further lookup."""
    g = GraphWriter(_empty_graph())
    with pytest.raises(UnknownPropertyError) as excinfo:
        g.add_node(
            "state/SetState",
            properties={"naem": "foo"},  # typo for "name"
        )
    msg = str(excinfo.value)
    assert "'naem'" in msg
    assert "'name'" in msg
    assert "'scope'" in msg


def test_add_node_no_properties_still_works():
    """Calling add_node without a properties arg should NOT trigger
    validation (there's nothing to validate)."""
    g = GraphWriter(_empty_graph())
    nid = g.add_node("core/Stage")
    assert g.graph["nodes"][nid]["properties"] == {}


def test_add_node_empty_properties_dict_still_works():
    """Same as above but with an explicit empty dict."""
    g = GraphWriter(_empty_graph())
    nid = g.add_node("core/Stage", properties={})
    assert g.graph["nodes"][nid]["properties"] == {}


def test_add_node_validation_runs_before_mutation():
    """When add_node raises UnknownPropertyError, no node should have
    been added to the graph (no partial-mutation footprint)."""
    g = GraphWriter(_empty_graph())
    before_count = len(g.graph["nodes"])
    with pytest.raises(UnknownPropertyError):
        g.add_node("state/SetState", properties={"bogus_key": "value"})
    after_count = len(g.graph["nodes"])
    assert after_count == before_count
