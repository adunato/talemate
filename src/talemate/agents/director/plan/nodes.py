"""
Plan graph nodes.

- CreatePlan: creates a plan with tasks and saves to agent state
- EstimateWords: estimates word count for a given number of beats
- CompleteTask: marks a task as completed in a plan
- ExpandStoryArc: expands beats into prose via the arc-expand pipeline

Beat execution is handled by the existing `direct_scene` FOCAL action.
Plan state is injected into the chat context when a plan exists.
"""

import structlog
from typing import ClassVar

from talemate.game.engine.nodes.core import (
    Node,
    GraphState,
    PropertyField,
)
from talemate.game.engine.nodes.registry import register
from talemate.game.engine.nodes.agent import AgentNode
from talemate.instance import get_agent
from talemate.context import active_scene
from talemate.agents.director.chat.context import director_chat_context

from .schema import (
    Beat,
    Plan,
    PlanStatus,
    Task,
    READING_SPEED_WPM,
    NARRATION_BEAT_RATIO,
)
from .util import save_plan, complete_task, emit_plan_updated, get_plan
from .expand import expand_beats

log = structlog.get_logger("talemate.agents.director.plan.nodes")

BEAT_FIELDS = {"type", "pacing", "tension", "characters"}


def _validate_task(item: dict, order: int) -> Task:
    """Validate a task dict as Beat or Task based on fields present."""
    item.setdefault("order", order)
    if BEAT_FIELDS & item.keys():
        return Beat.model_validate(item)
    return Task.model_validate(item)


@register("agents/director/plan/CreatePlan")
class CreatePlan(Node):
    """
    Creates a plan with tasks and saves it to agent state.

    Accepts a list of task dicts — automatically validates each as Beat
    (if beat-specific fields are present) or generic Task.
    """

    class Fields:
        instructions = PropertyField(
            name="instructions",
            type="text",
            description="Instructions for the plan",
            default="",
        )
        status = PropertyField(
            name="status",
            type="str",
            description="Initial plan status",
            default="ready",
            choices=[s.value for s in PlanStatus],
        )

    def __init__(self, title="Create Plan", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_input("instructions", socket_type="str")
        self.add_input("tasks", socket_type="list")
        self.add_input("meta", socket_type="dict", optional=True)
        self.add_input("status", socket_type="str", optional=True)

        self.set_property("instructions", "")
        self.set_property("status", "ready")

        self.add_output("state")
        self.add_output("plan_id", socket_type="str")
        self.add_output("result", socket_type="str")

    async def run(self, state: GraphState):
        input_state = self.require_input("state")
        instructions = self.require_input("instructions")
        raw_tasks = self.require_input("tasks")
        meta = self.normalized_input_value("meta") or {}
        status_value = self.normalized_input_value("status") or "ready"

        scene = active_scene.get()

        # Validate tasks
        tasks = []
        if not isinstance(raw_tasks, list):
            log.warning("plan.create.tasks_not_list", type=type(raw_tasks).__name__)
            raw_tasks = []
        for i, item in enumerate(raw_tasks):
            if not isinstance(item, dict):
                log.warning("plan.create.skip_task", index=i, reason="not a dict")
                continue
            try:
                tasks.append(_validate_task(item, order=i + 1))
            except Exception as e:
                log.warning("plan.create.skip_task", index=i, error=str(e))

        plan = Plan(
            instructions=instructions,
            status=PlanStatus(status_value),
            tasks=tasks,
            meta=meta,
        )

        save_plan(scene, plan)

        # Set scene perspective from plan meta if available
        perspective = meta.get("perspective")
        if perspective:
            scene.perspective = perspective
            log.info("plan.set_perspective", perspective=perspective)

        # Link plan to the active director chat via context
        chat_ctx = director_chat_context.get()
        chat_id = None
        if chat_ctx:
            chat_ctx.plan_id = plan.id
            chat_id = chat_ctx.chat_id
            log.info("plan.linked_to_chat", plan_id=plan.id, chat_id=chat_id)

        log.info("plan.created", plan_id=plan.id, task_count=len(tasks))

        emit_plan_updated(plan, chat_id=chat_id)

        self.set_output_values(
            {
                "state": input_state,
                "plan_id": plan.id,
                "result": plan.status_summary(),
            }
        )


@register("agents/director/plan/EstimateWords")
class EstimateWords(Node):
    """
    Estimates total word output for a given number of beats based on
    narrator and conversation agent token settings.
    """

    class Fields:
        beat_count = PropertyField(
            name="beat_count",
            type="int",
            description="Number of beats to estimate for",
            default=8,
        )

    def __init__(self, title="Estimate Words", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_input("beat_count", socket_type="int", optional=True)

        self.set_property("beat_count", 8)

        self.add_output("state")
        self.add_output("beat_count", socket_type="int")
        self.add_output("estimated_words", socket_type="int")
        self.add_output("estimated_reading_minutes", socket_type="float")

    async def run(self, state: GraphState):
        input_state = self.require_input("state")
        beat_count = self.normalized_input_value("beat_count") or 8
        scene = active_scene.get()

        narrator = get_agent("narrator")
        conversation = get_agent("conversation")
        narration_tokens = narrator.action_response_length("progress_story")
        dialogue_tokens = conversation.generation_settings_response_length

        dialogue_characters = len(list(scene.character_names))

        estimated_words = Beat.estimate_words(
            beat_count,
            narration_tokens,
            dialogue_tokens,
            dialogue_characters,
            narration_ratio=NARRATION_BEAT_RATIO,
        )
        estimated_reading = estimated_words / READING_SPEED_WPM

        self.set_output_values(
            {
                "state": input_state,
                "beat_count": beat_count,
                "estimated_words": estimated_words,
                "estimated_reading_minutes": round(estimated_reading, 1),
            }
        )


@register("agents/director/plan/GetActiveChatPlanId")
class GetActiveChatPlanId(Node):
    """
    Returns the plan_id linked to the currently active director chat,
    or None if no chat is active or no plan is linked.
    """

    def __init__(self, title="Get Active Chat Plan ID", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_output("state")
        self.add_output("plan_id", socket_type="str")
        self.add_output("has_plan", socket_type="bool")

    async def run(self, state: GraphState):
        input_state = self.require_input("state")

        chat_ctx = director_chat_context.get()
        plan_id = chat_ctx.plan_id if chat_ctx else None

        self.set_output_values(
            {
                "state": input_state,
                "plan_id": plan_id or "",
                "has_plan": plan_id is not None,
            }
        )


@register("agents/director/plan/GetActivePlan")
class GetActivePlan(Node):
    """
    Returns the plan linked to the currently active director chat.
    Outputs the full plan object, plan ID, perspective, and beat list.
    """

    def __init__(self, title="Get Active Plan", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_output("state")
        self.add_output("plan_id", socket_type="str")
        self.add_output("plan", socket_type="any")
        self.add_output("beats", socket_type="list")
        self.add_output("perspective", socket_type="str")
        self.add_output("has_plan", socket_type="bool")

    async def run(self, state: GraphState):
        input_state = self.require_input("state")

        chat_ctx = director_chat_context.get()
        plan_id = chat_ctx.plan_id if chat_ctx else None
        plan = get_plan(active_scene.get(), plan_id) if plan_id else None

        perspective = ""
        beats = []

        if plan:
            perspective = plan.meta.get("perspective", "")
            beats = [t for t in plan.tasks if isinstance(t, Beat)]

        self.set_output_values({
            "state": input_state,
            "plan_id": plan_id or "",
            "plan": plan,
            "perspective": perspective,
            "beats": beats,
            "has_plan": plan is not None,
        })


@register("agents/director/plan/CompleteTask")
class CompleteTask(AgentNode):
    """
    Marks a task as completed in a plan.
    """

    _agent_name: ClassVar[str] = "director"

    def __init__(self, title="Complete Task", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_input("task_id", socket_type="str")
        self.add_input("plan_id", socket_type="str", optional=True)
        self.add_output("state")
        self.add_output("result", socket_type="str")

    async def run(self, state: GraphState):
        input_state = self.require_input("state")
        task_id = self.require_input("task_id")
        plan_id = self.normalized_input_value("plan_id") or None
        scene = active_scene.get()

        result = complete_task(scene, task_id, plan_id=plan_id)

        # Emit updated plan to frontend
        chat_ctx = director_chat_context.get()
        chat_id = chat_ctx.chat_id if chat_ctx else None
        resolved_plan_id = plan_id or (chat_ctx.plan_id if chat_ctx else None)
        if resolved_plan_id:
            plan = get_plan(scene, resolved_plan_id)
            if plan:
                emit_plan_updated(plan, chat_id=chat_id)

        self.set_output_values({"state": input_state, "result": result})


@register("agents/director/plan/ExpandStoryArc")
class ExpandStoryArc(AgentNode):
    """
    Expands a plan's beats into full prose and pushes them to scene history.

    Delegates to expand_beats() which handles chunking, template calls,
    revision, emission, and task completion.
    """

    _agent_name: ClassVar[str] = "narrator"

    class Fields:
        chunk_size = PropertyField(
            name="chunk_size",
            type="int",
            description="Number of beats per expansion chunk",
            default=3,
        )

    def __init__(self, title="Expand Story Arc", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_input("plan_id", socket_type="str")
        self.add_input("beats", socket_type="list")
        self.add_input("perspective", socket_type="str")
        self.add_input("director_notes", socket_type="str", optional=True)

        self.set_property("chunk_size", 3)

        self.add_output("state")
        self.add_output("result", socket_type="str")
        self.add_output("word_count", socket_type="int")

    async def run(self, state: GraphState):
        input_state = self.require_input("state")
        plan_id = self.require_input("plan_id")
        beats = self.require_input("beats")
        perspective = self.require_input("perspective")
        director_notes = self.normalized_input_value("director_notes") or ""
        chunk_size = int(self.get_property("chunk_size"))

        scene = active_scene.get()
        narrator = get_agent("narrator")

        if not beats:
            self.set_output_values({
                "state": input_state,
                "result": "No beats to expand",
                "word_count": 0,
            })
            return

        chat_ctx = director_chat_context.get()
        chat_id = chat_ctx.chat_id if chat_ctx else None

        total_blocks, total_words = await expand_beats(
            scene=scene,
            narrator=narrator,
            beats=beats,
            plan_id=plan_id,
            perspective=perspective,
            director_notes=director_notes,
            chunk_size=chunk_size,
            chat_id=chat_id,
        )

        result = f"Generated {total_blocks} blocks, {total_words} words from {len(beats)} beats"
        log.info("expand_story_arc.done", result=result)

        self.set_output_values({
            "state": input_state,
            "result": result,
            "word_count": total_words,
        })
