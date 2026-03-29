"""
Arc expansion — expands plan beats into prose and pushes to scene history.
"""

import structlog

from talemate.emit import emit
import talemate.emit.async_signals
from talemate.agents.narrator import NarratorAgentEmission
from talemate.prompts import Prompt
from talemate.scene_message import NarratorMessage, CharacterMessage

from .schema import Beat
from .util import complete_task, emit_plan_updated, get_plan

log = structlog.get_logger("talemate.agents.director.plan.expand")


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

        log.info(
            "expand.chunk",
            chunk=i // chunk_size + 1,
            beats=f"{i + 1}-{i + len(chunk_beats)}",
            total=len(beats),
        )

        # Call the expansion template
        response, extracted = await Prompt.request(
            "narrator.arc-expand",
            narrator.client,
            "narrate_4096",
            vars={
                "scene": scene,
                "max_tokens": narrator.client.max_token_length,
                "beats": chunk_beats,
                "following_beats": following_beats,
                "preceding_text": preceding_text[-2000:] if preceding_text else "",
                "perspective": perspective,
                "director_notes": director_notes,
                "extra_instructions": narrator.extra_instructions,
                "response_length": 4096,
            },
        )

        blocks = extracted.get("response", [])
        if not blocks:
            log.warning("expand.no_blocks", chunk=i // chunk_size + 1)
            continue

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
