"""Tests for talemate.util.diff.

Covers dmp_inline_diff (HTML diff with span markup) and plain_text_diff
(bracket-marker diff).
"""

from talemate.util.diff import dmp_inline_diff, plain_text_diff


# ---------------------------------------------------------------------------
# dmp_inline_diff
# ---------------------------------------------------------------------------


def test_dmp_inline_diff_identical_returns_unwrapped_text():
    """Identical inputs should yield text without any diff markup."""
    text = "hello world"
    result = dmp_inline_diff(text, text)
    assert result == text
    assert "diff-delete" not in result
    assert "diff-insert" not in result


def test_dmp_inline_diff_pure_insertion_marks_inserted_chunk():
    """When text2 adds content, the new chunk is wrapped in diff-insert."""
    result = dmp_inline_diff("hello", "hello world")
    assert "diff-insert" in result
    assert "diff-delete" not in result
    # Original text appears unwrapped, insertion appears in span
    assert "hello" in result
    assert '<span class="diff-insert">' in result
    assert " world</span>" in result


def test_dmp_inline_diff_pure_deletion_marks_deleted_chunk():
    """When text2 removes content, the missing chunk is wrapped in diff-delete."""
    result = dmp_inline_diff("hello world", "hello")
    assert "diff-delete" in result
    assert "diff-insert" not in result
    assert '<span class="diff-delete">' in result
    assert " world</span>" in result


def test_dmp_inline_diff_replacement_has_both_delete_and_insert():
    """A replacement should produce both delete and insert spans."""
    result = dmp_inline_diff("hello world", "hello there")
    assert '<span class="diff-delete">' in result
    assert '<span class="diff-insert">' in result


def test_dmp_inline_diff_html_special_chars_are_escaped():
    """HTML-special characters in the text must be escaped in the output."""
    text1 = "before"
    text2 = "before <script>alert('x')</script> & more"
    result = dmp_inline_diff(text1, text2)
    # Raw HTML characters must NOT appear in the output
    assert "<script>" not in result
    assert "alert('x')" in result  # quotes are not escaped
    # Escaped versions should appear instead
    assert "&lt;script&gt;" in result
    assert "&amp;" in result


def test_dmp_inline_diff_ampersand_escaped_first():
    """An ampersand in the input should appear escaped, not corrupted by later replacements."""
    result = dmp_inline_diff("a", "a & b")
    # & should be escaped to &amp; — make sure we don't double-escape
    assert "&amp;" in result
    assert "&amp;amp;" not in result


def test_dmp_inline_diff_empty_to_text_is_pure_insertion():
    result = dmp_inline_diff("", "new content")
    assert '<span class="diff-insert">new content</span>' == result


def test_dmp_inline_diff_text_to_empty_is_pure_deletion():
    result = dmp_inline_diff("old content", "")
    assert '<span class="diff-delete">old content</span>' == result


def test_dmp_inline_diff_empty_to_empty_is_empty():
    assert dmp_inline_diff("", "") == ""


# ---------------------------------------------------------------------------
# plain_text_diff
# ---------------------------------------------------------------------------


def test_plain_text_diff_identical_returns_input():
    text = "the quick brown fox"
    assert plain_text_diff(text, text) == text


def test_plain_text_diff_insertion_uses_plus_brackets():
    """An insertion should be wrapped in [+ ... +] markers."""
    result = plain_text_diff("hello", "hello world")
    assert "[+" in result
    assert "+]" in result
    assert "[-" not in result
    # Specifically, the inserted part is " world"
    assert "[+ world+]" in result


def test_plain_text_diff_deletion_uses_minus_brackets():
    """A deletion should be wrapped in [- ... -] markers."""
    result = plain_text_diff("hello world", "hello")
    assert "[-" in result
    assert "-]" in result
    assert "[+" not in result
    assert "[- world-]" in result


def test_plain_text_diff_replacement_has_both_markers():
    result = plain_text_diff("hello world", "hello there")
    assert "[-" in result and "-]" in result
    assert "[+" in result and "+]" in result


def test_plain_text_diff_does_not_html_escape():
    """plain_text_diff should NOT html-escape its inputs."""
    result = plain_text_diff("a", "a <b>")
    # raw HTML characters must remain
    assert "<b>" in result
    assert "&lt;" not in result


def test_plain_text_diff_empty_inputs():
    assert plain_text_diff("", "") == ""
    assert plain_text_diff("", "abc") == "[+abc+]"
    assert plain_text_diff("abc", "") == "[-abc-]"
