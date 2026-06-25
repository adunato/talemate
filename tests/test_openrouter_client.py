import pytest

from talemate.config import get_config
from talemate.client.openrouter import ClientConfig, OpenRouterClient


@pytest.fixture
def openrouter_client():
    name = "OpenRouter Reason Prefill Test"
    config = get_config()
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
