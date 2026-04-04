"""
Plan utilities — plan state management and task parsing.
"""

import structlog

from talemate.emit import emit
from talemate.agents.director.chat.context import director_chat_context

from .schema import Beat, Plan, PlanStatus

log = structlog.get_logger("talemate.agents.director.plan.util")

PLANS_STATE_KEY = "plans"

# Plan statuses that block modifications
LOCKED_STATUSES = {PlanStatus.completed}


def _ensure_plans_collection(scene) -> dict:
    """Ensure the plans collection exists in director agent state."""
    if "director" not in scene.agent_state:
        scene.agent_state["director"] = {}
    if PLANS_STATE_KEY not in scene.agent_state["director"]:
        scene.agent_state["director"][PLANS_STATE_KEY] = {}
    return scene.agent_state["director"][PLANS_STATE_KEY]


def get_plan(scene, plan_id: str) -> Plan | None:
    """Read a plan by ID from agent state."""
    plans = scene.agent_state.get("director", {}).get(PLANS_STATE_KEY, {})
    raw = plans.get(plan_id)
    if raw is None:
        return None
    return Plan.model_validate(raw)


def emit_plan_updated(plan: Plan | None, chat_id: str | None = None):
    """Emit a plan_updated event to the frontend via the director handler."""
    emit(
        "director",
        data={
            "action": "plan_updated",
            "plan": plan.model_dump() if plan else None,
            "chat_id": chat_id or "",
        },
    )


def save_plan(scene, plan: Plan):
    """Persist a plan to the plans collection."""
    plans = _ensure_plans_collection(scene)
    plans[plan.id] = plan.model_dump()


def delete_plan(scene, plan_id: str) -> bool:
    """Remove a plan from the collection."""
    plans = _ensure_plans_collection(scene)
    if plan_id in plans:
        del plans[plan_id]
        return True
    return False


def find_plan_by_task(scene, task_id: str, plan_id: str | None = None) -> Plan | None:
    """Find the plan containing a task. Searches all plans if plan_id is not given."""
    if plan_id:
        return get_plan(scene, plan_id)
    plans = scene.agent_state.get("director", {}).get(PLANS_STATE_KEY, {})
    for pid, raw in plans.items():
        p = Plan.model_validate(raw)
        if p.get_task(task_id):
            return p
    return None


def complete_task(scene, task_id: str, plan_id: str | None = None) -> str:
    """Mark a task as completed. Searches all plans if plan_id is not given."""

    plan = find_plan_by_task(scene, task_id, plan_id)
    if not plan:
        msg = f"No plan found with ID '{plan_id}'" if plan_id else f"No task found with ID '{task_id}' in any plan"
        return msg

    task = plan.complete_task(task_id)
    if not task:
        return f"No task found with ID '{task_id}' in plan '{plan.id}'"

    save_plan(scene, plan)

    completed = plan.completed_count
    total = len(plan.tasks)
    return f"Task {task.order} [{task_id}] completed ({completed}/{total} done)"


def check_plan_locked(plan: Plan) -> str | None:
    """Return an error message if the plan is in a locked status, else None."""
    if plan.status in LOCKED_STATUSES:
        return f"Cannot modify plan [{plan.id}]: status is '{plan.status.value}'"
    return None


def resolve_plan_id(plan_id: str | None) -> str | None:
    """Resolve plan_id from input or active chat context."""
    if plan_id:
        return plan_id
    chat_ctx = director_chat_context.get()
    return chat_ctx.plan_id if chat_ctx else None


def get_active_plan(scene) -> Plan | None:
    """Get the plan from the active chat context."""
    plan_id = resolve_plan_id(None)
    if not plan_id:
        return None
    return get_plan(scene, plan_id)


def emit_plan_for_chat(plan: Plan):
    """Emit plan_updated for the current chat context."""
    chat_ctx = director_chat_context.get()
    chat_id = chat_ctx.chat_id if chat_ctx else None
    emit_plan_updated(plan, chat_id=chat_id)


def parse_beats(data) -> list[Beat]:
    """Parse beat list from extracted data (list of dicts or dict with 'beats' key)."""
    items = (
        data
        if isinstance(data, list)
        else data.get("beats", [])
        if isinstance(data, dict)
        else []
    )
    beats = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item.setdefault("order", i + 1)
        try:
            beats.append(Beat.model_validate(item))
        except Exception as e:
            log.warning("plan.parse_beat.skip", index=i, error=str(e))
    return beats
