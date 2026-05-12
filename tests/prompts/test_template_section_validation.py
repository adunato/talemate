"""
Validates that all prompt templates have properly closed sections.

Every <|SECTION:NAME|> must have a corresponding <|CLOSE_SECTION|> before
the next <|SECTION:...|> or end of file. This is required for XML-style
sectioning to work correctly, since XML needs explicit closing tags.
"""

import re
import pytest
from pathlib import Path

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "talemate"
    / "prompts"
    / "templates"
)

SECTION_OPEN_RE = re.compile(r"<\|SECTION:([^|]+)\|>")
SECTION_CLOSE_RE = re.compile(r"<\|CLOSE_SECTION\|>")

# Jinja calls that emit <|BOT|> into the rendered output
BOT_TOKEN_RE = re.compile(
    r"\{\{.*?("
    r"set_prepared_response|set_data_response|set_json_response"
    r"|set_prepared_response_random|bot_token"
    r").*?\}\}"
)

# Templates rendered without sectioning applied (raw markers pass through
# to the including template which handles closing). Paths relative to TEMPLATES_DIR.
SKIP_SECTION_VALIDATION = {
    "focal/instructions.jinja2",
}


def find_unclosed_sections(filepath: Path) -> list[dict]:
    """
    Parse a template file and return a list of unclosed sections.

    A section is unclosed if a <|SECTION:X|> is followed by another
    <|SECTION:Y|> or EOF without an intervening <|CLOSE_SECTION|>.
    """
    text = filepath.read_text()
    lines = text.split("\n")

    unclosed = []
    current_section = None
    current_section_line = None

    for line_num, line in enumerate(lines, start=1):
        open_match = SECTION_OPEN_RE.search(line)
        close_match = SECTION_CLOSE_RE.search(line)

        if open_match and close_match:
            # Both on same line - check order
            if line.index("<|SECTION:") < line.index("<|CLOSE_SECTION|>"):
                # Open then close on same line - section is self-closed
                # But first close previous if any
                if current_section:
                    unclosed.append(
                        {
                            "section": current_section,
                            "opened_at": current_section_line,
                            "reason": f"implicitly closed by new section at line {line_num}",
                        }
                    )
                current_section = None
                current_section_line = None
            else:
                # Close then open on same line
                current_section = open_match.group(1)
                current_section_line = line_num
            continue

        if open_match:
            if current_section:
                unclosed.append(
                    {
                        "section": current_section,
                        "opened_at": current_section_line,
                        "reason": f"implicitly closed by new section '{open_match.group(1)}' at line {line_num}",
                    }
                )
            current_section = open_match.group(1)
            current_section_line = line_num
            continue

        if close_match:
            current_section = None
            current_section_line = None

    # Check for section still open at EOF
    if current_section:
        unclosed.append(
            {
                "section": current_section,
                "opened_at": current_section_line,
                "reason": "still open at end of file",
            }
        )

    return unclosed


def collect_template_files() -> list[Path]:
    """Collect all jinja2 template files."""
    return sorted(TEMPLATES_DIR.rglob("*.jinja2"))


def test_all_sections_explicitly_closed():
    """Every <|SECTION:X|> must have an explicit <|CLOSE_SECTION|>."""
    template_files = collect_template_files()
    assert template_files, f"No template files found in {TEMPLATES_DIR}"

    failures = []

    for filepath in template_files:
        rel_path = filepath.relative_to(TEMPLATES_DIR)
        if str(rel_path) in SKIP_SECTION_VALIDATION:
            continue
        unclosed = find_unclosed_sections(filepath)
        if unclosed:
            for issue in unclosed:
                failures.append(
                    f"  {rel_path}:{issue['opened_at']} - "
                    f"section '{issue['section']}' {issue['reason']}"
                )

    if failures:
        msg = f"Found {len(failures)} unclosed section(s):\n" + "\n".join(failures)
        pytest.fail(msg)


def find_bot_token_inside_sections(filepath: Path) -> list[dict]:
    """
    Find cases where bot_token / set_prepared_response / set_data_response
    appears inside a section (between SECTION open and CLOSE_SECTION).

    When using XML sectioning, the closing tag would end up in the assistant
    prefill, corrupting the LLM's response start.
    """
    text = filepath.read_text()
    lines = text.split("\n")

    issues = []
    current_section = None
    current_section_line = None

    for line_num, line in enumerate(lines, start=1):
        open_match = SECTION_OPEN_RE.search(line)
        close_match = SECTION_CLOSE_RE.search(line)

        if open_match:
            current_section = open_match.group(1)
            current_section_line = line_num
        elif close_match:
            current_section = None
            current_section_line = None

        if current_section and BOT_TOKEN_RE.search(line):
            issues.append(
                {
                    "section": current_section,
                    "section_line": current_section_line,
                    "bot_line": line_num,
                    "call": BOT_TOKEN_RE.search(line).group(1),
                }
            )

    return issues


def test_no_bot_token_inside_sections():
    """Bot token / prepared response calls must not appear inside sections.

    When using XML sectioning, the closing tag (e.g. </TASK>) would end up
    in the assistant prefill after the <|BOT|> marker, corrupting the
    LLM's response start.
    """
    template_files = collect_template_files()
    assert template_files, f"No template files found in {TEMPLATES_DIR}"

    failures = []

    for filepath in template_files:
        rel_path = filepath.relative_to(TEMPLATES_DIR)
        if str(rel_path) in SKIP_SECTION_VALIDATION:
            continue
        issues = find_bot_token_inside_sections(filepath)
        if issues:
            for issue in issues:
                failures.append(
                    f"  {rel_path}:{issue['bot_line']} - "
                    f"{issue['call']}() inside section '{issue['section']}' "
                    f"(opened at line {issue['section_line']})"
                )

    if failures:
        msg = (
            f"Found {len(failures)} bot token/prepared response call(s) inside sections.\n"
            f"Move the call after <|CLOSE_SECTION|> or remove the enclosing section.\n"
            + "\n".join(failures)
        )
        pytest.fail(msg)
