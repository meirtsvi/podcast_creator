import sys
import os
import traceback

from common import (
    create_episode_title, create_episode_description,
    create_episode_folder, get_processed_urls, get_next_episode_number,
    upload_new_podcast_episode, generate_title_from_url, call_genai_api
)

from config import Configuration, EPISODE_URLS_FILENAME
from mp3 import add_pre_and_post_audio
from gen_podcast_text import generate_podcast_text
from gen_podcast_episode_from_text import generate_podcast_episode_audio_from_text
from url_to_md import get_markdown_from_url
from logger import logger

def process_batch(configuration: Configuration, batch_number: int, episode_number: int):
    urls = configuration.episode_urls
    titles = configuration.episode_titles

    logger.info(f"Processing batch {batch_number}")
    logger.info(f"# of URLs in this batch: {len(urls)}")

    episode_title = create_episode_title(configuration, titles, episode_number)
    logger.info(f'Episode name: {episode_title}')

    episode_description = create_episode_description(configuration, urls, titles)
    episode_description = episode_description.replace("```html", "").replace("```", "").replace("\n", "")
    logger.info(f'Episode description: {episode_description}')

    configuration.set_episode_details(episode_number, episode_title, episode_description)

    # Create episode folder and write URLs, name and description
    previous_summaries_file_path = create_episode_folder(configuration)

    episode_dir = configuration.episode_folder
    episode_urls_file_path =episode_dir / EPISODE_URLS_FILENAME
    with open(episode_urls_file_path, 'w') as f:
        for url in urls:
            f.write(f"{url}\n")

    episode_audio_file_path = episode_dir / configuration.episode_audio_filename

    podcast_text = generate_podcast_text(configuration)
    with open(episode_dir / "podcast_text.txt", "w", encoding="utf-8") as f:
        f.write(podcast_text)
    # speakers = call_genai_api("Extract two speaker names. Return a string with format 'speaker1,speaker2' from this text:" + podcast_text)
    # speaker_names = speakers.split(",")
    # speaker_names = [name.strip().strip("'") for name in speaker_names if name.strip()]
    # Extracted speaker names: ["['Yuval", "Amit']"]
    speaker_names = [configuration.man_speaker_name, configuration.woman_speaker_name]
    logger.info(f"Extracted speaker names: {speaker_names}")
    generate_podcast_episode_audio_from_text(podcast_text, episode_audio_file_path, speaker_names)
    add_pre_and_post_audio(episode_audio_file_path)
    upload_new_podcast_episode(configuration)

    logger.info(f"Completed processing batch {batch_number}")

def process_links(configuration: Configuration, is_single_url_episode: bool, processed_urls: set):
    configuration.set_prompts(is_single_url_episode)
    next_episode_number = get_next_episode_number(configuration)
    
    # First gather all URLs and titles
    all_urls = []
    all_titles = []

    with open(configuration.links_filename, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                parts = line.strip().split(',', 1)
                url = parts[0]
                title = parts[1] if len(parts) > 1 else ""

                if title == "":
                    valid, title, url = generate_title_from_url(url)
                    if not valid:
                        continue

                all_urls.append(url.rstrip('/'))
                all_titles.append(title)

    # Filter out processed URLs
    remaining_urls = []
    remaining_titles = []

    for url, title in zip(all_urls, all_titles):
        if url not in processed_urls:
            remaining_urls.append(url)
            remaining_titles.append(title)

    logger.info(f"Found {len(remaining_urls)} URLs to check for content...")

    # Create lists to store filtered URLs, titles, and content
    filtered_urls = []
    filtered_titles = []
    remaining_content = []

    for url, title in zip(remaining_urls, remaining_titles):
        # Try to extract content from the URL
        logger.info(f"Extracting content from {url}...")
        md_content, status_code = get_markdown_from_url(url)

        # Only keep URLs with valid content
        if md_content:
            filtered_urls.append(url)
            filtered_titles.append(title)
            remaining_content.append(md_content)
        else:
            logger.info(f"Skipping URL due to failed content extraction: {url}")

    # Update remaining_urls and remaining_titles to only include those with valid content
    remaining_urls = filtered_urls
    remaining_titles = filtered_titles

    logger.info(f"Found {len(remaining_urls)} URLs with valid content to process")

    # Process unprocessed URLs in batches of <batch_size>
    batch_size = configuration.batch_size
    url_batches = [remaining_urls[i:i + batch_size] for i in range(0, len(remaining_urls), batch_size)]
    title_batches = [remaining_titles[i:i + batch_size] for i in range(0, len(remaining_titles), batch_size)]
    content_batches = [remaining_content[i:i + batch_size] for i in range(0, len(remaining_content), batch_size)]

    for batch_number, (urls, titles, contents) in enumerate(zip(url_batches, title_batches, content_batches), 1):
        configuration.set_episode_urls(urls)
        configuration.set_episode_titles(titles)
        configuration.set_episode_contents(contents)
        process_batch(configuration, batch_number, next_episode_number + batch_number - 1)


def main(configuration: Configuration):
    try:
        processed_urls = get_processed_urls(configuration)

        process_links(configuration, True, processed_urls)
        process_links(configuration, False, processed_urls)

    except ValueError as e:
        logger.error(e)
        traceback.print_exc()
        sys.exit()

if __name__ == "__main__":
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    langs = sys.argv[1] if len(sys.argv) > 1 else "hebrew,english,russian"
    for lang in langs.split(","):
        configuration = Configuration(lang)
        main(configuration)
    # add_pre_and_post_audio(r"c:\Users\meir\Dropbox\tech_podcast_english\Episode_5\Episode_5.mp3")
    # configuration = Configuration("english")
    # configuration.episode_folder = r"c:\Users\meir\Dropbox\tech_podcast_english\Episode_5"
    # configuration.episode_audio_filename = "Episode_5.mp3"
    # configuration.set_episode_details(5,
    #                                   "Chapter 5 - Special Episode: Google I/O 2025: AI for the Win. Summary: A review of the artificial intelligence innovations presented at the Google I/O 2025 conference.",
    #                                   "Everything we learned from Google I/O 2025: AI, AI, and more AIhttps://mashable.com/article/google-io-2025-everything-you-need-to-know")
    # upload_new_podcast_episode(configuration)
