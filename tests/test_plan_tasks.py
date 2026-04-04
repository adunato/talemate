"""
Unit tests for plan task management operations.

Tests cover:
- Plan schema methods: remove_task, insert_task, edit_task, _renumber_tasks
- Plan utility functions: save_plan, get_plan, delete_plan, complete_task,
  check_plan_locked, cleanup_orphaned_plans
- Discriminated union serialization round-trips (Task vs Beat)
"""

import pytest

from conftest import MockScene

from talemate.agents.director.plan.schema import (
    Beat,
    Plan,
    PlanStatus,
    Task,
)
from talemate.agents.director.plan.util import (
    check_plan_locked,
    cleanup_orphaned_plans,
    complete_task,
    delete_plan,
    get_plan,
    save_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(**kwargs) -> Plan:
    """Create a plan with sensible defaults."""
    defaults = dict(
        instructions="test plan",
        status=PlanStatus.ready,
        tasks=[
            Task(description="first", order=1),
            Task(description="second", order=2),
            Task(description="third", order=3),
        ],
    )
    defaults.update(kwargs)
    return Plan(**defaults)


def _make_beat_plan(**kwargs) -> Plan:
    """Create a plan with Beat tasks."""
    defaults = dict(
        instructions="beat plan",
        status=PlanStatus.ready,
        tasks=[
            Beat(description="opening", order=1, type="narration", tension=0.2),
            Beat(
                description="confrontation",
                order=2,
                type="dialogue",
                tension=0.6,
                characters=["Alice", "Bob"],
            ),
            Beat(description="climax", order=3, type="action", tension=0.9),
        ],
    )
    defaults.update(kwargs)
    return Plan(**defaults)


def _make_scene_with_plans(*plans: Plan) -> MockScene:
    """Create a MockScene with plans in agent_state."""
    scene = MockScene()
    scene.agent_state = {"director": {"plans": {}}}
    for plan in plans:
        save_plan(scene, plan)
    return scene


# ---------------------------------------------------------------------------
# Plan.remove_task
# ---------------------------------------------------------------------------


class TestRemoveTask:
    def test_removes_existing_task(self):
        plan = _make_plan()
        task_id = plan.tasks[1].id
        removed = plan.remove_task(task_id)
        assert removed is not None
        assert removed.description == "second"
        assert len(plan.tasks) == 2

    def test_renumbers_after_removal(self):
        plan = _make_plan()
        task_id = plan.tasks[0].id
        plan.remove_task(task_id)
        assert [t.order for t in plan.tasks] == [1, 2]

    def test_returns_none_for_missing_id(self):
        plan = _make_plan()
        assert plan.remove_task("nonexistent") is None
        assert len(plan.tasks) == 3

    def test_remove_last_task(self):
        plan = _make_plan()
        task_id = plan.tasks[2].id
        plan.remove_task(task_id)
        assert len(plan.tasks) == 2
        assert [t.order for t in plan.tasks] == [1, 2]


# ---------------------------------------------------------------------------
# Plan.insert_task
# ---------------------------------------------------------------------------


class TestInsertTask:
    def test_insert_at_end(self):
        plan = _make_plan()
        new = Task(description="fourth")
        plan.insert_task(new, "end")
        assert len(plan.tasks) == 4
        assert plan.tasks[3].description == "fourth"
        assert [t.order for t in plan.tasks] == [1, 2, 3, 4]

    def test_insert_at_start(self):
        plan = _make_plan()
        new = Task(description="zeroth")
        plan.insert_task(new, "start")
        assert len(plan.tasks) == 4
        assert plan.tasks[0].description == "zeroth"
        assert [t.order for t in plan.tasks] == [1, 2, 3, 4]

    def test_insert_after_specific_task(self):
        plan = _make_plan()
        after_id = plan.tasks[1].id
        new = Task(description="between")
        plan.insert_task(new, after_id)
        assert len(plan.tasks) == 4
        assert plan.tasks[2].description == "between"
        assert [t.order for t in plan.tasks] == [1, 2, 3, 4]

    def test_insert_after_nonexistent_raises(self):
        plan = _make_plan()
        with pytest.raises(ValueError, match="No task with ID"):
            plan.insert_task(Task(description="orphan"), "nonexistent")

    def test_default_position_is_end(self):
        plan = _make_plan()
        new = Task(description="appended")
        plan.insert_task(new)
        assert plan.tasks[-1].description == "appended"

    def test_insert_beat_into_task_plan(self):
        plan = _make_plan()
        beat = Beat(description="new beat", type="dialogue", tension=0.5)
        plan.insert_task(beat, "end")
        assert len(plan.tasks) == 4
        assert isinstance(plan.tasks[3], Beat)
        assert plan.tasks[3].type == "dialogue"


# ---------------------------------------------------------------------------
# Plan.edit_task
# ---------------------------------------------------------------------------


class TestEditTask:
    def test_edit_description(self):
        plan = _make_plan()
        task_id = plan.tasks[0].id
        task, changed = plan.edit_task(task_id, {"description": "updated"})
        assert task.description == "updated"
        assert "description" in changed

    def test_edit_beat_fields(self):
        plan = _make_beat_plan()
        task_id = plan.tasks[0].id
        task, changed = plan.edit_task(
            task_id,
            {"type": "dialogue", "tension": 0.8, "characters": ["Eve"]},
        )
        assert task.type == "dialogue"
        assert task.tension == 0.8
        assert task.characters == ["Eve"]
        assert set(changed) == {"type", "tension", "characters"}

    def test_protected_fields_ignored(self):
        plan = _make_plan()
        task_id = plan.tasks[0].id
        original_order = plan.tasks[0].order
        task, changed = plan.edit_task(task_id, {"id": "hacked", "order": 99})
        assert task.id == task_id
        assert task.order == original_order
        assert changed == []

    def test_unknown_fields_ignored(self):
        plan = _make_plan()
        task_id = plan.tasks[0].id
        task, changed = plan.edit_task(task_id, {"nonexistent_field": "value"})
        assert task is not None
        assert changed == []

    def test_returns_none_for_missing_task(self):
        plan = _make_plan()
        task, changed = plan.edit_task("nonexistent", {"description": "x"})
        assert task is None
        assert changed == []

    def test_mixed_valid_and_invalid_fields(self):
        plan = _make_plan()
        task_id = plan.tasks[0].id
        task, changed = plan.edit_task(
            task_id, {"description": "new", "id": "hacked", "bogus": 1}
        )
        assert task.description == "new"
        assert changed == ["description"]


# ---------------------------------------------------------------------------
# Serialization round-trip (discriminated union)
# ---------------------------------------------------------------------------


class TestPlanSerialization:
    def test_mixed_task_beat_roundtrip(self):
        plan = Plan(
            instructions="mixed",
            tasks=[
                Task(description="generic", order=1),
                Beat(
                    description="beat",
                    order=2,
                    type="dialogue",
                    tension=0.7,
                    characters=["X"],
                ),
            ],
        )
        dumped = plan.model_dump()
        restored = Plan.model_validate(dumped)
        assert len(restored.tasks) == 2
        assert isinstance(restored.tasks[0], Task)
        assert not isinstance(restored.tasks[0], Beat)
        assert isinstance(restored.tasks[1], Beat)
        assert restored.tasks[1].tension == 0.7
        assert restored.tasks[1].characters == ["X"]

    def test_roundtrip_after_insert(self):
        plan = _make_beat_plan()
        new = Beat(description="inserted", type="reveal", tension=0.4)
        plan.insert_task(new, "start")
        dumped = plan.model_dump()
        restored = Plan.model_validate(dumped)
        assert len(restored.tasks) == 4
        assert restored.tasks[0].description == "inserted"
        assert [t.order for t in restored.tasks] == [1, 2, 3, 4]

    def test_roundtrip_after_edit(self):
        plan = _make_beat_plan()
        task_id = plan.tasks[1].id
        plan.edit_task(task_id, {"type": "action", "tension": 0.1})
        dumped = plan.model_dump()
        restored = Plan.model_validate(dumped)
        edited = restored.get_task(task_id)
        assert edited.type == "action"
        assert edited.tension == 0.1


# ---------------------------------------------------------------------------
# Utility functions: save_plan, get_plan, delete_plan
# ---------------------------------------------------------------------------


class TestPlanPersistence:
    def test_save_and_get(self):
        plan = _make_plan()
        scene = _make_scene_with_plans()
        save_plan(scene, plan)
        loaded = get_plan(scene, plan.id)
        assert loaded is not None
        assert loaded.id == plan.id
        assert len(loaded.tasks) == 3

    def test_get_nonexistent(self):
        scene = _make_scene_with_plans()
        assert get_plan(scene, "missing") is None

    def test_delete(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        assert delete_plan(scene, plan.id) is True
        assert get_plan(scene, plan.id) is None

    def test_delete_nonexistent(self):
        scene = _make_scene_with_plans()
        assert delete_plan(scene, "missing") is False

    def test_save_preserves_after_mutation(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        # mutate and re-save
        plan.insert_task(Task(description="new"), "end")
        save_plan(scene, plan)
        loaded = get_plan(scene, plan.id)
        assert len(loaded.tasks) == 4


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------


class TestCompleteTask:
    def test_complete_with_plan_id(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        task_id = plan.tasks[0].id
        result = complete_task(scene, task_id, plan_id=plan.id)
        assert "completed" in result
        loaded = get_plan(scene, plan.id)
        assert loaded.get_task(task_id).status == "completed"

    def test_complete_without_plan_id_searches_all(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        task_id = plan.tasks[1].id
        result = complete_task(scene, task_id)
        assert "completed" in result

    def test_complete_missing_task(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        result = complete_task(scene, "nonexistent")
        assert "No task found" in result

    def test_complete_all_tasks_completes_plan(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        for task in plan.tasks:
            complete_task(scene, task.id, plan_id=plan.id)
        loaded = get_plan(scene, plan.id)
        assert loaded.status == PlanStatus.completed


# ---------------------------------------------------------------------------
# check_plan_locked
# ---------------------------------------------------------------------------


class TestCheckPlanLocked:
    def test_ready_plan_not_locked(self):
        plan = _make_plan(status=PlanStatus.ready)
        assert check_plan_locked(plan) is None

    def test_executing_plan_not_locked(self):
        plan = _make_plan(status=PlanStatus.executing)
        assert check_plan_locked(plan) is None

    def test_planning_plan_not_locked(self):
        plan = _make_plan(status=PlanStatus.planning)
        assert check_plan_locked(plan) is None

    def test_completed_plan_locked(self):
        plan = _make_plan(status=PlanStatus.completed)
        result = check_plan_locked(plan)
        assert result is not None
        assert "completed" in result

    def test_cancelled_not_locked(self):
        plan = _make_plan(status=PlanStatus.cancelled)
        assert check_plan_locked(plan) is None


# ---------------------------------------------------------------------------
# cleanup_orphaned_plans
# ---------------------------------------------------------------------------


class TestCleanupOrphanedPlans:
    def test_removes_unreferenced_plans(self):
        plan_a = _make_plan()
        plan_b = _make_plan()
        scene = _make_scene_with_plans(plan_a, plan_b)
        # Only plan_a is referenced by a chat
        chats = {"chat1": {"plan_id": plan_a.id}}
        removed = cleanup_orphaned_plans(scene, chats)
        assert plan_b.id in removed
        assert get_plan(scene, plan_a.id) is not None
        assert get_plan(scene, plan_b.id) is None

    def test_keeps_all_when_referenced(self):
        plan_a = _make_plan()
        plan_b = _make_plan()
        scene = _make_scene_with_plans(plan_a, plan_b)
        chats = {
            "chat1": {"plan_id": plan_a.id},
            "chat2": {"plan_id": plan_b.id},
        }
        removed = cleanup_orphaned_plans(scene, chats)
        assert removed == []

    def test_removes_all_when_no_chats(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        removed = cleanup_orphaned_plans(scene, {})
        assert plan.id in removed
        assert get_plan(scene, plan.id) is None

    def test_no_plans_returns_empty(self):
        scene = _make_scene_with_plans()
        removed = cleanup_orphaned_plans(scene, {"chat1": {"plan_id": "whatever"}})
        assert removed == []

    def test_chats_with_no_plan_id(self):
        plan = _make_plan()
        scene = _make_scene_with_plans(plan)
        chats = {"chat1": {"plan_id": None}, "chat2": {}}
        removed = cleanup_orphaned_plans(scene, chats)
        assert plan.id in removed
