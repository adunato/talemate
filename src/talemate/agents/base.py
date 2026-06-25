from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
from inspect import signature
import re
import traceback
from abc import ABC
from functools import wraps
from typing import Awaitable, Callable, Literal
import uuid
import pydantic
from pydantic import ConfigDict
import structlog
from typing import TYPE_CHECKING

import talemate.emit.async_signals
import talemate.instance as instance
import talemate.util as util
from talemate.agents.context import ActiveAgent, active_agent
from talemate.emit import emit
from talemate.events import GameLoopStartEvent
from talemate.context import active_scene
from talemate.ux.schema import Action, Column, Note
from talemate.config import get_config, Config
import talemate.config.schema as config_schema
from talemate.client.context import (
    ClientContext,
)
from talemate.game.engine.nodes.core import GraphState
from talemate.game.engine.nodes.registry import get_nodes_by_base_type, get_node
from talemate.game.engine.nodes.run import FunctionWrapper

if TYPE_CHECKING:
    from talemate.tale_mate import Scene

__all__ = [
    "Agent",
    "AgentAction",
    "AgentActionConditional",
    "AgentActionConfig",
    "AgentDetail",
    "AgentEmission",
    "AgentTemplateEmission",
    "set_processing",
    "store_context_state",
]

log = structlog.get_logger("talemate.agents.base")


class AgentActionConditional(pydantic.BaseModel):
    attribute: str
    value: int | float | str | bool | list[int | float | str | bool] | None = None


class AgentActionNote(Note):
    pass


class AgentActionConfig(pydantic.BaseModel):
    type: Literal[
        "autocomplete",
        "blob",
        "bool",
        "flags",
        "number",
        "text",
        "vector2",
        "weights",
        "wstemplate",
        "password",
        "unified_api_key",
    ]
    label: str
    description: str = ""
    value: int | float | str | bool | list | dict | None = None
    default_value: int | float | str | bool | None = None
    max: int | float | None = None
    min: int | float | None = None
    step: int | float | None = None
    graduations: list[dict[str, int | float]] | None = None
    scope: str = "global"
    choices: (
        list[dict[str, str | int | float | bool | list[int | float | bool]]] | None
    ) = None
    note: AgentActionNote | None = None
    expensive: bool = False
    quick_toggle: bool = False
    condition: AgentActionConditional | None = None
    title: str | None = None
    value_migration: Callable | None = pydantic.Field(default=None, exclude=True)
    columns: list[Column] | None = None

    note_on_value: dict[str | int | float | bool, AgentActionNote] = pydantic.Field(
        default_factory=dict
    )
    save_on_change: bool = False

    wstemplate_type: (
        Literal[
            "state_reinforcement",
            "character_attribute",
            "character_detail",
            "spices",
            "writing_style",
            "visual_style",
            "agent_persona",
            "scene_type",
        ]
        | None
    ) = None
    wstemplate_filter: dict[str, str] | None = None

    @pydantic.field_validator("note", mode="before")
    @classmethod
    def validate_note(cls, v):
        if isinstance(v, str):
            return AgentActionNote(text=v)
        return v

    @pydantic.model_validator(mode="after")
    def ensure_note_is_object(self):
        if isinstance(self.note, str):
            self.note = AgentActionNote(text=self.note)
        return self

    @pydantic.field_serializer("note")
    def serialize_note(self, v):
        if isinstance(v, str):
            return AgentActionNote(text=v)
        return v

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AgentAction(pydantic.BaseModel):
    enabled: bool = True
    label: str
    description: str = ""
    warning: str = ""
    config: dict[str, AgentActionConfig] | None = None
    condition: AgentActionConditional | None = None
    container: bool = False
    icon: str | None = None
    can_be_disabled: bool = False
    quick_toggle: bool = False
    experimental: bool = False
    subtitle: str | None = None
    tools: list[Action] = pydantic.Field(default_factory=list)

    # When set, marks this action as a child of another action — used by the
    # frontend to visually group dynamic children under their registry tab.
    parent_key: str | None = None

    # When set, the frontend renders the registry-management UI for this
    # action using the named component (e.g. "TTSOpenAICompatibleBackends").
    # Falls back to the generic ``DynamicAgentRegistry`` component when None.
    # Only meaningful on actions that are themselves dynamic registries.
    dynamic_registry_component: str | None = None


# ---------------------------------------------------------------------------
# Dynamic action registries
# ---------------------------------------------------------------------------
#
# An AgentAction whose config contains a reserved field named
# ``dynamic_children`` (an AgentActionConfig of type "blob") is treated as a
# *registry*. The blob holds a JSON-encoded list of ``{"slug": ..., "label":
# ...}`` entries; one synthesized child AgentAction per entry is installed
# onto ``self.actions`` at config-load time via the agent-supplied factory.
#
# This lets agents expose user-managed lists of named configurations
# (TTS OpenAI-compatible backends, future per-instance integrations, etc.)
# without changes to AgentActionConfig schema or the save/load pipeline.

DYNAMIC_CHILDREN_FIELD = "dynamic_children"


def optimize_prompt_caching_action() -> AgentAction:
    """Reusable per-agent action for prompt caching optimization override."""
    return AgentAction(
        enabled=True,
        label="Prompt Caching",
        config={
            "optimize_prompt_caching": AgentActionConfig(
                type="text",
                label="Optimize for Prompt Caching",
                description="Place volatile context (long-term memory, dynamic notes) after the scene history for better prompt caching on API backends. May confuse weaker models. 'Auto' defers to the client setting.",
                value="auto",
                choices=[
                    {"label": "Auto (use client setting)", "value": "auto"},
                    {"label": "On", "value": "on"},
                    {"label": "Off", "value": "off"},
                ],
            ),
        },
    )


class AgentDetail(pydantic.BaseModel):
    value: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str = "grey"
    hidden: bool = False


class DynamicInstruction(pydantic.BaseModel):
    title: str
    content: str

    def __str__(self) -> str:
        if not self.content:
            return ""

        return "\n".join(
            [f"<|SECTION:{self.title}|>", self.content, "<|CLOSE_SECTION|>"]
        )


def args_and_kwargs_to_dict(
    fn, args: list, kwargs: dict, filter: list[str] = None
) -> dict:
    """
    Takes a list of arguments and a dict of keyword arguments and returns
    a dict mapping parameter names to their values.

    Args:
        fn: The function whose parameters we want to map
        args: List of positional arguments
        kwargs: Dictionary of keyword arguments
        filter: List of parameter names to include in the result, if None all parameters are included

    Returns:
        Dict mapping parameter names to their values
    """
    sig = signature(fn)
    bound_args = sig.bind(*args, **kwargs)
    bound_args.apply_defaults()
    rv = dict(bound_args.arguments)
    rv.pop("self", None)

    if filter:
        for key in list(rv.keys()):
            if key not in filter:
                rv.pop(key)

    return rv


class store_context_state:
    """
    Flag to store a function's arguments in the agent's context state.

    Any arguments passed to the function will be stored in the agent's context

    If no arguments are passed, all arguments will be stored.

    Keyword arguments can be passed to store additional values in the context state.
    """

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, fn):
        fn.store_context_state = self.args
        fn.store_context_state_kwargs = self.kwargs
        return fn


def set_processing(fn):
    """
    decorator that emits the agent status as processing while the function
    is running.

    Done via a try - final block to ensure the status is reset even if
    the function fails.
    """

    @wraps(fn)
    async def wrapper(self, *args, **kwargs):
        await self.wait_for_background_processing()
        is_background = self.is_background_task()

        with ClientContext():
            scene = active_scene.get()

            if scene:
                scene.continue_actions()

            with ActiveAgent(self, fn, args, kwargs) as active_agent_context:
                previous_action = self._current_action
                try:
                    action_name = fn.__name__
                    if action_name == "delegate":
                        action_name = args[0].__name__

                    self._current_action = action_name
                    if is_background:
                        await self.emit_status()
                    else:
                        await self.emit_status(processing=True)

                    # Now pass the complete args list
                    if getattr(fn, "store_context_state", None) is not None:
                        all_args = args_and_kwargs_to_dict(
                            fn,
                            [self] + list(args),
                            kwargs,
                            getattr(fn, "store_context_state", []),
                        )
                        if getattr(fn, "store_context_state_kwargs", None) is not None:
                            all_args.update(
                                getattr(fn, "store_context_state_kwargs", {})
                            )

                        all_args[f"fn_{fn.__name__}"] = True

                        active_agent_context.state_params = all_args

                        self.set_context_states(**all_args)

                    return await fn(self, *args, **kwargs)
                finally:
                    try:
                        self._current_action = previous_action
                        if is_background:
                            await self.emit_status()
                        else:
                            await self.emit_status(processing=False)
                    except RuntimeError as exc:
                        # not sure why this happens
                        # some concurrency error?
                        log.error("error emitting agent status", exc=exc)

    return wrapper


class Agent(ABC):
    """
    Base agent class, defines a role
    """

    agent_type = "agent"
    verbose_name = None
    set_processing = set_processing
    requires_llm_client = True
    websocket_handler = None
    essential = True
    ready_check_error = None

    # Debounce tracking for emit_status
    _emit_status_debounce_task: asyncio.Task | None = None

    _current_action: str | None = None
    _background_task: asyncio.Task | None = None
    _background_action: str | None = None
    _pending_background_action: (
        tuple[str, Callable[[], Awaitable], Callable | None] | None
    ) = None
    _background_waiters: int = 0

    @classmethod
    def init_actions(
        cls, actions: dict[str, AgentAction] | None = None
    ) -> dict[str, AgentAction]:
        if actions is None:
            actions = {}

        return actions

    @classmethod
    def config_options(cls, agent=None):
        config_options = {
            "client": [
                name for name, client in instance.client_instances() if client.enabled
            ],
            "enabled": agent.enabled if agent else True,
            "has_toggle": agent.has_toggle if agent else False,
            "experimental": agent.experimental if agent else False,
            "requires_llm_client": cls.requires_llm_client,
        }
        actions = getattr(agent, "actions", None)

        if actions:
            config_options["actions"] = {k: v.model_dump() for k, v in actions.items()}
        else:
            config_options["actions"] = {}

        return config_options

    @classmethod
    async def init_nodes(cls, scene: "Scene", state: GraphState):
        log.debug(f"{cls.agent_type}.init_nodes")

        if not cls.websocket_handler:
            return

        cls.websocket_handler.clear_sub_handlers()

        for node_cls in get_nodes_by_base_type("agents/AgentWebsocketHandler"):
            _node = node_cls()
            handler_name = _node.get_property("name")
            agent_type = _node.get_property("agent")
            if agent_type != cls.agent_type:
                continue

            async def handler_fn(router, data: dict, captured_node=_node):
                agent = instance.get_agent(cls.agent_type)

                async def wrapped_agent_action(agent: "Agent", *args, **kwargs):
                    state: GraphState = scene.nodegraph_state
                    node = get_node(captured_node.registry)()
                    fn = FunctionWrapper(node, node, state)
                    try:
                        await fn(websocket_router=router, data=data)
                    except Exception as e:
                        log.error(
                            "Error in agent action",
                            agent=cls.agent_type,
                            error=traceback.format_exc(),
                        )
                        emit(
                            "status",
                            message=f"Error in agent action: {e}",
                            status="error",
                        )

                # Set name before decoration so @wraps copies it to wrapper
                wrapped_agent_action.__name__ = f"{cls.agent_type}_{handler_name}"
                wrapped_agent_action = cls.set_processing(wrapped_agent_action)
                # Also set on wrapper in case @wraps didn't copy it properly
                wrapped_agent_action.__name__ = f"{cls.agent_type}_{handler_name}"
                asyncio.create_task(wrapped_agent_action(agent))

            cls.websocket_handler.register_sub_handler(handler_name, handler_fn)
            log.debug(
                f"{cls.agent_type}.init_nodes.websocket_handler",
                handler_name=handler_name,
            )

    @property
    def config(self) -> Config:
        return get_config()

    @property
    def agent_details(self):
        if hasattr(self, "client"):
            if self.client:
                return self.client.name
        return None

    @property
    def ready(self):
        if not self.requires_llm_client:
            return True

        if not hasattr(self, "client"):
            return False

        if not getattr(self.client, "enabled", True):
            return False

        if self.client and self.client.current_status in ["error", "warning"]:
            return False

        return self.client is not None

    @property
    def status(self):
        if not self.enabled:
            return "disabled"

        if not self.ready:
            return "uninitialized"

        if getattr(self, "processing", 0) > 0:
            return "busy"

        if getattr(self, "processing_bg", 0) > 0:
            return "busy_bg"

        return "idle"

    @property
    def enabled(self):
        # by default, agents are enabled, an agent class that
        # is disableable should override this property
        return True

    @property
    def disable(self):
        # by default, agents are enabled, an agent class that
        # is disableable should override this property to
        # disable the agent
        pass

    @property
    def has_toggle(self):
        # by default, agents do not have toggles to enable / disable
        # an agent class that is disableable should override this property
        return False

    @property
    def experimental(self):
        # by default, agents are not experimental, an agent class that
        # is experimental should override this property
        return False

    @property
    def meta(self):
        meta = {
            "essential": self.essential,
            "current_action": self._current_action or self._background_action,
            "background_waiters": getattr(self, "_background_waiters", 0),
        }

        return meta

    @property
    def sanitized_action_config(self):
        if not getattr(self, "actions", None):
            return {}

        return {k: v.model_dump() for k, v in self.actions.items()}

    # scene state

    def context_fingerprint(self, extra: list[str] = None) -> str | None:
        active_agent_context = active_agent.get()

        if not active_agent_context:
            return None

        if self.scene.history:
            fingerprint = f"{self.scene.history[-1].fingerprint}-{active_agent_context.first.fingerprint}"
        else:
            fingerprint = f"START-{active_agent_context.first.fingerprint}"

        if extra:
            for extra_key in extra:
                fingerprint += f"-{hash(extra_key)}"

        return fingerprint

    def get_scene_state(self, key: str, default=None):
        agent_state = self.scene.agent_state.get(self.agent_type, {})
        return agent_state.get(key, default)

    def set_scene_states(self, **kwargs):
        agent_state = self.scene.agent_state.get(self.agent_type, {})
        for key, value in kwargs.items():
            agent_state[key] = value
        self.scene.agent_state[self.agent_type] = agent_state

    def dump_scene_state(self):
        return self.scene.agent_state.get(self.agent_type, {})

    # active agent context state

    def get_context_state(self, key: str, default=None):
        key = f"{self.agent_type}__{key}"
        try:
            return active_agent.get().state.get(key, default)
        except AttributeError:
            log.warning("get_context_state error", agent=self.agent_type, key=key)
            return default

    def set_context_states(self, **kwargs):
        try:
            items = {f"{self.agent_type}__{k}": v for k, v in kwargs.items()}
            active_agent.get().state.update(items)
            log.debug(
                "set_context_states",
                agent=self.agent_type,
                state=active_agent.get().state,
            )
        except AttributeError:
            log.error("set_context_states error", agent=self.agent_type, kwargs=kwargs)

    def dump_context_state(self):
        try:
            return active_agent.get().state
        except AttributeError:
            return {}

    ###

    async def _handle_ready_check(self, fut: asyncio.Future):
        callback_failure = getattr(self, "on_ready_check_failure", None)
        if fut.cancelled():
            if callback_failure:
                await callback_failure()
            return

        if fut.exception():
            exc = fut.exception()
            self.ready_check_error = exc
            log.error("agent ready check error", agent=self.agent_type, exc=exc)
            if callback_failure:
                await callback_failure(exc)
            return

        callback = getattr(self, "on_ready_check_success", None)
        result = fut.result()
        if callback:
            await callback(result)

    async def setup_check(self):
        """Per-status-tick auto-setup hook.

        Called once per status tick by ``agent_ready_checks`` for *every*
        live agent slot — including agents whose ``enabled`` flag is False.
        Overrides must therefore be safe to invoke when the agent is
        disabled (e.g. they can still flip ``is_enabled`` on themselves
        when an external precondition is met, as TTSAgent does when a
        client reports a freshly loaded TTS model).
        """
        return False

    async def ready_check(self, task: asyncio.Task = None):
        self.ready_check_error = None
        if task:
            task.add_done_callback(
                lambda fut: asyncio.create_task(self._handle_ready_check(fut))
            )
            return
        return True

    # ------------------------------------------------------------------
    # Dynamic action registries
    # ------------------------------------------------------------------

    def is_dynamic_registry(self, action_key: str) -> bool:
        """Return True if the named action is a dynamic-children registry."""
        actions = getattr(self, "actions", None) or {}
        action = actions.get(action_key)
        return bool(
            action and action.config and DYNAMIC_CHILDREN_FIELD in action.config
        )

    def dynamic_registry_keys(self) -> list[str]:
        actions = getattr(self, "actions", None) or {}
        return [k for k in actions if self.is_dynamic_registry(k)]

    def dynamic_children_entries(self, registry_key: str) -> list[dict]:
        """Return parsed [{slug, label}, ...] from the registry's blob value."""
        if not self.is_dynamic_registry(registry_key):
            return []
        raw = self.actions[registry_key].config[DYNAMIC_CHILDREN_FIELD].value or "[]"
        try:
            entries = json.loads(raw)
        except (TypeError, ValueError):
            log.warning(
                "dynamic registry blob is not valid JSON",
                agent=self.agent_type,
                registry_key=registry_key,
            )
            return []
        return [e for e in entries if isinstance(e, dict) and e.get("slug")]

    def dynamic_child_slugs(self, registry_key: str) -> list[str]:
        return [e["slug"] for e in self.dynamic_children_entries(registry_key)]

    def _write_dynamic_children_entries(
        self, registry_key: str, entries: list[dict]
    ) -> None:
        self.actions[registry_key].config[DYNAMIC_CHILDREN_FIELD].value = json.dumps(
            entries
        )

    def dynamic_action_factory(
        self, registry_key: str, slug: str, label: str
    ) -> "AgentAction":
        """Subclasses override to produce a per-child AgentAction definition.

        The returned action MUST have ``parent_key=registry_key`` set so the
        frontend can group it under its registry tab.
        """
        raise NotImplementedError(
            f"{type(self).__name__} declared dynamic registry '{registry_key}' "
            "but did not implement dynamic_action_factory()"
        )

    def install_dynamic_children(self, registry_key: str) -> None:
        """Synthesize per-child actions from the registry's stored entries.

        Idempotent. Existing entries with matching slugs are left alone so that
        in-memory state (e.g., applied config values) survives re-installation.
        Stale child entries (slugs no longer in the registry) are removed.
        """
        if not self.is_dynamic_registry(registry_key):
            return

        entries = self.dynamic_children_entries(registry_key)
        slugs = {e["slug"] for e in entries}

        for entry in entries:
            slug = entry["slug"]
            label = entry.get("label") or slug
            if slug in self.actions:
                continue
            child = self.dynamic_action_factory(registry_key, slug, label)
            child.parent_key = registry_key
            self.actions[slug] = child

        # Drop synthesized children that are no longer registered.
        for action_key in list(self.actions.keys()):
            action = self.actions[action_key]
            if action.parent_key == registry_key and action_key not in slugs:
                del self.actions[action_key]

    # Lifecycle hooks — subclasses override as needed.
    #
    # These run *synchronously* immediately after the agent's in-memory
    # registry state mutates. Use them only for in-memory updates (refreshing
    # choices, purging cached data, etc.). For I/O-bound cleanup (e.g.,
    # persisting external state to disk), override
    # ``persist_dynamic_external_state`` instead — it's awaited by the
    # websocket plugin after the sync hook fires.
    def on_dynamic_child_added(self, registry_key: str, slug: str, label: str) -> None:
        return None

    def on_dynamic_child_removed(self, registry_key: str, slug: str) -> None:
        return None

    def on_dynamic_child_renamed(
        self, registry_key: str, slug: str, label: str
    ) -> None:
        return None

    async def persist_dynamic_external_state(self, registry_key: str) -> None:
        """Persist any external state mutated by sync lifecycle hooks.

        Default: no-op. TTSAgent overrides to flush its voice library after
        a backend purge, for example. Called by the agent-config websocket
        plugin after register/unregister so failures surface to the UI.
        """
        return None

    def reserved_slugs_for_registry(self, registry_key: str) -> set[str]:
        """Slugs the agent considers reserved for this registry.

        Subclasses may override to prevent registration of slugs that would
        collide with existing static action keys, dispatch identifiers, etc.

        Scope: the framework itself only enforces *intra-registry* slug
        uniqueness (no two children of the same registry share a slug).
        Cross-action collisions (with static action keys, other registries,
        runtime dispatch tables) are the agent's responsibility to declare
        here.
        """
        return set()

    # Mutation helpers.
    def register_dynamic_child(self, registry_key: str, slug: str, label: str) -> None:
        if not self.is_dynamic_registry(registry_key):
            raise ValueError(f"'{registry_key}' is not a dynamic registry")
        if not slug:
            raise ValueError("slug is required")
        if slug in self.reserved_slugs_for_registry(registry_key):
            raise ValueError(f"slug '{slug}' is reserved")
        entries = self.dynamic_children_entries(registry_key)
        if any(e["slug"] == slug for e in entries):
            raise ValueError(f"a child with slug '{slug}' already exists")
        entries.append({"slug": slug, "label": label or slug})
        self._write_dynamic_children_entries(registry_key, entries)
        self.install_dynamic_children(registry_key)
        self.on_dynamic_child_added(registry_key, slug, label or slug)

    def unregister_dynamic_child(self, registry_key: str, slug: str) -> None:
        if not self.is_dynamic_registry(registry_key):
            raise ValueError(f"'{registry_key}' is not a dynamic registry")
        entries = self.dynamic_children_entries(registry_key)
        if not any(e["slug"] == slug for e in entries):
            return
        entries = [e for e in entries if e["slug"] != slug]
        self._write_dynamic_children_entries(registry_key, entries)
        self.install_dynamic_children(registry_key)
        self.on_dynamic_child_removed(registry_key, slug)

    def rename_dynamic_child_label(
        self, registry_key: str, slug: str, label: str
    ) -> None:
        if not self.is_dynamic_registry(registry_key):
            raise ValueError(f"'{registry_key}' is not a dynamic registry")
        entries = self.dynamic_children_entries(registry_key)
        for entry in entries:
            if entry["slug"] == slug:
                entry["label"] = label or slug
                break
        else:
            return
        self._write_dynamic_children_entries(registry_key, entries)
        if slug in self.actions:
            self.actions[slug].label = label or slug
        self.on_dynamic_child_renamed(registry_key, slug, label or slug)

    # Resolver helpers — used by agents that dispatch via name-prefixed methods.
    def dynamic_attr(self, registry_key: str, slug: str, name: str, default=None):
        """Resolve a property-like helper for a dynamic child.

        Looks up ``self._<registry_key>_<name>`` and invokes it with the
        child slug. Returns ``default`` if the helper is missing.
        """
        fn = getattr(self, f"_{registry_key}_{name}", None)
        if fn is None:
            return default
        return fn(slug)

    def dynamic_method(self, registry_key: str, slug: str, name: str, default=None):
        """Resolve a callable helper for a dynamic child.

        Returns a partial that pre-binds the slug, so callers can invoke it
        the same way they would invoke a static per-api method.
        """
        fn = getattr(self, f"_{registry_key}_{name}", None)
        if fn is None:
            return default
        return functools.partial(fn, slug)

    async def apply_config(self, *args, **kwargs):
        if self.has_toggle and "enabled" in kwargs:
            self.is_enabled = kwargs.get("enabled", False)

        if not getattr(self, "actions", None):
            return

        # Pre-pass: for every dynamic registry, restore the blob value first
        # then synthesize per-child actions, so the standard apply loop below
        # can resolve and apply each child's saved config values.
        for registry_key in self.dynamic_registry_keys():
            blob_value = (
                kwargs.get("actions", {})
                .get(registry_key, {})
                .get("config", {})
                .get(DYNAMIC_CHILDREN_FIELD, {})
                .get("value")
            )
            if blob_value is not None:
                self.actions[registry_key].config[
                    DYNAMIC_CHILDREN_FIELD
                ].value = blob_value
            self.install_dynamic_children(registry_key)

        for action_key, action in self.actions.items():
            if not kwargs.get("actions"):
                continue

            action.enabled = (
                kwargs.get("actions", {}).get(action_key, {}).get("enabled", False)
            )

            if not action.config:
                continue

            for config_key, _config in action.config.items():
                try:
                    _config.value = (
                        kwargs.get("actions", {})
                        .get(action_key, {})
                        .get("config", {})
                        .get(config_key, {})
                        .get("value", _config.value)
                    )
                    if _config.value_migration and callable(_config.value_migration):
                        _config.value = _config.value_migration(_config.value)
                except AttributeError:
                    pass

    async def save_config(self):
        """
        Saves the agent config to the config file.

        If no config object is provided, the config is loaded from the config file.
        """

        app_config: Config = get_config()

        app_config.agents[self.agent_type] = config_schema.Agent(
            name=self.agent_type,
            client=self.client.name if getattr(self, "client", None) else None,
            enabled=self.enabled,
            actions={
                action_key: config_schema.AgentAction(
                    enabled=action.enabled,
                    config={
                        config_key: config_schema.AgentActionConfig(
                            value=config_obj.value
                        )
                        for config_key, config_obj in action.config.items()
                        if config_obj.type != "unified_api_key"
                    },
                )
                for action_key, action in self.actions.items()
            },
        )
        log.debug(
            "saving agent config",
            agent=self.agent_type,
            config=app_config.agents[self.agent_type],
        )

        app_config.dirty = True

    async def on_game_loop_start(self, event: GameLoopStartEvent):
        """
        Finds all ActionConfigs that have a scope of "scene" and resets them to their default values
        """

        if not getattr(self, "actions", None):
            return

        for _, action in self.actions.items():
            if not action.config:
                continue

            for _, _config in action.config.items():
                if _config.scope == "scene":
                    # if default_value is None, just use the `type` of the current
                    # value
                    if _config.default_value is None:
                        default_value = type(_config.value)()
                    else:
                        default_value = _config.default_value

                    log.debug(
                        "resetting config", config=_config, default_value=default_value
                    )
                    _config.value = default_value

        await self.emit_status()

    async def _do_emit_status(self):
        """Internal method that performs the actual emission"""
        emit(
            "agent_status",
            message=self.verbose_name or "",
            id=self.agent_type,
            status=self.status,
            details=self.agent_details,
            meta=self.meta,
            data=self.config_options(agent=self),
        )

    async def _debounced_emit_status(self):
        """Internal method for debounced emission"""
        await asyncio.sleep(0.05)  # 50ms debounce
        self._emit_status_debounce_task = None
        await self._do_emit_status()

    async def emit_status(self, processing: bool = None):
        # should keep a count of processing requests, and when the
        # number is 0 status is "idle", if the number is greater than 0
        # status is "busy"
        #
        # increase / decrease based on value of `processing`

        if getattr(self, "processing", None) is None:
            self.processing = 0

        # Always update processing counter immediately
        if processing is False:
            self.processing -= 1
            self.processing = max(0, self.processing)
        elif processing is True:
            self.processing += 1

        # If processing=True, emit immediately (user expects instant feedback when work starts)
        if processing is True:
            # Cancel any pending debounce
            if (
                self._emit_status_debounce_task
                and not self._emit_status_debounce_task.done()
            ):
                self._emit_status_debounce_task.cancel()
            self._emit_status_debounce_task = None

            await self._do_emit_status()
        else:
            # For processing=False or None, debounce the emission
            # Cancel and replace any existing debounce task
            if (
                self._emit_status_debounce_task
                and not self._emit_status_debounce_task.done()
            ):
                self._emit_status_debounce_task.cancel()

            self._emit_status_debounce_task = asyncio.create_task(
                self._debounced_emit_status()
            )

    async def _handle_background_processing(
        self, fut: asyncio.Future, error_handler=None
    ):
        try:
            if fut.cancelled():
                return

            if fut.exception():
                exc = fut.exception()
                tb = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                log.error(
                    "background processing error",
                    agent=self.agent_type,
                    exc=exc,
                    traceback=tb,
                )

                if error_handler:
                    await error_handler(fut.exception())

                await self.emit_status()
                return

            log.info("background processing done", agent=self.agent_type)
        finally:
            self.processing_bg -= 1
            await self.emit_status()

    @property
    def background_task(self) -> asyncio.Task | None:
        return getattr(self, "_background_task", None)

    def is_background_task(self) -> bool:
        task = getattr(self, "_background_task", None)
        return task is not None and task is asyncio.current_task()

    async def wait_for_background_processing(self):
        task = self.background_task
        if not task or task is asyncio.current_task():
            return

        self._background_waiters = getattr(self, "_background_waiters", 0) + 1
        await self.emit_status()
        try:
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._background_waiters = max(0, self._background_waiters - 1)
            await self.emit_status()

    async def _handle_single_background_task(
        self,
        task: asyncio.Task,
    ):
        if not task.cancelled():
            task.exception()
        if getattr(self, "_background_task", None) is task:
            self._background_task = None
            self._background_action = None
            self._pending_background_action = None
            self.processing_bg = max(0, getattr(self, "processing_bg", 0) - 1)
            await self.emit_status()

    async def _run_background_action_queue(
        self,
        action_name: str,
        action: Callable[[], Awaitable],
        error_handler=None,
    ):
        first_error = None

        while True:
            self._background_action = action_name
            try:
                await action()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(
                    "background agent action failed",
                    agent=self.agent_type,
                    action=action_name,
                    exc=exc,
                    traceback="".join(
                        traceback.format_exception(
                            type(exc), exc, exc.__traceback__
                        )
                    ),
                )
                if error_handler:
                    await error_handler(exc)
                if first_error is None:
                    first_error = exc

            pending = self._pending_background_action
            self._pending_background_action = None
            if not pending:
                break

            action_name, action, error_handler = pending

        if first_error:
            raise first_error

    async def run_background_action(
        self,
        action_name: str,
        action: Callable[[], Awaitable],
        error_handler=None,
    ):
        """
        Run one non-blocking action for this agent.

        If another background action is already active, coalesce the requested
        action into one pending rerun and return immediately.
        """
        task = self.background_task
        if task and not task.done():
            self._pending_background_action = (
                action_name,
                action,
                error_handler,
            )
            return task
        if task:
            await self._handle_single_background_task(task)

        task = asyncio.create_task(
            self._run_background_action_queue(
                action_name,
                action,
                error_handler=error_handler,
            ),
            name=f"talemate:{self.agent_type}:{action_name}",
        )
        self._background_task = task
        self._background_action = action_name
        self.processing_bg = getattr(self, "processing_bg", 0) + 1
        await self.emit_status()
        task.add_done_callback(
            lambda fut: asyncio.create_task(
                self._handle_single_background_task(fut)
            )
        )
        return task

    def action_execution(self, action_name: str) -> str:
        action = getattr(self, "actions", {}).get(action_name)
        if not action or not action.config:
            return "blocking"
        config = action.config.get("execution")
        return config.value if config else "blocking"

    async def run_configured_action(
        self,
        action_name: str,
        action: Callable[[], Awaitable],
        error_handler=None,
    ):
        if self.action_execution(action_name) == "background":
            return await self.run_background_action(
                action_name,
                action,
                error_handler=error_handler,
            )
        return await action()

    async def run_configured_actions(
        self,
        actions: list[tuple[str, Callable[[], Awaitable]]],
        background_action_name: str = "background_updates",
        error_handler=None,
    ):
        """
        Run blocking actions first, then group all background-configured actions
        into this agent's single background task.
        """
        blocking_actions = []
        background_actions = []

        for action_name, action in actions:
            if self.action_execution(action_name) == "background":
                background_actions.append(action)
            else:
                blocking_actions.append(action)

        results = []
        for action in blocking_actions:
            results.append(await action())

        if background_actions:

            async def run_background_actions():
                background_results = []
                for action in background_actions:
                    background_results.append(await action())
                return background_results

            results.append(
                await self.run_background_action(
                    background_action_name,
                    run_background_actions,
                    error_handler=error_handler,
                )
            )

        return results

    async def set_background_processing(self, task: asyncio.Task, error_handler=None):
        log.info("set_background_processing", agent=self.agent_type)
        if not hasattr(self, "processing_bg"):
            self.processing_bg = 0

        self.processing_bg += 1

        await self.emit_status()
        task.add_done_callback(
            lambda fut: asyncio.create_task(
                self._handle_background_processing(fut, error_handler)
            )
        )

    def connect(self, scene):
        background_task = self.background_task
        if background_task and getattr(self, "scene", None) is not scene:
            background_task.cancel()
        self.scene = scene
        talemate.emit.async_signals.get("game_loop_start").connect(
            self.on_game_loop_start
        )

    def clean_result(self, result):
        if "#" in result:
            result = result.split("#")[0]

        # Removes partial sentence at the end
        result = re.sub(r"[^\.\?\!]+(\n|$)", "", result)
        result = result.strip()

        if ":" in result:
            result = result.split(":")[1].strip()

        return result

    async def get_history_memory_context(
        self,
        memory_history_context_max: int,
        memory_context_max: int,
        exclude: list = [],
        exclude_fn: Callable = None,
    ):
        current_memory_context = []
        memory_helper = instance.get_agent("memory")
        if memory_helper:
            history_messages = "\n".join(
                self.scene.recent_history(memory_history_context_max)
            )
            memory_tokens = 0
            for memory in await memory_helper.agent.get(history_messages):
                if memory in exclude:
                    continue

                if exclude_fn:
                    for split in memory.split("\n"):
                        if exclude_fn(split):
                            continue

                memory_tokens += util.count_tokens(memory)

                if memory_tokens > memory_context_max:
                    break

                current_memory_context.append(memory)
        return current_memory_context

    # LLM client related methods. These are called during or after the client
    # sends the prompt to the API.

    def inject_prompt_paramters(
        self, prompt_param: dict, kind: str, agent_function_name: str
    ):
        """
        Injects prompt parameters before the client sends off the prompt
        Override as needed.
        """
        pass

    def allow_repetition_break(
        self, kind: str, agent_function_name: str, auto: bool = False
    ):
        """
        Returns True if repetition breaking is allowed, False otherwise.
        """
        return False

    @set_processing
    async def delegate(self, fn: Callable, *args, **kwargs):
        """
        Wraps a function as an agent action, allowing it to be called
        by the agent.
        """
        return await fn(*args, **kwargs)

    async def emit_message(
        self, header: str, message: str | list[dict], meta: dict = None, **data
    ):
        if not data:
            data = {}

        if not meta:
            meta = {}

        if "uuid" not in data:
            data["uuid"] = str(uuid.uuid4())

        if "agent" not in data:
            data["agent"] = self.agent_type

        data["header"] = header
        emit(
            "agent_message",
            message=message,
            data=data,
            meta=meta,
            websocket_passthrough=True,
        )


@dataclasses.dataclass
class AgentEmission:
    agent: Agent


@dataclasses.dataclass
class AgentTemplateEmission(AgentEmission):
    template_vars: dict = dataclasses.field(default_factory=dict)
    response: str = None
    dynamic_instructions: list[DynamicInstruction] = dataclasses.field(
        default_factory=list
    )


@dataclasses.dataclass
class RagBuildSubInstructionEmission(AgentEmission):
    sub_instruction: str | None = None
