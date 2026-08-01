"""Derive podcast chapters from the generated script and write them into the MP3.

The TTS step does not report per-line timings, so chapter start times are estimated by
distributing the measured speech duration across the script in proportion to the number of
spoken characters before each line. Speaking rate is near-constant within an episode, so
this lands close enough for chapter navigation.
"""

import json
import os

from google import genai
from google.genai import types

from podcast_creator.audio import (INTRO_LEAD_MS, OUTRO_TAIL_MS, add_chapters_to_mp3,
                                   get_audio_duration_ms)
from podcast_creator.config import Configuration
from podcast_creator.logger import logger

# Segmenting an existing script is a light task, so it does not need the Pro model.
CHAPTER_MODEL = "gemini-3.6-flash"

MIN_CHAPTERS = 3
MAX_CHAPTERS = 10

CHAPTER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "chapters": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "start_line": {"type": "INTEGER"},
                },
                "required": ["title", "start_line"],
            },
        }
    },
    "required": ["chapters"],
}


def _spoken_lines(podcast_text: str) -> list:
    """Split the script into (speaker, spoken_text) pairs, dropping the speaker prefix
    because it is never read aloud and so contributes no audio time."""
    lines = []
    for raw in podcast_text.splitlines():
        if not raw.strip():
            continue
        speaker, _, spoken = raw.partition(":")
        lines.append((speaker.strip(), spoken.strip() if spoken else raw.strip()))
    return lines


def generate_chapter_titles(podcast_text: str, configuration: Configuration) -> list:
    """Ask the model to segment the script into topical chapters.

    Returns a list of {"title": str, "start_line": int}, always starting at line 0.
    Returns an empty list if the model call fails - chapters are a nice-to-have and must
    never break episode production.
    """
    lines = _spoken_lines(podcast_text)
    if len(lines) < MIN_CHAPTERS:
        logger.warning(f"Script has only {len(lines)} lines; skipping chapter generation")
        return []

    numbered = "\n".join(f"[{i}] {speaker}: {text}" for i, (speaker, text) in enumerate(lines))
    prompt = (
        f"Below is a podcast script in {configuration.output_language}. Each line is prefixed "
        f"with its index in square brackets.\n\n"
        f"Split the episode into between {MIN_CHAPTERS} and {MAX_CHAPTERS} chapters, one per "
        f"major topic, in the order they are discussed.\n"
        f"Rules:\n"
        f"- The first chapter MUST have start_line 0 and should cover the intro.\n"
        f"- start_line is the index of the line where that chapter begins.\n"
        f"- start_line values must be strictly increasing.\n"
        f"- Give each chapter a short descriptive title in {configuration.output_language}, "
        f"at most 60 characters, with no numbering prefix and no quotes.\n"
        f"- The closing/goodbye section should be part of the last chapter, not its own chapter.\n\n"
        f"SCRIPT:\n{numbered}"
    )

    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=CHAPTER_MODEL,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CHAPTER_SCHEMA,
                temperature=0.2,
            ),
        )
        chapters = json.loads(response.text)["chapters"]
    except Exception as e:
        logger.error(f"Chapter generation failed, continuing without chapters: {e}")
        return []

    # Keep only sane, strictly increasing, in-range entries.
    cleaned, last_line = [], -1
    for chapter in chapters:
        start = int(chapter.get("start_line", -1))
        title = (chapter.get("title") or "").strip()
        if not title or start <= last_line or start >= len(lines):
            logger.warning(f"Dropping malformed chapter entry: {chapter}")
            continue
        cleaned.append({"title": title, "start_line": start})
        last_line = start

    if not cleaned:
        return []

    # The first chapter must cover the start of the audio, whatever the model returned.
    cleaned[0]["start_line"] = 0
    logger.info(f"Generated {len(cleaned)} chapters: {[c['title'] for c in cleaned]}")
    return cleaned


def build_chapter_timings(podcast_text: str, chapters: list, speech_duration_ms: int,
                          speech_offset_ms: int) -> list:
    """Convert chapter start lines into (title, start_ms, end_ms) tuples.

    `speech_duration_ms` is the length of the spoken audio and `speech_offset_ms` is how far
    into the final file that speech begins (the intro music runs before it).
    """
    lines = _spoken_lines(podcast_text)
    if not lines or not chapters:
        return []

    # Cumulative spoken characters before each line, used as a proxy for elapsed time.
    cumulative, total = [], 0
    for _, text in lines:
        cumulative.append(total)
        total += len(text)
    if total == 0:
        return []

    timings = []
    for i, chapter in enumerate(chapters):
        start_ms = speech_offset_ms + int(speech_duration_ms * cumulative[chapter["start_line"]] / total)
        if i + 1 < len(chapters):
            end_ms = speech_offset_ms + int(
                speech_duration_ms * cumulative[chapters[i + 1]["start_line"]] / total)
        else:
            end_ms = speech_offset_ms + speech_duration_ms
        if end_ms <= start_ms:
            logger.warning(f"Skipping zero-length chapter '{chapter['title']}'")
            continue
        timings.append((chapter["title"], start_ms, end_ms))

    return timings


def add_chapters_to_episode(mp3_path, podcast_text: str, configuration: Configuration):
    """Generate chapters for a finished episode and write them into the MP3.

    Must run AFTER add_pre_and_post_audio(), because the intro music shifts every timestamp.
    Chapters are cosmetic, so any failure is logged and swallowed rather than failing the
    episode.
    """
    try:
        chapters = generate_chapter_titles(podcast_text, configuration)
        if not chapters:
            return []

        total_ms = get_audio_duration_ms(mp3_path)
        speech_duration_ms = total_ms - INTRO_LEAD_MS - OUTRO_TAIL_MS
        if speech_duration_ms <= 0:
            logger.error(f"Audio at {mp3_path} is too short ({total_ms}ms) to hold chapters")
            return []

        timings = build_chapter_timings(podcast_text, chapters, speech_duration_ms, INTRO_LEAD_MS)
        add_chapters_to_mp3(mp3_path, timings)
        for title, start_ms, _ in timings:
            logger.info(f"  chapter {start_ms // 60000:02d}:{start_ms // 1000 % 60:02d} - {title}")
        return timings
    except Exception as e:
        logger.error(f"Failed to add chapters to {mp3_path}, continuing without them: {e}")
        return []
