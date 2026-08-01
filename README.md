# podcast_creator

Turns a list of article URLs into a finished, published podcast episode: it fetches the
articles, writes a two-host conversational script with Gemini, speaks it with Gemini TTS,
adds intro/outro music and chapter markers, and uploads the result to Transistor.fm.

It is multilingual — the shipped configuration runs Hebrew, English and Russian shows — and
everything podcast-specific (show, hosts, tone, language) lives in `.env` and Jinja templates
rather than in code.

## How it works

```
urls.txt
   │
   ├─ url_to_md.py ................ fetch each article, strip it to clean text
   │                               (handles paywalls, YouTube transcripts, PDFs, arXiv)
   ├─ gen_podcast_text.py ......... write the script with Gemini, as structured JSON
   │                               (retries until the word count lands in range)
   ├─ gen_podcast_episode_from_text.py  speak it with Gemini multi-speaker TTS
   ├─ audio.py .................... add intro/outro music, write ID3 tags
   ├─ chapters.py ................. derive chapters, write ID3 CHAP/CTOC frames
   └─ transistor.py ............... upload the episode as a draft
```

Two kinds of episode are produced:

- **Single-URL** — one episode per link, length proportional to the source.
- **Multi-URL** — several links batched into one ~20 minute news round-up.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) — required by `pydub` for MP3 encoding
- A [Gemini API key](https://aistudio.google.com/apikey)
- Optional: a [Transistor.fm](https://transistor.fm) account to publish,
  a [MailerSend](https://mailersend.com) key for failure notifications

## Setup

```bash
git clone https://github.com/meirtsvi/podcast_creator.git
cd podcast_creator

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # used for pages that need a real browser

cp src/podcast_creator/.env.example src/podcast_creator/.env
$EDITOR src/podcast_creator/.env     # add your Gemini key and paths
```

`.env.example` documents every variable. The minimum to generate an episode is `GEMINI_API_KEY`,
`OUTPUT_FOLDER_PREFIX`, `SINGLE_LINKS_FILEPATH` / `MULTI_LINKS_FILEPATH`, and one language block.
Leave `<LANG>_TRANSISTOR_SHOW_ID=0` to generate episodes locally without uploading.

## Usage

Put one URL per line in the file named by `SINGLE_LINKS_FILEPATH` or `MULTI_LINKS_FILEPATH`,
then:

```bash
cd src/podcast_creator
python main.py                # every language in PODCAST_LANGUAGES
python main.py english        # just one
```

Each episode is written to `<OUTPUT_FOLDER_PREFIX>_<language>/Episode_<n>/`:

| file | contents |
| --- | --- |
| `urls.txt` | the source links |
| `podcast_content.txt` | extracted article text |
| `podcast_input.txt` | the fully rendered prompt |
| `podcast_text.txt` | the final script, one `Speaker: line` per line |
| `Episode_<n>.mp3` | the finished audio, with chapters |
| `episode_name.txt`, `episode_desc.txt` | generated title and HTML description |

To run continuously, `monitor_new_links.py` watches a trigger file and starts a run whenever
it changes.

## Configuring a podcast

**Show, hosts and language** are environment variables, prefixed with the language name:

```ini
PODCAST_LANGUAGES=english

ENGLISH_TRANSISTOR_SHOW_ID=64687
ENGLISH_TRANSISTOR_SHOW_IDENTIFIER=your-show-slug
ENGLISH_PODCAST_NAME=Tech Updates
ENGLISH_MAN_SPEAKER_NAME=Yuval
ENGLISH_WOMAN_SPEAKER_NAME=Amit
ENGLISH_TEXT_DIRECTION=left-to-right
```

Adding a language means adding a block and listing it in `PODCAST_LANGUAGES` — no code changes.

**Prompts** are Jinja2 templates next to the code:

| template | purpose |
| --- | --- |
| `prompt_for_podcast_generation.j2` | the script itself — structure, length, style, hard rules |
| `tone_default.j2` | delivery notes shared by all languages |
| `tone_<language>.j2` | optional per-language override of the above |
| `prompt_for_*_episode_name.j2` | episode titles |
| `prompt_for_*_episode_desc.j2` | HTML episode descriptions |

Templates render with `StrictUndefined`, so a mistyped variable fails immediately rather than
silently producing an empty prompt.

**Episode length** is driven by `WORDS_PER_MINUTE` and the requested minutes. The generator
retries until the script lands inside a ±15% band around the target, and returns the closest
attempt if it runs out of retries.

**Pronunciation fixes** go in `translations.csv` — a source/target/gender triple applied to the
finished script, for words the TTS voice gets wrong.

## Notes

- Script generation asks for structured JSON via a response schema, so the script is parsed
  rather than scraped out of prose.
- Gemini 3.x counts thinking tokens against `max_output_tokens`; the token budget accounts for
  both, which is why the limits in `gen_podcast_text.py` look generous.
- TTS rotates across several `GEMINI_API_KEY_*` values to spread out rate limits. Set as many
  as you have; empty ones are skipped.
- Chapter timings are estimated by distributing the measured audio duration across the script
  in proportion to spoken characters — accurate enough to navigate by, not frame-exact.

## License

[MIT](LICENSE) © 2026 Meir Tsvi
