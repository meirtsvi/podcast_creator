"""Outline-then-sections podcast script generation for long episodes.

Single-shot generation tops out around 4-5k words no matter what the prompt says: the model
simply stops (finish_reason=STOP) long before a 10k+ word target. This module makes length
and coverage deterministic instead of prompted-and-hoped:

1. An outline call reads the full source and must return exactly n_sections sections whose
   word_targets sum to the episode target, with every source topic assigned to one section.
2. Each section is generated in its own call at a size the model reliably produces
   (~1,600 words), with the outline, its own spec, and the tail of the previous section for
   continuity. Only section 1 may open the episode and only section N may close it, so a
   mid-episode "thanks and goodbye" cannot appear by construction (and is retried if it does).
3. Word targets are re-budgeted after every section from the remaining word budget, so
   per-section variance cancels instead of accumulating. A shortfall at the end triggers an
   expansion section spliced in before the closing.
"""

import difflib
import json
import math
import os
import re
import time

import dotenv
from google import genai
from google.genai import types

from podcast_creator.config import Configuration
from podcast_creator.logger import logger
from podcast_creator.templates import render_template

dotenv.load_dotenv()

OUTLINE_TEMPLATE = "prompt_for_podcast_outline.j2"
SECTION_TEMPLATE = "prompt_for_podcast_section.j2"

# Above this target the single-shot path demonstrably fails (STOP at ~4.7k words at best),
# so generate_podcast_text routes to this module. The -1/Auto path caps at 4,200 words and
# multi-URL episodes are 2,800, so both stay single-shot.
SECTIONED_MODE_THRESHOLD_WORDS = 5000

# Per-section size the models hit reliably; benchmarked in experiments/ (see repo root).
SECTION_TARGET_WORDS = 1600
SECTION_MIN_WORDS = 1000
SECTION_MAX_WORDS = 2200
CLOSING_SECTION_MIN_WORDS = 300

# A section of <=2,200 words costs ~7.7k output tokens for Hebrew (3.5 tokens/word); the rest
# is margin for JSON scaffolding. Thinking is budgeted separately via thinking_level, so the
# 65,536 shared-pool squeeze of the single-shot path cannot happen here.
SECTION_MAX_OUTPUT_TOKENS = 20000
OUTLINE_MAX_OUTPUT_TOKENS = 16384

# Defaults come from the experiment matrix in experiments/exp_sectioned_generation.py.
OUTLINE_MODEL = "gemini-3.1-pro-preview"
SECTION_MODEL = "gemini-3.1-pro-preview"
OUTLINE_THINKING_LEVEL = "high"   # planning is where thinking pays off
SECTION_THINKING_LEVEL = "low"    # writing needs speed, not deliberation

CONTINUITY_TAIL_LINES = 6
SECTION_MAX_RETRIES = 3
OUTLINE_MAX_RETRIES = 3
# Flash-class models yield ~60-65% of a requested section length, so a shortfall of a few
# hundred to ~2k words at the end is normal; each expansion recovers ~700-1,100 words.
MAX_EXPANSION_SECTIONS = 4

# A farewell in any non-final section means the model closed the episode mid-way; the section
# is retried. Matched with word boundaries against the flattened section text.
FAREWELL_MARKERS = {
    "hebrew": ["להתראות", "תודה שהאזנתם", "תודה שהקשבתם", "תודה שהייתם איתנו",
               "נתראה בפרק הבא", "עד הפרק הבא", "זהו להיום", "ביי ביי"],
    "default": ["goodbye", "see you next time", "thanks for listening", "that's all for today",
                "until next time", "bye bye"],
}

PODCAST_OUTLINE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "episode_summary": {"type": "STRING"},
        "sections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "topics": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "word_target": {"type": "INTEGER"},
                },
                "required": ["title", "topics", "key_points", "word_target"],
            },
        },
    },
    "required": ["episode_summary", "sections"],
}

# Same shape as gen_podcast_text.PODCAST_SCRIPT_SCHEMA; duplicated here rather than imported
# because gen_podcast_text imports this module (the reverse import would be circular).
PODCAST_SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "script_lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "speaker": {"type": "STRING"},
                    "line": {"type": "STRING"},
                },
                "required": ["speaker", "line"],
            },
        }
    },
    "required": ["script_lines"],
}


class OutlineGenerationError(Exception):
    """The outline could not be generated; caller should fall back to single-shot."""


def flatten_script_lines(script_lines: list) -> str:
    """Flatten [{"speaker","line"}] to '<speaker>: <line>\n' text.

    Internal whitespace is collapsed so one script line is always exactly one text line -
    the TTS chunker splits on newlines and relies on every line starting with a speaker name.
    """
    text = ""
    for item in script_lines:
        speaker = " ".join(item["speaker"].split())
        line = " ".join(item["line"].split())
        if not line:
            logger.warning(f"Skipping empty line for speaker {speaker!r}")
            continue
        text += speaker + ": " + line + "\n"
    return text


def parse_script_json(raw_text: str) -> list:
    """Parse a model response into script_lines; raises ValueError on any shape problem."""
    try:
        parsed = json.loads(raw_text)
    except json.decoder.JSONDecodeError as e:
        raise ValueError(f"response is not JSON: {e}") from e
    if isinstance(parsed, dict):
        lines = parsed.get("script_lines")
    elif isinstance(parsed, list):
        lines = parsed
    else:
        raise ValueError(f"unexpected JSON root type: {type(parsed).__name__}")
    if not isinstance(lines, list) or not lines:
        raise ValueError("script_lines is missing or empty")
    for item in lines:
        if not isinstance(item, dict) or "speaker" not in item or "line" not in item:
            raise ValueError(f"malformed script line: {item!r}")
    return lines


def repair_truncated_duplicate_lines(script_lines: list) -> list:
    """Drop a line that was cut off mid-sentence and immediately regenerated.

    An unescaped quote inside a JSON string (e.g. the gershayim in מנכ"ל) ends the string
    early under schema-constrained decoding; the model recovers by re-emitting the whole
    line as a new script line. The signature is deterministic - a line that does not end a
    sentence, followed by a same-speaker line that restarts with the same text - and the
    regenerated line always supersedes the truncated one, so dropping it is safe.
    """
    repaired = []
    for i, item in enumerate(script_lines):
        if i + 1 < len(script_lines):
            nxt = script_lines[i + 1]
            line = " ".join(item["line"].split())
            next_line = " ".join(nxt["line"].split())
            if (item["speaker"] == nxt["speaker"] and line
                    and not text_is_complete(line)
                    and _restarts_with(line, next_line)):
                logger.warning(f"Dropping truncated line superseded by its regeneration: "
                               f"{line[-80:]!r}")
                continue
        repaired.append(item)
    return repaired


def _restarts_with(truncated: str, regenerated: str) -> bool:
    if regenerated.startswith(truncated):
        return True
    head = regenerated[:len(truncated)]
    return difflib.SequenceMatcher(None, truncated, head).ratio() >= 0.7


def find_incomplete_lines(script_lines: list) -> list:
    """Lines that end mid-sentence. Run after repair_truncated_duplicate_lines: whatever
    that could not fix deterministically is a defect worth a retry."""
    return [item["line"] for item in script_lines
            if item["line"].strip() and not text_is_complete(item["line"])]


def text_is_complete(text: str) -> bool:
    """True when the text ends on sentence-ending punctuation (a closing quote may follow)."""
    text = text.strip()
    if not text:
        return False
    if text[-1] in '.!?':
        return True
    return len(text) > 1 and text[-1] in '"\'”’' and text[-2] in '.!?'


def find_farewells(text: str, language: str) -> list:
    markers = FAREWELL_MARKERS.get(language, FAREWELL_MARKERS["default"])
    found = []
    for marker in markers:
        if re.search(r'(?<!\w)' + re.escape(marker) + r'(?!\w)', text):
            found.append(marker)
    return found


def count_words(text: str) -> int:
    return len(text.split())


def _stream_call(client, model: str, prompt: str, schema: dict, max_output_tokens: int,
                 thinking_level: str, temperature: float, usage_log: list, call_label: str) -> tuple:
    """One streaming generation call. Returns (raw_text, finish_reason)."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
    )
    raw_text = ""
    finish_reason = None
    usage = None
    last_logged = 0
    started = time.monotonic()
    for chunk in client.models.generate_content_stream(model=model, contents=prompt, config=config):
        if chunk and chunk.text:
            raw_text += chunk.text
            word_count = len(raw_text.split())
            if word_count - last_logged >= 500:
                logger.info(f"[{call_label}] Progress: {word_count} words generated...")
                last_logged = word_count
        if getattr(chunk, "candidates", None):
            candidate = chunk.candidates[0]
            if getattr(candidate, "finish_reason", None):
                finish_reason = candidate.finish_reason
        if getattr(chunk, "usage_metadata", None):
            usage = chunk.usage_metadata
    elapsed = time.monotonic() - started
    usage_entry = {"call": call_label, "model": model, "elapsed_seconds": round(elapsed, 1)}
    if usage is not None:
        usage_entry.update({
            "prompt_tokens": usage.prompt_token_count,
            "cached_tokens": usage.cached_content_token_count,
            "output_tokens": usage.candidates_token_count,
            "thinking_tokens": usage.thoughts_token_count,
        })
    usage_log.append(usage_entry)
    logger.info(f"[{call_label}] finish_reason={finish_reason}, {elapsed:.0f}s, usage={usage_entry}")
    return raw_text, finish_reason


def normalize_outline(outline: dict, n_sections: int, target_word_count: int) -> dict:
    """Validate the outline and force word_targets to sum exactly to the episode target."""
    sections = outline.get("sections") or []
    if not outline.get("episode_summary") or not sections:
        raise ValueError("outline is missing episode_summary or sections")
    if abs(len(sections) - n_sections) > 1:
        raise ValueError(f"outline has {len(sections)} sections, wanted {n_sections}")
    for s in sections:
        if not s.get("title") or not s.get("topics") or not s.get("key_points"):
            raise ValueError(f"outline section is missing title/topics/key_points: {s.get('title')!r}")

    weights = [max(int(s.get("word_target") or 0), 1) for s in sections]
    total_weight = sum(weights)
    for s, w in zip(sections, weights):
        s["word_target"] = max(int(round(w * target_word_count / total_weight)), 1)
    # Rounding drift lands on the largest section.
    drift = target_word_count - sum(s["word_target"] for s in sections)
    max(sections, key=lambda s: s["word_target"])["word_target"] += drift
    return outline


def generate_outline(client, configuration: Configuration, source_material: str,
                     target_word_count: int, n_sections: int, usage_log: list,
                     model: str = OUTLINE_MODEL, thinking_level: str = OUTLINE_THINKING_LEVEL,
                     max_retries: int = OUTLINE_MAX_RETRIES) -> dict:
    two_hosts = len(configuration.hosts) > 1
    prompt = render_template(
        OUTLINE_TEMPLATE,
        language=configuration.output_language,
        two_hosts=two_hosts,
        man_speaker=configuration.man_speaker_name,
        woman_speaker=configuration.woman_speaker_name,
        speaker=configuration.man_speaker_name if configuration.hosts[0].lower() == "male"
                else configuration.woman_speaker_name,
        host1=configuration.hosts[0],
        host2=configuration.hosts[1] if two_hosts else configuration.hosts[0],
        target_n_words=target_word_count,
        n_sections=n_sections,
        source_material=source_material,
    )
    last_error = None
    for attempt in range(max_retries):
        try:
            raw_text, _ = _stream_call(
                client, model, prompt, PODCAST_OUTLINE_SCHEMA, OUTLINE_MAX_OUTPUT_TOKENS,
                thinking_level, temperature=0.3, usage_log=usage_log,
                call_label=f"outline attempt {attempt + 1}")
            outline = normalize_outline(json.loads(raw_text), n_sections, target_word_count)
            titles = ", ".join(s["title"] for s in outline["sections"])
            logger.info(f"Outline: {len(outline['sections'])} sections: {titles}")
            return outline
        except (ValueError, json.decoder.JSONDecodeError) as e:
            last_error = e
            logger.error(f"Outline attempt {attempt + 1} failed: {e}")
    raise OutlineGenerationError(f"outline failed after {max_retries} attempts: {last_error}")


def generate_section(client, configuration: Configuration, source_material: str, outline: dict,
                     section_index: int, n_sections: int, section_spec: dict, word_target: int,
                     is_first: bool, is_last: bool, next_section_title: str, previous_tail: list,
                     usage_log: list, model: str = SECTION_MODEL,
                     thinking_level: str = SECTION_THINKING_LEVEL,
                     max_retries: int = SECTION_MAX_RETRIES) -> list:
    """Generate one section; returns its script_lines (best attempt, never raises after any parse success)."""
    two_hosts = len(configuration.hosts) > 1
    language = configuration.output_language
    tail_text = "\n".join(f"{item['speaker']}: {' '.join(item['line'].split())}"
                          for item in previous_tail)
    prompt = render_template(
        SECTION_TEMPLATE,
        language=language,
        two_hosts=two_hosts,
        episode_in_series=configuration.episode_number != -1,
        man_speaker=configuration.man_speaker_name,
        woman_speaker=configuration.woman_speaker_name,
        speaker=configuration.man_speaker_name if configuration.hosts[0].lower() == "male"
                else configuration.woman_speaker_name,
        host1=configuration.hosts[0],
        host2=configuration.hosts[1] if two_hosts else configuration.hosts[0],
        podcast_name=configuration.podcast_name,
        episode_number=configuration.episode_number,
        source_material=source_material,
        episode_summary=outline["episode_summary"],
        outline_sections=outline["sections"],
        section_index=section_index,
        n_sections=n_sections,
        section_title=section_spec["title"],
        topics=section_spec["topics"],
        key_points=section_spec["key_points"],
        section_word_target=word_target,
        min_section_words=int(word_target * 0.85),
        max_section_words=int(word_target * 1.15),
        is_first=is_first,
        is_last=is_last,
        next_section_title=next_section_title,
        previous_tail=tail_text,
    )

    best_lines, best_score = None, math.inf
    for attempt in range(max_retries):
        raw_text, _ = _stream_call(
            client, model, prompt, PODCAST_SCRIPT_SCHEMA, SECTION_MAX_OUTPUT_TOKENS,
            thinking_level, temperature=0.8, usage_log=usage_log,
            call_label=f"section {section_index}/{n_sections} attempt {attempt + 1}")
        try:
            lines = parse_script_json(raw_text)
        except ValueError as e:
            logger.error(f"Section {section_index} attempt {attempt + 1}: {e}")
            continue
        lines = repair_truncated_duplicate_lines(lines)

        text = flatten_script_lines(lines)
        word_count = count_words(text)
        complete = text_is_complete(text)
        incomplete_lines = find_incomplete_lines(lines)
        farewells = [] if is_last else find_farewells(text, language)

        # Distance from target, with hard penalties for defects worth a retry on their own.
        score = abs(word_count - word_target)
        if not complete:
            score += 100000
        if incomplete_lines:
            score += 100000
        if farewells:
            score += 100000
        if score < best_score:
            best_score, best_lines = score, lines

        problems = []
        if word_count < word_target * 0.6:
            problems.append(f"too short ({word_count} < {int(word_target * 0.6)})")
        if not complete:
            problems.append("ends mid-sentence")
        if incomplete_lines:
            problems.append(f"{len(incomplete_lines)} line(s) end mid-sentence")
        if farewells:
            problems.append(f"farewell in non-final section: {farewells}")
        if not problems:
            logger.info(f"Section {section_index}/{n_sections}: {word_count} words (target {word_target})")
            return lines
        logger.warning(f"Section {section_index} attempt {attempt + 1}: {', '.join(problems)}. Retrying...")

    if best_lines is None:
        logger.error(f"Section {section_index}: every attempt failed to parse; section will be empty")
        return []
    logger.warning(f"Section {section_index}: exhausted retries, keeping best attempt "
                   f"({count_words(flatten_script_lines(best_lines))} words)")
    return best_lines


def generate_podcast_text_sectioned(configuration: Configuration, target_word_count: int,
                                    min_n_words: int, max_n_words: int,
                                    outline_model: str = OUTLINE_MODEL,
                                    section_model: str = SECTION_MODEL,
                                    section_target_words: int = SECTION_TARGET_WORDS,
                                    section_thinking_level: str = SECTION_THINKING_LEVEL,
                                    client=None) -> str:
    """Full outline-then-sections pipeline; returns flattened '<speaker>: <line>' text.

    Raises OutlineGenerationError when no outline can be produced - the caller
    (generate_podcast_text) falls back to the single-shot path in that case.
    """
    started = time.monotonic()
    if client is None:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    source_material = "\n".join(line for line in configuration.episode_contents if line)
    n_sections = max(2, round(target_word_count / section_target_words))
    logger.info(f"Sectioned generation: target {target_word_count} words "
                f"({min_n_words}-{max_n_words}) in {n_sections} sections "
                f"(outline={outline_model}, sections={section_model})")

    usage_log = []
    outline = generate_outline(client, configuration, source_material, target_word_count,
                               n_sections, usage_log, model=outline_model)
    sections = outline["sections"]
    n_sections = len(sections)
    _write_artifact(configuration, "podcast_outline.json",
                    json.dumps(outline, ensure_ascii=False, indent=2))

    section_lines = []          # list of script_lines lists, one per outline section
    section_reports = []
    words_so_far = 0
    for i, spec in enumerate(sections):
        is_first, is_last = i == 0, i == n_sections - 1
        remaining_weight = sum(s["word_target"] for s in sections[i:])
        word_target = int(round((target_word_count - words_so_far) * spec["word_target"] / remaining_weight))
        low = CLOSING_SECTION_MIN_WORDS if is_last else SECTION_MIN_WORDS
        word_target = max(low, min(word_target, SECTION_MAX_WORDS))

        previous_tail = section_lines[-1][-CONTINUITY_TAIL_LINES:] if section_lines else []
        lines = generate_section(
            client, configuration, source_material, outline,
            section_index=i + 1, n_sections=n_sections, section_spec=spec,
            word_target=word_target, is_first=is_first, is_last=is_last,
            next_section_title=sections[i + 1]["title"] if not is_last else "",
            previous_tail=previous_tail, usage_log=usage_log, model=section_model,
            thinking_level=section_thinking_level)
        section_lines.append(lines)
        section_words = count_words(flatten_script_lines(lines))
        words_so_far += section_words
        section_reports.append({"index": i + 1, "title": spec["title"],
                                "word_target": word_target, "words": section_words})
        _write_artifact(configuration, f"podcast_section_{i + 1:02d}.txt", flatten_script_lines(lines))
        logger.info(f"Section {i + 1}/{n_sections} done: {section_words} words "
                    f"(target {word_target}), total {words_so_far}/{target_word_count}")

    # Shortfall: splice expansion sections in before the closing, deepening the sections that
    # fell furthest below their targets.
    expansions = 0
    while words_so_far < min_n_words and expansions < MAX_EXPANSION_SECTIONS:
        expansions += 1
        body_reports = section_reports[:n_sections - 1] if n_sections > 1 else section_reports
        weakest = sorted(body_reports, key=lambda r: r["words"] - r["word_target"])[:2]
        weakest_specs = [sections[r["index"] - 1] for r in weakest]
        needed = max(500, min(min_n_words - words_so_far + 300, SECTION_MAX_WORDS))
        logger.warning(f"Total {words_so_far} < {min_n_words}; expansion {expansions} of "
                       f"~{needed} words on: {[s['title'] for s in weakest_specs]}")
        expansion_spec = {
            "title": " + ".join(s["title"] for s in weakest_specs),
            "topics": [t for s in weakest_specs for t in s["topics"]],
            "key_points": [k for s in weakest_specs for k in s["key_points"]],
        }
        insert_at = max(len(section_lines) - 1, 0)   # before the closing section
        previous_tail = section_lines[insert_at - 1][-CONTINUITY_TAIL_LINES:] if insert_at > 0 else []
        lines = generate_section(
            client, configuration, source_material, outline,
            section_index=insert_at + 1, n_sections=n_sections, section_spec=expansion_spec,
            word_target=needed, is_first=False, is_last=False,
            next_section_title=sections[-1]["title"],
            previous_tail=previous_tail, usage_log=usage_log, model=section_model,
            thinking_level=section_thinking_level)
        section_lines.insert(insert_at, lines)
        added = count_words(flatten_script_lines(lines))
        words_so_far += added
        section_reports.append({"index": len(section_reports) + 1,
                                "title": f"expansion: {expansion_spec['title']}",
                                "word_target": needed, "words": added})
        _write_artifact(configuration, f"podcast_section_expansion_{expansions}.txt",
                        flatten_script_lines(lines))

    all_lines = [item for lines in section_lines for item in lines]
    podcast_text = flatten_script_lines(all_lines)
    total_words = count_words(podcast_text)
    elapsed = time.monotonic() - started
    in_range = min_n_words <= total_words <= max_n_words
    logger.info(f"Sectioned generation done: {total_words} words "
                f"(target {min_n_words}-{max_n_words}, in_range={in_range}) "
                f"in {elapsed:.0f}s over {len(usage_log)} calls")

    report = {
        "target_word_count": target_word_count,
        "min_n_words": min_n_words,
        "max_n_words": max_n_words,
        "total_words": total_words,
        "in_range": in_range,
        "outline_model": outline_model,
        "section_model": section_model,
        "section_target_words": section_target_words,
        "n_sections": n_sections,
        "expansions": expansions,
        "elapsed_seconds": round(elapsed, 1),
        "sections": section_reports,
        "calls": usage_log,
    }
    _write_artifact(configuration, "podcast_sectioned_report.json",
                    json.dumps(report, ensure_ascii=False, indent=2))
    return podcast_text


def _write_artifact(configuration: Configuration, filename: str, content: str):
    try:
        with open(configuration.episode_folder / filename, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        logger.warning(f"Could not write artifact {filename}: {e}")
