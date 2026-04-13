"""
Read-only static analysis of Talemate node graph JSON dicts.

Every public function takes an already-loaded ``graph: dict`` (the parsed
contents of a graph .json file) and returns a pydantic model. There is no
file I/O, no formatting, and no runtime instantiation of node classes from
the graph itself - we only consult the live ``NODES`` registry to look up
static socket layouts where available.

This module is intended as the analysis layer for both a developer CLI
(``tools/cli.py``) and, eventually, a UI surface. Keep it pure.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import pydantic
import structlog

log = structlog.get_logger("talemate.game.engine.nodes.tools.analysis")

__all__ = [
    # registry loader
    "ensure_registry_loaded",
    # models
    "GraphSummary",
    "NodeListEntry",
    "SocketRef",
    "NodeInputInfo",
    "NodeOutputInfo",
    "NodeDetails",
    "EdgeInfo",
    "FeedsResult",
    "ConsumersResult",
    "TraceNode",
    "StageInfo",
    "StageChain",
    "StageMapResult",
    "RegistryProblem",
    "RegistryCheckResult",
    # functions
    "summarize",
    "list_nodes",
    "get_node",
    "list_edges",
    "feeds",
    "consumers",
    "trace_forward",
    "trace_backward",
    "stage_map",
    "check_registries",
    # helpers
    "resolve_node_id",
    "split_socket_path",
]


# ---------------------------------------------------------------------------
# Registry loader (lazy, side-effecty - only run when actually needed)
# ---------------------------------------------------------------------------

_REGISTRY_LOADED = False


def ensure_registry_loaded() -> None:
    """Import all shipped Talemate node definitions exactly once.

    This is lazy because importing the registry has filesystem side effects
    (walks ``SEARCH_PATHS``) and we want analysis functions that don't need
    the registry to remain cheap.
    """

    global _REGISTRY_LOADED
    if _REGISTRY_LOADED:
        return
    # First import the Python modules that register native node classes
    # via @register decorators (core/MakeBool, validation/*, etc).
    import talemate.game.engine.nodes.load_definitions  # noqa: F401
    from talemate.game.engine.nodes.registry import import_initial_node_definitions

    # Then walk SEARCH_PATHS to register JSON-defined module nodes.
    import_initial_node_definitions()
    _REGISTRY_LOADED = True


def _registry_dict() -> dict[str, Any]:
    ensure_registry_loaded()
    from talemate.game.engine.nodes.registry import NODES

    return NODES


def _base_types_dict() -> dict[str, Any]:
    ensure_registry_loaded()
    from talemate.game.engine.nodes.base_types import BASE_TYPES

    return BASE_TYPES


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GraphSummary(pydantic.BaseModel):
    title: str | None
    id: str | None
    registry: str | None
    base_type: str | None
    extends: str | None
    node_count: int
    nodes_by_category: dict[str, int]
    stage_node_count: int
    input_count: int
    output_count: int
    module_property_count: int
    group_titles: list[str]


class NodeListEntry(pydantic.BaseModel):
    short_id: str
    id: str
    registry: str | None
    title: str


class SocketRef(pydantic.BaseModel):
    node_id: str
    short_id: str
    socket: str

    @property
    def full(self) -> str:
        return f"{self.node_id}.{self.socket}"


class NodeInputInfo(pydantic.BaseModel):
    name: str
    connected: bool
    source: SocketRef | None = None


class NodeOutputInfo(pydantic.BaseModel):
    name: str
    consumers: list[SocketRef] = pydantic.Field(default_factory=list)


class NodeDetails(pydantic.BaseModel):
    id: str
    short_id: str
    title: str
    registry: str | None
    base_type: str | None
    properties: dict[str, Any]
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    inputs: list[NodeInputInfo]
    outputs: list[NodeOutputInfo]
    registered: bool


class EdgeInfo(pydantic.BaseModel):
    source_node: str
    source_short: str
    source_socket: str
    target_node: str
    target_short: str
    target_socket: str


class FeedsResult(pydantic.BaseModel):
    target_node: str
    target_short: str
    target_socket: str
    source: SocketRef | None = None
    source_node_title: str | None = None
    source_node_registry: str | None = None


class ConsumersResult(pydantic.BaseModel):
    source_node: str
    source_short: str
    source_socket: str
    consumers: list[EdgeInfo] = pydantic.Field(default_factory=list)


class TraceNode(pydantic.BaseModel):
    node_id: str
    short_id: str
    title: str
    registry: str | None
    via_socket: str | None = None  # the socket on *this* node we walked through
    children: list["TraceNode"] = pydantic.Field(default_factory=list)
    cycle: bool = False
    truncated: bool = False  # depth limit hit


class StageInfo(pydantic.BaseModel):
    node_id: str
    short_id: str
    title: str
    stage: int
    chain_index: int


class StageChain(pydantic.BaseModel):
    index: int
    node_ids: list[str]
    stage_node_ids: list[str]
    stages: list[int]


class StageMapResult(pydantic.BaseModel):
    stage_nodes: list[StageInfo]
    chains: list[StageChain]
    unstaged_node_ids: list[str]


class RegistryProblem(pydantic.BaseModel):
    node_id: str
    short_id: str
    title: str
    registry: str | None
    base_type: str | None
    kind: str  # "registry" | "base_type"


class RegistryCheckResult(pydantic.BaseModel):
    unknown_registries: list[RegistryProblem]
    unknown_base_types: list[RegistryProblem]
    checked_registry_count: int
    checked_base_type_count: int

    @property
    def has_problems(self) -> bool:
        return bool(self.unknown_registries or self.unknown_base_types)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short(node_id: str) -> str:
    return node_id[:8]


def _nodes(graph: dict) -> dict[str, dict]:
    return graph.get("nodes") or {}


def _edges(graph: dict) -> dict[str, list[str]]:
    return graph.get("edges") or {}


def split_socket_path(path: str) -> tuple[str, str]:
    """Split a ``<node_id>.<socket>`` path on the FIRST dot.

    Node ids (full UUIDs or short prefixes) and socket names in this
    codebase never contain dots, so the first dot cleanly separates
    the node-id half from the socket-name half.
    """

    if "." not in path:
        raise ValueError(
            f"Socket path '{path}' must be of the form '<node_id>.<socket>'"
        )
    node_part, socket = path.split(".", 1)
    return node_part, socket


def resolve_node_id(graph: dict, prefix: str) -> str:
    """Resolve a possibly-truncated node id to a full node id.

    Accepts a full UUID or any unique prefix. Raises ``ValueError`` on
    miss or ambiguity.
    """

    nodes = _nodes(graph)
    if not prefix:
        raise ValueError("Empty node id prefix")

    if prefix in nodes:
        return prefix

    matches = [nid for nid in nodes if nid.startswith(prefix)]
    if not matches:
        raise ValueError(
            f"No node matches prefix '{prefix}'. "
            f"(Graph contains {len(nodes)} nodes; try `list-nodes` to find one.)"
        )
    if len(matches) > 1:
        joined = ", ".join(_short(m) for m in matches[:6])
        more = "" if len(matches) <= 6 else f" (+{len(matches) - 6} more)"
        raise ValueError(
            f"Ambiguous node prefix '{prefix}' matches {len(matches)} nodes: {joined}{more}"
        )
    return matches[0]


def _make_socket_ref(node_id: str, socket: str) -> SocketRef:
    return SocketRef(node_id=node_id, short_id=_short(node_id), socket=socket)


def _registered_node_class(registry: str | None) -> Any | None:
    """Return the live class registered under ``registry`` or ``None``."""

    if not registry:
        return None
    return _registry_dict().get(registry)


def _instance_for_class(node_cls: Any) -> Any | None:
    """Try to instantiate a node class to read its socket layout.

    This is a best-effort, side-effect-free probe: registered classes set
    up their sockets in ``setup()``, and that ``setup`` runs in
    ``__init__``. If anything goes wrong (constructor expects positional
    args, etc.), return ``None`` and let the caller fall back.
    """

    if node_cls is None:
        return None
    try:
        return node_cls()
    except Exception:
        return None


def _static_socket_names(registry: str | None) -> tuple[list[str] | None, list[str] | None]:
    """Return ``(input_names, output_names)`` from the registered class.

    Each element is ``None`` when we couldn't determine it.
    """

    cls = _registered_node_class(registry)
    instance = _instance_for_class(cls)
    if instance is None:
        return (None, None)

    input_names: list[str] | None
    output_names: list[str] | None
    try:
        input_names = [s.name for s in instance.inputs]
    except Exception:
        input_names = None
    try:
        output_names = [s.name for s in instance.outputs]
    except Exception:
        output_names = None
    return input_names, output_names


def _node_input_names(graph: dict, node_id: str, node: dict) -> list[str]:
    """Best-effort list of input socket names for a node.

    Order of preference:
        1. registered class static inputs (+ dynamic_inputs from JSON)
        2. dynamic_inputs from JSON alone
        3. inferred from edges where this node appears as a target
    """

    static_inputs, _ = _static_socket_names(node.get("registry"))
    dyn = [d.get("name") for d in (node.get("dynamic_inputs") or []) if d.get("name")]
    inferred = sorted(_inferred_input_names_from_edges(graph, node_id))

    if static_inputs is not None:
        # Combine static + dynamic, then drop duplicates while preserving order.
        seen: set[str] = set()
        combined: list[str] = []
        for name in [*static_inputs, *dyn, *inferred]:
            if name not in seen:
                seen.add(name)
                combined.append(name)
        return combined

    if dyn or inferred:
        seen = set()
        combined = []
        for name in [*dyn, *inferred]:
            if name not in seen:
                seen.add(name)
                combined.append(name)
        return combined

    return []


def _node_output_names(graph: dict, node_id: str, node: dict) -> list[str]:
    """Best-effort list of output socket names for a node."""

    _, static_outputs = _static_socket_names(node.get("registry"))
    inferred = sorted(_inferred_output_names_from_edges(graph, node_id))

    if static_outputs is not None:
        seen: set[str] = set()
        combined: list[str] = []
        for name in [*static_outputs, *inferred]:
            if name not in seen:
                seen.add(name)
                combined.append(name)
        return combined

    return inferred


def _inferred_input_names_from_edges(graph: dict, node_id: str) -> set[str]:
    names: set[str] = set()
    for _src, targets in _edges(graph).items():
        for tgt in targets:
            try:
                tgt_node, tgt_socket = split_socket_path(tgt)
            except ValueError:
                continue
            if tgt_node == node_id:
                names.add(tgt_socket)
    return names


def _inferred_output_names_from_edges(graph: dict, node_id: str) -> set[str]:
    names: set[str] = set()
    for src, _targets in _edges(graph).items():
        try:
            src_node, src_socket = split_socket_path(src)
        except ValueError:
            continue
        if src_node == node_id:
            names.add(src_socket)
    return names


def _input_source_map(graph: dict) -> dict[tuple[str, str], tuple[str, str]]:
    """Map ``(target_node, target_socket) -> (source_node, source_socket)``.

    The graph format treats edges as ``source -> [targets]``, so this
    inverts the relation. If a target socket somehow appears with multiple
    sources we keep the last one we see (the runtime treats inputs as
    single-source).
    """

    out: dict[tuple[str, str], tuple[str, str]] = {}
    for src, targets in _edges(graph).items():
        try:
            src_node, src_socket = split_socket_path(src)
        except ValueError:
            continue
        for tgt in targets:
            try:
                tgt_node, tgt_socket = split_socket_path(tgt)
            except ValueError:
                continue
            out[(tgt_node, tgt_socket)] = (src_node, src_socket)
    return out


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


def summarize(graph: dict) -> GraphSummary:
    nodes = _nodes(graph)

    categories: Counter[str] = Counter()
    stage_count = 0
    for node in nodes.values():
        registry = node.get("registry") or ""
        if "/" in registry:
            category = registry.split("/", 1)[0]
        else:
            category = registry or "<none>"
        categories[category] += 1
        if registry == "core/Stage":
            stage_count += 1

    groups = graph.get("groups") or []
    group_titles = [g.get("title", "") for g in groups]

    return GraphSummary(
        title=graph.get("title"),
        id=graph.get("id"),
        registry=graph.get("registry"),
        base_type=graph.get("base_type"),
        extends=graph.get("extends"),
        node_count=len(nodes),
        nodes_by_category=dict(sorted(categories.items())),
        stage_node_count=stage_count,
        input_count=len(graph.get("inputs") or []),
        output_count=len(graph.get("outputs") or []),
        module_property_count=len(graph.get("module_properties") or {}),
        group_titles=group_titles,
    )


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------


def list_nodes(
    graph: dict,
    *,
    registry_pattern: str | None = None,
    title_pattern: str | None = None,
) -> list[NodeListEntry]:
    nodes = _nodes(graph)
    rp = registry_pattern.lower() if registry_pattern else None
    tp = title_pattern.lower() if title_pattern else None

    out: list[NodeListEntry] = []
    for node_id, node in nodes.items():
        registry = node.get("registry") or ""
        title = node.get("title") or ""
        if rp and rp not in registry.lower():
            continue
        if tp and tp not in title.lower():
            continue
        out.append(
            NodeListEntry(
                short_id=_short(node_id),
                id=node_id,
                registry=registry or None,
                title=title,
            )
        )
    out.sort(key=lambda e: (e.registry or "", e.title))
    return out


# ---------------------------------------------------------------------------
# get_node
# ---------------------------------------------------------------------------


def get_node(graph: dict, node_id: str) -> NodeDetails:
    full_id = resolve_node_id(graph, node_id)
    node = _nodes(graph)[full_id]

    input_source_map = _input_source_map(graph)
    input_names = _node_input_names(graph, full_id, node)
    output_names = _node_output_names(graph, full_id, node)

    inputs: list[NodeInputInfo] = []
    for name in input_names:
        src = input_source_map.get((full_id, name))
        if src is not None:
            src_node, src_socket = src
            inputs.append(
                NodeInputInfo(
                    name=name,
                    connected=True,
                    source=_make_socket_ref(src_node, src_socket),
                )
            )
        else:
            inputs.append(NodeInputInfo(name=name, connected=False))

    outputs: list[NodeOutputInfo] = []
    edges = _edges(graph)
    for name in output_names:
        full_socket = f"{full_id}.{name}"
        targets = edges.get(full_socket, [])
        consumer_refs: list[SocketRef] = []
        for tgt in targets:
            try:
                tgt_node, tgt_socket = split_socket_path(tgt)
            except ValueError:
                continue
            consumer_refs.append(_make_socket_ref(tgt_node, tgt_socket))
        outputs.append(NodeOutputInfo(name=name, consumers=consumer_refs))

    registered = _registered_node_class(node.get("registry")) is not None

    return NodeDetails(
        id=full_id,
        short_id=_short(full_id),
        title=node.get("title", ""),
        registry=node.get("registry"),
        base_type=node.get("base_type"),
        properties=dict(node.get("properties") or {}),
        x=int(node.get("x", 0) or 0),
        y=int(node.get("y", 0) or 0),
        width=int(node.get("width", 0) or 0),
        height=int(node.get("height", 0) or 0),
        inputs=inputs,
        outputs=outputs,
        registered=registered,
    )


# ---------------------------------------------------------------------------
# list_edges
# ---------------------------------------------------------------------------


def list_edges(
    graph: dict,
    *,
    from_node: str | None = None,
    to_node: str | None = None,
) -> list[EdgeInfo]:
    from_full = resolve_node_id(graph, from_node) if from_node else None
    to_full = resolve_node_id(graph, to_node) if to_node else None

    out: list[EdgeInfo] = []
    for src, targets in _edges(graph).items():
        try:
            src_node, src_socket = split_socket_path(src)
        except ValueError:
            continue
        if from_full and src_node != from_full:
            continue
        for tgt in targets:
            try:
                tgt_node, tgt_socket = split_socket_path(tgt)
            except ValueError:
                continue
            if to_full and tgt_node != to_full:
                continue
            out.append(
                EdgeInfo(
                    source_node=src_node,
                    source_short=_short(src_node),
                    source_socket=src_socket,
                    target_node=tgt_node,
                    target_short=_short(tgt_node),
                    target_socket=tgt_socket,
                )
            )
    out.sort(key=lambda e: (e.source_short, e.source_socket, e.target_short, e.target_socket))
    return out


# ---------------------------------------------------------------------------
# feeds / consumers
# ---------------------------------------------------------------------------


def feeds(graph: dict, target: str) -> FeedsResult:
    """Return the source feeding ``target`` (a ``<node_or_prefix>.<socket>``).

    For an input socket: returns the upstream output that connects to it.
    """

    node_part, socket = split_socket_path(target)
    full_id = resolve_node_id(graph, node_part)
    src = _input_source_map(graph).get((full_id, socket))

    source_ref: SocketRef | None = None
    src_title: str | None = None
    src_registry: str | None = None
    if src is not None:
        src_node_id, src_socket = src
        source_ref = _make_socket_ref(src_node_id, src_socket)
        src_node = _nodes(graph).get(src_node_id, {})
        src_title = src_node.get("title")
        src_registry = src_node.get("registry")

    return FeedsResult(
        target_node=full_id,
        target_short=_short(full_id),
        target_socket=socket,
        source=source_ref,
        source_node_title=src_title,
        source_node_registry=src_registry,
    )


def consumers(graph: dict, source: str) -> ConsumersResult:
    """Return all targets reading ``source`` (a ``<node_or_prefix>.<socket>``)."""

    node_part, socket = split_socket_path(source)
    full_id = resolve_node_id(graph, node_part)
    full_key = f"{full_id}.{socket}"
    edges = _edges(graph).get(full_key, [])

    consumer_edges: list[EdgeInfo] = []
    for tgt in edges:
        try:
            tgt_node, tgt_socket = split_socket_path(tgt)
        except ValueError:
            continue
        consumer_edges.append(
            EdgeInfo(
                source_node=full_id,
                source_short=_short(full_id),
                source_socket=socket,
                target_node=tgt_node,
                target_short=_short(tgt_node),
                target_socket=tgt_socket,
            )
        )
    return ConsumersResult(
        source_node=full_id,
        source_short=_short(full_id),
        source_socket=socket,
        consumers=consumer_edges,
    )


# ---------------------------------------------------------------------------
# trace_forward / trace_backward
# ---------------------------------------------------------------------------


def _make_trace_leaf(graph: dict, node_id: str, via: str | None, *, cycle: bool, truncated: bool) -> TraceNode:
    node = _nodes(graph).get(node_id, {})
    return TraceNode(
        node_id=node_id,
        short_id=_short(node_id),
        title=node.get("title", ""),
        registry=node.get("registry"),
        via_socket=via,
        cycle=cycle,
        truncated=truncated,
    )


def _trace(
    graph: dict,
    start_id: str,
    *,
    depth: int,
    forward: bool,
    visited: set[str] | None = None,
    via: str | None = None,
) -> TraceNode:
    if visited is None:
        visited = set()
    node = _nodes(graph).get(start_id, {})
    if start_id in visited:
        return _make_trace_leaf(graph, start_id, via, cycle=True, truncated=False)

    current = TraceNode(
        node_id=start_id,
        short_id=_short(start_id),
        title=node.get("title", ""),
        registry=node.get("registry"),
        via_socket=via,
    )

    if depth <= 0:
        current.truncated = True
        return current

    visited = visited | {start_id}
    edges = _edges(graph)

    if forward:
        # walk every edge whose source is this node
        for src_path, targets in edges.items():
            try:
                src_node, src_socket = split_socket_path(src_path)
            except ValueError:
                continue
            if src_node != start_id:
                continue
            for tgt in targets:
                try:
                    tgt_node, tgt_socket = split_socket_path(tgt)
                except ValueError:
                    continue
                child = _trace(
                    graph,
                    tgt_node,
                    depth=depth - 1,
                    forward=True,
                    visited=visited,
                    via=tgt_socket,
                )
                current.children.append(child)
    else:
        # walk every edge whose target is this node
        for src_path, targets in edges.items():
            try:
                src_node, src_socket = split_socket_path(src_path)
            except ValueError:
                continue
            for tgt in targets:
                try:
                    tgt_node, tgt_socket = split_socket_path(tgt)
                except ValueError:
                    continue
                if tgt_node != start_id:
                    continue
                child = _trace(
                    graph,
                    src_node,
                    depth=depth - 1,
                    forward=False,
                    visited=visited,
                    via=src_socket,
                )
                current.children.append(child)

    return current


def trace_forward(graph: dict, start: str, *, depth: int = 3) -> TraceNode:
    full_id = resolve_node_id(graph, start)
    return _trace(graph, full_id, depth=depth, forward=True)


def trace_backward(graph: dict, end: str, *, depth: int = 3) -> TraceNode:
    full_id = resolve_node_id(graph, end)
    return _trace(graph, full_id, depth=depth, forward=False)


# ---------------------------------------------------------------------------
# stage_map
# ---------------------------------------------------------------------------


def _weakly_connected_components(graph: dict) -> list[set[str]]:
    """Compute weakly-connected components over the node-level edge graph."""

    nodes = _nodes(graph)
    parent: dict[str, str] = {nid: nid for nid in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for src, targets in _edges(graph).items():
        try:
            src_node, _ = split_socket_path(src)
        except ValueError:
            continue
        if src_node not in parent:
            continue
        for tgt in targets:
            try:
                tgt_node, _ = split_socket_path(tgt)
            except ValueError:
                continue
            if tgt_node not in parent:
                continue
            union(src_node, tgt_node)

    components: dict[str, set[str]] = defaultdict(set)
    for nid in nodes:
        components[find(nid)].add(nid)
    return list(components.values())


def stage_map(graph: dict) -> StageMapResult:
    nodes = _nodes(graph)
    components = _weakly_connected_components(graph)

    # index components for stable ordering: smallest stage first, then size
    def comp_sort_key(comp: set[str]) -> tuple[int, int]:
        stage_vals = [
            int((nodes[nid].get("properties") or {}).get("stage", 0))
            for nid in comp
            if nodes[nid].get("registry") == "core/Stage"
        ]
        min_stage = min(stage_vals) if stage_vals else 10**9
        return (min_stage, -len(comp))

    components.sort(key=comp_sort_key)

    stage_nodes: list[StageInfo] = []
    chains: list[StageChain] = []
    unstaged: list[str] = []

    for idx, comp in enumerate(components):
        comp_stage_ids = [
            nid for nid in comp if nodes[nid].get("registry") == "core/Stage"
        ]
        if not comp_stage_ids:
            unstaged.extend(sorted(comp))
            continue

        comp_stages: list[int] = []
        for sid in comp_stage_ids:
            stage_val = int((nodes[sid].get("properties") or {}).get("stage", 0))
            comp_stages.append(stage_val)
            stage_nodes.append(
                StageInfo(
                    node_id=sid,
                    short_id=_short(sid),
                    title=nodes[sid].get("title", ""),
                    stage=stage_val,
                    chain_index=idx,
                )
            )
        chains.append(
            StageChain(
                index=idx,
                node_ids=sorted(comp),
                stage_node_ids=sorted(comp_stage_ids),
                stages=sorted(comp_stages),
            )
        )

    stage_nodes.sort(key=lambda s: (s.chain_index, s.stage, s.short_id))
    return StageMapResult(
        stage_nodes=stage_nodes,
        chains=chains,
        unstaged_node_ids=sorted(unstaged),
    )


# ---------------------------------------------------------------------------
# check_registries
# ---------------------------------------------------------------------------


def check_registries(graph: dict) -> RegistryCheckResult:
    NODES = _registry_dict()
    BASE_TYPES = _base_types_dict()

    unknown_registries: list[RegistryProblem] = []
    unknown_base_types: list[RegistryProblem] = []
    checked_registries = 0
    checked_base_types = 0

    def _check(node_id: str, node: dict) -> None:
        nonlocal checked_registries, checked_base_types
        registry = node.get("registry")
        base_type = node.get("base_type")
        title = node.get("title") or ""
        if registry:
            checked_registries += 1
            if registry not in NODES:
                unknown_registries.append(
                    RegistryProblem(
                        node_id=node_id,
                        short_id=_short(node_id),
                        title=title,
                        registry=registry,
                        base_type=base_type,
                        kind="registry",
                    )
                )
        if base_type:
            checked_base_types += 1
            if base_type not in BASE_TYPES:
                unknown_base_types.append(
                    RegistryProblem(
                        node_id=node_id,
                        short_id=_short(node_id),
                        title=title,
                        registry=registry,
                        base_type=base_type,
                        kind="base_type",
                    )
                )

    # check the top-level graph itself
    top_id = graph.get("id") or "<graph>"
    _check(top_id, graph)

    # check every interior node
    for nid, node in _nodes(graph).items():
        _check(nid, node)

    return RegistryCheckResult(
        unknown_registries=unknown_registries,
        unknown_base_types=unknown_base_types,
        checked_registry_count=checked_registries,
        checked_base_type_count=checked_base_types,
    )
