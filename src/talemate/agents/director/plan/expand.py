"""
Arc expansion — expands plan beats into prose and pushes to scene history.
"""

import re
import structlog

from talemate.emit import emit
import talemate.emit.async_signals
from talemate.agents.narrator import NarratorAgentEmission
from talemate.prompts import Prompt
from talemate.scene_message import NarratorMessage, CharacterMessage

from .schema import Beat
from .util import complete_task, emit_plan_updated, get_plan

log = structlog.get_logger("talemate.agents.director.plan.expand")

# Matches any opening block tag leaked into content
# XXX: this can't hardcode the tag style or names -- the template defines it
_LEAKED_TAG_RE = re.compile(r"</?(?:NARRATOR|CHARACTER)\b", re.IGNORECASE)


async def revise_narrator_content(narrator, content: str) -> str:
    """Send narrator generated signal so automatic revision can process the content."""
    emission = NarratorAgentEmission(agent=narrator, response=content)
    await talemate.emit.async_signals.get("agent.narrator.generated").send(emission)
    return emission.response


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

    Returns (total_blocks, total_words).
    """
    total_words = 0
    total_blocks = 0
    preceding_text = ""

    for i in range(0, len(beats), chunk_size):
        chunk_beats = beats[i:i + chunk_size]
        following_beats = beats[i + chunk_size:i + chunk_size + 2]
        chunk_num = i // chunk_size + 1

        log.info(
            "expand.chunk",
            chunk=chunk_num,
            beats=f"{i + 1}-{i + len(chunk_beats)}",
            total=len(beats),
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

        # Push blocks to scene history and emit to frontend
        for block in blocks:
            words = await push_and_emit_block(scene, block, narrator)
            if words:
                total_blocks += 1
                total_words += words

        # Accumulate preceding text for next chunk
        chunk_text = "\n\n".join(b.get("content", "") for b in blocks)
        preceding_text += "\n\n" + chunk_text

        # Mark chunk's beats as completed
        for beat in chunk_beats:
            complete_task(scene, beat.id, plan_id=plan_id)

        # Emit plan update
        plan = get_plan(scene, plan_id)
        if plan:
            emit_plan_updated(plan, chat_id=chat_id)

    return total_blocks, total_words
