import sys
import os
import traceback

import sendmail
from utils import read_file_content

os.environ["PYTHONHTTPSVERIFY"] = "0"

from podcast_creator.common import (
    create_episode_title, create_episode_description,
    create_episode_folder, get_processed_urls, get_next_episode_number,
    generate_title_from_url, get_episodes_with_missing_audio,
)

from podcast_creator.config import Configuration, EPISODE_URLS_FILENAME, EPISODE_DESC_FILENAME, EPISODE_TEXT, EPISODE_TITLE_FILENAME, \
    SINGLE_URL_LINKS_FILEPATH, MULTI_URL_LINKS_FILEPATH
from podcast_creator.audio import add_pre_and_post_audio
from podcast_creator.chapters import add_chapters_to_episode
from podcast_creator.gen_podcast_text import generate_podcast_text
from podcast_creator.gen_podcast_episode_from_text import generate_podcast_episode_audio_from_text
from podcast_creator.transistor import upload_new_podcast_episode
from podcast_creator.url_to_md import get_markdown_from_url
from podcast_creator.logger import logger

def process_batch(configuration: Configuration, batch_number: int, episode_number: int):
    urls = configuration.episode_urls
    titles = configuration.episode_titles

    logger.info(f"Processing batch {batch_number}")
    logger.info(f"# of URLs in this batch: {len(urls)}")

    episode_title = create_episode_title(configuration, titles, episode_number)
    logger.info(f'Episode name: {episode_title}')

    episode_description = create_episode_description(configuration, urls, titles)
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
    speaker_names = [configuration.man_speaker_name, configuration.woman_speaker_name]
    logger.info(f"Extracted speaker names: {speaker_names}")
    generate_podcast_episode_audio_from_text(episode_dir, podcast_text, episode_audio_file_path, speaker_names)
    add_pre_and_post_audio(episode_audio_file_path)
    add_chapters_to_episode(episode_audio_file_path, podcast_text, configuration)
    upload_new_podcast_episode(configuration)

    logger.info(f"Completed processing batch {batch_number}")

def process_links(configuration: Configuration, is_single_url_episode: bool, processed_urls: set):
    configuration.set_prompts(is_single_url_episode)
    next_episode_number = get_next_episode_number(configuration)
    
    # First gather all URLs and titles
    all_urls = []
    all_titles = []
    all_lens = []
    url_without_content = []

    with open(configuration.links_filename, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                parts = line.strip().split(',', 1)
                url = parts[0]
                length = parts[1] if len(parts) > 1 else -1

                valid, title, url = generate_title_from_url(url)
                if not valid:
                    url_without_content.append(url)
                    continue

                all_urls.append(url.rstrip('/'))
                all_titles.append(title)
                all_lens.append(length)

    # Filter out processed URLs
    remaining_urls = []
    remaining_titles = []
    remaining_lens = []

    for url, title, length in zip(all_urls, all_titles, all_lens):
        if url not in processed_urls:
            remaining_urls.append(url)
            remaining_titles.append(title)
            remaining_lens.append(length)

    logger.info(f"Found {len(remaining_urls)} URLs to check for content...")

    # Create lists to store filtered URLs, titles, and content
    filtered_urls = []
    filtered_titles = []
    filtered_lens = []
    remaining_content = []

    for url, title, length in zip(remaining_urls, remaining_titles, remaining_lens):
        # Try to extract content from the URL
        logger.info(f"Extracting content from {url}...")
        md_content, status_code = get_markdown_from_url(url)

        # Only keep URLs with valid content
        if md_content:
            filtered_urls.append(url)
            filtered_titles.append(title)
            filtered_lens.append(length)
            remaining_content.append(md_content)
        else:
            logger.info(f"Skipping URL due to failed content extraction: {url}")
            url_without_content.append(url)


    if len(url_without_content) > 0:
        logger.info(f"Found {len(url_without_content)} URLs without content. Sending email notification...")
        sendmail.send_email(os.getenv("MAIL_SEND_TO"),
                            "URLs without content",
                            f"The following URLs were skipped due to failed content extraction:\n\n" +
                            "\n".join(url_without_content))

    # Update remaining_urls and remaining_titles to only include those with valid content
    remaining_urls = filtered_urls
    remaining_titles = filtered_titles
    remaining_lens = filtered_lens

    logger.info(f"Found {len(remaining_urls)} URLs with valid content to process")

    # Process unprocessed URLs in batches of <batch_size>
    batch_size = configuration.batch_size
    url_batches = [remaining_urls[i:i + batch_size] for i in range(0, len(remaining_urls), batch_size)]
    title_batches = [remaining_titles[i:i + batch_size] for i in range(0, len(remaining_titles), batch_size)]
    len_batches = [remaining_lens[i:i + batch_size] for i in range(0, len(remaining_lens), batch_size)]
    content_batches = [remaining_content[i:i + batch_size] for i in range(0, len(remaining_content), batch_size)]

    for batch_number, (urls, titles, lens, contents) in enumerate(zip(url_batches, title_batches, len_batches, content_batches), 1):
        if batch_number > 1 and not is_single_url_episode and len(urls) < batch_size:
            logger.info(f"Skipping batch {batch_number} as it contains less than {batch_size} URLs")
            continue
        elif batch_number == 1 and not is_single_url_episode and len(urls) < 6:
            logger.info(f"Skipping batch {batch_number} as it contains less than 6 URLs")
            continue
        configuration.set_episode_urls(urls)
        configuration.set_episode_titles(titles)
        if is_single_url_episode:
            configuration.set_episode_length(lens[0])
        configuration.set_episode_contents(contents)
        try:
            process_batch(configuration, batch_number, next_episode_number + batch_number - 1)
        except Exception as e:
            logger.error(f"Error processing batch {batch_number}: {e}")
            traceback.print_exc()
            continue


def produce_audio_for_missing_audio_episodes(configuration):
    logger.info(f"Processing missing audio episodes...")
    episodes_without_audio = get_episodes_with_missing_audio(configuration)
    for episode in episodes_without_audio:
        try:
            episode_number, episode_folder = episode
            logger.info(f"Producing audio for episode {episode_number} - {episode_folder}")
            episode_title = read_file_content(episode_folder / EPISODE_TITLE_FILENAME)
            episode_desc = read_file_content(episode_folder /  EPISODE_DESC_FILENAME)
            podcast_text = read_file_content(episode_folder / EPISODE_TEXT)
            configuration.set_episode_details(episode_number, episode_title, episode_desc)
            episode_audio_file_path = configuration.episode_folder / configuration.episode_audio_filename
            generate_podcast_episode_audio_from_text(configuration.episode_folder,
                                                      podcast_text,
                                                      episode_audio_file_path,
                                                      [configuration.man_speaker_name, configuration.woman_speaker_name])
            add_pre_and_post_audio(episode_audio_file_path)
            add_chapters_to_episode(episode_audio_file_path, podcast_text, configuration)
            upload_new_podcast_episode(configuration)
        except Exception as e:
            logger.error(f"Error processing episode {episode_number}: {e}")

def process_one_language(configuration: Configuration):
    try:
        processed_urls = get_processed_urls(configuration)

        process_links(configuration, True, processed_urls)
        process_links(configuration, False, processed_urls)

        produce_audio_for_missing_audio_episodes(configuration)

    except ValueError as e:
        logger.error(e)
        traceback.print_exc()
        sys.exit()


def cleanup_existing_links(langs):
    logger.info("Cleaning up existing links...")
    per_lang_processed_urls = {}
    for lang in langs.split(","):
        configuration = Configuration(lang)
        processed_urls = get_processed_urls(configuration)
        processed_urls = {url.rstrip('/') for url in processed_urls}
        per_lang_processed_urls[lang] = processed_urls
    common_processed_urls = set.intersection(*per_lang_processed_urls.values())
    common_processed_urls = {url.replace('&e=1&dl=0', '&dl=0') for url in common_processed_urls}
    with open(SINGLE_URL_LINKS_FILEPATH, "r", encoding="utf-8") as f:
        existing_single_urls = set(line.strip().split(',', 1)[0].strip() for line in f if line.strip())
        existing_single_urls = {url.rstrip('/') for url in existing_single_urls}
    new_single_urls = existing_single_urls - common_processed_urls
    if len(new_single_urls) != len(existing_single_urls):
        with open(SINGLE_URL_LINKS_FILEPATH, "w", encoding="utf-8") as f:
            for url in new_single_urls:
                f.write(f"{url}\n")

    with open(MULTI_URL_LINKS_FILEPATH, "r", encoding="utf-8") as f:
        existing_multi_urls = set(line.strip().split(',', 1)[0].strip() for line in f if line.strip())
        existing_multi_urls = {url.rstrip('/') for url in existing_multi_urls}
    new_multi_urls = existing_multi_urls - common_processed_urls
    if len(new_multi_urls) != len(existing_multi_urls):
        with open(MULTI_URL_LINKS_FILEPATH, "w", encoding="utf-8") as f:
            for url in new_multi_urls:
                f.write(f"{url}\n")

    logger.info(f"Cleaned up existing links. "
                f"Remaining single URLs: {len(new_single_urls)}, "
                f"Remaining multi URLs: {len(new_multi_urls)}")


def process_languages(langs):
    if not langs:
        langs = os.getenv("PODCAST_LANGUAGES", "hebrew,english,russian")
        cleanup_existing_links(langs)
    for lang in langs.split(","):
        logger.info(f"Processing language: {lang}")
        configuration = Configuration(lang)
        process_one_language(configuration)

if __name__ == "__main__":
    langs = sys.argv[1] if len(sys.argv) > 1 else None
    process_languages(langs)
    # add_pre_and_post_audio(r"c:\Users\meir\Dropbox\tech_podcast_english\Episode_5\Episode_5.mp3")
    # configuration = Configuration("english")
    # configuration.episode_folder = r"c:\Users\meir\Dropbox\tech_podcast_english\Episode_5"
    # configuration.episode_audio_filename = "Episode_5.mp3"
    # configuration.set_episode_details(5,
    #                                   "Chapter 5 - Special Episode: Google I/O 2025: AI for the Win. Summary: A review of the artificial intelligence innovations presented at the Google I/O 2025 conference.",
    #                                   "Everything we learned from Google I/O 2025: AI, AI, and more AIhttps://mashable.com/article/google-io-2025-everything-you-need-to-know")
    # upload_new_podcast_episode(configuration)
