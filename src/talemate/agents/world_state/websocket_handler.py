import pydantic

from talemate.instance import get_agent
from talemate.server.websocket_plugin import Plugin
from talemate.status import set_loading
from talemate.util.time import amount_unit_to_iso8601_duration

__all__ = [
    "WorldStateWebsocketHandler",
]


class RequestUpdatePayload(pydantic.BaseModel):
    reset: bool = False


class SummarizeAndPinPayload(pydantic.BaseModel):
    message_id: int | None = None
    num_messages: int = 5


class AdvanceTimePayload(pydantic.BaseModel):
    duration: str | None = None
    amount: int | None = None
    unit: str | None = None

    @pydantic.model_validator(mode="after")
    def check_duration_or_amount_unit(self):
        if not self.duration and (self.amount is None or not self.unit):
            raise ValueError("Either 'duration' or 'amount' + 'unit' must be provided.")
        return self

    @property
    def iso_duration(self) -> str:
        if self.duration:
            return self.duration
        return amount_unit_to_iso8601_duration(self.amount, self.unit)


class WorldStateWebsocketHandler(Plugin):
    router = "world_state_agent"

    @property
    def agent(self):
        return get_agent("world_state")

    @set_loading("Updating world state", cancellable=True, as_async=True)
    async def handle_request_update(self, data: dict):
        payload = RequestUpdatePayload(**data)

        if payload.reset:
            self.scene.world_state.reset()

        await self.scene.world_state.request_update()
        await self.signal_operation_done(allow_auto_save=False)

    @set_loading("Summarizing and pinning", cancellable=True, as_async=True)
    async def handle_summarize_and_pin(self, data: dict):
        payload = SummarizeAndPinPayload(**data)

        if not self.scene.history:
            raise ValueError("No history to summarize.")

        message_id = payload.message_id or self.scene.history[-1].id
        await self.agent.summarize_and_pin(
            message_id, num_messages=payload.num_messages
        )
        await self.signal_operation_done(allow_auto_save=False)

    @set_loading("Advancing time", cancellable=True, as_async=True)
    async def handle_advance_time(self, data: dict):
        payload = AdvanceTimePayload(**data)
        await self.agent.advance_time(payload.iso_duration)
        await self.signal_operation_done()
