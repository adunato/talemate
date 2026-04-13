"""
File-I/O choke point for the node-graph analysis tools.

This module also handles **scene-module auto-registration**: when you
load a graph file that lives under ``scenes/<name>/nodes/``, every
sibling ``*.json`` in the same directory is registered into the live
``NODES`` dict so that any ``add_node("...")`` or static-analysis call
that references one of those scene-level modules can resolve it. This
removes the footgun where ``ensure_registry_loaded()`` only walks the
shipped ``SEARCH_PATHS`` and silently doesn't see scene modules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import structlog

from talemate.game.engine.nodes.registry import NODES, import_node_definition

from .analysis import ensure_registry_loaded

__all__ = ["load_graph", "GraphLoadError", "register_scene_modules"]

log = structlog.get_logger("talemate.game.engine.nodes.tools.loader")


class GraphLoadError(ValueError):
    """Raised when a graph file is missing or unparseable."""


def load_graph(
    path: str | Path,
    *,
    extra_module_paths: Iterable[str | Path] | None = None,
) -> dict:
    """Load and parse a node graph JSON file into a plain dict.

    Raises ``GraphLoadError`` for missing files or malformed JSON. The
    returned dict is the raw JSON contents - we deliberately do NOT run
    it through any pydantic model so analysis remains side-effect-free.

    **Scene-module auto-registration** runs as a side effect:

    * If ``path`` matches the pattern ``**/scenes/<name>/nodes/<file>.json``,
      every sibling ``*.json`` in that directory is registered into the
      live ``NODES`` dict via ``register_scene_modules``. This means the
      writer and analysis layer can introspect scene-level modules that
      the standard ``ensure_registry_loaded()`` walk doesn't reach.
    * If ``extra_module_paths`` is given, every ``*.json`` under each of
      those paths is also registered. Use this for non-standard layouts.

    Auto-registration failures are logged at warn-level and never raise —
    the loader's primary job is still to load the requested file.
    """

    p = Path(path)
    if not p.exists():
        raise GraphLoadError(f"Graph file not found: {p}")
    if not p.is_file():
        raise GraphLoadError(f"Not a regular file: {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise GraphLoadError(f"Failed to parse graph JSON {p}: {exc}") from exc

    if not isinstance(data, dict):
        raise GraphLoadError(
            f"Graph JSON {p} did not parse to an object (got {type(data).__name__})"
        )

    # Auto-register scene modules when loading from a scene path. This
    # fires AFTER parsing the requested file (so a malformed file still
    # raises predictably) but BEFORE returning, so downstream callers
    # see the populated registry.
    _maybe_autoregister_scene_modules(p)
    if extra_module_paths:
        for extra in extra_module_paths:
            register_scene_modules(extra)

    return data


def register_scene_modules(directory: str | Path) -> int:
    """Walk ``directory`` for ``*.json`` files and register each as a node
    type in the live ``NODES`` dict. Returns the number of modules newly
    registered. Already-registered registries are skipped.

    Files without a top-level ``registry`` key are silently ignored
    (they aren't node modules — they may be scene shells, asset metadata,
    or other JSON in the same dir).

    The standard node registry (``ensure_registry_loaded()``) is always
    loaded first — scene modules typically reference standard node types
    like ``validation/ValidateValueIsSet`` and ``core/MakeBool`` in their
    interior, and ``import_node_definition`` validates the full graph at
    registration time. Without the standard registry loaded first, every
    scene module that uses any standard node would fail to validate.

    A retry loop handles inter-module dependencies inside the directory:
    if module A references module B and the alphabetic walk hits A first,
    A fails its first attempt because B isn't registered yet, then B
    succeeds, then A retries and succeeds.

    Failures after retries are logged but never raised. Useful when you
    have node modules in a non-standard location and want them visible
    to the writer/analysis layer.

    Why not just call ``registry.import_scene_node_definitions``?
        The native function requires a live ``Scene`` object and stores
        its classes in a per-scene container (``scene._NODE_DEFINITIONS``)
        rather than the global ``NODES`` dict. This helper exists because
        the tools package runs in contexts (CLI subprocesses, tests, one-
        shot scripts) where there is no active scene, and it needs the
        registered classes in the **global** ``NODES`` dict so the writer
        and analysis layer can introspect them. Additionally, this
        version rolls back half-registered classes between retry attempts
        (``@register`` fires synchronously at class definition, and a
        failed ``node.model_validate(node_data)`` leaves a broken class
        behind) — the native scene loader has that same latent issue but
        masks it by wiping its per-scene container on the next load.
        Consolidating these via a shared lower-level helper in
        ``registry.py`` is a worthwhile refactor but out of scope here.
    """

    d = Path(directory)
    if not d.is_dir():
        return 0

    # Make sure the standard registry is loaded before we try to validate
    # scene modules — otherwise references to standard types will fail.
    ensure_registry_loaded()

    # Collect all candidate module entries first so we can iterate with
    # a retry loop for inter-module dependencies.
    candidates: list[tuple[Path, dict, str]] = []
    for json_path in sorted(d.glob("*.json")):
        try:
            with json_path.open("r", encoding="utf-8") as fh:
                module_data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.debug("scene_module_skipped", path=str(json_path), reason=str(exc))
            continue
        if not isinstance(module_data, dict):
            continue
        registry = module_data.get("registry")
        if not registry:
            continue
        if registry in NODES:
            continue
        candidates.append((json_path, module_data, registry))

    registered = 0
    last_errors: dict[str, str] = {}

    # Retry loop: keep trying as long as at least one module made progress
    # in the previous pass. ``import_node_definition`` registers the class
    # via ``@register`` synchronously and THEN validates by instantiating,
    # so a failed validation leaves a half-broken class in NODES. We
    # explicitly remove failed registries between attempts so retries get
    # a clean slate.
    pending = list(candidates)
    progress = True
    while pending and progress:
        progress = False
        next_pending: list[tuple[Path, dict, str]] = []
        for entry in pending:
            json_path, module_data, registry = entry
            try:
                import_node_definition(module_data, NODES)
                registered += 1
                progress = True
                last_errors.pop(registry, None)
            except Exception as exc:  # noqa: BLE001 — see docstring
                # Roll back the half-registered class so the next retry
                # attempt re-creates it cleanly.
                NODES.pop(registry, None)
                last_errors[registry] = str(exc)
                next_pending.append(entry)
        pending = next_pending

    # Anything left in `pending` failed every attempt — log a single
    # summary warning per registry rather than spamming once per attempt.
    for json_path, _module_data, registry in pending:
        log.warning(
            "scene_module_import_failed",
            path=str(json_path),
            registry=registry,
            err=last_errors.get(registry, "unknown"),
        )

    if registered:
        # Clear the writer's metadata cache so any newly-registered
        # registry gets re-probed on its next ``get_node_metadata`` call.
        # Lazy to break the writer -> loader import cycle.
        from .writer import clear_metadata_cache

        clear_metadata_cache()

    return registered


def _maybe_autoregister_scene_modules(graph_path: Path) -> None:
    """If ``graph_path`` looks like ``**/scenes/<name>/nodes/*.json``,
    register every JSON sibling in the same directory.

    The pattern match is intentionally strict: it requires the immediate
    parent directory to be named ``nodes`` AND its grandparent to be a
    direct child of a directory named ``scenes``. This avoids accidentally
    walking unrelated ``scenes/`` directories elsewhere in the filesystem.
    """

    try:
        resolved = graph_path.resolve()
    except OSError:
        return
    parts = resolved.parts
    if len(parts) < 4:
        return
    if parts[-2] != "nodes":
        return
    if parts[-4] != "scenes":
        return
    register_scene_modules(resolved.parent)
