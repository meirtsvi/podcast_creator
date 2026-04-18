import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile

import requests
import urllib3

from podcast_creator.logger import logger
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

# Fix for macOS SSL certificate verification issue
ssl._create_default_https_context = ssl._create_unverified_context
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def extract_video_id(url: str) -> str:
    """Extract the video ID from various YouTube URL formats."""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?\/]|$)",
        r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",
        r"(?:embed\/)([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def get_video_title(video_id: str) -> str:
    """Scrape the video title from the YouTube page."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        response = requests.get(url, headers={"Accept-Language": "en-US"}, verify=False)
        match = re.search(r'"title":"([^"]+)"', response.text)
        if match:
            return match.group(1).encode().decode("unicode_escape")
    except Exception:
        pass
    return "Unknown Title"


def get_transcript(video_id: str, languages: list[str] = None):
    """Fetch the transcript for a given video ID (compatible with v1.x API)."""
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.list(video_id)

        if languages:
            transcript = transcript_list.find_transcript(languages)
        else:
            try:
                transcript = transcript_list.find_manually_created_transcript(
                    transcript_list._manually_created_transcripts.keys()
                )
            except Exception:
                transcript = transcript_list.find_generated_transcript(
                    transcript_list._generated_transcripts.keys()
                )

        fetched = transcript.fetch()
        return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]

    except TranscriptsDisabled:
        raise RuntimeError("Transcripts are disabled for this video.")
    except NoTranscriptFound:
        raise RuntimeError("No transcript found for this video.")


def format_transcript(transcript_list, include_timestamps: bool = True) -> str:
    """Format transcript entries into readable text."""
    lines = []
    for entry in transcript_list:
        if include_timestamps:
            start = entry["start"]
            minutes = int(start // 60)
            seconds = int(start % 60)
            lines.append(f"[{minutes:02d}:{seconds:02d}] {entry['text']}")
        else:
            lines.append(entry["text"])
    return "\n".join(lines)


def extract_content_via_transcript_api(youtube_url: str, lang: str = 'en'):
    """
    Try to extract content using youtube-transcript-api.
    Returns (title, description, transcript_text) or (None, None, None) on failure.
    """
    try:
        video_id = extract_video_id(youtube_url)
        title = get_video_title(video_id)
        transcript = get_transcript(video_id, languages=[lang])
        transcript_text = format_transcript(transcript, include_timestamps=False)
        if transcript_text and transcript_text.strip():
            return title, None, transcript_text
    except Exception as e:
        logger.warning(f"youtube-transcript-api failed for {youtube_url}: {e}")
    return None, None, None


def download_subtitles(youtube_url: str, lang: str = 'en'):
    """
    Download auto-generated subtitles from YouTube using yt-dlp CLI
    and return (title, description, subtitles_content).
    """

    # update yt-dlp to latest version
    subprocess.run(
        [
            "c:\\util\\yt-dlp",
            "-U",
        ],
        #stdout = subprocess.DEVNULL,
        #stderr = subprocess.DEVNULL,
        check = False
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_file = os.path.join(tmpdir, 'subtitles.vtt')

        # 1. Download subtitles only (no decoding needed here)
        subprocess.run(
            [
                "c:\\util\\yt-dlp",
                "--write-auto-subs",
                "--sub-lang", lang,
                "--skip-download",
                "--cookies", r"c:\util\cookies.txt", "--remote-components", "ejs:github",
                "--output", output_file,
                "--quiet",
                youtube_url
            ],
            # stdout=subprocess.DEVNULL,
            # stderr=subprocess.DEVNULL,
            check=False
        )

        # 2. Get title (force UTF-8 decoding)
        title_result = subprocess.run(
            ["c:\\util\\yt-dlp", "--get-title", "--quiet", youtube_url],
            capture_output=True
        )
        title = title_result.stdout.decode("utf-8", errors="replace").strip() if title_result.returncode == 0 else None

        # 3. Get description (force UTF-8 decoding)
        desc_result = subprocess.run(
            ["c:\\util\\yt-dlp", "--get-description", "--quiet", youtube_url],
            capture_output=True
        )
        description = desc_result.stdout.decode("utf-8", errors="replace").strip() if desc_result.returncode == 0 else None

        # 4. Read subtitles content
        output_file_with_lang = f"{output_file}.{lang}.vtt"
        if os.path.exists(output_file_with_lang):
            with open(output_file_with_lang, "r", encoding="utf-8") as f:
                content = f.read()
            return title, description, content

    return None, None, None


def extract_sentences_no_duplicates(vtt_text):
    sentences = []
    buffer = ""
    last_sentence = ""

    for line in vtt_text.splitlines():
        line = line.strip()

        # Skip timestamps
        if "-->" in line:
            continue

        # Remove all formatting tags like <c> and <00:00:12.345>
        clean_line = re.sub(r'<[^>]+>', '', line).strip()

        if not clean_line:
            continue

        # Accumulate text
        buffer += " " + clean_line
        buffer = buffer.strip()

        # Finalize sentence if it ends with ., !, or ?
        while re.search(r'[.!?]["\']?$', buffer):
            if buffer != last_sentence:
                sentences.append(buffer)
                last_sentence = buffer
            buffer = ""

    # Handle final buffer if it's non-empty and not a duplicate
    if buffer and buffer != last_sentence:
        sentences.append(buffer.strip())

    return "\n".join(sentences)

def _find_yt_dlp() -> str:
    """Find the yt-dlp executable (PATH or Windows fallback)."""
    path = shutil.which("yt-dlp")
    if path:
        return path
    win_path = "c:\\util\\yt-dlp"
    if os.path.exists(win_path):
        return win_path
    return None


def extract_content_via_whisper(youtube_url: str):
    """
    Download audio via yt-dlp and transcribe with local whisper.
    Returns (title, None, transcript_text) or (None, None, None) on failure.
    """
    try:
        yt_dlp = _find_yt_dlp()
        if not yt_dlp:
            raise RuntimeError("yt-dlp not found")

        whisper_bin = shutil.which("whisper")
        if not whisper_bin:
            raise RuntimeError("whisper is not installed")

        video_id = extract_video_id(youtube_url)
        title = get_video_title(video_id)

        with tempfile.TemporaryDirectory() as td:
            audio_template = os.path.join(td, "audio.%(ext)s")
            dl = subprocess.run(
                [yt_dlp, "-f", "bestaudio/best", "--extract-audio",
                 "--audio-format", "mp3", "-o", audio_template, youtube_url],
                capture_output=True, text=True
            )
            if dl.returncode != 0:
                raise RuntimeError(f"yt-dlp audio download failed: {dl.stderr.strip()}")

            mp3s = [os.path.join(td, f) for f in os.listdir(td) if f.endswith(".mp3")]
            if not mp3s:
                raise RuntimeError("audio download produced no mp3")

            outdir = os.path.join(td, "whisper_out")
            os.makedirs(outdir, exist_ok=True)
            tr = subprocess.run(
                [whisper_bin, mp3s[0], "--model", "base",
                 "--output_format", "txt", "--output_dir", outdir],
                capture_output=True, text=True
            )
            if tr.returncode != 0:
                raise RuntimeError(f"whisper transcription failed: {tr.stderr.strip()}")

            txts = [os.path.join(outdir, f) for f in os.listdir(outdir) if f.endswith(".txt")]
            if not txts:
                raise RuntimeError("whisper produced no transcript")

            with open(txts[0], "r", encoding="utf-8") as f:
                text = f.read().strip()
            if not text:
                raise RuntimeError("whisper produced empty transcript")

            return title, None, text
    except Exception as e:
        logger.warning(f"whisper fallback failed for {youtube_url}: {e}")
    return None, None, None


youtube_extraction_cache = {}
class youtube_extracted_data:
    def __init__(self, title, description, content):
        self.title = title
        self.description = description
        self.content = content

    title: str
    description: str
    content: str

def extract_content_from_youtube(youtube_url, lang='en'):
    # Assume all calls to the same youtube url has the same lang
    cached_youtube_extracted_data = youtube_extraction_cache.get(youtube_url)
    if cached_youtube_extracted_data:
        logger.info(f"Found youtube URL {youtube_url} in cache, returning: {cached_youtube_extracted_data.title}")
        return cached_youtube_extracted_data.title, cached_youtube_extracted_data.description, cached_youtube_extracted_data.content
    title, description, content = extract_content_from_youtube_innner(youtube_url, lang)
    cached_youtube_extracted_data = youtube_extracted_data(title, description, content)
    youtube_extraction_cache[youtube_url] = cached_youtube_extracted_data
    return title, description, content

def extract_content_from_youtube_innner(youtube_url, lang='en'):
    """
    Downloads subtitles from a YouTube video and extracts sentences without duplicates.
    First tries youtube-transcript-api, falls back to yt-dlp if that fails.

    :param youtube_url: URL of the YouTube video
    :param lang: Language code for the subtitles (default is 'en' for English)
    :return: (title, description, cleansed_script) where title is the video title,
                description is the video description, and cleansed_script is the extracted
                sentences without duplicates.
    """
    # Try youtube-transcript-api first
    title, description, transcript_text = extract_content_via_transcript_api(youtube_url, lang)
    if transcript_text:
        logger.info(f"Transcribed youtube url {youtube_url} via transcript API, title: {title}, transcript: {transcript_text}")
        return title, description, transcript_text

    # Fall back to yt-dlp
    logger.info(f"Falling back to yt-dlp for {youtube_url}")
    title, description, vtt_text = download_subtitles(youtube_url, lang)
    if vtt_text:
        cleansed_script = extract_sentences_no_duplicates(vtt_text)
        logger.info(f"Transcribe youtube url {youtube_url}, title: {title}, description: {description}, cleansed_script: {cleansed_script}")
        return title, description, cleansed_script

    # Fall back to whisper (local audio transcription)
    logger.info(f"Falling back to whisper for {youtube_url}")
    title, description, whisper_text = extract_content_via_whisper(youtube_url)
    if whisper_text:
        logger.info(f"Transcribed youtube url {youtube_url} via whisper, title: {title}")
        return title, description, whisper_text

    logger.warning(f"Failed to transcribe youtube url {youtube_url}")
    return None, None, None


if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=LCEmiRjPEtQ"
    url = "https://www.youtube.com/watch?v=TdbpoDjIvPk"
    title, description, cleansed_script = extract_content_from_youtube(url, lang='en')
    print(cleansed_script)
