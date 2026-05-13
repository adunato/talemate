import structlog
from typing import TYPE_CHECKING
from talemate.instance import get_agent
from talemate.emit import emit
import talemate.events as events
import talemate.emit.async_signals as async_signals
from talemate.context import regeneration_context
from talemate.scene_message import (
    SceneMessage,
    CharacterMessage,
    NarratorMessage,
    ReinforcementMessage,
    ContextInvestigationMessage,
)

if TYPE_CHECKING:
    from talemate.tale_mate import Scene, Character

__all__ = [
    "regenerate",
    "regenerate_message",
    "regenerate_character_message",
    "regenerate_target_message",
    "ensure_regenerate_allowed",
]

log = structlog.get_logger("talemate.regenerate")


def regenerate_target_message(scene: "Scene") -> SceneMessage | None:
    """
    Return the message that ``regenerate(scene)`` would attempt to
    regenerate, without mutating history (skips trailing reinforcement
    messages).
    """
    cur_idx = -1
    try:
        message = scene.history[cur_idx]
    except Exception:
        return None

    # mirror regenerate() behavior: skip trailing reinforcement messages
    while isinstance(message, ReinforcementMessage):
        try:
            cur_idx -= 1
            message = scene.history[cur_idx]
        except Exception:
            return None

    return message


def ensure_regenerate_allowed(scene: "Scene") -> tuple[bool, str | None]:
    """
    Returns (allowed, error_message).

    We currently block regeneration if the regen target is a CharacterMessage whose
    character is inactive (or has no active actor). This keeps regeneration behavior
    consistent and avoids hard failures in the conversation agent.
    """
    message = regenerate_target_message(scene)
    if not message or not isinstance(message, CharacterMessage):
        return True, None

    character = scene.get_character(message.character_name)
    if not character:
        return False, "Cannot regenerate: character not found."

    if character.name not in scene.active_characters:
        return (
            False,
            f"Cannot regenerate: character '{character.name}' is inactive. Activate the character first.",
        )

    if not getattr(character, "actor", None):
        return (
            False,
            f"Cannot regenerate: character '{character.name}' has no active actor.",
        )

    return True, None


async def _converse_for_character_message(
    message: CharacterMessage, scene: "Scene"
) -> tuple["Character", list[CharacterMessage]] | None:
    """
    Resolve the character that owns ``message`` and ask the conversation
    agent to produce replacement message(s). Returns ``(character,
    messages)`` or ``None`` when the message can't be regenerated (no
    character, or a static user line). Side-effect free — does NOT push
    to history or emit.
    """
    character: "Character | None" = scene.get_character(message.character_name)
    if not character:
        log.error(
            "regenerate: Could not find character for message", message=message
        )
        return None

    if message.source == "player" and not message.from_choice:
        log.warning(
            "regenerate: Static user message, no regeneration possible",
            message=message,
        )
        return None

    agent = get_agent("conversation")
    messages = await agent.converse(character.actor, instruction=message.from_choice)
    return character, messages


async def regenerate_character_message(
    message: CharacterMessage, scene: "Scene"
) -> list[CharacterMessage] | None:
    result = await _converse_for_character_message(message, scene)
    if result is None:
        return None
    character, messages = result

    for message in messages:
        await scene.push_history(message)
        emit("character", message=message, character=character)

    return messages


async def _dispatch_agent_regeneration(
    message: SceneMessage, scene: "Scene"
) -> list[SceneMessage] | None:
    """
    Build replacement message(s) by calling the agent function recorded on
    ``message.meta``. Does NOT push to history or emit — caller decides
    what to do with the result.

    For ``CharacterMessage`` the conversation agent's ``converse`` returns
    one or more ``CharacterMessage`` objects directly.
    For other supported types the meta-driven function may return either a
    new ``SceneMessage`` or a raw string (wrapped into ``message.__class__``).

    Returns the list of new SceneMessage objects (without character/narrator
    mutations tagged — that's caller's job via push_history or the editor's
    ``consume_pending_revision_original`` drain).
    """
    if isinstance(message, CharacterMessage):
        result = await _converse_for_character_message(message, scene)
        if result is None:
            return None
        _character, messages = result
        return messages

    try:
        agent = get_agent(message.meta.get("agent"))
    except Exception as e:
        log.error(
            "_dispatch_agent_regeneration: Could not find agent",
            message=message,
            error=e,
        )
        return None

    if not agent:
        log.error("_dispatch_agent_regeneration: Could not find agent", message=message)
        return None

    function_name = message.meta.get("function")
    fn = getattr(agent, function_name, None)

    if not fn:
        log.error(
            "_dispatch_agent_regeneration: Could not find agent function",
            message=message,
        )
        return None

    arguments = message.meta.get("arguments", {}).copy()

    # if `character` is set and a string, convert it to a Character
    if arguments.get("character") and isinstance(arguments.get("character"), str):
        arguments["character"] = scene.get_character(arguments.get("character"))

    log.debug(
        "_dispatch_agent_regeneration: Calling agent function",
        function=function_name,
        arguments=arguments,
    )

    new_message = await fn(**arguments)

    if not new_message:
        log.error(
            "_dispatch_agent_regeneration: No new message generated", message=message
        )
        return None

    if isinstance(new_message, str):
        new_message = message.__class__(new_message)
        new_message.meta = message.meta.copy()

    if isinstance(message, ContextInvestigationMessage):
        new_message.sub_type = message.sub_type

    return [new_message]


async def regenerate_message(
    message: SceneMessage, scene: "Scene"
) -> list[SceneMessage] | None:
    """
    Regenerate the message via the dispatch table and push the result(s)
    onto scene history. Used by the reinforcement-message re-generation
    pass — the in-place top-level ``regenerate`` flow uses
    ``_regenerate_inplace`` instead.
    """

    if isinstance(message, CharacterMessage):
        # character messages need specific handling — including its own
        # push + emit loop
        messages = await regenerate_character_message(message, scene)
        if messages is None:
            return None
    else:
        new_messages = await _dispatch_agent_regeneration(message, scene)
        if not new_messages:
            return None

        messages = new_messages
        for new_message in messages:
            if not isinstance(new_message, (ReinforcementMessage,)):
                await scene.push_history(new_message)
                emit(new_message.typ, message=new_message)

    for message in messages:
        await async_signals.get(f"regenerate.msg.{message.typ}").send(
            events.RegenerateGeneration(
                scene=scene,
                message=message,
                character=scene.get_character(message.character_name)
                if isinstance(message, CharacterMessage)
                else None,
                event_type=f"regenerate.msg.{message.typ}",
            )
        )

    return messages


async def _regenerate_inplace(
    message: SceneMessage, scene: "Scene"
) -> tuple[str, list[str]] | None:
    """
    Run the agent path for ``message`` and return the new canonical text
    plus any auto-revision intermediate(s) drained from the editor's
    pre-revision ContextVar.

    This does NOT push the new message to history nor emit any
    ``character``/``narrator`` message — the caller is expected to update
    the existing SceneMessage in place via ``scene.edit_message`` so the
    slot keeps its id and revision stack on the frontend.

    Returns ``(new_text, mutations)`` or ``None`` on failure / empty
    result. ``mutations`` is the editor's pre-revision text(s) only; the
    pre-regenerate canonical text is NOT included here (it's already in
    the user's revision stack at the active index).
    """
    new_messages = await _dispatch_agent_regeneration(message, scene)
    if not new_messages:
        return None

    # Use the first generated message as the new canonical. Multi-message
    # results (rare; conversation can split) collapse to the first.
    new_message = new_messages[0]
    new_text = new_message.message
    if not new_text:
        return None

    # Drain the editor's pre-revision ContextVar — auto-revision sets it
    # in ``revision_on_generation`` when it actually rewrote the text.
    # Normally ``revision_tag_on_push`` would drain it during push, but
    # in-place regenerate skips push entirely.
    mutations: list[str] = []
    try:
        editor = get_agent("editor")
    except Exception:
        editor = None
    if editor is not None and hasattr(editor, "consume_pending_revision_original"):
        original = editor.consume_pending_revision_original()
        # Skip when the auto-revision intermediate matches either the new
        # canonical (no-op edit) or the message's *prior* canonical text —
        # the prior version is already in the frontend stack at the active
        # index, so re-adding it would just duplicate the user's current
        # entry.
        if (
            original
            and original != new_text
            and original != message.message
        ):
            mutations.append(original)

    # Fire the regenerate.msg.* signals for downstream consumers (e.g.
    # avatar/asset hooks) — matches the previous push-emit flow.
    for m in new_messages:
        await async_signals.get(f"regenerate.msg.{m.typ}").send(
            events.RegenerateGeneration(
                scene=scene,
                message=m,
                character=scene.get_character(m.character_name)
                if isinstance(m, CharacterMessage)
                else None,
                event_type=f"regenerate.msg.{m.typ}",
            )
        )

    return new_text, mutations


async def regenerate(scene: "Scene") -> list[SceneMessage]:
    """
    In-place regenerate the most recent AI response (the tail of
    ``scene.history``, skipping trailing reinforcement messages): keep
    the original SceneMessage id, update its text, append any
    auto-revision intermediates as mutations so the frontend can splice
    them onto the existing revision stack.
    """
    try:
        message = scene.history[-1]
    except IndexError:
        return

    regenerated_messages: list[SceneMessage] = []

    # while message type is ReinforcementMessage, keep going back in history
    # until we find a message that is not a ReinforcementMessage
    #
    # we need to pop the ReinforcementMessage from the history because
    # previous messages may have contributed to the answer that the AI gave
    # for the reinforcement message
    popped_reinforcement_messages: list[ReinforcementMessage] = []

    while isinstance(message, (ReinforcementMessage,)):
        popped_reinforcement_messages.append(scene.history.pop())
        message = scene.history[-1]

    log.debug(f"Regenerating message: {message} [{message.id}]")

    if message.source == "player" and not message.from_choice:
        log.warning("Cannot regenerate player's message", message=message)
        # re-add the reinforcement messages
        for popped in reversed(popped_reinforcement_messages):
            await scene.push_history(popped)
        return regenerated_messages

    current_regeneration_context = regeneration_context.get()
    if current_regeneration_context:
        current_regeneration_context.message = message.message

    if not isinstance(
        message, (CharacterMessage, NarratorMessage, ContextInvestigationMessage)
    ):
        log.warning("Cannot regenerate message", message=message)
        return regenerated_messages

    # Pop the target from history before invoking the agent — otherwise
    # the LLM sees its own prior output in context and just rephrases it.
    # After the reinforcement-message pop loop above, the target is at the
    # tail, so a plain pop suffices. We re-append it afterward so the
    # message id (and the frontend's revision stack tied to it) is
    # preserved.
    scene.history.pop()

    try:
        outcome = await _regenerate_inplace(message, scene)
    except Exception as e:
        log.error("regenerate: Exception during regeneration", message=message, error=e)
        outcome = None

    if not outcome:
        log.error("No new message generated", message=message)
        # Put the message back where it was; nothing changed.
        scene.history.append(message)
        emit(
            "regenerate_failed",
            "",
            websocket_passthrough=True,
            kwargs={"id": message.id},
        )
        emit(
            "status",
            message="Could not regenerate message.",
            status="error",
        )
        for reinforcement_message in reversed(popped_reinforcement_messages):
            await scene.push_history(reinforcement_message)
        return regenerated_messages

    new_text, mutation_delta = outcome

    # Re-append with the same id, then commit the canonical swap via
    # edit_message — which finds the message by id, sets the new text,
    # and fires the message_edited wire event with reason="regenerate"
    # plus any auto-revision intermediates.
    scene.history.append(message)
    scene.edit_message(
        message.id,
        new_text,
        reason="regenerate",
        mutations=mutation_delta,
    )

    regenerated_messages.append(message)

    for reinforcement_message in popped_reinforcement_messages:
        new_messages = await regenerate_message(reinforcement_message, scene)
        if new_messages:
            regenerated_messages.extend(new_messages)

    return regenerated_messages
