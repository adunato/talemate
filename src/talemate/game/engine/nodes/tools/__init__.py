"""
Static analysis, mutation, and layout tools for Talemate node graphs.

This toolset is primarily aimed at helping **LLM-driven agents** author
and modify Talemate node-graph JSON files safely. Hand-editing these
graphs is error-prone for language models — socket names, property
keys, registry paths, and edge wiring are all easy to typo and produce
silently-broken files. The tools here exist so an agent can:

* **Analyze** existing graphs (``analysis.py``, exposed via the
  ``python -m talemate.game.engine.nodes.tools`` CLI) to understand
  structure before modifying anything — ``summary``, ``stages``,
  ``node``, ``rtrace``, ``check-registries``, etc.
* **Mutate** graphs programmatically (``writer.py``) via
  ``GraphWriter``, which validates socket names and property keys
  against the live node-class registry at write time and raises
  ``UnknownSocketError`` / ``UnknownPropertyError`` / ``CycleError`` /
  etc. rather than letting typos slip through into the JSON.
* **Lay out** graphs deterministically (``layout.py``) so the agent
  never has to pick ``(x, y)`` coordinates — a stage-stratified
  algorithm handles positioning with predecessor-aware row placement
  and height estimation.
* **Load** graph JSON with scene-module auto-registration
  (``loader.py``) so sibling scene modules are visible to the writer
  and analysis layer without manual registry setup.

A human working directly in the Talemate visual editor does not need
any of this — the editor handles socket wiring, layout, and validation
natively. These tools exist specifically for the out-of-editor,
machine-authored path.
"""

from .analysis import (
    ConsumersResult,
    EdgeInfo,
    FeedsResult,
    GraphSummary,
    NodeDetails,
    NodeListEntry,
    RegistryCheckResult,
    StageMapResult,
    TraceNode,
    check_registries,
    consumers,
    ensure_registry_loaded,
    feeds,
    get_node,
    list_edges,
    list_nodes,
    resolve_node_id,
    stage_map,
    summarize,
    trace_backward,
    trace_forward,
)
from .layout import LayoutError, LayoutOptions, layout_graph
from .loader import GraphLoadError, load_graph, register_scene_modules
from .writer import (
    AlreadyConnectedError,
    CycleError,
    DynamicInputError,
    GraphWriter,
    GroupColor,
    GroupError,
    NodeMetadata,
    NodeNotFoundError,
    NotConnectedError,
    UnknownPropertyError,
    UnknownRegistryError,
    UnknownSocketError,
    WriterError,
    add_dynamic_input,
    add_group,
    add_node,
    clear_metadata_cache,
    connect,
    disconnect,
    get_node_metadata,
    remove_dynamic_input,
    remove_node,
)

__all__ = [
    # analysis models
    "GraphSummary",
    "NodeListEntry",
    "NodeDetails",
    "EdgeInfo",
    "FeedsResult",
    "ConsumersResult",
    "TraceNode",
    "StageMapResult",
    "RegistryCheckResult",
    # analysis functions
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
    "resolve_node_id",
    "ensure_registry_loaded",
    # loader
    "GraphLoadError",
    "load_graph",
    "register_scene_modules",
    # writer
    "GraphWriter",
    "NodeMetadata",
    "GroupColor",
    "get_node_metadata",
    "clear_metadata_cache",
    "add_node",
    "remove_node",
    "connect",
    "disconnect",
    "add_dynamic_input",
    "remove_dynamic_input",
    "add_group",
    "WriterError",
    "UnknownRegistryError",
    "UnknownSocketError",
    "UnknownPropertyError",
    "AlreadyConnectedError",
    "NotConnectedError",
    "CycleError",
    "DynamicInputError",
    "GroupError",
    "NodeNotFoundError",
    # layout
    "layout_graph",
    "LayoutError",
    "LayoutOptions",
]
