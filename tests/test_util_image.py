"""Tests for talemate.util.image.

Covers fix_unquoted_keys (pure regex helper), read_metadata_from_png_text
(parses PNG tEXt chunks), and chara_read (top-level dispatcher). The tests
build small in-memory PNG fixtures with PIL — no network IO is performed.

WebP/EXIF code paths and the explicit "comment" PNG branch are skipped because
they require either piexif manipulation (37510 EXIF tag) or WebP-specific
encoding that pulls in heavier IO machinery.
"""

import base64
import json

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from talemate.util.image import (
    chara_read,
    extract_metadata,
    fix_unquoted_keys,
    read_metadata_from_png_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_png_with_text(
    path,
    *,
    chara: str | None = None,
    ccv3: str | None = None,
    comment: str | None = None,
    extra: dict | None = None,
):
    """Create a tiny PNG at *path* with optional tEXt entries.

    `chara`, `ccv3`, and `comment` should be raw strings (already base64-
    encoded if appropriate). The function creates a 4x4 red image so PIL is
    happy.
    """
    img = Image.new("RGB", (4, 4), color="red")
    info = PngInfo()
    if chara is not None:
        info.add_text("chara", chara)
    if ccv3 is not None:
        info.add_text("ccv3", ccv3)
    if comment is not None:
        info.add_text("comment", comment)
    if extra:
        for key, value in extra.items():
            info.add_text(key, value)
    img.save(str(path), format="PNG", pnginfo=info)


def _b64(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# fix_unquoted_keys
# ---------------------------------------------------------------------------


def test_fix_unquoted_keys_quotes_object_keys():
    """Bare keys after '{' or ',' should be wrapped in double quotes."""
    src = '{name: "Alice", age: 30}'
    out = fix_unquoted_keys(src)
    # Both keys should be quoted; output must be valid JSON
    parsed = json.loads(out)
    assert parsed == {"name": "Alice", "age": 30}


def test_fix_unquoted_keys_leaves_already_quoted_keys_untouched():
    src = '{"name": "Alice", "age": 30}'
    out = fix_unquoted_keys(src)
    assert json.loads(out) == {"name": "Alice", "age": 30}


def test_fix_unquoted_keys_handles_nested_objects():
    src = '{outer: {inner: 42, other: "x"}}'
    out = fix_unquoted_keys(src)
    parsed = json.loads(out)
    assert parsed == {"outer": {"inner": 42, "other": "x"}}


def test_fix_unquoted_keys_does_not_quote_inside_string_values():
    """Bare-word patterns appearing in regular string content stay unchanged."""
    # The pattern requires preceding '{' or ',' so 'value: x' inside a string is unaffected
    src = '{key: "this is value: x"}'
    out = fix_unquoted_keys(src)
    parsed = json.loads(out)
    assert parsed == {"key": "this is value: x"}


def test_fix_unquoted_keys_preserves_value_separators():
    """Make sure ': ' between key and value is preserved (no key collision)."""
    src = "{a: 1, b: 2, c: 3}"
    out = fix_unquoted_keys(src)
    parsed = json.loads(out)
    assert parsed == {"a": 1, "b": 2, "c": 3}


# ---------------------------------------------------------------------------
# read_metadata_from_png_text
# ---------------------------------------------------------------------------


def test_read_metadata_from_png_text_chara_chunk(tmp_path):
    """A PNG with only a 'chara' tEXt chunk should be decoded."""
    payload = {"name": "TestChar", "spec": "chara_card_v2", "data": {"id": 1}}
    img_path = tmp_path / "chara_only.png"
    _write_png_with_text(img_path, chara=_b64(payload))

    result = read_metadata_from_png_text(str(img_path))
    assert result == payload


def test_read_metadata_from_png_text_ccv3_chunk(tmp_path):
    """A PNG with only a 'ccv3' tEXt chunk should be decoded."""
    payload = {"spec": "chara_card_v3", "name": "V3Char"}
    img_path = tmp_path / "ccv3_only.png"
    _write_png_with_text(img_path, ccv3=_b64(payload))

    result = read_metadata_from_png_text(str(img_path))
    assert result == payload


def test_read_metadata_from_png_text_prefers_ccv3(tmp_path):
    """Per v3 spec, when both 'ccv3' and 'chara' exist, ccv3 wins."""
    chara_payload = {"name": "OldFormat"}
    ccv3_payload = {"name": "NewFormat", "spec": "chara_card_v3"}
    img_path = tmp_path / "both.png"
    _write_png_with_text(
        img_path,
        chara=_b64(chara_payload),
        ccv3=_b64(ccv3_payload),
    )

    result = read_metadata_from_png_text(str(img_path))
    assert result == ccv3_payload


def test_read_metadata_from_png_text_no_metadata_raises(tmp_path):
    """A PNG without chara/ccv3 chunks should raise ValueError."""
    img_path = tmp_path / "plain.png"
    _write_png_with_text(img_path)

    with pytest.raises(ValueError, match="No character metadata"):
        read_metadata_from_png_text(str(img_path))


def test_read_metadata_from_png_text_skips_unrelated_text_chunks(tmp_path):
    """tEXt chunks with unrelated keywords should be ignored."""
    payload = {"name": "WithExtras"}
    img_path = tmp_path / "extras.png"
    _write_png_with_text(
        img_path,
        chara=_b64(payload),
        extra={"author": "someone", "title": "irrelevant"},
    )

    result = read_metadata_from_png_text(str(img_path))
    assert result == payload


# ---------------------------------------------------------------------------
# chara_read (PNG path)
# ---------------------------------------------------------------------------


def test_chara_read_png_chara_info_chunk(tmp_path):
    """When PIL reads the 'chara' key from img.info, chara_read decodes it."""
    payload = {"name": "InfoChar", "data": {"x": 1}}
    img_path = tmp_path / "info_chara.png"
    _write_png_with_text(img_path, chara=_b64(payload))

    result = chara_read(str(img_path))
    assert result == payload


def test_chara_read_png_ccv3_info_chunk_takes_precedence(tmp_path):
    """ccv3 in img.info should be preferred over chara, per v3 spec."""
    chara_payload = {"name": "V2"}
    ccv3_payload = {"name": "V3", "spec": "chara_card_v3"}
    img_path = tmp_path / "info_both.png"
    _write_png_with_text(
        img_path,
        chara=_b64(chara_payload),
        ccv3=_b64(ccv3_payload),
    )

    result = chara_read(str(img_path))
    assert result == ccv3_payload


def test_chara_read_png_comment_chunk_returns_decoded_string(tmp_path):
    """When only 'comment' is present, chara_read returns the decoded string."""
    raw = "this is a comment payload"
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    img_path = tmp_path / "comment_only.png"
    _write_png_with_text(img_path, comment=encoded)

    result = chara_read(str(img_path))
    # Comment branch returns a string, not a dict
    assert result == raw


def test_chara_read_png_with_no_metadata_returns_false(tmp_path):
    """A bare PNG with no recognized metadata returns False."""
    img_path = tmp_path / "bare.png"
    _write_png_with_text(img_path)

    result = chara_read(str(img_path))
    assert result is False


def test_chara_read_unknown_format_returns_none(tmp_path):
    """Unknown input_format other than png/webp returns None."""
    # Build a minimal png file (chara_read must open the file before
    # selecting the branch since both webp and png read first).
    img_path = tmp_path / "f.png"
    _write_png_with_text(img_path)

    result = chara_read(str(img_path), input_format="bmp")
    assert result is None


def test_chara_read_format_inferred_from_extension(tmp_path):
    """No explicit format -> .webp triggers webp branch, otherwise png."""
    # Use a normal .png file but rename to .webp-not-really to verify the
    # extension-based branch selection. The webp branch reads EXIF, which
    # for a non-webp file will simply be empty -> "No chara data" -> False.
    src = tmp_path / "fake.webp"
    # Save a plain PNG under .webp suffix; PIL still saves as PNG bytes
    img = Image.new("RGB", (4, 4))
    img.save(str(src), format="PNG")

    result = chara_read(str(src))
    # webp branch with no EXIF tag 37510 -> returns False
    assert result is False


# ---------------------------------------------------------------------------
# extract_metadata (thin wrapper)
# ---------------------------------------------------------------------------


def test_extract_metadata_delegates_to_chara_read(tmp_path):
    payload = {"name": "Wrapper"}
    img_path = tmp_path / "wrap.png"
    _write_png_with_text(img_path, chara=_b64(payload))

    # extract_metadata ignores its second arg and calls chara_read
    assert extract_metadata(str(img_path), "png") == payload
    assert extract_metadata(str(img_path), "anything-here") == payload
