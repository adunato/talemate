"""String normalization helpers shared across the app."""

__all__ = [
    "normalize_name",
]


def normalize_name(raw: str | None, max_length: int) -> str | None:
    """
    Normalize a free-form user-entered name.

    Trims whitespace, collapses empty / whitespace-only input to ``None``, and
    truncates to at most ``max_length`` characters.

    Args:
        raw: The input string, or ``None``.
        max_length: Maximum length of the returned string.

    Returns:
        The normalized name, or ``None`` if the input was empty.
    """
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    return trimmed[:max_length]
