"""
Plan utilities — plan state management and task parsing.
"""

import structlog

from talemate.emit import emit

from .schema import Beat, Plan

log = structlog.get_logger("talemate.agents.director.plan.util")

PLANS_STATE_KEY = "plans"


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


def emit_plan_updated(plan: Plan, chat_id: str | None = None):
    """Emit a plan_updated event to the frontend via the director handler."""
    emit(
        "director",
        data={
            "action": "plan_updated",
            "plan": plan.model_dump(),
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


def complete_task(scene, task_id: str, plan_id: str | None = None) -> str:
    """Mark a task as completed. Searches all plans if plan_id is not given."""

    if plan_id:
        plan = get_plan(scene, plan_id)
        if not plan:
            return f"No plan found with ID '{plan_id}'"
    else:
        # Search all plans for the task
        plans = scene.agent_state.get("director", {}).get(PLANS_STATE_KEY, {})
        plan = None
        for pid, raw in plans.items():
            p = Plan.model_validate(raw)
            if p.get_task(task_id):
                plan = p
                break
        if not plan:
            return f"No task found with ID '{task_id}' in any plan"

    task = plan.complete_task(task_id)
    if not task:
        return f"No task found with ID '{task_id}' in plan '{plan.id}'"

    save_plan(scene, plan)

    completed = plan.completed_count
    total = len(plan.tasks)
    return f"Task {task.order} [{task_id}] completed ({completed}/{total} done)"


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
