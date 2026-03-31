"""
Arc expansion — pure functions for chunking and arc metadata computation.

The expand pipeline methods live on PlanMixin (mixin.py).
"""

import re

import pydantic

from .schema import Beat

# Matches any opening block tag leaked into content
_LEAKED_TAG_RE = re.compile(r"</?(?:NARRATOR|CHARACTER)\b", re.IGNORECASE)


def has_leaked_tags(blocks: list[dict]) -> bool:
    """Check if any extracted block contains raw block tags in its content."""
    return any(_LEAKED_TAG_RE.search(b.get("content", "")) for b in blocks)


# Minimum beats per chunk when splitting at tension valleys
MIN_CHUNK_BEATS = 3


class ChunkArcInfo(pydantic.BaseModel):
    """Arc metadata for a single expansion chunk."""

    position: str  # "opening", "rising", "climax", "climax_and_resolution", "resolution", "full"
    chunk_index: int
    total_chunks: int
    tension_range: tuple[float, float]  # (min, max) tension in this chunk
    has_peak: bool  # whether the arc's highest-tension beat is in this chunk


def compute_chunks(beats: list[Beat], max_chunk_size: int) -> list[list[Beat]]:
    """
    Split beats into chunks at tension valleys, respecting max_chunk_size.

    Prefers splitting where tension drops (natural scene breaks) over
    arbitrary even splits. Falls back to max_chunk_size if no valleys found.
    """
    if len(beats) <= max_chunk_size:
        return [beats]

    chunks: list[list[Beat]] = []
    current: list[Beat] = []

    for i, beat in enumerate(beats):
        current.append(beat)

        if len(current) < MIN_CHUNK_BEATS:
            continue

        # Check if we must split (hit max size)
        if len(current) >= max_chunk_size:
            chunks.append(current)
            current = []
            continue

        # Check for tension valley: current beat's tension > next beat's tension
        # and we have enough beats accumulated
        if i + 1 < len(beats) and beat.tension > beats[i + 1].tension:
            # Only split if remaining beats can form a valid chunk
            remaining = len(beats) - (i + 1)
            if remaining >= MIN_CHUNK_BEATS:
                chunks.append(current)
                current = []

    if current:
        # If the leftover is too small, merge with the last chunk
        if chunks and len(current) < MIN_CHUNK_BEATS:
            chunks[-1].extend(current)
        else:
            chunks.append(current)

    return chunks


def compute_arc_info(
    chunks: list[list[Beat]], all_beats: list[Beat]
) -> list[ChunkArcInfo]:
    """Derive arc-position metadata for each chunk."""
    total_chunks = len(chunks)
    peak_tension = max(b.tension for b in all_beats)

    infos: list[ChunkArcInfo] = []
    for idx, chunk in enumerate(chunks):
        tensions = [b.tension for b in chunk]
        t_min, t_max = min(tensions), max(tensions)
        has_peak = t_max >= peak_tension - 0.05  # within 0.05 of the global peak

        # Check if tension trends downward at the end of this chunk
        winds_down = len(tensions) >= 3 and tensions[-1] < t_max - 0.15

        # Determine position
        if total_chunks == 1:
            position = "full"
        elif idx == 0:
            position = "opening"
        elif has_peak and winds_down:
            position = "climax_and_resolution"
        elif has_peak:
            position = "climax"
        elif idx == total_chunks - 1:
            position = "resolution"
        else:
            position = "rising"

        infos.append(
            ChunkArcInfo(
                position=position,
                chunk_index=idx,
                total_chunks=total_chunks,
                tension_range=(t_min, t_max),
                has_peak=has_peak,
            )
        )

    return infos
