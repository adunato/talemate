"""
Baseline snapshot testing infrastructure for prompt templates with XML section format.

Mirrors the tests in baselines/ but with section_format="xml" on the mock client.
Baselines are stored separately in tests/data/prompts/baselines_xml/.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from ..baselines.conftest import make_baseline_checker


BASELINES_DIR = (
    Path(__file__).parent.parent.parent / "data" / "prompts" / "baselines_xml"
)


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client with section_format="xml".

    Overrides the default mock_llm_client fixture so that all agents
    created in these tests will use XML section formatting.
    """
    client = AsyncMock()
    client.send_prompt = AsyncMock(return_value="Mock LLM response")
    client.max_token_length = 4096
    client.decensor_enabled = False
    client.can_be_coerced = True
    client.model_name = "test-model"
    client.data_format = "json"
    client.section_format = "xml"
    client.optimize_prompt_caching = False
    client.enforce_response_length = "cap_tokens_and_instructions"
    client.reason_enabled = False
    client.double_coercion = None
    client.name = "test-client"
    return client


@pytest.fixture
def baseline_checker(update_baselines):
    """Fixture providing a bound baseline checker for XML baselines."""
    return make_baseline_checker(update_baselines, BASELINES_DIR)
