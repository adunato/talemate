from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import isodate
import structlog

import talemate.emit.async_signals
import talemate.util as util
from talemate.emit import emit
from talemate.events import GameLoopEvent, GameLoopActorIterEvent
from talemate.instance import get_agent
from talemate.client import ClientBase
from talemate.prompts import Prompt
from talemate.prompts.response import AnchorExtractor, ResponseSpec
from talemate.scene_message import (
    ReinforcementMessage,
    TimePassageMessage,
)

# Message types skipped while `request_world_state` walks scene.history
# collecting focus lines. Every focus message ends up in `anchor_message_ids`
# and can render inline highlights — so anything kept in this list is excluded
# from the focus span and gets no highlights. `context_investigation` is the
# soft case: the `include_context_investigation` config knob removes it from
# the per-call ignore set when enabled.
WORLD_STATE_SNAPSHOT_IGNORE = [
    "reinforcement",
    "director",
    "context_investigation",
]
from talemate.util.response import extract_list
from talemate.world_state import WorldStateResponse


from talemate.agents.base import (
    Agent,
    AgentAction,
    AgentActionConfig,
    AgentActionConditional,
    AgentEmission,
    DynamicInstruction,
    optimize_prompt_caching_action,
    set_processing,
)
from talemate.agents.registry import register
from talemate.agents.memory.rag import MemoryRAGMixin


from .character_progression import CharacterProgressionMixin
from .avatars import AvatarMixin
from .websocket_handler import WorldStateWebsocketHandler
import talemate.agents.world_state.nodes

if TYPE_CHECKING:
    from talemate.tale_mate import Character

log = structlog.get_logger("talemate.agents.world_state")

talemate.emit.async_signals.register("agent.world_state.time")


class WorldStateAgentEmission(AgentEmission):
    """
    Emission class for world state agent
    """

    pass


class TimePassageEmission(WorldStateAgentEmission):
    """
    Emission class for time passage
    """

    duration: str
    narrative: str | None = None
    human_duration: str | None = None


@register()
class WorldStateAgent(MemoryRAGMixin, CharacterProgressionMixin, AvatarMixin, Agent):
    """
    An agent that handles world state related tasks.
    """

    agent_type = "world_state"
    verbose_name = "World State"
    websocket_handler = WorldStateWebsocketHandler

    @classmethod
    def init_actions(cls) -> dict[str, AgentAction]:
        actions = {
            "prompt_caching": optimize_prompt_caching_action(),
            "update_world_state": AgentAction(
                enabled=True,
                can_be_disabled=True,
                container=True,
                icon="mdi-earth",
                label="Update world state",
                quick_toggle=True,
                description="Will attempt to update the world state based on the current scene. Runs automatically every N character turns.",
                config={
                    "initial": AgentActionConfig(
                        type="bool",
                        label="When a new scene is started",
                        description="Whether to update the world state on scene start.",
                        value=True,
                    ),
                    "turns": AgentActionConfig(
                        type="number",
                        label="Turns",
                        description="Number of character turns (player or AI) to wait before updating the world state.",
                        value=10,
                        min=1,
                        max=100,
                        step=1,
                    ),
                    "focus_lines": AgentActionConfig(
                        type="number",
                        label="Lines in the moment",
                        description="How many of the most recent messages count as the current moment. These messages can show inline entity highlights.",
                        value=3,
                        min=1,
                        max=10,
                        step=1,
                    ),
                    "include_context_investigation": AgentActionConfig(
                        type="bool",
                        label="Include Look at / Investigate",
                        description="When on, Look at and Investigate messages also count as part of the current moment and can show inline highlights.",
                        value=False,
                    ),
                    "examine_length": AgentActionConfig(
                        type="number",
                        label="Investigate length",
                        description="Token length for the Investigate result when taking a closer look at a highlighted entity. Shorter lengths also tell the model to keep the description briefer.",
                        value=256,
                        min=32,
                        max=1024,
                        step=32,
                    ),
                    "inject_as_scene_memory": AgentActionConfig(
                        type="bool",
                        label="Pin to context",
                        description="Pin the world state snapshot into the conversation and narrator prompts as live scene memory.",
                        value=False,
                    ),
                    "durable_snapshot": AgentActionConfig(
                        type="bool",
                        label="Durable snapshot",
                        quick_toggle=True,
                        description="Keep entities and their details across refreshes so the world state builds up over time. When off, every refresh starts from scratch.",
                        value=True,
                    ),
                    "eviction_threshold": AgentActionConfig(
                        type="number",
                        label="Auto-evict stale entries",
                        description="Automatically drop a snapshot entry the agent leaves unchanged for this many consecutive world state updates, guarding against stale entries the agent fails to remove on its own. Set to 0 to disable.",
                        value=3,
                        min=0,
                        max=20,
                        step=1,
                        condition=AgentActionConditional(
                            attribute="update_world_state.config.durable_snapshot",
                            value=True,
                        ),
                    ),
                },
            ),
            "update_reinforcements": AgentAction(
                enabled=True,
                can_be_disabled=True,
                container=True,
                icon="mdi-image-auto-adjust",
                label="Update state reinforcements",
                description="Will attempt to update any due state reinforcements.",
                config={},
            ),
            "check_pin_conditions": AgentAction(
                enabled=True,
                can_be_disabled=True,
                container=True,
                icon="mdi-pin",
                label="Update conditional context pins",
                description="Will evaluate context pins conditions and toggle those pins accordingly. Runs automatically every N turns.",
                config={
                    "turns": AgentActionConfig(
                        type="number",
                        label="Turns",
                        description="Number of turns to wait before checking conditions.",
                        value=2,
                        min=1,
                        max=100,
                        step=1,
                    )
                },
            ),
        }
        MemoryRAGMixin.add_actions(actions)
        CharacterProgressionMixin.add_actions(actions)
        AvatarMixin.add_actions(actions)
        return actions

    def __init__(self, client: ClientBase | None = None, **kwargs):
        self.client = client
        self.is_enabled = True
        self.next_update = 0
        self.next_pin_check = 0
        self.actions = WorldStateAgent.init_actions()

    @property
    def enabled(self):
        return self.is_enabled

    @property
    def has_toggle(self):
        return True

    @property
    def experimental(self):
        return True

    @property
    def initial_update(self):
        return self.resolve_config("update_world_state", "initial")

    @property
    def update_world_state_enabled(self) -> bool:
        return self.resolve_enabled("update_world_state")

    @property
    def update_world_state_turns(self) -> int:
        return self.resolve_config("update_world_state", "turns")

    @property
    def update_world_state_focus_lines(self) -> int:
        return self.resolve_config("update_world_state", "focus_lines")

    @property
    def update_world_state_include_context_investigation(self) -> bool:
        return self.resolve_config(
            "update_world_state", "include_context_investigation"
        )

    @property
    def update_world_state_examine_length(self) -> int:
        return self.resolve_config("update_world_state", "examine_length")

    @property
    def update_world_state_durable_snapshot(self) -> bool:
        return self.resolve_config("update_world_state", "durable_snapshot")

    @property
    def update_world_state_eviction_threshold(self) -> int:
        return self.resolve_config("update_world_state", "eviction_threshold")

    @property
    def update_reinforcements_enabled(self) -> bool:
        return self.resolve_enabled("update_reinforcements")

    @property
    def check_pin_conditions_enabled(self) -> bool:
        return self.resolve_enabled("check_pin_conditions")

    @property
    def check_pin_conditions_turns(self):
        return self.resolve_config("check_pin_conditions", "turns")

    def connect(self, scene):
        super().connect(scene)
        talemate.emit.async_signals.get("game_loop").connect(self.on_game_loop)
        talemate.emit.async_signals.get("game_loop_actor_iter").connect(
            self.on_game_loop_actor_iter
        )
        talemate.emit.async_signals.get("scene_loop_init_after").connect(
            self.on_scene_loop_init_after
        )

    async def advance_time(
        self, duration: str, narrative: str = None
    ) -> TimePassageMessage:
        """
        Emit a time passage message
        """

        isodate.parse_duration(duration)
        human_duration = util.iso8601_duration_to_human(duration, suffix=" later")
        message = TimePassageMessage(ts=duration, message=human_duration)

        log.debug("world_state.advance_time", message=message)
        await self.scene.push_history(message)
        self.scene.emit_status()

        emit("time", message)

        await talemate.emit.async_signals.get("agent.world_state.time").send(
            TimePassageEmission(
                agent=self,
                duration=duration,
                narrative=narrative,
                human_duration=human_duration,
            )
        )

        return message

    async def on_scene_loop_init_after(self, emission):
        """
        Called when a scene is initialized
        """
        if not self.enabled:
            return

        if not self.initial_update:
            return

        if self.get_scene_state("inital_update_done"):
            return

        await self.scene.world_state.request_update()
        self.set_scene_states(inital_update_done=True)

    async def on_game_loop(self, emission: GameLoopEvent):
        """
        Called once per scene-loop round.
        """

        if not self.enabled:
            return

        await self.auto_update_reinforcments()
        await self.auto_check_pin_conditions()

    async def on_game_loop_actor_iter(self, emission: GameLoopActorIterEvent):
        """
        Called after each actor (player or AI) has had a turn.
        """

        if not self.enabled:
            return

        await self.update_world_state()

    async def auto_update_reinforcments(self):
        if not self.enabled:
            return

        if not self.update_reinforcements_enabled:
            return

        await self.update_reinforcements()

    async def auto_check_pin_conditions(self):
        if not self.enabled:
            return

        if not self.check_pin_conditions_enabled:
            return

        if (
            self.next_pin_check % self.check_pin_conditions_turns != 0
            or self.next_pin_check == 0
        ):
            self.next_pin_check += 1
            return

        self.next_pin_check = 0

        await self.check_pin_conditions()

    async def update_world_state(self, force: bool = False):
        if not self.enabled:
            return

        if not self.update_world_state_enabled:
            return

        log.debug(
            "update_world_state",
            next_update=self.next_update,
            turns=self.update_world_state_turns,
        )

        scene = self.scene

        if (
            self.next_update % self.update_world_state_turns != 0
            or self.next_update == 0
        ) and not force:
            self.next_update += 1
            return

        self.next_update = 0
        await scene.world_state.request_update()

    @set_processing
    async def request_world_state(self) -> WorldStateResponse:
        # Walk scene.history backward collecting up to `focus_lines` narrative
        # messages. TimePassageMessage is a hard boundary — the moment on the
        # other side is a different scene, not what we're highlighting. Other
        # ignored types (reinforcement, director, ...) are skipped without
        # ending the walk. `context_investigation` is conditionally ignored
        # per the agent config: when off it stays in the ignore set; when on
        # it becomes a valid focus candidate that can also receive highlights.
        focus_lines = self.update_world_state_focus_lines
        ignore_types = set(WORLD_STATE_SNAPSHOT_IGNORE)
        if self.update_world_state_include_context_investigation:
            ignore_types.discard("context_investigation")

        focus_messages: list = []
        for message in reversed(self.scene.history):
            if isinstance(message, TimePassageMessage):
                break
            if message.typ in ignore_types:
                continue
            focus_messages.insert(0, message)
            if len(focus_messages) >= focus_lines:
                break

        # Fall back to the scene intro only on a fresh scene (no live history).
        # If the walk turned up empty because of a TimePassageMessage boundary
        # or director-only noise, the scene has already moved past the intro —
        # using it as the "current moment" would be wrong.
        intro_text = (
            self.scene.get_intro()
            if not focus_messages and not self.scene.history
            else ""
        )

        # Nothing to anchor highlights to AND no intro to fall back on. Silently
        # return an empty response so the world_state holder re-emits current
        # state and no LLM call is made.
        if not focus_messages and not intro_text:
            return WorldStateResponse()

        # Every focus message is a valid highlight anchor — the frontend
        # applies the parser's mention pass to all of them, so phrases that
        # only appear in an earlier focus line still get highlighted there.
        anchor_message_ids = [m.id for m in focus_messages]

        t1 = time.time()

        durable_snapshot = self.update_world_state_durable_snapshot

        # Pre-build a rendered current-state payload for the prompt's CURRENT
        # STATE section. Only shown in durable mode (legacy mode's prompt
        # framing is fresh-extract, so prior state would just be noise).
        # Mentions are excluded because they're focus-window-specific —
        # forcing the LLM to re-derive them keeps the highlight phrasing
        # current.
        current_state_payload = {}
        if durable_snapshot:
            ws = self.scene.world_state
            current_state_payload = {
                "characters": {
                    name: char.model_dump(exclude={"mentions", "misses"})
                    for name, char in ws.characters.items()
                },
                "items": {
                    name: obj.model_dump(exclude={"mentions", "misses"})
                    for name, obj in ws.items.items()
                },
                "places": {
                    name: place.model_dump(exclude={"mentions", "misses"})
                    for name, place in ws.places.items()
                },
                "location": ws.location,
            }

        _, world_state = await Prompt.request(
            "world_state.request-world-state-v2",
            self.client,
            "analyze_long",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "object_type": "character",
                "object_type_plural": "characters",
                "scene_focus": focus_messages,
                "intro_text": intro_text,
                "include_scene_intent": True,
                "include_scenario_premise": True,
                "durable_snapshot": durable_snapshot,
                "current_state_payload": current_state_payload,
            },
        )

        self.scene.log.debug(
            "request_world_state",
            response=world_state,
            anchor_message_ids=anchor_message_ids,
            time=time.time() - t1,
        )

        return WorldStateResponse(
            world_state=world_state, anchor_message_ids=anchor_message_ids
        )

    @set_processing
    async def examine_entity(
        self,
        entity_name: str,
        entity_kind: str,
        snapshot_text: str,
    ) -> str:
        """
        Synthesize an in-fiction "examine" result for a tooltip-highlighted
        entity. Takes the 2-3 sentence snapshot text and expands it into
        grounded prose describing what the player observes about the entity
        at this moment in the scene. Output length is governed by the
        `examine_length` config, which both caps the tokens and scales the
        auto-appended response-length instruction.

        Returns the synthesized text. Caller is responsible for surfacing it
        in the UI — typically as a ContextInvestigationMessage anchored to
        the moment of the examine click. Prior examine results land back in
        scene history, so repeated examines for the same or related entities
        accumulate context naturally via `scene.context_history()`.
        """
        if not snapshot_text or not snapshot_text.strip():
            raise ValueError("examine_entity requires non-empty snapshot_text")

        response_length = self.update_world_state_examine_length

        _, extracted = await Prompt.request(
            "world_state.examine-entity",
            self.client,
            f"create_{response_length}",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "entity_name": entity_name,
                "entity_kind": entity_kind,
                "snapshot_text": snapshot_text,
                "include_scene_intent": True,
                "include_scenario_premise": True,
            },
            response_spec=ResponseSpec(
                extractors={
                    "response": AnchorExtractor(
                        left="<EXAMINE>",
                        right="</EXAMINE>",
                        fallback_to_full=True,
                    ),
                },
            ),
        )

        return extracted["response"].strip()

    @set_processing
    async def analyze_text_and_extract_context(
        self,
        text: str,
        goal: str,
        include_character_context: bool = False,
        response_length=1024,
        num_queries=1,
        extra_context: list[str] = [],
    ):
        response, extracted = await Prompt.request(
            "world_state.analyze-text-and-extract-context",
            self.client,
            f"investigate_{response_length}",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "text": text,
                "goal": goal,
                "include_character_context": include_character_context,
                "response_length": response_length,
                "num_queries": num_queries,
                "extra_context": extra_context,
            },
        )

        log.debug(
            "analyze_text_and_extract_context", goal=goal, text=text, response=response
        )

        return extracted["response"]

    @set_processing
    async def analyze_text_and_extract_context_via_queries(
        self,
        text: str,
        goal: str,
        include_character_context: bool = False,
        response_length=1024,
        num_queries=1,
        extra_context: list[str] = [],
    ) -> list[str]:
        response, extracted = await Prompt.request(
            "world_state.analyze-text-and-generate-rag-queries",
            self.client,
            f"investigate_{response_length}",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "text": text,
                "goal": goal,
                "include_character_context": include_character_context,
                "response_length": response_length,
                "num_queries": num_queries,
                "extra_context": extra_context,
            },
        )

        queries = extract_list(extracted["response"])

        memory_agent = get_agent("memory")

        context = await memory_agent.multi_query(queries, iterate=3)

        # log.debug(
        #    "analyze_text_and_extract_context_via_queries",
        #    goal=goal,
        #    text=text,
        #    queries=queries,
        #    context=context,
        # )

        return context

    @set_processing
    async def analyze_and_follow_instruction(
        self,
        text: str,
        instruction: str,
        short: bool = False,
    ):
        kind = "analyze_freeform_short" if short else "analyze_freeform"

        response, extracted = await Prompt.request(
            "world_state.analyze-text-and-follow-instruction",
            self.client,
            kind,
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "text": text,
                "instruction": instruction,
            },
        )

        log.debug(
            "analyze_and_follow_instruction",
            instruction=instruction,
            text=text,
            response=response,
        )

        return extracted["response"]

    @set_processing
    async def analyze_text_and_answer_question(
        self,
        text: str,
        query: str,
        response_length: int = 512,
    ):
        kind = f"investigate_{response_length}"
        response, extracted = await Prompt.request(
            "world_state.analyze-text-and-answer-question",
            self.client,
            kind,
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "text": text,
                "query": query,
            },
        )

        log.debug(
            "analyze_text_and_answer_question",
            query=query,
            text=text,
            response=response,
        )

        return extracted["response"]

    @set_processing
    async def analyze_history_and_follow_instructions(
        self,
        entries: list[dict],
        instructions: str,
        analysis: str = "",
        response_length: int = 512,
    ) -> str:
        """
        Takes a list of archived_history or layered_history entries
        and follows the instructions to generate a response.
        """

        response, extracted = await Prompt.request(
            "world_state.analyze-history-and-follow-instructions",
            self.client,
            f"investigate_{response_length}",
            vars={
                "instructions": instructions,
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "entries": entries,
                "analysis": analysis,
                "response_length": response_length,
            },
        )

        return extracted["response"].strip()

    @set_processing
    async def answer_query_true_or_false(
        self,
        query: str,
        text: str,
    ) -> bool:
        query = f"{query} Answer with a yes or no."
        response = await self.analyze_text_and_answer_question(
            query=query, text=text, response_length=10
        )
        return response.lower().startswith("y")

    @set_processing
    async def identify_characters(
        self,
        text: str = None,
    ):
        """
        Attempts to identify characters in the given text.
        """

        _, data = await Prompt.request(
            "world_state.identify-characters",
            self.client,
            "analyze",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "text": text,
            },
        )

        log.debug("identify_characters", text=text, data=data)

        return data

    def _parse_character_sheet(
        self, response, max_attributes: int | None = None
    ) -> dict[str, str]:
        data = {}
        for line in response.split("\n"):
            if not line.strip():
                continue
            if ":" not in line:
                break
            name, value = line.split(":", 1)
            data[name.strip()] = value.strip()

            # Enforce max_attributes limit if set
            if max_attributes and max_attributes > 0 and len(data) >= max_attributes:
                break

        return data

    @set_processing
    async def extract_character_sheet(
        self,
        name: str,
        text: str = None,
        alteration_instructions: str = None,
        augmentation_instructions: str = None,
        dynamic_instructions: list[DynamicInstruction] = [],
        max_attributes: int | None = None,
    ) -> dict[str, str]:
        """
        Attempts to extract a character sheet from the given text.
        """

        response, extracted = await Prompt.request(
            "world_state.extract-character-sheet",
            self.client,
            "create",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "text": text,
                "name": name,
                "character": self.scene.get_character(name),
                "alteration_instructions": alteration_instructions or "",
                "augmentation_instructions": augmentation_instructions or "",
                "dynamic_instructions": dynamic_instructions,
                "max_attributes": max_attributes,
            },
        )

        # loop through each line in response and if it contains a : then extract
        # the left side as an attribute name and the right side as the value
        #
        # break as soon as a non-empty line is found that doesn't contain a :

        return self._parse_character_sheet(
            extracted["response"], max_attributes=max_attributes
        )

    @set_processing
    async def update_reinforcements(self, force: bool = False, reset: bool = False):
        """
        Queries due worldstate re-inforcements
        """

        for reinforcement in self.scene.world_state.reinforce:
            # Skip character reinforcements if require_active is True and character is not active
            if (
                reinforcement.require_active
                and reinforcement.character
                and not self.scene.character_is_active(reinforcement.character)
            ):
                continue

            if reinforcement.due <= 0 or force:
                await self.update_reinforcement(
                    reinforcement.question, reinforcement.character, reset=reset
                )
            else:
                reinforcement.due -= 1

    @set_processing
    async def update_reinforcement(
        self, question: str, character: "str | Character" = None, reset: bool = False
    ) -> str:
        """
        Queries a single re-inforcement
        """

        if isinstance(character, self.scene.Character):
            character = character.name

        message = None
        idx, reinforcement = await self.scene.world_state.find_reinforcement(
            question, character
        )

        if not reinforcement:
            log.warning(
                "Reinforcement not found", question=question, character=character
            )
            return

        message = ReinforcementMessage(message="")
        message.set_source(
            "world_state",
            "update_reinforcement",
            question=question,
            character=character,
        )

        if reset and reinforcement.insert == "sequential":
            self.scene.pop_history(
                typ="reinforcement", meta_hash=message.meta_hash, all=True
            )

        if reinforcement.insert == "sequential":
            kind = "analyze_freeform_medium_short"
        else:
            kind = "analyze_freeform"

        response, extracted = await Prompt.request(
            "world_state.update-reinforcements",
            self.client,
            kind,
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "question": reinforcement.question,
                "instructions": reinforcement.instructions or "",
                "character": (
                    self.scene.get_character(reinforcement.character)
                    if reinforcement.character
                    else None
                ),
                "answer": (reinforcement.answer if not reset else None) or "",
                "reinforcement": reinforcement,
            },
            response_spec=ResponseSpec(
                extractors={
                    "response": AnchorExtractor(
                        left="<ANSWER>",
                        right="</ANSWER>",
                        fallback_to_full=True,
                    ),
                },
            ),
        )

        answer = extracted["response"]

        reinforcement.answer = answer
        reinforcement.due = reinforcement.interval

        # remove any recent previous reinforcement message with same question
        # to avoid overloading the near history with reinforcement messages
        if not reset:
            self.scene.pop_history(
                typ="reinforcement", meta_hash=message.meta_hash, max_iterations=10
            )

        if reinforcement.insert == "sequential":
            # insert the reinforcement message at the current position
            message.message = answer
            log.debug("update_reinforcement", message=message, reset=reset)
            await self.scene.push_history(message)

        # if reinforcement has a character name set, update the character detail
        if reinforcement.character:
            character = self.scene.get_character(reinforcement.character)
            await character.set_detail(reinforcement.question, answer)

        else:
            # set world entry
            await self.scene.world_state_manager.save_world_entry(
                reinforcement.question,
                reinforcement.as_context_line,
                {},
            )

        self.scene.world_state.emit()

        return message

    @set_processing
    async def check_pin_conditions(
        self,
    ):
        """
        Checks if any context pin conditions
        """

        log.debug("check_pin_conditions", turns=self.check_pin_conditions_turns)

        world_state = self.scene.world_state

        state_change = False

        # Build list of pins to check, honoring decay semantics
        pins_to_check = {}
        for entry_id, pin in world_state.pins.items():
            # Skip game-state-controlled pins from the LLM loop
            if pin.gamestate_condition:
                continue
            # Initialize countdown if active with decay but no due set
            if pin.active and pin.decay and not pin.decay_due:
                pin.decay_due = pin.decay

            # Only pins with conditions are checked by the LLM
            if not pin.condition:
                continue

            # If pin is active and has decay, skip checks until it's about to decay (decay_due == 1)
            if (
                pin.active
                and pin.decay
                and (pin.decay_due is not None)
                and pin.decay_due > 1
            ):
                continue

            # Include pin for checking when it has no decay, is inactive, or is about to decay
            if (not pin.decay) or (not pin.active) or (pin.decay_due == 1):
                pins_to_check[entry_id] = {
                    "condition": pin.condition,
                    "state": pin.condition_state,
                }

        # Early return if nothing to check, but still tick decay
        if not pins_to_check:
            for entry_id, pin in world_state.pins.items():
                # Game-state-controlled pins do not decay
                if pin.gamestate_condition:
                    continue
                if pin.active and pin.decay:
                    if not pin.decay_due:
                        pin.decay_due = pin.decay
                    pin.decay_due -= self.check_pin_conditions_turns
                    log.debug("applying pin decay", pin=pin, decay_due=pin.decay_due)
                    if pin.decay_due <= 0:
                        log.debug("pin decay expired", pin=pin, decay_due=pin.decay_due)
                        pin.active = False
                        pin.decay_due = None
                        state_change = True
            if state_change:
                await self.scene.load_active_pins()
                self.scene.emit_status()
            return

        first_entry_id = list(pins_to_check.keys())[0]

        _, answers = await Prompt.request(
            "world_state.check-pin-conditions",
            self.client,
            "analyze",
            vars={
                "scene": self.scene,
                "max_tokens": self.client.max_token_length,
                "previous_states": json.dumps(pins_to_check, indent=2),
                "coercion": {first_entry_id: {"condition": ""}},
            },
        )

        # Apply LLM results
        for entry_id, answer in answers.items():
            if entry_id not in world_state.pins:
                log.debug(
                    "check_pin_conditions",
                    entry_id=entry_id,
                    answer=answer,
                    msg="entry_id not found in world_state.pins (LLM failed to produce a clean response)",
                )
                continue

            log.debug("check_pin_conditions", entry_id=entry_id, answer=answer)
            state = answer.get("state")
            pin = world_state.pins[entry_id]
            if state is True or (
                isinstance(state, str) and state.lower() in ["true", "yes", "y"]
            ):
                prev_state = pin.condition_state
                pin.condition_state = True
                if not pin.active:
                    state_change = True
                pin.active = True
                # Refresh decay countdown when condition is true and pin stays/turns active
                if pin.decay:
                    pin.decay_due = pin.decay
                if prev_state != pin.condition_state:
                    state_change = True
            else:
                if pin.condition_state is not False or pin.active:
                    pin.condition_state = False
                    pin.active = False
                    # Clear countdown when deactivated
                    pin.decay_due = None
                    state_change = True

        # Tick decay counters for all active pins with decay
        for entry_id, pin in world_state.pins.items():
            # Game-state-controlled pins do not decay
            if pin.gamestate_condition:
                continue
            if pin.active and pin.decay:
                if not pin.decay_due:
                    pin.decay_due = pin.decay
                # Decrement once per check cycle
                pin.decay_due -= 1
                if pin.decay_due <= 0:
                    # Auto-deactivate on expiry
                    pin.active = False
                    pin.decay_due = None
                    state_change = True

        if state_change:
            await self.scene.load_active_pins()
            self.scene.emit_status()

    @set_processing
    async def summarize_and_pin(self, message_id: int, num_messages: int = 3) -> str:
        """
        Will take a message index and then walk back N messages
        summarizing the scene and pinning it to the context.
        """

        creator = get_agent("creator")
        summarizer = get_agent("summarizer")

        message_index = self.scene.message_index(message_id)

        text = self.scene.snapshot(lines=num_messages, start=message_index)

        extra_context = self.scene.snapshot(
            lines=50, start=message_index - num_messages
        )

        summary = await summarizer.summarize(
            text,
            extra_context=[extra_context],
            method="short",
            extra_instructions="Pay particularly close attention to decisions, agreements or promises made.",
        )

        entry_id = util.clean_id(await creator.generate_title(summary))

        ts = self.scene.ts

        log.debug(
            "summarize_and_pin",
            message_id=message_id,
            message_index=message_index,
            num_messages=num_messages,
            summary=summary,
            entry_id=entry_id,
            ts=ts,
        )

        await self.scene.world_state_manager.save_world_entry(
            entry_id,
            summary,
            {
                "ts": ts,
                "pin_only": True,
            },
        )

        await self.scene.world_state_manager.set_pin(
            entry_id,
            active=True,
        )

        await self.scene.load_active_pins()
        self.scene.emit_status()

    @set_processing
    async def is_character_present(self, character: str) -> bool:
        """
        Check if a character is present in the scene

        Arguments:

        - `character`: The character to check.
        """

        if len(self.scene.history) < 10:
            text = self.scene.intro + "\n\n" + self.scene.snapshot(lines=50)
        else:
            text = self.scene.snapshot(lines=50)

        is_present = await self.analyze_text_and_answer_question(
            text=text,
            query=f"Is {character} present AND active in the current scene? Answer with 'yes' or 'no'.",
            response_length=10,
        )

        return is_present.lower().startswith("y")

    @set_processing
    async def is_character_leaving(self, character: str) -> bool:
        """
        Check if a character is leaving the scene

        Arguments:

        - `character`: The character to check.
        """

        if len(self.scene.history) < 10:
            text = self.scene.intro + "\n\n" + self.scene.snapshot(lines=50)
        else:
            text = self.scene.snapshot(lines=50)

        is_leaving = await self.analyze_text_and_answer_question(
            text=text,
            query=f"Is {character} leaving the current scene? Answer with 'yes' or 'no'.",
            response_length=10,
        )

        return is_leaving.lower().startswith("y")

    @set_processing
    async def manager(self, action_name: str, *args, **kwargs):
        """
        Executes a world state manager action through self.scene.world_state_manager
        """

        manager = self.scene.world_state_manager

        try:
            fn = getattr(manager, action_name, None)

            if not fn:
                raise ValueError(f"Unknown action: {action_name}")

            return await fn(*args, **kwargs)
        except Exception as e:
            log.error(
                "worldstate.manager",
                action_name=action_name,
                args=args,
                kwargs=kwargs,
                error=e,
            )
            raise
