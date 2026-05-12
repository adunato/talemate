"""
Tests for ``talemate.game.engine.nodes.tools`` static analysis CLI/library.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from talemate.game.engine.nodes.tools import analysis, cli
from talemate.game.engine.nodes.tools.loader import GraphLoadError, load_graph

REPO_ROOT = Path(__file__).resolve().parent.parent
DIRECTOR_FIXTURE = (
    REPO_ROOT
    / "src/talemate/agents/director/modules/director-action-direct-story-arc.json"
)


# ---------------------------------------------------------------------------
# Inline fixture graph
# ---------------------------------------------------------------------------


def _make_inline_graph() -> dict:
    """Hand-built tiny graph used for deterministic unit tests.

    Layout:

        a (core/MakeBool) ---value---> b (core/Stage stage=2)
                                       |
                                       state---> c (core/Watch)
        d (mystery/Unknown)            (no edges)
    """

    return {
        "title": "Inline Test Graph",
        "id": "00000000-0000-0000-0000-000000000000",
        "registry": "test/InlineGraph",
        "base_type": "core/Graph",
        "extends": None,
        "properties": {},
        "nodes": {
            "aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {
                "title": "true",
                "id": "aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "properties": {"value": True},
                "registry": "core/MakeBool",
                "base_type": "core/Node",
            },
            "bbbb2222-bbbb-bbbb-bbbb-bbbbbbbbbbbb": {
                "title": "Stage 2",
                "id": "bbbb2222-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "properties": {"stage": 2},
                "registry": "core/Stage",
                "base_type": "core/Node",
            },
            "cccc3333-cccc-cccc-cccc-cccccccccccc": {
                "title": "Watcher",
                "id": "cccc3333-cccc-cccc-cccc-cccccccccccc",
                "properties": {},
                "registry": "core/Watch",
                "base_type": "core/Node",
            },
            "dddd4444-dddd-dddd-dddd-dddddddddddd": {
                "title": "Mystery Node",
                "id": "dddd4444-dddd-dddd-dddd-dddddddddddd",
                "properties": {},
                "registry": "mystery/Unknown",
                "base_type": "core/Node",
            },
        },
        "edges": {
            "aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa.value": [
                "bbbb2222-bbbb-bbbb-bbbb-bbbbbbbbbbbb.state"
            ],
            "bbbb2222-bbbb-bbbb-bbbb-bbbbbbbbbbbb.state": [
                "cccc3333-cccc-cccc-cccc-cccccccccccc.value"
            ],
        },
        "groups": [{"title": "Inline Group"}],
        "comments": [],
        "inputs": [],
        "outputs": [],
        "module_properties": {},
    }


def _make_cycle_graph() -> dict:
    """Two-node cycle: A.out -> B.in, B.out -> A.in."""

    return {
        "title": "Cycle",
        "id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "registry": "test/Cycle",
        "base_type": "core/Graph",
        "nodes": {
            "11111111-1111-1111-1111-111111111111": {
                "title": "A",
                "id": "11111111-1111-1111-1111-111111111111",
                "properties": {},
                "registry": "core/MakeBool",
                "base_type": "core/Node",
            },
            "22222222-2222-2222-2222-222222222222": {
                "title": "B",
                "id": "22222222-2222-2222-2222-222222222222",
                "properties": {},
                "registry": "core/MakeBool",
                "base_type": "core/Node",
            },
        },
        "edges": {
            "11111111-1111-1111-1111-111111111111.out": [
                "22222222-2222-2222-2222-222222222222.in"
            ],
            "22222222-2222-2222-2222-222222222222.out": [
                "11111111-1111-1111-1111-111111111111.in"
            ],
        },
        "groups": [],
        "comments": [],
        "inputs": [],
        "outputs": [],
        "module_properties": {},
    }


# ---------------------------------------------------------------------------
# Inline graph: per-function tests
# ---------------------------------------------------------------------------


def test_summarize_inline():
    g = _make_inline_graph()
    s = analysis.summarize(g)

    assert s.title == "Inline Test Graph"
    assert s.registry == "test/InlineGraph"
    assert s.node_count == 4
    assert s.stage_node_count == 1
    assert s.nodes_by_category.get("core") == 3
    assert s.nodes_by_category.get("mystery") == 1
    assert s.group_titles == ["Inline Group"]


def test_list_nodes_filter_inline():
    g = _make_inline_graph()
    all_nodes = analysis.list_nodes(g)
    assert len(all_nodes) == 4

    only_core = analysis.list_nodes(g, registry_pattern="core/")
    assert len(only_core) == 3
    assert all(e.registry and e.registry.startswith("core/") for e in only_core)

    by_title = analysis.list_nodes(g, title_pattern="watch")
    assert len(by_title) == 1
    assert by_title[0].title == "Watcher"


def test_get_node_inline_with_short_prefix():
    g = _make_inline_graph()
    d = analysis.get_node(g, "bbbb2222")
    assert d.title == "Stage 2"
    assert d.short_id == "bbbb2222"
    assert d.registered is True

    # state input is connected
    state_in = next(i for i in d.inputs if i.name == "state")
    assert state_in.connected
    assert state_in.source is not None
    assert state_in.source.short_id == "aaaa1111"

    # state output is connected
    state_out = next(o for o in d.outputs if o.name == "state")
    assert any(c.short_id == "cccc3333" for c in state_out.consumers)

    # mystery node has no static class but should still resolve via edges
    mystery = analysis.get_node(g, "dddd4444")
    assert mystery.registered is False


def test_resolve_short_prefix_unique_ambiguous_missing():
    g = _make_inline_graph()
    # add another aaaa-prefixed node to make a collision
    g["nodes"]["aaaa9999-zzzz-zzzz-zzzz-zzzzzzzzzzzz"] = {
        "title": "Other",
        "id": "aaaa9999-zzzz-zzzz-zzzz-zzzzzzzzzzzz",
        "properties": {},
        "registry": "core/MakeBool",
        "base_type": "core/Node",
    }

    # unique-enough prefix still works
    assert (
        analysis.resolve_node_id(g, "aaaa1111")
        == "aaaa1111-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    # ambiguous
    with pytest.raises(ValueError, match="Ambiguous"):
        analysis.resolve_node_id(g, "aaaa")

    # missing
    with pytest.raises(ValueError, match="No node"):
        analysis.resolve_node_id(g, "ffff0000")


def test_list_edges_filter():
    g = _make_inline_graph()
    all_edges = analysis.list_edges(g)
    assert len(all_edges) == 2

    only_from_a = analysis.list_edges(g, from_node="aaaa1111")
    assert len(only_from_a) == 1
    assert only_from_a[0].target_short == "bbbb2222"

    only_to_c = analysis.list_edges(g, to_node="cccc3333")
    assert len(only_to_c) == 1
    assert only_to_c[0].source_short == "bbbb2222"


def test_feeds_and_consumers():
    g = _make_inline_graph()

    f = analysis.feeds(g, "bbbb2222.state")
    assert f.source is not None
    assert f.source.short_id == "aaaa1111"
    assert f.source_node_registry == "core/MakeBool"

    f_unconnected = analysis.feeds(g, "aaaa1111.in")
    assert f_unconnected.source is None

    c = analysis.consumers(g, "bbbb2222.state")
    assert len(c.consumers) == 1
    assert c.consumers[0].target_short == "cccc3333"


def test_trace_forward_inline():
    g = _make_inline_graph()
    t = analysis.trace_forward(g, "aaaa1111", depth=3)
    assert t.short_id == "aaaa1111"
    assert len(t.children) == 1
    assert t.children[0].short_id == "bbbb2222"
    assert t.children[0].children[0].short_id == "cccc3333"


def test_trace_backward_inline():
    g = _make_inline_graph()
    t = analysis.trace_backward(g, "cccc3333", depth=3)
    assert t.short_id == "cccc3333"
    assert t.children[0].short_id == "bbbb2222"
    assert t.children[0].children[0].short_id == "aaaa1111"


def test_trace_cycle_safety():
    g = _make_cycle_graph()
    t = analysis.trace_forward(g, "11111111", depth=10)
    # walk: A -> B -> A(cycle marker)
    assert t.short_id == "11111111"
    assert len(t.children) == 1
    b = t.children[0]
    assert b.short_id == "22222222"
    assert len(b.children) == 1
    cycled = b.children[0]
    assert cycled.cycle is True
    assert cycled.short_id == "11111111"
    # no infinite recursion below the cycle marker
    assert cycled.children == []


def test_stage_map_inline():
    g = _make_inline_graph()
    sm = analysis.stage_map(g)
    # one chain has the stage node, the other (mystery) is unstaged
    assert len(sm.stage_nodes) == 1
    assert sm.stage_nodes[0].stage == 2
    assert sm.stage_nodes[0].short_id == "bbbb2222"
    assert "dddd4444-dddd-dddd-dddd-dddddddddddd" in sm.unstaged_node_ids


def test_check_registries_inline_finds_unknown():
    g = _make_inline_graph()
    r = analysis.check_registries(g)
    unknowns = {p.registry for p in r.unknown_registries}
    # mystery/Unknown is fake, plus the top-level test/InlineGraph
    assert "mystery/Unknown" in unknowns
    assert "test/InlineGraph" in unknowns
    # core/Stage and core/MakeBool are real and should NOT appear
    assert "core/Stage" not in unknowns
    assert "core/MakeBool" not in unknowns


# ---------------------------------------------------------------------------
# Real-graph smoke tests
# ---------------------------------------------------------------------------


def test_real_graph_summarize_and_lists():
    assert DIRECTOR_FIXTURE.exists(), DIRECTOR_FIXTURE
    g = load_graph(DIRECTOR_FIXTURE)

    s = analysis.summarize(g)
    assert s.node_count > 0
    assert s.stage_node_count >= 1

    nodes = analysis.list_nodes(g)
    assert nodes  # non-empty

    sm = analysis.stage_map(g)
    assert sm.stage_nodes  # at least one Stage node

    chk = analysis.check_registries(g)
    # Shipped graph should not contain unknown registries.
    assert chk.unknown_registries == [], [
        p.model_dump() for p in chk.unknown_registries
    ]
    assert chk.unknown_base_types == [], [
        p.model_dump() for p in chk.unknown_base_types
    ]


def test_loader_missing_file(tmp_path):
    with pytest.raises(GraphLoadError):
        load_graph(tmp_path / "nope.json")


def test_loader_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(GraphLoadError):
        load_graph(p)


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    out = io.StringIO()
    err = io.StringIO()
    code = cli._run(args, out, err)
    return code, out.getvalue(), err.getvalue()


def test_cli_summary_text():
    code, out, err = _run_cli(["summary", str(DIRECTOR_FIXTURE)])
    assert code == 0, err
    assert "title:" in out
    assert "nodes:" in out
    assert "stage nodes:" in out


def test_cli_summary_json_is_valid():
    code, out, err = _run_cli(["summary", str(DIRECTOR_FIXTURE), "--json"])
    assert code == 0, err
    parsed = json.loads(out)
    assert parsed["registry"] == "agents/director/chat/directorActionDirectStoryArc"
    assert parsed["node_count"] > 0


def test_cli_check_registries_real_graph_returns_zero():
    code, out, err = _run_cli(["check-registries", str(DIRECTOR_FIXTURE)])
    assert code == 0, err + out
    assert "ok:" in out


def test_cli_check_registries_unknown_exits_2(tmp_path):
    g = _make_inline_graph()
    p = tmp_path / "g.json"
    p.write_text(json.dumps(g), encoding="utf-8")
    code, out, err = _run_cli(["check-registries", str(p)])
    assert code == cli.EXIT_REGISTRY_PROBLEMS
    assert "mystery/Unknown" in out


def test_cli_node_with_short_prefix():
    code, out, err = _run_cli(["node", str(DIRECTOR_FIXTURE), "ccb39d43"])
    # ccb39d43 is the top-level graph id, not a node id - expect error
    assert code == cli.EXIT_STRUCT_ERROR
    assert "No node" in err or "matches" in err
