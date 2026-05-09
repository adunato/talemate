"""Tests for talemate.util.__init__ pure helpers.

Covers count_tokens, limit_tokens, chunk_items_by_tokens, remove_substring_names,
select_best_texts_by_keyword, clean_id, and slugify.
"""

from talemate.scene_message import SceneMessage
from talemate.util import (
    chunk_items_by_tokens,
    clean_id,
    count_tokens,
    limit_tokens,
    remove_substring_names,
    select_best_texts_by_keyword,
    slugify,
)


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


def test_count_tokens_empty_string_is_zero():
    assert count_tokens("") == 0


def test_count_tokens_string_returns_positive_count():
    # tiktoken encoding produces at least one token for short ASCII strings
    n = count_tokens("hello world")
    assert n >= 1
    # And longer strings produce strictly more tokens
    assert count_tokens("hello world goodbye world hello again") > n


def test_count_tokens_list_sums_components():
    """List of strings should count to the sum of individual counts."""
    parts = ["alpha", "beta gamma", "delta"]
    expected = sum(count_tokens(p) for p in parts)
    assert count_tokens(parts) == expected


def test_count_tokens_empty_list_is_zero():
    assert count_tokens([]) == 0


def test_count_tokens_scene_message_uses_str():
    """SceneMessage should be tokenized by its string representation."""
    msg = SceneMessage(message="hello world")
    assert count_tokens(msg) == count_tokens("hello world")


def test_count_tokens_unknown_type_returns_zero():
    """Unknown types log a warning and return 0."""
    assert count_tokens(12345) == 0
    assert count_tokens({"a": 1}) == 0
    assert count_tokens(None) == 0


def test_count_tokens_nested_list():
    """count_tokens recursively sums nested lists of strings."""
    nested = [["alpha", "beta"], "gamma"]
    expected = count_tokens("alpha") + count_tokens("beta") + count_tokens("gamma")
    assert count_tokens(nested) == expected


# ---------------------------------------------------------------------------
# limit_tokens
# ---------------------------------------------------------------------------


def test_limit_tokens_under_limit_returns_input_unchanged():
    text = "line one\nline two\nline three"
    # Plenty of headroom
    assert limit_tokens(text, 1000) == text


def test_limit_tokens_drops_trailing_lines_until_under_limit():
    """When text exceeds the limit, trailing lines should be popped from the end."""
    lines = [f"sentence number {i} in this paragraph" for i in range(20)]
    text = "\n".join(lines)
    # Use the same per-line counting strategy that the function uses internally
    full_tokens = count_tokens(lines)
    target = full_tokens // 2
    result = limit_tokens(text, target)
    # Function uses list-based count internally; verify the surviving lines are within budget
    surviving_lines = result.split("\n")
    assert count_tokens(surviving_lines) <= target
    # Result must still be a prefix of the original (lines popped from the end)
    assert text.startswith(result)
    # First line must be preserved
    assert surviving_lines[0] == lines[0]
    # Some lines should have been dropped
    assert len(surviving_lines) < len(lines)


def test_limit_tokens_returns_empty_when_first_line_exceeds_limit():
    """If even the first line is too long, all lines are popped."""
    text = "this is a fairly long line of words\nanother line"
    # Set limit too small so even one line cannot fit
    result = limit_tokens(text, 0)
    assert result == ""


# ---------------------------------------------------------------------------
# chunk_items_by_tokens
# ---------------------------------------------------------------------------


def test_chunk_items_by_tokens_groups_items_under_limit():
    """Multiple small items should pack into a chunk that fits the limit."""
    items = ["one", "two", "three", "four"]
    # Each item is ~1 token, so any reasonable limit packs them together
    chunks = list(chunk_items_by_tokens(items, max_tokens=100))
    assert chunks == [items]


def test_chunk_items_by_tokens_starts_new_chunk_when_full():
    """When adding an item would exceed the limit, a new chunk begins."""
    items = ["alpha beta", "gamma delta", "epsilon zeta"]
    # Set max_tokens to roughly the size of one item so each item gets its own chunk
    max_tokens = max(count_tokens(i) for i in items)
    chunks = list(chunk_items_by_tokens(items, max_tokens=max_tokens))
    # Every item should be present, distributed across chunks
    flattened = [item for chunk in chunks for item in chunk]
    assert flattened == items
    # No chunk should exceed the limit
    for chunk in chunks:
        assert count_tokens(chunk) <= max_tokens
    # Multiple chunks since each item nearly fills the budget
    assert len(chunks) >= 2


def test_chunk_items_by_tokens_oversized_item_yielded_alone():
    """An item larger than max_tokens is yielded as its own chunk."""
    long_text = " ".join(["word"] * 200)  # large
    items = ["small one", long_text, "small two"]
    chunks = list(chunk_items_by_tokens(items, max_tokens=5))
    # The long item appears as a single-item chunk (size 1)
    oversized_chunks = [c for c in chunks if c == [long_text]]
    assert len(oversized_chunks) == 1
    # All original items appear in the output
    flattened = [i for c in chunks for i in c]
    assert flattened == items


def test_chunk_items_by_tokens_filters_empty_when_filter_empty_true():
    items = ["one", "", "  ", "two", None]
    chunks = list(chunk_items_by_tokens(items, max_tokens=100))
    # Only "one" and "two" survive
    assert chunks == [["one", "two"]]


def test_chunk_items_by_tokens_keeps_empty_when_filter_empty_false():
    items = ["one", "", "two"]
    chunks = list(chunk_items_by_tokens(items, max_tokens=100, filter_empty=False))
    flattened = [i for c in chunks for i in c]
    assert flattened == items


def test_chunk_items_by_tokens_empty_input_yields_nothing():
    assert list(chunk_items_by_tokens([], max_tokens=100)) == []
    # All-empty after filter -> nothing
    assert list(chunk_items_by_tokens(["", "  ", None], max_tokens=100)) == []


def test_chunk_items_by_tokens_custom_count_fn():
    """count_fn can be overridden; verify each item counted as 1."""
    items = ["a", "bb", "ccc", "dddd"]
    chunks = list(chunk_items_by_tokens(items, max_tokens=2, count_fn=lambda _: 1))
    # Each chunk should hold up to 2 items
    assert chunks == [["a", "bb"], ["ccc", "dddd"]]


# ---------------------------------------------------------------------------
# remove_substring_names
# ---------------------------------------------------------------------------


def test_remove_substring_names_empty_input():
    assert remove_substring_names([]) == []


def test_remove_substring_names_drops_substring_of_longer_name():
    """When a shorter name appears as a whole word in a longer name, drop it."""
    names = ["julia", "julia smith"]
    assert remove_substring_names(names) == ["julia smith"]


def test_remove_substring_names_preserves_original_order():
    """The function should preserve the input order of surviving names."""
    names = ["julia smith", "bob", "julia"]
    # "julia" is dropped; "julia smith" and "bob" remain in original order
    assert remove_substring_names(names) == ["julia smith", "bob"]


def test_remove_substring_names_does_not_match_partial_words():
    """'jul' inside 'julia' should not be considered a substring (whole-word match only)."""
    names = ["jul", "julia"]
    # 'jul' is not a whole word inside 'julia', so it should be kept
    result = remove_substring_names(names)
    assert "jul" in result
    assert "julia" in result


def test_remove_substring_names_case_insensitive():
    names = ["JULIA", "Julia Smith"]
    # 'JULIA' is a whole word inside 'Julia Smith' (case-insensitive)
    result = remove_substring_names(names)
    assert result == ["Julia Smith"]


def test_remove_substring_names_skips_blank_entries():
    names = ["  ", "alice", "alice cooper"]
    result = remove_substring_names(names)
    assert "alice" not in result
    assert "alice cooper" in result
    assert "  " not in result


# ---------------------------------------------------------------------------
# select_best_texts_by_keyword
# ---------------------------------------------------------------------------


def test_select_best_texts_by_keyword_empty_returns_empty():
    assert select_best_texts_by_keyword([], "anything", 100) == []


def test_select_best_texts_by_keyword_no_keyword_returns_input():
    """Falsy keyword short-circuits and returns the original list."""
    texts = ["one", "two"]
    assert select_best_texts_by_keyword(texts, "", 100) == texts


def test_select_best_texts_by_keyword_filters_texts_without_keyword():
    texts = [
        "Alice walked into the room.",
        "The weather is nice today.",
        "Alice loves to read books.",
    ]
    result = select_best_texts_by_keyword(texts, "alice", max_token_length=200)
    # Only texts containing "alice" should remain
    for selected in result:
        assert "alice" in selected.lower()
    assert len(result) == 2


def test_select_best_texts_by_keyword_orders_by_score_desc():
    texts = [
        "Alice mentioned once.",
        "Alice and Alice and Alice all spoke.",  # 3 occurrences
        "Alice went to Alice's house.",  # 2 occurrences
    ]
    result = select_best_texts_by_keyword(texts, "alice", max_token_length=1000)
    # Highest-occurrence text should be first
    assert result[0] == "Alice and Alice and Alice all spoke."
    assert result[1] == "Alice went to Alice's house."
    assert result[2] == "Alice mentioned once."


def test_select_best_texts_by_keyword_respects_token_budget():
    """Selection stops once the chunk_size budget is filled."""
    texts = [f"alice {i} alice" for i in range(50)]
    # tight budget — should select fewer than all 50
    result = select_best_texts_by_keyword(
        texts, "alice", max_token_length=20, chunk_size_ratio=1.0
    )
    assert 0 < len(result) < 50


def test_select_best_texts_by_keyword_whole_word_match_only():
    """Substring of another word should not count as a keyword occurrence."""
    texts = [
        "alicewonder is not alice",  # 1 whole-word "alice"
        "the cat is here",  # 0
    ]
    result = select_best_texts_by_keyword(texts, "alice", max_token_length=200)
    assert result == ["alicewonder is not alice"]


def test_select_best_texts_by_keyword_skips_blank_texts():
    # blank/whitespace entries are skipped before scoring
    # Note: None would error in .strip() — verify only non-None blanks are skipped
    texts_clean = ["", "   ", "alice spoke"]
    result = select_best_texts_by_keyword(texts_clean, "alice", max_token_length=200)
    assert result == ["alice spoke"]


# ---------------------------------------------------------------------------
# clean_id
# ---------------------------------------------------------------------------


def test_clean_id_removes_special_characters():
    assert clean_id("hello!@#$%^&*()world") == "helloworld"


def test_clean_id_preserves_allowed_characters():
    assert clean_id("Foo_Bar-Baz 123") == "Foo_Bar-Baz 123"


def test_clean_id_empty_input():
    assert clean_id("") == ""


def test_clean_id_only_special_characters():
    assert clean_id("@#$%") == ""


def test_clean_id_unicode_dropped():
    """Unicode characters outside a-zA-Z0-9_- and space are removed."""
    assert clean_id("café 42") == "caf 42"


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_basic_label():
    assert slugify("My vLLM Local") == "my-vllm-local"


def test_slugify_only_separators_returns_empty():
    assert slugify("  ___  ") == ""


def test_slugify_strips_punctuation_runs():
    assert slugify("Voice 1!") == "voice-1"


def test_slugify_collapses_consecutive_specials():
    assert slugify("a   b!!!c") == "a-b-c"


def test_slugify_empty_input():
    assert slugify("") == ""


def test_slugify_none_safe():
    """slugify guards against None input."""
    assert slugify(None) == ""


def test_slugify_already_slug():
    assert slugify("already-a-slug") == "already-a-slug"


def test_slugify_strips_leading_trailing_dashes():
    assert slugify("-foo-bar-") == "foo-bar"
