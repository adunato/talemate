"""
Shared helpers for director / agent tests.

Centralizes patterns that were duplicated across multiple test files:

- ``QueuedPromptRequest`` + ``patch_prompt_request_in``: a deterministic
  replacement for the *real* ``talemate.prompts.base.Prompt.request``
  classmethod, keyed by template name. The original ``Prompt`` class is
  preserved everywhere (no ``_StubPromptClass`` substitute) — only its
  ``.request`` method is swapped with ``raising=True`` so the tests fail
  loudly if the method is renamed or removed in production.

- ``add_character_to_scene``: build a real ``Character`` and
  ``Actor``/``Player`` and wire it into a real ``Scene`` exactly the way
  production code does (no shortcuts).
"""

from __future__ import annotations

from collections import deque
from typing import Any

from talemate.character import Character
from talemate.prompts.base import Prompt


class QueuedPromptRequest:
    """Replaces ``Prompt.request`` with a per-template response queue.

    ``Prompt.request`` is an ``@classmethod`` whose call signature is
    ``(uid: str, client, kind, vars=None, **kwargs)``. This stand-in
    matches that signature so the function-under-test sees no behavioral
    difference. Each call records ``{template, kind, vars, kwargs}`` and
    pops the next ``(raw_response, extracted_dict)`` tuple from the queue
    keyed by template name. Empty queue → returns ``("", {})``.
    """

    def __init__(self, responses: dict[str, list]):
        self.responses: dict[str, deque] = {
            k: deque(v) for k, v in responses.items()
        }
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, uid, client, kind, vars=None, **kwargs):
        self.calls.append(
            {"template": uid, "kind": kind, "vars": vars or {}, "kwargs": kwargs}
        )
        queue = self.responses.get(uid)
        if queue is None or not queue:
            return "", {}
        return queue.popleft()


def patch_prompt_request_in(monkeypatch, *modules) -> "PromptPatcher":
    """Return a callable that installs a ``QueuedPromptRequest`` on the real
    ``Prompt`` class.

    ``modules`` is intentionally accepted but **unused for the production
    binding**: the function-under-test imports ``Prompt`` from
    ``talemate.prompts.base`` (sometimes via re-export), and patching the
    canonical class with ``raising=True`` covers every import site. The
    parameter is preserved for API compatibility with old per-module
    patching patterns and to make the test's intent self-documenting.
    """
    # ``modules`` retained for future per-module patching scenarios; today
    # the canonical ``Prompt.request`` patch suffices for every caller.
    del modules

    def install(responses: dict[str, list]) -> QueuedPromptRequest:
        stub = QueuedPromptRequest(responses)

        async def _request(cls, uid, client, kind, vars=None, **kwargs):
            return await stub(uid, client, kind, vars=vars, **kwargs)

        # ``raising=True`` makes the patch fail hard if ``Prompt.request``
        # is renamed or removed — the protection real-type tests are for.
        monkeypatch.setattr(
            Prompt, "request", classmethod(_request), raising=True
        )
        return stub

    return install


# Type alias for clarity in callers.
PromptPatcher = Any  # callable: dict[str, list] -> QueuedPromptRequest


async def add_character_to_scene(
    scene,
    name: str,
    *,
    is_player: bool = False,
    description: str = "",
) -> Character:
    """Add a real ``Character`` to ``scene`` via its real ``Actor``/``Player``.

    Mirrors how production code constructs and wires actors. Used by
    several test files to set up a populated scene without copy-paste.
    """
    character = Character(
        name=name,
        description=description,
        is_player=is_player,
        base_attributes={},
        details={},
        color="#fff",
    )
    actor_cls = scene.Player if is_player else scene.Actor
    actor = actor_cls(character, None)
    await scene.add_actor(actor, commit_to_memory=False)
    if name not in scene.active_characters:
        scene.active_characters.append(name)
    return character
