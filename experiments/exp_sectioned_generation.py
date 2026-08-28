"""Experiment matrix for long-episode script generation.

Compares single-shot baselines against the outline-then-sections pipeline
(gen_podcast_text_sectioned) across Gemini models, on a real 54k-word transcript,
targeting a 90-minute Hebrew episode (12,600 words, range 10,710-14,489).

Usage:
    python experiments/exp_sectioned_generation.py --cells A1,A2,A3 --runs 2
    python experiments/exp_sectioned_generation.py --cells B,C,D,E --runs 2
    python experiments/exp_sectioned_generation.py --summary

Each run appends one JSON line to experiments/results/results.jsonl and keeps its
artifacts (outline, per-section texts, report) in experiments/results/<cell>_run<k>/.
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import dotenv
dotenv.load_dotenv(REPO_ROOT / "src" / "podcast_creator" / ".env")

import os
from google import genai
from google.genai import types

from podcast_creator.config import Configuration
from podcast_creator.templates import render_template
from podcast_creator.gen_podcast_text_sectioned import (
    PODCAST_SCRIPT_SCHEMA, count_words, find_farewells, flatten_script_lines,
    generate_podcast_text_sectioned, parse_script_json, text_is_complete,
)

SOURCE_PATH = Path("/tmp/original_content.txt")
RESULTS_DIR = Path(__file__).resolve().parent / "results"
# Overridden by --out-name so parallel experiment processes never interleave writes.
RESULTS_FILE = RESULTS_DIR / "results.jsonl"
REFERENCE_TOPICS_FILE = RESULTS_DIR / "reference_topics.json"

EPISODE_MINUTES = 90
WORDS_PER_MINUTE = 140
TARGET_WORDS = EPISODE_MINUTES * WORDS_PER_MINUTE          # 12600
MIN_WORDS = int(TARGET_WORDS * 0.85)                       # 10710
MAX_WORDS = int(TARGET_WORDS * 1.15)                       # 14489

PRO = "gemini-3.1-pro-preview"
FLASH37 = "gemini-3.7-flash"
FLASH36 = "gemini-3.6-flash"
JUDGE_MODEL = FLASH36

# cell -> (pipeline, outline_model, generation_model)
CELLS = {
    "A1": ("single_shot", None, PRO),
    "A2": ("single_shot", None, FLASH37),
    "A3": ("single_shot", None, FLASH36),
    "B": ("sectioned", PRO, PRO),
    "C": ("sectioned", PRO, FLASH37),
    "D": ("sectioned", FLASH37, FLASH37),
    "E": ("sectioned", PRO, FLASH36),
}

# $ per 1M tokens (input <=200k prompt, output). Cached input billed at 50%.
PRICING = {
    PRO: (2.00, 12.00),
    FLASH37: (0.75, 3.75),
    FLASH36: (0.75, 3.75),
}


def estimate_cost(calls: list) -> float:
    total = 0.0
    for c in calls:
        in_price, out_price = PRICING.get(c.get("model"), (0, 0))
        prompt = c.get("prompt_tokens") or 0
        cached = c.get("cached_tokens") or 0
        output = (c.get("output_tokens") or 0) + (c.get("thinking_tokens") or 0)
        total += ((prompt - cached) * in_price + cached * in_price * 0.5 + output * out_price) / 1e6
    return round(total, 3)


def make_configuration(run_dir: Path) -> Configuration:
    configuration = Configuration("hebrew")
    configuration.set_episode_details(episode_number=999, episode_title="experiment",
                                      episode_description="experiment")
    configuration.episode_folder = run_dir
    configuration.hosts = ["male", "female"]
    configuration.set_episode_length(EPISODE_MINUTES)
    configuration.set_prompts(is_single_url=True)
    with open(SOURCE_PATH, encoding="utf-8") as f:
        configuration.episode_contents = f.read().splitlines()
    return configuration


def get_reference_topics(client, source_material: str) -> list:
    """Extract the source's major topics once (held constant for every run's coverage judge)."""
    if REFERENCE_TOPICS_FILE.exists():
        return json.loads(REFERENCE_TOPICS_FILE.read_text(encoding="utf-8"))["topics"]
    schema = {
        "type": "OBJECT",
        "properties": {"topics": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": ["topics"],
    }
    prompt = (f"# SOURCE MATERIAL\n\n{source_material}\n\n# TASK\n\n"
              "List the 15 to 25 major topics discussed in the source material above, in Hebrew, "
              "one short phrase per topic, ordered as they appear. Cover the whole source: "
              "the last topics must come from the final parts of the material.")
    r = client.models.generate_content(
        model=PRO, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema,
            temperature=0.2, max_output_tokens=8192,
            thinking_config=types.ThinkingConfig(thinking_level="high")))
    topics = json.loads(r.text)["topics"]
    REFERENCE_TOPICS_FILE.write_text(json.dumps({"topics": topics}, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(f"Extracted {len(topics)} reference topics")
    return topics


def judge_coverage(client, topics: list, script_text: str) -> dict:
    schema = {
        "type": "OBJECT",
        "properties": {
            "coverage": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"topic": {"type": "STRING"}, "covered": {"type": "BOOLEAN"}},
                    "required": ["topic", "covered"],
                },
            }
        },
        "required": ["coverage"],
    }
    topic_list = "\n".join(f"- {t}" for t in topics)
    prompt = (f"# PODCAST SCRIPT\n\n{script_text}\n\n# TOPICS\n\n{topic_list}\n\n# TASK\n\n"
              "For every topic in the list, decide whether the podcast script above covers it "
              "substantively (more than a passing mention). Return every topic with a verdict.")
    r = client.models.generate_content(
        model=JUDGE_MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema,
            temperature=0.0, max_output_tokens=8192,
            thinking_config=types.ThinkingConfig(thinking_level="low")))
    coverage = json.loads(r.text)["coverage"]
    covered = sum(1 for c in coverage if c["covered"])
    return {"covered": covered, "total": len(coverage),
            "fraction": round(covered / max(len(coverage), 1), 3),
            "missing": [c["topic"] for c in coverage if not c["covered"]]}


def run_single_shot(client, model: str, configuration: Configuration, run_dir: Path) -> dict:
    """One production-parity single-shot attempt (schema, temp 0.8, 65,536 budget, no thinking cap)."""
    source_material = "\n".join(line for line in configuration.episode_contents if line)
    prompt = render_template(
        "prompt_for_podcast_generation.j2",
        language="hebrew", two_hosts=True, episode_in_series=True,
        man_speaker=configuration.man_speaker_name,
        woman_speaker=configuration.woman_speaker_name,
        speaker=configuration.man_speaker_name,
        host1="male", host2="female",
        podcast_name=configuration.podcast_name,
        episode_number=configuration.episode_number,
        min_n_words=MIN_WORDS, target_n_words=TARGET_WORDS, max_n_words=MAX_WORDS,
        source_material=source_material)
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=PODCAST_SCRIPT_SCHEMA,
        temperature=0.8, max_output_tokens=65536)
    started = time.monotonic()
    raw_text, finish_reason, usage = "", None, None
    for chunk in client.models.generate_content_stream(model=model, contents=prompt, config=config):
        if chunk and chunk.text:
            raw_text += chunk.text
        if getattr(chunk, "candidates", None) and getattr(chunk.candidates[0], "finish_reason", None):
            finish_reason = str(chunk.candidates[0].finish_reason)
        if getattr(chunk, "usage_metadata", None):
            usage = chunk.usage_metadata
    elapsed = time.monotonic() - started

    call = {"call": "single_shot", "model": model, "elapsed_seconds": round(elapsed, 1)}
    if usage is not None:
        call.update({"prompt_tokens": usage.prompt_token_count,
                     "cached_tokens": usage.cached_content_token_count,
                     "output_tokens": usage.candidates_token_count,
                     "thinking_tokens": usage.thoughts_token_count})
    result = {"finish_reason": finish_reason, "calls": [call], "elapsed_seconds": round(elapsed, 1)}
    try:
        lines = parse_script_json(raw_text)
    except ValueError as e:
        result.update({"total_words": 0, "parse_error": str(e), "text": ""})
        return result
    text = flatten_script_lines(lines)
    (run_dir / "podcast_text_original.txt").write_text(text, encoding="utf-8")
    result.update({"total_words": count_words(text), "complete": text_is_complete(text), "text": text})
    return result


def scan_section_farewells(run_dir: Path) -> list:
    """Farewell markers found in non-final section artifacts of a sectioned run."""
    section_files = sorted(run_dir.glob("podcast_section_[0-9]*.txt"))
    hits = []
    for f in section_files[:-1]:
        found = find_farewells(f.read_text(encoding="utf-8"), "hebrew")
        if found:
            hits.append({"section": f.name, "markers": found})
    for f in run_dir.glob("podcast_section_expansion_*.txt"):
        found = find_farewells(f.read_text(encoding="utf-8"), "hebrew")
        if found:
            hits.append({"section": f.name, "markers": found})
    return hits


def run_cell(client, cell: str, run_idx: int, section_words: int, topics: list,
             section_thinking: str = "low") -> dict:
    pipeline, outline_model, gen_model = CELLS[cell]
    label = (f"{cell}_run{run_idx}"
             + (f"_sw{section_words}" if section_words != 1600 else "")
             + (f"_th{section_thinking}" if section_thinking != "low" else ""))
    run_dir = RESULTS_DIR / label
    run_dir.mkdir(parents=True, exist_ok=True)
    configuration = make_configuration(run_dir)
    print(f"\n=== {label}: {pipeline} outline={outline_model} gen={gen_model} ===", flush=True)

    record = {"cell": cell, "run": run_idx, "label": label, "pipeline": pipeline,
              "outline_model": outline_model, "generation_model": gen_model,
              "section_target_words": section_words if pipeline == "sectioned" else None,
              "target": TARGET_WORDS, "range": [MIN_WORDS, MAX_WORDS],
              "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        if pipeline == "single_shot":
            r = run_single_shot(client, gen_model, configuration, run_dir)
            text = r.pop("text")
            record.update(r)
            record["farewell_hits"] = []          # single text: no per-section scan
        else:
            started = time.monotonic()
            text = generate_podcast_text_sectioned(
                configuration, TARGET_WORDS, MIN_WORDS, MAX_WORDS,
                outline_model=outline_model, section_model=gen_model,
                section_target_words=section_words,
                section_thinking_level=section_thinking, client=client)
            report = json.loads((run_dir / "podcast_sectioned_report.json").read_text(encoding="utf-8"))
            record.update({k: report[k] for k in
                           ("total_words", "in_range", "n_sections", "expansions",
                            "elapsed_seconds", "sections", "calls")})
            record["farewell_hits"] = scan_section_farewells(run_dir)
            record["section_word_error"] = round(sum(
                abs(s["words"] - s["word_target"]) / max(s["word_target"], 1)
                for s in report["sections"]) / max(len(report["sections"]), 1), 3)
        record["in_range"] = MIN_WORDS <= record.get("total_words", 0) <= MAX_WORDS
        record["cost_usd"] = estimate_cost(record.get("calls", []))
        if record.get("total_words", 0) > 0:
            record["coverage"] = judge_coverage(client, topics, text)
    except Exception as e:
        record["error"] = f"{type(e).__name__}: {e}"
        print(f"!!! {label} failed: {record['error']}", flush=True)

    with open(RESULTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"=== {label}: words={record.get('total_words')} in_range={record.get('in_range')} "
          f"cost=${record.get('cost_usd')} coverage={record.get('coverage', {}).get('fraction')} "
          f"farewells={len(record.get('farewell_hits', []))}", flush=True)
    return record


def print_summary():
    rows = []
    for f in sorted(RESULTS_DIR.glob("results*.jsonl")):
        rows += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("No results yet")
        return
    header = (f"{'label':<22} {'pipeline':<12} {'words':>6} {'in_range':>8} {'cov':>5} "
              f"{'farewells':>9} {'sec_err':>7} {'cost$':>6} {'min':>5}")
    print(header)
    print("-" * len(header))
    for r in rows:
        cov = r.get("coverage", {}).get("fraction", "")
        err = r.get("error")
        print(f"{r['label']:<22} {r['pipeline']:<12} {r.get('total_words', 0):>6} "
              f"{str(r.get('in_range', '')):>8} {str(cov):>5} "
              f"{len(r.get('farewell_hits', [])):>9} {str(r.get('section_word_error', '')):>7} "
              f"{str(r.get('cost_usd', '')):>6} {round((r.get('elapsed_seconds') or 0) / 60, 1):>5}"
              + (f"  ERROR: {err[:60]}" if err else ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", default="", help="comma-separated cells, e.g. A1,B,C")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--start-run", type=int, default=1)
    parser.add_argument("--section-words", type=int, default=1600)
    parser.add_argument("--section-thinking", default="low", choices=["low", "high"])
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--out-name", default="results.jsonl")
    args = parser.parse_args()

    if args.summary:
        print_summary()
        return
    global RESULTS_FILE
    RESULTS_FILE = RESULTS_DIR / args.out_name

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    source_material = "\n".join(l for l in SOURCE_PATH.read_text(encoding="utf-8").splitlines() if l)
    topics = get_reference_topics(client, source_material)

    for cell in [c.strip() for c in args.cells.split(",") if c.strip()]:
        if cell not in CELLS:
            print(f"Unknown cell {cell}, skipping")
            continue
        for run_idx in range(args.start_run, args.start_run + args.runs):
            run_cell(client, cell, run_idx, args.section_words, topics,
                     section_thinking=args.section_thinking)
    print_summary()


if __name__ == "__main__":
    main()
