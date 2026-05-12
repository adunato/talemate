"""
Websocket plugin for scene-level message mutation actions.

Routes frontend-initiated changes to a SceneMessage's body — manual edits
(double-click edit) and revision-stack canonical swaps. Both end up calling
into `Scene.edit_message`, but the manual-edit path additionally runs the
content through the editor agent's exposition cleanup when configured.
"""

import pydantic
import structlog

import talemate.instance as instance
from talemate.server.websocket_plugin import Plugin

log = structlog.get_logger("talemate.server.scene_message")

__all__ = ["SceneMessagePlugin"]


class EditPayload(pydantic.BaseModel):
    id: int
    text: str


class SwapRevisionPayload(pydantic.BaseModel):
    id: int
    text: str


class SceneMessagePlugin(Plugin):
    router = "scene_message"

    async def handle_edit(self, data: dict):
        """
        Replace a scene message's body with user-edited text. When the
        editor agent's exposition cleanup is enabled and the target is a
        character message, the text is cleaned up before being committed.
        """
        payload = EditPayload(**data)
        message = self.scene.get_message(payload.id)
        if message is None:
            await self.signal_operation_failed(
                f"Message {payload.id} not found"
            )
            return

        new_text = payload.text
        editor = instance.get_agent("editor")

        if (
            editor.enabled
            and message.typ == "character"
            and editor.fix_exposition_enabled
            and editor.fix_exposition_user_input
        ):
            character = self.scene.get_character(message.character_name)
            new_text = await editor.cleanup_character_message(
                new_text,
                character,
                strip_partial=not editor.allow_incomplete_sentences,
            )

        self.scene.edit_message(payload.id, new_text)

    async def handle_swap_revision(self, data: dict):
        """
        Set a scene message's body to a previously generated revision. The
        text is not run through editor cleanup; it was already a valid
        prior version of the message.
        """
        payload = SwapRevisionPayload(**data)
        if self.scene.get_message(payload.id) is None:
            await self.signal_operation_failed(
                f"Message {payload.id} not found"
            )
            return
        self.scene.edit_message(payload.id, payload.text)
