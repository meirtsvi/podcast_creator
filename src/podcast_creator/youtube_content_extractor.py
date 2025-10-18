import os
import re
import subprocess
import tempfile

from podcast_creator.logger import logger

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

    :param youtube_url: URL of the YouTube video
    :param lang: Language code for the subtitles (default is 'en' for English)
    :return: (title, description, cleansed_script) where title is the video title,
                description is the video description, and cleansed_script is the extracted
                sentences without duplicates.
    """
    title, description, vtt_text = download_subtitles(youtube_url, lang)
    if vtt_text:
        cleansed_script = extract_sentences_no_duplicates(vtt_text)
        logger.info(f"Transcribe youtube url {youtube_url}, title: {title}, description: {description}, cleansed_script: {cleansed_script}")
        return title, description, cleansed_script
    else:
        logger.warning(f"Failed to transcribe youtube url {youtube_url}")
        return None, None, None


if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=LCEmiRjPEtQ"
    title, description, cleansed_script = extract_content_from_youtube(url, lang='en')
    title, description, cleansed_script = extract_content_from_youtube(url, lang='en')
    print(cleansed_script)
