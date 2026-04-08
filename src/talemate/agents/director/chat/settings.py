"""
Mode-specific settings for director chats.

Each chat mode that needs per-chat configuration gets its own settings model
here. ChatModeSettings is the container embedded in both DirectorChat (for
persistence) and DirectorChatContext (for runtime access). Adding a new mode
is a matter of defining a new settings model and adding one field to
ChatModeSettings.
"""

import pydantic

__all__ = [
    "GenerateArcSettings",
    "ChatModeSettings",
]


class GenerateArcSettings(pydantic.BaseModel):
    """Settings for the generate_arc / generate_arc_expand modes."""

    close_arc: bool = False
    """
    When True, the arc lands a full resolution (character choice + wind-down).
    When False (default), the arc ends on a high-tension handoff so the user
    can keep playing from where the arc leaves off.
    """


class ChatModeSettings(pydantic.BaseModel):
    """Container for all mode-specific settings on a DirectorChat."""

    generate_arc: GenerateArcSettings = pydantic.Field(
        default_factory=GenerateArcSettings
    )
