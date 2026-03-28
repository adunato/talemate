from typing import Union

import structlog
from openai import AsyncOpenAI
from talemate.ux.schema import Action
from talemate.agents.base import AgentAction, AgentActionConfig, AgentDetail
from .schema import VoiceLibrary, Chunk, GenerationContext, INFO_CHUNK_SIZE

log = structlog.get_logger("talemate.agents.tts.openai_compatible")

OPENAI_COMPATIBLE_INFO = """
Connect to any TTS service that exposes an OpenAI-compatible `/v1/audio/speech` endpoint (e.g. vLLM, LocalAI, Speaches, etc.).

You will need to manually add voices to the voice library using the voice IDs supported by your server.
"""


class OpenAICompatibleMixin:
    """
    OpenAI-compatible TTS endpoint mixin.

    Connects to any server implementing the OpenAI /v1/audio/speech API.
    """

    @classmethod
    def add_actions(cls, actions: dict[str, AgentAction]):
        actions["_config"].config["apis"].choices.append(
            {
                "value": "openai_compatible",
                "label": "OpenAI Compatible",
                "help": "Any TTS service with an OpenAI-compatible /v1/audio/speech endpoint.",
            }
        )

        actions["openai_compatible"] = AgentAction(
            enabled=True,
            container=True,
            icon="mdi-server-outline",
            label="OpenAI Compatible",
            description="Connect to any TTS service that exposes an OpenAI-compatible /v1/audio/speech endpoint.",
            config={
                "api_url": AgentActionConfig(
                    type="text",
                    value="http://localhost:8000/v1",
                    label="API Base URL",
                    description="Base URL of the OpenAI-compatible TTS server (e.g. http://localhost:8000/v1)",
                ),
                "api_key": AgentActionConfig(
                    type="password",
                    value="",
                    label="API Key",
                    description="API key for the server (leave empty if not required)",
                ),
                "model": AgentActionConfig(
                    type="text",
                    value="tts-1",
                    label="Model",
                    description="Model identifier to send in requests",
                ),
                "chunk_size": AgentActionConfig(
                    type="number",
                    min=0,
                    step=64,
                    max=2048,
                    value=512,
                    label="Chunk size",
                    note=INFO_CHUNK_SIZE,
                ),
            },
        )

        return actions

    @classmethod
    def add_voices(cls, voices: dict[str, VoiceLibrary]):
        voices["openai_compatible"] = VoiceLibrary(api="openai_compatible")

    @property
    def openai_compatible_chunk_size(self) -> int:
        return self.actions["openai_compatible"].config["chunk_size"].value

    @property
    def openai_compatible_max_generation_length(self) -> int:
        return 1024

    @property
    def openai_compatible_api_url(self) -> str:
        return self.actions["openai_compatible"].config["api_url"].value

    @property
    def openai_compatible_api_key(self) -> str:
        return self.actions["openai_compatible"].config["api_key"].value

    @property
    def openai_compatible_model(self) -> str:
        return self.actions["openai_compatible"].config["model"].value

    @property
    def openai_compatible_configured(self) -> bool:
        return bool(self.openai_compatible_api_url)

    @property
    def openai_compatible_info(self) -> str:
        return OPENAI_COMPATIBLE_INFO

    @property
    def openai_compatible_not_configured_reason(self) -> str | None:
        if not self.openai_compatible_api_url:
            return "API base URL not set"
        return None

    @property
    def openai_compatible_not_configured_action(self) -> Action | None:
        if not self.openai_compatible_api_url:
            return Action(
                action_name="openAgentSettings",
                arguments=["tts", "openai_compatible"],
                label="Set API URL",
                icon="mdi-link",
            )
        return None

    @property
    def openai_compatible_agent_details(self) -> dict:
        details = {}

        if not self.openai_compatible_configured:
            details["openai_compatible_url"] = AgentDetail(
                icon="mdi-link",
                value="API URL not set",
                description="Set the base URL of your OpenAI-compatible TTS server.",
                color="error",
            ).model_dump()
        else:
            details["openai_compatible_url"] = AgentDetail(
                icon="mdi-link",
                value=self.openai_compatible_api_url,
                description="OpenAI-compatible TTS endpoint",
            ).model_dump()
            details["openai_compatible_model"] = AgentDetail(
                icon="mdi-brain",
                value=self.openai_compatible_model,
                description="Model identifier",
            ).model_dump()

        return details

    async def openai_compatible_generate(
        self, chunk: Chunk, context: GenerationContext
    ) -> Union[bytes, None]:
        api_key = self.openai_compatible_api_key or "no-key"

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.openai_compatible_api_url,
        )

        model = chunk.model or self.openai_compatible_model

        response = await client.audio.speech.create(
            model=model, voice=chunk.voice.provider_id, input=chunk.cleaned_text
        )

        return response.content
