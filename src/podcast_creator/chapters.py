"""Derive podcast chapters from the generated script and write them into the MP3.

Multi-article episodes get one chapter per source article, so the chapter list lines up with
the article list in the episode description; single-article episodes fall back to splitting
the script by topic.

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

# Apple truncates chapter titles past this, and Spotify drops chapters that start less than
# 30 seconds after the one before them.
MAX_TITLE_CHARS = 45
MIN_CHAPTER_GAP_MS = 30_000

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

ARTICLE_CHAPTER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "chapters": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "article_index": {"type": "INTEGER"},
                    "start_line": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                },
                "required": ["article_index", "start_line", "title"],
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


def _call_chapter_model(prompt: str, schema: dict) -> list:
    """Return the model's "chapters" array, or [] on any failure.

    Chapters are a nice-to-have and must never break episode production, so every error path
    here degrades to "no chapters" rather than raising.
    """
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=CHAPTER_MODEL,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.2,
            ),
        )
        return json.loads(response.text)["chapters"]
    except Exception as e:
        logger.error(f"Chapter generation failed, continuing without chapters: {e}")
        return []


def _clean_title(title: str) -> str:
    """Collapse whitespace and trim to the length Apple will display."""
    title = " ".join((title or "").split())
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS - 1].rstrip() + "…"
    return title


def _numbered_script(lines: list) -> str:
    return "\n".join(f"[{i}] {speaker}: {text}" for i, (speaker, text) in enumerate(lines))


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
        f"at most {MAX_TITLE_CHARS} characters, with no numbering prefix and no quotes.\n"
        f"- The closing/goodbye section should be part of the last chapter, not its own chapter.\n\n"
        f"SCRIPT:\n{_numbered_script(lines)}"
    )

    chapters = _call_chapter_model(prompt, CHAPTER_SCHEMA)

    # Keep only sane, strictly increasing, in-range entries.
    cleaned, last_line = [], -1
    for chapter in chapters:
        start = int(chapter.get("start_line", -1))
        title = _clean_title(chapter.get("title"))
        if not title or start <= last_line or start >= len(lines):
            logger.warning(f"Dropping malformed chapter entry: {chapter}")
            continue
        cleaned.append({"title": title, "start_line": start, "url": None})
        last_line = start

    if not cleaned:
        return []

    # The first chapter must cover the start of the audio, whatever the model returned.
    cleaned[0]["start_line"] = 0
    logger.info(f"Generated {len(cleaned)} chapters: {[c['title'] for c in cleaned]}")
    return cleaned


def generate_article_chapters(podcast_text: str, configuration: Configuration) -> list:
    """Map each source article onto the script line where the hosts start discussing it.

    Returns a list of {"title", "start_line", "url"} in script order, or [] when the episode
    has fewer than two articles (nothing to chapter) or the mapping comes back unusable.

    The titles are rewritten by the model rather than taken from configuration.episode_titles
    verbatim: those come from generate_title_from_url(), so they are the source page's own
    title - usually English and usually far longer than a chapter title may be, while the
    audio is in the podcast's language.
    """
    titles = getattr(configuration, "episode_titles", None) or []
    urls = getattr(configuration, "episode_urls", None) or []
    if len(titles) < 2:
        return []

    lines = _spoken_lines(podcast_text)
    if len(lines) < MIN_CHAPTERS:
        logger.warning(f"Script has only {len(lines)} lines; skipping article chapters")
        return []

    numbered_articles = "\n".join(f"[{i}] {title}" for i, title in enumerate(titles))
    prompt = (
        f"Below is a podcast script in {configuration.output_language}. Each line is prefixed "
        f"with its index in square brackets.\n\n"
        f"The episode covers these articles, in this order:\n{numbered_articles}\n\n"
        f"For EACH article, find the index of the first script line where the hosts start "
        f"discussing it, and write a chapter title for it.\n"
        f"Rules:\n"
        f"- article_index is the number of the article in the list above.\n"
        f"- start_line is the index of the first script line about that article, or -1 if the "
        f"article is never discussed in the script.\n"
        f"- The opening greeting belongs to the first article's chapter. Do not create an entry "
        f"for the intro, and do not create an entry for the closing.\n"
        f"- If two articles are covered together in one stretch of script, give the first one "
        f"its start_line and the other -1.\n"
        f"- title is that article's subject in {configuration.output_language}, at most "
        f"{MAX_TITLE_CHARS} characters, plain text, no numbering prefix and no quotes. "
        f"Translate the article title rather than transliterating it.\n\n"
        f"SCRIPT:\n{_numbered_script(lines)}"
    )

    entries = _call_chapter_model(prompt, ARTICLE_CHAPTER_SCHEMA)

    cleaned = []
    for entry in entries:
        try:
            index = int(entry.get("article_index", -1))
            start = int(entry.get("start_line", -1))
        except (TypeError, ValueError):
            logger.warning(f"Dropping malformed article chapter entry: {entry}")
            continue
        title = _clean_title(entry.get("title"))
        if not title or start < 0 or start >= len(lines) or not 0 <= index < len(titles):
            logger.warning(f"Dropping malformed article chapter entry: {entry}")
            continue
        cleaned.append({"title": title, "start_line": start,
                        "url": urls[index] if index < len(urls) else None})

    # The model can return the articles out of the order they are actually discussed in, and
    # nothing forces the script to follow the article list - so trust the script, not the list.
    cleaned.sort(key=lambda chapter: chapter["start_line"])
    deduped, last_line = [], -1
    for chapter in cleaned:
        if deduped and chapter["start_line"] <= last_line:
            logger.warning(f"Dropping overlapping chapter '{chapter['title']}'")
            continue
        deduped.append(chapter)
        last_line = chapter["start_line"]

    if len(deduped) < 2:
        logger.warning(f"Only {len(deduped)} of {len(titles)} articles mapped to the script; "
                       f"falling back to topical chapters")
        return []

    # The first chapter must cover the start of the audio, whatever the model returned.
    deduped[0]["start_line"] = 0
    logger.info(f"Mapped {len(deduped)}/{len(titles)} articles to chapters: "
                f"{[c['title'] for c in deduped]}")
    return deduped


def build_chapter_timings(podcast_text: str, chapters: list, speech_duration_ms: int,
                          speech_offset_ms: int) -> list:
    """Convert chapter start lines into (title, start_ms, end_ms, url) tuples.

    `speech_duration_ms` is the length of the spoken audio and `speech_offset_ms` is how far
    into the final file that speech begins (the intro music runs before it).

    The first chapter is pulled back to 0 so it swallows the intro music: Apple and Spotify
    both require the first chapter to start at 00:00:00, and will ignore a chapter list that
    does not.
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
        timings.append((chapter["title"], start_ms, end_ms, chapter.get("url")))

    if timings:
        title, _, end_ms, url = timings[0]
        timings[0] = (title, 0, end_ms, url)

    return timings


def drop_chapters_too_close_together(timings: list) -> list:
    """Drop chapters that start less than MIN_CHAPTER_GAP_MS after the previous one.

    Spotify discards a whole chapter list that breaks its 30-second minimum, so a single
    article the hosts covered in a sentence would otherwise cost us every other chapter. The
    dropped chapter's span is handed to the one before it.
    """
    kept = []
    for title, start_ms, end_ms, url in timings:
        if kept and start_ms - kept[-1][1] < MIN_CHAPTER_GAP_MS:
            logger.warning(f"Dropping chapter '{title}': starts less than "
                           f"{MIN_CHAPTER_GAP_MS // 1000}s after '{kept[-1][0]}'")
            prev_title, prev_start, _, prev_url = kept[-1]
            kept[-1] = (prev_title, prev_start, end_ms, prev_url)
            continue
        kept.append((title, start_ms, end_ms, url))
    return kept


def add_chapters_to_episode(mp3_path, podcast_text: str, configuration: Configuration):
    """Generate chapters for a finished episode and write them into the MP3.

    One chapter per source article where the episode has several; episodes built from a single
    article fall back to splitting the script by topic instead.

    Must run AFTER add_pre_and_post_audio(), because the intro music shifts every timestamp.
    Returns the (title, start_ms, end_ms, url) tuples so the caller can put the same list in
    the episode description. Chapters are cosmetic, so any failure is logged and swallowed
    rather than failing the episode.
    """
    try:
        chapters = generate_article_chapters(podcast_text, configuration)
        if not chapters:
            chapters = generate_chapter_titles(podcast_text, configuration)
        if not chapters:
            return []

        total_ms = get_audio_duration_ms(mp3_path)
        speech_duration_ms = total_ms - INTRO_LEAD_MS - OUTRO_TAIL_MS
        if speech_duration_ms <= 0:
            logger.error(f"Audio at {mp3_path} is too short ({total_ms}ms) to hold chapters")
            return []

        timings = build_chapter_timings(podcast_text, chapters, speech_duration_ms, INTRO_LEAD_MS)
        timings = drop_chapters_too_close_together(timings)
        if len(timings) < 2:
            logger.warning(f"Only {len(timings)} chapter(s) left for {mp3_path}; skipping")
            return []

        add_chapters_to_mp3(mp3_path, timings)
        for title, start_ms, _, _ in timings:
            logger.info(f"  chapter {start_ms // 60000:02d}:{start_ms // 1000 % 60:02d} - {title}")
        return timings
    except Exception as e:
        logger.error(f"Failed to add chapters to {mp3_path}, continuing without them: {e}")
        return []
