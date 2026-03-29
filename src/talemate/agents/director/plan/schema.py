"""
Plan and task data models.

Generic planning system with Beat as a specialized task type for arc generation.
"""

from enum import Enum
from typing import Annotated, Literal, Union
import uuid

import pydantic
import talemate.util as util

__all__ = [
    "Task",
    "Beat",
    "Plan",
    "PlanStatus",
    "READING_SPEED_WPM",
    "words_per_token",
]

READING_SPEED_WPM = 250

# Assumed ratio of narration vs dialogue beats for word estimation.
NARRATION_BEAT_RATIO = 0.6

# Sample prose for deriving words-per-token ratio empirically.
_SAMPLE_TEXT = (
    "The starship drifted through the endless void, its hull scarred by "
    "countless journeys across the galaxy. Inside, the crew prepared for "
    "what would be their most dangerous mission yet."
)


def words_per_token() -> float:
    """Derive words-per-token ratio using the tokenizer approximation."""
    word_count = len(_SAMPLE_TEXT.split())
    token_count = util.count_tokens(_SAMPLE_TEXT)
    if token_count == 0:
        return 0.75
    return word_count / token_count


class PlanStatus(str, Enum):
    planning = "planning"
    ready = "ready"
    executing = "executing"
    completed = "completed"
    cancelled = "cancelled"


class Task(pydantic.BaseModel):
    """Generic task within a plan."""

    type: Literal["task"] = "task"
    id: str = pydantic.Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    status: Literal["pending", "executing", "completed", "skipped"] = "pending"
    order: int = 0

    def as_text(self) -> str:
        """Single-line text representation."""
        return f"Task {self.order} [{self.id}] | status: {self.status}\n  {self.description}"


class Beat(Task):
    """A beat — a specialized task for arc generation with narrative metadata."""

    type: Literal["narration", "dialogue", "action", "transition", "reveal"] = (
        "narration"
    )
    characters: list[str] = pydantic.Field(default_factory=list)
    pacing: Literal["slow", "moderate", "fast"] = "moderate"
    tension: float = 0.5

    @staticmethod
    def estimate_words(
        beat_count: int,
        narration_tokens: int = 512,
        dialogue_tokens: int = 75,
        dialogue_characters: int = 2,
        narration_ratio: float = NARRATION_BEAT_RATIO,
    ) -> int:
        """Estimate total word output for a given number of beats."""
        dialogue_ratio = 1.0 - narration_ratio
        wpt = words_per_token()
        narration_words = narration_tokens * wpt
        dialogue_words = dialogue_tokens * dialogue_characters * wpt
        words_per_beat = (narration_words * narration_ratio) + (
            dialogue_words * dialogue_ratio
        )
        return max(1, round(beat_count * words_per_beat))

    def as_text(self) -> str:
        """Single-line text representation with beat-specific fields."""
        chars = ", ".join(self.characters) if self.characters else "none"
        return (
            f"Beat {self.order} [{self.id}]: {self.type} | "
            f"pacing={self.pacing} tension={self.tension} | "
            f"characters: {chars} | status: {self.status}\n"
            f"  {self.description}"
        )


# Discriminated union: Beat before Task (more specific first)
TaskType = Annotated[Union[Beat, Task], pydantic.Field(discriminator="type")]


class Plan(pydantic.BaseModel):
    """A plan with an ordered list of tasks."""

    id: str = pydantic.Field(default_factory=lambda: str(uuid.uuid4())[:10])
    instructions: str = ""
    status: PlanStatus = PlanStatus.planning
    tasks: list[TaskType] = pydantic.Field(default_factory=list)
    meta: dict = pydantic.Field(default_factory=dict)

    def get_task(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def next_pending_task(self) -> Task | None:
        for task in self.tasks:
            if task.status == "pending":
                return task
        return None

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "completed")

    @property
    def all_tasks_done(self) -> bool:
        return (
            all(t.status in ("completed", "skipped") for t in self.tasks)
            and len(self.tasks) > 0
        )

    def complete_task(self, task_id: str) -> Task | None:
        """Mark a task as completed. Returns the task, or None if not found."""
        task = self.get_task(task_id)
        if task:
            task.status = "completed"
            if self.all_tasks_done:
                self.status = PlanStatus.completed
        return task

    def tasks_as_text(self) -> str:
        """Plain-text representation of all tasks, delegating to each task's as_text()."""
        return "\n".join(task.as_text() for task in self.tasks)

    def status_summary(self) -> str:
        """Plain-text status summary for chat output."""
        completed = self.completed_count
        total = len(self.tasks)
        next_task = self.next_pending_task()

        lines = [
            f"Plan [{self.id}] status: {self.status.value}",
            f"Instructions: {self.instructions}",
            f"Progress: {completed}/{total} tasks completed",
        ]

        perspective = self.meta.get("perspective")
        estimated_words = self.meta.get("estimated_words")
        if perspective:
            lines.append(f"Perspective: {perspective}")
        if estimated_words:
            reading_minutes = estimated_words / READING_SPEED_WPM
            lines.append(
                f"Estimated: ~{estimated_words} words, ~{reading_minutes:.0f} min reading time"
            )

        if next_task:
            lines.append(
                f"Next: [{next_task.id}] task {next_task.order} - {next_task.description[:100]}"
            )

        if self.tasks:
            lines.append("\nTasks:")
            lines.append(self.tasks_as_text())

        return "\n".join(lines)
