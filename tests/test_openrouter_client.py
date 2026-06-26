import json

import pytest
import httpx

from talemate.config import get_config
from talemate.client.openrouter import ClientConfig, OpenRouterClient


@pytest.fixture
def openrouter_client():
    name = "OpenRouter Reason Prefill Test"
    config = get_config()
    original_api_key = config.openrouter.api_key
    config.openrouter.api_key = "test-api-key"
    config.clients[name] = ClientConfig(
        type="openrouter",
        name=name,
        model="test/model",
    )
    client = OpenRouterClient(name=name)
    client.get_system_message = lambda kind: "system prompt"
    try:
        yield client
    finally:
        config.clients.pop(name, None)
        config.openrouter.api_key = original_api_key


def test_openrouter_supports_reason_prefill(openrouter_client):
    assert openrouter_client.supports_reason_prefill is True


def test_reason_prefill_is_trailing_assistant_message(openrouter_client):
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.reason_prefill = "<think>"

    messages = openrouter_client.build_messages("user prompt", "conversation")

    assert messages == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
        {"role": "assistant", "content": "<think>"},
    ]


def test_user_system_chat_message_template_reorders_messages(openrouter_client):
    openrouter_client.client_config.chat_message_template = json.dumps(
        [
            {"slot": "user_prompt", "role": "user"},
            {"slot": "system_prompt", "role": "system"},
            {"slot": "assistant_prefill", "role": "assistant"},
        ]
    )
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.reason_prefill = "<think>"

    messages = openrouter_client.build_messages("user prompt", "conversation")

    assert messages == [
        {"role": "user", "content": "user prompt"},
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "<think>"},
    ]


def test_user_system_template_keeps_coercion_prefill_prefix(openrouter_client):
    openrouter_client.client_config.chat_message_template = json.dumps(
        [
            {"slot": "user_prompt", "role": "user"},
            {"slot": "system_prompt", "role": "system"},
            {"slot": "assistant_prefill", "role": "assistant"},
        ]
    )
    openrouter_client.client_config.reason_enabled = False

    messages = openrouter_client.build_messages(
        "user prompt<|BOT|>assistant prefix",
        "conversation",
    )

    assert messages == [
        {"role": "user", "content": "user prompt"},
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "assistant prefix", "prefix": True},
    ]


def test_chat_message_template_can_change_roles(openrouter_client):
    openrouter_client.client_config.chat_message_template = json.dumps(
        [
            {"slot": "user_prompt", "role": "user"},
            {"slot": "system_prompt", "role": "user"},
        ]
    )

    messages = openrouter_client.build_messages("user prompt", "conversation")

    assert messages == [
        {"role": "user", "content": "user prompt"},
        {"role": "user", "content": "system prompt"},
    ]


def test_legacy_chat_message_template_name_still_works(openrouter_client):
    openrouter_client.client_config.chat_message_template = "user-system-assistant"

    messages = openrouter_client.build_messages("user prompt", "conversation")

    assert messages == [
        {"role": "user", "content": "user prompt"},
        {"role": "system", "content": "system prompt"},
    ]


def test_invalid_chat_message_template_falls_back(openrouter_client):
    openrouter_client.client_config.chat_message_template = "not json"

    messages = openrouter_client.build_messages("user prompt", "conversation")

    assert messages == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]


def test_reason_prefill_does_not_enable_general_coercion(openrouter_client):
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.reason_prefill = "<think>"
    openrouter_client.client_config.double_coercion = "Certainly:"

    messages = openrouter_client.build_messages("user prompt", "conversation")

    assert messages[-1] == {"role": "assistant", "content": "<think>"}


def test_blank_reason_prefill_does_not_add_assistant_message(openrouter_client):
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.reason_prefill = "   "

    messages = openrouter_client.build_messages("user prompt", "conversation")

    assert [message["role"] for message in messages] == ["system", "user"]


def test_non_reasoning_coercion_keeps_existing_prefix_behavior(openrouter_client):
    openrouter_client.client_config.reason_enabled = False

    messages = openrouter_client.build_messages(
        "user prompt<|BOT|>assistant prefix",
        "conversation",
    )

    assert messages[-1] == {
        "role": "assistant",
        "content": "assistant prefix",
        "prefix": True,
    }


class MockStreamResponse:
    status_code = 200

    def __init__(self, response_chunks=None):
        self.response_chunks = response_chunks or ["data: [DONE]\n"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def aiter_text(self):
        for chunk in self.response_chunks:
            yield chunk


class MockAsyncClient:
    def __init__(self, captured_payload, response_chunks=None):
        self.captured_payload = captured_payload
        self.response_chunks = response_chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def stream(self, method, url, **kwargs):
        self.captured_payload.update(kwargs["json"])
        return MockStreamResponse(self.response_chunks)


@pytest.mark.asyncio
async def test_budget_reasoning_sends_max_tokens(
    openrouter_client, monkeypatch
):
    captured_payload = {}
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.reason_tokens = 2048
    openrouter_client.client_config.reasoning_effort = "budget"
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda: MockAsyncClient(captured_payload),
    )

    await openrouter_client.generate("user prompt", {}, "conversation")

    assert captured_payload["reasoning"] == {"max_tokens": 2048}
    assert openrouter_client._request_payload == captured_payload


@pytest.mark.asyncio
async def test_request_payload_preserves_final_chat_message_order(
    openrouter_client, monkeypatch
):
    captured_payload = {}
    openrouter_client.client_config.chat_message_template = json.dumps(
        [
            {"slot": "user_prompt", "role": "user"},
            {"slot": "system_prompt", "role": "system"},
            {"slot": "assistant_prefill", "role": "assistant"},
        ]
    )
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.reason_prefill = "<think>"
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda: MockAsyncClient(captured_payload),
    )

    await openrouter_client.generate("user prompt", {}, "conversation")

    assert openrouter_client._request_payload["messages"] == [
        {"role": "user", "content": "user prompt"},
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "<think>"},
    ]
    assert openrouter_client._request_payload == captured_payload


@pytest.mark.asyncio
async def test_reasoning_effort_replaces_token_budget(
    openrouter_client, monkeypatch
):
    captured_payload = {}
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.reason_tokens = 2048
    openrouter_client.client_config.reasoning_effort = "high"
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda: MockAsyncClient(captured_payload),
    )

    await openrouter_client.generate("user prompt", {}, "conversation")

    assert captured_payload["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_disabled_reasoning_omits_reasoning_parameter(
    openrouter_client, monkeypatch
):
    captured_payload = {}
    openrouter_client.client_config.reason_enabled = False
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda: MockAsyncClient(captured_payload),
    )

    await openrouter_client.generate("user prompt", {}, "conversation")

    assert "reasoning" not in captured_payload


@pytest.mark.asyncio
async def test_reasoning_promoted_when_response_is_empty(
    openrouter_client, monkeypatch
):
    captured_payload = {}
    response_chunks = [
        'data: {"choices":[{"delta":{"reasoning":"Visible answer"}}]}\n',
        "data: [DONE]\n",
    ]
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.promote_reasoning_on_empty_response = True
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda: MockAsyncClient(captured_payload, response_chunks),
    )

    response = await openrouter_client.generate("user prompt", {}, "conversation")

    assert response == "Visible answer"
    assert openrouter_client.reasoning_response == ""


@pytest.mark.asyncio
async def test_reasoning_not_promoted_when_fallback_is_disabled(
    openrouter_client, monkeypatch
):
    captured_payload = {}
    response_chunks = [
        'data: {"choices":[{"delta":{"reasoning":"Internal analysis"}}]}\n',
        "data: [DONE]\n",
    ]
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.promote_reasoning_on_empty_response = False
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda: MockAsyncClient(captured_payload, response_chunks),
    )

    response = await openrouter_client.generate("user prompt", {}, "conversation")

    assert response == ""
    assert openrouter_client.reasoning_response == "Internal analysis"


@pytest.mark.asyncio
async def test_reasoning_not_promoted_when_response_has_content(
    openrouter_client, monkeypatch
):
    captured_payload = {}
    response_chunks = [
        'data: {"choices":[{"delta":{"reasoning":"Internal analysis"}}]}\n',
        'data: {"choices":[{"delta":{"content":"Visible answer"}}]}\n',
        "data: [DONE]\n",
    ]
    openrouter_client.client_config.reason_enabled = True
    openrouter_client.client_config.promote_reasoning_on_empty_response = True
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda: MockAsyncClient(captured_payload, response_chunks),
    )

    response = await openrouter_client.generate("user prompt", {}, "conversation")

    assert response == "Visible answer"
    assert openrouter_client.reasoning_response == "Internal analysis"
