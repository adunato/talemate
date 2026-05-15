"""
Unit tests for the scene_message websocket plugin.

Focuses on the wire contract of ``SceneMessagePlugin.handle_edit``: the
optional ``reason`` / ``mutation_source`` metadata is forwarded to
``Scene.edit_message`` so the frontend's ``message_edited`` echo can
splice the new text onto the per-slot revision stack rather than
replacing the active entry in place. The editor agent's
``cleanup_character_message`` branch is bypassed by stubbing the agent
to ``enabled=False``; that branch is exercised in its own agent tests
and isn't the contract under test here.
"""

import pytest

from talemate.scene_message import CharacterMessage, reset_message_id
from talemate.server.scene_message import SceneMessagePlugin
from talemate.tale_mate import Scene


class _MockWebsocketHandler:
    """Minimal handler stand-in: exposes ``scene`` and a queue_put list."""

    def __init__(self, scene):
        self._scene = scene
        self.messages: list[dict] = []

    @property
    def scene(self):
        return self._scene

    def queue_put(self, data):
        self.messages.append(data)


class _DisabledEditor:
    """Stub editor agent; the plugin checks ``enabled`` and short-circuits."""

    enabled = False


@pytest.fixture(autouse=True)
def _reset_message_ids():
    reset_message_id()
    yield
    reset_message_id()


@pytest.fixture
def scene_with_message():
    """Real Scene carrying one CharacterMessage. Tests target its id."""
    scene = Scene()
    msg = CharacterMessage(message="Alice: Hello.")
    scene.history.append(msg)
    return scene, msg


@pytest.fixture
def plugin(scene_with_message, monkeypatch):
    """SceneMessagePlugin wired to a real Scene with editor cleanup disabled.

    ``edit_message`` is replaced by a spy so the test asserts on the
    plugin's pass-through directly without running the emit bus.
    """
    scene, _ = scene_with_message
    handler = _MockWebsocketHandler(scene=scene)
    plug = SceneMessagePlugin(handler)

    monkeypatch.setattr(
        "talemate.server.scene_message.instance.get_agent",
        lambda name: _DisabledEditor(),
    )

    calls: list[dict] = []

    def _spy(message_id, message, *, reason=None, mutation_source=None):
        calls.append(
            {
                "message_id": message_id,
                "message": message,
                "reason": reason,
                "mutation_source": mutation_source,
            }
        )

    monkeypatch.setattr(scene, "edit_message", _spy)
    plug._spy_calls = calls
    return plug


class TestSceneMessagePluginHandleEdit:
    @pytest.mark.asyncio
    async def test_plain_edit_forwards_no_metadata(self, plugin, scene_with_message):
        """A bare edit payload must not invent metadata; reason and
        mutation_source default to None so ``Scene.edit_message`` emits a
        plain ``message_edited`` envelope (data=None)."""
        _, msg = scene_with_message
        await plugin.handle_edit({"id": msg.id, "text": "Alice: Hi."})

        assert plugin._spy_calls == [
            {
                "message_id": msg.id,
                "message": "Alice: Hi.",
                "reason": None,
                "mutation_source": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_continue_edit_forwards_revision_metadata(
        self, plugin, scene_with_message
    ):
        """The Continue action ships ``reason='continue'`` and
        ``mutation_source='continue'``; both must reach
        ``Scene.edit_message`` so the resulting emit carries them onto
        the frontend's revision-stack splice."""
        _, msg = scene_with_message
        await plugin.handle_edit(
            {
                "id": msg.id,
                "text": "Alice: Hi. How are you?",
                "reason": "continue",
                "mutation_source": "continue",
            }
        )

        assert plugin._spy_calls == [
            {
                "message_id": msg.id,
                "message": "Alice: Hi. How are you?",
                "reason": "continue",
                "mutation_source": "continue",
            }
        ]

    @pytest.mark.asyncio
    async def test_client_supplied_mutations_field_is_dropped(
        self, plugin, scene_with_message
    ):
        """``mutations`` is intentionally NOT part of ``EditPayload``;
        clients cannot inject prior-state entries into the revision
        stack via this wire path. Pydantic's default ``extra=ignore``
        drops the field, and ``Scene.edit_message`` is invoked without
        ``mutations`` in its kwargs."""
        _, msg = scene_with_message
        await plugin.handle_edit(
            {
                "id": msg.id,
                "text": "Alice: Hi.",
                "reason": "continue",
                "mutation_source": "continue",
                "mutations": [{"message": "injected", "source": "original"}],
            }
        )

        # Only the supported metadata was forwarded; the rogue mutations
        # field never reached Scene.edit_message.
        call = plugin._spy_calls[0]
        assert "mutations" not in call or call.get("mutations") is None
        assert call["reason"] == "continue"
        assert call["mutation_source"] == "continue"
