"""
Unit tests for talemate.prompts.extensions.

Covers CaptureContextExtension — a Jinja2 extension that renders block
content normally while also appending it to ``prompt_instance.captured_context``.
"""

import jinja2

from talemate.prompts.extensions import CaptureContextExtension


class _PromptStub:
    """Minimal stand-in for a Prompt object exposing the captured_context attr."""

    def __init__(self, initial: str = ""):
        self.captured_context = initial


def _make_env(prompt: _PromptStub | None) -> jinja2.Environment:
    """Build a Jinja2 environment with the capture_context extension installed."""
    env = jinja2.Environment(extensions=[CaptureContextExtension])
    env.globals["prompt_instance"] = prompt
    return env


class TestCaptureContextExtension:
    """Tests for CaptureContextExtension."""

    def test_block_renders_inline_in_output(self):
        """The captured block content is also returned as part of normal output."""
        prompt = _PromptStub()
        env = _make_env(prompt)

        template = env.from_string(
            "before|{% capture_context %}captured body{% end_capture_context %}|after"
        )
        rendered = template.render()

        assert rendered == "before|captured body|after"

    def test_block_content_appended_to_prompt_instance(self):
        """The block body is appended to ``prompt_instance.captured_context``."""
        prompt = _PromptStub()
        env = _make_env(prompt)

        env.from_string(
            "x{% capture_context %}hello world{% end_capture_context %}y"
        ).render()

        assert prompt.captured_context == "hello world"

    def test_multiple_blocks_concatenate_in_order(self):
        """Sequential capture blocks accumulate onto captured_context in order."""
        prompt = _PromptStub()
        env = _make_env(prompt)

        env.from_string(
            "{% capture_context %}first{% end_capture_context %}"
            "MIDDLE"
            "{% capture_context %}second{% end_capture_context %}"
        ).render()

        assert prompt.captured_context == "firstsecond"

    def test_existing_captured_context_is_preserved(self):
        """Pre-existing captured_context is preserved; new content appends after."""
        prompt = _PromptStub(initial="seeded:")
        env = _make_env(prompt)

        env.from_string("{% capture_context %}added{% end_capture_context %}").render()

        assert prompt.captured_context == "seeded:added"

    def test_capture_works_with_jinja_variables_in_block(self):
        """Variables and expressions inside the block are rendered before capture."""
        prompt = _PromptStub()
        env = _make_env(prompt)

        rendered = env.from_string(
            "{% capture_context %}name={{ name }}{% end_capture_context %}"
        ).render(name="alice")

        assert rendered == "name=alice"
        assert prompt.captured_context == "name=alice"

    def test_no_prompt_instance_does_not_raise(self):
        """When prompt_instance is missing, content still renders without error."""
        # No prompt_instance global at all.
        env = jinja2.Environment(extensions=[CaptureContextExtension])

        rendered = env.from_string(
            "x{% capture_context %}body{% end_capture_context %}y"
        ).render()

        # Block content still appears in the rendered output.
        assert rendered == "xbodyy"

    def test_prompt_instance_is_none_does_not_raise(self):
        """A prompt_instance set to None is treated like missing — no capture, no error."""
        env = _make_env(None)

        rendered = env.from_string(
            "{% capture_context %}body{% end_capture_context %}"
        ).render()

        assert rendered == "body"

    def test_empty_block_appends_empty_string(self):
        """An empty capture block leaves captured_context unchanged in content."""
        prompt = _PromptStub(initial="orig")
        env = _make_env(prompt)

        env.from_string("{% capture_context %}{% end_capture_context %}").render()

        assert prompt.captured_context == "orig"

    def test_capture_does_not_strip_whitespace_in_block(self):
        """Whitespace inside the block is captured verbatim (no implicit trimming)."""
        prompt = _PromptStub()
        env = _make_env(prompt)

        env.from_string(
            "{% capture_context %}  spaced  {% end_capture_context %}"
        ).render()

        assert prompt.captured_context == "  spaced  "
