import os

import yt_dlp
import re

def download_subtitles(youtube_url, lang='en'):
    output_file = "subtitles.vtt"
    ydl_opts = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': [lang],
        'skip_download': True,
        'outtmpl': output_file,
        'quiet': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        title = info.get('title', 'Unknown Title')
        description = info.get('description', 'No Description Available')
        subs_filepath = info.get('requested_subtitles', {}).get(lang, {}).get('filepath')
        if subs_filepath:
            with open(subs_filepath, encoding='utf-8') as f:
                content = f.read()
            os.remove(subs_filepath)
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

def extract_content_from_youtube(youtube_url, lang='en'):
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
        return title, description, cleansed_script
    else:
        return None, None, None


if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=LCEmiRjPEtQ"
    cleansed_script = extract_content_from_youtube(url, lang='en')
    print(cleansed_script)
