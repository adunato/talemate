"""
Arc expansion — expands plan beats into prose and pushes to scene history.
"""

import re
import structlog

import pydantic
from talemate.emit import emit
import talemate.emit.async_signals
from talemate.agents.narrator import NarratorAgentEmission
from talemate.prompts import Prompt
from talemate.scene_message import NarratorMessage, CharacterMessage

from .schema import Beat
from .util import complete_task, emit_plan_updated, get_plan

log = structlog.get_logger("talemate.agents.director.plan.expand")

# Matches any opening block tag leaked into content
# TODO: this can't hardcode the tag style or names -- the template defines it
_LEAKED_TAG_RE = re.compile(r"</?(?:NARRATOR|CHARACTER)\b", re.IGNORECASE)

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


async def revise_narrator_content(narrator, content: str) -> str:
    """Send narrator generated signal so automatic revision can process the content."""
    emission = NarratorAgentEmission(agent=narrator, response=content)
    await talemate.emit.async_signals.get("agent.narrator.generated").send(emission)
    return emission.response


async def critique_expanded_blocks(
    blocks: list[dict], narrator
) -> list[dict]:
    """
    Run a post-expansion critique pass on all blocks to fix cross-beat
    redundancy, intensity monotony, and repeated vocabulary.
    """
    log.info("expand.critique", block_count=len(blocks))

    response, extracted = await Prompt.request(
        "narrator.arc-expand-critique",
        narrator.client,
        "narrate_4096",
        vars={
            "blocks": blocks,
            "max_tokens": narrator.client.max_token_length,
            "response_length": 4096,
        },
    )

    revised = extracted.get("response", [])
    if not revised:
        log.warning("expand.critique.no_blocks_returned, using originals")
        return blocks

    log.info("expand.critique.done", original=len(blocks), revised=len(revised))
    return revised


async def push_and_emit_block(scene, block: dict, narrator) -> int:
    """
    Push a single extracted block to scene history and emit to frontend.

    Runs automatic revision on narrator blocks. Returns word count of the block,
    or 0 if the block was empty/skipped.
    """
    content = block.get("content", "").strip()
    if not content:
        return 0

    if block["type"] == "narrator":
        content = await revise_narrator_content(narrator, content)
        msg = NarratorMessage(content)
        await scene.push_history(msg)
        emit("narrator", msg)
    elif block["type"] == "character":
        char_name = block.get("name", "Unknown")
        msg = CharacterMessage(f"{char_name}: {content}")
        await scene.push_history(msg)
        character = scene.get_character(char_name)
        emit("character", message=msg, character=character)
    else:
        return 0

    return len(content.split())


async def expand_beats(
    scene,
    narrator,
    beats: list[Beat],
    plan_id: str,
    perspective: str,
    director_notes: str = "",
    chunk_size: int = 3,
    chat_id: str | None = None,
) -> tuple[int, int]:
    """
    Expand beats into prose in chunks and push to scene history.

    Chunks are split at tension valleys when possible (deliberate chunking),
    falling back to max chunk_size. Each chunk receives arc-position metadata
    to guide pacing.

    Returns (total_blocks, total_words).
    """
    total_words = 0
    total_blocks = 0
    preceding_text = ""
    all_blocks: list[dict] = []

    # Compute chunks and arc metadata
    chunks = compute_chunks(beats, chunk_size)
    arc_infos = compute_arc_info(chunks, beats)

    # Build a flat index to find following beats across chunk boundaries
    beat_offset = 0

    for chunk_num, (chunk_beats, arc_info) in enumerate(
        zip(chunks, arc_infos), start=1
    ):
        # Following beats: first 2 beats from the next chunk
        following_beats = []
        next_offset = beat_offset + len(chunk_beats)
        if next_offset < len(beats):
            following_beats = beats[next_offset : next_offset + 2]

        log.info(
            "expand.chunk",
            chunk=chunk_num,
            beats=f"{beat_offset + 1}-{beat_offset + len(chunk_beats)}",
            total=len(beats),
            position=arc_info.position,
            tension=f"{arc_info.tension_range[0]:.1f}-{arc_info.tension_range[1]:.1f}",
        )

        # Call the expansion template with retry on malformed output
        max_attempts = 3
        blocks = []
        prompt_vars = {
            "scene": scene,
            "max_tokens": narrator.client.max_token_length,
            "beats": chunk_beats,
            "following_beats": following_beats,
            "preceding_text": preceding_text[-2000:] if preceding_text else "",
            "perspective": perspective,
            "director_notes": director_notes,
            "extra_instructions": narrator.extra_instructions,
            "response_length": 4096,
            "arc_info": arc_info,
        }

        for attempt in range(1, max_attempts + 1):
            response, extracted = await Prompt.request(
                "narrator.arc-expand",
                narrator.client,
                "narrate_4096",
                vars=prompt_vars,
            )

            blocks = extracted.get("response", [])
            if not blocks:
                log.warning("expand.no_blocks", chunk=chunk_num, attempt=attempt)
                continue

            # Validate: check for leaked block tags in content
            has_leaked_tags = any(
                _LEAKED_TAG_RE.search(b.get("content", "")) for b in blocks
            )
            if not has_leaked_tags:
                break

            log.warning("expand.leaked_tags", chunk=chunk_num, attempt=attempt)
            blocks = []

        if not blocks:
            raise RuntimeError(
                f"Expansion failed for chunk {chunk_num} after {max_attempts} attempts. "
                f"The model produced malformed output with leaked block tags. "
                f"This may indicate the model is too weak for structured generation — "
                f"consider using a more capable model."
            )

        all_blocks.extend(blocks)

        # Accumulate preceding text for next chunk
        chunk_text = "\n\n".join(b.get("content", "") for b in blocks)
        preceding_text += "\n\n" + chunk_text

        beat_offset += len(chunk_beats)

    # Post-expansion critique pass
    if len(chunks) > 1 and all_blocks:
        all_blocks = await critique_expanded_blocks(all_blocks, narrator)

    # Push all blocks to scene history and emit to frontend
    for block in all_blocks:
        words = await push_and_emit_block(scene, block, narrator)
        if words:
            total_blocks += 1
            total_words += words

    # Mark all beats as completed
    for beat in beats:
        complete_task(scene, beat.id, plan_id=plan_id)

    # Emit plan update
    plan = get_plan(scene, plan_id)
    if plan:
        emit_plan_updated(plan, chat_id=chat_id)

    return total_blocks, total_words
