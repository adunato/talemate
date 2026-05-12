"""
Shared helpers for world_state test modules.

Centralizes fixture-builders so the multiple new world_state test files
do not duplicate the same scaffolding.
"""

import pytest

import talemate.instance as instance
from conftest import MockMemoryAgent, MockScene, bootstrap_scene
from talemate.character import Character
from talemate.tale_mate import Actor, Player
from talemate.world_state.manager import WorldStateManager


class TrackingMemoryAgent(MockMemoryAgent):
    """Mock memory agent that records `add_many` and `delete` calls and
    serves a stub `get_document` / `multi_query` for tests that need them.

    Useful for assertions like "manager called add_many with the new entry".
    """

    def __init__(self):
        super().__init__()
        self.add_many_calls: list[list[dict]] = []
        self.delete_calls: list[dict] = []
        self.documents: dict[str, "_StubDocument"] = {}

    async def add_many(self, items: list[dict]):
        self.add_many_calls.append(items)

    async def delete(self, filters: dict):
        self.delete_calls.append(filters)

    async def get_document(self, id):  # noqa: A002 - mirrors source signature
        if isinstance(id, list):
            return {i: self.documents[i] for i in id if i in self.documents}
        if id in self.documents:
            return {id: self.documents[id]}
        return {}

    async def multi_query(self, *args, **kwargs):
        # Return whatever is configured; default empty.
        return getattr(self, "_multi_query_result", [])


class _StubDocument:
    """Document object compatible with `manager.get_pins` / get_context_db_entries.

    Mimics the shape used in `WorldStateManager.get_pins`:
    `documents[entry_id].raw`, `str(documents[entry_id])`, `.context_id`.
    """

    def __init__(self, raw: str, context_id=None, meta: dict | None = None, id=None):
        self.raw = raw
        self.context_id = context_id
        self.meta = meta or {}
        self.id = id

    def __str__(self) -> str:
        # time_aware_text is the str() representation of the document.
        return self.raw


def make_actor(scene: MockScene, name: str, is_player: bool = False) -> Character:
    """Create a real Character + Actor and add it to the scene synchronously
    (does NOT touch memory)."""
    character = Character(name=name, is_player=is_player)
    if is_player:
        actor = Player(character=character, agent=None)
    else:
        actor = Actor(character=character, agent=None)
    scene.actors.append(actor)
    scene.character_data[name] = character
    scene.active_characters.append(name)
    return character


def install_tracking_memory(scene: MockScene) -> TrackingMemoryAgent:
    """Replace the bootstrapped memory agent with a TrackingMemoryAgent."""
    tracking = TrackingMemoryAgent()
    instance.AGENTS["memory"] = tracking
    # All agents share a client; reuse the existing one
    tracking.client = scene.mock_client
    tracking.scene = scene
    return tracking


@pytest.fixture
def scene():
    """Bootstrapped MockScene with all agents wired up."""
    s = MockScene()
    bootstrap_scene(s)
    return s


@pytest.fixture
def scene_with_memory(scene):
    """MockScene where the memory agent is a TrackingMemoryAgent (records calls)."""
    tracking = install_tracking_memory(scene)
    return scene, tracking


@pytest.fixture
def world_state(scene):
    return scene.world_state


@pytest.fixture
def manager(scene):
    return WorldStateManager(scene)


@pytest.fixture
def manager_with_memory(scene_with_memory):
    scene, tracking = scene_with_memory
    return WorldStateManager(scene), tracking
